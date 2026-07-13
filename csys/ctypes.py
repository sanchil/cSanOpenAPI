"""
csys.ctypes — public types facade for the system layer.

Canonical definitions live in :mod:`csys.ctrader_api.ctypes`; this module
re-exports them so design.txt's ``ctypes → csys`` mapping is explicit.
"""

from csys.ctrader_api.ctypes import (  # noqa: F401
    DEFAULT_BAR_CAPACITY,
    DECAY_STRATEGY,
    DTYPE,
    PRICE_SCALE,
    IndData,
    SIG,
    T_SIG,
    decode_trendbar,
    relative_to_price,
)

__all__ = [
    "PRICE_SCALE",
    "DEFAULT_BAR_CAPACITY",
    "SIG",
    "DECAY_STRATEGY",
    "DTYPE",
    "T_SIG",
    "IndData",
    "relative_to_price",
    "decode_trendbar",
]
