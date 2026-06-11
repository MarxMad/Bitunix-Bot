"""
Exploratory quant analysis + hypothesis backtests on REAL Bitunix klines.
Works on the CSVs produced by fetch_bitunix_klines.py.

Usage: python3 analyze_bitunix.py ETHUSDT_1m.csv
"""
import sys, os
import pandas as pd
import numpy as np

# Bitunix VIP0 crypto fees
TAKER = 0.0006   # 0.06%
MAKER = 0.0002   # 0.02%

def load(path):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    return df

def main(path):
    df = load(path)
    name = os.path.basename(path)
    print("="*70); print(f"ANALYSIS: {name}"); print("="*70)
    print(f"Bars: {len(df):,} | {df['ts'].iloc[0]} -> {df['ts'].iloc[-1]}")
    print(f"Span: {(df['ts'].iloc[-1]-df['ts'].iloc[0]).days} days")
    print(f"Price: {df['low'].min():.6g} -> {df['high'].max():.6g} | last {df['close'].iloc[-1]:.6g}")

    # continuity / coverage
    d = df["ts"].diff().dt.total_seconds()/60
    print(f"Modal interval: {d.mode().iloc[0]:.0f} min | gaps>5min: {(d>5).sum()}")

    # returns (mask gaps)
    df["lr"] = np.log(df["close"]/df["close"].shift(1))
    df.loc[d>5, "lr"] = np.nan
    r = df["lr"].dropna()
    print(f"\n--- RETURN STATS (per min) ---")
    print(f"std={r.std()*1e4:.2f}bps | ann vol={r.std()*np.sqrt(60*24*365)*100:.0f}% "
          f"| skew={r.skew():+.2f} | kurt={r.kurt():.0f}")
    for lag in [1,5,15,60]:
        print(f"  autocorr lag{lag:>3}: {r.autocorr(lag):+.4f}")
    def vr(s,q):
        s=s.dropna().values; n=len(s); mu=s.mean()
        v1=((s-mu)**2).sum()/n
        agg=np.add.reduceat(s,np.arange(0,n-n%q,q))
        vq=((agg-q*mu)**2).sum()/len(agg)/q
        return vq/v1
    print("  VR:", {q: round(vr(r,q),3) for q in [5,15,30,60]},
          "(>1 trend, <1 mean-revert)")

    # hourly profile (UTC)
    df["hour"]=df["ts"].dt.hour
    df["rng"]=df["high"]-df["low"]
    h=df.groupby("hour").agg(vol=("baseVol","mean"),rng=("rng","mean"),
                             ret=("lr","mean"),n=("lr","count"))
    h["ret_bps"]=h["ret"]*1e4
    h["t"]=df.groupby("hour")["lr"].mean()/(df.groupby("hour")["lr"].std()/np.sqrt(h["n"]))
    print(f"\n--- HOURLY (UTC): peak vol={h['vol'].idxmax()}h peak volat={h['rng'].idxmax()}h ---")
    sig=h[h["t"].abs()>1.8]
    if len(sig): print("  hours with |t|>1.8 drift:",
                       {int(i):round(row["ret_bps"],3) for i,row in sig.iterrows()})
    else: print("  no hour with significant drift (|t|>1.8)")

    # ATR
    tr=pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),
                  (df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
    df["atr"]=tr.rolling(14).mean()
    print(f"ATR(14) median={df['atr'].median():.6g} "
          f"({df['atr'].median()/df['close'].median()*1e4:.1f} bps of price)")

    # ---- HYPOTHESIS BACKTESTS (M5 resample) ----
    df5=df.set_index("ts").resample("5min").agg(
        open=("open","first"),high=("high","max"),low=("low","min"),
        close=("close","last"),baseVol=("baseVol","sum")).dropna()
    df5["gap"]=df5.index.to_series().diff().dt.total_seconds()/60>6

    print("\n--- H1: EMA(12/48) trend-follow M5 (cost per flip = taker) ---")
    ef=df5["close"].ewm(span=12).mean(); es=df5["close"].ewm(span=48).mean()
    sig=np.where(ef>es,1,-1)
    fwd=df5["close"].pct_change().shift(-1)
    sret=(sig*fwd); sret[df5["gap"].values]=np.nan
    flips=pd.Series(sig,index=df5.index).diff().abs()/2
    net=(sret-flips*TAKER).dropna()
    print(f"  flips={int(flips.sum())} gross={sret.sum()*100:+.1f}% net={net.sum()*100:+.1f}%")

    print("--- H2: Z-score mean-reversion M5 (Z_in=2, exit Z<0.3, stop Z>4) ---")
    for zin in [2.0,2.5]:
        c=df5["close"];ma=c.rolling(20).mean();sd=c.rolling(20).std();z=(c-ma)/sd
        pos=0;entry=0;rets=[]
        for i in range(20,len(df5)):
            if df5["gap"].iloc[i]:pos=0;continue
            zi=z.iloc[i];px=c.iloc[i]
            if pos==0 and np.isfinite(zi):
                if zi<=-zin:pos,entry=1,px
                elif zi>=zin:pos,entry=-1,px
            elif pos!=0 and (abs(zi)<=0.3 or abs(zi)>=4):
                rets.append((px/entry-1)*pos-TAKER*2);pos=0
        rets=np.array(rets)
        if len(rets):print(f"  Z_in={zin}: trades={len(rets)} win={ (rets>0).mean()*100:.0f}% "
                           f"net={rets.sum()*100:+.1f}% avg={rets.mean()*100:+.3f}%")

    print("--- H3: Donchian(20) breakout M5, ATR stop 1.5x, exit opposite ---")
    hh=df5["high"].rolling(20).max().shift();ll=df5["low"].rolling(20).min().shift()
    pos=0;entry=0;rets=[]
    atr5=pd.concat([df5["high"]-df5["low"],(df5["high"]-df5["close"].shift()).abs(),
                    (df5["low"]-df5["close"].shift()).abs()],axis=1).max(axis=1).rolling(14).mean()
    for i in range(20,len(df5)):
        if df5["gap"].iloc[i]:pos=0;continue
        px=df5["close"].iloc[i]
        if pos==0:
            if px>hh.iloc[i]:pos,entry=1,px
            elif px<ll.iloc[i]:pos,entry=-1,px
        elif pos==1 and px<ll.iloc[i]:
            rets.append((px/entry-1)-TAKER*2);pos=0
        elif pos==-1 and px>hh.iloc[i]:
            rets.append(-(px/entry-1)-TAKER*2);pos=0
    rets=np.array(rets)
    if len(rets):print(f"  trades={len(rets)} win={(rets>0).mean()*100:.0f}% "
                       f"net={rets.sum()*100:+.1f}% avg={rets.mean()*100:+.3f}%")

    print(f"--- CONTEXT: buy&hold = {(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.1f}% ---")

if __name__=="__main__":
    main(sys.argv[1])
