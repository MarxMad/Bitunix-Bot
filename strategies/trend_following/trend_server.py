"""
Trend-Following — Dedicated FastAPI Dashboard Server (port 8002)
===============================================================
Runs ONLY the trend-following strategy, separate from the volume (8000) and
mean-reversion (8001) dashboards. Serves dashboard/trend.html and pushes live
state (price + EMAs, position/target, PnL, trades) over WebSocket.

Run:  python strategies/trend_following/trend_server.py   ->  http://localhost:8002
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))

import os, json, time, asyncio, logging, threading
from typing import Optional, Set
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from shared.bitunix_client import BitunixClient
from strategies.trend_following.core import TrendParams
from strategies.trend_following.strategy_trend import TrendFollower, TrendConfig

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("trend_server")

load_dotenv()
API_KEY = os.getenv("BITUNIX_API_KEY", os.getenv("API_KEY", ""))
SECRET_KEY = os.getenv("BITUNIX_SECRET_KEY", os.getenv("SECRET_KEY", ""))
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "trend.html")

_lock = threading.Lock()
state: dict = {
    "running": False, "mode": "—", "symbol": "XLMUSDT", "tf": "1h",
    "price": 0.0, "signal": 0.0, "position": 0.0, "target": 0.0, "entry": 0.0,
    "fast": 12, "slow": 100,
    "realized_pnl": 0.0, "daily_pnl": 0.0, "unrealized": 0.0, "trade_count": 0,
    "session_loss_limit": 4.0, "daily_loss_limit": 1.0, "uptime_seconds": 0,
    "price_history": [], "equity_curve": [], "trades": [], "log_lines": [],
}
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_start_time: Optional[float] = None
_ws: Set[WebSocket] = set()


class _LogHandler(logging.Handler):
    def emit(self, record):
        with _lock:
            state["log_lines"].append(self.format(record))
            if len(state["log_lines"]) > 40:
                state["log_lines"].pop(0)


_h = _LogHandler(); _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
logging.getLogger().addHandler(_h)


class InstrumentedTrend(TrendFollower):
    def __init__(self, client, cfg, stop_event):
        super().__init__(client, cfg)
        self._stop = stop_event
        self._prev_realized = 0.0

    def _sim_fill(self, target, price):
        before = self.realized_pnl
        super()._sim_fill(target, price)
        if self.realized_pnl != before:      # a trade closed -> record
            with _lock:
                state["trades"].append({
                    "n": self.trade_count, "price": round(price, 6),
                    "pnl": round(self.realized_pnl - before, 4),
                    "t": datetime.now(timezone.utc).strftime("%H:%M:%S")})
                if len(state["trades"]) > 50:
                    state["trades"].pop(0)
                state["equity_curve"].append({"n": self.trade_count,
                                              "equity": round(self.realized_pnl, 4)})
                if len(state["equity_curve"]) > 200:
                    state["equity_curve"].pop(0)

    def _push(self):
        df = self.last_df
        hist = []
        if df is not None:
            ef = df["close"].ewm(span=self.cfg.params.fast, adjust=False).mean()
            es = df["close"].ewm(span=self.cfg.params.slow, adjust=False).mean()
            tail = df.tail(120)
            for ts, c, f, s in zip(tail.index, tail["close"], ef.tail(120), es.tail(120)):
                hist.append({"t": ts.strftime("%m-%d %H:%M"), "close": round(float(c), 6),
                             "ef": round(float(f), 6), "es": round(float(s), 6)})
        with _lock:
            state.update({
                "running": self.running and not self._stop.is_set(),
                "mode": "DRY-RUN" if self.cfg.dry_run else "LIVE",
                "symbol": self.cfg.symbol, "tf": self.cfg.micro_tf,
                "price": round(self.last_price, 6), "signal": self.signal,
                "position": self.position, "target": self.target,
                "entry": round(self.entry_px, 6),
                "fast": self.cfg.params.fast, "slow": self.cfg.params.slow,
                "realized_pnl": round(self.realized_pnl, 4),
                "daily_pnl": round(self.daily_pnl, 4),
                "unrealized": round(self.unrealized(self.last_price), 4),
                "trade_count": self.trade_count,
                "session_loss_limit": self.cfg.max_total_loss_usdt,
                "daily_loss_limit": self.cfg.daily_loss_limit_usdt,
                "uptime_seconds": int(time.time() - _start_time) if _start_time else 0,
            })
            if hist:
                state["price_history"] = hist

    def step(self):
        super().step()
        self._push()

    def run(self):
        self.setup(); self._push()
        while self.running and not self._stop.is_set():
            try:
                self.step()
            except Exception as e:
                logger.error(f"step error: {e}", exc_info=True)
            self._stop.wait(timeout=self.cfg.poll_interval)
        if not self.cfg.dry_run:
            self._rebalance(0.0, self._mark_price())
        with _lock:
            state["running"] = False


@asynccontextmanager
async def lifespan(app):
    logger.info("🌐 Trend-Following Dashboard — http://localhost:8002")
    yield
    _stop.set()


app = FastAPI(title="Bitunix Trend-Following Dashboard", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class StartReq(BaseModel):
    symbol: str = "XLMUSDT"
    tf: str = "1h"
    fast: int = 12
    slow: int = 100
    min_sep: float = 0.01
    vt_target: float = 0.006
    base_qty: float = 80
    leverage: int = 3
    dry_run: bool = True


@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open(HTML_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>dashboard/trend.html not found</h1>"


@app.get("/status")
def status():
    with _lock:
        return dict(state)


@app.post("/start")
def start(r: StartReq):
    global _thread, _stop, _start_time
    if state.get("running"):
        return {"ok": False, "detail": "already running"}
    if not r.dry_run and (not API_KEY or not SECRET_KEY):
        return {"ok": False, "detail": "API keys not configured for LIVE"}
    _stop = threading.Event(); _start_time = time.time()
    client = BitunixClient(API_KEY or "public", SECRET_KEY or "public")
    cfg = TrendConfig(
        symbol=r.symbol, micro_tf=r.tf,
        params=TrendParams(tf=r.tf, fast=r.fast, slow=r.slow, min_sep=r.min_sep),
        vt_target=r.vt_target, base_qty=r.base_qty, leverage=r.leverage, dry_run=r.dry_run)
    with _lock:
        state.update({"price_history": [], "equity_curve": [], "trades": [],
                      "log_lines": [], "realized_pnl": 0.0, "daily_pnl": 0.0,
                      "trade_count": 0, "position": 0.0})
    mm = InstrumentedTrend(client, cfg, _stop)
    _thread = threading.Thread(target=mm.run, daemon=True, name="TrendThread")
    _thread.start()
    logger.info(f"✅ Started {r.symbol} {r.tf} EMA{r.fast}/{r.slow} vt={r.vt_target} dry={r.dry_run}")
    return {"ok": True, "detail": f"started {r.symbol}"}


@app.post("/stop")
def stop():
    if not state.get("running"):
        return {"ok": False, "detail": "not running"}
    _stop.set()
    with _lock:
        state["running"] = False
    return {"ok": True, "detail": "stopping"}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept(); _ws.add(sock)
    try:
        while True:
            with _lock:
                payload = dict(state)
            await sock.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _ws.discard(sock)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
