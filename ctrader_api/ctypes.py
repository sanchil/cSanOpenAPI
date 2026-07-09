from collections import deque
from datetime import datetime

from .cenums import SIG

class IndData:
    """Central data container - Python equivalent of MQL4 INDDATA struct"""

    def __init__(self):
        self.symbol_id = 0
        self.symbol_name = ""

        # --- Historical Rolling Windows ---
        self.open = deque(maxlen=500)
        self.high = deque(maxlen=500)
        self.low = deque(maxlen=500)
        self.close = deque(maxlen=500)
        self.time = deque(maxlen=500)
        self.tick_volume = deque(maxlen=500)

        # --- Indicators ---
        self.std_close = deque(maxlen=500)
        self.std_open = deque(maxlen=500)
        self.mfi = deque(maxlen=500)
        self.obv = deque(maxlen=500)
        self.rsi = deque(maxlen=500)
        self.atr = deque(maxlen=500)
        self.adx = deque(maxlen=500)
        self.adx_plus = deque(maxlen=500)
        self.adx_minus = deque(maxlen=500)
        self.ima5 = deque(maxlen=500)
        self.ima14 = deque(maxlen=500)
        self.ima30 = deque(maxlen=500)
        self.ima60 = deque(maxlen=500)
        self.ima120 = deque(maxlen=500)
        self.ima240 = deque(maxlen=500)
        self.ima500 = deque(maxlen=500)
        self.avg_std = deque(maxlen=500)

        # --- Trading State ---
        self.magic_number:int = 0
        self.close_profit : float = 0.0
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
        self.new_bar_open_time = datetime.now()
        self.prev_bar_open_time = datetime.now()
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
        self.current_period = 0.0
        self.dbl_epsilon = 1e-10
        self.consensus_threshold = 0.0

    def clear(self):
        """Clear all data - equivalent to MQL4 freeData()"""
        for attr in [
            'open', 'high', 'low', 'close', 'time', 'tick_volume',
            'std_close', 'std_open', 'mfi', 'obv', 'rsi', 'atr',
            'adx', 'adx_plus', 'adx_minus', 'ima5', 'ima14', 'ima30',
            'ima60', 'ima120', 'ima240', 'ima500', 'avg_std'
        ]:
            getattr(self, attr).clear()

        # Reset scalars
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

    def resize(self, primary: int = 500, secondary: int = 500):
        """Resize arrays (mainly for compatibility)"""
        print(f"IndData resized: primary={primary}, secondary={secondary}")
        # Note: deques have fixed maxlen, so we usually recreate if needed