"""
TradeJournal — real-trade quality record for the trade log (V1-T36).

Per real position (one per symbol under the executor policy):
  * fills: broker fill price vs. the decision reference quote → slippage
    in pips (positive = worse than the reference), spread at decision,
    commission on each deal
  * close: gross profit, swap, commissions and net from the broker's close
    detail; realized pips from entry / exit fill prices
  * excursion: best (MFE) and worst (MAE) unrealized pips over the closed
    bars the position lived through (bar high / low), bars held
  * exit reason: opposite signal, CLOSE with the active strategy's driving
    T_SIG field (e.g. peak_trough_sig=SIDEWAYS → flat market), or
    "external" for closes this app did not request (manual, stop-out)

Flow: CTraderApp calls on_decision() before OrderExecutor.handle_signal
(records the intent and the reference quote, advances MFE / MAE with the
bar that just closed), then on_fill() for each broker deal. In dry-run
there are no broker deals; synthetic_fill() fills at the reference quote
(slippage 0, money fields NaN). Reference quote: ask for buys, bid for
sells, else the closed bar's close ("bar", includes half the spread).
"""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from csys.ctypes import SIG, T_SIG

NAN = float("nan")

# T_SIG field that drives each strategy's CLOSE (for the exit reason)
STRATEGY_DRIVERS = {
    1: "micro_wave_sig", 2: "base_slope_sig", 3: "base_slope_sig", 4: "slope30_sig",
    5: "slope30_sig", 6: "base_slope_sig", 7: "base_slope_sig_v1", 8: "peak_trough_sig",
    9: "base_slope_sig_v2",
}
_CAUSE = {SIG.SIDEWAYS: "flat market", SIG.CLOSE: "deceleration / stall"}


@dataclass
class _Intent:
    kind: str            # "open" | "close"
    side: SIG            # side of the deal this intent will produce
    ref_price: float
    ref_src: str         # "ask" | "bid" | "bar"
    spread_pips: float
    reason: str
    time: Optional[datetime]


@dataclass
class TradeRecord:
    """One real (or dry-run) position, open → close."""

    symbol_id: int
    symbol: str
    position_id: int
    side: str
    volume: float
    entry_time: str = ""
    entry_price: float = NAN
    entry_ref: float = NAN
    entry_ref_src: str = ""
    entry_slip_pips: float = NAN
    entry_spread_pips: float = NAN
    entry_commission: float = NAN
    entry_reason: str = ""
    exit_time: str = ""
    exit_price: float = NAN
    exit_ref: float = NAN
    exit_ref_src: str = ""
    exit_slip_pips: float = NAN
    exit_spread_pips: float = NAN
    exit_commission: float = NAN
    exit_reason: str = ""
    pips: float = NAN
    gross: float = NAN
    swap: float = NAN
    commission: float = NAN
    net: float = NAN
    bars: int = 0
    mfe_pips: float = 0.0
    mae_pips: float = 0.0
    dry_run: bool = False


def _slip(deal_side: SIG, fill: float, ref: float, pip: float) -> float:
    """Adverse slippage in pips: buys filled above ref, sells below, are > 0."""
    if not (pip > 0 and ref == ref and fill == fill):
        return NAN
    d = (fill - ref) if deal_side == SIG.BUY else (ref - fill)
    return d / pip


def _nan_sum(*xs: float) -> float:
    vals = [x for x in xs if x == x]
    return sum(vals) if vals else NAN


@dataclass
class _PeriodStats:
    trades: int = 0
    wins: int = 0
    pips: float = 0.0
    net: float = 0.0
    money_n: int = 0
    slip: List[float] = field(default_factory=list)
    spread: List[float] = field(default_factory=list)
    mfe: float = 0.0
    mae: float = 0.0
    bars: int = 0


