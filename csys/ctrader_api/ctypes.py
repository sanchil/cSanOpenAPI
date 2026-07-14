"""
Common types module (CTypes.cs / mq4 equivalent).

Single home for:
  - enums: SIG, DECAY_STRATEGY
  - value types: DTYPE, T_SIG
  - market container: IndData
  - price/trendbar helpers used by the Open API layer

Prefer:
    from csys.ctypes import SIG, IndData, DTYPE, T_SIG
or:
    from csys import SIG, IndData, DTYPE, T_SIG
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# cTrader relative price scale: prices are in 1/100000 of a price unit.
PRICE_SCALE = 100_000
DEFAULT_BAR_CAPACITY = 500

# Bar period name → minutes (PhyBot GetMinutes / mq4 _Period style)
PERIOD_MINUTES: Dict[str, int] = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M10": 10,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "H12": 720,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
}


# ---------------------------------------------------------------------------
# Symbol meta (from ProtoOASymbol — Open API has digits/pipPosition, not PipSize)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolMeta:
    """Per-symbol trading meta derived from Open API ProtoOASymbol.

    cTrader Automate maps:
      PipSize  ← pip_size   (price length of one pip)
      TickSize ← point      (min price increment)
      Digits   ← digits
      PipValue ← pip_value  (approx money/pip; 0 if unknown)
    """

    symbol_id: int
    name: str = ""
    digits: int = 5
    pip_position: int = 4
    pip_size: float = 0.0001
    point: float = 0.00001
    lot_size: int = 0  # protocol cents-of-units for 1 lot
    pip_value: float = 0.0  # deposit/quote approximation; may be 0
    min_volume: float = 0.0  # high-level units
    step_volume: float = 0.0

    @property
    def dbl_epsilon(self) -> float:
        return self.point * 0.1 if self.point > 0 else 1e-10


def period_minutes(period: str) -> int:
    """Map ProtoOATrendbarPeriod name (e.g. M1) → minutes (cBot GetMinutes)."""
    return PERIOD_MINUTES.get(str(period).upper(), 1)


def period_seconds(period: str) -> int:
    """Timeframe length in seconds (design.txt: M1 → 60)."""
    return period_minutes(period) * 60


def symbol_meta_from_proto(sym: Any, name: str = "") -> SymbolMeta:
    """
    Build SymbolMeta from a ProtoOASymbol (full) message.

    Open API does not expose PipSize/PipValue; derive:
      point    = 10^(-digits)
      pip_size = 10^(-pipPosition) if pipPosition > 0 else point
      pip_value ≈ pip_size * (lotSize/100)  # FX quote-ccy approx per 1 lot
    """
    symbol_id = int(getattr(sym, "symbolId", 0) or 0)
    digits = int(getattr(sym, "digits", 5) or 5)
    pip_position = int(getattr(sym, "pipPosition", 0) or 0)
    lot_size = int(getattr(sym, "lotSize", 0) or 0)
    min_vol = int(getattr(sym, "minVolume", 0) or 0) / 100.0
    step_vol = int(getattr(sym, "stepVolume", 0) or 0) / 100.0

    digits = max(0, min(digits, 12))
    point = 10.0 ** (-digits) if digits > 0 else 1.0

    if pip_position > 0:
        pip_position = max(0, min(pip_position, 12))
        pip_size = 10.0 ** (-pip_position)
    else:
        # Fallback: 5-digit style (pip = 10 * point) or point itself
        pip_size = point * 10.0 if digits >= 3 else point

    # Approximate pip value in quote units for 1 lot (Open API has no deposit conversion here)
    pip_value = 0.0
    if lot_size > 0 and pip_size > 0:
        # lotSize is volume of 1 lot in cents of units → units = lotSize/100
        pip_value = pip_size * (lot_size / 100.0)

    sym_name = name or str(getattr(sym, "symbolName", "") or getattr(sym, "name", "") or "")

    return SymbolMeta(
        symbol_id=symbol_id,
        name=sym_name,
        digits=digits,
        pip_position=pip_position,
        pip_size=pip_size,
        point=point,
        lot_size=lot_size,
        pip_value=pip_value,
        min_volume=min_vol,
        step_volume=step_vol,
    )


def apply_symbol_meta(
    ind: "IndData",
    meta: SymbolMeta,
    *,
    period_secs: Optional[int] = None,
    period_mins: Optional[int] = None,
) -> "IndData":
    """Copy SymbolMeta fields onto IndData (PhyBot InitIndData parity).

    Parameters
    ----------
    period_secs :
        Bar timeframe length in **seconds** (design.txt: M1 → 60).
        Preferred for IndData.period / current_period.
    period_mins :
        Legacy alias; converted to seconds if period_secs is omitted.
    """
    ind.symbol_id = meta.symbol_id
    if meta.name:
        ind.symbol_name = meta.name
    # PhyBot: Digits, PipSize, PipValue, Point(=TickSize), DBL_EPSILON
    ind.digits = meta.digits
    ind.pip_position = meta.pip_position
    ind.pip_size = meta.pip_size
    ind.pip_value = meta.pip_value
    ind.point = meta.point  # TickSize
    ind.dbl_epsilon = meta.dbl_epsilon
    # design.txt: M1 period = 60 seconds (not MQL enum 1)
    if period_secs is None:
        period_secs = int(period_mins) * 60 if period_mins is not None else 60
    ind.period = int(period_secs)
    ind.current_period = float(period_secs)
    ind.bars_held = 0
    return ind


# ---------------------------------------------------------------------------
# Enums  (cenums / CTypes.cs)
# ---------------------------------------------------------------------------

class SIG(Enum):
    """Trading signals — equivalent to C# SIG / MQL SAN_SIGNAL."""

    HOLD = 101
    BUY = 102
    SELL = 103
    CLOSE = 104
    TRADE = 105
    NOTRADE = 106
    SIDEWAYS = 107
    NOSIG = 108

    def is_long(self) -> bool:
        return self == SIG.BUY

    def is_short(self) -> bool:
        return self == SIG.SELL

    def is_neutral(self) -> bool:
        return self in (SIG.HOLD, SIG.NOSIG, SIG.NOTRADE, SIG.SIDEWAYS)

    def is_directional(self) -> bool:
        return self in (SIG.BUY, SIG.SELL)

    def __str__(self) -> str:
        return self.name


