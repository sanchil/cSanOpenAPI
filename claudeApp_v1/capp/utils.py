"""
SanUtils — application helpers (mq4 SanUtils subset).

TASK 7: average floating trade profit for strategy profit-close gates.
TASK 10: wired from CTraderApp via a sequence of PositionPnL (no network here).

Magic number is optional (mq4-only). cTrader uses position **label**.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional, Union


@dataclass(frozen=True)
class PositionPnL:
    """Minimal open-position snapshot for PnL averaging.

    ``net_profit`` = floating P&amp;L in account currency
    (Open API netUnrealizedPnL, or mq4 profit+swap+commission).
    """

    net_profit: float
    symbol_id: Union[int, str] = 0
    magic_number: Optional[Union[int, str]] = None  # mq4 optional; unused in cTrader
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


def _matches_filters(
    pos: PositionPnL,
    *,
    label: Optional[str],
    magic: Optional[Union[int, str]],
) -> bool:
    """cTrader: filter by label when set. Magic is optional mq4 legacy only."""
    # Label wins (cTrader path)
    if label is not None and label != "":
        return pos.label == label

    # Optional magic filter (mq4); never required
    if magic is not None:
        magic_str = str(magic)
        if pos.magic_number is not None and str(pos.magic_number) == magic_str:
            return True
        if pos.label == magic_str:
            return True
        return False

    return True  # no filter → include all open


class SanUtils:
    """
    Pure helpers — no Open API / network.

    Primary API:
      get_total_trade_profits(positions) → mean floating PnL of open trades

    design.txt multi-asset average:
      3 USDJPY + 4 EURUSD → sum(profits) / 7

    cTrader: pass ``label=`` (e.g. GrokApp). Magic is optional mq4-only.
    """

    def __init__(
        self,
        default_label: Optional[str] = None,
        default_magic: Optional[Union[int, str]] = None,
    ) -> None:
        self.default_label = default_label
        # mq4 only — optional, not used by cTrader Open API
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
            Sequence of PositionPnL (or dicts / duck-types).
        magic_number :
            Optional mq4 magic filter. Ignored when ``label`` is set.
            cTrader has no magic — prefer label.
        label :
            cTrader position label filter (preferred).
        symbol_id / all_symbols :
            Per-symbol vs multi-asset average (default multi-asset).
        """
        use_label = label if label is not None else self.default_label
        use_magic = None
        if use_label is None or use_label == "":
            use_magic = (
                magic_number if magic_number is not None else self.default_magic
            )

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
            if not _matches_filters(pos, label=use_label, magic=use_magic):
                continue
            if not all_symbols and pos.symbol_id != symbol_id:
                continue
            total += float(pos.net_profit)
            count += 1

        if count <= 0:
            return 0.0
        return total / float(count)

    getTotalTradeProfits = get_total_trade_profits

    def get_symbol_trade_profits(
        self,
        positions: Sequence[Any] | Iterable[Any],
        symbol_id: Union[int, str],
        magic_number: Optional[Union[int, str]] = None,
        *,
        label: Optional[str] = None,
    ) -> float:
        """Average floating PnL for one symbol only."""
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
