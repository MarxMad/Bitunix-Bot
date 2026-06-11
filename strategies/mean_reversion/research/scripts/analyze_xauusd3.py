"""
Round 3: test MEAN-REVERSION (since breakout lost) + session long-bias,
with realistic costs and trade-frequency sensitivity.
"""
import pandas as pd
import numpy as np

COLS = ["date","time","open","high","low","close","volume"]
df = pd.read_csv("XAUUSD1.csv", header=None, names=COLS)
df["ts"] = pd.to_datetime(df["date"]+" "+df["time"], format="%Y.%m.%d %H:%M")
df = df.sort_values("ts").reset_index(drop=True)
df = df[df["ts"] < "2025-12-01"].reset_index(drop=True)
df["dt_min"] = df["ts"].diff().dt.total_seconds()/60
df["hour"] = df["ts"].dt.hour
df["day"] = df["ts"].dt.date

tr = pd.concat([df["high"]-df["low"],
                (df["high"]-df["close"].shift()).abs(),
                (df["low"]-df["close"].shift()).abs()], axis=1).max(axis=1)
df["atr"] = tr.rolling(14).mean()

# ============================================================
# H4: Z-score mean reversion on M5, trade only NY high-liquidity hours
# Enter when price deviates > Z from rolling mean; exit on revert to mean.
# ============================================================
df5 = df.set_index("ts").resample("5min").agg(
    open=("open","first"),high=("high","max"),low=("low","min"),
    close=("close","last"),volume=("volume","sum"),hour=("hour","first")).dropna()
df5["gap"] = df5.index.to_series().diff().dt.total_seconds()/60 > 6

def bt_meanrev(lookback=20, z_in=2.0, z_out=0.3, hours=range(14,21),
               cost_pct=0.0006, stop_z=4.0):
    c = df5["close"]
    ma = c.rolling(lookback).mean()
    sd = c.rolling(lookback).std()
    z = (c-ma)/sd
    pos = 0
    rets, entry_px, ntr = [], 0.0, 0
    idx = df5.index
    for i in range(lookback, len(df5)):
        if df5["gap"].iloc[i]:
            pos = 0
            continue
        h = df5["hour"].iloc[i]
        px = c.iloc[i]
        zi = z.iloc[i]
        if pos == 0:
            if h in hours and np.isfinite(zi):
                if zi <= -z_in:
                    pos, entry_px = 1, px; ntr += 1
                elif zi >= z_in:
                    pos, entry_px = -1, px; ntr += 1
        else:
            exit_now = (abs(zi) <= z_out) or (abs(zi) >= stop_z) or (h not in hours)
            if exit_now:
                rets.append((px/entry_px - 1)*pos - cost_pct*2)
                pos = 0
    rets = np.array(rets)
    return rets, ntr

print("=== H4: M5 Z-score MEAN REVERSION (NY hours 14-20 broker) ===")
for zin in [1.5, 2.0, 2.5]:
    rets, ntr = bt_meanrev(z_in=zin)
    if len(rets)==0: continue
    print(f"  Z_in={zin}: trades={len(rets):3d}  win%={(rets>0).mean()*100:4.1f}  "
          f"cumNet={rets.sum()*100:+6.2f}%  avg/tr={rets.mean()*100:+.3f}%  "
          f"Sharpe~={rets.mean()/rets.std()*np.sqrt(len(rets)):.2f}" )

# ============================================================
# H5: Pure session long-bias — buy at 16:00, hold to 19:00 (broker), daily.
# Measures the NY drift edge without micro-trading.
# ============================================================
print("\n=== H5: Session LONG-bias (buy 16:00 close, sell 19:00 close) ===")
res = []
for day, g in df.groupby("day"):
    g = g[(g["hour"]>=16)&(g["hour"]<19)]
    if len(g) < 30: continue
    e = g["close"].iloc[0]; x = g["close"].iloc[-1]
    res.append((x/e-1) - 0.0006*2)  # round-trip taker cost
res = np.array(res)
if len(res):
    print(f"  days={len(res)}  win%={(res>0).mean()*100:.1f}  "
          f"cumNet={res.sum()*100:+.2f}%  avg/day={res.mean()*100:+.3f}%  "
          f"Sharpe~={res.mean()/res.std()*np.sqrt(252):.2f}")

# Same but SHORT the dead hour 0 (was significantly negative)
print("\n=== H5b: SHORT hour 23->0 (the significant negative drift) ===")
res2=[]
for day, g in df.groupby("day"):
    g23 = g[g["hour"]==23]; g0 = g[g["hour"]==0]
    if len(g23)<30 or len(g0)<30: continue
    e=g23["close"].iloc[-1]; x=g0["close"].iloc[-1]
    res2.append(-(x/e-1) - 0.0006*2)
res2=np.array(res2)
if len(res2):
    print(f"  days={len(res2)}  win%={(res2>0).mean()*100:.1f}  "
          f"cumNet={res2.sum()*100:+.2f}%  avg/day={res2.mean()*100:+.3f}%")

# ============================================================
# H6: Frequency cost demonstration — buy&hold the whole sample
# ============================================================
print("\n=== H6: Context — buy & hold whole sample ===")
bh = df["close"].iloc[-1]/df["close"].iloc[0]-1
print(f"  Buy&hold return (gold, Jul-Sep 2025) = {bh*100:+.1f}%  "
      f"(STRONG UPTREND — long strategies flattered by regime)")
