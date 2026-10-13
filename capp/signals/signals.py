"""
SanSignals / HSIG core — design.txt execution path.

Migrated from:
  SanSignals::kineticAccelerationSIG
  (+ kinetic_acceleration_sig_v1 — intentional redesign, TASKS.txt TASK 3/4)

Deviation (V1-T26/T27): SanSignals keeps per-(symbol, series) flat-regime
state for kinetic_acceleration_sig_v1 hysteresis; MQ4 SanSignals is stateless.
  SanSignals::fastSlowSIG
  HSIG::fuseSIG
  HSIG::initSIG  (fast path: fsig* + base/slope30 kinetic + fused fastSIG)

Call once per cycle after indicators are refreshed on IndData.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from csys.ctypes import DTYPE, IndData, SIG, T_SIG

from .stats import mql_at, resolve_pip, slope_val

# MQ4 SS::update slope windows (fast, wide) for baseSlope / slope30
SLOPE_DENOM = 5
SLOPE_DENOM_WIDE = 21

# kinetic_acceleration_sig_v1 defaults (not MQ4; untuned starting points).
# All "trend" / "acc" values are ATR per bar (slopes in pips/bar ÷ ATR pips).
# Flat band: 0.015 pips/bar (MQ4 fixed cutoff) is ~0.015 ATR/bar on an M1
# major with ~1 pip ATR, so 0.01/0.02 brackets it.
KIN_V1_FLAT_ENTER = 0.01   # trending → flat when |s|/ATR drops below this
KIN_V1_FLAT_EXIT = 0.02    # flat → trending when |s|/ATR reaches this
# mode="acc" (default, V1-T27): acc = sign(s)·(f - s)/ATR
#   open +0.002 ≈ ratio +0.05..+0.10 at s = 0.02..0.04 ATR/bar
#   close 0.010 / 0.015 ≈ ratio -0.20 / -0.30 at s = 0.05 ATR/bar
KIN_V1_OPEN_ACC = 0.002
KIN_V1_CLOSE_ACC_BASE = 0.010
KIN_V1_CLOSE_ACC_SLOPE30 = 0.015
# mode="ratio" (V1-T26 baseline, kept for walk-forward comparison): f/s - 1
KIN_V1_OPEN_LIMIT = 0.0
KIN_V1_CLOSE_LIMIT_BASE = -0.20
KIN_V1_CLOSE_LIMIT_SLOPE30 = -0.30
KIN_V1_MODES = ("acc", "ratio")
_KIN_V1_EPS = 1e-9         # float tolerance on the gates


class SanSignals:
    """Signal generators operating on an IndData snapshot (once per cycle)."""

    def __init__(self, default_shift: int = 1, *, kin_v1_mode: str = "acc") -> None:
        # MQL SHIFT for closed-bar analysis (1 = previous bar if [0] is forming)
        self.default_shift = int(default_shift)
        if kin_v1_mode not in KIN_V1_MODES:
            raise ValueError(f"kin_v1_mode must be one of {KIN_V1_MODES}")
        self.kin_v1_mode = kin_v1_mode
        # kinetic_acceleration_sig_v1 flat regime per (symbol_id, series label)
        self._kin_v1_flat: Dict[Tuple[int, str], bool] = {}

    def reset_state(self, symbol_id: Optional[int] = None) -> None:
        """Forget v1 flat-regime state (one symbol, or all)."""
        if symbol_id is None:
            self._kin_v1_flat.clear()
        else:
            for key in [k for k in self._kin_v1_flat if k[0] == int(symbol_id)]:
                del self._kin_v1_flat[key]

    # ------------------------------------------------------------------ primitives
    @staticmethod
    def fast_slow_sig(
        fast_sig: float,
        slow_sig: float,
        threshold_pct: float = 0.0005,
    ) -> SIG:
        """
        Universal PPO structural signal — SanSignals::fastSlowSIG.

        ppo = (fast - slow) / |slow|
        Reference currently gates on sign(ppo); threshold kept for API parity.
        """
        if math.isnan(fast_sig) or math.isnan(slow_sig):
            return SIG.NOSIG

        if abs(slow_sig) < 1e-6:
            if fast_sig > 0:
                return SIG.BUY
            if fast_sig < 0:
                return SIG.SELL
            return SIG.SIDEWAYS

        ppo = (fast_sig - slow_sig) / abs(slow_sig)
        # threshold_pct reserved for noise band (design currently uses sign only)
        _ = threshold_pct
        if ppo > 0:
            return SIG.BUY
        if ppo < 0:
            return SIG.SELL
        return SIG.SIDEWAYS

    # camelCase alias
    fastSlowSIG = fast_slow_sig

    @staticmethod
    def kinetic_acceleration_sig(
        fast_slope: float,
        slow_slope: float,
        trade_zone_check: float = 0.02,
        trade_close_limit: float = -0.08,
        func_label: str = "",
    ) -> SIG:
        """
        Kinetic acceleration engine — SanSignals::kineticAccelerationSIG.

        ratio = (fastSlope - slowSlope) / slowSlope
        - flat / zero slow  → CLOSE
        - ratio >= -0.05    → BUY/SELL by fastSlope sign
        - ratio < close lim → CLOSE
        - else              → NOSIG (hold / no new action)
        """
        _ = func_label  # debug label in mq4 prints
        if math.isnan(fast_slope) or math.isnan(slow_slope):
            return SIG.NOSIG

        abs_slow = abs(slow_slope)
        trade_open_limit = -0.05

        if abs_slow < 1e-6:
            return SIG.CLOSE

        if abs_slow <= trade_zone_check:
            return SIG.CLOSE

        ratio = (fast_slope - slow_slope) / slow_slope

        if ratio >= trade_open_limit:
            if fast_slope > 0.0:
                return SIG.BUY
            if fast_slope < 0.0:
                return SIG.SELL

        if ratio < trade_close_limit:
            return SIG.CLOSE

        return SIG.NOSIG

    kineticAccelerationSIG = kinetic_acceleration_sig

    @staticmethod
    def kin_v1_flat_regime(
        slow_slope: float,
        atr_pips: float,
        prev_flat: Optional[bool] = None,
        *,
        flat_enter: float = KIN_V1_FLAT_ENTER,
        flat_exit: float = KIN_V1_FLAT_EXIT,
    ) -> Optional[bool]:
        """
        Flat-regime classifier for kinetic_acceleration_sig_v1.

        trend = |s| / atr_pips (ATR per bar). Returns True (flat), False
        (trending) or None (undecided: bad input, or inside the band with no
        ``prev_flat``). With ``prev_flat`` this is real hysteresis: a flat
        regime stays flat until trend >= flat_exit, a trending one stays
        trending until trend < flat_enter.
        """
        # x != x is the NaN test (cheaper than math.isnan in tight loops)
        if slow_slope != slow_slope or atr_pips != atr_pips or atr_pips <= 0.0:
            return None
        trend = abs(slow_slope) / atr_pips
        if trend < flat_enter:
            return True
        if trend >= flat_exit:
            return False
        return prev_flat

    @staticmethod
    def kinetic_acceleration_sig_v1(
        fast_slope: float,
        slow_slope: float,
        atr_pips: float,
        *,
        prev_flat: Optional[bool] = None,
        mode: str = "acc",
        flat_enter: float = KIN_V1_FLAT_ENTER,
        flat_exit: float = KIN_V1_FLAT_EXIT,
        open_acc: float = KIN_V1_OPEN_ACC,
        close_acc: float = KIN_V1_CLOSE_ACC_BASE,
        open_limit: float = KIN_V1_OPEN_LIMIT,
        close_limit: float = KIN_V1_CLOSE_LIMIT_BASE,
    ) -> SIG:
        """
        Kinetic acceleration v1 — intentional redesign of kineticAccelerationSIG
        (TASKS.txt TASK 3/4; not MQ4 behavior, used by Strategy_7).

        Slopes are pips/bar (slope_val) and ``atr_pips`` is ATR in pips, so
        every threshold is in ATR per bar and means the same on every symbol.

        Regime (kin_v1_flat_regime, hysteresis via ``prev_flat``):
        - bad input / ATR <= 0     → NOSIG (no data, no action)
        - flat                     → SIDEWAYS (flat-market close)
        - undecided (band, no prev)→ NOSIG (dead band)

        Trending, mode="acc" (default): d = sign(s),
        acc = d·(f - s)/atr_pips (ATR/bar, + = accelerating with the trend)
        - f·s > 0 and acc >= open_acc  → BUY/SELL by sign of s (convex entry)
        - f·s <= 0 or acc < -close_acc → CLOSE (reversal / deceleration)
        - else                         → NOSIG (hold band)

        Trending, mode="ratio" (V1-T26 baseline): ratio = f/s - 1
        - ratio >= open_limit → BUY/SELL; ratio < close_limit → CLOSE; else NOSIG

        Why "acc": the ratio divides by s, so just above the flat gate a small
        noise in f swings it by ±1; acc has no near-zero denominator and its
        thresholds mean the same at every slope magnitude. open_acc > 0 is an
        entry margin so a steady trend (f ≈ s) doesn't flicker BUY/NOSIG.
        Inputs are SMMA slopes over 5/21 bars, so signals carry MA lag.
        """
        if fast_slope != fast_slope:
            return SIG.NOSIG
        flat = SanSignals.kin_v1_flat_regime(
            slow_slope, atr_pips, prev_flat, flat_enter=flat_enter, flat_exit=flat_exit
        )
        if flat is None:
            return SIG.NOSIG
        if flat:
            return SIG.SIDEWAYS

        side = SIG.BUY if slow_slope > 0.0 else SIG.SELL
        if mode == "ratio":
            ratio = fast_slope / slow_slope - 1.0
            if ratio >= open_limit - _KIN_V1_EPS:
                return side
            if ratio < close_limit - _KIN_V1_EPS:
                return SIG.CLOSE
            return SIG.NOSIG

        d = 1.0 if slow_slope > 0.0 else -1.0
        acc = d * (fast_slope - slow_slope) / atr_pips
        same_sign = fast_slope * slow_slope > 0.0
        if same_sign and acc >= open_acc - _KIN_V1_EPS:
            return side
        if not same_sign or acc < -close_acc - _KIN_V1_EPS:
            return SIG.CLOSE
        return SIG.NOSIG

    kineticAccelerationSIG_v1 = kinetic_acceleration_sig_v1

    @staticmethod
    def fuse_sig(a: SIG, b: SIG, weight_a: float = 1.0, weight_b: float = 1.0) -> SIG:
        """
        Weighted fusion — HSIG::fuseSIG.

        BUY = +w, SELL = -w, other = 0
        |total| > 0.5 → BUY/SELL
        |total| < 0.1 → HOLD
        else          → CLOSE
        """
        score_a = weight_a if a == SIG.BUY else (-weight_a if a == SIG.SELL else 0.0)
        score_b = weight_b if b == SIG.BUY else (-weight_b if b == SIG.SELL else 0.0)
        total = score_a + score_b
        abs_total = abs(total)

        if abs_total > 0.5:
            return SIG.BUY if total > 0 else SIG.SELL
        if abs_total < 0.1:
            return SIG.HOLD
        return SIG.CLOSE

    fuseSIG = fuse_sig

    # ------------------------------------------------------------------ init path
    def init_sig(
        self,
        ind: IndData,
        shift: Optional[int] = None,
        *,
        threshold_pct: float = 0.0005,
    ) -> T_SIG:
        """
        Build the tactical signal bundle for one IndData cycle.

        Mirrors the design.txt path:
          fsig* = fastSlowSIG(close, ima*)
          baseSlopeSIG / slope30SIG = kineticAccelerationSIG(slopeVal(...))
          fastSIG = fuse(fuse(fsig5, fsig14), fsig30) or CLOSE
        """
        t = T_SIG()
        sh = self.default_shift if shift is None else int(shift)
        # Prefer IndData.shift when caller left default and ind has a non-zero shift
        if shift is None and getattr(ind, "shift", 0):
            sh = int(ind.shift)

        pip = float(getattr(ind, "pip_size", 0.0) or 0.0)
        point = float(getattr(ind, "point", 0.0) or 0.0)

        # --- slopes on MAs ---
        # MQ4 SS::update: slopeSIGData(ima, 5, 21, 1) → slopeVal(ima, 5, 21).
        # The kinetic thresholds below were tuned for these windows (cBot's
        # 3/5 defaults are a divergence, not the reference).
        t.base_slope_data = slope_val(
            ind.ima240, slope_denom=SLOPE_DENOM, slope_denom_wide=SLOPE_DENOM_WIDE,
            shift=sh, pip_size=pip, point=point,
        )
        t.slope30_data = slope_val(
            ind.ima30, slope_denom=SLOPE_DENOM, slope_denom_wide=SLOPE_DENOM_WIDE, shift=sh,
            pip_size=pip, point=point,
        )

        t.base_slope_sig = self.kinetic_acceleration_sig(
            t.base_slope_data.val1,
            t.base_slope_data.val2,
            0.015,
            -0.06,
            " Base",
        )
        t.slope30_sig = self.kinetic_acceleration_sig(
            t.slope30_data.val1,
            t.slope30_data.val2,
            0.015,
            -0.2,
            " Slope30",
        )

        # --- v1 kinetic (Strategy_7): flat band in ATR units ---
        try:
            atr_pips = mql_at(ind.atr, sh) / resolve_pip(pip, point)
        except (IndexError, TypeError, ValueError):
            atr_pips = float("nan")
        sid = int(getattr(ind, "symbol_id", 0) or 0)
        t.base_slope_sig_v1 = self._kin_v1(
            sid, "base", t.base_slope_data, atr_pips,
            close_acc=KIN_V1_CLOSE_ACC_BASE, close_limit=KIN_V1_CLOSE_LIMIT_BASE,
        )
        t.slope30_sig_v1 = self._kin_v1(
            sid, "slope30", t.slope30_data, atr_pips,
            close_acc=KIN_V1_CLOSE_ACC_SLOPE30, close_limit=KIN_V1_CLOSE_LIMIT_SLOPE30,
        )

        # --- price vs MA fast/slow ---
        try:
            close = mql_at(ind.close, sh)
            t.fsig5 = self.fast_slow_sig(close, mql_at(ind.ima5, sh), threshold_pct)
            t.fsig14 = self.fast_slow_sig(close, mql_at(ind.ima14, sh), threshold_pct)
            t.fsig30 = self.fast_slow_sig(close, mql_at(ind.ima30, sh), threshold_pct)
            t.fsig60 = self.fast_slow_sig(close, mql_at(ind.ima60, sh), threshold_pct)
            t.fsig120 = self.fast_slow_sig(close, mql_at(ind.ima120, sh), threshold_pct)
            t.fsig240 = self.fast_slow_sig(close, mql_at(ind.ima240, sh), threshold_pct)
            t.fsig500 = self.fast_slow_sig(close, mql_at(ind.ima500, sh), threshold_pct)
        except (IndexError, TypeError, ValueError):
            # Not enough bars yet
            return t

        fused = self.fuse_sig(self.fuse_sig(t.fsig5, t.fsig14), t.fsig30)
        # design: != NOSIG ? fused : CLOSE  (fuse rarely returns NOSIG)
        t.fast_sig = fused if fused != SIG.NOSIG else SIG.CLOSE

        # Lightweight micro-wave proxy for Strategy_1 when full wave engine
        # is not yet ported: align fsig30 with base slope direction.
        t.micro_wave_sig = self._micro_wave_proxy(t)

        return t

    # Alias matching HSIG::initSIG naming
    initSIG = init_sig

    def _kin_v1(
        self,
        symbol_id: int,
        label: str,
        data: DTYPE,
        atr_pips: float,
        **limits: float,
    ) -> SIG:
        """kinetic_acceleration_sig_v1 with this symbol's flat regime carried bar to bar."""
        key = (symbol_id, label)
        prev = self._kin_v1_flat.get(key)
        sig = self.kinetic_acceleration_sig_v1(
            data.val1, data.val2, atr_pips, prev_flat=prev, mode=self.kin_v1_mode, **limits
        )
        flat = self.kin_v1_flat_regime(data.val2, atr_pips, prev)
        if flat is not None:
            self._kin_v1_flat[key] = flat
        return sig

    @staticmethod
    def _micro_wave_proxy(t: T_SIG) -> SIG:
        """
        Interim microWaveSIG until full MicroWaveSIG(engine) is ported.

        Uses agreement of fsig30 with baseSlopeSIG when both are directional.
        """
        if t.fsig30 in (SIG.BUY, SIG.SELL) and t.fsig30 == t.base_slope_sig:
            return t.fsig30
        if t.base_slope_sig == SIG.CLOSE:
            return SIG.CLOSE
        return SIG.NOSIG


# Module-level functional API (stateless convenience)
def fast_slow_sig(fast_sig: float, slow_sig: float, threshold_pct: float = 0.0005) -> SIG:
    return SanSignals.fast_slow_sig(fast_sig, slow_sig, threshold_pct)


def kinetic_acceleration_sig(
    fast_slope: float,
    slow_slope: float,
    trade_zone_check: float = 0.02,
    trade_close_limit: float = -0.08,
    func_label: str = "",
) -> SIG:
    return SanSignals.kinetic_acceleration_sig(
        fast_slope, slow_slope, trade_zone_check, trade_close_limit, func_label
    )


def kinetic_acceleration_sig_v1(
    fast_slope: float,
    slow_slope: float,
    atr_pips: float,
    **kwargs: float,
) -> SIG:
    return SanSignals.kinetic_acceleration_sig_v1(fast_slope, slow_slope, atr_pips, **kwargs)


def fuse_sig(a: SIG, b: SIG, weight_a: float = 1.0, weight_b: float = 1.0) -> SIG:
    return SanSignals.fuse_sig(a, b, weight_a, weight_b)
