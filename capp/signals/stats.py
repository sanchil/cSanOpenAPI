"""
Stats / kinematics helpers migrated from mq4 Stats::slopeVal and
Stats::computeStencilKinematics (and cBot CStats.slopesVal).

Series convention
-----------------
Input series are **oldest → newest** (IndData deques).

MQL/cBot use index 0 = newest/current. We bridge with ``mql_at`` / ``mql_index``
so formulas match the reference code with the same ``shift`` meaning
(0 = newest, 1 = previous closed bar, …).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Union

from csys.ctypes import DTYPE

Series = Union[Sequence[float], list, tuple]

DEFAULT_PIP = 0.0001


def mql_index(length: int, shift: int) -> int:
    """Map MQL shift (0=newest) → Python index into oldest→newest series."""
    if length <= 0:
        raise IndexError("empty series")
    idx = length - 1 - int(shift)
    if idx < 0 or idx >= length:
        raise IndexError(f"shift={shift} out of range for length={length}")
    return idx


def mql_at(series: Series, shift: int) -> float:
    """Value at MQL-style shift (0=newest)."""
    return float(series[mql_index(len(series), shift)])


def resolve_pip(pip_size: float = 0.0, point: float = 0.0) -> float:
    """Pip size with safe FX fallbacks."""
    if pip_size and pip_size > 0:
        return float(pip_size)
    if point and point > 0:
        return float(point)
    return DEFAULT_PIP


def slope_val(
    sig: Series,
    slope_denom: int = 3,
    slope_denom_wide: int = 5,
    shift: int = 1,
    *,
    pip_size: float = 0.0,
    point: float = 0.0,
) -> DTYPE:
    """
    Generic differentiation — mq4 Stats::slopeVal / CStats.slopesVal.

    val1 = (sig[shift] - sig[shift+SLOPEDENOM]) / (SLOPEDENOM * pip)
    val2 = (sig[shift] - sig[shift+SLOPEDENOM_WIDE]) / (SLOPEDENOM_WIDE * pip)
    val3 = val1 - val2   (acceleration / curvature proxy)
    val4 = val1 - val2   (C# also stores this as val4)
    """
    dt = DTYPE()
    n = len(sig)
    need = shift + max(slope_denom, slope_denom_wide)
    if n <= need or slope_denom <= 0 or slope_denom_wide <= 0:
        return dt

    pip = resolve_pip(pip_size, point)
    try:
        a = mql_at(sig, shift)
        b_fast = mql_at(sig, shift + slope_denom)
        b_wide = mql_at(sig, shift + slope_denom_wide)
    except IndexError:
        return dt

    if any(math.isnan(x) for x in (a, b_fast, b_wide)):
        return dt

    dt.val1 = round((a - b_fast) / (slope_denom * pip), 4)
    dt.val2 = round((a - b_wide) / (slope_denom_wide * pip), 4)
    dt.val3 = dt.val1 - dt.val2
    dt.val4 = dt.val1 - dt.val2
    return dt


# Alias matching design.txt / mq4 naming
slopeVal = slope_val


def compute_stencil_kinematics(
    sig: Series,
    shift: int = 3,
    *,
    pip_size: float = 0.0,
    point: float = 0.0,
) -> DTYPE:
    """
    Superior finite-difference kinematics — mq4 Stats::computeStencilKinematics.

    5-point stencil centered at ``shift`` (MQL index; 0=newest):
      val1 — 4th-order velocity
      val2 — central 2nd derivative (acceleration)
      val3 — velocity / acceleration (momentum state)
    """
    dt = DTYPE()
    n = len(sig)
    # Need indices shift-2 … shift+2 in MQL space → length > shift+2 and shift >= 2
    if shift < 2 or n < shift + 3:
        return dt

    pip = resolve_pip(pip_size, point)
    if pip <= 0:
        pip = DEFAULT_PIP

    try:
        y_m2 = mql_at(sig, shift - 2) / pip  # newer relative to center
        y_m1 = mql_at(sig, shift - 1) / pip
        y_0 = mql_at(sig, shift) / pip
        y_p1 = mql_at(sig, shift + 1) / pip  # older
        y_p2 = mql_at(sig, shift + 2) / pip
    except IndexError:
        return dt

    if any(math.isnan(x) for x in (y_m2, y_m1, y_0, y_p1, y_p2)):
        return dt

    # Velocity (first derivative)
    dt.val1 = (-y_p2 + 8.0 * y_p1 - 8.0 * y_m1 + y_m2) / 12.0
    # Acceleration (second derivative)
    dt.val2 = y_p1 - 2.0 * y_0 + y_m1
    # Momentum state
    dt.val3 = 0.0 if abs(dt.val2) < 1e-9 else dt.val1 / dt.val2
    return dt


# Alias matching design.txt
computeStencilKinematics = compute_stencil_kinematics
