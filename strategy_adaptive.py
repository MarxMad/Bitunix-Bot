"""
Adaptive Market Making Strategy for Bitunix Futures
====================================================
An enhanced version of strategy_market_maker.py with five optimizations:

  1. Volatility-Adaptive Spread
     Compute rolling 1-min candle volatility and widen/tighten the spread
     dynamically so the bot earns more in quiet markets and avoids being
     caught on the wrong side during trending moves.

  2. Funding Rate Bias
     Fetch the current perpetual funding rate and skew order qty toward the
     side that *earns* funding — passive income on top of the spread.

  3. Smart Anti-Martingale Sizing
     Track consecutive stop-loss streaks. After N SL hits in a row reduce
     qty by 50 % (cool-down mode). Restore once N_restore consecutive fills
     succeed.

  4. Spread Capture Tracking
     Compare the spread the bot expected to earn vs what it actually
     captured; if fill quality is poor, widen the spread automatically.

  5. Time-of-Day Awareness
     BTC has well-known intraday liquidity patterns. Reduce qty during the
     quiet Asian overnight window (03–07 UTC) and become slightly more
     aggressive during the high-volume EU/US overlap (09–11 & 14–16 UTC).

Usage:
    from strategy_adaptive import AdaptiveMarketMaker, AdaptiveConfig
    mm = AdaptiveMarketMaker(client, AdaptiveConfig(symbol="BTCUSDT"))
    mm.run()
"""

import time
import math
import logging
import uuid
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List

from bitunix_client import BitunixClient

logger = logging.getLogger(__name__)


# ─── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class AdaptiveConfig:
    """
    Drop-in superset of MarketMakerConfig with all adaptive-strategy knobs.
    Every field that existed in MarketMakerConfig keeps the same default so
    that existing callers can migrate without change.
    """

    # ── Base (same as MarketMakerConfig) ──────────────────────────────────────
    symbol: str = "BTCUSDT"
    spread_pct: float = 0.0004          # base spread (0.04% each side)
    order_qty: str = "0.001"            # base order size in contracts
    max_position_qty: float = 0.005
    stop_loss_pct: float = -0.5         # -0.5% of margin
    max_total_loss_usdt: float = 10.0
    refresh_interval: float = 3.0
    price_drift_threshold_pct: float = 0.002
    inventory_management: bool = True
    leverage: int = 5

    # ── Optimization 1: Volatility-Adaptive Spread ────────────────────────────
    base_spread_pct: float = 0.0005     # anchor for vol-adjustment
    vol_lookback_candles: int = 20      # 20 × 1-min candles = 20-min window
    vol_widen_threshold: float = 0.015  # 1h ret > 1.5%  → widen ×2
    vol_tighten_threshold: float = 0.003  # 1h ret < 0.3% → tighten ×0.7
    vol_widen_multiplier: float = 2.0
    vol_tighten_multiplier: float = 0.7

    # ── Optimization 2: Funding Rate Bias ─────────────────────────────────────
    funding_bias: bool = True
    funding_bias_threshold: float = 0.0001   # |rate| > 0.01 % triggers bias
    funding_bias_qty_pct: float = 0.20       # boost preferred side by 20 %

    # ── Optimization 3: Anti-Martingale Sizing ────────────────────────────────
    anti_martingale: bool = True
    cool_down_after_sl: int = 3         # SL streak → cut qty 50 %
    restore_after_fills: int = 5        # fill streak → restore base qty

    # ── Optimization 4: Spread Capture Tracking ───────────────────────────────
    spread_capture_tracking: bool = True
    min_capture_ratio: float = 0.5     # if capture < 50 % of expected → widen
    capture_history_len: int = 20       # look at last N fills for quality

    # ── Optimization 5: Time-of-Day Awareness ────────────────────────────────
    time_aware: bool = True
    low_vol_hours_utc: tuple = (3, 7)   # 03–07 UTC: low liquidity
    high_vol_hours_utc: tuple = (        # 09–11 & 14–16 UTC: high liquidity
        (9, 11), (14, 16)
    )
    low_vol_qty_factor: float = 0.70    # reduce qty by 30 % during quiet hours
    high_vol_spread_factor: float = 0.85  # 15 % tighter spread during busy hours


# ─── Adaptive Market Maker ─────────────────────────────────────────────────────

