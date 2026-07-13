"""
Once-per-cycle indicator snapshot into IndData.

Call `compute_indicators(ind)` once on_bar (or once per decision cycle).
Within that cycle, read atr / adx / ima* etc. from the IndData snapshot —
do not re-invoke the raw indicator modules repeatedly for the same bar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

from .adx import adx
from .atr import atr
from .ma import ma
from .stddev import avg_std, stddev, stddev_of_ma
from ._utils import DEFAULT_SERIES_CAPACITY, write_series

if TYPE_CHECKING:
    from csys.ctypes import IndData

# Periods matching IndData.ima* fields
IMA_PERIODS: Sequence[int] = (5, 14, 30, 60, 120, 240, 500)

DEFAULT_ATR_PERIOD = 14
DEFAULT_ADX_PERIOD = 14
DEFAULT_STD_PERIOD = 20


def compute_indicators(
    ind: "IndData",
    *,
    size: Optional[int] = None,
    ma_method: str = "sma",
    atr_period: int = DEFAULT_ATR_PERIOD,
    adx_period: int = DEFAULT_ADX_PERIOD,
    std_period: int = DEFAULT_STD_PERIOD,
    std_ddof: int = 0,
) -> "IndData":
    """
    Recompute all indicator series on `ind` from its OHLCV snapshot.

    Parameters
    ----------
    ind :
        Populated IndData (open/high/low/close already filled).
    size :
        Output length for every indicator series. Default = number of bars
        currently in IndData (fits the container's rolling window).
    ma_method :
        'sma' | 'ema' | 'wma' for ima* fields.
    atr_period / adx_period / std_period :
        Lookbacks for ATR, ADX/DI, and StdDev.
    std_ddof :
        0 = population std (Bollinger-style), 1 = sample std.

    Returns
    -------
    The same IndData instance (mutated in place) for chaining.
    """
    n = len(ind.close)
    if n == 0:
        return ind

    # Align to bars currently held; never exceed IndData capacity
    out_size = size if size is not None else n
    out_size = min(int(out_size), ind.capacity, n)

    opens = list(ind.open)
    highs = list(ind.high)
    lows = list(ind.low)
    closes = list(ind.close)

    # --- Moving averages on close (ima*) ---
    ima_map = {
        5: ind.ima5,
        14: ind.ima14,
        30: ind.ima30,
        60: ind.ima60,
        120: ind.ima120,
        240: ind.ima240,
        500: ind.ima500,
    }
    for period, target in ima_map.items():
        write_series(
            target,
            ma(closes, period=period, size=out_size, method=ma_method),
        )

    # --- ATR ---
    write_series(
        ind.atr,
        atr(highs, lows, closes, period=atr_period, size=out_size),
    )

    # --- ADX / DI+ / DI- ---
    adx_s, di_p, di_m = adx(
        highs, lows, closes, period=adx_period, size=out_size
    )
    write_series(ind.adx, adx_s)
    write_series(ind.adx_plus, di_p)
    write_series(ind.adx_minus, di_m)

    # --- StdDev on close / open ---
    std_c = stddev(closes, period=std_period, size=out_size, ddof=std_ddof)
    std_o = stddev(opens, period=std_period, size=out_size, ddof=std_ddof)
    write_series(ind.std_close, std_c)
    write_series(ind.std_open, std_o)

    # avg_std: mean of close/open stddev series
    write_series(ind.avg_std, avg_std(std_c, std_o, size=out_size))

    return ind


def compute_ma_series(
    data,
    period: int = 14,
    size: Optional[int] = None,
    method: str = "sma",
):
    """Thin wrapper: raw series → MA series (for ad-hoc use outside IndData)."""
    return ma(data, period=period, size=size, method=method)


def compute_std_of_ma(
    data,
    ma_period: int = 14,
    std_period: int = 20,
    size: Optional[int] = None,
    *,
    ma_method: str = "sma",
    ddof: int = 0,
):
    """StdDev of a moving average of `data` (close, open, or any series)."""
    return stddev_of_ma(
        data,
        ma_period=ma_period,
        std_period=std_period,
        size=size,
        ma_method=ma_method,
        ddof=ddof,
    )


def default_size(ind: "IndData") -> int:
    """Bars currently held, capped by capacity (IndData-fittable size)."""
    return min(len(ind.close), ind.capacity or DEFAULT_SERIES_CAPACITY)
