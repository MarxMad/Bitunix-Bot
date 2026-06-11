"""
Mean-Reversion Strategy — Dedicated FastAPI Dashboard Server
============================================================
Separate from bot_server.py (the volume market-maker dashboard) so both can run
side by side. This one serves ONLY the XLM mean-reversion strategy.

Run:
    python meanrev_server.py
    # then open http://localhost:8001  (served directly)

Endpoints:
    GET  /              → the dashboard HTML
    GET  /status        → current strategy snapshot
    POST /start         → {symbol, qty, leverage, interval, z_in, z_exit, dry_run}
    POST /stop          → stop the strategy
    WS   /ws            → live JSON state every second
"""

import os
import json
import time
import asyncio
import logging
import threading
from typing import Optional, Set, List
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# ── make repo root importable when run directly ──
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))

from shared.bitunix_client import BitunixClient
from strategies.mean_reversion.meanrev_core import MeanRevParams
from strategies.mean_reversion.strategy_meanrev import MeanReversionMaker, MeanRevConfig

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("meanrev_server")

load_dotenv()
API_KEY = os.getenv("BITUNIX_API_KEY", os.getenv("API_KEY", ""))
SECRET_KEY = os.getenv("BITUNIX_SECRET_KEY", os.getenv("SECRET_KEY", ""))

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "dashboard", "meanrev.html")

# ─── Shared state ────────────────────────────────────────────────────────────
_lock = threading.Lock()
mr_state: dict = {
    "running": False, "mode": "—", "symbol": "XLMUSDT", "interval": "5m",
    "exec_state": "FLAT", "z": 0.0, "mid": 0.0,
    "z_in": 3.0, "z_exit": 0.5, "z_stop": 4.5,
    "position_side": None, "entry_px": 0.0, "bars_held": 0, "max_hold": 24,
    "realized_pnl": 0.0, "daily_pnl": 0.0, "trade_count": 0, "wins": 0,
    "consec_losses": 0, "session_loss_limit": 4.0, "daily_loss_limit": 1.0,
    "uptime_seconds": 0,
    "z_history": [], "equity_curve": [], "trades": [], "log_lines": [],
}

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_start_time: Optional[float] = None
_ws_clients: Set[WebSocket] = set()


class _LogHandler(logging.Handler):
    def emit(self, record):
        with _lock:
            mr_state["log_lines"].append(self.format(record))
            if len(mr_state["log_lines"]) > 40:
                mr_state["log_lines"].pop(0)


_h = _LogHandler()
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
logging.getLogger().addHandler(_h)


# ─── Instrumented strategy ─────────────────────────────────────────────────--

class InstrumentedMeanReversionMaker(MeanReversionMaker):
    """Pushes z-score / PnL / trade state into mr_state for the dashboard."""

    def __init__(self, client, cfg, stop_event):
        super().__init__(client, cfg)
        self._stop_event = stop_event
        self._last_z: Optional[float] = None
        self._last_mid: float = 0.0

    def _on_new_bar(self, closes, z):
        self._last_z = z
        self._last_mid = closes[-1]
        if z is not None:
            with _lock:
                mr_state["z_history"].append({
                    "t": datetime.now(timezone.utc).strftime("%H:%M"),
                    "z": round(z, 3), "price": round(closes[-1], 6),
                })
                if len(mr_state["z_history"]) > 180:
                    mr_state["z_history"].pop(0)
        super()._on_new_bar(closes, z)

    def _book_trade(self, exit_px, reason):
        before = self.realized_pnl
        side = self.side
        entry = self.entry_px
        super()._book_trade(exit_px, reason)
        pnl = self.realized_pnl - before
        with _lock:
            mr_state["trades"].append({
                "n": self.trade_count, "side": side, "reason": reason,
                "entry": round(entry, 6), "exit": round(exit_px, 6),
                "pnl": round(pnl, 4),
                "t": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            })
            if len(mr_state["trades"]) > 50:
                mr_state["trades"].pop(0)
            mr_state["equity_curve"].append({
                "n": self.trade_count, "equity": round(self.realized_pnl, 4),
            })
            if len(mr_state["equity_curve"]) > 200:
                mr_state["equity_curve"].pop(0)

    def _push(self):
        wins = sum(1 for t in mr_state["trades"] if t["pnl"] > 0)
        with _lock:
            mr_state.update({
                "running": self.running and not self._stop_event.is_set(),
                "mode": "DRY-RUN" if self.cfg.dry_run else "LIVE",
                "symbol": self.cfg.symbol, "interval": self.cfg.interval,
                "exec_state": self.state,
                "z": round(self._last_z, 3) if self._last_z is not None else 0.0,
                "mid": round(self._last_mid, 6),
                "z_in": self.cfg.params.z_in, "z_exit": self.cfg.params.z_exit,
                "z_stop": self.cfg.params.z_stop, "max_hold": self.cfg.params.max_hold_bars,
                "position_side": self.side, "entry_px": round(self.entry_px, 6),
                "bars_held": self.bars_held,
                "realized_pnl": round(self.realized_pnl, 4),
                "daily_pnl": round(self.daily_pnl, 4),
                "trade_count": self.trade_count, "wins": wins,
                "consec_losses": self.consec_losses,
                "session_loss_limit": self.cfg.max_total_loss_usdt,
                "daily_loss_limit": self.cfg.daily_loss_limit_usdt,
                "uptime_seconds": int(time.time() - _start_time) if _start_time else 0,
            })

    def step(self):
        super().step()
        self._push()

    def run(self):
        self.setup()
        self._push()
        logger.info(f"🚀 Mean-Reversion dashboard strategy on {self.cfg.symbol} "
                    f"[{'DRY-RUN' if self.cfg.dry_run else 'LIVE'}]")
        while self.running and not self._stop_event.is_set():
            try:
                self.step()
            except Exception as e:
                logger.error(f"step error: {e}", exc_info=True)
            self._stop_event.wait(timeout=self.cfg.poll_interval)
        if not self.cfg.dry_run:
            self._cancel()
        with _lock:
            mr_state["running"] = False
        logger.info(f"📈 Session ended: trades={self.trade_count} "
                    f"realized={self.realized_pnl:+.4f} USDT")


