# -*- coding: utf-8 -*-
"""
Momentum-MTF backtester + optimizer (vectorized).

Single:   python strategies/momentum_mtf/backtest.py DATA.csv --macro-tf 4h --macro-ema 100 --micro-fast 12 --micro-slow 50
Optimize: python strategies/momentum_mtf/backtest.py DATA.csv --optimize
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..")))

import argparse
import itertools

from shared.backtest_engine import (
    load_klines, vectorized_pnl, trade_list, metrics, split_time, TAKER,
)
from strategies.momentum_mtf.core import MTFParams, build


def _run(df_raw, p, fee, label=""):
    micro, pos = build(df_raw, p)
    net, turn = vectorized_pnl(micro, pos, fee=fee)
    return metrics(net, p.micro_tf, n_trades=int(turn.sum() / 2), label=label), micro, pos


def single(csv, p, fee):
    df = load_klines(csv)
    micro, pos = build(df, p)
    span = (micro.index[-1] - micro.index[0]).days
    print(f"Data: {csv}  ->  {len(micro):,} {p.micro_tf} bars ({span} days)")
    print(f"Config: macro {p.macro_tf}/EMA{p.macro_ema}  micro {p.micro_tf} "
          f"{p.micro_fast}/{p.micro_slow}  short={p.allow_short}  fee={fee*100:.3f}%\n")

    net, turn = vectorized_pnl(micro, pos, fee=fee)
    metrics(net, p.micro_tf, int(turn.sum()/2), "FULL")

    # split: rebuild on each slice's raw window would change resample edges; instead
    # split the micro/pos series directly (good enough for evaluation)
    k = int(len(micro) * 0.66)
    for lab, sl in [("TRAIN (66%)", slice(0, k)), ("TEST  (34%)", slice(k, None))]:
        m, ps = micro.iloc[sl], pos.iloc[sl]
        n, t = vectorized_pnl(m, ps, fee=fee)
        metrics(n, p.micro_tf, int(t.sum()/2), lab)

    trades = trade_list(micro, pos, fee)
    if trades:
        wins = sum(1 for t in trades if t["ret"] > 0)
        print(f"\n  per-trade: n={len(trades)} win={wins/len(trades)*100:.1f}% "
              f"avg={sum(t['ret'] for t in trades)/len(trades)*1e4:+.1f}bps "
              f"flat%={(pos==0).mean()*100:.0f}")
    print(f"  buy&hold: {(micro['close'].iloc[-1]/micro['close'].iloc[0]-1)*100:+.1f}%")


def optimize(csv, fee):
    df = load_klines(csv)
    grid = {
        "macro_tf": ["4h", "1d"],
        "macro_ema": [50, 100, 200],
        "micro_fast": [8, 12, 21],
        "micro_slow": [50, 100],
    }
    results = []
    for mtf, mema, mf, ms in itertools.product(
            grid["macro_tf"], grid["macro_ema"], grid["micro_fast"], grid["micro_slow"]):
        if mf >= ms:
            continue
        p = MTFParams(macro_tf=mtf, macro_ema=mema, micro_fast=mf, micro_slow=ms)
        micro, pos = build(df, p)
        k = int(len(micro) * 0.66)
        ntr, _ = vectorized_pnl(micro.iloc[:k], pos.iloc[:k], fee)
        nte, _ = vectorized_pnl(micro.iloc[k:], pos.iloc[k:], fee)
        results.append((p, metrics(ntr, p.micro_tf), metrics(nte, p.micro_tf)))

    results.sort(key=lambda r: r[1]["sharpe"], reverse=True)
    print(f"\nData: {csv}  | {len(results)} configs | fee={fee*100:.3f}%")
    print(f"Ranked by TRAIN Sharpe (TEST shown for generalization):\n")
    print(f"  {'macroTF':>7} {'mEMA':>4} {'mf':>3} {'ms':>4} | "
          f"{'TRAIN net':>9} {'shrp':>5} | {'TEST net':>9} {'shrp':>5}")
    print("  " + "-" * 60)
    for p, mtr, mte in results[:15]:
        print(f"  {p.macro_tf:>7} {p.macro_ema:>4} {p.micro_fast:>3} {p.micro_slow:>4} | "
              f"{mtr['net_pct']:>8.1f}% {mtr['sharpe']:>5.2f} | "
              f"{mte['net_pct']:>8.1f}% {mte['sharpe']:>5.2f}")

    robust = [(p, a, b) for p, a, b in results if a["sharpe"] > 0 and b["sharpe"] > 0]
    robust.sort(key=lambda r: min(r[1]["sharpe"], r[2]["sharpe"]), reverse=True)
    print(f"\n  Positive on BOTH train & test: {len(robust)}/{len(results)}")
    if robust:
        p, a, b = robust[0]
        print(f"  >> Most robust: macro {p.macro_tf}/EMA{p.macro_ema} micro "
              f"{p.micro_fast}/{p.micro_slow} | train {a['net_pct']:+.1f}%/{a['sharpe']:+.2f}"
              f"  test {b['net_pct']:+.1f}%/{b['sharpe']:+.2f}")
    else:
        print("  >> No config generalizes. MTF momentum has no robust edge here either.")


def main():
    ap = argparse.ArgumentParser(description="Momentum multi-timeframe backtester")
    ap.add_argument("csv")
    ap.add_argument("--micro-tf", default="1h")
    ap.add_argument("--macro-tf", default="4h")
    ap.add_argument("--macro-ema", type=int, default=100)
    ap.add_argument("--micro-fast", type=int, default=12)
    ap.add_argument("--micro-slow", type=int, default=50)
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--fee", type=float, default=TAKER)
    ap.add_argument("--optimize", action="store_true")
    a = ap.parse_args()

    if a.optimize:
        optimize(a.csv, a.fee)
    else:
        p = MTFParams(micro_tf=a.micro_tf, macro_tf=a.macro_tf, macro_ema=a.macro_ema,
                      micro_fast=a.micro_fast, micro_slow=a.micro_slow,
                      allow_short=not a.no_short)
        single(a.csv, p, a.fee)


if __name__ == "__main__":
    main()
