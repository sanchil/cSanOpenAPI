from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union
import time

from twisted.internet import defer

# System layer
from csys.ctrader_api import CTraderOpenAPI, OrderExecutor
from csys.ctrader_api.client import is_spot_event, scale_money
from csys.ctrader_api.execution import parse_deal_fill
from csys.ctypes import (
    DEFAULT_HISTORY_BARS,
    IndData,
    SIG,
    SymbolMeta,
    T_SIG,
    apply_symbol_meta,
    decode_trendbar,
    period_seconds,
    relative_to_price,
    symbol_meta_from_proto,
)
from csys.cindicators.snapshot import compute_indicators

# Application layer
from capp.signals import SanSignals
from capp.strategies import CStrategies, ProfitTrail
from capp.tradelog import TradeLogAgent, TradeLogConfig
from capp.utils import PositionPnL, SanUtils


RECONCILE_ATTEMPTS = 3      # reconcile tries per session before reconnecting
RECONCILE_RETRY_S = 5.0


class CTraderApp:
    """
    Business-layer trading app (capp) over the system layer (csys).

    Uses:
      * csys.ctrader_api   — Open API client + OrderExecutor
      * csys.ctypes       — IndData / SIG / T_SIG
      * csys.cindicators  — once-per-cycle indicator snapshot
      * capp.signals / capp.strategies — signals + strategies
      * capp.utils.SanUtils — avg floating PnL (TASK 10)

    Per bar cycle (once, live):
      compute_indicators
        → fetch_position_pnls (single-flight reconcile + unrealized PnL)
        → SanUtils.get_total_trade_profits
        → strategies.evaluate
        → OrderExecutor.handle_signal

    Account PnL is serialized: concurrent symbol bar cycles join one in-flight
    fetch instead of parallel reconcile/PnL requests (TASK 11).
    """

    def __init__(
        self,
        config,
        active_strategy: int = 1,
        *,
        trade_volume: float = 10000.0,
        trade_label: str = "cSanOpenAPI",
        trading_enabled: bool = True,
        dry_run: bool = False,
        trade_log: Optional[TradeLogConfig] = None,
        pt_mode: str = "swing",
        profit_trail: bool = False,
    ):
        self.api = CTraderOpenAPI(config)
        self.api.on_ready = self.on_init
        self.api.on_message = self.on_message
        self.api.on_disconnected = self._on_disconnected
        # V1-T42: connect / auth / watchdog / token events → console + trade log
        self.api.on_status = self._note
        # V1-T43: trading state + last bar in the container health file
        self.api.health_extra = lambda: {
            "trading": bool(self._hist_ready),
            "last_bar_min": max(self._last_live_bar_minutes.values(), default=None),
        }

        # Per-symbol historical data: Dict[symbol_id, IndData]
        self.hist_bars: Dict[int, IndData] = {}

        self.symbol_map: Dict[int, str] = {}  # id → name (light list)
        # Full symbol meta from ProtoOASymbolById (digits, pip_size, point, …)
        self.symbol_meta: Dict[int, SymbolMeta] = {}
        # Convenience map: symbol_id → digits (always available after meta load)
        self.symbol_digits: Dict[int, int] = {}
        self.current_bar: Dict[int, dict] = {}
        # Latest (bid, ask) per symbol from spot events (trade log slippage / spread)
        self.quotes: Dict[int, tuple] = {}
        self.bar_interval = 60  # seconds between synthetic bars (M1)
        self.bar_period = "M1"
        # Long window so SMMA ima240/ima500 converge (MQ4 MAs see full history)
        self.bar_count = DEFAULT_HISTORY_BARS
        self.last_bar_time: Optional[float] = None
