"""
Series-level physics metrics (MarketMetrics-style building blocks).

First metric (V1-T24): determinant of the 2×2 sample covariance matrix of two
series over the previous ``n`` periods.

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
