"""
csys.ctrader_api — Open API client, config, and order execution.

System-layer only: connect, auth, proto requests, market data, orders.
Business app loop lives in :mod:`capp.ctraderapp`.
"""

from .client import CTraderOpenAPI
from .config import CTraderConfig, load_config
from .ctypes import (
    DEFAULT_BAR_CAPACITY,
    DECAY_STRATEGY,
    DTYPE,
    IndData,
    SIG,
    T_SIG,
    decode_trendbar,
    relative_to_price,
)
from .execution import ExecutionResult, OpenPosition, OrderExecutor

__all__ = [
    "CTraderOpenAPI",
    "CTraderConfig",
    "load_config",
    "IndData",
    "SIG",
    "DECAY_STRATEGY",
    "DTYPE",
    "T_SIG",
    "DEFAULT_BAR_CAPACITY",
    "decode_trendbar",
    "relative_to_price",
    "OrderExecutor",
    "OpenPosition",
    "ExecutionResult",
]
