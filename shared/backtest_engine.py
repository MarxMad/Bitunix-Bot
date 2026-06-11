"""
Shared Backtest Engine
======================
Reusable, vectorized building blocks for backtesting ANY strategy on Bitunix
klines (CSV from research/scripts/fetch_bitunix_klines.py). Keeps data loading,
resampling, cost modelling, PnL and metrics in one place so each strategy folder
only implements its *signal*.

Two evaluation styles:
  • vectorized_pnl()  — for always-in / position-series strategies (trend, momentum)
  • trade-list metrics — for event strategies (handled in each strategy's loop)
"""

import numpy as np
import pandas as pd

# Bitunix VIP0 crypto fees (fraction of notional)
MAKER = 0.0002   # 0.02% — limit orders
TAKER = 0.0006   # 0.06% — market orders

_TF_TD = {  # pandas resample alias -> timedelta for gap detection
    "1min": "1min", "3min": "3min", "5min": "5min", "15min": "15min",
    "30min": "30min", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1d",
}
_BARS_PER_YEAR = {
    "1min": 525600, "3min": 175200, "5min": 105120, "15min": 35040,
    "30min": 17520, "1h": 8760, "2h": 4380, "4h": 2190, "1d": 365,
}


def load_klines(csv_path: str) -> pd.DataFrame:
    """Load M1 CSV -> DataFrame[ts, open, high, low, close, baseVol]."""
    df = pd.read_csv(csv_path)
    df["ts"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df.sort_values("ts").reset_index(drop=True)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Aggregate to timeframe `tf` (e.g. '1h','5min'); flags gaps."""
    agg = {"open": ("open", "first"), "high": ("high", "max"),
           "low": ("low", "min"), "close": ("close", "last"),
           "baseVol": ("baseVol", "sum")}
    r = df.set_index("ts").resample(tf).agg(**agg).dropna().copy()
    step = pd.Timedelta(_TF_TD.get(tf, tf))
    r["gap"] = r.index.to_series().diff() > step * 1.5
    r.loc[r.index[0], "gap"] = False
    return r


def bars_per_year(tf: str) -> int:
    return _BARS_PER_YEAR.get(tf, 8760)


def vectorized_pnl(df: pd.DataFrame, position: pd.Series, fee: float = TAKER):
    """
    Compute net per-bar returns for a position series (values in {-1,0,+1}).
    We earn position held from the PREVIOUS bar close over this bar's return.
    Costs charged on turnover (|Δposition|) at `fee`. Gaps zero the return.

    Returns (net_returns: pd.Series, turnover: pd.Series).
    """
    ret = df["close"].pct_change().fillna(0.0)
    pos = position.reindex(df.index).fillna(0.0)
    gross = pos.shift(1).fillna(0.0) * ret
    turnover = pos.diff().abs().fillna(pos.abs())
    net = gross - turnover * fee
    if "gap" in df:
        net = net.where(~df["gap"], 0.0)
    return net, turnover


def trade_list(df: pd.DataFrame, position: pd.Series, fee: float = TAKER):
    """
    Segment a position series into discrete trades. Returns list of dicts:
    {entry_ts, exit_ts, dir, ret} where ret is net of round-trip fee.
    A trade is a maximal run of constant non-zero position.
    """
    pos = position.reindex(df.index).fillna(0.0).values
    close = df["close"].values
    ts = df.index
    trades = []
    i, n = 0, len(pos)
    while i < n:
        if pos[i] == 0:
            i += 1
            continue
        side = pos[i]
        j = i
        while j + 1 < n and pos[j + 1] == side:
            j += 1
        entry, exit_ = close[i], close[j]
        gross = (exit_ / entry - 1) * np.sign(side)
        trades.append({"entry_ts": ts[i], "exit_ts": ts[j],
                       "dir": "long" if side > 0 else "short",
                       "ret": gross - 2 * fee})
        i = j + 1
    return trades


def metrics(net: pd.Series, tf: str, n_trades: int = None, label: str = ""):
    """Print + return a metrics dict for a net-returns series."""
    eq = (1 + net).cumprod()
    peak = eq.cummax()
    maxdd = float((eq / peak - 1).min())
    total = float(eq.iloc[-1] - 1) if len(eq) else 0.0
    sd = float(net.std())
    sharpe = float(net.mean() / sd * np.sqrt(bars_per_year(tf))) if sd > 0 else 0.0
    out = {"net_pct": total * 100, "sharpe": sharpe, "maxdd_pct": maxdd * 100,
           "trades": n_trades}
    if label:
        t = f" trades={n_trades}" if n_trades is not None else ""
        print(f"  {label}: net={total*100:+7.1f}%  Sharpe~={sharpe:+.2f}  "
              f"maxDD={maxdd*100:6.1f}%{t}")
    return out


def split_time(df: pd.DataFrame, frac_train: float = 0.66):
    """Split a DataFrame chronologically into (train, test)."""
    k = int(len(df) * frac_train)
    return df.iloc[:k], df.iloc[k:]
