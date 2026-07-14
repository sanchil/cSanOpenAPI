from datetime import datetime, timezone
from typing import Dict, List, Optional, Union
import time

from twisted.internet import defer

# System layer
from csys.ctrader_api import CTraderOpenAPI, OrderExecutor
from csys.ctrader_api.client import scale_money
from csys.ctypes import (
    DEFAULT_BAR_CAPACITY,
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
from capp.strategies import CStrategies
from capp.utils import PositionPnL, SanUtils


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
        → fetch_position_pnls (reconcile + unrealized PnL; finish-callback)
        → SanUtils.get_total_trade_profits
        → strategies.evaluate
        → OrderExecutor.handle_signal
    """

    def __init__(
        self,
        config,
        active_strategy: int = 1,
        *,
        trade_volume: float = 10000.0,
        trade_label: str = "GrokApp",
        trading_enabled: bool = True,
        dry_run: bool = False,
    ):
        self.api = CTraderOpenAPI(config)
        self.api.on_ready = self.on_init
        self.api.on_message = self.on_message

        # Per-symbol historical data: Dict[symbol_id, IndData]
        self.hist_bars: Dict[int, IndData] = {}

        self.symbol_map: Dict[int, str] = {}  # id → name (light list)
        # Full symbol meta from ProtoOASymbolById (digits, pip_size, point, …)
        self.symbol_meta: Dict[int, SymbolMeta] = {}
        # Convenience map: symbol_id → digits (always available after meta load)
        self.symbol_digits: Dict[int, int] = {}
        self.current_bar: Dict[int, dict] = {}
        self.bar_interval = 60  # seconds between synthetic bars (M1)
        self.bar_period = "M1"
        self.bar_count = DEFAULT_BAR_CAPACITY
        self.last_bar_time: Optional[float] = None
        self.fxpair_arr = [1, 4]  # EURUSD, USDJPY (demo symbol ids; broker-dependent)
        # Track open-minute of the last committed live bar per symbol
        self._last_live_bar_minutes: Dict[int, int] = {}
        self._hist_pending = 0
        self._hist_ready = False

        # design.txt: signals → strategies (once per cycle)
        self.signals = SanSignals(default_shift=1)
        self.strategies = CStrategies(self.signals, active=active_strategy)
        self.last_t_sig: Dict[int, T_SIG] = {}
        self.last_trade_sig: Dict[int, SIG] = {}
        self.last_total_trade_profits: float = 0.0

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

        # Step 3: full ProtoOASymbol for watched ids → digits / pip_size / point
        d = self.api.get_symbols_by_id(
            self.fxpair_arr,
            client_msg_id="symbols_by_id",
        )
        d.addCallback(self.on_symbol_details_received)
        d.addErrback(self.on_symbol_details_error)

    def on_symbol_details_received(self, response):
        """Cache symbol_id → SymbolMeta (digits, pipPosition → pip_size)."""
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
        self.init_ind_data()

    def on_symbol_details_error(self, failure):
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
        self.init_ind_data()

    def on_subscription_error(self, failure):
        print(f"❌ Subscription failed: {failure}")

    def on_symbols_error(self, failure):
        print(f"❌ Failed to load symbols: {failure}")

    # ---------------------------------------------------------- historical seed
    def init_ind_data(self):
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
        self._hist_pending = len(self.fxpair_arr)
        self._hist_ready = False
        # design.txt: M1 _Period = 60 seconds; bar_interval matches for synthetic clock
        period_secs = period_seconds(self.bar_period)
        self.bar_interval = period_secs

        for symbol_id in self.fxpair_arr:
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
            d.addCallback(self.on_historical_bars_received, symbol_id)
            d.addErrback(self.on_historical_error, symbol_id)

    def on_historical_bars_received(self, response, symbol_id: int):
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
        """After history is in: reconcile positions, then subscribe live bars."""
        self._hist_ready = True
        print("Reconciling open positions…")
        d = self.executor.sync_positions()
        d.addCallback(self._on_positions_synced)
        d.addErrback(self._on_positions_sync_error)

    def _on_positions_synced(self, positions):
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
        self._subscribe_live_trendbars()

    def _on_positions_sync_error(self, failure):
        print(f"⚠️  Position reconcile failed: {failure.getErrorMessage()} — continuing")
        self._subscribe_live_trendbars()

    def _subscribe_live_trendbars(self):
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

    # --------------------------------------------------------------- messages
    def on_message(self, message):
        """Dispatch inbound extracted protobuf messages."""
        # Execution events (fills / closes) — update order book
        if hasattr(message, "executionType"):
            self.executor.on_execution_event(message)
            return

        # Spot / price path
        self.on_tick(message)

    def on_tick(self, message):
        """Handle spot events (live prices / trendbars)."""
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

            # Live cycle is async (PnL fetch first); logging happens in finish
            self.run_signal_cycle(data, symbol_id, execute=True, log_bar=True)
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
    def fetch_position_pnls(self) -> defer.Deferred:
        """
        Build Sequence[PositionPnL] for SanUtils (TASK 10).

        Calls (both must finish before merge — DeferredList):
          1. api.reconcile()            → open positions + label/symbol
          2. api.get_unrealized_pnl()   → netUnrealizedPnL per positionId

        dry_run / no network: empty list (avg profits = 0).
        """
        if self.executor.dry_run:
            return defer.succeed([])

        d_rec = self.api.reconcile(client_msg_id="pnl_reconcile")
        d_pnl = self.api.get_unrealized_pnl(client_msg_id="pnl_unrealized")
        return defer.DeferredList(
            [d_rec, d_pnl], consumeErrors=True
        ).addCallback(self._merge_position_pnls)

    def _merge_position_pnls(self, results) -> List[PositionPnL]:
        """Merge reconcile + unrealized PnL after both Deferreds complete."""
        (ok_rec, rec_or_fail), (ok_pnl, pnl_or_fail) = results

        if not ok_rec:
            err = getattr(rec_or_fail, "getErrorMessage", lambda: str(rec_or_fail))()
            print(f"⚠️  reconcile for PnL failed: {err}")
            return []

        rec = rec_or_fail
        # Keep executor book aligned with broker
        try:
            self.executor.sync_from_reconcile(rec)
        except Exception as e:
            print(f"⚠️  sync_from_reconcile: {e}")

        unrealized: Dict[int, float] = {}
        money_digits = 0
        if ok_pnl:
            pnl_res = pnl_or_fail
            money_digits = int(getattr(pnl_res, "moneyDigits", 0) or 0)
            for u in getattr(pnl_res, "positionUnrealizedPnL", []) or []:
                pid = int(getattr(u, "positionId", 0) or 0)
                net_raw = getattr(u, "netUnrealizedPnL", 0) or 0
                unrealized[pid] = scale_money(net_raw, money_digits)
        else:
            err = getattr(pnl_or_fail, "getErrorMessage", lambda: str(pnl_or_fail))()
            print(f"⚠️  unrealized PnL failed: {err} — using 0 for net_profit")

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
            # Prefer Open API net unrealized (mark-to-market)
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

        Live path waits for reconcile + unrealized PnL (finish-callback) before
        strategy/execution. Seed path (execute=False) skips PnL and is sync.
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

        # Dependent calls must complete before strategy (finish-callback pattern)
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
        )
        t_sig = self.strategies.last_t_sig
        if t_sig is not None:
            self.last_t_sig[sid] = t_sig
        self.last_trade_sig[sid] = trade_sig
        self.last_total_trade_profits = float(total_trade_profits)

        self._sync_position_state(data, sid, trade_sig)

        if execute and self._hist_ready:
            self.executor.handle_signal(sid, trade_sig)
            self._sync_position_state(data, sid, trade_sig)

        if log_bar:
            self._log_cycle_result(data, sid, trade_sig, total_trade_profits, log_tag)

        return trade_sig

    def _log_cycle_result(
        self,
        data: IndData,
        sid: int,
        trade_sig: SIG,
        avg_pnl: float,
        log_tag: str,
    ) -> None:
        symbol_name = self.symbol_map.get(sid, f"ID_{sid}")
        tag = f" ({log_tag})" if log_tag else ""
        if len(data.close) >= 2:
            print(
                f"New bar closed [{symbol_name}] "
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
        self.api.run()
