"""
cindicators — pure-Python TA indicators for IndData snapshots.

Design
------
* Each module takes **raw** series (list / deque / sequence) and returns a
  list of indicator values of length ``size`` (default fits IndData capacity).
* Indicators are intended to be computed **once per cycle** (on_bar / decision
  tick) via :func:`compute_indicators`, which writes into an :class:`IndData`
  snapshot. Downstream logic should read that snapshot, not re-call these
  modules multiple times within the same cycle.

Modules
-------
* :mod:`ma`      — SMA, EMA, WMA
* :mod:`atr`     — Average True Range (Wilder)
* :mod:`adx`     — ADX, DI+, DI-
* :mod:`stddev`  — rolling StdDev (prices or MA series)
* :mod:`snapshot`— once-per-cycle fill of IndData
"""

from .adx import adx, adx_bundle, average_directional_index
from .atr import atr, average_true_range, true_ranges
from .ma import (
    ema,
    exponential_moving_average,
    ma,
    moving_average,
    simple_moving_average,
    sma,
    weighted_moving_average,
    wma,
)
from .snapshot import (
    DEFAULT_ADX_PERIOD,
    DEFAULT_ATR_PERIOD,
    DEFAULT_STD_PERIOD,
    IMA_PERIODS,
    compute_indicators,
    compute_ma_series,
    compute_std_of_ma,
    default_size,
)
from .stddev import avg_std, rolling_std, standard_deviation, stddev, stddev_of_ma

__all__ = [
    # MA
    "sma",
    "ema",
    "wma",
    "ma",
    "simple_moving_average",
    "exponential_moving_average",
    "weighted_moving_average",
    "moving_average",
    # ATR
    "atr",
    "average_true_range",
    "true_ranges",
    # ADX
    "adx",
    "adx_bundle",
    "average_directional_index",
    # StdDev
    "stddev",
    "standard_deviation",
    "rolling_std",
    "stddev_of_ma",
    "avg_std",
    # Snapshot / IndData
    "compute_indicators",
    "compute_ma_series",
    "compute_std_of_ma",
    "default_size",
    "IMA_PERIODS",
    "DEFAULT_ATR_PERIOD",
    "DEFAULT_ADX_PERIOD",
    "DEFAULT_STD_PERIOD",
]
