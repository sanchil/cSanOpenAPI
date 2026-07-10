"""cTrader Open API Python interface."""

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

# CTraderApp is imported lazily — it pulls in cindicators/csignals/cstrategies,
# which import ctypes and would cycle if loaded here eagerly.

__all__ = [
    "CTraderApp",
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
]


def __getattr__(name: str):
    if name == "CTraderApp":
        from .ctraderapp import CTraderApp

        return CTraderApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
