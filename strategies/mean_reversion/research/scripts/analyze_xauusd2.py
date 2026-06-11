"""
Refined analysis: clean stats (no cross-gap returns) + hypothesis backtests.
Focus on the 2025-07 .. 2025-09 continuous block (the 2026-06 tail = 4 bars).
"""
import pandas as pd
import numpy as np

COLS = ["date", "time", "open", "high", "low", "close", "volume"]
df = pd.read_csv("XAUUSD1.csv", header=None, names=COLS)
df["ts"] = pd.to_datetime(df["date"] + " " + df["time"], format="%Y.%m.%d %H:%M")
df = df.sort_values("ts").reset_index(drop=True)

# Drop the 4-bar 2026 tail so the big gap doesn't pollute anything
df = df[df["ts"] < "2025-12-01"].reset_index(drop=True)

# Mark session breaks: a return is INVALID if previous bar is >5 min away (weekend/gap)
df["dt_min"] = df["ts"].diff().dt.total_seconds() / 60
df["valid"] = df["dt_min"] <= 5
df["logret"] = np.log(df["close"] / df["close"].shift(1))
df.loc[~df["valid"], "logret"] = np.nan

r = df["logret"].dropna()
print("=== CLEAN PER-MINUTE RETURN STATS (intraday only) ===")
print(f"Bars (clean rets) : {len(r):,}")
print(f"Mean              : {r.mean()*1e4:+.4f} bps/min")
print(f"Std               : {r.std()*1e4:.2f} bps/min")
print(f"Skew              : {r.skew():+.3f}")
print(f"Kurtosis (excess) : {r.kurt():.2f}")
print(f"Max up / down     : {r.max()*1e4:+.1f} / {r.min()*1e4:+.1f} bps")
ann = r.std()*np.sqrt(60*24*252)*100
print(f"Annualized vol    : {ann:.1f}%")

# ----- Realistic cost model for a $20 futures account on gold-like perp -----
# Assume taker 0.06%, maker 0.02%. Gold moves ~0.8 pt ATR(14) per minute.
# We size by risk, not by notional fixed qty.

# ============================================================
# HYPOTHESIS 1: NY-session Opening Range Breakout (ORB)
# Broker time ~GMT+3 => 16:00-19:00 = London/NY overlap (peak vol).
# Build OR from first 15 min of 16:00, trade breakout, ATR stop/target.
# ============================================================
df["hour"] = df["ts"].dt.hour
df["day"] = df["ts"].dt.date

# ATR for sizing
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift()).abs(),
    (df["low"] - df["close"].shift()).abs(),
], axis=1).max(axis=1)
df["atr"] = tr.rolling(14).mean()

def backtest_orb(or_minutes=15, session_start=16, session_end=20,
                 atr_stop=1.0, rr=1.5, cost_pts=0.6):
    trades = []
    for day, g in df.groupby("day"):
        g = g.reset_index(drop=True)
        sess = g[(g["hour"] >= session_start) & (g["hour"] < session_end)]
        if len(sess) < or_minutes + 5:
            continue
        opening = sess.iloc[:or_minutes]
        or_hi, or_lo = opening["high"].max(), opening["low"].min()
        rest = sess.iloc[or_minutes:]
        atr = opening["atr"].iloc[-1]
        if not np.isfinite(atr) or atr <= 0:
            continue
        pos = 0  # 0 flat, 1 long, -1 short
        entry = stop = target = 0.0
        for _, bar in rest.iterrows():
            if pos == 0:
                if bar["high"] > or_hi:  # long breakout
                    pos, entry = 1, or_hi
                    stop, target = entry - atr*atr_stop, entry + atr*atr_stop*rr
                elif bar["low"] < or_lo:  # short breakout
                    pos, entry = -1, or_lo
                    stop, target = entry + atr*atr_stop, entry - atr*atr_stop*rr
            else:
                if pos == 1:
                    if bar["low"] <= stop:
                        trades.append((entry, stop, -1)); pos = 0; break
                    if bar["high"] >= target:
                        trades.append((entry, target, 1)); pos = 0; break
                else:
                    if bar["high"] >= stop:
                        trades.append((entry, stop, -1)); pos = 0; break
                    if bar["low"] <= target:
                        trades.append((entry, target, 1)); pos = 0; break
        # end of session: close at last
        if pos != 0:
            last = rest.iloc[-1]["close"]
            pnl_pts = (last - entry) * pos
            trades.append((entry, last, np.sign(pnl_pts) if pnl_pts!=0 else 0))
    if not trades:
        return None
    # PnL in points, minus cost
    pts = []
    for entry, exitp, _ in trades:
        # we need direction sign embedded; recompute via stored exit vs entry won't keep dir.
        pass
    return trades

