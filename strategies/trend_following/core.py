"""
Trend-Following — signal core
==============================
Dual-EMA crossover position signal (vectorized). Optional neutral band to sit
in cash during chop (anti-whipsaw) and a long-only switch.

Position convention: +1 long, -1 short, 0 flat. The backtest engine earns the
position held from the previous bar's close.
"""

from dataclasses import dataclass
import pandas as pd


@dataclass
class TrendParams:
    tf: str = "1h"          # timeframe (pandas alias)
    fast: int = 12          # fast EMA span (bars)
    slow: int = 48          # slow EMA span (bars)
    min_sep: float = 0.0    # neutral band: |fast-slow|/slow must exceed this to take a side
    allow_short: bool = True


def compute_position(df: pd.DataFrame, p: TrendParams) -> pd.Series:
    """Return a position series in {-1,0,+1} from the EMA cross with hysteresis.

    With min_sep>0 we require the EMAs to be separated by at least that fraction
    before flipping, and we HOLD the prior position inside the band (hysteresis)
    rather than going flat on every wiggle — this cuts whipsaw without sitting out.
    """
    c = df["close"]
    ef = c.ewm(span=p.fast, adjust=False).mean()
    es = c.ewm(span=p.slow, adjust=False).mean()
    sep = (ef - es) / es

    raw = pd.Series(0.0, index=df.index)
    raw[sep > p.min_sep] = 1.0
    raw[sep < -p.min_sep] = -1.0

    if p.min_sep > 0:
        # hysteresis: inside the band (raw==0) keep the previous decided side
        raw = raw.replace(0.0, pd.NA).ffill().fillna(0.0).astype(float)

    if not p.allow_short:
        raw[raw < 0] = 0.0
    return raw


def vol_target(df: pd.DataFrame, pos: pd.Series, target: float = 0.008,
               lookback: int = 72, max_lev: float = 2.0) -> pd.Series:
    """Scale a ±1/0 position by (target / realized per-bar vol), capped at max_lev.

    Volatility targeting: shrink exposure when the market is wild, grow it when
    calm. Directly reduces drawdown and smooths the equity curve without touching
    the entry/exit signal. Returns a continuous position series.
    """
    rv = df["close"].pct_change().rolling(lookback).std()
    scale = (target / rv).clip(upper=max_lev).fillna(0.0)
    return pos * scale
