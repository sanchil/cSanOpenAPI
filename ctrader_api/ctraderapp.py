from datetime import datetime, timezone
from typing import Dict, Optional
import time

from .client import CTraderOpenAPI
from .ctypes import (
    DEFAULT_BAR_CAPACITY,
    IndData,
    decode_trendbar,
    relative_to_price,
)

# Indicators computed once per cycle from the IndData OHLCV snapshot
from cindicators.snapshot import compute_indicators


class CTraderApp:
    """
    Application layer over CTraderOpenAPI.

    Startup call flow (after auth is ready):
      1. ProtoOASubscribeSpotsReq      — live quotes
      2. ProtoOASymbolsListReq         — symbol id → name map
      3. ProtoOAGetTrendbarsReq        — seed IndData (OHLCV snapshot, count=500)
      4. ProtoOASubscribeLiveTrendbarReq — official live bars on spots

    self.hist_bars[symbol_id] is an IndData rolling window of Open/High/Low/
    Close/Volume/Time (maxlen=DEFAULT_BAR_CAPACITY).
    """

    def __init__(self, config):
        self.api = CTraderOpenAPI(config)
        self.api.on_ready = self.on_init
        self.api.on_message = self.on_tick

        # Per-symbol historical data: Dict[symbol_id, IndData]
        self.hist_bars: Dict[int, IndData] = {}

        self.symbol_map: Dict[int, str] = {}
        self.current_bar: Dict[int, dict] = {}
        self.bar_interval = 60  # seconds; matches M1
        self.bar_period = "M1"
        self.bar_count = DEFAULT_BAR_CAPACITY
        self.last_bar_time: Optional[float] = None
        self.fxpair_arr = [1, 4]  # EURUSD, USDJPY (demo symbol ids; broker-dependent)
        # Track open-minute of the last committed live bar per symbol
        self._last_live_bar_minutes: Dict[int, int] = {}
        self._hist_pending = 0
        self._hist_ready = False

        self.on_bar_handlers = []

    # ------------------------------------------------------------------ init
    def on_init(self):
        print(f"✅ cTrader connected! Account: {self.api.account_id}")
        print("Subscribing to spots...")

        # Step 1: Subscribe to spots (required before live trendbars)
        d = self.api.subscribe_spots(self.fxpair_arr, subscribe_to_timestamp=True)
        d.addCallback(self.on_spots_subscribed)
        d.addErrback(self.on_subscription_error)

    def on_spots_subscribed(self, _):
        print("✅ Successfully subscribed to live prices")

        # Step 2: Symbol list for names
        d = self.api.get_symbols()
        d.addCallback(self.on_symbols_received)
        d.addErrback(self.on_symbols_error)

    def on_symbols_received(self, response):
        symbols = getattr(response, "symbol", [])
        for symbol in symbols:
            self.symbol_map[symbol.symbolId] = symbol.symbolName
        print(f"✅ Loaded {len(self.symbol_map)} symbols")

        # Step 3: Seed IndData from historical trendbars
        self.init_ind_data()

    def on_subscription_error(self, failure):
        print(f"❌ Subscription failed: {failure}")

    def on_symbols_error(self, failure):
        print(f"❌ Failed to load symbols: {failure}")

    # ---------------------------------------------------------- historical seed
    def init_ind_data(self):
        """Populate IndData for each watched symbol via ProtoOAGetTrendbarsReq.

        Uses count=bar_count (default 500) so we request exactly the snapshot
        size IndData can hold, avoiding oversized ranges / INCORRECT_BOUNDARIES.
        """
        self._hist_pending = len(self.fxpair_arr)
        self._hist_ready = False

        for symbol_id in self.fxpair_arr:
            data = IndData(capacity=self.bar_count)
            data.symbol_id = symbol_id
            data.symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
            data.period = self.bar_interval
            self.hist_bars[symbol_id] = data

            d = self.api.get_trendbars(
                symbol_id=symbol_id,
                period=self.bar_period,
                count=self.bar_count,
                client_msg_id=f"hist_{symbol_id}",
            )
            d.addCallback(self.on_historical_bars_received, symbol_id)
            d.addErrback(self.on_historical_error, symbol_id)

    def on_historical_bars_received(self, response, symbol_id: int):
        raw_bars = list(getattr(response, "trendbar", []) or [])
        symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
        data = self.hist_bars[symbol_id]

        # Decode ProtoOATrendbar delta encoding → absolute OHLCV
        decoded = [decode_trendbar(bar) for bar in raw_bars]
        # Sort oldest → newest by bar open time (API usually already does this)
        decoded.sort(key=lambda b: b["utc_minutes"])

        loaded = data.load_bars(decoded)
        if decoded:
            self._last_live_bar_minutes[symbol_id] = decoded[-1]["utc_minutes"]

        # Once-per-seed cycle: fill indicator snapshot from OHLCV
        if loaded:
            self.refresh_indicators(data)

        print(
            f"✅ Loaded {loaded}/{len(raw_bars)} historical bars for {symbol_name} "
            f"(capacity={data.capacity})"
        )
        if loaded:
            last = decoded[-1]
            atr_last = data.atr[-1] if data.atr else float("nan")
            print(
                f"   last bar: t={last['time']} O={last['open']:.5f} "
                f"H={last['high']:.5f} L={last['low']:.5f} "
                f"C={last['close']:.5f} V={last['volume']} "
                f"ATR={atr_last:.5f}"
            )

        self._hist_pending -= 1
        if self._hist_pending <= 0:
            self._on_all_historical_loaded()

    def on_historical_error(self, failure, symbol_id: int):
        print(
            f"❌ Failed to load historical data for symbol {symbol_id}: "
            f"{failure.getErrorMessage()}"
        )
        self._hist_pending -= 1
        if self._hist_pending <= 0:
            self._on_all_historical_loaded()

    def _on_all_historical_loaded(self):
        """After history is in, subscribe to live trendbars for robust updates."""
        self._hist_ready = True
        print("Subscribing to live trendbars...")
        for symbol_id in self.fxpair_arr:
            d = self.api.subscribe_live_trendbars(
                symbol_id=symbol_id,
                period=self.bar_period,
                client_msg_id=f"livebar_{symbol_id}",
            )
            d.addCallback(
                lambda _, sid=symbol_id: print(
                    f"✅ Live trendbar subscribed for "
                    f"{self.symbol_map.get(sid, sid)}"
                )
            )
            d.addErrback(
                lambda f, sid=symbol_id: print(
                    f"❌ Live trendbar subscribe failed for {sid}: "
                    f"{f.getErrorMessage()}"
                )
            )

    # --------------------------------------------------------------- live ticks
    def on_tick(self, message):
        """Handle inbound extracted protobuf messages (primarily spot events)."""
        # Spot events always carry symbolId; bid/ask are optional uint64 relative prices
        if not hasattr(message, "symbolId"):
            return

        symbol_id = message.symbolId
        if symbol_id not in self.hist_bars:
            return

        # Prefer official live trendbars when present (after subscribe_live_trendbars)
        live_bars = list(getattr(message, "trendbar", []) or [])
        if live_bars:
            for bar in live_bars:
                self._apply_live_trendbar(symbol_id, bar)
            return

        # Fallback: synthesize bars from mid price if no live trendbar payload
        if not (hasattr(message, "bid") and hasattr(message, "ask")):
            return
        # bid/ask are 1/100000 of a price unit
        bid = relative_to_price(message.bid)
        ask = relative_to_price(message.ask)
        price = (bid + ask) / 2.0
        # ProtoOASpotEvent has no volume field — keep 0 for synthetic bars
        volume = 0
        # Prefer server timestamp (ms) when subscribeToSpotTimestamp=True
        if getattr(message, "timestamp", 0):
            timestamp = message.timestamp / 1000.0
        else:
            timestamp = time.time()

        self.update_current_bar(symbol_id, price, volume, timestamp)
        self.check_for_new_bar(timestamp)

    def _apply_live_trendbar(self, symbol_id: int, bar) -> None:
        """Update IndData from an official ProtoOATrendbar on a spot event.

        While the bar is forming, utcTimestampInMinutes is stable and OHLCV is
        overwritten on the last slot. When the minute rolls, a new bar is appended
        and on_bar handlers fire.
        """
        decoded = decode_trendbar(bar)
        data = self.hist_bars[symbol_id]
        minutes = decoded["utc_minutes"]
        prev = self._last_live_bar_minutes.get(symbol_id)

        if prev is None:
            # First live bar after history — replace trailing incomplete bar if same minute
            if data.time and data.time[-1] == decoded["time"]:
                data.update_last_bar(
                    decoded["open"],
                    decoded["high"],
                    decoded["low"],
                    decoded["close"],
                    decoded["volume"],
                    decoded["time"],
                )
            else:
                data.append_decoded_bar(decoded)
            self._last_live_bar_minutes[symbol_id] = minutes
            return

        if minutes == prev:
            # Same forming bar — update in place
            data.update_last_bar(
                decoded["open"],
                decoded["high"],
                decoded["low"],
                decoded["close"],
                decoded["volume"],
                decoded["time"],
            )
            return

        if minutes > prev:
            # New bar opened → previous bar is complete
            data.new_bar = True
            data.prev_bar_open_time = data.time[-1] if data.time else decoded["time"]
            data.new_bar_open_time = decoded["time"]
            data.append_decoded_bar(decoded)
            self._last_live_bar_minutes[symbol_id] = minutes

            # Once per bar cycle: recompute indicator snapshot for this symbol
            self.refresh_indicators(data)

            symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
            if len(data.close) >= 2:
                # Print the completed bar (second-to-last)
                print(
                    f"New bar closed [{symbol_name}] "
                    f"O={data.open[-2]:.5f} H={data.high[-2]:.5f} "
                    f"L={data.low[-2]:.5f} C={data.close[-2]:.5f} "
                    f"V={data.tick_volume[-2]:.0f} "
                    f"ATR={data.atr[-1] if data.atr else float('nan'):.5f} "
                    f"ADX={data.adx[-1] if data.adx else float('nan'):.2f}"
                )
            self._fire_on_bar_event()

    # --------------------------------------------- synthetic bar path (fallback)
    def update_current_bar(self, symbol_id: int, price: float, volume: int, timestamp: float):
        if symbol_id not in self.current_bar:
            self.current_bar[symbol_id] = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "timestamp": timestamp,
            }
            return

        bar = self.current_bar[symbol_id]
        bar["high"] = max(bar["high"], price)
        bar["low"] = min(bar["low"], price)
        bar["close"] = price
        bar["volume"] += volume
        bar["timestamp"] = timestamp

    def check_for_new_bar(self, current_time: float):
        if self.last_bar_time is None:
            self.last_bar_time = current_time - (current_time % self.bar_interval)
            return

        current_bar_start = current_time - (current_time % self.bar_interval)

        if current_bar_start > self.last_bar_time:
            self.on_bar()
            self.last_bar_time = current_bar_start

    def on_bar(self):
        """Fallback: commit synthetic bars built from mid prices."""
        for symbol_id, bar in list(self.current_bar.items()):
            completed_bar = bar.copy()
            data = self.hist_bars[symbol_id]
            data.append_bar(
                completed_bar["open"],
                completed_bar["high"],
                completed_bar["low"],
                completed_bar["close"],
                completed_bar["volume"],
                datetime.fromtimestamp(
                    completed_bar["timestamp"], tz=timezone.utc
                ).replace(tzinfo=None),
            )

            # Once per bar cycle
            self.refresh_indicators(data)

            symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
            print(
                f"New bar closed [{symbol_name}] "
                f"O={bar['open']:.5f} C={bar['close']:.5f} (synthetic)"
            )

            self.current_bar[symbol_id] = {
                "open": bar["close"],
                "high": bar["close"],
                "low": bar["close"],
                "close": bar["close"],
                "volume": 0,
                "timestamp": time.time(),
            }

            self._fire_on_bar_event()

    def refresh_indicators(self, data: IndData) -> IndData:
        """Recompute indicator snapshot once for this IndData (per cycle).

        Fills ima*, atr, adx/di+/di-, std_close/std_open/avg_std from the
        current OHLCV window. Safe to call after history load or bar close.
        """
        return compute_indicators(data)

    def _fire_on_bar_event(self):
        for handler in self.on_bar_handlers:
            try:
                handler(self.hist_bars)  # Pass the whole dict of IndData
            except Exception as e:
                print(f"Error in on_bar handler: {e}")

    def add_on_bar_handler(self, handler):
        self.on_bar_handlers.append(handler)

    def run(self):
        self.api.run()
