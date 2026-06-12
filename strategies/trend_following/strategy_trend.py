"""
Trend-Following Strategy for Bitunix Futures (live + dry-run)
============================================================
Executable version of the edge validated in strategies/trend_following/RESULTS.md
(XLM 1h, EMA 12/100, hysteresis band 0.01, volatility targeting). Reuses the SAME
vectorized core (compute_position + vol_target) the backtester uses, so the live
signal matches what we validated.

Execution model — POSITION-BASED rebalancing:
  Each new closed micro bar (1h) we compute a TARGET position (signed units) and
  rebalance toward it with a MARKET order (trend chases → taker). Most bars the
  target is unchanged (no action). This is low-frequency.

Risk: session circuit breaker + daily loss limit. Trend lets winners run and cuts
losers via the signal flip itself, so we DON'T use a tight stop (it would harm the
edge); the daily/session breakers protect the small account from a bad regime.
"""

import time
import math
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from shared.bitunix_client import BitunixClient
from strategies.trend_following.core import TrendParams, compute_position, vol_target

logger = logging.getLogger(__name__)

_TF_MS = {"5min": 300_000, "15min": 900_000, "30min": 1_800_000,
          "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}
# kline endpoint uses exchange interval codes
_TF_CODE = {"5min": "5m", "15min": "15m", "30min": "30m",
            "1h": "1h", "2h": "2h", "4h": "4h"}


@dataclass
class TrendConfig:
    symbol: str = "XLMUSDT"
    micro_tf: str = "1h"               # signal/rebalance timeframe
    params: TrendParams = field(default_factory=lambda: TrendParams(
        tf="1h", fast=12, slow=100, min_sep=0.01, allow_short=True))

    # volatility targeting (sizing). vt_target=0 disables (fixed base_qty).
    vt_target: float = 0.0             # per-bar vol target (e.g. 0.006)
    vt_lookback: int = 72
    vt_max_lev: float = 1.5

    base_qty: float = 80               # XLMUSDT min 80 units
    qty_precision: int = 0
    price_tick: float = 0.00001
    leverage: int = 3

    poll_interval: float = 30.0        # check for a new closed bar this often
    max_total_loss_usdt: float = 4.0   # session circuit breaker
    daily_loss_limit_usdt: float = 1.0 # pause for the UTC day

    dry_run: bool = True


class TrendFollower:
    def __init__(self, client: BitunixClient, cfg: TrendConfig):
        self.client = client
        self.cfg = cfg
        self.tf_ms = _TF_MS.get(cfg.micro_tf, 3_600_000)

        self.position: float = 0.0     # signed units (dry-run truth; live = mirror)
        self.entry_px: float = 0.0
        self.last_df: Optional[pd.DataFrame] = None   # cached for dashboards
        self.last_bar_time: int = 0
        self.last_price: float = 0.0
        self.signal: float = 0.0
        self.target: float = 0.0

        self.realized_pnl: float = 0.0
        self.daily_pnl: float = 0.0
        self.trade_count: int = 0
        self.day = datetime.now(timezone.utc).date()
        self.running = True

    # ─── Market data ─────────────────────────────────────────────────────────

    def _closed_bars(self) -> Optional[pd.DataFrame]:
        """Fetch klines and return a DataFrame of CLOSED bars (ts index, ohlc)."""
        try:
            code = _TF_CODE.get(self.cfg.micro_tf, "1h")
            resp = self.client.get_kline(self.cfg.symbol, granularity=code, limit=200)
            data = resp.get("data", []) or []
            rows = [{"t": int(b["time"]), "open": float(b["open"]), "high": float(b["high"]),
                     "low": float(b["low"]), "close": float(b["close"])} for b in data]
            rows.sort(key=lambda r: r["t"])
            now = int(time.time() * 1000)
            rows = [r for r in rows if r["t"] + self.tf_ms <= now]  # only closed
            if len(rows) < self.cfg.params.slow + 5:
                return None
            df = pd.DataFrame(rows)
            df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
            return df
        except Exception as e:
            logger.error(f"kline fetch failed: {e}")
            return None

    def _mark_price(self) -> float:
        try:
            d = self.client.get_depth(self.cfg.symbol, limit=5).get("data", {})
            return (float(d["bids"][0][0]) + float(d["asks"][0][0])) / 2.0
        except Exception:
            return self.last_price

    def _live_position(self) -> float:
        """Signed open position from the exchange (live mode)."""
        try:
            for p in self.client.get_positions(self.cfg.symbol).get("data", []) or []:
                if p.get("symbol") == self.cfg.symbol and float(p.get("qty", 0)) != 0:
                    q = float(p["qty"])
                    return q if str(p.get("side", "")).lower() == "long" else -q
        except Exception as e:
            logger.error(f"position fetch failed: {e}")
        return 0.0

    # ─── Signal → target position ────────────────────────────────────────────

    def _target_position(self, df: pd.DataFrame) -> tuple[float, float]:
        """Return (signal in {-1,0,+1}, target_units signed)."""
        pos = compute_position(df, self.cfg.params)
        sig = float(pos.iloc[-1])
        if self.cfg.vt_target > 0:
            scaled = vol_target(df, pos, self.cfg.vt_target,
                                self.cfg.vt_lookback, self.cfg.vt_max_lev)
            mult = abs(float(scaled.iloc[-1]))
        else:
            mult = 1.0
        raw_units = self.cfg.base_qty * mult
        # respect minimum order size: below min → flat; else round to integer units
        if raw_units < self.cfg.base_qty * 0.5:
            units = 0.0
        else:
            units = max(self.cfg.base_qty, round(raw_units))
        return sig, sig * units

    def _fmt_qty(self, q: float) -> str:
        q = round(abs(q), self.cfg.qty_precision)
        return f"{int(q)}" if self.cfg.qty_precision == 0 else f"{q:.{self.cfg.qty_precision}f}"

    # ─── Rebalance ───────────────────────────────────────────────────────────

    def _rebalance(self, target: float, price: float):
        """Move current position toward target via market order(s)."""
        cur = self.position if self.cfg.dry_run else self._live_position()
        if abs(target - cur) < 1:           # within one unit → nothing to do
            return
        logger.info(f"⚖️  rebalance {cur:+.0f} -> {target:+.0f} @ {price:.5f}")
        if self.cfg.dry_run:
            self._sim_fill(target, price)
        else:
            self._live_rebalance(cur, target)

    def _sim_fill(self, target: float, price: float):
        """Dry-run: update position + realized PnL as a real position would behave."""
        cur, fee = self.position, 0.0006   # taker
        # realize PnL on any portion being closed (reduce or flip)
        if cur != 0 and (math.copysign(1, target) != math.copysign(1, cur) or abs(target) < abs(cur)):
            closed = abs(cur) - (abs(target) if math.copysign(1, target) == math.copysign(1, cur) else 0.0)
            closed = min(abs(cur), closed if closed > 0 else abs(cur))
            pnl = (price - self.entry_px) * math.copysign(1, cur) * closed
            cost = price * closed * fee
            self.realized_pnl += pnl - cost
            self.daily_pnl += pnl - cost
            self.trade_count += 1
            logger.info(f"📒 closed {closed:.0f} units: pnl={pnl-cost:+.4f} USDT "
                        f"| session={self.realized_pnl:+.4f}")
        # establish new average entry for the resulting position
        if target == 0:
            self.entry_px = 0.0
        elif cur == 0 or math.copysign(1, target) != math.copysign(1, cur):
            self.entry_px = price                       # fresh position
        elif abs(target) > abs(cur):                    # added in same direction
            added = abs(target) - abs(cur)
            self.entry_px = (self.entry_px * abs(cur) + price * added) / abs(target)
        self.position = target

    def _live_rebalance(self, cur: float, target: float):
        diff = target - cur
        side = "BUY" if diff > 0 else "SELL"
        # if flipping sign, close current first (reduce-only), then open the rest
        try:
            if cur != 0 and math.copysign(1, target) != math.copysign(1, cur):
                close_side = "BUY" if cur < 0 else "SELL"
                self.client.place_order(symbol=self.cfg.symbol, side=close_side,
                                        order_type="MARKET", qty=self._fmt_qty(cur),
                                        reduce_only=True, trade_side="CLOSE",
                                        client_id=f"tf_c_{uuid.uuid4().hex[:8]}")
                open_units = abs(target)
                open_side = "BUY" if target > 0 else "SELL"
                self.client.place_order(symbol=self.cfg.symbol, side=open_side,
                                        order_type="MARKET", qty=self._fmt_qty(open_units),
                                        reduce_only=False, trade_side="OPEN",
                                        client_id=f"tf_o_{uuid.uuid4().hex[:8]}")
            else:
                reduce = abs(target) < abs(cur)
                self.client.place_order(symbol=self.cfg.symbol, side=side,
                                        order_type="MARKET", qty=self._fmt_qty(diff),
                                        reduce_only=reduce,
                                        trade_side="CLOSE" if reduce else "OPEN",
                                        client_id=f"tf_{uuid.uuid4().hex[:8]}")
        except Exception as e:
            logger.error(f"live rebalance failed: {e}")

    # ─── Risk ────────────────────────────────────────────────────────────────

    def _risk_halt(self) -> bool:
        if self.realized_pnl <= -self.cfg.max_total_loss_usdt:
            logger.critical(f"🚨 CIRCUIT BREAKER: session {self.realized_pnl:.4f} "
                            f"<= -{self.cfg.max_total_loss_usdt}. Flattening & stopping.")
            self._rebalance(0.0, self._mark_price())
            return True
        return False

    def _day_paused(self) -> bool:
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day, self.daily_pnl = today, 0.0
            logger.info("🌅 New UTC day — daily PnL reset")
        return self.daily_pnl <= -self.cfg.daily_loss_limit_usdt

    def unrealized(self, price: float) -> float:
        if self.position == 0 or self.entry_px == 0:
            return 0.0
        return (price - self.entry_px) * math.copysign(1, self.position) * abs(self.position)

    # ─── Loop ────────────────────────────────────────────────────────────────

    def setup(self):
        logger.info(f"🔧 leverage={self.cfg.leverage}x for {self.cfg.symbol}")
        if not self.cfg.dry_run:
            try:
                self.client.set_leverage(self.cfg.symbol, self.cfg.leverage)
            except Exception as e:
                logger.warning(f"set_leverage failed: {e}")

    def step(self):
        if self._risk_halt():
            self.running = False
            return
        df = self._closed_bars()
        if df is None:
            return
        self.last_df = df
        self.last_price = self._mark_price() or float(df["close"].iloc[-1])
        newest = int(df["t"].iloc[-1])
        if newest == self.last_bar_time:
            return                                       # no new closed bar
        self.last_bar_time = newest

        sig, target = self._target_position(df)
        self.signal = sig
        # daily pause: don't open new exposure, but allow closing toward flat
        if self._day_paused() and abs(target) > abs(self.position):
            logger.info(f"⏸️  daily loss limit hit — not increasing exposure")
            target = 0.0 if math.copysign(1, target) != math.copysign(1, self.position or target) else self.position
        self.target = target

        logger.info(f"📊 {self.cfg.symbol} {self.cfg.micro_tf} close={df['close'].iloc[-1]:.5f} "
                    f"signal={sig:+.0f} pos={self.position:+.0f} target={target:+.0f} "
                    f"pnl={self.realized_pnl:+.4f}")
        self._rebalance(target, self.last_price)

    def run(self):
        self.setup()
        mode = "DRY-RUN (no real orders)" if self.cfg.dry_run else "LIVE"
        logger.info(f"🚀 Trend-Follower [{mode}] {self.cfg.symbol} {self.cfg.micro_tf} "
                    f"EMA {self.cfg.params.fast}/{self.cfg.params.slow} sep={self.cfg.params.min_sep} "
                    f"vt={self.cfg.vt_target or 'off'} qty={self.cfg.base_qty}")
        while self.running:
            try:
                self.step()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"step error: {e}", exc_info=True)
            time.sleep(self.cfg.poll_interval)
        if not self.cfg.dry_run:
            self._rebalance(0.0, self._mark_price())     # flatten on exit
        logger.info(f"📈 Session: trades={self.trade_count} realized={self.realized_pnl:+.4f} USDT")