# ─── FastAPI ───────────────────────────────────────────────────────────────--

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🌐 Mean-Reversion Dashboard — http://localhost:8001")
    yield
    _stop_event.set()


app = FastAPI(title="Bitunix Mean-Reversion Dashboard", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class StartRequest(BaseModel):
    symbol: str = "XLMUSDT"
    qty: float = 80
    leverage: int = 3
    interval: str = "5m"
    z_in: float = 3.0
    z_exit: float = 0.5
    dry_run: bool = True


@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open(HTML_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>dashboard/meanrev.html not found</h1>"


@app.get("/status")
def status():
    with _lock:
        return dict(mr_state)


@app.post("/start")
def start(req: StartRequest):
    global _thread, _stop_event, _start_time
    if mr_state.get("running"):
        return {"ok": False, "detail": "Strategy already running"}
    if not req.dry_run and (not API_KEY or not SECRET_KEY):
        return {"ok": False, "detail": "API keys not configured in .env (needed for LIVE)"}

    _stop_event = threading.Event()
    _start_time = time.time()
    client = BitunixClient(API_KEY or "public", SECRET_KEY or "public")
    cfg = MeanRevConfig(
        symbol=req.symbol, interval=req.interval,
        order_qty=req.qty if req.qty >= 1 else 80, leverage=req.leverage,
        params=MeanRevParams(z_in=req.z_in, z_exit=req.z_exit),
        dry_run=req.dry_run,
    )
    # reset volatile state
    with _lock:
        mr_state.update({"z_history": [], "equity_curve": [], "trades": [],
                         "log_lines": [], "realized_pnl": 0.0, "daily_pnl": 0.0,
                         "trade_count": 0, "wins": 0, "consec_losses": 0})

    mm = InstrumentedMeanReversionMaker(client, cfg, _stop_event)
    _thread = threading.Thread(target=mm.run, daemon=True, name="MeanRevThread")
    _thread.start()
    logger.info(f"✅ Started {req.symbol} qty={req.qty} lev={req.leverage}x "
                f"z_in={req.z_in} z_exit={req.z_exit} dry_run={req.dry_run}")
    return {"ok": True, "detail": f"Started on {req.symbol}"}


@app.post("/stop")
def stop():
    if not mr_state.get("running"):
        return {"ok": False, "detail": "Not running"}
    _stop_event.set()
    with _lock:
        mr_state["running"] = False
    logger.info("🛑 Stop requested")
    return {"ok": True, "detail": "Stop signal sent"}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    _ws_clients.add(sock)
    try:
        while True:
            with _lock:
                payload = dict(mr_state)
            await sock.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _ws_clients.discard(sock)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
