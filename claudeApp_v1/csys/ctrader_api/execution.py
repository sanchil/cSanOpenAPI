"""
Order execution framework (design.txt TASK 2).

Policy (single trade at a time — no pyramid, no SL/TP):
  * BUY / SELL while flat      → open one market order
  * BUY / SELL while same side → do nothing (already in trade)
  * Opposite signal            → close the open position (no reverse open same cycle)
  * CLOSE signal               → close the open position
  * Never attach stop-loss or take-profit

Cues from PhyBot.cs / mq4 placeOrder, simplified to max 1 position per symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from twisted.internet import defer

from .ctypes import SIG

if TYPE_CHECKING:
    from .client import CTraderOpenAPI

# ProtoOATradeSide
_SIDE_BUY = 1
_SIDE_SELL = 2

# ProtoOAPositionStatus
_STATUS_OPEN = 1
_STATUS_CLOSED = 2

# ProtoOAExecutionType
_EXEC_FILLED = 3
_EXEC_PARTIAL = 11


@dataclass
class OpenPosition:
    """Local snapshot of a single open position for one symbol."""

    position_id: int
    symbol_id: int
    side: SIG  # BUY or SELL
    volume: float  # high-level units (protocol_volume / 100)
    label: str = ""
    open_timestamp: int = 0
    entry_price: float = 0.0

    @property
    def trade_side_str(self) -> str:
        return "BUY" if self.side == SIG.BUY else "SELL"


@dataclass
class ExecutionResult:
    """Outcome of handle_signal for logging / tests."""

    action: str  # "none" | "open" | "close" | "skipped" | "error"
    symbol_id: int
    signal: SIG
    detail: str = ""
    position: Optional[OpenPosition] = None
    deferred: Any = field(default=None, repr=False)


class OrderExecutor:
    """
    Single-position market order executor.

    Parameters
    ----------
    api :
        Live CTraderOpenAPI (or a test double with the same methods).
    volume :
        Order size in symbol units (e.g. 10000 ≈ 0.1 lot on standard FX).
    label :
        Order label used to identify our positions on reconcile.
    dry_run :
        If True, only update local state / log — do not send network requests.
    enabled :
        Master switch; when False, handle_signal is a no-op.
    """

    def __init__(
        self,
        api: Optional["CTraderOpenAPI"] = None,
        *,
        volume: float = 10000.0,
        label: str = "GrokApp",
        dry_run: bool = False,
        enabled: bool = True,
    ) -> None:
        self.api = api
        self.volume = float(volume)
        self.label = str(label)
        self.dry_run = bool(dry_run)
        self.enabled = bool(enabled)

        # symbol_id → open position (at most one)
        self.positions: Dict[int, OpenPosition] = {}
        # symbols with an in-flight open/close request
        self._pending: set[int] = set()

        self.on_action: Optional[Callable[[ExecutionResult], None]] = None

    # ------------------------------------------------------------------ state
    def has_position(self, symbol_id: int) -> bool:
        return int(symbol_id) in self.positions

    def get_position(self, symbol_id: int) -> Optional[OpenPosition]:
        return self.positions.get(int(symbol_id))

    def position_side(self, symbol_id: int) -> SIG:
        pos = self.get_position(symbol_id)
        return pos.side if pos else SIG.NOSIG

    def clear(self) -> None:
        self.positions.clear()
        self._pending.clear()

    # ------------------------------------------------------------------ sync
    def sync_from_reconcile(self, reconcile_res: Any) -> Dict[int, OpenPosition]:
        """
        Replace local book from ProtoOAReconcileRes.

        Keeps only OPEN positions matching our label (or empty label filter).
        Enforces single-position-per-symbol (last one wins if broker has more).
        """
        self.positions.clear()
        for pos in getattr(reconcile_res, "position", []) or []:
            parsed = self._parse_position(pos)
            if parsed is None:
                continue
            # Single-trade policy: one slot per symbol
            self.positions[parsed.symbol_id] = parsed
        return dict(self.positions)

    def sync_positions(self) -> defer.Deferred:
        """Network reconcile → update local positions. No-op in dry_run without api."""
        if self.dry_run or self.api is None:
            return defer.succeed(dict(self.positions))

        def _ok(res):
            return self.sync_from_reconcile(res)

        return self.api.reconcile(client_msg_id="exec_reconcile").addCallback(_ok)

    # ----------------------------------------------------------- event ingest
    def on_execution_event(self, message: Any) -> None:
        """Update local state from ProtoOAExecutionEvent (filled / closed)."""
        exec_type = getattr(message, "executionType", None)
        position = getattr(message, "position", None)
        if position is None:
            return

        parsed = self._parse_position(position, require_label=False)
        if parsed is None:
            return

        status = getattr(position, "positionStatus", None)
        sid = parsed.symbol_id

        if status == _STATUS_CLOSED or (
            exec_type == _EXEC_FILLED and status == _STATUS_CLOSED
        ):
            self.positions.pop(sid, None)
            self._pending.discard(sid)
            print(f"[EXEC] Position closed symbol={sid} id={parsed.position_id}")
            return

        if exec_type in (_EXEC_FILLED, _EXEC_PARTIAL) or status == _STATUS_OPEN:
            # Only track our label when known
            label = parsed.label or ""
            if label and label != self.label:
                return
            self.positions[sid] = parsed
            self._pending.discard(sid)
            print(
                f"[EXEC] Position open/updated symbol={sid} "
                f"id={parsed.position_id} side={parsed.side} vol={parsed.volume}"
            )

    # -------------------------------------------------------------- main API
    def handle_signal(self, symbol_id: int, signal: SIG) -> defer.Deferred:
        """
        Apply TASK 2 policy for one symbol / signal (once per bar cycle).

        Returns a Deferred firing an ExecutionResult.
        """
        symbol_id = int(symbol_id)
        result = ExecutionResult(action="none", symbol_id=symbol_id, signal=signal)

        if not self.enabled:
            result.action = "skipped"
            result.detail = "executor disabled"
            return self._finish(result)

        if symbol_id in self._pending:
            result.action = "skipped"
            result.detail = "order already pending for symbol"
            return self._finish(result)

        pos = self.positions.get(symbol_id)

        # --- Close path ---
        if pos is not None:
            if signal == SIG.CLOSE or self._is_opposite(pos.side, signal):
                return self._close(pos, signal, result)
            # Same-side BUY/SELL or HOLD/NOSIG/etc. → hold position, no new order
            result.action = "none"
            result.detail = f"already in {pos.side.name}; no new order"
            result.position = pos
            return self._finish(result)

        # --- Open path (flat only) ---
        if signal in (SIG.BUY, SIG.SELL):
            return self._open(symbol_id, signal, result)

        result.action = "none"
        result.detail = f"flat; signal={signal.name} — no action"
        return self._finish(result)

    # ---------------------------------------------------------------- internal
    @staticmethod
    def _is_opposite(position_side: SIG, signal: SIG) -> bool:
        return (position_side == SIG.BUY and signal == SIG.SELL) or (
            position_side == SIG.SELL and signal == SIG.BUY
        )

    def _open(self, symbol_id: int, signal: SIG, result: ExecutionResult) -> defer.Deferred:
        side = "BUY" if signal == SIG.BUY else "SELL"
        result.action = "open"
        result.detail = f"market {side} volume={self.volume} label={self.label}"

        if self.dry_run or self.api is None:
            # Simulate fill for offline tests
            pos = OpenPosition(
                position_id=-(symbol_id),  # synthetic id
                symbol_id=symbol_id,
                side=signal,
                volume=self.volume,
                label=self.label,
            )
            self.positions[symbol_id] = pos
            result.position = pos
            result.detail += " [dry_run]"
            print(f"[EXEC] OPEN {side} symbol={symbol_id} vol={self.volume} (dry_run)")
            return self._finish(result)

        self._pending.add(symbol_id)

        def _ok(res):
            self._pending.discard(symbol_id)
            # Optimistic local state until execution event / reconcile arrives
            if symbol_id not in self.positions:
                self.positions[symbol_id] = OpenPosition(
                    position_id=0,
                    symbol_id=symbol_id,
                    side=signal,
                    volume=self.volume,
                    label=self.label,
                )
            result.position = self.positions.get(symbol_id)
            print(f"[EXEC] OPEN sent {side} symbol={symbol_id} vol={self.volume}")
            return result

        def _err(failure):
            self._pending.discard(symbol_id)
            result.action = "error"
            result.detail = f"open failed: {failure.getErrorMessage()}"
            print(f"[EXEC] OPEN error symbol={symbol_id}: {result.detail}")
            return result

        d = self.api.new_market_order(
            symbol_id,
            side,
            self.volume,
            client_msg_id=f"open_{symbol_id}_{side}",
            label=self.label,
            comment="TASK2 single-trade",
        )
        result.deferred = d
        d.addCallbacks(_ok, _err)
        d.addCallback(lambda r: self._emit(r))
        return d

    def _close(
        self,
        pos: OpenPosition,
        signal: SIG,
        result: ExecutionResult,
    ) -> defer.Deferred:
        reason = "CLOSE" if signal == SIG.CLOSE else f"opposite ({signal.name})"
        result.action = "close"
        result.detail = (
            f"close pos={pos.position_id} side={pos.side.name} "
            f"vol={pos.volume} reason={reason}"
        )
        result.position = pos
        symbol_id = pos.symbol_id

        if self.dry_run or self.api is None:
            self.positions.pop(symbol_id, None)
            result.detail += " [dry_run]"
            print(f"[EXEC] CLOSE symbol={symbol_id} ({reason}) (dry_run)")
            return self._finish(result)

        # position_id 0 means we only have optimistic state — reconcile first
        if not pos.position_id:
            print(f"[EXEC] CLOSE deferred — unknown position_id, reconciling…")
            d = self.sync_positions()

            def _after_sync(_):
                real = self.positions.get(symbol_id)
                if real is None or not real.position_id:
                    result.action = "skipped"
                    result.detail = "no broker position to close after reconcile"
                    return self._finish(result)
                return self._close(real, signal, result)

            return d.addCallback(_after_sync)

        self._pending.add(symbol_id)

        def _ok(res):
            self._pending.discard(symbol_id)
            self.positions.pop(symbol_id, None)
            print(f"[EXEC] CLOSE sent symbol={symbol_id} pos={pos.position_id} ({reason})")
            return result

        def _err(failure):
            self._pending.discard(symbol_id)
            result.action = "error"
            result.detail = f"close failed: {failure.getErrorMessage()}"
            print(f"[EXEC] CLOSE error symbol={symbol_id}: {result.detail}")
            return result

        d = self.api.close_position(
            pos.position_id,
            pos.volume,
            client_msg_id=f"close_{symbol_id}_{pos.position_id}",
        )
        result.deferred = d
        d.addCallbacks(_ok, _err)
        d.addCallback(lambda r: self._emit(r))
        return d

    def _parse_position(self, pos: Any, require_label: bool = True) -> Optional[OpenPosition]:
        status = getattr(pos, "positionStatus", _STATUS_OPEN)
        if status == _STATUS_CLOSED:
            return None

        trade = getattr(pos, "tradeData", None)
        if trade is None:
            return None

        label = getattr(trade, "label", "") or ""
        if require_label and self.label and label and label != self.label:
            return None
        if require_label and self.label and not label:
            # Unlabeled positions ignored when we filter by label
            return None

        side_raw = getattr(trade, "tradeSide", 0)
        if side_raw == _SIDE_BUY:
            side = SIG.BUY
        elif side_raw == _SIDE_SELL:
            side = SIG.SELL
        else:
            return None

        protocol_vol = int(getattr(trade, "volume", 0) or 0)
        return OpenPosition(
            position_id=int(getattr(pos, "positionId", 0) or 0),
            symbol_id=int(getattr(trade, "symbolId", 0) or 0),
            side=side,
            volume=protocol_vol / 100.0,
            label=label,
            open_timestamp=int(getattr(trade, "openTimestamp", 0) or 0),
            entry_price=float(getattr(pos, "price", 0.0) or 0.0),
        )

    def _finish(self, result: ExecutionResult) -> defer.Deferred:
        self._emit(result)
        return defer.succeed(result)

    def _emit(self, result: ExecutionResult) -> ExecutionResult:
        if self.on_action:
            try:
                self.on_action(result)
            except Exception as e:
                print(f"[EXEC] on_action error: {e}")
        return result
