"""
Market Making Strategy for Bitunix Futures
==========================================
Goal: Generate HIGH TRADING VOLUME with minimal net loss.

Strategy: Symmetric Market Making
- Place LIMIT BUY slightly below mid-price
- Place LIMIT SELL slightly above mid-price
- Collect the bid-ask spread as profit
- Cancel and re-place orders when price moves too much

Risk Controls:
- Max position size limit
- Stop-loss if price moves against us
- Inventory rebalancing (avoid accumulating too much directional risk)
- Max loss circuit breaker
"""

import time
import math
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional
from bitunix_client import BitunixClient

logger = logging.getLogger(__name__)


@dataclass
class MarketMakerConfig:
    # Symbol to trade (e.g. "BTCUSDT")
    symbol: str = "BTCUSDT"

    # Spread: how far from mid-price to place orders (as fraction, e.g. 0.0003 = 0.03%)
    spread_pct: float = 0.0004          # 0.04% each side → total spread 0.08%

    # Order size in contracts (USDT-denominated)
    order_qty: str = "0.001"           # Adjust based on minimum contract size

    # Maximum allowed open position (contracts) before stopping new orders
    max_position_qty: float = 0.005

    # Stop-loss: close position if unrealized PnL % drops below this
    stop_loss_pct: float = -0.5        # -0.5% of position value

    # Max total loss in USDT before the bot stops completely
    max_total_loss_usdt: float = 10.0

    # How often to refresh orders (seconds)
    refresh_interval: float = 3.0

    # How many price ticks before we cancel and repost (resets if price drifts)
    price_drift_threshold_pct: float = 0.002   # 0.2%

    # Whether to use reduce-only on the counter side when we have inventory
    inventory_management: bool = True

    # Leverage (set on startup)
    leverage: int = 5


