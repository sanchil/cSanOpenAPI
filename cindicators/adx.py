"""
Average Directional Index (ADX) with DI+ and DI- (Wilder).

Takes raw high / low / close series and returns three series of length `size`
(default: min(len(close), IndData capacity)):

  adx, di_plus, di_minus = adx(high, low, close, period=14, size=...)

Or as a dict via adx_bundle(...).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ._utils import NAN, SeriesIn, SeriesOut, finalize, to_floats, true_range, wilder_smooth


def _directional_moves(
    high: list[float],
    low: list[float],
) -> Tuple[list[float], list[float]]:
    """+DM / -DM series (index 0 = 0)."""
    n = min(len(high), len(low))
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down
    return plus_dm, minus_dm


def adx(
    high: SeriesIn,
    low: SeriesIn,
    close: SeriesIn,
    period: int = 14,
    size: Optional[int] = None,
) -> Tuple[SeriesOut, SeriesOut, SeriesOut]:
    """
    Compute ADX, DI+, DI- (Wilder).

    Returns
    -------
    (adx_series, di_plus_series, di_minus_series)
        Each of length `size` (or IndData-fittable default).
    """
    if period <= 0:
        raise ValueError("period must be >= 1")

    h = to_floats(high)
    l = to_floats(low)
    c = to_floats(close)
    n = min(len(h), len(l), len(c))
    if n == 0:
        empty = finalize([], size)
        return empty, empty[:], empty[:]

    h, l, c = h[:n], l[:n], c[:n]

    # True range
    tr = [0.0] * n
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = true_range(h[i], l[i], c[i - 1])

    plus_dm, minus_dm = _directional_moves(h, l)

    # Wilder smooth TR / +DM / -DM
    atr_s = wilder_smooth(tr, period)
    plus_s = wilder_smooth(plus_dm, period)
    minus_s = wilder_smooth(minus_dm, period)

    di_plus = [NAN] * n
    di_minus = [NAN] * n
    dx = [NAN] * n

    for i in range(n):
        atr_i = atr_s[i]
        if atr_i != atr_i or atr_i == 0.0:  # NaN or zero
            continue
        di_p = 100.0 * plus_s[i] / atr_i
        di_m = 100.0 * minus_s[i] / atr_i
        di_plus[i] = di_p
        di_minus[i] = di_m
        denom = di_p + di_m
        if denom != 0.0:
            dx[i] = 100.0 * abs(di_p - di_m) / denom
        else:
            dx[i] = 0.0

    # ADX = Wilder smooth of DX, but only over valid DX values.
    # Standard approach: first ADX at index (2*period - 1) = SMA of first
    # `period` DX values that start once DI is available (index period-1).
    adx_full = [NAN] * n
    # DX becomes available at period-1; need `period` DX values → index 2*period-2
    first_dx = period - 1
    first_adx = first_dx + period - 1  # 2*period - 2
    if n > first_adx and first_dx >= 0:
        # Collect contiguous DX from first_dx
        seed_vals = dx[first_dx : first_dx + period]
        if all(v == v for v in seed_vals):  # all finite
            adx_full[first_adx] = sum(seed_vals) / period
            for i in range(first_adx + 1, n):
                if dx[i] != dx[i]:
                    adx_full[i] = adx_full[i - 1]
                else:
                    adx_full[i] = (adx_full[i - 1] * (period - 1) + dx[i]) / period

    return (
        finalize(adx_full, size),
        finalize(di_plus, size),
        finalize(di_minus, size),
    )


def adx_bundle(
    high: SeriesIn,
    low: SeriesIn,
    close: SeriesIn,
    period: int = 14,
    size: Optional[int] = None,
) -> Dict[str, SeriesOut]:
    """Same as adx() but returns a named dict: adx, di_plus, di_minus."""
    a, p, m = adx(high, low, close, period=period, size=size)
    return {"adx": a, "di_plus": p, "di_minus": m, "adx_plus": p, "adx_minus": m}


# Aliases
average_directional_index = adx
di_plus = lambda high, low, close, period=14, size=None: adx(high, low, close, period, size)[1]
di_minus = lambda high, low, close, period=14, size=None: adx(high, low, close, period, size)[2]