class TradeJournal:
    """Matches decisions to fills and keeps per-trade quality metrics."""

    def __init__(
        self,
        *,
        csv_dir: Optional[str] = None,
        rotation: Any = "day",
        name_of: Optional[Callable[[int], str]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.csv_dir, self.rotation = csv_dir, rotation
        self.name_of = name_of
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.pips: Dict[int, float] = {}
        self.intents: Dict[int, _Intent] = {}
        self.open: Dict[int, TradeRecord] = {}
        self.period = _PeriodStats()
        self._csv_key = None
        self._fh = None
        self._writer = None

    # ---------------------------------------------------------- decisions
    def on_decision(
        self,
        *,
        symbol_id: int,
        trade_sig: SIG,
        position_side: Optional[SIG],
        bar_high: float,
        bar_low: float,
        bar_close: float,
        pip: float,
        quote: Optional[Tuple[float, float]],
        t_sig: Optional[T_SIG],
        active: int,
        when: Optional[datetime],
    ) -> None:
        """Advance excursion with the bar that just closed, then record what
        the executor policy will do with ``trade_sig``."""
        if pip > 0:
            self.pips[symbol_id] = pip
        self._advance(symbol_id, bar_high, bar_low)

        flat = position_side not in (SIG.BUY, SIG.SELL)
        if flat and trade_sig in (SIG.BUY, SIG.SELL):
            kind, deal_side = "open", trade_sig
            reason = f"{trade_sig.name} ({self._driver(t_sig, active)})"
        elif not flat and (trade_sig == SIG.CLOSE or (trade_sig in (SIG.BUY, SIG.SELL) and trade_sig != position_side)):
            kind = "close"
            deal_side = SIG.SELL if position_side == SIG.BUY else SIG.BUY
            if trade_sig == SIG.CLOSE:
                reason = f"CLOSE ({self._driver(t_sig, active, cause=True)})"
            else:
                reason = f"opposite {trade_sig.name}"
        else:
            self.intents.pop(symbol_id, None)
            return

        ref, src, spread = bar_close, "bar", NAN
        if quote and quote[0] > 0 and quote[1] > 0:
            bid, ask = quote
            ref, src = (ask, "ask") if deal_side == SIG.BUY else (bid, "bid")
            spread = (ask - bid) / pip if pip > 0 else NAN
        self.intents[symbol_id] = _Intent(kind, deal_side, ref, src, spread, reason, when)

    @staticmethod
    def _driver(t_sig: Optional[T_SIG], active: int, cause: bool = False) -> str:
        name = STRATEGY_DRIVERS.get(active)
        if t_sig is None or name is None:
            return f"Strategy_{active}"
        v = getattr(t_sig, name, SIG.NOSIG)
        txt = f"{name}={v.name}"
        if cause and v in _CAUSE:
            txt += f": {_CAUSE[v]}"
        return txt

    def _advance(self, symbol_id: int, high: float, low: float) -> None:
        tr = self.open.get(symbol_id)
        pip = self.pips.get(symbol_id, 0.0)
        if tr is None or not (pip > 0 and high == high and low == low and tr.entry_price == tr.entry_price):
            return
        tr.bars += 1
        if tr.side == "BUY":
            fav, adv = (high - tr.entry_price) / pip, (low - tr.entry_price) / pip
        else:
            fav, adv = (tr.entry_price - low) / pip, (tr.entry_price - high) / pip
        tr.mfe_pips = max(tr.mfe_pips, fav)
        tr.mae_pips = min(tr.mae_pips, adv)

    # -------------------------------------------------------------- fills
    def on_fill(self, fill: Any) -> Tuple[List[str], Optional[TradeRecord]]:
        """Apply a csys DealFill. Returns (log lines, closed record or None)."""
        sid = fill.symbol_id
        pip = self.pips.get(sid, 0.0)
        intent = self.intents.get(sid)
        name = self._name(sid)
        when = fill.time.isoformat(sep=" ") if fill.time else ""

        if not fill.is_close:
            ref = intent if intent and intent.kind == "open" else None
            if ref:
                self.intents.pop(sid, None)
            tr = self.open.get(sid)
            if tr is not None and tr.position_id == fill.position_id:      # partial fill: average in
                total = tr.volume + fill.volume
                tr.entry_price = (tr.entry_price * tr.volume + fill.price * fill.volume) / total if total else fill.price
                tr.volume = total
                tr.entry_commission = _nan_sum(tr.entry_commission, fill.commission)
            else:
                tr = TradeRecord(
                    symbol_id=sid, symbol=name, position_id=fill.position_id, side=fill.side.name,
                    volume=fill.volume, entry_time=when, entry_price=fill.price,
                    entry_ref=ref.ref_price if ref else NAN, entry_ref_src=ref.ref_src if ref else "",
                    entry_slip_pips=_slip(fill.side, fill.price, ref.ref_price, pip) if ref else NAN,
                    entry_spread_pips=ref.spread_pips if ref else NAN,
                    entry_commission=fill.commission,
                    entry_reason=ref.reason if ref else "external (not requested by this app)",
                )
                self.open[sid] = tr
            line = (f"FILL [{name}] OPEN {fill.side.name} {fill.volume:g} @ {fill.price:.5f} "
                    f"ref={tr.entry_ref:.5f}({tr.entry_ref_src or '-'}) slip={tr.entry_slip_pips:+.1f}p "
                    f"spread={tr.entry_spread_pips:.1f}p comm={fill.commission:.2f} pos={fill.position_id}")
            return [line], None

        ref = intent if intent and intent.kind == "close" else None
        if ref:
            self.intents.pop(sid, None)
        tr = self.open.pop(sid, None)
        if tr is None or (tr.position_id and fill.position_id and tr.position_id != fill.position_id):
            # Position opened before this run (reconciled) or not ours
            pos_side = "SELL" if fill.side == SIG.BUY else "BUY"
            tr = TradeRecord(symbol_id=sid, symbol=name, position_id=fill.position_id, side=pos_side,
                             volume=fill.volume, entry_price=fill.entry_price,
                             entry_reason="unknown (opened before this run)")
        tr.exit_time = when
        tr.exit_price = fill.price
        tr.exit_ref = ref.ref_price if ref else NAN
        tr.exit_ref_src = ref.ref_src if ref else ""
        tr.exit_slip_pips = _slip(fill.side, fill.price, ref.ref_price, pip) if ref else NAN
        tr.exit_spread_pips = ref.spread_pips if ref else NAN
        tr.exit_commission = fill.commission
        tr.exit_reason = ref.reason if ref else "external (not requested by this app)"
        entry = tr.entry_price if tr.entry_price == tr.entry_price else fill.entry_price
        if pip > 0 and entry == entry:
            tr.pips = ((fill.price - entry) if tr.side == "BUY" else (entry - fill.price)) / pip
        tr.gross, tr.swap = fill.gross_profit, fill.swap
        # Deal commissions (open + close); closePositionDetail.commission is
        # not added on top to avoid counting the close leg twice.
        tr.commission = _nan_sum(tr.entry_commission, fill.commission)
        tr.net = _nan_sum(tr.gross, tr.swap, tr.commission) if tr.gross == tr.gross else NAN
        self._close(tr)
        return [self.close_line(tr)], tr

    def synthetic_fill(self, symbol_id: int, action: str, volume: float = 0.0) -> Tuple[List[str], Optional[TradeRecord]]:
        """Dry-run: fill the pending intent at its reference quote."""
        intent = self.intents.get(symbol_id)
        if intent is None or intent.kind != action:
            return [], None
        fill = _DryFill(
            symbol_id=symbol_id, position_id=-symbol_id, side=intent.side, volume=volume,
            price=intent.ref_price, time=intent.time, is_close=(action == "close"),
        )
        lines, rec = self.on_fill(fill)
        if action == "open" and symbol_id in self.open:
            self.open[symbol_id].dry_run = True
        return lines, rec

    # ------------------------------------------------------------ output
    def close_line(self, tr: TradeRecord) -> str:
        return (
            f"TRADE [{tr.symbol}] CLOSED {tr.side} pos={tr.position_id} entry={tr.entry_price:.5f} "
            f"exit={tr.exit_price:.5f} pips={tr.pips:+.1f} gross={tr.gross:.2f} swap={tr.swap:.2f} "
            f"comm={tr.commission:.2f} net={tr.net:.2f} bars={tr.bars} MFE={tr.mfe_pips:+.1f}p "
            f"MAE={tr.mae_pips:+.1f}p slip(in/out)={tr.entry_slip_pips:+.1f}/{tr.exit_slip_pips:+.1f}p "
            f"spread(in/out)={tr.entry_spread_pips:.1f}/{tr.exit_spread_pips:.1f}p "
            f"in: {tr.entry_reason} | out: {tr.exit_reason}{' [dry_run]' if tr.dry_run else ''}"
        )

    def _close(self, tr: TradeRecord) -> None:
        p = self.period
        p.trades += 1
        p.wins += (tr.pips > 0) if tr.pips == tr.pips else 0
        p.pips += tr.pips if tr.pips == tr.pips else 0.0
        if tr.net == tr.net:
            p.net += tr.net
            p.money_n += 1
        p.slip += [x for x in (tr.entry_slip_pips, tr.exit_slip_pips) if x == x]
        p.spread += [x for x in (tr.entry_spread_pips, tr.exit_spread_pips) if x == x]
        p.mfe += tr.mfe_pips
        p.mae += tr.mae_pips
        p.bars += tr.bars
        self._write_csv(tr)

    def summary_line(self) -> Optional[str]:
        p = self.period
        if not p.trades and not self.open:
            return None
        n = p.trades or 1
        avg = lambda xs: sum(xs) / len(xs) if xs else NAN
        opened = ", ".join(f"{t.symbol} {t.side} bars={t.bars} MFE={t.mfe_pips:+.1f} MAE={t.mae_pips:+.1f}"
                           for t in self.open.values()) or "-"
        return (
            f"  Real trades: {p.trades} win={100.0 * p.wins / n:.1f}% "
            f"net={f'{p.net:+.2f}' if p.money_n else 'n/a'} pips={p.pips:+.1f} "
            f"avg_slip={avg(p.slip):+.2f}p avg_spread={avg(p.spread):.2f}p avg_MFE={p.mfe / n:+.1f}p "
            f"avg_MAE={p.mae / n:+.1f}p avg_bars={p.bars / n:.1f} open: {opened}"
        )

    def reset_period(self) -> None:
        self.period = _PeriodStats()

    def _name(self, sid: int) -> str:
        return self.name_of(sid) if self.name_of else str(sid)

    def _write_csv(self, tr: TradeRecord) -> None:
        if not self.csv_dir:
            return
        from capp.tradelog.agent import period_label, period_start  # avoid import cycle

        key = period_start(self.now(), self.rotation)
        if key != self._csv_key:
            self.close()
            os.makedirs(self.csv_dir, exist_ok=True)
            path = os.path.join(self.csv_dir, f"trades_{period_label(key, self.rotation)}.csv")
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            self._fh = open(path, "a", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._fh, fieldnames=list(asdict(tr).keys()))
            if new:
                self._writer.writeheader()
            self._csv_key = key
        self._writer.writerow(asdict(tr))
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._writer = None


@dataclass
class _DryFill:
    """Minimal DealFill stand-in for dry-run fills."""

    symbol_id: int
    position_id: int
    side: SIG
    volume: float
    price: float
    time: Optional[datetime]
    is_close: bool
    commission: float = NAN
    entry_price: float = NAN
    gross_profit: float = NAN
    swap: float = NAN
