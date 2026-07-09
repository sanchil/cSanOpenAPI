from dataclasses import dataclass
from typing import List, Optional
import numpy as np
from datetime import datetime

from .cenums import SIG   # We'll create this next

@dataclass
class IndData:
    """Central data container - equivalent to C# IndData struct"""
    
    # --- 1. HISTORICAL ARRAYS ---
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    time: List[datetime]
    tick_volume: np.ndarray

    # --- Indicators ---
    std_close: np.ndarray
    std_open: np.ndarray
    mfi: np.ndarray
    obv: np.ndarray
    rsi: np.ndarray
    atr: np.ndarray
    adx: np.ndarray
    adx_plus: np.ndarray
    adx_minus: np.ndarray
    ima5: np.ndarray
    ima14: np.ndarray
    ima30: np.ndarray
    ima60: np.ndarray
    ima120: np.ndarray
    ima240: np.ndarray
    ima500: np.ndarray
    avg_std: np.ndarray

    # --- Trading State ---
    magic_number: int = 0
    close_profit: float = 0.0
    stop_loss: float = 0.0
    curr_profit: float = 0.0
    max_profit: float = 0.0
    trade_position: SIG = SIG.NOSIG
    avg_trade_position: SIG = SIG.NOSIG
    curr_spread: int = 0
    shift: int = 0
    bars_held: int = 0
    base_slope: float = 0.0

    # --- Physics / Advanced Scores ---
    fmsr_raw: float = 0.0
    fmsr_norm: float = 0.0
    hold_score: float = 0.0
    bayesian_hold_score: float = 0.0
    neuron_hold_score: float = 0.0
    fractal_alignment: float = 0.0
    micro_lots: float = 0.0
    conviction_factor: float = 0.0
    physics_action: int = 0
    cobb_douglas_action: int = 0
    hyperbolic_action: int = 0
    market_action: int = 0
    pip_value: float = 0.0
    point: float = 0.0
    period: int = 0
    pip_size: float = 0.0
    current_period: float = 0.0
    dbl_epsilon: float = 1e-10
    spread_limit: float = 0.0
    candle_traded: bool = False
    digits: int = 5
    total_orders: int = 0