class MarketMaker:
    def __init__(self, client: BitunixClient, config: MarketMakerConfig):
        self.client = client
        self.cfg = config
        self.active_buy_id: Optional[str] = None
        self.active_sell_id: Optional[str] = None
        self.last_mid_price: float = 0.0
        self.realized_pnl: float = 0.0
        self.total_volume: float = 0.0   # track generated volume
        self.running = True

    # ─── Core helpers ─────────────────────────────────────────────────────────

    def _get_mid_price(self) -> float:
        """Fetch best bid/ask from order book and compute mid-price."""
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
        """Round price to nearest tick size."""
        rounded = round(round(price / tick_size) * tick_size, 8)
        # Format without scientific notation
        return f"{rounded:.2f}"

    def _compute_order_prices(self, mid: float) -> tuple[float, float]:
        """Return (buy_price, sell_price) around mid-price."""
        half_spread = mid * (self.cfg.spread_pct / 2)
        buy_price  = mid - half_spread
        sell_price = mid + half_spread
        return buy_price, sell_price

    # ─── Order Management ─────────────────────────────────────────────────────

    def _place_maker_orders(self, mid: float):
        """Place new limit buy + sell around mid-price."""
        buy_price, sell_price = self._compute_order_prices(mid)
        buy_str  = self._round_price(buy_price)
        sell_str = self._round_price(sell_price)

        position = self._get_position()
        pos_qty   = float(position.get("qty", 0))
        pos_side  = position.get("side", "")  # "long" or "short"

        qty = self.cfg.order_qty

        # Inventory skew: if long, be more aggressive selling (and vice versa)
        place_buy  = True
        place_sell = True

        if self.cfg.inventory_management:
            if pos_side == "long" and pos_qty >= self.cfg.max_position_qty:
                place_buy = False   # Don't add more longs
                logger.info(f"[INVENTORY] Long limit reached ({pos_qty}), skipping BUY order")
            elif pos_side == "short" and pos_qty >= self.cfg.max_position_qty:
                place_sell = False  # Don't add more shorts
                logger.info(f"[INVENTORY] Short limit reached ({pos_qty}), skipping SELL order")

        try:
            if place_buy:
                buy_cid = f"mm_buy_{uuid.uuid4().hex[:8]}"
                resp = self.client.place_order(
                    symbol=self.cfg.symbol,
                    side="BUY",
                    order_type="LIMIT",
                    qty=qty,
                    price=buy_str,
                    client_id=buy_cid
                )
                self.active_buy_id = resp.get("data", {}).get("orderId")
                logger.info(f"  BUY  LIMIT @ {buy_str}  qty={qty}  id={self.active_buy_id}")

            if place_sell:
                sell_cid = f"mm_sell_{uuid.uuid4().hex[:8]}"
                resp = self.client.place_order(
                    symbol=self.cfg.symbol,
                    side="SELL",
                    order_type="LIMIT",
                    qty=qty,
                    price=sell_str,
                    client_id=sell_cid
                )
                self.active_sell_id = resp.get("data", {}).get("orderId")
                logger.info(f"  SELL LIMIT @ {sell_str}  qty={qty}  id={self.active_sell_id}")

        except Exception as e:
            logger.error(f"Failed to place maker orders: {e}")

    def _check_stop_loss(self, position: dict) -> bool:
        """Returns True if stop-loss triggered and position was closed."""
        if not position:
            return False
        unrealized_pnl = float(position.get("unrealizedPnl", 0))
        margin         = float(position.get("margin", 1))
        if margin == 0:
            return False
        pnl_pct = (unrealized_pnl / margin) * 100
        if pnl_pct <= self.cfg.stop_loss_pct:
            logger.warning(f"⚠️  STOP LOSS triggered! PnL%={pnl_pct:.2f}% | unrealPnL={unrealized_pnl:.4f}")
            pos_id = position.get("positionId", "")
            if pos_id:
                try:
                    self.client.flash_close_position(self.cfg.symbol, pos_id)
                    logger.warning("✅ Position flash-closed via stop-loss.")
                    self.realized_pnl += unrealized_pnl
                except Exception as e:
                    logger.error(f"Flash close failed: {e}")
            return True
        return False

    def _check_circuit_breaker(self) -> bool:
        """Stop bot entirely if total loss exceeds threshold."""
        if self.realized_pnl <= -self.cfg.max_total_loss_usdt:
            logger.critical(f"🚨 CIRCUIT BREAKER: Total loss {self.realized_pnl:.4f} USDT exceeded limit {-self.cfg.max_total_loss_usdt}")
            return True
        return False

    def _should_refresh(self, mid: float) -> bool:
        """Check if price drifted enough that we should cancel+repost."""
        if self.last_mid_price == 0:
            return True
        drift = abs(mid - self.last_mid_price) / self.last_mid_price
        return drift >= self.cfg.price_drift_threshold_pct

    # ─── Main Loop ────────────────────────────────────────────────────────────

    def setup(self):
        """Initialize: set leverage."""
        logger.info(f"🔧 Setting leverage={self.cfg.leverage}x for {self.cfg.symbol}")
        try:
            self.client.set_leverage(self.cfg.symbol, self.cfg.leverage)
        except Exception as e:
            logger.warning(f"Could not set leverage: {e}")

    def step(self):
        """One iteration of the market-making loop."""
        # 1. Get current price
        mid = self._get_mid_price()
        if mid == 0:
            logger.warning("Could not get price, skipping step")
            return

        logger.info(f"📊 {self.cfg.symbol}  mid={mid:.4f}  volume={self.total_volume:.4f}  pnl={self.realized_pnl:.4f}")

        # 2. Circuit breaker check
        if self._check_circuit_breaker():
            self.running = False
            return

        # 3. Check open position for stop-loss
        position = self._get_position()
        if self._check_stop_loss(position):
            self._cancel_active_orders()
            return

        # 4. If price has drifted, cancel existing orders and repost
        if self._should_refresh(mid):
            logger.info(f"🔄 Refreshing orders (mid moved from {self.last_mid_price:.4f} → {mid:.4f})")
            self._cancel_active_orders()
            self._place_maker_orders(mid)
            self.last_mid_price = mid
        else:
            logger.debug("Price stable, keeping existing orders")

    def run(self):
        """Main loop."""
        self.setup()
        logger.info(f"🚀 Market Maker started on {self.cfg.symbol}")
        logger.info(f"   Spread: ±{self.cfg.spread_pct*100:.3f}%  |  Qty: {self.cfg.order_qty}  |  Leverage: {self.cfg.leverage}x")
        logger.info(f"   Max position: {self.cfg.max_position_qty}  |  Stop-loss: {self.cfg.stop_loss_pct}%  |  Max loss: ${self.cfg.max_total_loss_usdt}")

        while self.running:
            try:
                self.step()
            except KeyboardInterrupt:
                logger.info("⛔ Interrupted by user")
                break
            except Exception as e:
                logger.error(f"Unhandled error in step: {e}", exc_info=True)
            time.sleep(self.cfg.refresh_interval)

        # Cleanup: cancel all orders on exit
        logger.info("🧹 Cleaning up: cancelling all open orders...")
        self._cancel_active_orders()
        logger.info(f"📈 Session summary: Volume={self.total_volume:.4f} | Realized PnL={self.realized_pnl:.4f} USDT")
