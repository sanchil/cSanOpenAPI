from collections import defaultdict, deque
import pandas as pd
import time
from twisted.internet import task
from datetime import datetime

from .client import CTraderOpenAPI
from .ctypes import IndData
from .cenums import SIG

class CTraderApp:
    def __init__(self, config):
        self.api = CTraderOpenAPI(config)
        self.api.on_ready = self.on_init
        self.api.on_message = self.on_tick

        self.ind_data: IndData | None = None

        self.symbol_map = {}
        self.current_bar = {}
        self.bar_interval = 60
        self.last_bar_time = None

        self.on_bar_handlers = []

    def on_init(self):
        """Called once when connection is ready"""
        print(f"✅ cTrader connected! Account: {self.api.account_id}")
        print("Starting subscription and symbol loading...")

        # Step 1: Subscribe to spots
        d = self.api.subscribe_spots([1, 3])   # EURUSD and USDJPY
        d.addCallback(self.on_spots_subscribed)
        d.addErrback(self.on_subscription_error)

    def on_spots_subscribed(self, _):
        """Called after successful subscription"""
        print("✅ Successfully subscribed to live prices")

        # Step 2: Get symbol names
        d = self.api.get_symbols()
        d.addCallback(self.on_symbols_received)
        d.addErrback(self.on_symbols_error)

    def on_symbols_received(self, response):
        symbols = getattr(response, 'symbol', [])
        for symbol in symbols:
            self.symbol_map[symbol.symbolId] = symbol.symbolName
        print(f"✅ Loaded {len(self.symbol_map)} symbols")

        # Step 3: Load historical data
        self.init_ind_data()

    def on_subscription_error(self, failure):
        print(f"❌ Subscription failed: {failure}")

    def on_symbols_error(self, failure):
        print(f"❌ Failed to load symbols: {failure}")

    def init_ind_data(self):
        """Populate IndData with historical data"""
        self.ind_data = IndData()

        for symbol_id in [1, 3]:   # EURUSD and USDJPY
            d = self.api.get_trendbars(
                symbol_id=symbol_id, 
                period="M1", 
                weeks=1,
                client_msg_id=f"hist_{symbol_id}"
            )
            d.addCallback(self.on_historical_bars_received, symbol_id)
            d.addErrback(self.on_historical_error, symbol_id)

    def on_historical_bars_received(self, response, symbol_id: int):
        bars = getattr(response, 'trendbar', [])
        symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")

        print(f"Loaded {len(bars)} historical bars for {symbol_name}")

        if self.ind_data:
            self.ind_data.symbol_id = symbol_id
            self.ind_data.symbol_name = symbol_name

        for bar in bars:
            if self.ind_data:
                self.ind_data.open.append(getattr(bar, 'open', 0) / 100000)
                self.ind_data.high.append(getattr(bar, 'high', 0) / 100000)
                self.ind_data.low.append(getattr(bar, 'low', 0) / 100000)
                self.ind_data.close.append(getattr(bar, 'close', 0) / 100000)
                self.ind_data.tick_volume.append(getattr(bar, 'volume', 0) / 100)
                self.ind_data.time.append(datetime.fromtimestamp(getattr(bar, 'timestamp', 0) / 1000))


    def on_historical_error(self, failure, symbol_id: int):
        """Handle errors when loading historical data"""
        print(f"❌ Failed to load historical data for symbol {symbol_id}: {failure.getErrorMessage()}")

    def on_tick(self, message):
        if not hasattr(message, 'symbolId') or not hasattr(message, 'bid'):
            return

        symbol_id = message.symbolId
        price = (message.bid + message.ask) / 2
        volume = getattr(message, 'volume', 0)
        timestamp = time.time()

        self.update_current_bar(symbol_id, price, volume, timestamp)
        self.check_for_new_bar(timestamp)

    def update_current_bar(self, symbol_id: int, price: float, volume: int, timestamp: float):
        if symbol_id not in self.current_bar:
            self.current_bar[symbol_id] = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume,
                'timestamp': timestamp
            }
            return

        bar = self.current_bar[symbol_id]
        bar['high'] = max(bar['high'], price)
        bar['low'] = min(bar['low'], price)
        bar['close'] = price
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

            if self.ind_data:
                self.ind_data.open.append(completed_bar['open'])
                self.ind_data.high.append(completed_bar['high'])
                self.ind_data.low.append(completed_bar['low'])
                self.ind_data.close.append(completed_bar['close'])
                self.ind_data.tick_volume.append(completed_bar['volume'])
                self.ind_data.time.append(datetime.fromtimestamp(completed_bar['timestamp']))

            symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
            print(f"New bar closed [{symbol_name}] O={bar['open']:.5f} C={bar['close']:.5f}")

            self.current_bar[symbol_id] = {
                'open': bar['close'],
                'high': bar['close'],
                'low': bar['close'],
                'close': bar['close'],
                'volume': 0,
                'timestamp': time.time()
            }

            self._fire_on_bar_event()

    def _fire_on_bar_event(self):
        for handler in self.on_bar_handlers:
            try:
                handler(self.ind_data)
            except Exception as e:
                print(f"Error in on_bar handler: {e}")

    def add_on_bar_handler(self, handler):
        self.on_bar_handlers.append(handler)

    def run(self):
        self.api.run()