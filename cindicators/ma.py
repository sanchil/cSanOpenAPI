"""
Moving Average indicators: SMA, EMA, WMA, and a generic `ma` dispatcher.

All functions take a raw price series (oldest → newest) and return a series of
indicator values of length `size` (default: min(len(data), IndData capacity)).
"""

from __future__ import annotations

import math
from typing import Optional

from ._utils import NAN, SeriesIn, SeriesOut, finalize, resolve_size, to_floats


def sma(
    data: SeriesIn,
    period: int = 14,
    size: Optional[int] = None,
) -> SeriesOut:
    """Simple Moving Average.

    value[i] = mean(data[i-period+1 : i+1]) for i >= period-1, else NaN.
    """
    if period <= 0:
        raise ValueError("period must be >= 1")

    src = to_floats(data)
    n = len(src)
    full = [NAN] * n
    if n < period:
        return finalize(full, size)

    window_sum = sum(src[:period])
    full[period - 1] = window_sum / period
    for i in range(period, n):
        window_sum += src[i] - src[i - period]
        full[i] = window_sum / period
    return finalize(full, size)


def ema(
    data: SeriesIn,
    period: int = 14,
    size: Optional[int] = None,
) -> SeriesOut:
    """Exponential Moving Average (seeded with SMA of the first `period` bars).

    k = 2 / (period + 1)
    ema[i] = price[i] * k + ema[i-1] * (1 - k)
    """
    if period <= 0:
        raise ValueError("period must be >= 1")

    src = to_floats(data)
    n = len(src)
    full = [NAN] * n
    if n < period:
        return finalize(full, size)

    k = 2.0 / (period + 1.0)
    seed = sum(src[:period]) / period
    full[period - 1] = seed
    for i in range(period, n):
        full[i] = src[i] * k + full[i - 1] * (1.0 - k)
    return finalize(full, size)


def wma(
    data: SeriesIn,
    period: int = 14,
    size: Optional[int] = None,
) -> SeriesOut:
    """Linear Weighted Moving Average (most recent bar has highest weight)."""
    if period <= 0:
        raise ValueError("period must be >= 1")

    src = to_floats(data)
    n = len(src)
    full = [NAN] * n
    if n < period:
        return finalize(full, size)

    weight_sum = period * (period + 1) / 2.0
    for i in range(period - 1, n):
        acc = 0.0
        for w in range(1, period + 1):
            acc += src[i - period + w] * w
        full[i] = acc / weight_sum
    return finalize(full, size)


def ma(
    data: SeriesIn,
    period: int = 14,
    size: Optional[int] = None,
    method: str = "sma",
) -> SeriesOut:
    """Generic moving average dispatcher.

    method: 'sma' | 'ema' | 'wma' (case-insensitive)
    """
    m = (method or "sma").strip().lower()
    if m in ("sma", "simple", "s"):
        return sma(data, period, size)
    if m in ("ema", "exponential", "e", "exp"):
        return ema(data, period, size)
    if m in ("wma", "weighted", "w", "lwma"):
        return wma(data, period, size)
    raise ValueError(f"Unknown MA method: {method!r} (use sma|ema|wma)")


# Convenience aliases matching common TA naming
simple_moving_average = sma
exponential_moving_average = ema
weighted_moving_average = wma
moving_average = ma


def last_value(series: SeriesOut) -> float:
    """Most recent finite value, or NaN if none."""
    for v in reversed(series):
        if v == v and math.isfinite(v):
            return v
    return NAN


def default_output_size(data: SeriesIn) -> int:
    """Size that fits IndData when caller omits `size`."""
    return resolve_size(len(list(data)) if not hasattr(data, "__len__") else len(data), None)
