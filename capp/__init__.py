"""
capp — application / business-logic layer.

Holds strategies, signals, and the trading app loop. Depends on :mod:`csys`
for connectivity, types, market data, and indicators.

  capp.signals      — SanSignals builders (types from csys.ctypes)
  capp.strategies   — Strategy_1..4
  capp.ctraderapp    — CTraderApp orchestration
"""

from csys.ctypes import DTYPE, T_SIG

from capp.ctraderapp import CTraderApp
from capp.signals import SanSignals, fast_slow_sig, fuse_sig
from capp.strategies import CStrategies

__all__ = [
    "CTraderApp",
    "SanSignals",
    "CStrategies",
    "T_SIG",
    "DTYPE",
    "fuse_sig",
    "fast_slow_sig",
]