class DECAY_STRATEGY(Enum):
    """Adaptive decay source selection (CTypes.cs DECAY_STRATEGY)."""

    STRAT_ATR = 0  # Volatility fuel
    STRAT_ADX = 1  # Trend quality
    STRAT_ER = 2  # Efficiency ratio
    STRAT_MIX = 3  # Weighted mix (0.5 ATR + 0.3 ADX + 0.2 ER)


# ---------------------------------------------------------------------------
# Value types  (DTYPE / T_SIG)
# ---------------------------------------------------------------------------

@dataclass
class DTYPE:
    """Generic multi-value transport (C#/MQL DTYPE).

    Used by slopeVal / computeStencilKinematics:
      val1 — primary (fast slope / velocity)
      val2 — secondary (wide slope / acceleration)
      val3 — tertiary (acceleration / momentum ratio)
      val4, val5 — spare
    """

    val1: float = float("nan")
    val2: float = float("nan")
    val3: float = float("nan")
    val4: float = float("nan")
    val5: float = float("nan")

    def clear(self) -> None:
        self.val1 = self.val2 = self.val3 = self.val4 = self.val5 = float("nan")


@dataclass
class T_SIG:
    """Tactical signal bundle for one IndData snapshot / cycle.

    Populated once per bar by SanSignals.init_sig(); consumed by capp.strategies.
    """

    # Core path from design.txt / HSIG::initSIG
    base_slope_sig: SIG = SIG.NOSIG
    slope30_sig: SIG = SIG.NOSIG
    fast_sig: SIG = SIG.NOSIG
    fsig5: SIG = SIG.NOSIG
    fsig14: SIG = SIG.NOSIG
    fsig30: SIG = SIG.NOSIG
    fsig60: SIG = SIG.NOSIG
    fsig120: SIG = SIG.NOSIG
    fsig240: SIG = SIG.NOSIG
    fsig500: SIG = SIG.NOSIG

    # Extended slots used by strategies / future ports
    micro_wave_sig: SIG = SIG.NOSIG
    macro_wave_sig: SIG = SIG.NOSIG
    wave_tide_sig: SIG = SIG.NOSIG
    trade_slope_sig: SIG = SIG.NOSIG
    vol_momentum_sig: SIG = SIG.NOSIG
    candle_vol_sig: SIG = SIG.NOSIG
    slope_analyzer_sig: SIG = SIG.NOSIG
    open_sig: SIG = SIG.NOSIG
    close_sig: SIG = SIG.NOSIG
    fuse_fast_sig: SIG = SIG.NOSIG
    fuse_slow_sig: SIG = SIG.NOSIG

    # Final strategy output for this cycle
    strategy_sig: SIG = SIG.NOSIG

    # Accompanying slope payloads (optional diagnostics)
    base_slope_data: DTYPE = field(default_factory=DTYPE)
    slope30_data: DTYPE = field(default_factory=DTYPE)

    # camelCase aliases for C#/MQL naming parity
    @property
    def baseSlopeSIG(self) -> SIG:
        return self.base_slope_sig

    @baseSlopeSIG.setter
    def baseSlopeSIG(self, v: SIG) -> None:
        self.base_slope_sig = v

    @property
    def slope30SIG(self) -> SIG:
        return self.slope30_sig

    @slope30SIG.setter
    def slope30SIG(self, v: SIG) -> None:
        self.slope30_sig = v

    @property
    def fastSIG(self) -> SIG:
        return self.fast_sig

    @fastSIG.setter
    def fastSIG(self, v: SIG) -> None:
        self.fast_sig = v

    @property
    def microWaveSIG(self) -> SIG:
        return self.micro_wave_sig

    @microWaveSIG.setter
    def microWaveSIG(self, v: SIG) -> None:
        self.micro_wave_sig = v


