# Mean-Reversion Maker Strategy (XLMUSDT)

> A **profit-oriented** strategy for Bitunix Futures — distinct from the volume
> market maker. It fades statistical extremes with maker-only orders.
> Validated on 60 days of real Bitunix data: **+18% / Sharpe ~1.3**, holds
> out-of-sample, survives slippage. See full research in [`research/`](research/).

---

## TL;DR

| | |
|---|---|
| **Asset** | `XLMUSDT` (validated). ETH and others showed **no edge** — don't use blindly. |
| **Idea** | When XLM deviates ≥ 3 standard deviations from its rolling mean, fade it with a **limit (maker)** order; exit when it reverts toward the mean. |
| **Why it works** | XLM is a volatile small-cap (≈109% annualized vol) → strong retail overreaction → mean reversion (Variance Ratio 0.80–0.89). |
| **The #1 lever** | **Maker execution.** Taker fees (0.06%) turn the same strategy from +18% to −40%. Maker (0.02%) preserves the edge. |
| **Capital** | Designed for a small account (~$20). Risk is bounded by the stop + daily circuit breakers. |

---

## How it works

The signal runs on **5-minute bars**. For each closed bar it computes the
z-score of the latest close versus a rolling window:

```
z = (close − mean(last 40 bars)) / std(last 40 bars)
```

State machine (one decision per closed bar):

```
        z ≤ −3.0                      z ≥ +3.0
   ┌──────────────┐             ┌──────────────┐
   │  oversold    │             │  overbought  │
   │  BUY  (limit)│             │  SELL (limit)│
   └──────┬───────┘             └──────┬───────┘
          │   price reverts to |z| ≤ 0.5 → EXIT (limit, take profit)
          │   |z| ≥ 4.5                 → STOP (maker, escalates to market)
          │   held ≥ 24 bars (2h)       → TIME-STOP (limit)
          ▼
        FLAT
```

- **Entry** rests passively on our side of the book (maker).
- **Exit / time-stop** is a reduce-only maker limit at the mean.
- **Catastrophe stop** is a maker limit that **escalates to a market close**
  if it doesn't fill within `stop_escalate_seconds` — protecting the account
  in a fast move.

### Why these parameters
The edge lives specifically in the **≥3σ tail**. Entering at 2.5σ *loses* money
(the move hasn't over-extended enough to revert reliably). Every parameter is
documented and validated in [`research/04_backtest_eth_xlm.md`](research/04_backtest_eth_xlm.md).

---

## Files

| File | Role |
|---|---|
| `meanrev_core.py` | **Pure signal logic** (`zscore`, `decide`). Shared by backtest **and** live bot, so the validated logic is exactly what trades. |
| `backtest.py` | Reusable backtester over real Bitunix klines (fees, slippage, out-of-sample split). |
| `strategy_meanrev.py` | Live + dry-run strategy. Execution state machine, maker orders, circuit breakers. |
| `meanrev_server.py` | Dedicated FastAPI dashboard server (port 8001). |
| `dashboard/meanrev.html` | Dedicated dashboard: live z-score with bands, PnL curve, trades, log. |
| `research/scripts/fetch_bitunix_klines.py` | Downloads real Bitunix klines for backtesting. |

---

## Usage

### 0. Install dependencies (one time)
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 1. Download fresh data & backtest
```bash
.venv/bin/python strategies/mean_reversion/research/scripts/fetch_bitunix_klines.py XLMUSDT --days 60 --interval 1m
.venv/bin/python strategies/mean_reversion/backtest.py strategies/mean_reversion/research/data/XLMUSDT_1m.csv --slip-bps 2 --oos
```

### 2. Dry-run (no real orders — recommended first)
```bash
# Via the dedicated dashboard:
.venv/bin/python strategies/mean_reversion/meanrev_server.py        # open http://localhost:8001

# …or headless via the CLI (needs .env for the account check):
.venv/bin/python bot.py --meanrev --symbol XLMUSDT --dry-run
```

### 3. Live (only after dry-run confirms good fills)
```bash
.venv/bin/python bot.py --meanrev --symbol XLMUSDT --qty 80 --leverage 3
```

---

## Parameters

| Flag / field | Default | Notes |
|---|---|---|
| `z_in` | 3.0 | Entry threshold. **Do not lower below 3** — the edge disappears. |
| `z_exit` | 0.5 | Exit band around the mean. |
| `z_stop` | 4.5 | Catastrophe stop. |
| `max_hold_bars` | 24 | Time-stop (24 × 5m = 2h). |
| `lookback` | 40 | Rolling window for mean/std. |
| `order_qty` | 80 | XLMUSDT minimum (≈$15 notional). |
| `leverage` | 3 | Risk is set by the stop, not by leverage. |

### Risk controls (for a ~$20 account)
- `max_total_loss_usdt = 4.0` — session circuit breaker (20% of account).
- `daily_loss_limit_usdt = 1.0` — pause for the rest of the UTC day.
- `cooldown_after_losses = 2` — 2 consecutive losses → pause for the day.

---

## Honest caveats

1. **Validated on 60 days / one dataset.** There will be losing stretches
   (one of four walk-forward blocks was flat/negative). This is a small, real
   edge — not a money printer.
2. **The maker fill at 3σ is the optimistic assumption.** In a real extreme
   move your limit may not fill, or fill with adverse selection. The dry-run
   measures exactly this gap — use it before risking capital.
3. **Edges decay.** Re-download data and re-validate periodically.
4. **Symbol-specific.** The edge was found on XLM; it does **not** transfer to
   ETH (tested, no edge). Re-run the research pipeline before trusting any other
   symbol.

> Educational software. Trading futures risks your capital. Start in dry-run,
> then with the minimum size.
