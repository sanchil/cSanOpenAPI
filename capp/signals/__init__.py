"""
capp.signals — signal primitives (SanSignals / init_sig path).

Types (DTYPE, T_SIG, SIG) live in :mod:`csys.ctypes` — not duplicated here.
Used by :mod:`capp.strategies` and :mod:`capp.ctraderapp`.
"""

from csys.ctypes import DTYPE, SIG, T_SIG

from .signals import (
    SanSignals,
    fast_slow_sig,
    fuse_sig,
    kinetic_acceleration_sig,
)
from .stats import (
    compute_stencil_kinematics,
    computeStencilKinematics,
    mql_at,
    slope_val,
    slopeVal,
)

__all__ = [
    "SanSignals",
    "T_SIG",
    "DTYPE",
    "SIG",
    "fast_slow_sig",
    "kinetic_acceleration_sig",
    "fuse_sig",
    "slope_val",
    "slopeVal",
    "compute_stencil_kinematics",
    "computeStencilKinematics",
    "mql_at",
]
