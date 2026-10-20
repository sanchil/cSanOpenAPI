"""
TradeLogAgent — switchable, levelled, period-rotated trade log (V1-T35).

One agent per CTraderApp. Fed once per closed bar from
``CTraderApp._log_cycle_result`` and from ``OrderExecutor.on_action``.

Levels (cumulative):
  off      nothing
  bar      "New bar closed" line, order executions, period summaries
  signals  + "Total Trade Profits" line, signal / strategy comparison line,
           shadow-book trade events (paper trades per compared source)
  debug    + every T_SIG signal and DTYPE data field

Files: ``<log_dir>/<prefix>_<period start>.log``, a new file per period.
Rotation "day" (default, UTC 00:00 → 24:00), "hour", "week", or an integer
number of seconds (epoch-aligned buckets). Console echo is optional.

Shadow book: every compared strategy / signal gets a paper position per
symbol under the live executor policy (flat + BUY/SELL opens, opposite or
CLOSE / SIDEWAYS closes, no reversal in the same bar, NOSIG / HOLD / NOTRADE
hold), filled at the closed bar's close. Results are in pips, no spread or
commission; MFE / MAE from the high / low of each bar a paper position
lived through. Summaries are written at each rollover and on close().

Trade journal (V1-T36, capp.tradelog.journal): real positions — fill vs.
decision quote (slippage), spread, commission / swap / gross / net from the
broker, MFE / MAE, bars held, entry / exit reason. FILL / TRADE lines at
level "bar"; one CSV row per closed trade in <log_dir>/trades_<period>.csv.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from csys.ctypes import DTYPE, IndData, SIG, T_SIG

from capp.tradelog.journal import TradeJournal
from capp.tradelog.ml import MLRecorder

SIGNALS = 15
logging.addLevelName(SIGNALS, "SIGNALS")
LEVELS = {"off": None, "bar": logging.INFO, "signals": SIGNALS, "debug": logging.DEBUG}

Rotation = Union[str, int]

# Display names for T_SIG fields in the comparison line
SIGNAL_LABELS = {
    "peak_trough_sig": "Peak/Trough",
    "peak_trough_sig_alt": "Peak/Trough alt",
    "slope_dist_sig": "Slope Distribution",
    "base_slope_sig": "Kinetic MQ4",
    "base_slope_sig_v1": "Kinetic v1",
    "base_slope_sig_v2": "Kinetic v2",
    "fast_sig": "Fast",
}

_CLOSING = (SIG.CLOSE, SIG.SIDEWAYS)
NAN = float("nan")


@dataclass
class TradeLogConfig:
    """Agent settings (constructor-driven until V1-T01 config lands)."""

    enabled: bool = True
    level: str = "signals"                 # off | bar | signals | debug
    log_dir: str = "logs"
    prefix: str = "trade"
    rotation: Rotation = "day"             # day | hour | week | seconds
    utc: bool = True                       # period boundaries in UTC (else local)
    console: bool = True                   # echo log lines to stdout
    compare_strategies: Tuple[int, ...] = (7, 8, 9)
    compare_signals: Tuple[str, ...] = (
        "peak_trough_sig", "peak_trough_sig_alt", "slope_dist_sig", "base_slope_sig_v2"
    )
    shadow_book: bool = True
    trade_journal: bool = True             # real-trade quality + trades_<period>.csv
    ml_capture: bool = False               # per-bar feature rows → CSV
    ml_dir: str = "logs/ml"


def period_start(now: datetime, rotation: Rotation) -> datetime:
    """Start of the rotation period containing ``now``."""
    if isinstance(rotation, int):
        if rotation <= 0:
            raise ValueError("rotation seconds must be > 0")
        ts = int(now.timestamp())
        return datetime.fromtimestamp(ts - ts % rotation, tz=now.tzinfo)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if rotation == "day":
        return day
    if rotation == "hour":
        return now.replace(minute=0, second=0, microsecond=0)
    if rotation == "week":
        return day - timedelta(days=day.weekday())
    raise ValueError(f"unknown rotation {rotation!r} (day | hour | week | seconds)")


def period_label(start: datetime, rotation: Rotation) -> str:
    if rotation == "hour":
        return start.strftime("%Y-%m-%d_%H")
    if isinstance(rotation, int):
        return start.strftime("%Y-%m-%d_%H%M%S")
    return start.strftime("%Y-%m-%d")


class PeriodFileHandler(logging.Handler):
    """logging.Handler writing to ``<dir>/<prefix>_<period>.log``; switches
    file when the period changes (checked per record)."""

    def __init__(self, log_dir: str, prefix: str, rotation: Rotation, utc: bool = True) -> None:
        super().__init__()
        self.log_dir, self.prefix, self.rotation, self.utc = log_dir, prefix, rotation, utc
        self._key: Optional[datetime] = None
        self._stream = None
        self.path: Optional[str] = None
        # When set, records go to this period's file (rollover summaries)
        self.pinned: Optional[datetime] = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc) if self.utc else datetime.now().astimezone()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            key = self.pinned or period_start(self._now(), self.rotation)
            if key != self._key:
                self._open(key)
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def _open(self, key: datetime) -> None:
        if self._stream is not None:
            self._stream.close()
        os.makedirs(self.log_dir, exist_ok=True)
        self.path = os.path.join(self.log_dir, f"{self.prefix}_{period_label(key, self.rotation)}.log")
        self._stream = open(self.path, "a", encoding="utf-8")
        self._key = key

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()


@dataclass
class _Paper:
    side: SIG
    price: float
    opened: datetime
    bars: int = 0
    mfe: float = 0.0
    mae: float = 0.0


@dataclass
class _Stats:
    trades: int = 0
    wins: int = 0
    net_pips: float = 0.0
    best: float = 0.0
    worst: float = 0.0
    bars: int = 0
    mfe: float = 0.0
    mae: float = 0.0

    def add(self, pips: float, bars: int, mfe: float = 0.0, mae: float = 0.0) -> None:
        self.trades += 1
        self.wins += pips > 0
        self.net_pips += pips
        self.best = max(self.best, pips)
        self.worst = min(self.worst, pips)
        self.bars += bars
        self.mfe += mfe
        self.mae += mae


@dataclass
class ShadowBook:
    """Paper positions per (symbol, source) under the executor policy."""

    positions: Dict[Tuple[int, str], _Paper] = field(default_factory=dict)
    stats: Dict[str, _Stats] = field(default_factory=dict)
    last_price: Dict[int, Tuple[float, float]] = field(default_factory=dict)  # sid → (price, pip)

    def apply(
        self, sid: int, source: str, sig: SIG, price: float, pip: float, when: datetime,
        high: float = NAN, low: float = NAN,
    ) -> Optional[str]:
        """Advance one bar; return an event string on open / close."""
        self.last_price[sid] = (price, pip)
        self.stats.setdefault(source, _Stats())
        key = (sid, source)
        pos = self.positions.get(key)
        if pos is not None:
            pos.bars += 1
            if pip > 0 and high == high and low == low:
                hi, lo = (high, low) if pos.side == SIG.BUY else (low, high)
                pos.mfe = max(pos.mfe, self._pips(pos, hi, pip))
                pos.mae = min(pos.mae, self._pips(pos, lo, pip))
            opposite = (pos.side == SIG.BUY and sig == SIG.SELL) or (pos.side == SIG.SELL and sig == SIG.BUY)
            if sig in _CLOSING or opposite:
                pips = self._pips(pos, price, pip)
                self.stats[source].add(pips, pos.bars, pos.mfe, pos.mae)
                del self.positions[key]
                return (f"CLOSE {pos.side.name} @ {price:.5f} pnl={pips:+.1f}p bars={pos.bars} "
                        f"MFE={pos.mfe:+.1f}p MAE={pos.mae:+.1f}p ({sig.name})")
            return None
        if sig in (SIG.BUY, SIG.SELL):
            self.positions[key] = _Paper(sig, price, when)
            return f"OPEN {sig.name} @ {price:.5f}"
        return None

    @staticmethod
    def _pips(pos: _Paper, price: float, pip: float) -> float:
        d = (price - pos.price) if pos.side == SIG.BUY else (pos.price - price)
        return d / pip if pip > 0 else 0.0

    def summary_lines(self, symbol_names: Dict[int, str]) -> List[str]:
        lines = []
        for source in sorted(self.stats):
            st = self.stats[source]
            open_pips = [
                (sid, self._pips(p, *self.last_price.get(sid, (p.price, 0.0))))
                for (sid, src), p in self.positions.items() if src == source
            ]
            win = 100.0 * st.wins / st.trades if st.trades else 0.0
            avg_bars = st.bars / st.trades if st.trades else 0.0
            avg_mfe = st.mfe / st.trades if st.trades else 0.0
            avg_mae = st.mae / st.trades if st.trades else 0.0
            open_txt = ", ".join(f"{symbol_names.get(s, s)} {v:+.1f}p" for s, v in open_pips) or "-"
            lines.append(
                f"  {source:22s} trades={st.trades:3d} win={win:5.1f}% net={st.net_pips:+8.1f}p "
                f"best={st.best:+6.1f}p worst={st.worst:+6.1f}p avg_bars={avg_bars:5.1f} "
                f"avg_MFE={avg_mfe:+5.1f}p avg_MAE={avg_mae:+5.1f}p open: {open_txt}"
            )
        return lines

    def reset_period(self) -> None:
        self.stats = {k: _Stats() for k in self.stats}


def _name(sig: Any) -> str:
    return sig.name if isinstance(sig, SIG) else str(sig)


def _at(series, i: int) -> float:
    try:
        return float(series[i])
    except (IndexError, TypeError, ValueError):
        return float("nan")


class TradeLogAgent:
    """Switchable per-bar trade logger with signal comparison and paper books."""

    def __init__(self, config: Optional[TradeLogConfig] = None) -> None:
        self.config = config or TradeLogConfig()
        level = LEVELS.get(self.config.level, "invalid")
        if level == "invalid":
            raise ValueError(f"level must be one of {tuple(LEVELS)}")
        self.enabled = bool(self.config.enabled and level is not None)
        self.level = level or logging.CRITICAL + 1
        self.book = ShadowBook()
        self.symbol_names: Dict[int, str] = {}
        # Optional sid → name lookup (set by CTraderApp; orders log before bars)
        self.name_of: Optional[Callable[[int], str]] = None
        self._period: Optional[datetime] = None
        self.ml: Optional[MLRecorder] = None
        self.journal: Optional[TradeJournal] = None

        self.logger = logging.getLogger(f"capp.tradelog.{id(self):x}")
        self.logger.propagate = False
        self.logger.setLevel(self.level)
        self.file_handler: Optional[PeriodFileHandler] = None
        if not self.enabled:
            return
        c = self.config
        self.file_handler = PeriodFileHandler(c.log_dir, c.prefix, c.rotation, c.utc)
        fmt = logging.Formatter("%(asctime)sZ %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
        fmt.converter = time.gmtime
        self.file_handler.setFormatter(fmt)
        self.logger.addHandler(self.file_handler)
        if c.console:
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(console)
        if c.ml_capture:
            self.ml = MLRecorder(c.ml_dir, rotation=c.rotation, utc=c.utc)
        if c.trade_journal:
            self.journal = TradeJournal(csv_dir=c.log_dir, rotation=c.rotation, name_of=self._sym, now=self._now)

    # ------------------------------------------------------------- helpers
    def _sym(self, sid: int) -> str:
        if self.name_of:
            return self.name_of(sid)
        return self.symbol_names.get(sid, str(sid))

    def is_on(self, level: int) -> bool:
        return self.enabled and self.level <= level

    def _now(self) -> datetime:
        return datetime.now(timezone.utc) if self.config.utc else datetime.now().astimezone()

    def _check_rollover(self) -> None:
        """Write the previous period's summaries before its file closes.
        Called at every entry point so the summary precedes any new-period
        line (the file handler switches files on the first such line)."""
        key = period_start(self._now(), self.config.rotation)
        if self._period is not None and key != self._period:
            self._summary(self._period)
            self.book.reset_period()
            if self.journal is not None:
                self.journal.reset_period()
        self._period = key

    def _summary(self, start: Optional[datetime]) -> None:
        if self.file_handler is not None:
            self.file_handler.pinned = start     # into the closing period's file
        try:
            self._write_summary(start)
        finally:
            if self.file_handler is not None:
                self.file_handler.pinned = None

    def _write_summary(self, start: Optional[datetime]) -> None:
        label = period_label(start, self.config.rotation) if start else "?"
        real = self.journal.summary_line() if self.journal is not None else None
        if real:
            self.logger.info("Period summary %s (real trades, account currency / pips):", label)
            self.logger.info(real)
        if self.config.shadow_book and self.book.stats:
            self.logger.info("Period summary %s (paper, pips, no costs):", label)
            for line in self.book.summary_lines(self.symbol_names):
                self.logger.info(line)

    def compare_outputs(
        self, strategies: Any, t_sig: T_SIG, active: int, trade_sig: SIG, avg_pnl: float, sid: int
    ) -> Dict[str, SIG]:
        """Active strategy output plus compared strategies and T_SIG signals.
        The active strategy is not re-run (Strategy_6 keeps sticky state)."""
        out: Dict[str, SIG] = {f"Strategy_{active}": trade_sig}
        for n in self.config.compare_strategies:
            if n == active or strategies is None:
                continue
            try:
                out[f"Strategy_{n}"] = strategies.run_strategy(n, t_sig, avg_pnl, symbol_id=sid)
            except Exception as e:  # a broken shadow strategy must not stop trading
                self.logger.warning("Strategy_%s shadow failed: %s", n, e)
        for name in self.config.compare_signals:
            out[SIGNAL_LABELS.get(name, name)] = getattr(t_sig, name, SIG.NOSIG)
        return out

    # ------------------------------------------------------------ main API
    def on_cycle(
        self,
        *,
        data: IndData,
        symbol_id: int,
        symbol_name: str,
        t_sig: Optional[T_SIG],
        trade_sig: SIG,
        active: int,
        avg_pnl: float,
        strategies: Any = None,
        position_side: Optional[SIG] = None,
        tag: str = "",
    ) -> None:
        """Log one closed-bar cycle (call after the strategy has run)."""
        if not self.enabled:
            return
        self.symbol_names[symbol_id] = symbol_name
        self._check_rollover()

        i = -2 if len(data.close) >= 2 else -1     # [-1] is the bar that just opened
        try:
            opened = data.time[i]
            close_time = opened + timedelta(seconds=int(getattr(data, "period", 0) or 60))
        except (IndexError, TypeError):
            close_time = None
        when = close_time or self._now().replace(tzinfo=None)
        price = _at(data.close, i)
        pip = float(getattr(data, "pip_size", 0.0) or 0.0)
        suffix = f" ({tag})" if tag else ""

        self.logger.info(
            "New bar closed: %s [%s] O=%.5f H=%.5f L=%.5f C=%.5f V=%.0f ATR=%.5f ADX=%.2f avgPnL=%.2f SIG=%s%s",
            close_time.strftime("%Y-%m-%d %H:%M:%S") if close_time else "?", symbol_name,
            _at(data.open, i), _at(data.high, i), _at(data.low, i), price, _at(data.tick_volume, i),
            _at(data.atr, i), _at(data.adx, i), avg_pnl, _name(trade_sig), suffix,
        )
        if t_sig is None:
            return

        outputs = self.compare_outputs(strategies, t_sig, active, trade_sig, avg_pnl, symbol_id)
        self.logger.log(SIGNALS, "Total Trade Profits: %.2f Active Strategy: %s", avg_pnl, active)
        self.logger.log(
            SIGNALS, "Signals [%s] %s", symbol_name,
            " | ".join(f"{k}{' (active)' if k == f'Strategy_{active}' else ''}: {_name(v)}" for k, v in outputs.items()),
        )

        if self.config.shadow_book and price == price:
            high, low = _at(data.high, i), _at(data.low, i)
            for source, sig in outputs.items():
                event = self.book.apply(symbol_id, source, sig, price, pip, when, high, low)
                if event:
                    self.logger.log(SIGNALS, "Shadow [%s] %s %s", symbol_name, source, event)

        if self.is_on(logging.DEBUG):
            sigs, data_fields = [], []
            for f in dataclasses.fields(t_sig):
                v = getattr(t_sig, f.name)
                if isinstance(v, SIG):
                    sigs.append(f"{f.name}={v.name}")
                elif isinstance(v, DTYPE):
                    data_fields.append(
                        f"{f.name}=({v.val1:.4g}, {v.val2:.4g}, {v.val3:.4g}, {v.val4:.4g}, {v.val5:.4g})"
                    )
            self.logger.debug("T_SIG [%s] %s", symbol_name, " ".join(sigs))
            self.logger.debug("T_SIG data [%s] %s", symbol_name, " ".join(data_fields))

        if self.ml is not None:
            self.ml.record(
                data=data, symbol_id=symbol_id, symbol_name=symbol_name, t_sig=t_sig,
                close_time=close_time, index=i, active=active, trade_sig=trade_sig,
                avg_pnl=avg_pnl, position_side=position_side, outputs=outputs,
            )

    def on_decision(
        self,
        *,
        data: IndData,
        symbol_id: int,
        trade_sig: SIG,
        position_side: Optional[SIG],
        quote: Optional[Tuple[float, float]],
        t_sig: Optional[T_SIG],
        active: int,
        reason: Optional[str] = None,
    ) -> None:
        """Call before OrderExecutor.handle_signal: records the order intent
        and reference quote, and advances open trades' MFE / MAE. ``reason``
        overrides the journal's close reason (e.g. the V1-T38 profit trail)."""
        if not self.enabled or self.journal is None:
            return
        self._check_rollover()
        i = -2 if len(data.close) >= 2 else -1
        try:
            when = data.time[i] + timedelta(seconds=int(getattr(data, "period", 0) or 60))
        except (IndexError, TypeError):
            when = None
        self.journal.on_decision(
            symbol_id=symbol_id, trade_sig=trade_sig, position_side=position_side,
            bar_high=_at(data.high, i), bar_low=_at(data.low, i), bar_close=_at(data.close, i),
            pip=float(getattr(data, "pip_size", 0.0) or 0.0), quote=quote, t_sig=t_sig,
            active=active, when=when, reason=reason,
        )

    def note(self, msg: str, level: int = logging.WARNING) -> None:
        """Free-form ops line (stalls, disconnects, history reloads)."""
        if not self.enabled:
            return
        self._check_rollover()
        self.logger.log(level, msg)

    def on_fill(self, fill: Any) -> None:
        """Broker deal (csys DealFill) → FILL / TRADE lines and trades CSV."""
        if not self.enabled or self.journal is None:
            return
        self._check_rollover()
        lines, _ = self.journal.on_fill(fill)
        for line in lines:
            self.logger.info(line)

    def log_execution(self, result: Any) -> None:
        """OrderExecutor.on_action hook: log real opens / closes / errors."""
        if not self.enabled or getattr(result, "action", "none") == "none":
            return
        self._check_rollover()
        sid = getattr(result, "symbol_id", 0)
        detail = getattr(result, "detail", "")
        self.logger.info(
            "EXEC [%s] %s %s — %s", self._sym(sid), result.action,
            _name(getattr(result, "signal", "")), detail,
        )
        # Dry run: no broker deal will arrive — fill at the decision quote
        if self.journal is not None and "[dry_run]" in detail and result.action in ("open", "close"):
            pos = getattr(result, "position", None)
            lines, _ = self.journal.synthetic_fill(sid, result.action, getattr(pos, "volume", 0.0) or 0.0)
            for line in lines:
                self.logger.info(line)

    def close(self) -> None:
        """Write the open period's summary and close files (call on shutdown)."""
        if not self.enabled:
            return
        self._summary(self._period)
        for h in list(self.logger.handlers):
            h.close()
            self.logger.removeHandler(h)
        if self.ml is not None:
            self.ml.close()
        if self.journal is not None:
            self.journal.close()
        self.enabled = False
