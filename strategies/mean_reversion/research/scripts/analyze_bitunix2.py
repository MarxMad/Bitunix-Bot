"""
Round 2 on real Bitunix data: exploit the MEAN-REVERSION (VR<1) with MAKER
execution (limit orders, 0.02%) instead of taker. Tune selectivity + time-stop.

Assumption: a limit entry at the band fills when price trades through it (standard
backtest fill model). Round-trip cost = 2*MAKER. We add a hard time-stop and a
catastrophe stop. Compares taker vs maker to show the fee lever.
"""
import sys, os
import pandas as pd, numpy as np

MAKER = 0.0002
TAKER = 0.0006

def load(path):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df.sort_values("ts").reset_index(drop=True)

def bt_meanrev(df5, lookback=40, z_in=2.5, z_exit=0.0, z_stop=4.5,
               max_hold=24, cost=MAKER, trend_filter=False, trend_span=200):
    """Mean-reversion: enter limit at -/+ z_in, exit at mean (z_exit), hard stop z_stop,
    time-stop max_hold bars. Optional: only take longs above long EMA / shorts below."""
    c = df5["close"]
    ma = c.rolling(lookback).mean()
    sd = c.rolling(lookback).std()
    z = (c - ma) / sd
    ema = c.ewm(span=trend_span).mean()
    gap = df5["gap"].values
    pos = 0; entry = 0.0; held = 0; rets = []
    for i in range(lookback, len(df5)):
        if gap[i]:
            pos = 0; continue
        px = c.iloc[i]; zi = z.iloc[i]
        if pos == 0:
            if not np.isfinite(zi):
                continue
            long_ok = (not trend_filter) or (px > ema.iloc[i])
            short_ok = (not trend_filter) or (px < ema.iloc[i])
            if zi <= -z_in and long_ok:
                pos, entry, held = 1, px, 0
            elif zi >= z_in and short_ok:
                pos, entry, held = -1, px, 0
        else:
            held += 1
            hit_target = (pos == 1 and zi >= z_exit) or (pos == -1 and zi <= z_exit)
            hit_stop = abs(zi) >= z_stop
            hit_time = held >= max_hold
            if hit_target or hit_stop or hit_time:
                # target/time = maker (limit); stop = taker (market)
                exit_cost = cost if (hit_target or hit_time) else TAKER
                rets.append((px/entry - 1)*pos - cost - exit_cost)
                pos = 0
    return np.array(rets)

def summarize(rets, label):
    if len(rets) == 0:
        print(f"  {label}: no trades"); return
    eq = np.cumprod(1+rets)
    dd = (eq/np.maximum.accumulate(eq) - 1).min()
    sharpe = rets.mean()/rets.std()*np.sqrt(len(rets)) if rets.std()>0 else 0
    print(f"  {label}: n={len(rets):4d} win={(rets>0).mean()*100:4.1f}% "
          f"net={rets.sum()*100:+7.1f}% avg={rets.mean()*1e4:+6.1f}bps "
          f"maxDD={dd*100:5.1f}% Sharpe~={sharpe:+.2f}")

def run(path):
    df = load(path)
    name = os.path.basename(path)
    df5 = df.set_index("ts").resample("5min").agg(
        open=("open","first"),high=("high","max"),low=("low","min"),
        close=("close","last")).dropna()
    df5["gap"] = df5.index.to_series().diff().dt.total_seconds()/60 > 6
    print("="*72); print(f"{name}  ({len(df5):,} M5 bars)"); print("="*72)

    print("\n[A] Fee lever: same strat (z_in=2.5) TAKER vs MAKER")
    summarize(bt_meanrev(df5, z_in=2.5, cost=TAKER), "TAKER 0.06%")
    summarize(bt_meanrev(df5, z_in=2.5, cost=MAKER), "MAKER 0.02%")

    print("\n[B] Selectivity sweep (MAKER, exit at mean, time-stop 24 bars=2h)")
    for zin in [2.0, 2.5, 3.0, 3.5]:
        summarize(bt_meanrev(df5, z_in=zin, cost=MAKER), f"z_in={zin}")

    print("\n[C] Exit target sweep (MAKER, z_in=3.0)")
    for ze in [0.0, 0.5, 1.0]:
        summarize(bt_meanrev(df5, z_in=3.0, z_exit=ze, cost=MAKER), f"z_exit={ze}")

    print("\n[D] Time-stop sweep (MAKER, z_in=3.0, exit mean)")
    for mh in [6, 12, 24, 48]:
        summarize(bt_meanrev(df5, z_in=3.0, max_hold=mh, cost=MAKER), f"max_hold={mh}bars")

    print("\n[E] With trend filter (only revert toward EMA200) MAKER z_in=3.0")
    summarize(bt_meanrev(df5, z_in=3.0, cost=MAKER, trend_filter=True), "trend-filtered")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        run(p)
