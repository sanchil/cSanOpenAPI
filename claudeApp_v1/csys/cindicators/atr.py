"""
Average True Range (ATR) — Wilder's smoothing (standard TA) or simple mean
of TR (MQL4 iATR).

Takes raw high / low / close series and returns an ATR series of length `size`
(default: min(len(close), IndData capacity)).
"""

from __future__ import annotations

from typing import Optional

from ._utils import NAN, SeriesIn, SeriesOut, finalize, to_floats, true_range, wilder_smooth
from .ma import sma


def true_ranges(
    high: SeriesIn,
    low: SeriesIn,
    close: SeriesIn,
) -> SeriesOut:
    """Full True Range series (oldest → newest). First bar = high - low."""
    h = to_floats(high)
    l = to_floats(low)
    c = to_floats(close)
    n = min(len(h), len(l), len(c))
    if n == 0:
        return []

    out = [NAN] * n
    out[0] = h[0] - l[0]
    for i in range(1, n):
        out[i] = true_range(h[i], l[i], c[i - 1])
    return out


def atr(
    high: SeriesIn,
    low: SeriesIn,
    close: SeriesIn,
    period: int = 14,
    size: Optional[int] = None,
    *,
    method: str = "wilder",
) -> SeriesOut:
    """
    Average True Range.

    TR[0] = high[0] - low[0]
    TR[i] = max(H-L, |H-prevC|, |L-prevC|)

    method:
      'wilder' — seed = SMA(TR, period), then Wilder smooth (standard TA)
      'sma'    — rolling simple mean of TR (MQL4 built-in iATR)
    """
    if period <= 0:
        raise ValueError("period must be >= 1")

    tr = true_ranges(high, low, close)
    if not tr:
        return finalize([], size)

    m = (method or "wilder").strip().lower()
    if m == "wilder":
        # Wilder smooth over TR; first value is TR[0] which is valid
        return finalize(wilder_smooth(tr, period), size)
    if m == "sma":
        return finalize(sma(tr, period, size=len(tr)), size)
    raise ValueError(f"Unknown ATR method: {method!r} (use wilder|sma)")


# Alias
average_true_range = atr
