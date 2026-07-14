"""
csys.ctrader_api — Open API client, config, and order execution.

System-layer only: connect, auth, proto requests, market data, orders.
Business app loop lives in :mod:`capp.ctraderapp`.
"""

from .client import CTraderOpenAPI, scale_money
from .config import CTraderConfig, load_config
from .ctypes import (
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
from .execution import ExecutionResult, OpenPosition, OrderExecutor

__all__ = [
    "CTraderOpenAPI",
    "scale_money",
    "CTraderConfig",
    "load_config",
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
    "OrderExecutor",
    "OpenPosition",
    "ExecutionResult",
]
