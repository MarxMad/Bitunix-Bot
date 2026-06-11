"""
Round 3: VALIDATION of the XLM mean-reversion edge.
- Out-of-sample split (train first half / test second half)
- Walk-forward (consecutive 15-day blocks)
- Slippage/fill-haircut sensitivity (maker fills are optimistic at 3-sigma)
Chosen robust config: z_in=3.0, z_exit=0.5, max_hold=24, lookback=40, maker.
"""
import sys, os
import pandas as pd, numpy as np

MAKER = 0.0002; TAKER = 0.0006

def load(path):
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df.sort_values("ts").reset_index(drop=True)

def to_m5(df):
    d = df.set_index("ts").resample("5min").agg(
        open=("open","first"),high=("high","max"),low=("low","min"),
        close=("close","last")).dropna()
    d["gap"] = d.index.to_series().diff().dt.total_seconds()/60 > 6
    return d

def bt(df5, lookback=40, z_in=3.0, z_exit=0.5, z_stop=4.5, max_hold=24,
       cost=MAKER, slip_bps=0.0):
    c=df5["close"]; ma=c.rolling(lookback).mean(); sd=c.rolling(lookback).std()
    z=(c-ma)/sd; gap=df5["gap"].values
    pos=0; entry=0.0; held=0; rets=[]; slip=slip_bps/1e4
    for i in range(lookback,len(df5)):
        if gap[i]: pos=0; continue
        px=c.iloc[i]; zi=z.iloc[i]
        if pos==0:
            if not np.isfinite(zi): continue
            if zi<=-z_in: pos,entry,held=1,px,0
            elif zi>=z_in: pos,entry,held=-1,px,0
        else:
            held+=1
            target=(pos==1 and zi>=z_exit) or (pos==-1 and zi<=z_exit)
            stop=abs(zi)>=z_stop; tstop=held>=max_hold
            if target or stop or tstop:
                ec = cost if (target or tstop) else TAKER
                # slippage hits both legs adversely
                rets.append((px/entry-1)*pos - cost - ec - 2*slip)
                pos=0
    return np.array(rets)

def stats(r):
    if len(r)==0: return "no trades"
    eq=np.cumprod(1+r); dd=(eq/np.maximum.accumulate(eq)-1).min()
    sh=r.mean()/r.std()*np.sqrt(len(r)) if r.std()>0 else 0
    return (f"n={len(r):4d} win={(r>0).mean()*100:4.1f}% net={r.sum()*100:+6.1f}% "
            f"avg={r.mean()*1e4:+5.1f}bps maxDD={dd*100:5.1f}% Sharpe~={sh:+.2f}")

def run(path):
    df=load(path); name=os.path.basename(path)
    df5=to_m5(df)
    n=len(df5); mid=n//2
    print("="*72); print(f"VALIDATION {name}"); print("="*72)

    print("\n[1] OUT-OF-SAMPLE SPLIT (chosen config z_in=3,z_exit=.5)")
    print(f"  TRAIN (1st half): {stats(bt(df5.iloc[:mid]))}")
    print(f"  TEST  (2nd half): {stats(bt(df5.iloc[mid:]))}")
    print(f"  FULL            : {stats(bt(df5))}")

    print("\n[2] WALK-FORWARD (consecutive ~15-day blocks)")
    block=n//4
    for k in range(4):
        seg=df5.iloc[k*block:(k+1)*block]
        d0=seg.index[0].strftime('%m-%d'); d1=seg.index[-1].strftime('%m-%d')
        print(f"  block {k+1} [{d0}->{d1}]: {stats(bt(seg))}")

    print("\n[3] SLIPPAGE SENSITIVITY (full, adverse bps added per leg)")
    for s in [0,1,2,3,5]:
        print(f"  slip={s}bps/leg: {stats(bt(df5, slip_bps=s))}")

    print("\n[4] ROBUSTNESS: nearby params (full data, slip=2bps)")
    for zin in [2.5,3.0,3.5]:
        for ze in [0.0,0.5,1.0]:
            print(f"  z_in={zin} z_exit={ze}: {stats(bt(df5,z_in=zin,z_exit=ze,slip_bps=2))}")

if __name__=="__main__":
    for p in sys.argv[1:]: run(p)
