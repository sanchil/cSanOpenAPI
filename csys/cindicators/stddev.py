"""
Rolling Standard Deviation for arbitrary series (close, open, MAs, …).

Takes a raw series and returns std-dev values of length `size`
(default: min(len(data), IndData capacity)).
"""

from __future__ import annotations

import math
from typing import Optional

from ._utils import NAN, SeriesIn, SeriesOut, finalize, to_floats
from .ma import sma


def stddev(
    data: SeriesIn,
    period: int = 20,
    size: Optional[int] = None,
    *,
    ddof: int = 0,
    ma_method: str = "sma",
) -> SeriesOut:
    """
    Rolling standard deviation.

    Parameters
    ----------
    data :
        Price or indicator series (oldest → newest).
    period :
        Lookback window.
    size :
        Output length; default fits IndData.
    ddof :
        Delta degrees of freedom. 0 = population (TA / Bollinger default),
        1 = sample stddev.
    ma_method :
        Centre of the deviations. 'sma' = window mean (classic stddev).
        Any other ma() method reproduces MQL4 iStdDev(..., ma_method, ...):
          std[i] = sqrt( sum_{k<period} (x[i-k] - MA[i])^2 / (period - ddof) )
        where MA[i] is that method's moving average at bar i.
    """
    if period <= 0:
        raise ValueError("period must be >= 1")
    if ddof < 0 or ddof >= period:
        raise ValueError(f"ddof must be in [0, period); got {ddof}")

    src = to_floats(data)
    n = len(src)
    full = [NAN] * n
    if n < period:
        return finalize(full, size)

    if (ma_method or "sma").strip().lower() not in ("sma", "simple", "s"):
        from .ma import ma

        centre = ma(src, period=period, size=n, method=ma_method)
        denom = period - ddof
        for i in range(period - 1, n):
            m = centre[i]
            if m != m:  # MA not ready
                continue
            acc = 0.0
            for k in range(i - period + 1, i + 1):
                d = src[k] - m
                acc += d * d
            full[i] = math.sqrt(acc / denom)
        return finalize(full, size)

    # Incremental Welford-friendly approach via rolling sum / sumsq
    window_sum = sum(src[:period])
    window_sumsq = sum(x * x for x in src[:period])
    denom = period - ddof

    def _std(s: float, ssq: float) -> float:
        # max(0, …) guards tiny negative from float error
        var = max(0.0, (ssq - (s * s) / period) / denom)
        return math.sqrt(var)

    full[period - 1] = _std(window_sum, window_sumsq)
    for i in range(period, n):
        old = src[i - period]
        new = src[i]
        window_sum += new - old
        window_sumsq += new * new - old * old
        full[i] = _std(window_sum, window_sumsq)

    return finalize(full, size)


def stddev_of_ma(
    data: SeriesIn,
    ma_period: int = 14,
    std_period: int = 20,
    size: Optional[int] = None,
    *,
    ma_method: str = "sma",
    ddof: int = 0,
) -> SeriesOut:
    """
    StdDev of a moving-average series of `data`.

    Useful for volatility-of-trend: first MA(data), then rolling StdDev of that MA.
    Warm-up NaNs from the MA propagate into the stddev window naturally.
    """
    from .ma import ma

    # Explicit sizes: size=None would cap at DEFAULT_SERIES_CAPACITY (500)
    src = to_floats(data)
    ma_series = ma(src, period=ma_period, size=len(src), method=ma_method)
    # Drop leading NaNs for stddev math? Keep alignment — stddev treats NaN poorly.
    # Replace leading NaNs with forward-fill of first finite would bias; keep
    # length-aligned by only computing where MA is finite via a cleaned series.
    cleaned: list[float] = []
    last = NAN
    for v in ma_series:
        if v == v:  # finite / not NaN
            last = v
            cleaned.append(v)
        else:
            # Leave a sentinel: use last known MA so window stays dense after warm-up,
            # but mark pre-warm as NaN in final by running stddev on raw and then
            # nulling positions where MA was NaN.
            cleaned.append(last if last == last else 0.0)

    raw_std = stddev(cleaned, period=std_period, size=len(cleaned), ddof=ddof)
    # Null out bars where MA was not ready
    for i, v in enumerate(ma_series):
        if v != v:
            raw_std[i] = NAN
    return finalize(raw_std, size)


def avg_std(
    series_a: SeriesIn,
    series_b: SeriesIn,
    size: Optional[int] = None,
) -> SeriesOut:
    """Element-wise average of two stddev (or any) series, aligned from the right."""
    a = to_floats(series_a)
    b = to_floats(series_b)
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    out: list[float] = []
    for x, y in zip(a, b):
        if x != x or y != y:
            out.append(NAN)
        else:
            out.append(0.5 * (x + y))
    return finalize(out, size)


# Aliases
standard_deviation = stddev
rolling_std = stddev
