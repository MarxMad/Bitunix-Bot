# -*- coding: utf-8 -*-
"""
Trend-Following backtester + optimizer
======================================
Vectorized. Uses shared/backtest_engine.py for data, PnL and metrics, and
core.compute_position for the signal.

Single run:
    python strategies/trend_following/backtest.py DATA.csv --tf 1h --fast 12 --slow 48

Optimize (parameter sweep with honest train/test split):
    python strategies/trend_following/backtest.py DATA.csv --optimize
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))

import argparse
import itertools

from shared.backtest_engine import (
    load_klines, resample, vectorized_pnl, trade_list, metrics, split_time, TAKER,
)
from strategies.trend_following.core import TrendParams, compute_position, vol_target

# module-level vol-targeting config (set from CLI); 0 target disables
VT = {"target": 0.0, "lookback": 72, "max_lev": 2.0}


def _position(df_tf, p):
    pos = compute_position(df_tf, p)
    if VT["target"] > 0:
        pos = vol_target(df_tf, pos, VT["target"], VT["lookback"], VT["max_lev"])
    return pos


def run_one(df_tf, p, fee, label=""):
    pos = _position(df_tf, p)
    net, turn = vectorized_pnl(df_tf, pos, fee=fee)
    n_trades = int(turn.sum() / 2)
    return metrics(net, p.tf, n_trades=n_trades, label=label)


def single(csv, p, fee):
    df = load_klines(csv)
    df_tf = resample(df, p.tf)
    span = (df_tf.index[-1] - df_tf.index[0]).days
    print(f"Data: {csv}  ->  {len(df_tf):,} {p.tf} bars ({span} days)")
    print(f"Config: tf={p.tf} fast={p.fast} slow={p.slow} min_sep={p.min_sep} "
          f"short={p.allow_short} fee={fee*100:.3f}%"
          + (f"  vol_target={VT['target']} (lb={VT['lookback']}, maxLev={VT['max_lev']})"
             if VT["target"] > 0 else "  vol_target=off") + "\n")

    run_one(df_tf, p, fee, "FULL")
    tr, te = split_time(df_tf, 0.66)
    run_one(tr, p, fee, "TRAIN (66%)")
    run_one(te, p, fee, "TEST  (34%)")

    # exit/trade quality on full (segment by raw signal; sizing doesn't add trades)
    trades = trade_list(df_tf, compute_position(df_tf, p), fee)
    if trades:
        wins = sum(1 for t in trades if t["ret"] > 0)
        print(f"\n  per-trade: n={len(trades)} win={wins/len(trades)*100:.1f}% "
              f"avg={sum(t['ret'] for t in trades)/len(trades)*1e4:+.1f}bps")
    print(f"  buy&hold: {(df_tf['close'].iloc[-1]/df_tf['close'].iloc[0]-1)*100:+.1f}%")


def optimize(csv, fee):
    df = load_klines(csv)
    grid_tf = ["1h", "4h"]
    grid_fast = [8, 12, 21, 34]
    grid_slow = [50, 100, 150, 200]
    grid_sep = [0.0, 0.005, 0.01]

    results = []
    cache = {}
    for tf in grid_tf:
        if tf not in cache:
            cache[tf] = resample(df, tf)
        df_tf = cache[tf]
        tr, te = split_time(df_tf, 0.66)
        for fast, slow, sep in itertools.product(grid_fast, grid_slow, grid_sep):
            if fast >= slow:
                continue
            p = TrendParams(tf=tf, fast=fast, slow=slow, min_sep=sep)
            ptr, pte = compute_position(tr, p), compute_position(te, p)
            ntr, _ = vectorized_pnl(tr, ptr, fee)
            nte, _ = vectorized_pnl(te, pte, fee)
            m_tr = metrics(ntr, tf)
            m_te = metrics(nte, tf)
            results.append((p, m_tr, m_te))

    # rank by TRAIN sharpe; show TEST alongside (honest generalization check)
    results.sort(key=lambda r: r[1]["sharpe"], reverse=True)
    print(f"\nData: {csv}  | {len(results)} configs | fee={fee*100:.3f}%")
    print(f"Ranked by TRAIN Sharpe (showing TEST for generalization):\n")
    print(f"  {'tf':>3} {'fast':>4} {'slow':>4} {'sep':>5} | "
          f"{'TRAIN net':>9} {'shrp':>5} | {'TEST net':>9} {'shrp':>5}")
    print("  " + "-" * 62)
    for p, mtr, mte in results[:15]:
        print(f"  {p.tf:>3} {p.fast:>4} {p.slow:>4} {p.min_sep:>5} | "
              f"{mtr['net_pct']:>8.1f}% {mtr['sharpe']:>5.2f} | "
              f"{mte['net_pct']:>8.1f}% {mte['sharpe']:>5.2f}")

    # robust pick: positive on BOTH train and test, best by min(train,test) sharpe
    robust = [(p, mtr, mte) for p, mtr, mte in results
              if mtr["sharpe"] > 0 and mte["sharpe"] > 0]
    robust.sort(key=lambda r: min(r[1]["sharpe"], r[2]["sharpe"]), reverse=True)
    print(f"\n  Configs positive on BOTH train & test: {len(robust)}/{len(results)}")
    if robust:
        p, mtr, mte = robust[0]
        print(f"  >> Most robust: tf={p.tf} fast={p.fast} slow={p.slow} sep={p.min_sep}"
              f"  | train {mtr['net_pct']:+.1f}%/{mtr['sharpe']:+.2f}"
              f"  test {mte['net_pct']:+.1f}%/{mte['sharpe']:+.2f}")
    else:
        print("  >> No config generalizes (positive on both halves). Trend-following "
              "may not have a robust edge here — same honesty as mean-reversion.")


def main():
    ap = argparse.ArgumentParser(description="Trend-following backtester/optimizer")
    ap.add_argument("csv")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    ap.add_argument("--min-sep", type=float, default=0.0)
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--fee", type=float, default=TAKER, help="per-leg fee fraction")
    ap.add_argument("--vol-target", type=float, default=0.0,
                    help="per-bar vol target for sizing (0=off, e.g. 0.008)")
    ap.add_argument("--vol-lookback", type=int, default=72)
    ap.add_argument("--max-lev", type=float, default=2.0)
    ap.add_argument("--optimize", action="store_true")
    a = ap.parse_args()

    VT["target"], VT["lookback"], VT["max_lev"] = a.vol_target, a.vol_lookback, a.max_lev

    if a.optimize:
        optimize(a.csv, a.fee)
    else:
        p = TrendParams(tf=a.tf, fast=a.fast, slow=a.slow, min_sep=a.min_sep,
                        allow_short=not a.no_short)
        single(a.csv, p, a.fee)


if __name__ == "__main__":
    main()
