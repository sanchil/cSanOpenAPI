"""
csignals — signal primitives and tactical signal bundle (T_SIG).

Migrated from mq4 SanSignals / HSIG and cBot CSignal for the execution path
described in design.txt. Used by ``cstrategies`` and ``CTraderApp``.
"""

from ctrader_api.ctypes import DTYPE, SIG, T_SIG

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
