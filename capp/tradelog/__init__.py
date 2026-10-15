"""
capp.tradelog — trade logging agent (V1-T35, TASKS.txt TASK 12).

  TradeLogAgent   switchable, levelled (off | bar | signals | debug) log,
                  new file per period (default: one per UTC day), signal /
                  strategy comparison and paper shadow books
  TradeLogConfig  agent settings
  MLRecorder      per-bar CSV feature rows for ML (opt-in)
  TradeJournal    real-trade quality: slippage, spread, costs, MFE / MAE,
                  bars held, exit reason (V1-T36)

Business layer: may import csys and capp; csys never imports it.
"""

from capp.tradelog.agent import (
    LEVELS,
    SIGNALS,
    PeriodFileHandler,
    ShadowBook,
    TradeLogAgent,
    TradeLogConfig,
    period_label,
    period_start,
)
from capp.tradelog.journal import TradeJournal, TradeRecord
from capp.tradelog.ml import MLRecorder

__all__ = [
    "LEVELS",
    "SIGNALS",
    "MLRecorder",
    "PeriodFileHandler",
    "ShadowBook",
    "TradeLogAgent",
    "TradeLogConfig",
    "TradeJournal",
    "TradeRecord",
    "period_label",
    "period_start",
]