# ---------------------------------------------------------------------------
# Open API price / trendbar helpers
# ---------------------------------------------------------------------------

def relative_to_price(relative: int | float, digits: Optional[int] = None) -> float:
    """Convert a cTrader relative price (1/100000 units) to absolute price."""
    price = float(relative) / PRICE_SCALE
    if digits is not None:
        return round(price, digits)
    return price


def decode_trendbar(bar, digits: Optional[int] = None) -> dict:
    """
    Decode a ProtoOATrendbar into absolute OHLCV + time.

    Official encoding (see ProtoOATrendbar):
      - low               absolute relative price
      - open  = low + deltaOpen
      - high  = low + deltaHigh
      - close = low + deltaClose
      - volume            tick volume (integer count, not scaled)
      - utcTimestampInMinutes  Unix minutes of bar open
    """
    low_raw = int(getattr(bar, "low", 0) or 0)
    delta_open = int(getattr(bar, "deltaOpen", 0) or 0)
    delta_high = int(getattr(bar, "deltaHigh", 0) or 0)
    delta_close = int(getattr(bar, "deltaClose", 0) or 0)
    volume = int(getattr(bar, "volume", 0) or 0)
    minutes = int(getattr(bar, "utcTimestampInMinutes", 0) or 0)

    return {
        "open": relative_to_price(low_raw + delta_open, digits),
        "high": relative_to_price(low_raw + delta_high, digits),
        "low": relative_to_price(low_raw, digits),
        "close": relative_to_price(low_raw + delta_close, digits),
        "volume": volume,  # tick volume — do NOT divide by 100
        "time": datetime.fromtimestamp(minutes * 60, tz=timezone.utc).replace(tzinfo=None),
        "utc_minutes": minutes,
    }