# Re-implement cleanly returning point PnL
def backtest_orb2(or_minutes=15, session_start=16, session_end=20,
                  atr_stop=1.0, rr=1.5, cost_pts=0.6, vol_filter=True):
    results = []
    for day, g in df.groupby("day"):
        g = g.reset_index(drop=True)
        sess = g[(g["hour"] >= session_start) & (g["hour"] < session_end)].reset_index(drop=True)
        if len(sess) < or_minutes + 5:
            continue
        opening = sess.iloc[:or_minutes]
        or_hi, or_lo = opening["high"].max(), opening["low"].min()
        rest = sess.iloc[or_minutes:]
        atr = opening["atr"].iloc[-1]
        if not np.isfinite(atr) or atr <= 0:
            continue
        pos, entry, stop, target = 0, 0.0, 0.0, 0.0
        for _, bar in rest.iterrows():
            if pos == 0:
                if bar["high"] > or_hi:
                    pos, entry = 1, or_hi
                    stop, target = entry - atr*atr_stop, entry + atr*atr_stop*rr
                elif bar["low"] < or_lo:
                    pos, entry = -1, or_lo
                    stop, target = entry + atr*atr_stop, entry - atr*atr_stop*rr
            elif pos == 1:
                if bar["low"] <= stop:
                    results.append(stop-entry-cost_pts); pos=0; break
                if bar["high"] >= target:
                    results.append(target-entry-cost_pts); pos=0; break
            elif pos == -1:
                if bar["high"] >= stop:
                    results.append(entry-stop-cost_pts); pos=0; break
                if bar["low"] <= target:
                    results.append(entry-target-cost_pts); pos=0; break
        if pos != 0:
            last = rest.iloc[-1]["close"]
            results.append(((last-entry)*pos)-cost_pts)
    return np.array(results)

print("\n=== HYPOTHESIS 1: NY Opening-Range Breakout (16:00 broker, ATR stop) ===")
for rr in [1.0, 1.5, 2.0]:
    for st in [0.75, 1.0, 1.5]:
        res = backtest_orb2(atr_stop=st, rr=rr)
        if len(res)==0: continue
        wins = (res>0).mean()
        print(f"  stop={st}ATR rr={rr}: trades={len(res):3d}  "
              f"win%={wins*100:4.1f}  netPts={res.sum():+7.1f}  "
              f"avg={res.mean():+.2f}  expectancy/trade={res.mean():+.2f}pts")

# ============================================================
# HYPOTHESIS 2: Intraday momentum (EMA fast/slow) on M5 resample
# ============================================================
print("\n=== HYPOTHESIS 2: EMA crossover trend-follow (M5) ===")
df5 = df.set_index("ts").resample("5min").agg(
    open=("open","first"), high=("high","max"),
    low=("low","min"), close=("close","last"), volume=("volume","sum")
).dropna()
df5["ef"] = df5["close"].ewm(span=12).mean()
df5["es"] = df5["close"].ewm(span=48).mean()
df5["sig"] = np.where(df5["ef"]>df5["es"], 1, -1)
df5["fwd"] = df5["close"].pct_change().shift(-1)
# only count when bars are contiguous
df5["gap"] = df5.index.to_series().diff().dt.total_seconds()/60 > 6
strat_ret = (df5["sig"] * df5["fwd"]).where(~df5["gap"])
cost_per_flip = 0.0006  # 0.06% taker
flips = df5["sig"].diff().abs()/2
net = strat_ret - flips*cost_per_flip
net = net.dropna()
print(f"  M5 bars={len(df5)}  flips={int(flips.sum())}")
print(f"  Gross cum ret={strat_ret.sum()*100:+.2f}%  Net (after fees)={net.sum()*100:+.2f}%")
print(f"  Sharpe (per-bar, ann~)={net.mean()/net.std()*np.sqrt(288*252):.2f}" if net.std()>0 else "  n/a")

# ============================================================
# HYPOTHESIS 3: Session bias — does gold drift up/down in NY hours?
# ============================================================
print("\n=== HYPOTHESIS 3: Per-hour mean return (drift) ===")
hr = df.groupby("hour")["logret"].agg(["mean","count"])
hr["bps"] = hr["mean"]*1e4
hr["t_stat"] = hr["mean"]/ (df.groupby("hour")["logret"].std()/np.sqrt(hr["count"]))
print(hr[["bps","count","t_stat"]].round(3).to_string())
