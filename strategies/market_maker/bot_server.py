"""
Bitunix Market Maker — FastAPI Dashboard Server
================================================
Run with:
    uvicorn bot_server:app --host 0.0.0.0 --port 8000

Exposes:
    GET  /status        → current bot state snapshot
    GET  /config        → current MarketMakerConfig as JSON
    POST /start         → body: {symbol, qty, leverage, spread, max_loss}
    POST /stop          → stop the running bot
    WS   /ws            → live JSON state pushed every second
"""

import os
import time
import json
import asyncio
import logging
import threading
from typing import Optional, List, Set
from datetime import datetime

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# ── make repo root importable when run directly ──
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))

from shared.bitunix_client import BitunixClient
from strategies.market_maker.strategy_market_maker import MarketMaker, MarketMakerConfig
from strategies.market_maker.strategy_adaptive import AdaptiveMarketMaker, AdaptiveConfig

# ─── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot_server")

# ─── Load environment ──────────────────────────────────────────────────────────
load_dotenv()
API_KEY    = os.getenv("BITUNIX_API_KEY", os.getenv("API_KEY", ""))
SECRET_KEY = os.getenv("BITUNIX_SECRET_KEY", os.getenv("SECRET_KEY", ""))

# ─── Shared state ─────────────────────────────────────────────────────────────
# This dict is written by the bot thread and read by the WebSocket broadcaster.
# All writes are protected by _state_lock.
_state_lock = threading.Lock()

bot_state: dict = {
    "is_running":       False,
    "capital":          0.0,
    "pnl":              0.0,
    "volume":           0.0,
    "num_fills":        0,
    "num_round_trips":  0,
    "active_orders":    [],        # list of {side, price, qty, id}
    "position":         {},        # {symbol, side, qty, entry_price, unrealized_pnl, margin}
    "last_price":       0.0,
    "uptime_seconds":   0,
    "stop_losses":      0,
    "symbol":           "ETHUSDT",
    "qty":              "0.01",
    "leverage":         5,
    "spread_pct":       0.0004,
    "max_loss":         10.0,
    "log_lines":        [],        # last 20 log lines
    "circuit_breaker":  False,
    "drawdown_pct":     0.0,
    "circuit_pct":      0.0,
}

# ─── Bot thread control ───────────────────────────────────────────────────────
_bot_thread:    Optional[threading.Thread] = None
_stop_event:    threading.Event = threading.Event()
_start_time:    Optional[float] = None

# ─── WebSocket manager ────────────────────────────────────────────────────────
_ws_clients: Set[WebSocket] = set()


class InMemoryLogHandler(logging.Handler):
    """Capture log records into bot_state['log_lines'] (max 20)."""
    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        with _state_lock:
            bot_state["log_lines"].append(msg)
            if len(bot_state["log_lines"]) > 20:
                bot_state["log_lines"].pop(0)


_log_handler = InMemoryLogHandler()
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
logging.getLogger().addHandler(_log_handler)


# ─── Instrumented MarketMaker subclass ────────────────────────────────────────

