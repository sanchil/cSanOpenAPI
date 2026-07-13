"""
capp — application / business-logic layer.

Holds strategies, signals, and the trading app loop. Depends on :mod:`csys`
for connectivity, types, market data, and indicators.

  capp.csignals      — SanSignals / T_SIG builders
  capp.cstrategies   — Strategy_1..4
  capp.ctraderapp    — CTraderApp orchestration
"""

from capp.ctraderapp import CTraderApp
from capp.csignals import SanSignals, T_SIG, DTYPE, fuse_sig, fast_slow_sig
from capp.cstrategies import CStrategies

__all__ = [
    "CTraderApp",
    "SanSignals",
    "CStrategies",
    "T_SIG",
    "DTYPE",
    "fuse_sig",
    "fast_slow_sig",
]
