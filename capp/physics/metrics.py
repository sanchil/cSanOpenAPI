"""
Series-level physics metrics (MarketMetrics-style building blocks).

First metric (V1-T24): determinant of the 2×2 sample covariance matrix of two
series over the previous ``n`` periods.

V1-T28: local extrema (``find_extrema`` NumPy / ``find_extrema_scipy``
find_peaks) — peak/trough building block for a future support/resistance
(higher-highs / lower-lows) signal. Used by SanSignals.peak_trough_sig (V1-T29).

Series convention
-----------------
Inputs are **oldest → newest** (IndData / project standard). The analysis
window is the last ``n`` samples. Order within the window does not affect
variance or covariance. Task text that indexes newest-first (a1 at t1) maps
to the same window numerically.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import numpy as np

from capp.physics.math import covariance_matrix_2x2, matrix_determinant

ArrayLike = Union[Sequence[float], np.ndarray]


def covariance_matrix_determinant(
    series_a: ArrayLike,
    series_b: ArrayLike,
    n: int,
    *,
    ddof: int = 1,
) -> float:
    """
    Determinant of the 2×2 covariance matrix of ``series_a`` and ``series_b``
    over the last ``n`` paired samples.

        det([[var(A), cov(A,B)],
             [cov(A,B), var(B)]])

    Intended to be called once per completed bar (each period uses the previous
    ``n`` periods). Returns a single float (generalized variance).

    Parameters
    ----------
    series_a, series_b :
        Paired float series, oldest → newest.
    n :
        Window length (number of previous periods).
    ddof :
        Delta degrees of freedom for covariance (default 1 = sample).

    Returns
    -------
    float
        Determinant of the sample covariance matrix, or ``nan`` if the window
        is too short or contains non-finite values.
    """
    if n < 2 or ddof < 0 or n <= ddof:
        return float("nan")

    a = np.asarray(series_a, dtype=float)
    b = np.asarray(series_b, dtype=float)

    if a.ndim != 1 or b.ndim != 1:
        raise ValueError("series_a and series_b must be 1-D")
    if a.shape[0] != b.shape[0]:
        raise ValueError(
            f"series length mismatch: {a.shape[0]} != {b.shape[0]}"
        )
    if a.shape[0] < n:
        return float("nan")

    a_win = a[-n:]
    b_win = b[-n:]
    if not (np.isfinite(a_win).all() and np.isfinite(b_win).all()):
        return float("nan")

    cov = covariance_matrix_2x2(a_win, b_win, ddof=ddof)
    return matrix_determinant(cov)


# ---------------------------------------------------------------------------
# V1-T28: local extrema (peaks / troughs) — support/resistance building block
# ---------------------------------------------------------------------------
#
# Two interchangeable backends with the same return shape
# ``(maxima, minima)``, each a list of ``(index, value)`` sorted by index:
#
#   find_extrema        NumPy, strict ``order``-neighbour dominance
#   find_extrema_scipy  scipy.signal.find_peaks (prominence / distance / width)
#
# Index is into the input series (oldest → newest). Plateaus are reported at
# their leftmost bar in both backends. With ``pip_size`` the value is
# ``(price - series[0]) / pip_size``: pips relative to the window's first bar,
# so differences between extrema are pips, but absolute values shift as a
# rolling window slides — compare extrema within one call only.
#
# Live use: an extremum at index i is only confirmed once lower (higher) data
# exists to its right, so the newest bars can never be extrema (lag of
# ``order`` distinct values / >= 1 bar). Confirmed extrema do not repaint as
# bars are appended; with a prominence filter a recent peak may appear late
# (prominence only grows as the right side develops) but is never withdrawn.

ExtremaList = list[tuple[int, float]]


def _extrema_output(
    data: np.ndarray, idx: np.ndarray, pip_size: float | None
) -> ExtremaList:
    if pip_size is None:
        return [(int(i), float(data[i])) for i in idx]
    base = data[0]
    return [(int(i), float((data[i] - base) / pip_size)) for i in idx]


def _validate_series(series: ArrayLike, pip_size: float | None) -> np.ndarray:
    data = np.asarray(series, dtype=float)
    if data.ndim != 1:
        raise ValueError("expected 1D input")
    if pip_size is not None and not pip_size > 0:
        raise ValueError("pip_size must be > 0")
    return data


def find_extrema(
    series: ArrayLike,
    order: int = 1,
    pip_size: float | None = None,
) -> tuple[ExtremaList, ExtremaList]:
    """Local maxima/minima: a bar must be strictly higher (lower) than the
    ``order`` distinct-valued neighbours on each side. Flat plateaus are
    collapsed first and reported at their leftmost bar.

    ``order`` counts distinct values (runs), not bars, so across plateaus the
    effective bar window is wider than ``order``. A NaN never equals anything,
    so it is its own run and blocks any extremum within ``order`` of it.
    Unlike ``scipy.signal.argrelextrema`` (which misses plateau tops), a flat
    top/bottom is still found.
    """
    if order < 1:
        raise ValueError("order must be >= 1")
    data = _validate_series(series, pip_size)
    if data.size == 0:
        return [], []

    # Collapse runs of equal values; pos maps compressed -> original index
    keep = np.r_[True, data[1:] != data[:-1]]
    pos = np.flatnonzero(keep)
    d = data[pos]
    m = len(d)
    if m < 2 * order + 1:
        return [], []

    s, e = order, m - order
    c = d[s:e]
    is_max = np.ones(len(c), dtype=bool)
    is_min = np.ones(len(c), dtype=bool)
    for k in range(1, order + 1):
        left, right = d[s - k:e - k], d[s + k:e + k]
        is_max &= (c > left) & (c > right)
        is_min &= (c < left) & (c < right)

    max_idx = pos[np.flatnonzero(is_max) + s]
    min_idx = pos[np.flatnonzero(is_min) + s]
    return (
        _extrema_output(data, max_idx, pip_size),
        _extrema_output(data, min_idx, pip_size),
    )


def find_extrema_scipy(
    series: ArrayLike,
    *,
    prominence: float | None = None,
    distance: int | None = None,
    width: float | None = None,
    pip_size: float | None = None,
) -> tuple[ExtremaList, ExtremaList]:
    """Local maxima/minima via ``scipy.signal.find_peaks`` (minima on the
    negated series). Plateaus are reported at their leftmost bar to match
    ``find_extrema``.

    ``prominence`` and ``width`` filter noise: prominence is the drop from the
    peak to the higher of its two surrounding bases, in **pips** when
    ``pip_size`` is given (else price units); width is in bars at half
    prominence. ``distance`` is the minimum bar gap between kept peaks (the
    lower one is dropped). With no filters every strict local extremum
    (neighbour order 1) is returned. For the zone extents behind a peak, call
    ``scipy.signal.peak_prominences`` / ``peak_widths`` on the returned
    indices.

    SciPy is imported lazily so the live path does not depend on it.
    """
    from scipy.signal import find_peaks

    data = _validate_series(series, pip_size)
    if data.size < 3:
        return [], []
    if prominence is not None and pip_size is not None:
        prominence = prominence * pip_size

    kwargs = dict(prominence=prominence, distance=distance, width=width,
                  plateau_size=1)  # plateau_size=1 → left_edges reported

    def edges(x: np.ndarray) -> np.ndarray:
        _, props = find_peaks(x, **kwargs)
        return props["left_edges"]

    return (
        _extrema_output(data, edges(data), pip_size),
        _extrema_output(data, edges(-data), pip_size),
    )
