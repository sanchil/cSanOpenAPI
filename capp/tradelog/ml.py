"""
MLRecorder — per-bar feature rows for offline ML (V1-T35).

One CSV row per closed bar and symbol, in ``<ml_dir>/features_<period>.csv``
(same rotation as the trade log; header written when a file is created).
Columns: bar time / OHLCV, indicator values at the closed bar, every T_SIG
signal (as its name) and DTYPE data value, the active strategy output,
compared outputs, avg PnL and the live position side.

Labels are not written: forward returns / MFE / MAE are computed offline
from consecutive rows (no look-ahead at record time).
"""

from __future__ import annotations

import csv
import dataclasses
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from csys.ctypes import DTYPE, IndData, SIG, T_SIG

_IND_FIELDS = ("atr", "adx", "adx_plus", "adx_minus", "std_close",
               "ima5", "ima14", "ima30", "ima60", "ima120", "ima240", "ima500")


def _f(series, i: int) -> float:
    try:
        return float(series[i])
    except (IndexError, TypeError, ValueError):
        return float("nan")


class MLRecorder:
    """Append-only CSV feature log with period rotation."""

    def __init__(self, ml_dir: str = "logs/ml", *, rotation="day", utc: bool = True, prefix: str = "features") -> None:
        from capp.tradelog.agent import period_label, period_start  # avoid import cycle

        self._period_start, self._period_label = period_start, period_label
        self.ml_dir, self.rotation, self.utc, self.prefix = ml_dir, rotation, utc, prefix
        self._key: Optional[datetime] = None
        self._fh = None
        self._writer: Optional[csv.DictWriter] = None
        self.path: Optional[str] = None

    def _open(self, key: datetime, columns) -> None:
        self.close()
        os.makedirs(self.ml_dir, exist_ok=True)
        self.path = os.path.join(self.ml_dir, f"{self.prefix}_{self._period_label(key, self.rotation)}.csv")
        new = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=list(columns), extrasaction="ignore", restval="")
        if new:
            self._writer.writeheader()
        self._key = key

    def record(
        self,
        *,
        data: IndData,
        symbol_id: int,
        symbol_name: str,
        t_sig: T_SIG,
        close_time: Optional[datetime],
        index: int,
        active: int,
        trade_sig: SIG,
        avg_pnl: float,
        position_side: Optional[SIG],
        outputs: Dict[str, Any],
    ) -> None:
        i = index
        row: Dict[str, Any] = {
            "close_time": close_time.isoformat() if close_time else "",
            "symbol_id": symbol_id,
            "symbol": symbol_name,
            "open": _f(data.open, i), "high": _f(data.high, i), "low": _f(data.low, i),
            "close": _f(data.close, i), "volume": _f(data.tick_volume, i),
            "pip_size": float(getattr(data, "pip_size", 0.0) or 0.0),
        }
        for name in _IND_FIELDS:
            row[name] = _f(getattr(data, name, ()), i)
        for f in dataclasses.fields(t_sig):
            v = getattr(t_sig, f.name)
            if isinstance(v, SIG):
                row[f.name] = v.name
            elif isinstance(v, DTYPE):
                for k in ("val1", "val2", "val3", "val4", "val5"):
                    row[f"{f.name}_{k}"] = getattr(v, k)
        row["active_strategy"] = active
        row["trade_sig"] = trade_sig.name
        for k, v in outputs.items():
            row[f"out_{k.replace(' ', '_').replace('/', '_')}"] = v.name if isinstance(v, SIG) else v
        row["avg_pnl"] = avg_pnl
        row["position"] = position_side.name if isinstance(position_side, SIG) else ""

        now = datetime.now(timezone.utc) if self.utc else datetime.now().astimezone()
        key = self._period_start(now, self.rotation)
        if key != self._key:
            self._open(key, row.keys())
        self._writer.writerow(row)
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._writer = None