class InstrumentedMarketMaker(MarketMaker):
    """
    Extends MarketMaker to push live state into `bot_state` after every step.
    Also counts fills, round-trips, and stop-loss events.
    """

    def __init__(self, client: BitunixClient, config: MarketMakerConfig, stop_event: threading.Event):
        super().__init__(client, config)
        self._stop_event = stop_event
        self._fill_count = 0
        self._rt_count   = 0
        self._sl_count   = 0
        self._last_buy_price = 0.0
        self._last_sell_price = 0.0

    def _detect_fills(self, active_orders: list, mid: float):
        """Compare current pending orders on the exchange with our tracked active orders to detect fills."""
        current_ids = {o["id"] for o in active_orders}
        
        # Check Buy side
        if self.active_buy_id and self.active_buy_id not in current_ids:
            self._fill_count += 1
            qty = float(self.cfg.order_qty)
            price = self._last_buy_price if self._last_buy_price > 0 else (mid * (1 - self.cfg.spread_pct/2))
            val = qty * price
            self.total_volume += val
            logger.info(f"🎉 [FILL] BUY LIMIT filled! Price: {price:.2f} | Session Vol: +${val:.2f}")
            self.active_buy_id = None
            
        # Check Sell side
        if self.active_sell_id and self.active_sell_id not in current_ids:
            self._fill_count += 1
            qty = float(self.cfg.order_qty)
            price = self._last_sell_price if self._last_sell_price > 0 else (mid * (1 + self.cfg.spread_pct/2))
            val = qty * price
            self.total_volume += val
            logger.info(f"🎉 [FILL] SELL LIMIT filled! Price: {price:.2f} | Session Vol: +${val:.2f}")
            
            if self.active_buy_id is None: # Meaning buy filled earlier or just now
                self._rt_count += 1
                logger.info(f"🔁 [ROUND TRIP] Buy and Sell both filled!")
            self.active_sell_id = None

    def _update_state(self, mid: float, position: dict, active_orders: list):
        """Push current bot metrics into the shared state dict."""
        pos_side   = position.get("side", "FLAT") if position else "FLAT"
        pos_qty    = float(position.get("qty", 0))    if position else 0.0
        entry_px   = float(position.get("avgOpenPrice", 0)) if position else 0.0
        unreal_pnl = float(position.get("unrealizedPnl", 0)) if position else 0.0
        margin     = float(position.get("margin", 1))  if position else 1.0

        max_loss = self.cfg.max_total_loss_usdt
        drawdown_pct = min(100.0, abs(min(0.0, self.realized_pnl)) / max(max_loss, 0.01) * 100)
        circuit_pct  = drawdown_pct

        uptime = int(time.time() - _start_time) if _start_time else 0

        capital = 0.0
        try:
            acc = self.client.get_account()
            cap_raw = acc.get("data", {})
            if isinstance(cap_raw, list) and cap_raw:
                cap_raw = cap_raw[0]
            if isinstance(cap_raw, dict):
                capital = float(cap_raw.get("available", cap_raw.get("equity", 0)) or 0)
        except Exception:
            pass

        with _state_lock:
            bot_state["is_running"]      = self.running and not self._stop_event.is_set()
            bot_state["pnl"]             = round(self.realized_pnl, 4)
            bot_state["volume"]          = round(self.total_volume, 4)
            bot_state["num_fills"]       = self._fill_count
            bot_state["num_round_trips"] = self._rt_count
            bot_state["active_orders"]   = active_orders
            bot_state["last_price"]      = mid
            bot_state["uptime_seconds"]  = uptime
            bot_state["stop_losses"]     = self._sl_count
            bot_state["capital"]         = round(capital, 2)
            bot_state["circuit_breaker"] = self.realized_pnl <= -max_loss
            bot_state["drawdown_pct"]    = round(drawdown_pct, 2)
            bot_state["circuit_pct"]     = round(circuit_pct, 2)
            bot_state["position"] = {
                "symbol":        self.cfg.symbol,
                "side":          pos_side.upper() if pos_side else "FLAT",
                "qty":           pos_qty,
                "entry_price":   entry_px,
                "unrealized_pnl": unreal_pnl,
                "margin":        margin,
            }

    def _check_stop_loss(self, position: dict) -> bool:
        triggered = super()._check_stop_loss(position)
        if triggered:
            self._sl_count += 1
        return triggered

    def _place_maker_orders(self, mid: float):
        self._last_buy_price = mid * (1 - self.cfg.spread_pct / 2)
        self._last_sell_price = mid * (1 + self.cfg.spread_pct / 2)
        super()._place_maker_orders(mid)

    def step(self):
        """Override step to update shared state after each cycle."""
        mid = self._get_mid_price()
        if mid == 0:
            logger.warning("Could not get price, skipping step")
            return

        logger.info(f"📊 {self.cfg.symbol}  mid={mid:.4f}  volume={self.total_volume:.2f}  pnl={self.realized_pnl:.4f}")

        # Fetch pending orders from exchange
        active_orders = []
        try:
            resp = self.client.get_pending_orders(self.cfg.symbol)
            orders = resp.get("data", []) or []
            for o in orders:
                active_orders.append({
                    "id":    o.get("orderId", ""),
                    "side":  o.get("side", ""),
                    "price": float(o.get("price", 0)),
                    "qty":   o.get("qty", ""),
                })
        except Exception:
            pass

        # Detect any executed fills *before* checking stops or reposting
        self._detect_fills(active_orders, mid)

        # Circuit breaker
        if self._check_circuit_breaker():
            self.running = False
            position = self._get_position()
            self._update_state(mid, position, active_orders)
            return

        # Position + stop-loss
        position = self._get_position()
        if self._check_stop_loss(position):
            self._cancel_active_orders()
            self._update_state(mid, {}, [])
            return

        # Refresh orders if price drifted
        if self._should_refresh(mid):
            logger.info(f"🔄 Refreshing orders (mid {self.last_mid_price:.4f} → {mid:.4f})")
            self._cancel_active_orders()
            self._place_maker_orders(mid)
            self.last_mid_price = mid
            # Fetch active orders again since we just replaced them
            active_orders = []
            try:
                resp = self.client.get_pending_orders(self.cfg.symbol)
                orders = resp.get("data", []) or []
                for o in orders:
                    active_orders.append({
                        "id":    o.get("orderId", ""),
                        "side":  o.get("side", ""),
                        "price": float(o.get("price", 0)),
                        "qty":   o.get("qty", ""),
                    })
            except Exception:
                pass

        # Update shared state every step
        self._update_state(mid, position, active_orders)

    def run(self):
        """Run loop that respects the stop_event."""
        self.setup()
        logger.info(f"🚀 Market Maker started on {self.cfg.symbol}")

        while self.running and not self._stop_event.is_set():
            try:
                self.step()
            except Exception as e:
                logger.error(f"Unhandled error in step: {e}", exc_info=True)
            self._stop_event.wait(timeout=self.cfg.refresh_interval)

        logger.info("🧹 Cleaning up: cancelling all open orders...")
        self._cancel_active_orders()
        logger.info(f"📈 Session ended: Volume={self.total_volume:.4f} | PnL={self.realized_pnl:.4f} USDT")

        with _state_lock:
            bot_state["is_running"] = False


