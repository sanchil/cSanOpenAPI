"""
SanSignals / HSIG core — design.txt execution path.

Migrated from:
  SanSignals::kineticAccelerationSIG
  (+ kinetic_acceleration_sig_v1 — intentional redesign, TASKS.txt TASK 3)
  SanSignals::fastSlowSIG
  HSIG::fuseSIG
  HSIG::initSIG  (fast path: fsig* + base/slope30 kinetic + fused fastSIG)

Call once per cycle after indicators are refreshed on IndData.
"""

from __future__ import annotations

import math
from typing import Optional

from csys.ctypes import IndData, SIG, T_SIG

from .stats import mql_at, resolve_pip, slope_val

# MQ4 SS::update slope windows (fast, wide) for baseSlope / slope30
SLOPE_DENOM = 5
SLOPE_DENOM_WIDE = 21

# kinetic_acceleration_sig_v1 defaults (not MQ4; untuned starting points).
# Flat band is |slowSlope| in ATR per bar: 0.015 pips/bar (MQ4 fixed cutoff)
# is ~0.015 ATR/bar on an M1 major with ~1 pip ATR, so 0.01/0.02 brackets it.
KIN_V1_FLAT_ENTER = 0.01   # |s|/ATR below this → flat (SIDEWAYS)
KIN_V1_FLAT_EXIT = 0.02    # |s|/ATR at/above this → trending; between → NOSIG
KIN_V1_OPEN_LIMIT = 0.0    # ratio >= 0 (f/s >= 1): convex only
KIN_V1_CLOSE_LIMIT_BASE = -0.20
KIN_V1_CLOSE_LIMIT_SLOPE30 = -0.30
_KIN_V1_EPS = 1e-9         # float tolerance on the ratio gates


class SanSignals:
    """Signal generators operating on an IndData snapshot (once per cycle)."""

    def __init__(self, default_shift: int = 1) -> None:
        # MQL SHIFT for closed-bar analysis (1 = previous bar if [0] is forming)
        self.default_shift = int(default_shift)

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
    def kinetic_acceleration_sig_v1(
        fast_slope: float,
        slow_slope: float,
        atr_pips: float,
        *,
        flat_enter: float = KIN_V1_FLAT_ENTER,
        flat_exit: float = KIN_V1_FLAT_EXIT,
        open_limit: float = KIN_V1_OPEN_LIMIT,
        close_limit: float = KIN_V1_CLOSE_LIMIT_BASE,
    ) -> SIG:
        """
        Kinetic acceleration v1 — intentional redesign of kineticAccelerationSIG
        (TASKS.txt TASK 3; not MQ4 behavior, used by Strategy_7).

        Slopes are pips/bar (slope_val), ``atr_pips`` is ATR in pips, so
        ``trend = |s| / atr_pips`` is ATR per bar and means the same on every
        symbol. ``ratio = f/s - 1`` as in v0.

        - NaN input / ATR <= 0           → NOSIG (no data, no action)
        - trend <  flat_enter            → SIDEWAYS (flat-market close)
        - trend <  flat_exit             → NOSIG (flat hysteresis band)
        - ratio >= open_limit (0 = f>=s) → BUY/SELL by sign of s (convex only)
        - ratio <  close_limit           → CLOSE (deceleration close)
        - else                           → NOSIG (hold band)

        Differences from v0: flat is volatility-relative and returns SIDEWAYS
        (not CLOSE) so strategies can tell flat from deceleration; entry needs
        convexity (v0 opened at f/s >= 0.95); a wide hold band replaces v0's
        1-point band (base -0.05/-0.06); ratio gates use a float tolerance; the
        dead ``|s| < 1e-6`` guard is gone (covered by ATR normalization).
        """
        if any(math.isnan(x) for x in (fast_slope, slow_slope, atr_pips)):
            return SIG.NOSIG
        if atr_pips <= 0.0:
            return SIG.NOSIG

        trend = abs(slow_slope) / atr_pips
        if trend < flat_enter:
            return SIG.SIDEWAYS
        if trend < flat_exit:
            return SIG.NOSIG

        ratio = fast_slope / slow_slope - 1.0
        if ratio >= open_limit - _KIN_V1_EPS:
            return SIG.BUY if slow_slope > 0.0 else SIG.SELL
        if ratio < close_limit - _KIN_V1_EPS:
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
        t.base_slope_sig_v1 = self.kinetic_acceleration_sig_v1(
            t.base_slope_data.val1, t.base_slope_data.val2, atr_pips,
            close_limit=KIN_V1_CLOSE_LIMIT_BASE,
        )
        t.slope30_sig_v1 = self.kinetic_acceleration_sig_v1(
            t.slope30_data.val1, t.slope30_data.val2, atr_pips,
            close_limit=KIN_V1_CLOSE_LIMIT_SLOPE30,
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
