"""
Trading strategies — migrated from cBot CStrategies / mq4 SanStrategies.

Each strategy consumes a once-per-cycle T_SIG snapshot from capp.signals and
returns a single SIG (BUY / SELL / HOLD / CLOSE / NOSIG).

CTraderApp calls ``evaluate(ind)`` once per bar after indicators + init_sig.
"""

from __future__ import annotations

from typing import Optional

from csys.ctypes import IndData, SIG, T_SIG
from capp.signals import SanSignals


class CStrategies:
    """Strategy layer over SanSignals."""

    def __init__(
        self,
        signals: Optional[SanSignals] = None,
        *,
        active: int = 1,
        profit_close_threshold: float = 4.0,
    ) -> None:
        self.signals = signals or SanSignals()
        self.active = int(active)  # Strategy_1 .. Strategy_5
        self.profit_close_threshold = float(profit_close_threshold)
        self.last_t_sig: Optional[T_SIG] = None
        self.last_sig: SIG = SIG.NOSIG

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
        print(f" Total Trade Profits: {total_trade_profits} Active Strategy: {strategy} ")
        t_sig = self.signals.init_sig(ind, shift=shift)
        self.last_t_sig = t_sig
        n = self.active if strategy is None else int(strategy)
        sig = self.run_strategy(n, t_sig, total_trade_profits)
        t_sig.strategy_sig = sig
        t_sig.open_sig = sig
        self.last_sig = sig
        return sig

    def run_strategy(
        self,
        n: int,
        t_sig: T_SIG,
        total_trade_profits: float = 0.0,
    ) -> SIG:
        dispatch = {
            1: self.strategy_1,
            2: self.strategy_2,
            3: self.strategy_3,
            4: self.strategy_4,
            5: self.strategy_5,
        }
        fn = dispatch.get(int(n), self.strategy_1)
        if n == 1:
            return fn(t_sig)
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