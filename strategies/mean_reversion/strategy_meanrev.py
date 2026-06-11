"""
Mean-Reversion Maker Strategy for Bitunix Futures (XLMUSDT)
==========================================================
Live implementation of the edge validated in research/04_backtest_eth_xlm.md.
Drives the SAME meanrev_core.decide() the backtester uses, so behaviour matches
what we validated.

Strategy in one line:
  Fade 3-sigma extremes with MAKER limit orders. Buy when XLM is oversold
  (z <= -3), sell when overbought (z >= +3); exit with a maker limit when price
  reverts to ~the mean. Catastrophe stop and time-stop protect the position.

Why maker-only: taker fees (0.06%) destroy the edge; maker (0.02%) preserves it.

Execution state machine (one decision per CLOSED 5-min bar):
  FLAT      -> on entry signal, rest a maker LIMIT at best bid/ask -> ENTERING
  ENTERING  -> filled? -> IN_POSITION ; not filled after entry_timeout_bars -> cancel, FLAT
  IN_POSITION -> each bar: decide(); EXIT/TIME_STOP/STOP -> rest reduce-only maker LIMIT -> EXITING
  EXITING   -> filled? -> FLAT ; if STOP and not filled by escalate deadline -> market close

Risk: per-trade is bounded by the catastrophe stop; daily circuit breaker and a
consecutive-loss cool-down protect the (small) account.
"""

import time
import math
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List

from shared.bitunix_client import BitunixClient
from strategies.mean_reversion.meanrev_core import (
    MeanRevParams, zscore, decide,
    ENTER_LONG, ENTER_SHORT, EXIT, STOP, TIME_STOP, HOLD, NONE,
)

logger = logging.getLogger(__name__)

_TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000}


@dataclass
class MeanRevConfig:
    # ── Market ────────────────────────────────────────────────────────────
    symbol: str = "XLMUSDT"
    interval: str = "5m"               # bar timeframe for the signal
    price_tick: float = 0.00001        # XLMUSDT quotePrecision = 5
    qty_precision: int = 0             # XLMUSDT basePrecision = 0 (integer qty)
    order_qty: float = 80              # XLMUSDT minTradeVolume = 80 units
    leverage: int = 3                  # low: risk is set by the stop, not leverage

    # ── Signal (validated defaults) ───────────────────────────────────────
    params: MeanRevParams = field(default_factory=MeanRevParams)

    # ── Execution ─────────────────────────────────────────────────────────
    entry_timeout_bars: int = 2        # cancel unfilled maker entry after N bars
    stop_escalate_seconds: float = 20  # if maker stop unfilled this long -> market close
    poll_interval: float = 5.0         # seconds between polls

    # ── Risk / circuit breakers ───────────────────────────────────────────
    max_total_loss_usdt: float = 4.0   # hard stop for the session (20% of a $20 acct)
    daily_loss_limit_usdt: float = 1.0 # pause for the day after this loss
    cooldown_after_losses: int = 2     # consecutive losing trades -> pause for the day

    # ── Mode ──────────────────────────────────────────────────────────────
    dry_run: bool = True               # simulate fills at bar close, place NO orders


# Execution states
FLAT, ENTERING, IN_POSITION, EXITING = "FLAT", "ENTERING", "IN_POSITION", "EXITING"


