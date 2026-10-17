"""
Trading strategies — migrated from cBot CStrategies / mq4 SanStrategies.

Each strategy consumes a once-per-cycle T_SIG snapshot from capp.signals and
returns a single SIG (BUY / SELL / HOLD / CLOSE / NOSIG).

CTraderApp calls ``evaluate(ind)`` once per bar after indicators + init_sig.

Strategy_7 is an intentional deviation from MQ4 (TASKS.txt TASK 3): it reads
kinetic_acceleration_sig_v1 and splits CLOSE into flat vs. deceleration.
Strategy_6 remains the MQ4-parity baseline.

Strategy_8 (V1-T30, not MQ4) trades market structure from peak_trough_sig:
peaks + troughs structure → BUY / SELL, flat or conflicting → CLOSE.

Strategy_9 (V1-T32, not MQ4) trades kinetic_acceleration_sig_v2: any CLOSE
or SIDEWAYS closes (no PnL gate — quick exit), BUY / SELL enter.

ProfitTrail (V1-T38, not MQ4; TASKS.txt TASK 15) is a profit lock on top of
Strategy_8: once the open trade's best excursion reaches
S8_TRAIL_ACTIVATE_ATR × ATR, a close that gives back S8_TRAIL_ATR × ATR from
that best high / low turns the bar's signal into CLOSE. Structure exits alone
give back about one full swing. After a trail exit, same-side entries are
blocked until the strategy output leaves that side, so a still-BUY structure
does not re-buy the top. It only ever emits CLOSE (no SL/TP, policy I1–I10
unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from csys.ctypes import IndData, SIG, T_SIG
from capp.signals import SanSignals

# ProfitTrail defaults (not MQ4; V1-T38). Checked on 2026-10-16 USDJPY M1
# only: A=3 / K=2 turned the 15:46 BUY from +11.8p into about +33.6p.
S8_TRAIL_ACTIVATE_ATR = 3.0   # best excursion (ATRs) before the trail arms
S8_TRAIL_ATR = 2.0            # giveback from the best price (ATRs) that closes


class CStrategies:
    """Strategy layer over SanSignals."""

    def __init__(
        self,
        signals: Optional[SanSignals] = None,
        *,
        active: int = 1,
        profit_close_threshold: float = 4.0,
        mq4_profit_close_threshold: float = 2.0,
        decel_profit_close_threshold: float = 2.0,
    ) -> None:
        self.signals = signals or SanSignals()
        self.active = int(active)  # Strategy_1 .. Strategy_9
        self.profit_close_threshold = float(profit_close_threshold)
        # Strategy_6: HSIG::initSIG uses getTotalTradeProfits(1002) >= 2.0
        self.mq4_profit_close_threshold = float(mq4_profit_close_threshold)
        # Strategy_7: deceleration CLOSE only when avg PnL >= this
        self.decel_profit_close_threshold = float(decel_profit_close_threshold)
        self.last_t_sig: Optional[T_SIG] = None
        self.last_sig: SIG = SIG.NOSIG
        # Strategy_6 sticky HSIG state per symbol_id (see strategy_6)
        self._hsig_state: Dict[int, _HSigState] = {}

    # ------------------------------------------------------------------ API
    def evaluate(
        self,
        ind: IndData,
        *,
        total_trade_profits: float = 0.0,
        strategy: Optional[int] = None,
        shift: Optional[int] = None,
    ) -> SIG:
        """
        Full cycle: init_sig(ind) → Strategy_N(t_sig).

        Call once per bar after ``compute_indicators(ind)``.
        """
        t_sig = self.signals.init_sig(ind, shift=shift)
        self.last_t_sig = t_sig
        n = self.active if strategy is None else int(strategy)
        sig = self.run_strategy(
            n, t_sig, total_trade_profits, symbol_id=int(getattr(ind, "symbol_id", 0) or 0)
        )
        t_sig.strategy_sig = sig
        t_sig.open_sig = sig
        self.last_sig = sig
        return sig

    def run_strategy(
        self,
        n: int,
        t_sig: T_SIG,
        total_trade_profits: float = 0.0,
        *,
        symbol_id: int = 0,
    ) -> SIG:
        dispatch = {
            1: self.strategy_1,
            2: self.strategy_2,
            3: self.strategy_3,
            4: self.strategy_4,
            5: self.strategy_5,
            6: self.strategy_6,
            7: self.strategy_7,
            8: self.strategy_8,
            9: self.strategy_9,
        }
        fn = dispatch.get(int(n), self.strategy_1)
        if n == 1:
            return fn(t_sig)
        if n == 6:
            return fn(t_sig, total_trade_profits, symbol_id=symbol_id)
        return fn(t_sig, total_trade_profits)

    # -------------------------------------------------------------- strategies
    def strategy_1(self, t_sig: T_SIG) -> SIG:
        """
        micWave strategy (CStrategies.Strategy_1).

        BUY/SELL when fsig5 == fsig30 == microWaveSIG and directional.
        Else CLOSE.
        """
        if (
            t_sig.fsig5 == t_sig.fsig30
            and t_sig.fsig30 == t_sig.micro_wave_sig
            and t_sig.fsig30 in (SIG.BUY, SIG.SELL)
        ):
            return t_sig.fsig30
        return SIG.CLOSE

    Strategy_1 = strategy_1

    def strategy_2(self, t_sig: T_SIG, total_trade_profits: float = 0.0) -> SIG:
        """
        baseSlope strategy — close when baseSlope != CLOSE path
        (CStrategies.Strategy_2).
        """
        if t_sig.base_slope_sig != SIG.CLOSE and t_sig.fast_sig == SIG.HOLD:
            return SIG.CLOSE
        if (
            t_sig.base_slope_sig == SIG.CLOSE
            and total_trade_profits >= self.profit_close_threshold
        ):
            return SIG.CLOSE
        if (
            t_sig.fsig5 == t_sig.fsig30
            and t_sig.fsig30 == t_sig.fast_sig
            and t_sig.fsig30 in (SIG.BUY, SIG.SELL)
            and t_sig.fsig30 == t_sig.base_slope_sig
        ):
            return t_sig.fsig30
        return SIG.NOSIG

    Strategy_2 = strategy_2

    def strategy_3(self, t_sig: T_SIG, total_trade_profits: float = 0.0) -> SIG:
        """
        baseSlope strategy — close when baseSlope == CLOSE
        (CStrategies.Strategy_3).
        """
        if t_sig.base_slope_sig == SIG.CLOSE and t_sig.fast_sig == SIG.HOLD:
            return SIG.CLOSE
        if (
            t_sig.base_slope_sig == SIG.CLOSE
            and total_trade_profits >= self.profit_close_threshold
        ):
            return SIG.CLOSE
        if (
            t_sig.fsig5 == t_sig.fsig30
            and t_sig.fsig30 == t_sig.fast_sig
            and t_sig.fsig30 in (SIG.BUY, SIG.SELL)
            and t_sig.fsig30 == t_sig.base_slope_sig
        ):
            return t_sig.fsig30
        return SIG.NOSIG

    Strategy_3 = strategy_3

    def strategy_4(self, t_sig: T_SIG, total_trade_profits: float = 0.0) -> SIG:
        """
        slope30 strategy (CStrategies.Strategy_4).
        """
        if t_sig.slope30_sig == SIG.CLOSE and t_sig.fast_sig == SIG.HOLD:
            return SIG.CLOSE
        if (
            t_sig.slope30_sig == SIG.CLOSE
            and total_trade_profits >= self.profit_close_threshold
        ):
            return SIG.CLOSE
        if t_sig.slope30_sig in (SIG.BUY, SIG.SELL):
            return t_sig.slope30_sig
        return SIG.NOSIG

    Strategy_4 = strategy_4

    def strategy_5(self, t_sig: T_SIG, total_trade_profits: float = 0.0) -> SIG:
        """
        slope30 + profit-exit strategy (CStrategies.Strategy_5).

        - CLOSE when slope30_sig is CLOSE (any fast_sig).
        - CLOSE when total_trade_profits >= profit_close_threshold (any slope).
        - BUY/SELL when slope30_sig is directional.
        - Else NOSIG.
        """
        if t_sig.slope30_sig == SIG.CLOSE:
            return SIG.CLOSE
        if total_trade_profits >= self.profit_close_threshold:
            return SIG.CLOSE
        if t_sig.slope30_sig in (SIG.BUY, SIG.SELL):
            return t_sig.slope30_sig
        return SIG.NOSIG

    Strategy_5 = strategy_5

    def strategy_6(
        self,
        t_sig: T_SIG,
        total_trade_profits: float = 0.0,
        *,
        symbol_id: int = 0,
    ) -> SIG:
        """
        MQ4 live path — HSIG::initSIG "baseSlopeSIG Strategy"
        → setSIGForStrategy_v3 → SanStrategies::dominantSIG (code_base_v98).

        Composite (compositeSIG_1 / composite_CloseSIG_1):
          - baseSlope != CLOSE and fastSIG == HOLD        → close
          - baseSlope == CLOSE and avg PnL >= threshold   → close (2.0)
          - baseSlope BUY/SELL                            → open baseSlope
            (no fsig alignment gate, unlike Strategy_2/3)
          - otherwise                                     → unchanged
        setSIGForStrategy_v3: close wins, else directional open, else unchanged.
        dominantSIG: SIDEWAYS > CLOSE > directional open > NOSIG.

        Sticky state: in MQ4 the HSIG object lives for the EA's lifetime and
        baseInit() only runs in its constructor, so when no branch matches the
        previous composite / open / close values persist. Kept per symbol here.

        Executor maps the result like imaSt3 + OnEntryExit_3: CLOSE or a signal
        opposite the position closes; directional while flat opens.

        Intentional deviations: evaluated once per closed bar (MQ4: every tick);
        avg PnL is the multi-asset label average (TASK 7) rather than MQ4's
        per-symbol sum / OrdersTotal(); single position, no pyramid (TASK 2).
        """
        st = self._hsig_state.setdefault(int(symbol_id), _HSigState())
        base = t_sig.base_slope_sig

        # HSIG::initSIG — baseSlopeSIG Strategy (composite)
        if base != SIG.CLOSE and t_sig.fast_sig == SIG.HOLD:
            st.composite_open, st.composite_close = SIG.NOSIG, SIG.CLOSE
        elif base == SIG.CLOSE and total_trade_profits >= self.mq4_profit_close_threshold:
            st.composite_open, st.composite_close = SIG.NOSIG, SIG.CLOSE
        elif base in (SIG.BUY, SIG.SELL):
            st.composite_open, st.composite_close = base, SIG.NOSIG

        # HSIG::setSIGForStrategy_v3
        if st.composite_close == SIG.CLOSE:
            st.open_sig, st.close_sig = SIG.NOSIG, SIG.CLOSE
        elif st.composite_open in (SIG.BUY, SIG.SELL):
            st.open_sig, st.close_sig = st.composite_open, SIG.NOSIG

        # SanStrategies::dominantSIG
        if st.open_sig == SIG.SIDEWAYS:
            return SIG.SIDEWAYS
        if st.close_sig == SIG.CLOSE:
            return SIG.CLOSE
        if st.open_sig in (SIG.BUY, SIG.SELL):
            return st.open_sig
        return SIG.NOSIG

    Strategy_6 = strategy_6

    def strategy_7(self, t_sig: T_SIG, total_trade_profits: float = 0.0) -> SIG:
        """
        Clean baseSlope strategy on kinetic_acceleration_sig_v1 (TASK 3).

          - base v1 SIDEWAYS (flat)                         → CLOSE, always
          - base v1 CLOSE (deceleration), avg PnL >= thr    → CLOSE
          - base v1 BUY/SELL (convex, trending)             → BUY/SELL
          - otherwise                                       → NOSIG (hold)

        Stateless: NOSIG already means "hold" in the executor, so no sticky
        HSIG state is needed. Deliberately drops Strategy_6's
        "fastSIG == HOLD → close" branch, which chattered on any fsig5/14/30
        disagreement. A losing trade in a decelerating (not flat) market is
        held until the market goes flat or base v1 turns opposite.
        """
        base = t_sig.base_slope_sig_v2
        if base == SIG.SIDEWAYS:
            return SIG.CLOSE
        if base == SIG.CLOSE:
            if total_trade_profits >= self.decel_profit_close_threshold:
                return SIG.CLOSE
            return SIG.NOSIG
        if base in (SIG.BUY, SIG.SELL):
            return base
        return SIG.NOSIG

    Strategy_7 = strategy_7

    def strategy_8(self, t_sig: T_SIG, total_trade_profits: float = 0.0) -> SIG:
        """
        Peak/trough structure strategy on peak_trough_sig (V1-T29/T30).

          - SIDEWAYS (flat or conflicting structure) → CLOSE, always
          - BUY / SELL (peaks + troughs score > 0 / < 0) → BUY / SELL
          - otherwise (too few extrema, NOSIG)       → NOSIG (hold)

        peak_trough_sig defaults to pt_mode="structure" (V1-T31): peaks and
        troughs weigh equally, and a close beyond the last peak / trough zone
        counts at once, so a broken uptrend reads SIDEWAYS and closes.

        Stateless. peak_trough_sig changes only on a newly confirmed extremum
        or a zone break, so the output is naturally persistent. An opposite
        signal closes via the executor policy (no reversal in the same
        cycle); the next bar re-opens on the new side. ``total_trade_profits``
        is unused (no PnL gate) and kept for the shared dispatch signature.
        """
        _ = total_trade_profits
        pt = t_sig.peak_trough_sig
        if pt == SIG.SIDEWAYS:
            return SIG.CLOSE
        if pt in (SIG.BUY, SIG.SELL):
            return pt
        return SIG.NOSIG

    Strategy_8 = strategy_8

    def strategy_9(self, t_sig: T_SIG, total_trade_profits: float = 0.0) -> SIG:
        """
        Kinetic v2 strategy on base_slope_sig_v2 (V1-T32, TASKS.txt TASK 9).

          - v2 SIDEWAYS (dead) or CLOSE (stalled / decelerating) → CLOSE
          - v2 BUY / SELL (tail accelerating past history)      → that side
          - otherwise (dead band, steady trend, NOSIG)           → NOSIG (hold)

        Stateless, no PnL gate: Strategy_7's "hold losers on deceleration"
        gate made v1 slow to exit; v2's stall dead band already absorbs the
        noise the gate was hiding. ``total_trade_profits`` is unused and kept
        for the shared dispatch signature.
        """
        _ = total_trade_profits
        v2 = t_sig.base_slope_sig_v2
        if v2 in (SIG.SIDEWAYS, SIG.CLOSE):
            return SIG.CLOSE
        if v2 in (SIG.BUY, SIG.SELL):
            return v2
        return SIG.NOSIG

    Strategy_9 = strategy_9

    def reset_state(self, symbol_id: Optional[int] = None) -> None:
        """Clear Strategy_6 sticky state and the v1 flat regime (Strategy_7)
        for one symbol, or all — MQ4 baseInit()."""
        if symbol_id is None:
            self._hsig_state.clear()
        else:
            self._hsig_state.pop(int(symbol_id), None)
        self.signals.reset_state(symbol_id)


@dataclass
class _HSigState:
    """Per-symbol mirror of the HSIG members Strategy_6 reads/writes."""

    composite_open: SIG = SIG.NOSIG   # compositeSIG_1
    composite_close: SIG = SIG.NOSIG  # composite_CloseSIG_1
    open_sig: SIG = SIG.NOSIG         # HSIG.openSIG
    close_sig: SIG = SIG.NOSIG        # HSIG.closeSIG

def trail_exit(
    side: SIG,
    entry_price: float,
    best: float,
    close: float,
    atr: float,
    activate_atr: float = S8_TRAIL_ACTIVATE_ATR,
    trail_atr: float = S8_TRAIL_ATR,
) -> bool:
    """True when an armed ATR trail is hit (V1-T38).

    ``best`` is the highest high (BUY) / lowest low (SELL) since entry. The
    trail arms once best is ``activate_atr`` ATRs in profit and fires when
    ``close`` is ``trail_atr`` ATRs back from best. NaN inputs → False.
    """
    if side not in (SIG.BUY, SIG.SELL) or not (atr > 0.0):
        return False
    if not (entry_price == entry_price and best == best and close == close):
        return False
    s = 1.0 if side == SIG.BUY else -1.0
    return s * (best - entry_price) >= activate_atr * atr and s * (best - close) >= trail_atr * atr


@dataclass
class _Trail:
    side: SIG
    entry_price: float
    best: float


class ProfitTrail:
    """Per-symbol ATR trailing profit lock over a strategy's output (V1-T38).

    Feed once per closed bar with the live position side. Returns the signal
    to execute: the strategy's own, CLOSE when the trail fires, or NOSIG for a
    same-side re-entry right after a trail exit.
    """

    def __init__(
        self,
        activate_atr: float = S8_TRAIL_ACTIVATE_ATR,
        trail_atr: float = S8_TRAIL_ATR,
    ) -> None:
        self.activate_atr = float(activate_atr)
        self.trail_atr = float(trail_atr)
        self._open: Dict[int, _Trail] = {}
        self._blocked: Dict[int, SIG] = {}
        self.last_fired: Dict[int, bool] = {}

    def apply(
        self,
        symbol_id: int,
        sig: SIG,
        position_side: SIG,
        *,
        entry_price: float,
        high: float,
        low: float,
        close: float,
        atr: float,
    ) -> SIG:
        """``entry_price`` <= 0 or NaN falls back to ``close`` on the first
        tracked bar (broker fill price not known yet)."""
        self.last_fired[symbol_id] = False
        blocked = self._blocked.get(symbol_id)
        if blocked is not None:
            if sig == blocked:
                sig = SIG.NOSIG
            else:
                del self._blocked[symbol_id]

        if position_side not in (SIG.BUY, SIG.SELL):
            self._open.pop(symbol_id, None)
            return sig

        tr = self._open.get(symbol_id)
        if tr is None or tr.side != position_side:
            entry = entry_price if entry_price == entry_price and entry_price > 0.0 else close
            tr = _Trail(position_side, entry, high if position_side == SIG.BUY else low)
            self._open[symbol_id] = tr
        elif position_side == SIG.BUY:
            tr.best = max(tr.best, high)
        else:
            tr.best = min(tr.best, low)

        if trail_exit(tr.side, tr.entry_price, tr.best, close, atr, self.activate_atr, self.trail_atr):
            self._open.pop(symbol_id, None)
            self._blocked[symbol_id] = tr.side
            self.last_fired[symbol_id] = True
            return SIG.CLOSE
        return sig

    def observe(self, symbol_id: int, highs: Sequence[float], lows: Sequence[float]) -> None:
        """Extend an open trail's best price with bars it did not see (e.g.
        bars reloaded after a host sleep). No-op when nothing is tracked."""
        tr = self._open.get(symbol_id)
        if tr is None:
            return
        vals = [v for v in (highs if tr.side == SIG.BUY else lows) if v == v]
        if vals:
            tr.best = max(tr.best, max(vals)) if tr.side == SIG.BUY else min(tr.best, min(vals))

    def reset(self, symbol_id: Optional[int] = None) -> None:
        if symbol_id is None:
            self._open.clear()
            self._blocked.clear()
            self.last_fired.clear()
        else:
            self._open.pop(symbol_id, None)
            self._blocked.pop(symbol_id, None)
            self.last_fired.pop(symbol_id, None)
