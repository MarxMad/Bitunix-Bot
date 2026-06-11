"""
Mean-Reversion Core — shared signal logic
==========================================
Pure, dependency-free z-score mean-reversion engine used by BOTH the backtester
(backtest.py) and the live strategy (strategy_meanrev.py). Keeping the decision
logic in one place guarantees that what we validated is exactly what trades.

Edge (validated on 60d of real Bitunix M1 klines — see research/04_backtest_eth_xlm.md):
  XLMUSDT reverts from 3-sigma extremes. Enter LIMIT at |z| >= z_in, exit LIMIT
  near the mean (|z| <= z_exit), catastrophe stop at |z| >= z_stop, time-stop after
  max_hold_bars. Maker execution is mandatory (taker fees destroy the edge).
"""

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class MeanRevParams:
    """Validated defaults for XLMUSDT on a 5-minute timeframe."""
    lookback: int = 40          # rolling window (bars) for mean/std — 40 x M5 = ~3.3h
    z_in: float = 3.0           # enter when |z| >= this (edge lives at >=3 sigma)
    z_exit: float = 0.5         # exit when z reverts to this band around the mean
    z_stop: float = 4.5         # catastrophe stop: deviation got worse, bail out
    max_hold_bars: int = 24     # time-stop: give up after this many bars (M5 -> 2h)


# Decision constants returned by decide()
ENTER_LONG = "enter_long"     # price is oversold (z <= -z_in) -> buy the dip
ENTER_SHORT = "enter_short"   # price is overbought (z >= +z_in) -> sell the rip
EXIT = "exit"                 # reverted to the mean -> take profit (maker)
STOP = "stop"                 # deviation widened past z_stop -> protective exit
TIME_STOP = "time_stop"       # held too long without reverting -> flat (maker)
HOLD = "hold"                 # in a position, keep waiting
NONE = "none"                 # flat, no signal


def zscore(closes: Sequence[float], lookback: int) -> Optional[float]:
    """
    Z-score of the latest close vs the rolling window. Uses SAMPLE std (ddof=1)
    to match pandas .std() used during validation. Returns None if not enough
    data or zero variance.
    """
    if len(closes) < lookback:
        return None
    window = closes[-lookback:]
    mean = statistics.fmean(window)
    try:
        sd = statistics.stdev(window)  # sample std, ddof=1 (matches pandas)
    except statistics.StatisticsError:
        return None
    if sd == 0:
        return None
    return (closes[-1] - mean) / sd


def decide(z: Optional[float], position_side: Optional[str],
           bars_held: int, params: MeanRevParams) -> str:
    """
    Decide the next action from the current z-score and position state.

    position_side: "long", "short", or None (flat)
    bars_held:     bars since entry (ignored when flat)

    Pure function — no I/O, no time. Same call in backtest and live.
    """
    if z is None:
        return HOLD if position_side else NONE

    # ── Flat: look for an extreme to fade ──────────────────────────────────
    if not position_side:
        if z <= -params.z_in:
            return ENTER_LONG
        if z >= params.z_in:
            return ENTER_SHORT
        return NONE

    # ── In a position: stop / target / time-stop / hold ────────────────────
    if abs(z) >= params.z_stop:
        return STOP
    if bars_held >= params.max_hold_bars:
        return TIME_STOP
    if position_side == "long" and z >= -params.z_exit:
        return EXIT
    if position_side == "short" and z <= params.z_exit:
        return EXIT
    return HOLD
