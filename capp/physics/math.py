"""
NumPy / SciPy linear-algebra and stats primitives for the physics layer.

Foundation for porting ``code_references`` Stats / MarketMetrics / Physics
analysis onto a scientific stack (prefer library routines over hand-rolled
linAlg). SciPy is a declared dependency for future stats/probability helpers;
this module's first surface uses NumPy only.

V1-T24: 2×2 covariance matrix + determinant.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import numpy as np

ArrayLike = Union[Sequence[float], np.ndarray]


def covariance_matrix_2x2(
    a: ArrayLike,
    b: ArrayLike,
    *,
    ddof: int = 1,
) -> np.ndarray:
    """
    Sample (default) 2×2 covariance matrix of paired series ``a`` and ``b``.

    Returns
    -------
    np.ndarray
        Shape ``(2, 2)``::

            [[var(a), cov(a,b)],
             [cov(a,b), var(b)]]

    Notes
    -----
    Uses ``numpy.cov(..., ddof=1)`` by default (sample covariance), matching
    mq4 ``Stats::cov`` divisor ``(SIZE - 1)``.
    """
    aa = np.asarray(a, dtype=float)
    bb = np.asarray(b, dtype=float)
    if aa.ndim != 1 or bb.ndim != 1:
        raise ValueError("a and b must be 1-D series")
    if aa.shape[0] != bb.shape[0]:
        raise ValueError(
            f"a and b length mismatch: {aa.shape[0]} != {bb.shape[0]}"
        )
    # np.cov with two 1-D args returns (2, 2)
    return np.cov(aa, bb, ddof=ddof)


def matrix_determinant(m: ArrayLike) -> float:
    """
    Determinant of a square matrix via ``numpy.linalg.det``.

    Parameters
    ----------
    m :
        Square matrix (e.g. 2×2 covariance matrix).
    """
    arr = np.asarray(m, dtype=float)
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
        raise ValueError(f"expected square matrix, got shape {arr.shape}")
    return float(np.linalg.det(arr))
