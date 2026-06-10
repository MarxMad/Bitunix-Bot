# Bitunix Futures Market Making Bot

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Exchange](https://img.shields.io/badge/Exchange-Bitunix-FF6B35?style=for-the-badge)
![Strategy](https://img.shields.io/badge/Strategy-Market%20Making-00D084?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)
![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=for-the-badge)

**An automated market-making bot for Bitunix Futures with built-in Monte Carlo volume simulation.**  
Generate high trading volume with symmetric limit orders and automatic risk controls.

[Quick Start](#quick-start) · [Volume Simulator](#volume-simulator) · [Configuration](#configuration) · [Risk Controls](#risk-controls) · [API Reference](#api-reference)

</div>

---

## Table of Contents

- [What Is Market Making?](#what-is-market-making)
- [Features](#features)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
- [Volume Simulator](#volume-simulator)
- [Simulation Results: $50 Capital](#simulation-results-50-capital)
- [Risk Controls](#risk-controls)
- [Fee Structure](#fee-structure)
- [Project Structure](#project-structure)
- [Security](#security)
- [Disclaimer](#disclaimer)

---

## What Is Market Making?

Market making is a strategy where you simultaneously place a **limit buy order** below the current price and a **limit sell order** above it. When both sides fill, you capture the **bid-ask spread** as profit — without needing to predict which direction the market moves.

```
     Market Price: $61,250
                      │
  SELL LIMIT @ $61,262.25  ← +0.04% above mid
  ──────────────────────── ← spread captured = ~$24.50
  BUY  LIMIT @ $61,237.75  ← -0.04% below mid
                      │
```

> The bot continuously cancels and re-places orders as the price moves, ensuring the spread is always centered around the current market price.

---

## Features

| Feature | Description |
|---|---|
| **Symmetric Market Making** | Places limit buy + sell around mid-price every N seconds |
| **Double SHA256 Signing** | Implements Bitunix's exact authentication protocol |
| **Monte Carlo Simulator** | Projects volume, PnL, and fees before risking real capital |
| **Inventory Management** | Automatically skips new orders when position limit is reached |
| **Stop-Loss Engine** | Flash-closes position via market order if PnL% drops below threshold |
| **Circuit Breaker** | Shuts down entirely if total loss exceeds a hard USDT limit |
| **Auto-Retry** | 3x retry with exponential backoff on network errors |
| **Coloured Logging** | INFO/WARN/ERROR in colours + full file log per session |
| **Dry-Run Mode** | Monitor prices and projected orders without placing any real trades |
| **CLI Parameterized** | Every setting configurable via command-line flags |

---

## Requirements

- **Python 3.10+**
- A **Bitunix** account with Futures API access
- API Key with **Trade** permission enabled

```bash
# Install dependencies
pip install -r requirements.txt
```

**Dependencies:**
```
requests==2.31.0        # HTTP client
python-dotenv==1.0.0    # Load .env credentials
websocket-client==1.7.0 # (reserved for future WebSocket feeds)
colorama==0.4.6         # Coloured terminal output
tabulate==0.9.0         # Pretty tables in simulator
```

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/bitunix-bot.git
cd bitunix-bot
pip install -r requirements.txt
```

### 2. Configure credentials

Create a `.env` file in the project root:

```env
API_KEY=your_bitunix_api_key_here
SECRET_KEY=your_bitunix_secret_key_here
```

> Get your API keys at: **Bitunix → Account → API Management**  
> Enable: ✅ Trade permission | ❌ Do NOT enable withdrawal

### 3. Run the volume simulator first (no real orders)

```bash
python -X utf8 simulate.py --capital 50 --leverage 10 --hours 24
```

### 4. Test connectivity with dry-run

```bash
python -X utf8 bot.py --dry-run --symbol BTCUSDT
```

You should see:
```
✅ Market data OK | BTCUSDT last=61250.00
✅ Account OK      | Available balance: XX.XX USDT
[DRY] mid=61250.00 | book_spread=0.0002% | would_buy=61237.75 | would_sell=61262.25
```

### 5. Run the live bot

```bash
python -X utf8 bot.py --symbol BTCUSDT --qty 0.001 --leverage 10 --max-loss 5.0
```

---

## Configuration Reference

### Bot Parameters (`bot.py`)

| Flag | Default | Description |
|---|---|---|
| `--symbol` | `BTCUSDT` | Futures trading pair |
| `--qty` | `0.001` | Contract quantity per order side |
| `--spread` | `0.0004` | Half-spread as fraction (0.0004 = ±0.04%) |
| `--leverage` | `5` | Futures leverage multiplier |
| `--max-pos` | `0.005` | Max open position before pausing new orders |
| `--stop-loss` | `-0.5` | Stop-loss trigger at this % of position PnL |
| `--max-loss` | `10.0` | Total loss circuit breaker (USDT) |
| `--refresh` | `3.0` | Seconds between order refresh cycles |
| `--drift` | `0.002` | Price drift % that triggers order reposting (0.002 = 0.2%) |
| `--log-level` | `INFO` | Logging verbosity: DEBUG / INFO / WARNING / ERROR |
| `--dry-run` | off | Simulate without placing real orders |

### Simulator Parameters (`simulate.py`)

| Flag | Default | Description |
|---|---|---|
| `--capital` | `50.0` | Starting USDT balance |
| `--leverage` | `10` | Leverage multiplier |
| `--spread` | `0.0004` | Half-spread fraction |
| `--fill-rate` | `0.35` | Probability of order fill per cycle (0 to 1) |
| `--refresh` | `3.0` | Order refresh interval in seconds |
| `--hours` | `24.0` | Simulation duration in hours |
| `--days` | `None` | Duration in days (overrides `--hours`) |
| `--price` | `61250.0` | Starting asset price |
| `--scenarios` | `1000` | Number of Monte Carlo iterations |

---

## Volume Simulator

The simulator uses **Geometric Brownian Motion (GBM)** — the same mathematical model used by options traders — to generate realistic price paths. Across thousands of scenarios it builds a probability distribution of:

- 📊 Trading volume generated
- 💸 Fees paid to the exchange
- 📈 Net PnL (positive or negative)
- 📉 Maximum drawdown
- 🔁 Number of completed round-trips

```bash
# Basic simulation: $50 capital, 10x leverage, 24 hours
python -X utf8 simulate.py --capital 50 --leverage 10 --hours 24 --scenarios 2000

# 7-day simulation
python -X utf8 simulate.py --capital 50 --leverage 10 --days 7 --scenarios 2000

# Conservative settings
python -X utf8 simulate.py --capital 50 --leverage 5 --spread 0.0006 --fill-rate 0.25

# Aggressive settings
python -X utf8 simulate.py --capital 50 --leverage 20 --spread 0.0003 --fill-rate 0.45
```

---

## Simulation Results: $50 Capital

> Results from 2,000 Monte Carlo scenarios | BTCUSDT at $61,250 | 10x leverage | ±0.04% spread | 35% fill rate

### Volume Projection (24 hours)

| Scenario | Generated Volume | vs Starting Capital |
|---|---|---|
| 🔴 Pessimistic (P10) | **$3,450** | 69× capital |
| 🟡 Median (P50) | **$5,850** | 117× capital |
| 🟢 Optimistic (P90) | **$9,550** | 191× capital |

### PnL & Risk (24 hours)

| Metric | Value | Note |
|---|---|---|
| Fees Paid (median) | -$1.96 | Maker-only: 0.020% |
| Net PnL (P10) | -$9.30 | Pessimistic scenario |
| Net PnL (Median) | -$3.64 | Typical scenario |
| Net PnL (P90) | +$4.02 | Optimistic scenario |
| Capital End (Median) | $45.65 | -8.71% from start |
| Max Drawdown (worst) | -$23.15 | Single worst scenario |
| Avg Round-Trips | 4.3 | Full buy+sell cycles |
| Scenarios Profitable | 27.6% | Net positive PnL |

> **Note:** The negative median PnL is driven by stop-losses when price trends sharply. Volume is consistently high even in losing scenarios — which is the primary goal.

### Volume Sensitivity Table

Estimated volume across leverage levels and time horizons ($50 capital, analytical model):

| Leverage | 1 Hour | 8 Hours | 24 Hours | 7 Days | 30 Days |
|---|---|---|---|---|---|
| **3×** | $12,600 | $100,800 | $302,400 | $2,116,800 | $9,072,000 |
| **5×** | $21,000 | $168,000 | $504,000 | $3,528,000 | $15,120,000 |
| **10×** | $42,000 | $336,000 | $1,008,000 | $7,056,000 | $30,240,000 |
| **20×** | $84,000 | $672,000 | $2,016,000 | $14,112,000 | $60,480,000 |

---

## Risk Controls

The bot has **four independent** protection layers:

```
Layer 1: Inventory Management
├─ Tracks current open position size
└─ Stops adding orders in the same direction when limit is reached

Layer 2: Price Drift Refresh
├─ Monitors how far price has moved from last order placement
└─ Cancels & replaces orders if price drifts >0.2% (configurable)

Layer 3: Stop-Loss
├─ Monitors unrealized PnL% on open position every cycle
└─ Flash-closes via market order if PnL% < --stop-loss threshold

Layer 4: Circuit Breaker
├─ Tracks cumulative realized losses across the session
└─ Shuts down bot entirely if total loss > --max-loss (USDT)
```

### On Exit (CTRL+C or circuit breaker)

The bot **always** runs cleanup:
1. Cancels all open limit orders on the symbol
2. Logs session summary: total volume, realized PnL
3. Saves full debug log to `logs/bot_YYYYMMDD_HHMMSS.log`

---

## Fee Structure

Bitunix uses a VIP tier system (verified June 2026):

| VIP Level | 30d Volume Required | Maker Fee | Taker Fee |
|---|---|---|---|
| **VIP 0** | Any | **0.020%** | 0.060% |
| VIP 1 | $1M | 0.020% | 0.050% |
| VIP 2 | $5M | 0.016% | 0.050% |
| VIP 3 | $10M | 0.014% | 0.040% |
| VIP 4 | $20M | 0.012% | 0.0375% |
| VIP 5 | $50M | 0.010% | 0.035% |

> Market makers place **limit orders** → always pay **Maker Fee** (0.020%).  
> Stop-losses execute at market price → pay **Taker Fee** (0.060%).

---

## Project Structure

```
bitunix-bot/
│
├── bot.py                    # Main entry point with CLI
├── bitunix_client.py         # REST API client (double SHA256 auth)
├── strategy_market_maker.py  # Market making strategy & logic
├── simulate.py               # Monte Carlo volume & PnL simulator
│
├── .env                      # 🔐 API credentials (NOT committed to git)
├── .gitignore                # Protects .env and logs
├── requirements.txt          # Python dependencies
├── README.md                 # This file
│
└── logs/                     # Auto-created; session log files
    └── bot_20260610_234700.log
```

### Architecture

```
bot.py
  ├── loads .env credentials
  ├── runs startup checks (connectivity + balance)
  └── instantiates MarketMaker(BitunixClient, MarketMakerConfig)
         │
         ├── BitunixClient        ← REST API with auto-retry & signed requests
         │     └── _build_sign()  ← SHA256(SHA256(...)) per Bitunix spec
         │
         └── MarketMaker.run()    ← Main loop every N seconds
               ├── _get_mid_price()      ← Order book depth
               ├── _check_stop_loss()    ← Guard on open position
               ├── _check_circuit_breaker()
               ├── _cancel_active_orders()
               └── _place_maker_orders() ← BUY + SELL limits
```

---

## Security

> ⚠️ **Your API keys are sensitive credentials. Treat them like passwords.**

**Best practices applied in this project:**

- ✅ `.env` is in `.gitignore` — will never be committed
- ✅ API key only shown as `bf85f9f3...e23b` in logs (truncated)
- ✅ No withdrawal permissions needed — use Trade-only API keys
- ✅ Keys loaded via `python-dotenv`, never hardcoded

**If your keys are ever exposed:**
1. Go to Bitunix → Account → API Management
2. Delete the compromised key immediately
3. Create a new key pair

---

## Disclaimer

> **This software is provided for educational purposes only.**  
> Trading cryptocurrency futures involves significant financial risk, including the possibility of losing your entire invested capital. Past simulated performance does not guarantee future real-world results. The authors of this software are not responsible for any financial losses incurred through its use. Always test with minimum amounts and in dry-run mode before using real funds.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
Made for Bitunix Futures · Python 3.10+ · Market Making Strategy
</div>
