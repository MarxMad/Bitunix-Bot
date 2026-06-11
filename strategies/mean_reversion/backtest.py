# -*- coding: utf-8 -*-
"""
Backtester for the Mean-Reversion strategy
==========================================
Reusable, repo-level backtester that drives meanrev_core.decide() bar by bar on
real Bitunix klines — so the numbers here reflect the SAME logic the live bot runs.

Data: CSV produced by research/scripts/fetch_bitunix_klines.py
      (cols: time,datetime,open,high,low,close,baseVol,quoteVol)

Usage:
    python backtest.py research/data/XLMUSDT_1m.csv
    python backtest.py research/data/XLMUSDT_1m.csv --tf 5min --z-in 3.0 --slip-bps 2
    python backtest.py research/data/XLMUSDT_1m.csv --oos        # train/test split
"""

import argparse
import csv
from dataclasses import dataclass

# ── make repo root importable when run directly ──
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))

from strategies.mean_reversion.meanrev_core import (
    MeanRevParams, zscore, decide,
    ENTER_LONG, ENTER_SHORT, EXIT, STOP, TIME_STOP,
)

# Bitunix VIP0 crypto fees (fraction of notional)
MAKER = 0.0002   # 0.02% — limit orders
TAKER = 0.0006   # 0.06% — market orders


@dataclass
class CostModel:
    entry_fee: float = MAKER      # limit entry
    exit_fee: float = MAKER       # limit target / time-stop
    stop_fee: float = MAKER       # catastrophe stop (maker by default; see --stop-market)
    slip_bps_per_leg: float = 0.0 # adverse slippage haircut per leg


# ─── Data loading + resampling ──────────────────────────────────────────────

def load_m1(path):
    """Load the M1 CSV -> list of (epoch_ms, open, high, low, close)."""
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append((int(r["time"]), float(r["open"]), float(r["high"]),
                         float(r["low"]), float(r["close"])))
    rows.sort(key=lambda x: x[0])
    return rows