class InstrumentedAdaptiveMarketMaker(InstrumentedMarketMaker, AdaptiveMarketMaker):
    """
    Extends AdaptiveMarketMaker using InstrumentedMarketMaker's fill detection & state reporting.
    """

    def __init__(self, client: BitunixClient, config: AdaptiveConfig, stop_event: threading.Event):
        # Explicitly call constructors of both bases
        AdaptiveMarketMaker.__init__(self, client, config)
        self._stop_event = stop_event
        self._fill_count = 0
        self._rt_count   = 0
        self._sl_count   = 0
        self._last_buy_price = 0.0
        self._last_sell_price = 0.0

    def _update_state(self, mid: float, position: dict, active_orders: list):
        """Override to also push adaptive stats details."""
        # Call the base state updater first
        super()._update_state(mid, position, active_orders)
        
        # Then append adaptive stats
        with _state_lock:
            bot_state["adaptive_info"] = {
                "adaptive_mode":    True,
                "spread_pct":       round(self._current_spread_pct * 100, 4),
                "sl_streak":        self._sl_streak,
                "fill_streak":      self._fill_streak,
                "in_cool_down":     self._in_cool_down,
                "avg_capture":      round(self._avg_capture_ratio(), 2),
            }

    def _place_maker_orders(self, mid: float):
        # For adaptive, the spread is calculated during placement
        spread = self._adaptive_spread()
        self._current_spread_pct = spread
        self._last_buy_price = mid * (1 - spread / 2)
        self._last_sell_price = mid * (1 + spread / 2)
        
        # Call AdaptiveMarketMaker's placement method directly
        AdaptiveMarketMaker._place_maker_orders(self, mid)

    def _check_stop_loss(self, position: dict) -> bool:
        # Resolve to Adaptive's risk logic (which registers SL streaks)
        triggered = AdaptiveMarketMaker._check_stop_loss(self, position)
        if triggered:
            self._sl_count += 1
        return triggered

    def setup(self):
        # Resolve to Adaptive's setup to correctly configure and log adaptive settings
        AdaptiveMarketMaker.setup(self)

    def _compute_order_prices(self, mid: float, spread_pct: float) -> tuple[float, float]:
        # Resolve to Adaptive's price computation that accepts spread_pct
        return AdaptiveMarketMaker._compute_order_prices(self, mid, spread_pct)

    def step(self):
        """Override step using Adaptive's checks but with fill detection and state updates."""
        mid = self._get_mid_price()
        if mid == 0:
            logger.warning("Could not get price, skipping step")
            return

        logger.info(f"📊 [ADAPTIVE] {self.cfg.symbol}  mid={mid:.4f}  volume={self.total_volume:.2f}  pnl={self.realized_pnl:.4f}")

        # Fetch active orders
        active_orders = []
        try:
            resp = self.client.get_pending_orders(self.cfg.symbol)
            orders = resp.get("data", []) or []
            for o in orders:
                active_orders.append({
                    "id":    o.get("orderId", ""),
                    "side":  o.get("side", ""),
                    "price": float(o.get("price", 0)),
                    "qty":   o.get("qty", ""),
                })
        except Exception:
            pass

        # Detect executed fills
        self._detect_fills(active_orders, mid)

        # Circuit breaker
        if self._check_circuit_breaker():
            self.running = False
            position = self._get_position()
            self._update_state(mid, position, active_orders)
            return

        # Position + stop-loss
        position = self._get_position()
        if self._check_stop_loss(position):
            self._cancel_active_orders()
            self._update_state(mid, {}, [])
            return

        # Periodic fill-quality report
        if len(self._fill_records) > 0 and len(self._fill_records) % 10 == 0:
            self._log_fill_quality()

        # Refresh orders if price drifted
        if self._should_refresh(mid):
            logger.info(f"🔄 Refreshing orders (mid {self.last_mid_price:.4f} → {mid:.4f})")
            self._cancel_active_orders()
            self._place_maker_orders(mid)
            self.last_mid_price = mid
            # Fetch active orders again
            active_orders = []
            try:
                resp = self.client.get_pending_orders(self.cfg.symbol)
                orders = resp.get("data", []) or []
                for o in orders:
                    active_orders.append({
                        "id":    o.get("orderId", ""),
                        "side":  o.get("side", ""),
                        "price": float(o.get("price", 0)),
                        "qty":   o.get("qty", ""),
                    })
            except Exception:
                pass

        # Update state
        self._update_state(mid, position, active_orders)


