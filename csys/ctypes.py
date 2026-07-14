"""
csys.ctypes — public types facade for the system layer.

Canonical definitions live in :mod:`csys.ctrader_api.ctypes`; this module
re-exports them so design.txt's ``ctypes → csys`` mapping is explicit.
"""

from csys.ctrader_api.ctypes import (  # noqa: F401
    DEFAULT_BAR_CAPACITY,
    DECAY_STRATEGY,
    DTYPE,
    PERIOD_MINUTES,
    PRICE_SCALE,
    IndData,
    SIG,
    SymbolMeta,
    T_SIG,
    apply_symbol_meta,
    decode_trendbar,
    period_minutes,
    period_seconds,
    relative_to_price,
    symbol_meta_from_proto,
)

__all__ = [
    "PRICE_SCALE",
    "DEFAULT_BAR_CAPACITY",
    "PERIOD_MINUTES",
    "SIG",
    "DECAY_STRATEGY",
    "DTYPE",
    "T_SIG",
    "IndData",
    "SymbolMeta",
    "relative_to_price",
    "decode_trendbar",
    "symbol_meta_from_proto",
    "apply_symbol_meta",
    "period_minutes",
    "period_seconds",
]