class AdaptiveMarketMaker:
    """
    Enhanced market maker that inherits all base behaviour and layers
    the five optimizations on top.
    """

    def __init__(self, client: BitunixClient, config: AdaptiveConfig):
        self.client = client
        self.cfg = config

        # ── Order tracking ───────────────────────────────────────────────────
        self.active_buy_id: Optional[str] = None
        self.active_sell_id: Optional[str] = None
        self.last_mid_price: float = 0.0

        # ── P&L / volume ─────────────────────────────────────────────────────
        self.realized_pnl: float = 0.0
        self.total_volume: float = 0.0
        self.running: bool = True

        # ── Opt-3: Anti-Martingale state ──────────────────────────────────────
        self._sl_streak: int = 0        # consecutive stop-losses
        self._fill_streak: int = 0      # consecutive fills without SL
        self._in_cool_down: bool = False

        # ── Opt-4: Spread Capture state ───────────────────────────────────────
        # Each entry: {"expected_spread_pct": float, "actual_spread_pct": float}
        self._fill_records: List[dict] = []
        # Tracks what price the LAST order cycle expected to earn
        self._last_expected_spread_pct: float = 0.0
        self._last_buy_price: float = 0.0
        self._last_sell_price: float = 0.0

        # ── Runtime spread (gets updated each step) ───────────────────────────
        self._current_spread_pct: float = config.base_spread_pct

    # ═══════════════════════════════════════════════════════════════════════════
    # Optimization 1 — Volatility-Adaptive Spread
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_volatility(self) -> float:
        """
        Fetch recent 1-min klines and compute rolling return volatility.
        Returns the volatility as a decimal fraction (e.g. 0.008 = 0.8%).
        Falls back to 0.0 on any error (caller keeps base spread).
        """
        try:
            resp = self.client.get_kline(
                self.cfg.symbol,
                granularity="1m",
                limit=self.cfg.vol_lookback_candles + 1,   # +1 for diff
            )
            candles = resp.get("data", [])
            if len(candles) < 2:
                logger.warning("Not enough kline data for volatility calc")
                return 0.0

            # Each candle: {open, high, low, close, volume, ts}
            # Compute log returns on close prices
            closes = [float(c["close"]) for c in candles]
            log_returns = [
                math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))
                if closes[i - 1] > 0
            ]
            if len(log_returns) < 2:
                return 0.0

            vol = statistics.stdev(log_returns)   # 1-period std dev
            logger.debug(f"[VOL] {self.cfg.vol_lookback_candles}-candle vol = {vol*100:.4f}%")
            return vol

        except Exception as e:
            logger.warning(f"[VOL] Volatility fetch failed: {e}")
            return 0.0

    def _adaptive_spread(self) -> float:
        """
        Return spread_pct adjusted for current volatility AND time-of-day.
        Priority: vol adjustment → time-of-day adjustment.
        """
        vol = self._get_volatility()
        spread = self.cfg.base_spread_pct

        # Volatility adjustment
        if vol >= self.cfg.vol_widen_threshold:
            spread *= self.cfg.vol_widen_multiplier
            logger.info(
                f"[SPREAD] High vol ({vol*100:.3f}%) → widen ×{self.cfg.vol_widen_multiplier}"
                f"  spread={spread*100:.4f}%"
            )
        elif vol > 0 and vol <= self.cfg.vol_tighten_threshold:
            spread *= self.cfg.vol_tighten_multiplier
            logger.info(
                f"[SPREAD] Low vol ({vol*100:.3f}%) → tighten ×{self.cfg.vol_tighten_multiplier}"
                f"  spread={spread*100:.4f}%"
            )
        else:
            logger.debug(f"[SPREAD] Normal vol ({vol*100:.3f}%), base spread kept")

        # Time-of-day adjustment (Opt-5 — applied to spread too)
        if self.cfg.time_aware:
            hour = datetime.now(timezone.utc).hour
            # High-volume hours → tighter spread
            for h_start, h_end in self.cfg.high_vol_hours_utc:
                if h_start <= hour < h_end:
                    spread *= self.cfg.high_vol_spread_factor
                    logger.debug(
                        f"[TIME] Peak hour {hour}UTC → tighten spread ×{self.cfg.high_vol_spread_factor}"
                    )
                    break

        # Spread-capture quality feedback (Opt-4)
        if self.cfg.spread_capture_tracking and len(self._fill_records) >= 5:
            avg_capture = self._avg_capture_ratio()
            if avg_capture < self.cfg.min_capture_ratio:
                pre = spread
                spread *= (1 + (1 - avg_capture))   # proportionally widen
                logger.info(
                    f"[CAPTURE] Poor fill quality (ratio={avg_capture:.2f}) "
                    f"→ widening spread {pre*100:.4f}% → {spread*100:.4f}%"
                )

        return spread

    # ═══════════════════════════════════════════════════════════════════════════
    # Optimization 2 — Funding Rate Bias
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_funding_rate(self) -> float:
        """
        Fetch the current perpetual funding rate.
        Returns the rate as a decimal (e.g. 0.0001 = 0.01%).
        Falls back to 0.0 on error.
        """
        try:
            resp = self.client.get_funding_rate(self.cfg.symbol)
            rate_str = (
                resp.get("data", {}).get("fundingRate")
                or resp.get("data", {}).get("lastFundingRate")
                or "0"
            )
            rate = float(rate_str)
            logger.debug(f"[FUNDING] rate = {rate*100:.5f}%")
            return rate
        except Exception as e:
            logger.warning(f"[FUNDING] Fetch failed: {e}")
            return 0.0

    def _funding_qty_factors(self) -> tuple[float, float]:
        """
        Returns (buy_factor, sell_factor) multipliers for order qty.
        Positive funding (longs pay shorts) → prefer SELL (+20% sell qty).
        Negative funding (shorts pay longs) → prefer BUY  (+20% buy qty).
        """
        if not self.cfg.funding_bias:
            return 1.0, 1.0

        rate = self._get_funding_rate()
        bias = self.cfg.funding_bias_qty_pct
        threshold = self.cfg.funding_bias_threshold

        if rate > threshold:
            # Longs pay shorts → be heavier on SELL side
            logger.info(f"[FUNDING] Positive rate ({rate*100:.5f}%) → boosting SELL qty by {bias*100:.0f}%")
            return 1.0, 1.0 + bias
        elif rate < -threshold:
            # Shorts pay longs → be heavier on BUY side
            logger.info(f"[FUNDING] Negative rate ({rate*100:.5f}%) → boosting BUY qty by {bias*100:.0f}%")
            return 1.0 + bias, 1.0
        else:
            return 1.0, 1.0

    # ═══════════════════════════════════════════════════════════════════════════
    # Optimization 3 — Smart Anti-Martingale Sizing
    # ═══════════════════════════════════════════════════════════════════════════

    def _smart_qty(self, base_qty: float, buy_factor: float, sell_factor: float
                   ) -> tuple[float, float]:
        """
        Apply anti-martingale cool-down to buy and sell quantities.
        Returns (buy_qty, sell_qty) as floats.
        """
        if not self.cfg.anti_martingale:
            return base_qty * buy_factor, base_qty * sell_factor

        if self._in_cool_down:
            effective = base_qty * 0.50
            logger.info(
                f"[ANTI-MARTIN] Cool-down active (SL streak={self._sl_streak}) "
                f"→ qty reduced 50%  base={base_qty:.5f}  effective={effective:.5f}"
            )
        else:
            effective = base_qty

        return effective * buy_factor, effective * sell_factor

    def _record_sl(self):
        """Call whenever a stop-loss fires."""
        self._sl_streak += 1
        self._fill_streak = 0
        if self._sl_streak >= self.cfg.cool_down_after_sl:
            if not self._in_cool_down:
                logger.warning(
                    f"[ANTI-MARTIN] {self._sl_streak} consecutive SLs → entering COOL-DOWN mode"
                )
            self._in_cool_down = True

    def _record_fill(self):
        """Call whenever an order is filled without triggering a stop-loss."""
        self._fill_streak += 1
        self._sl_streak = 0
        if self._in_cool_down and self._fill_streak >= self.cfg.restore_after_fills:
            logger.info(
                f"[ANTI-MARTIN] {self._fill_streak} clean fills → RESTORING base qty"
            )
            self._in_cool_down = False
            self._fill_streak = 0

    # ═══════════════════════════════════════════════════════════════════════════
    # Optimization 4 — Spread Capture Tracking
    # ═══════════════════════════════════════════════════════════════════════════

    def _record_expected_spread(self, buy_price: float, sell_price: float):
        """Store what we expected to earn this cycle."""
        mid = (buy_price + sell_price) / 2.0
        self._last_expected_spread_pct = (sell_price - buy_price) / mid if mid > 0 else 0.0
        self._last_buy_price = buy_price
        self._last_sell_price = sell_price

    def _record_actual_fill(self, filled_buy: float, filled_sell: float):
        """
        Call when both sides of a cycle actually fill.
        Measures what fraction of the expected spread was captured.
        """
        if self._last_expected_spread_pct <= 0:
            return
        actual_spread = filled_sell - filled_buy
        mid = (filled_buy + filled_sell) / 2.0
        actual_pct = actual_spread / mid if mid > 0 else 0.0
        ratio = actual_pct / self._last_expected_spread_pct

        self._fill_records.append({
            "expected_spread_pct": self._last_expected_spread_pct,
            "actual_spread_pct": actual_pct,
            "capture_ratio": ratio,
        })
        # Keep only recent history
        if len(self._fill_records) > self.cfg.capture_history_len:
            self._fill_records.pop(0)

        logger.info(
            f"[CAPTURE] Expected={self._last_expected_spread_pct*100:.4f}%  "
            f"Actual={actual_pct*100:.4f}%  ratio={ratio:.2f}"
        )

    def _avg_capture_ratio(self) -> float:
        """Average fill-quality ratio over recent history."""
        if not self._fill_records:
            return 1.0
        return statistics.mean(r["capture_ratio"] for r in self._fill_records)

    def _log_fill_quality(self):
        """Emit a periodic fill-quality summary."""
        if not self._fill_records:
            return
        avg = self._avg_capture_ratio()
        logger.info(
            f"[CAPTURE] Fill quality over last {len(self._fill_records)} cycles: "
            f"avg_capture_ratio={avg:.3f}  "
            f"({'✅ GOOD' if avg >= self.cfg.min_capture_ratio else '⚠️ POOR'})"
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Optimization 5 — Time-of-Day Awareness
    # ═══════════════════════════════════════════════════════════════════════════

    def _time_qty_factor(self) -> float:
        """
        Returns a multiplier for order quantity based on the current UTC hour.
        Low-volume hours → 0.70  (reduce qty 30%)
        Normal / high-volume hours → 1.00
        """
        if not self.cfg.time_aware:
            return 1.0

        hour = datetime.now(timezone.utc).hour
        lo_start, lo_end = self.cfg.low_vol_hours_utc

        if lo_start <= hour < lo_end:
            logger.info(
                f"[TIME] Low-volume window ({lo_start}–{lo_end} UTC, current={hour}UTC) "
                f"→ qty ×{self.cfg.low_vol_qty_factor}"
            )
            return self.cfg.low_vol_qty_factor

        return 1.0

    # ═══════════════════════════════════════════════════════════════════════════
    # Core helpers  (mirrors base MarketMaker)
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_mid_price(self) -> float:
        """Fetch best bid/ask from order book and return mid-price."""
        try:
            resp = self.client.get_depth(self.cfg.symbol, limit=5)
            data = resp.get("data", {})
            asks = data.get("asks", [])
            bids = data.get("bids", [])
            if not asks or not bids:
                raise ValueError("Empty order book")
            best_ask = float(asks[0][0])
            best_bid = float(bids[0][0])
            return (best_ask + best_bid) / 2.0
        except Exception as e:
            logger.error(f"Failed to get mid price: {e}")
            return 0.0

    def _get_position(self) -> dict:
        """Return current open position for the symbol (or empty dict)."""
        try:
            resp = self.client.get_positions(self.cfg.symbol)
            positions = resp.get("data", [])
            for pos in positions:
                if pos.get("symbol") == self.cfg.symbol:
                    return pos
        except Exception as e:
            logger.error(f"Failed to get position: {e}")
        return {}

    def _cancel_active_orders(self):
        """Cancel both active maker orders."""
        try:
            self.client.cancel_all_orders(self.cfg.symbol)
            logger.info("Cancelled all orders")
        except Exception as e:
            logger.warning(f"Cancel all orders failed: {e}")
        self.active_buy_id = None
        self.active_sell_id = None

    def _round_price(self, price: float, tick_size: float = 0.1) -> str:
        """Round price to nearest tick and return as formatted string."""
        rounded = round(round(price / tick_size) * tick_size, 8)
        return f"{rounded:.2f}"

    def _compute_order_prices(self, mid: float, spread_pct: float) -> tuple[float, float]:
        """Return (buy_price, sell_price) using the given spread_pct."""
        half_spread = mid * (spread_pct / 2)
        return mid - half_spread, mid + half_spread

    # ─── Order placement ──────────────────────────────────────────────────────

    def _place_maker_orders(self, mid: float):
        """
        Place new limit buy + sell with all five optimizations applied:
          spread  → adaptive (vol + time-of-day + capture quality)
          qty     → anti-martingale × time-of-day × funding bias
        """
        # ── Compute adaptive spread ──────────────────────────────────────────
        spread = self._adaptive_spread()
        self._current_spread_pct = spread

        buy_price, sell_price = self._compute_order_prices(mid, spread)
        buy_str  = self._round_price(buy_price)
        sell_str = self._round_price(sell_price)

        # Track expected spread for Opt-4
        self._record_expected_spread(float(buy_str), float(sell_str))

        # ── Compute adaptive quantities ──────────────────────────────────────
        base_qty = float(self.cfg.order_qty)

        # Funding bias factors (Opt-2)
        buy_fund_factor, sell_fund_factor = self._funding_qty_factors()

        # Time-of-day factor (Opt-5)
        time_factor = self._time_qty_factor()

        # Anti-martingale (Opt-3) — receives already-combined fund factors
        buy_qty, sell_qty = self._smart_qty(
            base_qty * time_factor,
            buy_fund_factor,
            sell_fund_factor,
        )

        # ── Inventory management (from base strategy) ─────────────────────────
        position   = self._get_position()
        pos_qty    = float(position.get("qty", 0))
        pos_side   = position.get("side", "")

        place_buy  = True
        place_sell = True

        if self.cfg.inventory_management:
            if pos_side == "long" and pos_qty >= self.cfg.max_position_qty:
                place_buy = False
                logger.info(f"[INVENTORY] Long limit ({pos_qty}), skipping BUY")
            elif pos_side == "short" and pos_qty >= self.cfg.max_position_qty:
                place_sell = False
                logger.info(f"[INVENTORY] Short limit ({pos_qty}), skipping SELL")

        # ── Place orders ──────────────────────────────────────────────────────
        logger.info(
            f"[PLACE] spread={spread*100:.4f}%  "
            f"buy={buy_str}(qty={buy_qty:.5f})  sell={sell_str}(qty={sell_qty:.5f})"
        )

        try:
            if place_buy:
                buy_cid = f"am_buy_{uuid.uuid4().hex[:8]}"
                resp = self.client.place_order(
                    symbol=self.cfg.symbol,
                    side="BUY",
                    order_type="LIMIT",
                    qty=f"{buy_qty:.5f}",
                    price=buy_str,
                    client_id=buy_cid,
                )
                self.active_buy_id = resp.get("data", {}).get("orderId")
                logger.info(f"  BUY  LIMIT @ {buy_str}  qty={buy_qty:.5f}  id={self.active_buy_id}")

            if place_sell:
                sell_cid = f"am_sell_{uuid.uuid4().hex[:8]}"
                resp = self.client.place_order(
                    symbol=self.cfg.symbol,
                    side="SELL",
                    order_type="LIMIT",
                    qty=f"{sell_qty:.5f}",
                    price=sell_str,
                    client_id=sell_cid,
                )
                self.active_sell_id = resp.get("data", {}).get("orderId")
                logger.info(f"  SELL LIMIT @ {sell_str}  qty={sell_qty:.5f}  id={self.active_sell_id}")

        except Exception as e:
            logger.error(f"Failed to place maker orders: {e}")

    # ─── Risk management ──────────────────────────────────────────────────────

    def _check_stop_loss(self, position: dict) -> bool:
        """
        Returns True if stop-loss triggered and position was closed.
        Also records the SL event for anti-martingale tracking (Opt-3).
        """
        if not position:
            return False
        unrealized_pnl = float(position.get("unrealizedPnl", 0))
        margin = float(position.get("margin", 1))
        if margin == 0:
            return False
        pnl_pct = (unrealized_pnl / margin) * 100
        if pnl_pct <= self.cfg.stop_loss_pct:
            logger.warning(
                f"⚠️  STOP LOSS triggered! PnL%={pnl_pct:.2f}% | unrealPnL={unrealized_pnl:.4f}"
            )
            pos_id = position.get("positionId", "")
            if pos_id:
                try:
                    self.client.flash_close_position(self.cfg.symbol, pos_id)
                    logger.warning("✅ Position flash-closed via stop-loss.")
                    self.realized_pnl += unrealized_pnl
                except Exception as e:
                    logger.error(f"Flash close failed: {e}")
            # Opt-3: register SL event
            self._record_sl()
            return True
        return False

    def _check_circuit_breaker(self) -> bool:
        """Stop bot entirely if cumulative loss exceeds threshold."""
        if self.realized_pnl <= -self.cfg.max_total_loss_usdt:
            logger.critical(
                f"🚨 CIRCUIT BREAKER: Total loss {self.realized_pnl:.4f} USDT "
                f"exceeded limit {-self.cfg.max_total_loss_usdt}"
            )
            return True
        return False

    def _should_refresh(self, mid: float) -> bool:
        """True if price drifted beyond threshold since last order placement."""
        if self.last_mid_price == 0:
            return True
        drift = abs(mid - self.last_mid_price) / self.last_mid_price
        return drift >= self.cfg.price_drift_threshold_pct

    # ═══════════════════════════════════════════════════════════════════════════
    # Main loop  (compatible interface with base MarketMaker)
    # ═══════════════════════════════════════════════════════════════════════════

    def setup(self):
        """Initialize: set leverage and log adaptive parameters."""
        logger.info(f"🔧 Setting leverage={self.cfg.leverage}x for {self.cfg.symbol}")
        try:
            self.client.set_leverage(self.cfg.symbol, self.cfg.leverage)
        except Exception as e:
            logger.warning(f"Could not set leverage: {e}")

        logger.info(
            f"⚙️  Adaptive config | "
            f"base_spread={self.cfg.base_spread_pct*100:.4f}%  "
            f"vol_candles={self.cfg.vol_lookback_candles}  "
            f"funding_bias={self.cfg.funding_bias}  "
            f"anti_martingale={self.cfg.anti_martingale}  "
            f"time_aware={self.cfg.time_aware}"
        )

    def step(self):
        """One iteration of the adaptive market-making loop."""
        # 1. Get current mid price
        mid = self._get_mid_price()
        if mid == 0:
            logger.warning("Could not get price, skipping step")
            return

        logger.info(
            f"📊 {self.cfg.symbol}  mid={mid:.4f}  "
            f"spread={self._current_spread_pct*100:.4f}%  "
            f"volume={self.total_volume:.4f}  pnl={self.realized_pnl:.4f}  "
            f"sl_streak={self._sl_streak}  cool_down={self._in_cool_down}"
        )

        # 2. Circuit breaker
        if self._check_circuit_breaker():
            self.running = False
            return

        # 3. Check open position for stop-loss
        position = self._get_position()
        if self._check_stop_loss(position):
            self._cancel_active_orders()
            return

        # 4. Periodic fill-quality report (every 10 steps approx)
        if len(self._fill_records) > 0 and len(self._fill_records) % 10 == 0:
            self._log_fill_quality()

        # 5. Refresh orders if price drifted
        if self._should_refresh(mid):
            logger.info(f"🔄 Refreshing orders (mid {self.last_mid_price:.4f} → {mid:.4f})")
            self._cancel_active_orders()
            self._place_maker_orders(mid)
            self.last_mid_price = mid
        else:
            logger.debug("Price stable, keeping existing orders")

    def run(self):
        """Main loop — same interface as MarketMaker.run()."""
        self.setup()
        logger.info(f"🚀 Adaptive Market Maker started on {self.cfg.symbol}")
        logger.info(
            f"   Base spread: ±{self.cfg.base_spread_pct*100:.3f}%  |  "
            f"Qty: {self.cfg.order_qty}  |  Leverage: {self.cfg.leverage}x"
        )
        logger.info(
            f"   Max position: {self.cfg.max_position_qty}  |  "
            f"Stop-loss: {self.cfg.stop_loss_pct}%  |  "
            f"Max loss: ${self.cfg.max_total_loss_usdt}"
        )

        while self.running:
            try:
                self.step()
            except KeyboardInterrupt:
                logger.info("⛔ Interrupted by user")
                break
            except Exception as e:
                logger.error(f"Unhandled error in step: {e}", exc_info=True)
            time.sleep(self.cfg.refresh_interval)

        # Cleanup
        logger.info("🧹 Cleaning up: cancelling all open orders...")
        self._cancel_active_orders()
        logger.info(
            f"📈 Session summary: "
            f"Volume={self.total_volume:.4f}  |  "
            f"Realized PnL={self.realized_pnl:.4f} USDT  |  "
            f"Avg capture ratio={self._avg_capture_ratio():.3f}  |  "
            f"Total SL hits={self._sl_streak}"
        )
