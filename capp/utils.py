"""
SanUtils — application helpers (mq4 SanUtils subset).

TASK 7: average floating trade profit for strategy profit-close gates.
Not wired into CTraderApp / strategies yet — call sites will pass the result
as ``total_trade_profits`` later.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional, Union


@dataclass(frozen=True)
class PositionPnL:
    """Minimal open-position snapshot for PnL averaging.

    ``net_profit`` = floating P&amp;L in account currency
    (gross + swap + commission in mq4; NetProfit in cBot).
    """

    net_profit: float
    symbol_id: Union[int, str] = 0
    magic_number: Optional[Union[int, str]] = None
    label: str = ""
    position_id: int = 0
    is_open: bool = True


def _as_position_pnl(item: Any) -> PositionPnL:
    """Normalize PositionPnL | mapping | duck-typed object into PositionPnL."""
    if isinstance(item, PositionPnL):
        return item
    if isinstance(item, Mapping):
        return PositionPnL(
            net_profit=float(item.get("net_profit", item.get("profit", 0.0)) or 0.0),
            symbol_id=item.get("symbol_id", item.get("symbol", 0)) or 0,
            magic_number=item.get("magic_number", item.get("magic", None)),
            label=str(item.get("label", "") or ""),
            position_id=int(item.get("position_id", 0) or 0),
            is_open=bool(item.get("is_open", True)),
        )
    net = getattr(item, "net_profit", None)
    if net is None:
        net = getattr(item, "profit", 0.0)
    return PositionPnL(
        net_profit=float(net or 0.0),
        symbol_id=getattr(item, "symbol_id", getattr(item, "symbol", 0)) or 0,
        magic_number=getattr(item, "magic_number", getattr(item, "magic", None)),
        label=str(getattr(item, "label", "") or ""),
        position_id=int(getattr(item, "position_id", 0) or 0),
        is_open=bool(getattr(item, "is_open", True)),
    )


def _matches_identity(
    pos: PositionPnL,
    magic: Optional[Union[int, str]],
    label: Optional[str],
) -> bool:
    """True if position matches magic and/or label filters (when provided)."""
    if magic is None and not label:
        return True

    magic_str = str(magic) if magic is not None else None
    label_filter = label if label else None

    # cBot: magic often stored as Label string
    if label_filter is not None:
        if pos.label == label_filter:
            return True
        if pos.magic_number is not None and str(pos.magic_number) == label_filter:
            return True
        if magic_str is not None and pos.label == magic_str:
            return True
        # Explicit label required but not matched
        if pos.label or pos.magic_number is not None:
            return False
        return False

    # Numeric / string magic only
    if magic_str is not None:
        if pos.magic_number is not None and str(pos.magic_number) == magic_str:
            return True
        if pos.label and pos.label == magic_str:
            return True
        # Position carries no identity — include only if no filter was meaningful
        if pos.magic_number is None and not pos.label:
            return True
        return False

    return True


class SanUtils:
    """
    Application utilities ported from mq4 ``SanUtils``.

    Primary TASK 7 API:
      get_total_trade_profits(...) → average floating PnL of held trades

    design.txt (multi-asset average):
      3 USDJPY + 4 EURUSD open →
        avg = sum(net_profit of all 7) / 7

    Strategy profit-close gates (Strategy_2/3/4) use this as
    ``total_trade_profits >= threshold``.

    Note: mq4 divided by OrdersTotal() (all account orders). This port divides
    by the **count of matching open positions** only.
    """

    def __init__(self, default_magic: Optional[Union[int, str]] = None) -> None:
        self.default_magic = default_magic

    def get_total_trade_profits(
        self,
        positions: Sequence[Any] | Iterable[Any],
        magic_number: Optional[Union[int, str]] = None,
        *,
        symbol_id: Optional[Union[int, str]] = None,
        label: Optional[str] = None,
        all_symbols: bool = True,
    ) -> float:
        """
        Average floating net profit across currently held open trades.

        Parameters
        ----------
        positions :
            Iterable of PositionPnL, dicts, or objects with net_profit /
            symbol_id / magic_number / label.
        magic_number :
            Filter by magic (mq4) or label string (cBot). None → use
            ``default_magic`` if set; else no magic filter.
        symbol_id :
            Used only when ``all_symbols=False`` (mq4 per-chart ``_Symbol``).
        label :
            Optional explicit cTrader label filter.
        all_symbols :
            True (default): average across every matching open trade on
            all symbols (design.txt multi-asset example).
            False: only ``symbol_id`` (required).

        Returns
        -------
        float
            mean net_profit of matching open positions, or 0.0 if none.
        """
        magic = magic_number if magic_number is not None else self.default_magic

        if not all_symbols and symbol_id is None:
            raise ValueError(
                "symbol_id is required when all_symbols=False (mq4 per-symbol mode)"
            )

        total = 0.0
        count = 0

        for raw in positions:
            pos = _as_position_pnl(raw)
            if not pos.is_open:
                continue
            if not _matches_identity(pos, magic, label):
                continue
            if not all_symbols and pos.symbol_id != symbol_id:
                continue

            total += float(pos.net_profit)
            count += 1

        if count <= 0:
            return 0.0
        return total / float(count)

    # mq4 / camelCase alias
    getTotalTradeProfits = get_total_trade_profits

    def get_symbol_trade_profits(
        self,
        positions: Sequence[Any] | Iterable[Any],
        symbol_id: Union[int, str],
        magic_number: Optional[Union[int, str]] = None,
        *,
        label: Optional[str] = None,
    ) -> float:
        """Average floating PnL for one symbol only (mq4 ``_Symbol`` scope)."""
        return self.get_total_trade_profits(
            positions,
            magic_number=magic_number,
            symbol_id=symbol_id,
            label=label,
            all_symbols=False,
        )


_default_utils = SanUtils()


def get_total_trade_profits(
    positions: Sequence[Any] | Iterable[Any],
    magic_number: Optional[Union[int, str]] = None,
    **kwargs: Any,
) -> float:
    """Module-level wrapper around :meth:`SanUtils.get_total_trade_profits`."""
    return _default_utils.get_total_trade_profits(
        positions, magic_number=magic_number, **kwargs
    )
