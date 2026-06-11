"""
Quantitative exploratory analysis of XAUUSD1.csv (1-minute bars).
Outputs: timeframe, range, date coverage, gaps, bar count,
hourly volume/volatility profile, return distribution, ATR.
"""
import pandas as pd
import numpy as np

COLS = ["date", "time", "open", "high", "low", "close", "volume"]
df = pd.read_csv("XAUUSD1.csv", header=None, names=COLS)

# Build a proper timestamp
df["ts"] = pd.to_datetime(df["date"] + " " + df["time"], format="%Y.%m.%d %H:%M")
df = df.sort_values("ts").reset_index(drop=True)

print("=" * 70)
print("1. BASIC STRUCTURE")
print("=" * 70)
print(f"Total bars        : {len(df):,}")
print(f"First bar         : {df['ts'].iloc[0]}")
print(f"Last bar          : {df['ts'].iloc[-1]}")
print(f"Price range       : {df['low'].min():.2f}  ->  {df['high'].max():.2f}")
print(f"First close       : {df['close'].iloc[0]:.2f}")
print(f"Last close        : {df['close'].iloc[-1]:.2f}")

# Timeframe detection via modal delta
deltas = df["ts"].diff().dropna()
modal = deltas.mode().iloc[0]
print(f"Modal bar interval: {modal}  (=> timeframe)")

print("\n" + "=" * 70)
print("2. TIME COVERAGE & GAPS")
print("=" * 70)
span = df["ts"].iloc[-1] - df["ts"].iloc[0]
print(f"Calendar span     : {span}")
expected_if_cont = int(span.total_seconds() / 60) + 1
print(f"Bars if continuous M1: {expected_if_cont:,}  | actual: {len(df):,}  "
      f"=> {len(df)/expected_if_cont*100:.1f}% coverage")

# Big gaps (> 1 day)
gaps = deltas[deltas > pd.Timedelta(days=1)]
print(f"\nGaps > 1 day: {len(gaps)}")
for idx in gaps.index:
    print(f"  {df['ts'].iloc[idx-1]}  ->  {df['ts'].iloc[idx]}   gap={deltas.loc[idx]}")

# Distinct calendar months present
df["ym"] = df["ts"].dt.to_period("M")
print("\nBars per month:")
print(df.groupby("ym").size().to_string())

print("\n" + "=" * 70)
print("3. HOURLY PROFILE (volume + volatility)  [timestamps as-is in file]")
print("=" * 70)
df["hour"] = df["ts"].dt.hour
df["range_pts"] = df["high"] - df["low"]
df["ret_bps"] = (df["close"] / df["open"] - 1) * 1e4  # basis points per minute

hourly = df.groupby("hour").agg(
    bars=("close", "size"),
    avg_vol=("volume", "mean"),
    total_vol=("volume", "sum"),
    avg_range_pts=("range_pts", "mean"),
    abs_ret_bps=("ret_bps", lambda x: x.abs().mean()),
)
hourly["vol_share_%"] = hourly["total_vol"] / hourly["total_vol"].sum() * 100
print(hourly.round(2).to_string())

top_vol_hour = hourly["avg_vol"].idxmax()
top_volat_hour = hourly["avg_range_pts"].idxmax()
print(f"\n>> Hour with HIGHEST avg volume     : {top_vol_hour:02d}:00  "
      f"(avg vol {hourly['avg_vol'].max():.1f})")
print(f">> Hour with HIGHEST avg volatility : {top_volat_hour:02d}:00  "
      f"(avg range {hourly['avg_range_pts'].max():.2f} pts)")

print("\n" + "=" * 70)
print("4. RETURN / VOLATILITY STATISTICS (per minute, on close-to-close)")
print("=" * 70)
df["logret"] = np.log(df["close"] / df["close"].shift(1))
r = df["logret"].dropna()
print(f"Mean ret/min      : {r.mean()*1e4:.4f} bps")
print(f"Std  ret/min      : {r.std()*1e4:.2f} bps")
print(f"Annualized vol    : {r.std()*np.sqrt(60*24*252)*100:.1f}%  (approx)")
print(f"Skew              : {r.skew():.3f}")
print(f"Kurtosis (excess) : {r.kurt():.2f}")
print(f"Max 1-min up      : {r.max()*1e4:.1f} bps")
print(f"Max 1-min down    : {r.min()*1e4:.1f} bps")

# ATR(14) on the natural bar
tr = pd.concat([
    df["high"] - df["low"],
    (df["high"] - df["close"].shift()).abs(),
    (df["low"] - df["close"].shift()).abs(),
], axis=1).max(axis=1)
atr14 = tr.rolling(14).mean()
print(f"\nATR(14) median    : {atr14.median():.2f} pts  "
      f"(= {atr14.median()/df['close'].median()*1e4:.1f} bps of price)")
print(f"ATR(14) p90       : {atr14.quantile(0.9):.2f} pts")

# Simple autocorrelation (trend vs mean-reversion signature)
print("\n" + "=" * 70)
print("5. MARKET CHARACTER (autocorrelation of returns)")
print("=" * 70)
for lag in [1, 5, 15, 60]:
    ac = r.autocorr(lag=lag)
    sign = "momentum/trend" if ac > 0 else "mean-reversion"
    print(f"  lag {lag:>3} min: autocorr = {ac:+.4f}  ({sign})")

# Variance ratio (Lo-MacKinlay style, quick): VR>1 trending, <1 mean-reverting
def variance_ratio(series, q):
    s = series.dropna().values
    n = len(s)
    mu = s.mean()
    var1 = ((s - mu) ** 2).sum() / n
    agg = np.add.reduceat(s, np.arange(0, n - n % q, q))
    var_q = ((agg - q * mu) ** 2).sum() / (len(agg)) / q
    return var_q / var1

for q in [5, 15, 30, 60]:
    vr = variance_ratio(r, q)
    print(f"  VR({q:>2}) = {vr:.3f}  ({'trending' if vr>1.05 else 'mean-reverting' if vr<0.95 else 'random-walk'})")
