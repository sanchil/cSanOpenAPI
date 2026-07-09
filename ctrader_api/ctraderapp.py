from collections import defaultdict, deque
from typing import Deque, Dict, Any
import pandas as pd
import time
from twisted.internet import task

from .client import CTraderOpenAPI
from .ctypes import IndData
from .cenums import SIG

class CTraderApp:
    def __init__(self, config):
        self.api = CTraderOpenAPI(config)
        self.api.on_ready = self.on_init
        self.api.on_message = self.on_tick

        # Rolling windows: symbol_id -> deque of dicts (bars)
        self.rolling_windows: Dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=500))
        
        # Full history (limited)
        self.bars: Dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=2000))

        self.current_bar: Dict[int, dict] = {}
        self.symbol_map: Dict[int, str] = {}

        self.bar_interval = 60
        self.last_bar_time = None

        self.on_bar_handlers = []

    def on_init(self):
        print(f"✅ cTrader connected! Account: {self.api.account_id}")
        print("Starting OnBar system...")
        
        # Initialize IndData
        self.ind_data = IndData()
        
        symbols = getattr(response, 'symbol', [])
        for symbol in symbols:
            self.symbol_map[symbol.symbolId] = symbol.symbolName

    def on_tick(self, message):
        if not hasattr(message, 'symbolId') or not hasattr(message, 'bid'):
            return

        symbol_id = message.symbolId
        price = (message.bid + message.ask) / 2
        bid = message.bid
        ask = message.ask
        volume = getattr(message, 'volume', 0)
        timestamp = time.time()

        self.update_current_bar(symbol_id, price, bid, ask, volume, timestamp)
        self.check_for_new_bar(timestamp)

    def update_current_bar(self, symbol_id: int, price: float, bid: float, ask: float, volume: int, timestamp: float):
        if symbol_id not in self.current_bar:
            self.current_bar[symbol_id] = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'bid': bid,
                'ask': ask,
                'volume': volume,
                'timestamp': timestamp
            }
            return

        bar = self.current_bar[symbol_id]
        bar['high'] = max(bar['high'], price)
        bar['low'] = min(bar['low'], price)
        bar['close'] = price
        bar['bid'] = bid
        bar['ask'] = ask
        bar['volume'] += volume
        bar['timestamp'] = timestamp

    def check_for_new_bar(self, current_time: float):
        if self.last_bar_time is None:
            self.last_bar_time = current_time - (current_time % self.bar_interval)
            return

        current_bar_start = current_time - (current_time % self.bar_interval)

        if current_bar_start > self.last_bar_time:
            self.on_bar()
            self.last_bar_time = current_bar_start

    def on_bar(self):
        """Called when a new bar is completed"""
        for symbol_id, bar in list(self.current_bar.items()):
            completed_bar = bar.copy()
            
            self.bars[symbol_id].append(completed_bar)
            self.rolling_windows[symbol_id].append(completed_bar)

            symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
            print(f"New bar closed [{symbol_name}] O={bar['open']:.5f} C={bar['close']:.5f}")

            self.current_bar[symbol_id] = {
                'open': bar['close'],
                'high': bar['close'],
                'low': bar['close'],
                'close': bar['close'],
                'bid': bar['close'],
                'ask': bar['close'],
                'volume': 0,
                'timestamp': time.time()
            }

            self._fire_on_bar_event(symbol_id, completed_bar)

    def _fire_on_bar_event(self, symbol_id: int, bar: dict):
        for handler in self.on_bar_handlers:
            try:
                handler(symbol_id, bar)
            except Exception as e:
                print(f"Error in on_bar handler: {e}")

    def add_on_bar_handler(self, handler):
        self.on_bar_handlers.append(handler)

    def command_loop(self):
        """Your custom periodic task - runs every 10 seconds"""
        print(f"Command loop running at {time.strftime('%H:%M:%S')}")

    def run(self):
        self.api.run()