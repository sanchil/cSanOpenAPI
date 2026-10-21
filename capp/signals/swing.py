"""
Swing-structure regime — peak_trough_sig v2 (TASKS.txt TASK 21, V1-T44; not MQ4).

An incremental ATR zigzag turns closed bars into confirmed swing peaks (on
highs) and troughs (on lows), and a regime state machine reads the market
structure from them:

- Swing size: a leg is confirmed once price retraces ``swing_atr`` × the
  slow ATR (mean ATR over the last ``scale_bars`` bars; 8 × a 12 h mean ≈
  10–13 pips on USDJPY M1). Swings the size of the ctrader_5.png legs, not
  the 5-bar wiggles find_extrema(order=5) reports.
- Break of structure (on the bar close, ``zone_atr`` × slow ATR buffer):
  - close above the last peak  → BUY if troughs are not lower (HH + HL, or
    flat troughs: the U-bottom / flat resistance breakout); otherwise
    (LL then HH: expanding range / V-turn) a SELL regime only → CLOSE.
  - close below the last trough → SELL if peaks are not higher (LH + LL, or
    flat peaks breaking down); otherwise a BUY regime only → CLOSE.
- The regime persists between breaks (no SIDEWAYS exit): a trend is held
  through pullbacks and sideways zigzags until its last swing low (BUY) /
  high (SELL) breaks. That level ratchets with every new higher trough /
  lower peak, so it is a structural trailing stop.

Deviations from the TASK 21 wording, chosen on replay (Aug 5 – Oct 21):
- "Flat peaks + higher troughs → BUY" fires on the break of the flat peak
  line, not when the higher trough confirms. Entering on the trough
  confirmation cost ~60 % of net pips (more range whipsaw).
- A reversal goes BUY → SELL directly only when the peaks have already
  stopped rising; a V-turn first CLOSEs and re-enters once the new
  structure confirms. Pure stop-and-reverse had ~30 % more trades.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, List, Optional

from csys.ctypes import SIG

# Defaults (not MQ4; V1-T44). Plateau on USDJPY M1 Aug 5 – Oct 21 2026:
# swing 7–9 / zone 0–0.5 / scale 720–1440 all net positive after costs.
SW_SWING_ATR = 8.0      # retrace (slow ATRs) that confirms a swing
SW_ZONE_ATR = 0.5       # close must clear the swing level by this (slow ATRs)
SW_SCALE_BARS = 720     # slow ATR = mean ATR over this many closed bars


@dataclass
class SwingStructure:
    """Per-symbol incremental zigzag + structure regime.

    Feed closed bars oldest → newest with :meth:`update`; :attr:`regime` is
    BUY / SELL / CLOSE (trend broken, no opposite structure yet) / NOSIG
    (warm-up). ``peaks`` / ``troughs`` hold the last two confirmed levels.
    """

    swing_atr: float = SW_SWING_ATR
    zone_atr: float = SW_ZONE_ATR
    scale_bars: int = SW_SCALE_BARS
    regime: SIG = SIG.NOSIG
    peaks: List[float] = field(default_factory=list)
    troughs: List[float] = field(default_factory=list)
    last_time: Optional[datetime] = None
    _dir: int = 0                       # +1 up-leg, -1 down-leg, 0 not started
    _hi: float = float("-inf")          # current leg's extreme candidates
    _lo: float = float("inf")
    _hi_n: int = 0                      # bar count at _hi / _lo (leg order at start)
    _lo_n: int = 0
    _n: int = 0
    _atrs: Deque[float] = field(default_factory=deque)
    _atr_sum: float = 0.0

    @property
    def scale(self) -> float:
        """Slow ATR (price units); NaN before the first valid ATR."""
        return self._atr_sum / len(self._atrs) if self._atrs else float("nan")

    def update(self, high: float, low: float, close: float, atr: float,
               time: Optional[datetime] = None) -> SIG:
        """Consume one closed bar; returns the regime after it."""
        self._n += 1
        self.last_time = time
        if atr == atr and atr > 0.0:
            self._atrs.append(atr)
            self._atr_sum += atr
            if len(self._atrs) > self.scale_bars:
                self._atr_sum -= self._atrs.popleft()
        sc = self.scale
        if not (sc == sc and high == high and low == low and close == close):
            return self.regime
        th, z = self.swing_atr * sc, self.zone_atr * sc

        if high > self._hi:
            self._hi, self._hi_n = high, self._n
        if low < self._lo:
            self._lo, self._lo_n = low, self._n
        if self._dir == 0:
            if self._hi - self._lo >= th:
                # first leg: whichever extreme came first is the confirmed swing
                if self._hi_n < self._lo_n:
                    self._push(self.peaks, self._hi)
                    self._dir = -1
                else:
                    self._push(self.troughs, self._lo)
                    self._dir = 1
        elif self._dir > 0 and self._hi - low >= th:
            self._push(self.peaks, self._hi)
            self._dir, self._lo, self._lo_n = -1, low, self._n
        elif self._dir < 0 and high - self._lo >= th:
            self._push(self.troughs, self._lo)
            self._dir, self._hi, self._hi_n = 1, high, self._n

        if self.peaks and close > self.peaks[-1] + z:
            if len(self.troughs) < 2 or self.troughs[-1] - self.troughs[-2] >= -z:
                self.regime = SIG.BUY
            elif self.regime == SIG.SELL:
                self.regime = SIG.CLOSE
        elif self.troughs and close < self.troughs[-1] - z:
            if len(self.peaks) < 2 or self.peaks[-1] - self.peaks[-2] <= z:
                self.regime = SIG.SELL
            elif self.regime == SIG.BUY:
                self.regime = SIG.CLOSE
        return self.regime

    @staticmethod
    def _push(levels: List[float], v: float) -> None:
        levels.append(v)
        if len(levels) > 2:
            del levels[0]
