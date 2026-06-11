"""
Momentum Multi-Timeframe — signal core
======================================
Direction is gated by a HIGH timeframe (macro), entries timed on a LOW timeframe
(micro). Fewer, higher-quality trades → less fee drag and less chop than a
single-timeframe EMA cross.

No lookahead: the macro signal is shifted by one macro bar before being aligned
onto the micro index (we only use a macro bar after it has closed).
"""

from dataclasses import dataclass
import numpy as np
import pandas as pd

from shared.backtest_engine import resample


@dataclass
class MTFParams:
    micro_tf: str = "1h"     # entry timeframe
    macro_tf: str = "4h"     # regime timeframe
    macro_ema: int = 100     # macro trend = close vs this EMA (on macro_tf)
    micro_fast: int = 12     # micro entry EMA cross
    micro_slow: int = 50
    allow_short: bool = True


def build(df_raw: pd.DataFrame, p: MTFParams):
    """Return (micro_df, position_series) with macro-gated micro momentum.

    position is +1 only when macro is bullish AND micro crosses up; -1 only when
    macro bearish AND micro crosses down; else 0 (flat / no conviction).
    """
    micro = resample(df_raw, p.micro_tf)
    macro = resample(df_raw, p.macro_tf)

    # macro regime: +1 if macro close above its EMA, else -1 (shifted = no lookahead)
    mc = macro["close"]
    macro_trend = np.sign(mc - mc.ewm(span=p.macro_ema, adjust=False).mean())
    macro_trend = macro_trend.shift(1)                      # use only closed macro bars
    macro_on_micro = macro_trend.reindex(micro.index, method="ffill").fillna(0.0)

    # micro momentum: EMA cross on the entry timeframe
    c = micro["close"]
    micro_sig = np.sign(c.ewm(span=p.micro_fast, adjust=False).mean()
                        - c.ewm(span=p.micro_slow, adjust=False).mean())

    # take the micro side only when it agrees with the macro regime
    pos = micro_sig.where(micro_sig.values == macro_on_micro.values, 0.0)
    pos = pd.Series(pos, index=micro.index).fillna(0.0)
    if not p.allow_short:
        pos[pos < 0] = 0.0
    return micro, pos
