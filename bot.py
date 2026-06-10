# -*- coding: utf-8 -*-
"""
Bitunix Futures Trading Bot - Main Entry Point
==============================================
Run:  python bot.py
      python bot.py --symbol ETHUSDT --qty 0.01 --spread 0.0005

Environment variables (from .env):
  API_KEY    — Bitunix API key
  SECRET_KEY — Bitunix secret key
"""

import os
import sys
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import time
import argparse
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from colorama import init, Fore, Style

from bitunix_client import BitunixClient
from strategy_market_maker import MarketMaker, MarketMakerConfig

# ─── Coloured logging ─────────────────────────────────────────────────────────

init(autoreset=True)


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG:    Fore.CYAN,
        logging.INFO:     Fore.GREEN,
        logging.WARNING:  Fore.YELLOW,
        logging.ERROR:    Fore.RED,
        logging.CRITICAL: Fore.MAGENTA + Style.BRIGHT,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        fmt = f"{Fore.WHITE}%(asctime)s{Style.RESET_ALL} {color}%(levelname)-8s{Style.RESET_ALL} %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
        return formatter.format(record)


def setup_logging(log_level: str = "INFO"):
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(ColorFormatter())
    console.setLevel(level)

    # File handler (rotating by date)
    Path("logs").mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(f"logs/bot_{date_str}.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    file_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)


# ─── Banner ───────────────────────────────────────────────────────────────────

BANNER = f"""
{Fore.CYAN}+------------------------------------------------------+
|   {Fore.YELLOW}[BITUNIX FUTURES BOT] MARKET MAKER v1.0{Fore.CYAN}          |
|   {Fore.WHITE}High Volume | Low Risk | Auto Risk Control{Fore.CYAN}        |
+------------------------------------------------------+{Style.RESET_ALL}
"""

# ─── Argument parser ──────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Bitunix Futures Market Making Bot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--symbol",        default="BTCUSDT",   help="Trading pair symbol")
    parser.add_argument("--qty",           default="0.001",     help="Order quantity per side")
    parser.add_argument("--spread",        default=0.0004,      type=float, help="Spread fraction (e.g. 0.0004 = 0.04%%)")
    parser.add_argument("--leverage",      default=5,           type=int,   help="Leverage multiplier")
    parser.add_argument("--max-pos",       default=0.005,       type=float, help="Max position qty before pausing orders")
    parser.add_argument("--stop-loss",     default=-0.5,        type=float, help="Stop-loss %% (e.g. -0.5 = -0.5%%)")
    parser.add_argument("--max-loss",      default=10.0,        type=float, help="Max total loss in USDT (circuit breaker)")
    parser.add_argument("--refresh",       default=3.0,         type=float, help="Order refresh interval in seconds")
    parser.add_argument("--drift",         default=0.002,       type=float, help="Price drift %% to trigger order refresh")
    parser.add_argument("--log-level",     default="INFO",      help="Log level: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--dry-run",       action="store_true", help="Dry run: fetch market data but do NOT place orders")
    return parser.parse_args()


# ─── Sanity checks ────────────────────────────────────────────────────────────

def check_connection(client: BitunixClient, symbol: str) -> bool:
    """Test API connectivity and print account info."""
    log = logging.getLogger("startup")
    try:
        # Public endpoint — no auth needed
        ticker = client.get_tickers(symbol)
        data = ticker.get("data", [])
        if data:
            t = data[0] if isinstance(data, list) else data
            log.info(f"✅ Market data OK | {symbol} last={t.get('lastPrice', '?')} vol={t.get('vol24h', '?')}")
        else:
            log.warning(f"Ticker response empty: {ticker}")
    except Exception as e:
        log.error(f"❌ Market data failed: {e}")
        return False

    try:
        acct = client.get_account()
        acct_data = acct.get("data", {})
        balance = acct_data.get("available", acct_data.get("availableBalance", "?"))
        log.info(f"✅ Account OK      | Available balance: {balance} USDT")
    except Exception as e:
        log.error(f"❌ Account check failed: {e}")
        return False

    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    load_dotenv()
    args = parse_args()
    setup_logging(args.log_level)

    print(BANNER)
    log = logging.getLogger("main")

    # Load credentials
    api_key = os.getenv("API_KEY", "")
    secret_key = os.getenv("SECRET_KEY", "")

    if not api_key or not secret_key:
        log.critical("❌ API_KEY or SECRET_KEY not found in .env file!")
        sys.exit(1)

    log.info(f"🔑 API Key loaded: {api_key[:8]}...{api_key[-4:]}")

    # Create client
    client = BitunixClient(api_key, secret_key)

    # Connectivity check
    log.info("🔌 Testing API connectivity...")
    if not check_connection(client, args.symbol):
        log.critical("Could not connect to Bitunix API. Check credentials and network.")
        sys.exit(1)

    if args.dry_run:
        log.warning("⚠️  DRY RUN mode: No orders will be placed!")

    # Build config
    cfg = MarketMakerConfig(
        symbol=args.symbol,
        spread_pct=args.spread,
        order_qty=args.qty,
        max_position_qty=args.max_pos,
        stop_loss_pct=args.stop_loss,
        max_total_loss_usdt=args.max_loss,
        refresh_interval=args.refresh,
        price_drift_threshold_pct=args.drift,
        leverage=args.leverage,
    )

    if args.dry_run:
        # Dry run: just print price data every 5 seconds
        log.info(f"📡 Monitoring {args.symbol} (dry run)...")
        while True:
            try:
                mid_data = client.get_depth(args.symbol, limit=5)
                asks = mid_data.get("data", {}).get("asks", [])
                bids = mid_data.get("data", {}).get("bids", [])
                if asks and bids:
                    best_ask = float(asks[0][0])
                    best_bid = float(bids[0][0])
                    mid = (best_ask + best_bid) / 2
                    spread_pct = ((best_ask - best_bid) / mid) * 100
                    buy_p, sell_p = mid * (1 - cfg.spread_pct/2), mid * (1 + cfg.spread_pct/2)
                    log.info(f"[DRY] mid={mid:.4f} | book_spread={spread_pct:.4f}% | would_buy={buy_p:.4f} | would_sell={sell_p:.4f}")
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"Dry run error: {e}")
            time.sleep(5)
        return

    # Run the market maker
    mm = MarketMaker(client, cfg)
    try:
        mm.run()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        log.info("Bot shutdown complete.")


if __name__ == "__main__":
    main()