# ---------------------------------------------------------------------------
# IndData — central market / indicator container
# ---------------------------------------------------------------------------

class IndData:
    """Central data container — Python equivalent of MQL4 INDDATA / C# IndData.

    Primary market series (open/high/low/close/time/tick_volume) are fixed-length
    rolling windows (default 500 bars) kept in lock-step via append_bar().
    Indicator series are filled once per cycle by cindicators.compute_indicators.
    """

    def __init__(self, capacity: int = DEFAULT_BAR_CAPACITY):
        self.symbol_id = 0
        self.symbol_name = ""
        self.capacity = capacity

        # --- Historical Rolling Windows (OHLCV + Time) ---
        self.open: Deque[float] = deque(maxlen=capacity)
        self.high: Deque[float] = deque(maxlen=capacity)
        self.low: Deque[float] = deque(maxlen=capacity)
        self.close: Deque[float] = deque(maxlen=capacity)
        self.time: Deque[datetime] = deque(maxlen=capacity)
        self.tick_volume: Deque[float] = deque(maxlen=capacity)

        # --- Indicators ---
        self.std_close = deque(maxlen=capacity)
        self.std_open = deque(maxlen=capacity)
        self.mfi = deque(maxlen=capacity)
        self.obv = deque(maxlen=capacity)
        self.rsi = deque(maxlen=capacity)
        self.atr = deque(maxlen=capacity)
        self.adx = deque(maxlen=capacity)
        self.adx_plus = deque(maxlen=capacity)
        self.adx_minus = deque(maxlen=capacity)
        self.ima5 = deque(maxlen=capacity)
        self.ima14 = deque(maxlen=capacity)
        self.ima30 = deque(maxlen=capacity)
        self.ima60 = deque(maxlen=capacity)
        self.ima120 = deque(maxlen=capacity)
        self.ima240 = deque(maxlen=capacity)
        self.ima500 = deque(maxlen=capacity)
        self.avg_std = deque(maxlen=capacity)

        # --- Trading State ---
        self.magic_number: int = 0
        self.close_profit: float = 0.0
        self.stop_loss = 0.0
        self.curr_profit = 0.0
        self.max_profit = 0.0
        self.trade_position = SIG.NOSIG
        self.avg_trade_position = SIG.NOSIG
        self.curr_spread = 0
        self.shift = 0
        self.bars_held = 0
        self.base_slope = 0.0
        self.new_bar = False
        self.new_bar_open_time = datetime.now(timezone.utc).replace(tzinfo=None)
        self.prev_bar_open_time = datetime.now(timezone.utc).replace(tzinfo=None)
        self.curr_bar_orders = 0
        self.candle_traded = False
        self.spread_limit = 0
        self.max_pyramid_trades = 0
        self.total_orders = 0

        # --- Physics / Advanced Scores ---
        self.hold_score = 0.0
        self.bayesian_hold_score = 0.0
        self.neuron_hold_score = 0.0
        self.fmsr_raw = 0.0
        self.fmsr_norm = 0.0
        self.fractal_alignment = 0.0
        self.micro_lots = 0.0
        self.conviction_factor = 0.0
        self.physics_action = 0
        self.cobb_douglas_action = 0
        self.hyperbolic_action = 0
        self.market_action = 0
        self.pip_value = 0.0
        self.point = 0.0
        self.period = 0
        self.pip_size = 0.0
        self.digits: int = 5  # price precision (from ProtoOASymbol.digits)
        self.pip_position: int = 4  # ProtoOASymbol.pipPosition
        self.current_period = 0.0
        self.dbl_epsilon = 1e-10
        self.consensus_threshold = 0.0

    # ------------------------------------------------------------------ series
    def __len__(self) -> int:
        return len(self.close)

    @property
    def bars(self) -> int:
        """Number of bars currently held (alias for len)."""
        return len(self.close)

    def append_bar(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        time: datetime,
    ) -> None:
        """Atomically append one OHLCV bar so series lengths stay aligned."""
        self.open.append(float(open_))
        self.high.append(float(high))
        self.low.append(float(low))
        self.close.append(float(close))
        self.tick_volume.append(float(volume))
        self.time.append(time)

    def append_decoded_bar(self, bar: dict) -> None:
        """Append a bar dict as produced by decode_trendbar()."""
        self.append_bar(
            bar["open"],
            bar["high"],
            bar["low"],
            bar["close"],
            bar["volume"],
            bar["time"],
        )

    def update_last_bar(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        time: Optional[datetime] = None,
    ) -> None:
        """Overwrite the most recent bar in place (live forming bar)."""
        if not self.close:
            self.append_bar(
                open_, high, low, close, volume,
                time or datetime.now(timezone.utc).replace(tzinfo=None),
            )
            return
        self.open[-1] = float(open_)
        self.high[-1] = float(high)
        self.low[-1] = float(low)
        self.close[-1] = float(close)
        self.tick_volume[-1] = float(volume)
        if time is not None:
            self.time[-1] = time

    def load_bars(self, bars: Iterable[dict]) -> int:
        """Replace series with a sequence of decoded bar dicts (oldest→newest).

        Only the last `capacity` bars are kept. Returns number of bars loaded.
        """
        self.clear_series()
        bars_list = list(bars)
        if len(bars_list) > self.capacity:
            bars_list = bars_list[-self.capacity :]
        for bar in bars_list:
            self.append_decoded_bar(bar)
        return len(bars_list)

    def clear_series(self) -> None:
        """Clear only OHLCV + time series (keep trading state / indicators empty)."""
        for attr in ("open", "high", "low", "close", "time", "tick_volume"):
            getattr(self, attr).clear()

    def clear(self):
        """Clear all series data — equivalent to MQL4 freeData()."""
        for attr in [
            "open",
            "high",
            "low",
            "close",
            "time",
            "tick_volume",
            "std_close",
            "std_open",
            "mfi",
            "obv",
            "rsi",
            "atr",
            "adx",
            "adx_plus",
            "adx_minus",
            "ima5",
            "ima14",
            "ima30",
            "ima60",
            "ima120",
            "ima240",
            "ima500",
            "avg_std",
        ]:
            getattr(self, attr).clear()

        self.magic_number = 0
        self.close_profit = 0.0
        self.stop_loss = 0.0
        self.curr_profit = 0.0
        self.max_profit = 0.0
        self.trade_position = SIG.NOSIG
        self.bars_held = 0
        self.new_bar = False
        self.candle_traded = False
        self.curr_bar_orders = 0

    def resize(self, primary: int = DEFAULT_BAR_CAPACITY, secondary: int = DEFAULT_BAR_CAPACITY):
        """Recreate series deques with a new maxlen, preserving existing values."""
        capacity = int(primary)
        self.capacity = capacity

        def _recreate(old: deque) -> deque:
            return deque(old, maxlen=capacity)

        for attr in [
            "open",
            "high",
            "low",
            "close",
            "time",
            "tick_volume",
            "std_close",
            "std_open",
            "mfi",
            "obv",
            "rsi",
            "atr",
            "adx",
            "adx_plus",
            "adx_minus",
            "ima5",
            "ima14",
            "ima30",
            "ima60",
            "ima120",
            "ima240",
            "ima500",
            "avg_std",
        ]:
            setattr(self, attr, _recreate(getattr(self, attr)))
        print(f"IndData resized: primary={primary}, secondary={secondary}")


__all__ = [
    "PRICE_SCALE",
    "DEFAULT_BAR_CAPACITY",
    "PERIOD_MINUTES",
    "SIG",
    "DECAY_STRATEGY",
    "DTYPE",
    "T_SIG",
    "IndData",
    "SymbolMeta",
    "period_minutes",
    "period_seconds",
    "symbol_meta_from_proto",
    "apply_symbol_meta",
    "relative_to_price",
    "decode_trendbar",
]