#        self.fxpair_arr = [1, 4]  # EURUSD, USDJPY (demo symbol ids; broker-dependent)
        self.fxpair_arr = [4]  # EURUSD, USDJPY (demo symbol ids; broker-dependent)
        # Track open-minute of the last committed live bar per symbol
        self._last_live_bar_minutes: Dict[int, int] = {}
        self._hist_pending = 0
        self._hist_ready = False
        # V1-T39: symbols with live trendbars flowing (no synthetic fallback),
        # and symbols reloading history after a bar gap (host sleep / stall)
        self._live_seen: set = set()
        self._resyncing: set = set()
        # V1-T42: one number per session build (on_init); callbacks of an
        # older session (connection dropped mid-init) are ignored
        self._session = 0
        # Last bar seen before a reseed, so ProfitTrail can see missed bars
        self._pre_reseed_last_time: Dict[int, datetime] = {}

        # design.txt: signals → strategies (once per cycle)
        # peak_trough_sig mode for Strategy_8 (V1-T44: "swing" = v2 default)
        self.signals = SanSignals(default_shift=1, pt_mode=pt_mode)
        self.strategies = CStrategies(self.signals, active=active_strategy)
        self.last_t_sig: Dict[int, T_SIG] = {}
        self.last_trade_sig: Dict[int, SIG] = {}
        self.last_total_trade_profits: float = 0.0
        # V1-T38: ATR trailing profit lock over these strategies' output
        # (off by default since V1-T44: it cut swing trends short; opt in)
        self.profit_trail = ProfitTrail()
        self.trail_strategies = {8} if profit_trail else set()

        # TASK 11: at most one account PnL fetch in flight (multi-symbol bars join it)
        self._pnl_fetch_inflight: Optional[defer.Deferred] = None
        self._pnl_req_seq: int = 0

        # TASK 2: single-trade order execution (no SL/TP, no pyramid)
        self.executor = OrderExecutor(
            self.api,
            volume=trade_volume,
            label=trade_label,
            dry_run=dry_run,
            enabled=trading_enabled,
        )
        # TASK 7/10: pure PnL average; cTrader filter = label
        self.utils = SanUtils(default_label=trade_label)

        # V1-T35: per-bar trade log (file per day, signal comparison, paper books)
        self.trade_log = TradeLogAgent(trade_log)
        if self.trade_log.enabled:
            self.executor.on_action = self.trade_log.log_execution
            self.trade_log.name_of = lambda sid: self.symbol_map.get(sid, f"ID_{sid}")

        self.on_bar_handlers = []

    # ------------------------------------------------------------------ init
    def on_init(self):
        """Runs on every authenticated session (first connect and each
        reconnect): resubscribe → symbols → reseed → reconcile → live bars."""
        self._session += 1
        gen = self._session
        self._hist_ready = False  # no orders until reconcile finishes
        print(f"✅ cTrader connected! Account: {self.api.account_id}")
        if self._last_live_bar_minutes:
            # Reconnect (e.g. host woke from sleep): history is reseeded below,
            # so the V1-T39 gap check never sees the hole — log it here (V1-T40)
            gap = int(time.time() // 60) - max(self._last_live_bar_minutes.values())
            self._note(f"⚠️  Reconnected — reseeding history (≈{gap} min since last bar)")
        print("Subscribing to spots...")

        # Step 1: Subscribe to spots (required before live trendbars)
        d = self.api.subscribe_spots(self.fxpair_arr, subscribe_to_timestamp=True)
        d.addCallback(self.on_spots_subscribed, gen)
        d.addErrback(self.on_subscription_error, gen)

    def _stale(self, gen: int) -> bool:
        """True for a callback from a session that was since replaced."""
        return gen != self._session

    def on_spots_subscribed(self, _, gen: int = 0):
        if self._stale(gen):
            return
        print("✅ Successfully subscribed to live prices")

        # Step 2: Symbol list for names
        d = self.api.get_symbols()
        d.addCallback(self.on_symbols_received, gen)
        d.addErrback(self.on_symbols_error, gen)

    def on_symbols_received(self, response, gen: int = 0):
        if self._stale(gen):
            return
        symbols = getattr(response, "symbol", [])
        for symbol in symbols:
            self.symbol_map[symbol.symbolId] = symbol.symbolName
        print(f"✅ Loaded {len(self.symbol_map)} symbols")

        # Step 3: full ProtoOASymbol for watched ids → digits / pip_size / point
        d = self.api.get_symbols_by_id(
            self.fxpair_arr,
            client_msg_id="symbols_by_id",
        )
        d.addCallback(self.on_symbol_details_received, gen)
        d.addErrback(self.on_symbol_details_error, gen)

    def on_symbol_details_received(self, response, gen: int = 0):
        """Cache symbol_id → SymbolMeta (digits, pipPosition → pip_size)."""
        if self._stale(gen):
            return
        full_symbols = list(getattr(response, "symbol", []) or [])
        for sym in full_symbols:
            sid = int(getattr(sym, "symbolId", 0) or 0)
            name = self.symbol_map.get(sid, "")
            meta = symbol_meta_from_proto(sym, name=name)
            self.symbol_meta[sid] = meta
            self.symbol_digits[sid] = meta.digits
            print(
                f"   symbol {sid} {meta.name or '?'}: "
                f"digits={meta.digits} pipPos={meta.pip_position} "
                f"pip_size={meta.pip_size} point={meta.point}"
            )

        # Fallback meta for any watched id missing from response
        for sid in self.fxpair_arr:
            if sid not in self.symbol_meta:
                name = self.symbol_map.get(sid, f"ID_{sid}")
                print(f"⚠️  No ProtoOASymbol detail for {sid} ({name}) — using FX5 defaults")
                self.symbol_meta[sid] = SymbolMeta(
                    symbol_id=sid,
                    name=name,
                    digits=5,
                    pip_position=4,
                    pip_size=0.0001,
                    point=0.00001,
                )
                self.symbol_digits[sid] = 5

        print(f"✅ Symbol meta cached for {len(self.symbol_meta)} ids")
        # Step 4: Seed IndData from historical trendbars
        self.init_ind_data(gen)

    def on_symbol_details_error(self, failure, gen: int = 0):
        if self._stale(gen):
            return
        print(
            f"⚠️  SymbolById failed: {failure.getErrorMessage()} "
            f"— using default pip_size=0.0001 for watched symbols"
        )
        for sid in self.fxpair_arr:
            name = self.symbol_map.get(sid, f"ID_{sid}")
            self.symbol_meta[sid] = SymbolMeta(
                symbol_id=sid,
                name=name,
                digits=5,
                pip_position=4,
                pip_size=0.0001,
                point=0.00001,
            )
            self.symbol_digits[sid] = 5
        self.init_ind_data(gen)

    def on_subscription_error(self, failure, gen: int = 0):
        if self._stale(gen):
            return
        self._session_step_failed("spot subscription", failure)

    def on_symbols_error(self, failure, gen: int = 0):
        if self._stale(gen):
            return
        self._session_step_failed("symbol list", failure)

    def _session_step_failed(self, step: str, failure) -> None:
        """A session-build step failed: drop the connection so the client
        rebuilds the whole session, instead of running half-initialised."""
        self._note(f"❌ Session init: {step} failed ({failure.getErrorMessage()}) — reconnecting")
        self.api._drop_connection(f"{step} failed")

    # ---------------------------------------------------------- historical seed
    def init_ind_data(self, gen: Optional[int] = None):
        """Populate IndData for each watched symbol (PhyBot.InitIndData parity).

        From Open API ProtoOASymbol (via symbol_meta cache):
          pip_size  ← 10^(-pipPosition)   ~ Symbol.PipSize
          point     ← 10^(-digits)        ~ Symbol.TickSize
          digits    ← ProtoOASymbol.digits
          pip_value ← approx lot*pip_size ~ Symbol.PipValue
          dbl_epsilon ← point * 0.1
          period / current_period ← timeframe seconds (M1 → 60)

        Then loads historical trendbars into OHLCV deques.
        """
        gen = self._session if gen is None else gen
        self._hist_pending = len(self.fxpair_arr)
        self._hist_ready = False
        # design.txt: M1 _Period = 60 seconds; bar_interval matches for synthetic clock
        period_secs = period_seconds(self.bar_period)
        self.bar_interval = period_secs

        for symbol_id in self.fxpair_arr:
            old = self.hist_bars.get(symbol_id)
            if old is not None and old.time:
                self._pre_reseed_last_time[symbol_id] = old.time[-1]
            data = IndData(capacity=self.bar_count)
            meta = self.symbol_meta.get(symbol_id)
            if meta is not None:
                apply_symbol_meta(data, meta, period_secs=period_secs)
            else:
                data.symbol_id = symbol_id
                data.symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
                data.pip_size = 0.0001
                data.pip_value = 0.0
                data.point = 0.00001
                data.digits = 5
                data.pip_position = 4
                data.dbl_epsilon = data.point * 0.1
                data.period = period_secs
                data.current_period = float(period_secs)
                data.bars_held = 0
            data.shift = 1  # closed-bar analysis (signals); PhyBot uses 0 for live series
            data.total_orders = 0
            self.hist_bars[symbol_id] = data
            print(
                f"   IndData[{data.symbol_name or symbol_id}]: "
                f"digits={data.digits} pip_size={data.pip_size} "
                f"point={data.point} pip_value={data.pip_value} "
                f"period={data.period}s"
            )

            d = self.api.get_trendbars(
                symbol_id=symbol_id,
                period=self.bar_period,
                count=self.bar_count,
                client_msg_id=f"hist_{symbol_id}",
            )
            d.addCallback(self.on_historical_bars_received, symbol_id, gen)
            d.addErrback(self.on_historical_error, symbol_id, gen)

    def on_historical_bars_received(self, response, symbol_id: int, gen: int = 0):
        if self._stale(gen):
            return
        raw_bars = list(getattr(response, "trendbar", []) or [])
        symbol_name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
        data = self.hist_bars[symbol_id]

        # Decode ProtoOATrendbar delta encoding → absolute OHLCV
        # Round to symbol digits when known (from symbol_digits map / IndData)
        digits = self.symbol_digits.get(symbol_id, getattr(data, "digits", None))
        decoded = [decode_trendbar(bar, digits=digits) for bar in raw_bars]
        # Sort oldest → newest by bar open time (API usually already does this)
        decoded.sort(key=lambda b: b["utc_minutes"])

        loaded = data.load_bars(decoded)
        if decoded:
            self._last_live_bar_minutes[symbol_id] = decoded[-1]["utc_minutes"]
        # Reseed after a reconnect: show the trail the bars it missed (V1-T42)
        last_seen = self._pre_reseed_last_time.pop(symbol_id, None)
        if last_seen is not None:
            missed = [b for b in decoded[:-1] if b["time"] > last_seen]
            self.profit_trail.observe(
                symbol_id, [b["high"] for b in missed], [b["low"] for b in missed]
            )

        # Once-per-seed: indicators + signals only (no live trading until ready)
        trade_sig = SIG.NOSIG
        if loaded:
            trade_sig = self.run_signal_cycle(
                data, symbol_id, execute=False
            )

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
                f"ATR={atr_last:.5f} SIG={trade_sig}"
            )

        self._hist_pending -= 1
        if self._hist_pending <= 0:
            self._on_all_historical_loaded(gen)

    def on_historical_error(self, failure, symbol_id: int, gen: int = 0):
        if self._stale(gen):
            return
        print(
            f"❌ Failed to load historical data for symbol {symbol_id}: "
            f"{failure.getErrorMessage()}"
        )
        self._hist_pending -= 1
        if self._hist_pending <= 0:
            self._on_all_historical_loaded(gen)

    def _on_all_historical_loaded(self, gen: int = 0, attempt: int = 1):
        """After history is in: reconcile positions, then subscribe live bars.
        Trading (``_hist_ready``) resumes only once the book is reconciled
        (V1-T42), so a reconnect never acts on a stale position book."""
        if self._stale(gen):
            return
        print("Reconciling open positions…")
        d = self.executor.sync_positions()
        d.addCallback(self._on_positions_synced, gen)
        d.addErrback(self._on_positions_sync_error, gen, attempt)

    def _on_positions_synced(self, positions, gen: int = 0):
        if self._stale(gen):
            return
        n = len(positions) if positions is not None else 0
        print(f"✅ Position book synced ({n} open under label={self.executor.label})")
        # PhyBot: TotalOrders = positions with our label
        for sid in self.hist_bars:
            self.hist_bars[sid].total_orders = 0
            self.hist_bars[sid].trade_position = SIG.NOSIG
        for sid, pos in (positions or {}).items():
            name = self.symbol_map.get(sid, sid)
            print(f"   {name}: {pos.side.name} vol={pos.volume} id={pos.position_id}")
            if sid in self.hist_bars:
                self.hist_bars[sid].trade_position = pos.side
                self.hist_bars[sid].total_orders = 1
        self._hist_ready = True
        self._note(f"▶️  Session {gen} ready — trading enabled ({n} open position(s))")
        self._subscribe_live_trendbars(gen)

    def _on_positions_sync_error(self, failure, gen: int = 0, attempt: int = 1):
        if self._stale(gen):
            return
        why = failure.getErrorMessage()
        if attempt < RECONCILE_ATTEMPTS:
            self._note(f"⚠️  Position reconcile failed ({why}) — retry {attempt + 1}/{RECONCILE_ATTEMPTS} in {RECONCILE_RETRY_S:g}s")
            from twisted.internet import reactor
            reactor.callLater(RECONCILE_RETRY_S, self._on_all_historical_loaded, gen, attempt + 1)
            return
        self._session_step_failed("position reconcile", failure)

    def _subscribe_live_trendbars(self, gen: int = 0):
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
            # No live bars = no trading: rebuild the session (V1-T42)
            d.addErrback(
                lambda f, sid=symbol_id: None
                if self._stale(gen)
                else self._session_step_failed(f"live trendbar subscribe ({sid})", f)
            )

    # --------------------------------------------------------------- messages
    def on_message(self, message):
        """Dispatch inbound extracted protobuf messages."""
        # Execution events (fills / closes) — update order book
        if hasattr(message, "executionType"):
            self.executor.on_execution_event(message)
            if self.trade_log.enabled:
                fill = parse_deal_fill(message)
                if fill is not None:
                    self.trade_log.on_fill(fill)
            return

        # Spot / price path
        self.on_tick(message)

    def on_tick(self, message):
        """Handle spot events (live prices / trendbars)."""
        # RPC responses (e.g. GetTrendbarsRes) and other events (e.g.
        # SymbolChangedEvent with repeated symbolId) also reach on_message;
        # only ProtoOASpotEvent carries live prices.
        if not is_spot_event(message):
            return

        symbol_id = int(message.symbolId)
        if symbol_id not in self.hist_bars:
            return
        self._update_quote(symbol_id, message)

        # Prefer official live trendbars when present (after subscribe_live_trendbars)
        live_bars = list(getattr(message, "trendbar", []) or [])
        if live_bars:
            for bar in live_bars:
                self._apply_live_trendbar(symbol_id, bar)
            return

        # Fallback: synthesize bars from mid price if no live trendbar payload.
        # Not once live trendbars have arrived: after a host sleep the first
        # spot often has no trendbar, and a synthetic bar would commit the
        # stale pre-sleep minute (V1-T39).
        if symbol_id in self._live_seen:
            return
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
        self._live_seen.add(symbol_id)
        if symbol_id in self._resyncing:
            return  # history reload in flight; it replaces the series
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

        step = max(int(self.bar_interval) // 60, 1)
        if minutes > prev + step and self._hist_ready:
            # Missed bars (host sleep / feed stall): reload instead of appending
            # across a hole (V1-T39)
            self._resync_history(symbol_id, minutes - prev - step, decoded)
            return

        if minutes > prev:
            # New bar opened → previous bar is complete
            data.new_bar = True
            data.prev_bar_open_time = data.time[-1] if data.time else decoded["time"]
            data.new_bar_open_time = decoded["time"]
            data.append_decoded_bar(decoded)
            self._last_live_bar_minutes[symbol_id] = minutes

            # Live cycle is async (PnL fetch first); logging happens in finish
            self.run_signal_cycle(data, symbol_id, execute=True, log_bar=True)
            self._fire_on_bar_event()

    # ------------------------------------------------- bar gap resync (V1-T39)
    def _resync_history(self, symbol_id: int, gap_min: int, decoded: dict) -> None:
        """Reload the trendbar history after a gap, then run one live cycle."""
        name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
        self._note(f"⚠️  [{name}] bar gap {gap_min} min (host sleep / feed stall) — reloading history")
        self._resyncing.add(symbol_id)
        d = self.api.get_trendbars(
            symbol_id=symbol_id,
            period=self.bar_period,
            count=self.bar_count,
            client_msg_id=f"resync_{symbol_id}",
        )
        d.addCallback(self._on_resync_bars, symbol_id, decoded)
        d.addErrback(self._on_resync_error, symbol_id, decoded)

    def _on_resync_bars(self, response, symbol_id: int, trigger: dict) -> None:
        raw_bars = list(getattr(response, "trendbar", []) or [])
        data = self.hist_bars[symbol_id]
        digits = self.symbol_digits.get(symbol_id, getattr(data, "digits", None))
        decoded = sorted(
            (decode_trendbar(bar, digits=digits) for bar in raw_bars),
            key=lambda b: b["utc_minutes"],
        )
        if not decoded:
            self._on_resync_error(None, symbol_id, trigger)
            return
        last_seen = data.time[-1] if data.time else None
        data.load_bars(decoded)
        self._last_live_bar_minutes[symbol_id] = decoded[-1]["utc_minutes"]
        self._resyncing.discard(symbol_id)
        # Bars the trail missed while asleep (closed bars only; [-1] is forming)
        missed = [b for b in decoded[:-1] if last_seen is None or b["time"] > last_seen]
        self.profit_trail.observe(
            symbol_id, [b["high"] for b in missed], [b["low"] for b in missed]
        )
        name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
        self._note(f"✅ [{name}] history reloaded ({len(decoded)} bars, {len(missed)} new) — resuming")
        data.new_bar = True
        self.run_signal_cycle(data, symbol_id, execute=True, log_bar=True, log_tag="resync")
        self._fire_on_bar_event()

    def _on_resync_error(self, failure, symbol_id: int, trigger: dict) -> None:
        """Reload failed: fall back to appending the live bar across the gap."""
        self._resyncing.discard(symbol_id)
        why = failure.getErrorMessage() if failure is not None else "no bars returned"
        name = self.symbol_map.get(symbol_id, f"ID_{symbol_id}")
        self._note(f"❌ [{name}] history reload failed: {why} — continuing with the gap")
        data = self.hist_bars[symbol_id]
        data.new_bar = True
        data.prev_bar_open_time = data.time[-1] if data.time else trigger["time"]
        data.new_bar_open_time = trigger["time"]
        data.append_decoded_bar(trigger)
        self._last_live_bar_minutes[symbol_id] = trigger["utc_minutes"]
        self.run_signal_cycle(data, symbol_id, execute=True, log_bar=True)
        self._fire_on_bar_event()

    def _on_disconnected(self, reason) -> None:
        """The client already logged the drop (on_status); pause trading until
        on_init rebuilds the session and reconciles (V1-T42)."""
        _ = reason
        self._hist_ready = False
        self._note("⏸️  Trading paused until the session is rebuilt")

    def _note(self, msg: str) -> None:
        """Console + trade log (ops events the log must show)."""
        if self.trade_log.enabled:
            self.trade_log.note(msg)
            if self.trade_log.config.console:
                return  # the trade log already echoes to the console
        print(msg)

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

            self.run_signal_cycle(
                data, symbol_id, execute=True, log_bar=True, log_tag="synthetic"
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
        """Recompute indicator snapshot once for this IndData (per cycle)."""
        return compute_indicators(data)

    # ---------------------------------------------------------- position PnL
    # Timeout for a single reconcile / unrealized-PnL RPC (not per parallel race)
    _PNL_TIMEOUT_S = 30

    def fetch_position_pnls(self) -> defer.Deferred:
        """
        Build Sequence[PositionPnL] for SanUtils (TASK 10 / 11).

        Single-flight across symbols: if a fetch is already in progress, join it
        (same snapshot) instead of starting another reconcile + PnL pair.

        Internal chain (one at a time):
          1. api.reconcile(timeout=30)          → open positions + label/symbol
          2. api.get_unrealized_pnl(timeout=30) → netUnrealizedPnL per positionId
          3. merge → list[PositionPnL]

        dry_run: empty list (avg profits = 0).

        Each caller gets its own Deferred so strategy callbacks can transform the
        result without corrupting sibling cycles that joined the same fetch.
        """
        if self.executor.dry_run:
            return defer.succeed([])

        if self._pnl_fetch_inflight is not None:
            return self._share_pnl_result(self._pnl_fetch_inflight)

        master = self._start_pnl_fetch()
        self._pnl_fetch_inflight = master

        def _clear_inflight(result):
            if self._pnl_fetch_inflight is master:
                self._pnl_fetch_inflight = None
            return result

        master.addBoth(_clear_inflight)
        return self._share_pnl_result(master)

    def _share_pnl_result(self, master: defer.Deferred) -> defer.Deferred:
        """Fan-out: new Deferred fires with master's list without chaining SIG transforms."""
        out: defer.Deferred = defer.Deferred()

        def _ok(result):
            if not out.called:
                # Shallow copy so consumers cannot mutate a shared list in place
                out.callback(list(result) if isinstance(result, list) else result)
            return result

        def _err(failure):
            if not out.called:
                out.errback(failure)
            return failure

        master.addCallbacks(_ok, _err)
        return out

    def _start_pnl_fetch(self) -> defer.Deferred:
        """Kick off one reconcile → unrealized PnL chain (unique clientMsgIds)."""
        self._pnl_req_seq += 1
        seq = self._pnl_req_seq
        d = self.api.reconcile(
            client_msg_id=f"pnl_reconcile_{seq}",
            timeout=self._PNL_TIMEOUT_S,
        )
        d.addCallback(self._after_reconcile_fetch_pnl, seq)
        d.addErrback(self._on_reconcile_pnl_error)
        return d

    def _after_reconcile_fetch_pnl(self, rec, seq: int) -> defer.Deferred:
        """Only after reconcile succeeds, request unrealized PnL."""
        try:
            self.executor.sync_from_reconcile(rec)
        except Exception as e:
            print(f"⚠️  sync_from_reconcile: {e}")

        d_pnl = self.api.get_unrealized_pnl(
            client_msg_id=f"pnl_unrealized_{seq}",
            timeout=self._PNL_TIMEOUT_S,
        )
        d_pnl.addCallback(lambda pnl_res: self._build_position_pnls(rec, pnl_res))
        d_pnl.addErrback(lambda fail: self._build_position_pnls(rec, None, fail))
        return d_pnl

    def _on_reconcile_pnl_error(self, failure) -> List[PositionPnL]:
        print(f"⚠️  reconcile for PnL failed: {failure.getErrorMessage()}")
        return []

    def _build_position_pnls(
        self,
        rec,
        pnl_res=None,
        pnl_failure=None,
    ) -> List[PositionPnL]:
        """Merge reconcile positions with unrealized PnL (after both steps)."""
        unrealized: Dict[int, float] = {}
        if pnl_failure is not None:
            print(
                f"⚠️  unrealized PnL failed: {pnl_failure.getErrorMessage()} "
                f"— using 0 for net_profit"
            )
        elif pnl_res is not None:
            money_digits = int(getattr(pnl_res, "moneyDigits", 0) or 0)
            for u in getattr(pnl_res, "positionUnrealizedPnL", []) or []:
                pid = int(getattr(u, "positionId", 0) or 0)
                net_raw = getattr(u, "netUnrealizedPnL", 0) or 0
                unrealized[pid] = scale_money(net_raw, money_digits)

        label_want = self.executor.label or ""
        out: List[PositionPnL] = []
        for pos in getattr(rec, "position", []) or []:
            status = getattr(pos, "positionStatus", 1)
            if status == 2:  # POSITION_STATUS_CLOSED
                continue
            trade = getattr(pos, "tradeData", None)
            if trade is None:
                continue
            lbl = str(getattr(trade, "label", "") or "")
            if label_want and lbl != label_want:
                continue
            pid = int(getattr(pos, "positionId", 0) or 0)
            sid = int(getattr(trade, "symbolId", 0) or 0)
            net = float(unrealized.get(pid, 0.0))
            out.append(
                PositionPnL(
                    net_profit=net,
                    symbol_id=sid,
                    label=lbl,
                    position_id=pid,
                    is_open=True,
                )
            )
        return out

    def run_signal_cycle(
        self,
        data: IndData,
        symbol_id: Optional[int] = None,
        *,
        total_trade_profits: float = 0.0,
        execute: bool = True,
        log_bar: bool = False,
        log_tag: str = "",
    ) -> Union[SIG, defer.Deferred]:
        """
        Once-per-cycle pipeline (TASK 1 + 2 + 10):

          1. compute_indicators
          2. [live] fetch_position_pnls → SanUtils.get_total_trade_profits
          3. strategies.evaluate(..., total_trade_profits)
          4. executor.handle_signal

        Live path waits for single-flight reconcile + unrealized PnL before
        strategy/execution. Concurrent symbols join the same in-flight snapshot.
        Seed path (execute=False) skips PnL and is sync.
        """
        self.refresh_indicators(data)
        sid = symbol_id if symbol_id is not None else data.symbol_id
        need_live = bool(execute and self._hist_ready)

        if not need_live:
            return self._finish_signal_cycle(
                data,
                sid,
                total_trade_profits=total_trade_profits,
                execute=False,
                log_bar=log_bar,
                log_tag=log_tag,
            )

        # Single-flight PnL; each cycle has its own Deferred for strategy finish
        d = self.fetch_position_pnls()
        d.addCallback(
            self._on_position_pnls_ready, data, sid, log_bar, log_tag
        )
        d.addErrback(
            self._on_position_pnls_error, data, sid, log_bar, log_tag
        )
        return d

    def _on_position_pnls_ready(
        self,
        pnls: List[PositionPnL],
        data: IndData,
        sid: int,
        log_bar: bool,
        log_tag: str,
    ) -> SIG:
        avg = self.utils.get_total_trade_profits(
            pnls, label=self.executor.label
        )
        self.last_total_trade_profits = avg
        return self._finish_signal_cycle(
            data,
            sid,
            total_trade_profits=avg,
            execute=True,
            log_bar=log_bar,
            log_tag=log_tag,
        )

    def _on_position_pnls_error(
        self,
        failure,
        data: IndData,
        sid: int,
        log_bar: bool,
        log_tag: str,
    ) -> SIG:
        print(
            f"⚠️  position PnL chain failed: {failure.getErrorMessage()} "
            f"— using total_trade_profits=0"
        )
        self.last_total_trade_profits = 0.0
        return self._finish_signal_cycle(
            data,
            sid,
            total_trade_profits=0.0,
            execute=True,
            log_bar=log_bar,
            log_tag=log_tag,
        )

    def _finish_signal_cycle(
        self,
        data: IndData,
        sid: int,
        *,
        total_trade_profits: float,
        execute: bool,
        log_bar: bool = False,
        log_tag: str = "",
    ) -> SIG:
        """Strategy + optional order after indicators (and PnL if live) are ready."""
        trade_sig = self.strategies.evaluate(
            data,
            total_trade_profits=total_trade_profits,
            strategy=self.strategies.active,
        )
        t_sig = self.strategies.last_t_sig
        if t_sig is not None:
            self.last_t_sig[sid] = t_sig
        self.last_trade_sig[sid] = trade_sig
        self.last_total_trade_profits = float(total_trade_profits)

        self._sync_position_state(data, sid, trade_sig)

        if execute and self._hist_ready:
            reason = None
            if self.strategies.active in self.trail_strategies:
                trade_sig = self._apply_profit_trail(data, sid, trade_sig)
                self.last_trade_sig[sid] = trade_sig
                if self.profit_trail.last_fired.get(sid):
                    reason = (
                        f"CLOSE (trail: giveback >= {self.profit_trail.trail_atr:g} ATR "
                        f"after >= {self.profit_trail.activate_atr:g} ATR profit)"
                    )
            if self.trade_log.enabled:
                self.trade_log.on_decision(
                    data=data,
                    symbol_id=sid,
                    trade_sig=trade_sig,
                    position_side=self.executor.position_side(sid),
                    quote=self.quotes.get(sid),
                    t_sig=t_sig,
                    active=self.strategies.active,
                    reason=reason,
                )
            self.executor.handle_signal(sid, trade_sig)
            self._sync_position_state(data, sid, trade_sig)

        if log_bar:
            self._log_cycle_result(data, sid, trade_sig, total_trade_profits, log_tag)

        return trade_sig

    def _apply_profit_trail(self, data: IndData, sid: int, trade_sig: SIG) -> SIG:
        """V1-T38: ATR trail on the last closed bar ([-2]; [-1] is forming)."""
        i = -2 if len(data.close) >= 2 else -1
        try:
            high, low, close = data.high[i], data.low[i], data.close[i]
        except IndexError:
            return trade_sig
        atr = data.atr[i] if len(data.atr) >= -i else float("nan")
        pos = self.executor.get_position(sid)
        return self.profit_trail.apply(
            sid,
            trade_sig,
            self.executor.position_side(sid),
            entry_price=pos.entry_price if pos is not None else float("nan"),
            high=high,
            low=low,
            close=close,
            atr=atr,
        )

    def _log_cycle_result(
        self,
        data: IndData,
        sid: int,
        trade_sig: SIG,
        avg_pnl: float,
        log_tag: str,
    ) -> None:
        symbol_name = self.symbol_map.get(sid, f"ID_{sid}")
        if self.trade_log.enabled:
            self.trade_log.on_cycle(
                data=data,
                symbol_id=sid,
                symbol_name=symbol_name,
                t_sig=self.last_t_sig.get(sid),
                trade_sig=trade_sig,
                active=self.strategies.active,
                avg_pnl=avg_pnl,
                strategies=self.strategies,
                position_side=self.executor.position_side(sid)
                if self.executor.has_position(sid) else None,
                tag=log_tag,
            )
            return
        tag = f" ({log_tag})" if log_tag else ""
        if len(data.close) >= 2:
            closed = data.time[-2] + timedelta(seconds=int(data.period or self.bar_interval))
            print(
                f"New bar closed: {closed:%Y-%m-%d %H:%M:%S} [{symbol_name}] "
                f"O={data.open[-2]:.5f} H={data.high[-2]:.5f} "
                f"L={data.low[-2]:.5f} C={data.close[-2]:.5f} "
                f"V={data.tick_volume[-2]:.0f} "
                f"ATR={data.atr[-1] if data.atr else float('nan'):.5f} "
                f"ADX={data.adx[-1] if data.adx else float('nan'):.2f} "
                f"avgPnL={avg_pnl:.2f} SIG={trade_sig}{tag}"
            )
        else:
            print(
                f"Signal [{symbol_name}] avgPnL={avg_pnl:.2f} "
                f"SIG={trade_sig}{tag}"
            )

    def _update_quote(self, symbol_id: int, message) -> None:
        """Cache bid/ask; a spot event may carry only the side that changed."""
        bid, ask = self.quotes.get(symbol_id, (0.0, 0.0))
        for name in ("bid", "ask"):
            try:
                present = message.HasField(name)
            except (AttributeError, ValueError):
                present = bool(getattr(message, name, 0))
            if present:
                value = relative_to_price(getattr(message, name))
                bid, ask = (value, ask) if name == "bid" else (bid, value)
        self.quotes[symbol_id] = (bid, ask)

    def _sync_position_state(
        self,
        data: IndData,
        symbol_id: int,
        trade_sig: SIG,
    ) -> None:
        """Mirror executor open-position book onto IndData trading fields."""
        if self.executor.has_position(symbol_id):
            data.trade_position = self.executor.position_side(symbol_id)
            data.total_orders = 1
            return

        data.total_orders = 0
        if trade_sig in (SIG.BUY, SIG.SELL):
            data.trade_position = trade_sig
        else:
            data.trade_position = SIG.NOSIG

    def _fire_on_bar_event(self):
        for handler in self.on_bar_handlers:
            try:
                handler(self.hist_bars)
            except Exception as e:
                print(f"Error in on_bar handler: {e}")

    def add_on_bar_handler(self, handler):
        self.on_bar_handlers.append(handler)

    def run(self):
        from twisted.internet import reactor

        # Flush the trade log's period summary on Ctrl-C / reactor stop
        reactor.addSystemEventTrigger("before", "shutdown", self.trade_log.close)
        self.api.run()
