"""
csys — system layer.

Low-level connectivity and data plumbing for the cTrader Open API:
  * Proto request/response client (csys.ctrader_api)
  * Shared types / IndData (csys.ctypes)
  * Indicator engines (csys.cindicators)

Business logic (signals, strategies, app loop) lives in :mod:`capp`.
"""

from csys.ctrader_api import (
    CTraderConfig,
    CTraderOpenAPI,
    ExecutionResult,
    OpenPosition,
    OrderExecutor,
    load_config,
)
from csys.ctrader_api.ctypes import (
    DEFAULT_BAR_CAPACITY,
    DECAY_STRATEGY,
    DTYPE,
    IndData,
    PERIOD_MINUTES,
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
    "CTraderOpenAPI",
    "CTraderConfig",
    "load_config",
    "OrderExecutor",
    "OpenPosition",
    "ExecutionResult",
    "IndData",
    "SIG",
    "DECAY_STRATEGY",
    "DTYPE",
    "T_SIG",
    "SymbolMeta",
    "DEFAULT_BAR_CAPACITY",
    "PERIOD_MINUTES",
    "decode_trendbar",
    "relative_to_price",
    "symbol_meta_from_proto",
    "apply_symbol_meta",
    "period_minutes",
    "period_seconds",
]