# ─── FastAPI app ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🌐 Bitunix Dashboard Server started — http://localhost:8000")
    logger.info("   Dashboard: open dashboard/index.html in your browser")
    yield
    _stop_event.set()
    logger.info("Server shutting down, bot stop signal sent.")


app = FastAPI(title="Bitunix Market Maker Dashboard", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic models ──────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    symbol:           str   = "ETHUSDT"
    qty:              str   = "0.01"
    leverage:         int   = 5
    spread:           float = 0.0004
    max_loss:         float = 10.0
    adaptive:         bool  = True
    funding_bias:     bool  = True
    anti_martingale:  bool  = True
    time_aware:       bool  = True
    vol_adaptive:     bool  = True
    spread_capture:   bool  = True


# ─── REST endpoints ───────────────────────────────────────────────────────────

@app.get("/status")
def get_status():
    with _state_lock:
        return dict(bot_state)


@app.get("/config")
def get_config():
    with _state_lock:
        return {
            "symbol":     bot_state.get("symbol", "BTCUSDT"),
            "leverage":   bot_state.get("leverage", 5),
            "spread":     bot_state.get("spread_pct", 0.0004),
            "max_loss":   bot_state.get("max_loss", 10.0),
            "is_running": bot_state.get("is_running", False),
            "adaptive_info": bot_state.get("adaptive_info", {}),
        }


@app.post("/start")
def start_bot(req: StartRequest):
    global _bot_thread, _stop_event, _start_time

    if bot_state.get("is_running"):
        return {"ok": False, "detail": "Bot is already running"}

    if not API_KEY or not SECRET_KEY:
        return {"ok": False, "detail": "API keys not configured in .env"}

    _stop_event = threading.Event()
    _start_time = time.time()

    client = BitunixClient(API_KEY, SECRET_KEY)

    if req.adaptive:
        config = AdaptiveConfig(
            symbol=req.symbol,
            order_qty=req.qty,
            leverage=req.leverage,
            spread_pct=req.spread,
            base_spread_pct=req.spread,
            max_total_loss_usdt=req.max_loss,
            funding_bias=req.funding_bias,
            anti_martingale=req.anti_martingale,
            time_aware=req.time_aware,
            spread_capture_tracking=req.spread_capture,
        )
        mm = InstrumentedAdaptiveMarketMaker(client, config, _stop_event)
    else:
        config = MarketMakerConfig(
            symbol=req.symbol,
            order_qty=req.qty,
            leverage=req.leverage,
            spread_pct=req.spread,
            max_total_loss_usdt=req.max_loss,
        )
        mm = InstrumentedMarketMaker(client, config, _stop_event)

    with _state_lock:
        bot_state["is_running"]  = True
        bot_state["symbol"]      = req.symbol
        bot_state["leverage"]    = req.leverage
        bot_state["spread_pct"]  = req.spread
        bot_state["max_loss"]    = req.max_loss
        bot_state["pnl"]         = 0.0
        bot_state["volume"]      = 0.0
        bot_state["num_fills"]   = 0
        bot_state["stop_losses"] = 0
        bot_state["log_lines"]   = []

    _bot_thread = threading.Thread(target=mm.run, daemon=True, name="MarketMakerThread")
    _bot_thread.start()
    logger.info(f"✅ Bot started: {req.symbol} qty={req.qty} lev={req.leverage}x spread={req.spread} max_loss={req.max_loss}")

    return {"ok": True, "detail": f"Bot started on {req.symbol}"}


@app.post("/stop")
def stop_bot():
    global _stop_event

    if not bot_state.get("is_running"):
        return {"ok": False, "detail": "Bot is not running"}

    _stop_event.set()
    logger.info("🛑 Stop requested by user")

    with _state_lock:
        bot_state["is_running"] = False

    return {"ok": True, "detail": "Stop signal sent"}


# ─── WebSocket endpoint ───────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    logger.info(f"WebSocket client connected ({len(_ws_clients)} total)")
    try:
        while True:
            with _state_lock:
                payload = dict(bot_state)
            # Remove log_lines from the periodic payload to keep it lean
            snapshot = {k: v for k, v in payload.items() if k != "log_lines"}
            snapshot["log_lines"] = payload.get("log_lines", [])
            await ws.send_text(json.dumps(snapshot, default=str))
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _ws_clients.discard(ws)
        logger.info(f"WebSocket client disconnected ({len(_ws_clients)} remaining)")


# ─── Startup / Shutdown events ────────────────────────────────────────────────
# Handled via lifespan context manager during FastAPI app instantiation.


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
