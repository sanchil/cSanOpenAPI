"""Shared helpers for indicator series sizing and I/O."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from typing import Iterable, List, Optional, Union

# Matches IndData default capacity; keep local so pure indicators stay
# independent of the ctrader_api package.
DEFAULT_SERIES_CAPACITY = 500

SeriesIn = Union[Sequence[float], Iterable[float], deque]
SeriesOut = List[float]

NAN = float("nan")


def to_floats(data: SeriesIn) -> list[float]:
    """Normalize any sequence-like input to a list of floats (oldest → newest)."""
    return [float(x) for x in data]


def resolve_size(
    data_len: int,
    size: Optional[int] = None,
    *,
    default_capacity: int = DEFAULT_SERIES_CAPACITY,
) -> int:
    """
    Decide output length.

    - size is None  → min(data_len, default_capacity)  (fits IndData)
    - size given    → that length (left-padded with NaN if longer than data)
    """
    if size is None:
        return min(int(data_len), int(default_capacity))
    return max(0, int(size))


def finalize(values: Sequence[float], size: Optional[int]) -> SeriesOut:
    """Trim or left-pad `values` to the requested output size."""
    n = len(values)
    out_len = resolve_size(n, size)
    if out_len == 0:
        return []
    if n >= out_len:
        return list(values[-out_len:])
    return [NAN] * (out_len - n) + list(values)


def write_series(target: deque, values: Sequence[float]) -> None:
    """Replace a rolling deque series with new values (respects maxlen)."""
    target.clear()
    target.extend(float(v) for v in values)


def is_ready(value: float) -> bool:
    """True if value is a usable finite number (not NaN/inf)."""
    return value == value and math.isfinite(value)


def true_range(high: float, low: float, prev_close: float) -> float:
    """Classic True Range for one bar."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def wilder_smooth(values: Sequence[float], period: int) -> list[float]:
    """
    Wilder's smoothing (used by ATR / ADX).

    First non-NaN seed = SMA of the first `period` values starting at the first
    fully available window; subsequent:
        s[i] = (s[i-1] * (period - 1) + values[i]) / period
    Positions before the first full window are NaN.
    """
    n = len(values)
    out = [NAN] * n
    if period <= 0 or n < period:
        return out

    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out