def resample(m1, tf_minutes):
    """Aggregate M1 bars into tf_minutes OHLC bars. Returns list of dict bars."""
    bucket_ms = tf_minutes * 60_000
    bars, cur = [], None
    for ts, o, h, l, c in m1:
        b = (ts // bucket_ms) * bucket_ms
        if cur is None or b != cur["b"]:
            if cur:
                bars.append(cur)
            cur = {"b": b, "open": o, "high": h, "low": l, "close": c, "prev_b": b}
        else:
            cur["high"] = max(cur["high"], h)
            cur["low"] = min(cur["low"], l)
            cur["close"] = c
        cur["ts"] = b
    if cur:
        bars.append(cur)
    # mark gaps (missing buckets between consecutive bars)
    for i in range(1, len(bars)):
        bars[i]["gap"] = (bars[i]["b"] - bars[i - 1]["b"]) > bucket_ms
    if bars:
        bars[0]["gap"] = False
    return bars


# ─── Core backtest loop (mirrors live execution) ────────────────────────────

def run_backtest(bars, params: MeanRevParams, costs: CostModel):
    """Walk bars, apply decide(), collect per-trade returns (fractions)."""
    closes = []
    pos_side = None          # "long" / "short" / None
    entry_px = 0.0
    bars_held = 0
    trades = []              # dicts: ret, reason, dir
    slip = costs.slip_bps_per_leg / 1e4

    for bar in bars:
        # A gap (missing data / downtime) forces us flat — no fills across a gap
        if bar.get("gap"):
            pos_side, bars_held = None, 0
        closes.append(bar["close"])
        z = zscore(closes, params.lookback)

        if pos_side:
            bars_held += 1
        action = decide(z, pos_side, bars_held, params)
        px = bar["close"]

        if action in (ENTER_LONG, ENTER_SHORT):
            pos_side = "long" if action == ENTER_LONG else "short"
            entry_px = px
            bars_held = 0
        elif action in (EXIT, STOP, TIME_STOP):
            d = 1 if pos_side == "long" else -1
            exit_fee = costs.stop_fee if action == STOP else costs.exit_fee
            gross = (px / entry_px - 1) * d
            ret = gross - costs.entry_fee - exit_fee - 2 * slip
            trades.append({"ret": ret, "reason": action, "dir": pos_side})
            pos_side, bars_held = None, 0

    return trades


# ─── Metrics ────────────────────────────────────────────────────────────────

def metrics(trades, label=""):
    if not trades:
        print(f"  {label}: no trades")
        return
    rets = [t["ret"] for t in trades]
    n = len(rets)
    wins = sum(1 for r in rets if r > 0)
    # equity curve + max drawdown
    eq, peak, maxdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= (1 + r)
        peak = max(peak, eq)
        maxdd = min(maxdd, eq / peak - 1)
    total = (eq - 1) * 100
    avg_bps = sum(rets) / n * 1e4
    mean, var = sum(rets) / n, 0.0
    for r in rets:
        var += (r - mean) ** 2
    sd = (var / n) ** 0.5
    sharpe = (mean / sd * (n ** 0.5)) if sd > 0 else 0.0
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    print(f"  {label}: trades={n}  win={wins/n*100:.1f}%  net={total:+.1f}%  "
          f"avg={avg_bps:+.1f}bps  maxDD={maxdd*100:.1f}%  Sharpe~={sharpe:+.2f}")
    print(f"      exits: {reasons}")


# ─── CLI ──────────────────────────────────────────────────────────────────--

def main():
    ap = argparse.ArgumentParser(description="Mean-reversion backtester (Bitunix data)")
    ap.add_argument("csv", help="Path to M1 CSV from fetch_bitunix_klines.py")
    ap.add_argument("--tf", type=int, default=5, help="Timeframe minutes (default 5)")
    ap.add_argument("--lookback", type=int, default=40)
    ap.add_argument("--z-in", type=float, default=3.0)
    ap.add_argument("--z-exit", type=float, default=0.5)
    ap.add_argument("--z-stop", type=float, default=4.5)
    ap.add_argument("--max-hold", type=int, default=24, help="Time-stop in bars")
    ap.add_argument("--slip-bps", type=float, default=2.0, help="Adverse slippage per leg")
    ap.add_argument("--stop-market", action="store_true",
                    help="Model catastrophe stop as TAKER (market) instead of maker")
    ap.add_argument("--oos", action="store_true", help="Also show train/test split")
    ap.add_argument("--walk", type=int, default=0,
                    help="Walk-forward: split into N consecutive blocks and report each")
    a = ap.parse_args()

    params = MeanRevParams(lookback=a.lookback, z_in=a.z_in, z_exit=a.z_exit,
                           z_stop=a.z_stop, max_hold_bars=a.max_hold)
    costs = CostModel(stop_fee=(TAKER if a.stop_market else MAKER),
                      slip_bps_per_leg=a.slip_bps)

    m1 = load_m1(a.csv)
    bars = resample(m1, a.tf)
    span_days = (bars[-1]["b"] - bars[0]["b"]) / 86_400_000 if bars else 0
    print(f"Data: {a.csv}")
    print(f"  {len(m1):,} M1 bars -> {len(bars):,} M{a.tf} bars ({span_days:.1f} days)")
    print(f"Params: z_in={a.z_in} z_exit={a.z_exit} z_stop={a.z_stop} "
          f"lookback={a.lookback} max_hold={a.max_hold}  "
          f"stop={'TAKER' if a.stop_market else 'MAKER'}  slip={a.slip_bps}bps/leg\n")

    metrics(run_backtest(bars, params, costs), "FULL")

    if a.oos:
        mid = len(bars) // 2
        print()
        metrics(run_backtest(bars[:mid], params, costs), "TRAIN (1st half)")
        metrics(run_backtest(bars[mid:], params, costs), "TEST  (2nd half)")

    if a.walk > 1:
        import time as _t
        print(f"\nWalk-forward ({a.walk} consecutive blocks):")
        block = len(bars) // a.walk
        for k in range(a.walk):
            seg = bars[k*block:(k+1)*block] if k < a.walk-1 else bars[k*block:]
            if len(seg) < a.lookback + 5:
                continue
            d0 = _t.strftime("%Y-%m-%d", _t.gmtime(seg[0]["b"]/1000))
            d1 = _t.strftime("%m-%d", _t.gmtime(seg[-1]["b"]/1000))
            metrics(run_backtest(seg, params, costs), f"[{d0}->{d1}]")


if __name__ == "__main__":
    main()