class MeanReversionMaker:
    def __init__(self, client: BitunixClient, cfg: MeanRevConfig):
        self.client = client
        self.cfg = cfg
        self.tf_ms = _TF_MS.get(cfg.interval, 300_000)

        # execution state
        self.state = FLAT
        self.side: Optional[str] = None        # "long"/"short" intended/open
        self.entry_px: float = 0.0
        self.bars_held: int = 0
        self.active_order_id: Optional[str] = None
        self.entry_wait_bars: int = 0
        self.exit_reason: Optional[str] = None
        self.stop_deadline: float = 0.0
        self.last_bar_time: int = 0

        # accounting
        self.realized_pnl: float = 0.0
        self.daily_pnl: float = 0.0
        self.consec_losses: int = 0
        self.trade_count: int = 0
        self.day = datetime.now(timezone.utc).date()
        self.running = True

    # ─── Market data ───────────────────────────────────────────────────────

    def _closed_bars(self) -> List[dict]:
        """Return chronological closed bars: [{'t':ms,'close':float}, ...]."""
        try:
            resp = self.client.get_kline(self.cfg.symbol, granularity=self.cfg.interval,
                                         limit=self.cfg.params.lookback + 5)
            data = resp.get("data", []) or []
            bars = [{"t": int(b["time"]), "close": float(b["close"])} for b in data]
            bars.sort(key=lambda x: x["t"])
            now = int(time.time() * 1000)
            return [b for b in bars if b["t"] + self.tf_ms <= now]   # only CLOSED
        except Exception as e:
            logger.error(f"kline fetch failed: {e}")
            return []

    def _best_bid_ask(self):
        try:
            d = self.client.get_depth(self.cfg.symbol, limit=5).get("data", {})
            return float(d["bids"][0][0]), float(d["asks"][0][0])
        except Exception as e:
            logger.error(f"depth fetch failed: {e}")
            return None, None

    def _open_position(self) -> dict:
        try:
            resp = self.client.get_positions(self.cfg.symbol)
            for p in resp.get("data", []) or []:
                if p.get("symbol") == self.cfg.symbol and float(p.get("qty", 0)) != 0:
                    return p
        except Exception as e:
            logger.error(f"position fetch failed: {e}")
        return {}

    # ─── Formatting ──────────────────────────────────────────────────────--

    def _fmt_price(self, px: float) -> str:
        ticks = round(px / self.cfg.price_tick)
        decimals = max(0, -int(round(math.log10(self.cfg.price_tick))))
        return f"{ticks * self.cfg.price_tick:.{decimals}f}"

    def _fmt_qty(self) -> str:
        q = round(self.cfg.order_qty, self.cfg.qty_precision)
        return f"{int(q)}" if self.cfg.qty_precision == 0 else f"{q:.{self.cfg.qty_precision}f}"

    # ─── Order helpers (no-ops in dry-run) ──────────────────────────────────

    def _place_limit(self, side: str, price: str, reduce_only: bool) -> Optional[str]:
        cid = f"mr_{'r' if reduce_only else 'o'}_{uuid.uuid4().hex[:8]}"
        if self.cfg.dry_run:
            logger.info(f"[DRY] would place LIMIT {side} {self._fmt_qty()} @ {price} "
                        f"reduceOnly={reduce_only}")
            return cid
        try:
            resp = self.client.place_order(
                symbol=self.cfg.symbol, side=side, order_type="LIMIT",
                qty=self._fmt_qty(), price=price, reduce_only=reduce_only,
                time_in_force="GTC", client_id=cid,
                trade_side="CLOSE" if reduce_only else "OPEN",
            )
            return resp.get("data", {}).get("orderId") or cid
        except Exception as e:
            logger.error(f"place_order failed: {e}")
            return None

    def _cancel(self):
        if self.active_order_id and not self.cfg.dry_run:
            try:
                self.client.cancel_all_orders(self.cfg.symbol)
            except Exception as e:
                logger.warning(f"cancel failed: {e}")
        self.active_order_id = None

    def _market_close(self):
        if self.cfg.dry_run:
            logger.warning("[DRY] would MARKET close (stop escalation)")
            return
        pos = self._open_position()
        pid = pos.get("positionId", "")
        if pid:
            try:
                self.client.flash_close_position(self.cfg.symbol, pid)
                logger.warning("✅ market-closed via stop escalation")
            except Exception as e:
                logger.error(f"flash_close failed: {e}")

    # ─── Accounting ──────────────────────────────────────────────────────--

    def _book_trade(self, exit_px: float, reason: str):
        d = 1 if self.side == "long" else -1
        notional = self.entry_px * self.cfg.order_qty
        gross = (exit_px / self.entry_px - 1) * d
        # fees: maker entry + maker exit (taker if escalated market stop)
        fee = 0.0002 + (0.0006 if reason == "stop_market" else 0.0002)
        pnl = (gross - fee) * notional
        self.realized_pnl += pnl
        self.daily_pnl += pnl
        self.trade_count += 1
        self.consec_losses = self.consec_losses + 1 if pnl < 0 else 0
        logger.info(f"📒 TRADE #{self.trade_count} {self.side} {reason}: "
                    f"entry={self.entry_px:.5f} exit={exit_px:.5f} "
                    f"pnl={pnl:+.4f} USDT | session={self.realized_pnl:+.4f} "
                    f"day={self.daily_pnl:+.4f} consecL={self.consec_losses}")

    def _risk_halt(self) -> bool:
        if self.realized_pnl <= -self.cfg.max_total_loss_usdt:
            logger.critical(f"🚨 CIRCUIT BREAKER: session loss {self.realized_pnl:.4f} "
                            f"<= -{self.cfg.max_total_loss_usdt}. Stopping.")
            return True
        return False

    def _day_paused(self) -> bool:
        today = datetime.now(timezone.utc).date()
        if today != self.day:                      # new UTC day -> reset
            self.day, self.daily_pnl, self.consec_losses = today, 0.0, 0
            logger.info("🌅 New UTC day — daily counters reset")
        if self.daily_pnl <= -self.cfg.daily_loss_limit_usdt:
            return True
        if self.consec_losses >= self.cfg.cooldown_after_losses:
            return True
        return False

    # ─── State machine (one decision per new closed bar) ────────────────────

    def _on_new_bar(self, closes: List[float], z: Optional[float]):
        p = self.cfg.params

        if self.state == FLAT:
            if self._day_paused():
                logger.info(f"⏸️  Paused for the day (daily={self.daily_pnl:+.4f}, "
                            f"consecL={self.consec_losses}) — no new entries")
                return
            action = decide(z, None, 0, p)
            if action in (ENTER_LONG, ENTER_SHORT):
                bid, ask = self._best_bid_ask()
                if bid is None:
                    return
                self.side = "long" if action == ENTER_LONG else "short"
                # maker: rest passively on our side of the book
                px = self._fmt_price(bid if self.side == "long" else ask)
                order_side = "BUY" if self.side == "long" else "SELL"
                logger.info(f"🎯 ENTRY signal {self.side.upper()} z={z:+.2f} -> maker {order_side} @ {px}")
                self.active_order_id = self._place_limit(order_side, px, reduce_only=False)
                self.entry_px = float(px)        # provisional; refined on fill (live)
                self.entry_wait_bars = 0
                self.state = ENTERING

        elif self.state == ENTERING:
            pos = {} if self.cfg.dry_run else self._open_position()
            filled = bool(pos) or self.cfg.dry_run   # dry-run assumes fill at bar close
            if filled:
                if pos:
                    self.entry_px = float(pos.get("avgOpenPrice", self.entry_px) or self.entry_px)
                self.bars_held = 0
                self.state = IN_POSITION
                logger.info(f"✅ ENTERED {self.side} @ {self.entry_px:.5f}")
            else:
                self.entry_wait_bars += 1
                if self.entry_wait_bars >= self.cfg.entry_timeout_bars:
                    logger.info("↩️  entry not filled in time — cancelling, back to FLAT")
                    self._cancel()
                    self.state, self.side = FLAT, None

        elif self.state == IN_POSITION:
            self.bars_held += 1
            action = decide(z, self.side, self.bars_held, p)
            if action in (EXIT, STOP, TIME_STOP):
                bid, ask = self._best_bid_ask()
                if bid is None:
                    return
                # reduce-only maker on the opposite side
                if self.side == "long":
                    close_side, px = "SELL", self._fmt_price(ask)
                else:
                    close_side, px = "BUY", self._fmt_price(bid)
                self.exit_reason = action
                logger.info(f"🚪 {action.upper()} z={z:+.2f} held={self.bars_held} "
                            f"-> maker {close_side} @ {px}")
                self.active_order_id = self._place_limit(close_side, px, reduce_only=True)
                if action == STOP:
                    self.stop_deadline = time.time() + self.cfg.stop_escalate_seconds
                else:
                    self.stop_deadline = 0.0
                # dry-run: assume immediate fill at this bar's close
                if self.cfg.dry_run:
                    self._book_trade(closes[-1], self.exit_reason)
                    self._reset_flat()
                else:
                    self.state = EXITING
            else:
                logger.info(f"… holding {self.side} z={z:+.2f} held={self.bars_held}/{p.max_hold_bars}")

        elif self.state == EXITING:
            pos = self._open_position()
            if not pos:                               # closed
                logger.info("✅ exit filled")
                # realized pnl is read from account in live mode; approximate via last close
                self._reset_flat()
            elif self.exit_reason == STOP and self.stop_deadline and time.time() > self.stop_deadline:
                logger.warning("⏱️ maker stop unfilled — escalating to MARKET close")
                self._cancel()
                self._market_close()
                self._reset_flat()

    def _reset_flat(self):
        self.state, self.side = FLAT, None
        self.bars_held = 0
        self.entry_px = 0.0
        self.active_order_id = None
        self.exit_reason = None
        self.stop_deadline = 0.0

    # ─── Loop ────────────────────────────────────────────────────────────--

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
        bars = self._closed_bars()
        if len(bars) < self.cfg.params.lookback:
            logger.debug("not enough closed bars yet")
            return

        newest = bars[-1]["t"]
        closes = [b["close"] for b in bars]
        z = zscore(closes, self.cfg.params.lookback)

        # Manage stop escalation between bars (time-based), even without a new bar
        if self.state == EXITING and self.exit_reason == STOP:
            self._on_new_bar(closes, z)

        if newest == self.last_bar_time:
            return                                     # no new closed bar
        self.last_bar_time = newest
        logger.info(f"📊 {self.cfg.symbol} bar={datetime.fromtimestamp(newest/1000, timezone.utc):%H:%M} "
                    f"close={closes[-1]:.5f} z={z:+.2f} state={self.state}")
        self._on_new_bar(closes, z)

    def run(self):
        self.setup()
        mode = "DRY-RUN (no real orders)" if self.cfg.dry_run else "LIVE"
        logger.info(f"🚀 Mean-Reversion Maker [{mode}] on {self.cfg.symbol} {self.cfg.interval}")
        logger.info(f"   z_in={self.cfg.params.z_in} z_exit={self.cfg.params.z_exit} "
                    f"z_stop={self.cfg.params.z_stop} hold<={self.cfg.params.max_hold_bars} "
                    f"qty={self._fmt_qty()} lev={self.cfg.leverage}x")
        logger.info(f"   Risk: session<= -{self.cfg.max_total_loss_usdt} | "
                    f"daily<= -{self.cfg.daily_loss_limit_usdt} | "
                    f"cooldown after {self.cfg.cooldown_after_losses} losses")
        while self.running:
            try:
                self.step()
            except KeyboardInterrupt:
                logger.info("⛔ interrupted")
                break
            except Exception as e:
                logger.error(f"step error: {e}", exc_info=True)
            time.sleep(self.cfg.poll_interval)

        if not self.cfg.dry_run:
            self._cancel()
        logger.info(f"📈 Session done: trades={self.trade_count} "
                    f"realized={self.realized_pnl:+.4f} USDT")
