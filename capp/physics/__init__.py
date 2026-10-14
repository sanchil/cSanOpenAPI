"""
capp.physics — NumPy/SciPy analysis layer (linAlg, stats, series metrics).

Home for pure physics / MarketMetrics-style building blocks ported from
``code_references`` onto a scientific stack. Prefer NumPy/SciPy over
hand-rolled linear algebra and probability.

Does not import Open API / Twisted. Series convention: oldest → newest.

V1-T24: covariance-matrix determinant metric.
V1-T28: local extrema (find_extrema / find_extrema_scipy) for S/R signals.
"""

from __future__ import annotations

from capp.physics.math import covariance_matrix_2x2, matrix_determinant
from capp.physics.metrics import (
    covariance_matrix_determinant,
    find_extrema,
    find_extrema_scipy,
)

__all__ = [
    "covariance_matrix_2x2",
    "matrix_determinant",
    "covariance_matrix_determinant",
    "find_extrema",
    "find_extrema_scipy",
]
