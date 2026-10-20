"""High-level Python interface to the cTrader Open API.

Connection health (V1-T42, TASKS.txt TASK 18):
  - Heartbeat: ProtoHeartbeatEvent every HEARTBEAT_INTERVAL s (the SDK only
    sends one after 20 s of silence; cTrader expects ~10 s).
  - Session rebuild: every TCP connect runs the full chain application auth
    → (token refresh if due) → account auth → on_ready. The app's on_ready
    resubscribes, reseeds history and reconciles positions. ClientService
    reconnects with RETRY_* backoff.
  - Readiness gate: send() fails fast with NotReadyError unless the session
    is authenticated, so nothing reaches a blank or half-built session.
  - Watchdog: inbound silence > PROBE_AFTER s → ProtoOAVersionReq probe;
    > DEAD_AFTER s, or a wall-clock jump (host sleep), → abort the socket and
    reconnect. TCP keepalive is a second layer.
  - Tokens: ProtoOARefreshTokenReq in-session on CH_ACCESS_TOKEN_INVALID /
    OA_AUTH_TOKEN_EXPIRED, on ProtoOAAccountsTokenInvalidatedEvent, and ahead
    of a known expiry; the new pair is persisted (config.save_token_store).
  - Auth failures: before the first ready session they are fatal (bad
    credentials); afterwards the socket is dropped and retried, fatal after
    MAX_AUTH_FAILURES in a row. ``fatal`` lets main.py exit non-zero so a
    supervisor (systemd Restart=always) restarts the process.
Every connect / disconnect / auth / refresh / watchdog event goes to on_status.
"""

from __future__ import annotations

import calendar
import datetime
import socket
import time
from collections.abc import Callable
from typing import Any

from ctrader_open_api import Auth, Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAAccountLogoutReq,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAAssetClassListReq,
    ProtoOAAssetListReq,
    ProtoOACancelOrderReq,
    ProtoOAClosePositionReq,
    ProtoOADealOffsetListReq,
    ProtoOAErrorRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetPositionUnrealizedPnLReq,
    ProtoOAGetTickDataReq,
    ProtoOAGetTrendbarsReq,
    ProtoOANewOrderReq,
    ProtoOAOrderDetailsReq,
    ProtoOAOrderListByPositionIdReq,
    ProtoOAAccountDisconnectEvent,
    ProtoOAAccountsTokenInvalidatedEvent,
    ProtoOAClientDisconnectEvent,
    ProtoOAReconcileReq,
    ProtoOARefreshTokenReq,
    ProtoOARefreshTokenRes,
    ProtoOASubscribeLiveTrendbarReq,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOASymbolByIdReq,
    ProtoOASymbolCategoryListReq,
    ProtoOASymbolsListReq,
    ProtoOATraderReq,
    ProtoOAUnsubscribeSpotsReq,
    ProtoOAVersionReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOAQuoteType,
    ProtoOATradeSide,
    ProtoOATrendbarPeriod,
)
from twisted.application.internet import backoffPolicy
from twisted.internet import defer, reactor, task
from twisted.python.failure import Failure

from .config import CTraderConfig, load_config, save_token_store

MessageCallback = Callable[[Any], None]
ErrorCallback = Callable[[Any], None]
StatusCallback = Callable[[str], None]

HEARTBEAT_INTERVAL = 10.0     # s between ProtoHeartbeatEvent sends
WATCHDOG_INTERVAL = 5.0       # s between watchdog checks
PROBE_AFTER = 20.0            # s of inbound silence → version probe
DEAD_AFTER = 40.0             # s of inbound silence → force reconnect
SLEEP_JUMP = 30.0             # s wall-clock jump between watchdog ticks → host slept
AUTH_TIMEOUT = 15             # s per auth / refresh request
TOKEN_REFRESH_MARGIN = 86400.0  # refresh when a known expiry is < 1 day away
TOKEN_CHECK_INTERVAL = 3600.0   # s between proactive expiry checks
MAX_AUTH_FAILURES = 5         # consecutive failed session builds → fatal
RETRY_INITIAL_DELAY = 1.0     # ClientService reconnect backoff
RETRY_MAX_DELAY = 30.0
TOKEN_ERRORS = frozenset({"CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED"})


class NotReadyError(RuntimeError):
    """Request refused: no authenticated session (connecting / re-authenticating)."""


class ApiError(RuntimeError):
    """A request answered with ProtoOAErrorRes (was passed on as a success
    before V1-T42 — e.g. a failed reconcile wiped the local position book)."""

    def __init__(self, code: str, description: str = "") -> None:
        super().__init__(f"{code} {description}".rstrip())
        self.code = code


class AuthError(RuntimeError):
    def __init__(self, stage: str, code: str, description: str = "") -> None:
        super().__init__(f"{stage} auth failed: {code} {description}".rstrip())
        self.stage = stage
        self.code = code


def scale_money(value: int | float, money_digits: int = 0) -> float:
    """Convert Open API monetary protocol integers to real amounts.

    E.g. moneyDigits=8 → real = value / 10^8.
    """
    digits = int(money_digits or 0)
    if digits <= 0:
        return float(value)
    return float(value) / (10.0 ** digits)


def is_spot_event(message: Any) -> bool:
    """True only for ProtoOASpotEvent (bid/ask + live trendbar pushes).

    The SDK hands *every* inbound message to the message callback — including
    RPC responses such as ProtoOAGetTrendbarsRes (scalar symbolId + trendbar[])
    and events such as ProtoOASymbolChangedEvent (repeated symbolId) — before
    the request Deferred fires. Price handling must ignore all of those.
    """
    return isinstance(message, ProtoOASpotEvent)


class CTraderOpenAPI:
    """Wrapper around the official ctrader-open-api SDK."""

    def __init__(self, config: CTraderConfig | None = None) -> None:
        self.config = config or load_config()
        self._client = Client(
            self.config.protobuf_host,
            self.config.protobuf_port,
            TcpProtocol,
            retryPolicy=backoffPolicy(
                initialDelay=RETRY_INITIAL_DELAY, maxDelay=RETRY_MAX_DELAY
            ),
        )
        self._ready = False
        self._shutting_down = False
        self._current_account_id: int | None = None

        # Session health (V1-T42)
        self._protocol: Any = None
        self._connects = 0
        self._connected_at: float | None = None
        self._last_rx = time.time()
        self._last_tick = time.time()
        self._probe_inflight = False
        self._down_since: float | None = time.time()
        self._down_logged = 0.0
        self._ever_ready = False
        self._auth_failures = 0
        self.fatal: str | None = None
        self._heartbeat = task.LoopingCall(self._send_heartbeat)
        self._watchdog = task.LoopingCall(self._watchdog_tick)
        self._token_check = task.LoopingCall(self._token_tick)

        # Tokens (config is frozen; refreshed values live here)
        self._access_token = self.config.access_token
        self._refresh_token = self.config.refresh_token
        self._token_expires_at = self.config.token_expires_at
        self._refreshing: defer.Deferred | None = None
        self._refresh_waiters: list[defer.Deferred] = []

        self.on_ready: Callable[[], None] | None = None
        self.on_message: MessageCallback | None = None
        self.on_error: ErrorCallback | None = None
        self.on_disconnected: Callable[[Any], None] | None = None
        self.on_status: StatusCallback | None = None

        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)
        # The SDK passes the protocol to Client._connected but only the client
        # to our callback; capture it (heartbeat, keepalive, forced drops).
        sdk_connected = self._client._connected

        def _capture(protocol: Any) -> None:
            self._protocol = protocol
            _enable_keepalive(protocol)
            sdk_connected(protocol)

        self._client._connected = _capture

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def account_id(self) -> int:
        return self._current_account_id or self.config.account_id

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)
        else:
            print(msg)

    def connect(self) -> None:
        """Start the TCP connection to cTrader (reconnects automatically)."""
        print(
            f"Connecting to {self.config.protobuf_host}:{self.config.protobuf_port} "
            f"(host={self.config.host}, account={self.config.account_id})..."
        )
        self._shutting_down = False
        for loop, interval in (
            (self._heartbeat, HEARTBEAT_INTERVAL),
            (self._watchdog, WATCHDOG_INTERVAL),
            (self._token_check, TOKEN_CHECK_INTERVAL),
        ):
            if not loop.running:
                loop.start(interval, now=False)
        self._client.startService()

    def run(self) -> None:
        """Connect and block on the Twisted reactor."""
        self.connect()
        reactor.run()

    def disconnect(self) -> defer.Deferred:
        """Log out and close the connection cleanly."""
        self._shutting_down = True
        self._ready = False
        for loop in (self._heartbeat, self._watchdog, self._token_check):
            if loop.running:
                loop.stop()

        if self._client.isConnected and self._current_account_id is not None:
            logout = ProtoOAAccountLogoutReq()
            logout.ctidTraderAccountId = self._current_account_id
            return self._client.send(logout, responseTimeoutInSeconds=3).addBoth(
                self._finish_disconnect
            )

        return self._finish_disconnect(None)

    def stop(self) -> None:
        """Disconnect and stop the Twisted reactor."""
        self.disconnect()

    def _finish_disconnect(self, _result: Any) -> None:
        self._client.stopService()
        if reactor.running:
            reactor.stop()

    def _fail_fatal(self, why: str) -> None:
        """Unrecoverable: stop so a supervisor restarts the process."""
        self.fatal = why
        self._status(f"❌ FATAL: {why} — stopping")
        self.disconnect()

    def refresh_access_token(self) -> defer.Deferred:
        """Refresh the OAuth token pair in-session (ProtoOARefreshTokenReq).

        Needs an application-authenticated connection and
        CTRADER_REFRESH_TOKEN. Concurrent callers share one request. Fires
        with the new access token; the pair is persisted to the token store.
        """
        if self._refreshing is not None:
            waiter = defer.Deferred()   # joiners never chain onto the master
            self._refresh_waiters.append(waiter)
            return waiter
        if not self._refresh_token:
            return defer.fail(ValueError("CTRADER_REFRESH_TOKEN is not set — cannot refresh"))

        request = ProtoOARefreshTokenReq()
        request.refreshToken = self._refresh_token
        d = self._client.send(request, responseTimeoutInSeconds=AUTH_TIMEOUT)

        def _done(message):
            res = Protobuf.extract(message)
            if message.payloadType != ProtoOARefreshTokenRes().payloadType:
                raise AuthError(
                    "token refresh",
                    str(getattr(res, "errorCode", "?")),
                    str(getattr(res, "description", "")),
                )
            self._access_token = res.accessToken
            if res.refreshToken:
                self._refresh_token = res.refreshToken
            expires_in = float(getattr(res, "expiresIn", 0) or 0)
            self._token_expires_at = time.time() + expires_in if expires_in > 0 else None
            self._persist_tokens()
            days = f"{expires_in / 86400:.1f} days" if expires_in > 0 else "unknown"
            self._status(f"🔑 Access token refreshed (expires in {days})")
            return self._access_token

        def _clear(result):
            self._refreshing = None
            waiters, self._refresh_waiters = self._refresh_waiters, []
            for w in waiters:
                if isinstance(result, Failure):
                    w.errback(result)
                else:
                    w.callback(result)
            return result

        d.addCallback(_done)
        d.addBoth(_clear)
        self._refreshing = d
        return d

    def _persist_tokens(self) -> None:
        if not self.config.token_store:
            return
        try:
            save_token_store(
                self.config.token_store,
                access_token=self._access_token,
                refresh_token=self._refresh_token or "",
                expires_at=self._token_expires_at,
                source_access_token=self.config.env_access_token or self.config.access_token,
            )
        except OSError as e:
            self._status(f"⚠️  Could not persist refreshed tokens: {e}")

    def set_account(self, account_id: int) -> defer.Deferred:
        """Switch the active trading account."""
        if self._current_account_id is not None:
            logout = ProtoOAAccountLogoutReq()
            logout.ctidTraderAccountId = self._current_account_id
            self._client.send(logout)

        self._current_account_id = int(account_id)
        self._ready = False
        return self._authenticate_account().addCallbacks(
            self._on_session_ready, self._on_session_failed
        )

    def send(self, request: Any, client_msg_id: str | None = None, timeout: int = 10) -> defer.Deferred:
        """Send a raw protobuf request and return an extracted response deferred.

        Fails fast with NotReadyError while the session is not authenticated
        (V1-T42): requests must never reach a blank / half-built session.
        """
        if not self._ready:
            return defer.fail(NotReadyError(f"{type(request).__name__}: session not ready"))
        if hasattr(request, "ctidTraderAccountId") and not getattr(request, "ctidTraderAccountId", None):
            request.ctidTraderAccountId = self.account_id
        return self._client.send(
            request, clientMsgId=client_msg_id, responseTimeoutInSeconds=timeout
        ).addCallback(_extract_or_raise)

    def get_version(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOAVersionReq(), client_msg_id)

    def get_accounts(self, client_msg_id: str | None = None) -> defer.Deferred:
        request = ProtoOAGetAccountListByAccessTokenReq()
        request.accessToken = self._access_token
        return self.send(request, client_msg_id)

    def get_assets(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOAAssetListReq(), client_msg_id)

    def get_asset_classes(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOAAssetClassListReq(), client_msg_id)

    def get_symbol_categories(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOASymbolCategoryListReq(), client_msg_id)

    def get_symbols(self, include_archived: bool = False, client_msg_id: str | None = None) -> defer.Deferred:
        request = ProtoOASymbolsListReq()
        request.includeArchivedSymbols = include_archived
        return self.send(request, client_msg_id)

    def get_symbols_by_id(
        self,
        symbol_ids: list[int] | int,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        """Fetch full ProtoOASymbol details (digits, pipPosition, lotSize, …).

        Light list from get_symbols() has names only; use this for pip/point meta.
        """
        request = ProtoOASymbolByIdReq()
        ids = [symbol_ids] if isinstance(symbol_ids, int) else list(symbol_ids)
        request.symbolId.extend(int(sid) for sid in ids)
        return self.send(request, client_msg_id)

    def get_trader(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOATraderReq(), client_msg_id)

    def reconcile(
        self,
        client_msg_id: str | None = None,
        timeout: int = 30,
    ) -> defer.Deferred:
        """ProtoOAReconcileReq — open positions/orders. Default timeout 30s."""
        return self.send(ProtoOAReconcileReq(), client_msg_id, timeout=timeout)

    def subscribe_spots(
        self,
        symbol_ids: list[int] | int,
        subscribe_to_timestamp: bool = False,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        request = ProtoOASubscribeSpotsReq()
        ids = [symbol_ids] if isinstance(symbol_ids, int) else symbol_ids
        request.symbolId.extend(int(symbol_id) for symbol_id in ids)
        request.subscribeToSpotTimestamp = subscribe_to_timestamp
        return self.send(request, client_msg_id)

    def unsubscribe_spots(self, symbol_ids: list[int] | int, client_msg_id: str | None = None) -> defer.Deferred:
        request = ProtoOAUnsubscribeSpotsReq()
        ids = [symbol_ids] if isinstance(symbol_ids, int) else symbol_ids
        request.symbolId.extend(int(symbol_id) for symbol_id in ids)
        return self.send(request, client_msg_id)

    # Minutes per ProtoOATrendbarPeriod name (used to size fromTimestamp windows)
    _PERIOD_MINUTES = {
        "M1": 1,
        "M2": 2,
        "M3": 3,
        "M4": 4,
        "M5": 5,
        "M10": 10,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "H12": 720,
        "D1": 1440,
        "W1": 10080,
        "MN1": 43200,
    }

    def get_trendbars(
        self,
        symbol_id: int,
        period: str,
        weeks: int | None = None,
        count: int | None = 500,
        from_timestamp: int | None = None,
        to_timestamp: int | None = None,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        """Request historical trend bars (ProtoOAGetTrendbarsReq).

        Preferred robust pattern for a fixed-size snapshot matching IndData:
          get_trendbars(symbol_id, "M1", count=500)

        Notes:
          - fromTimestamp / toTimestamp are required by the protobuf schema.
          - `count` limits bars returned *backwards from* toTimestamp.
          - Oversized ranges without count may yield INCORRECT_BOUNDARIES.
        """
        period_key = period.upper()
        request = ProtoOAGetTrendbarsReq()
        request.period = ProtoOATrendbarPeriod.Value(period_key)
        request.symbolId = int(symbol_id)

        to_ts = int(to_timestamp) if to_timestamp is not None else self._timestamp_now()
        request.toTimestamp = to_ts

        if from_timestamp is not None:
            request.fromTimestamp = int(from_timestamp)
        elif weeks is not None:
            request.fromTimestamp = self._timestamp_weeks_ago(weeks)
        else:
            # Size the window from bar count + 50% slack for weekends/gaps
            bar_count = int(count) if count is not None else 500
            minutes = self._PERIOD_MINUTES.get(period_key, 1) * bar_count
            minutes = int(minutes * 1.5) + self._PERIOD_MINUTES.get(period_key, 1)
            request.fromTimestamp = to_ts - minutes * 60 * 1000

        if count is not None:
            request.count = int(count)

        return self.send(request, client_msg_id)

    def subscribe_live_trendbars(
        self,
        symbol_id: int,
        period: str = "M1",
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        """Subscribe to live trend bars for a symbol (requires spot subscription).

        Live bars arrive inside ProtoOASpotEvent.trendbar once subscribed.
        """
        request = ProtoOASubscribeLiveTrendbarReq()
        request.period = ProtoOATrendbarPeriod.Value(period.upper())
        request.symbolId = int(symbol_id)
        return self.send(request, client_msg_id)

    def get_tick_data(
        self,
        symbol_id: int,
        quote_type: str = "BID",
        days: int = 1,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        request = ProtoOAGetTickDataReq()
        request.type = ProtoOAQuoteType.Value(quote_type.upper())
        request.fromTimestamp = self._timestamp_days_ago(days)
        request.toTimestamp = self._timestamp_now()
        request.symbolId = int(symbol_id)
        return self.send(request, client_msg_id)

    def new_order(
        self,
        symbol_id: int,
        trade_side: str,
        volume: float,
        order_type: str = "MARKET",
        price: float | None = None,
        client_msg_id: str | None = None,
        *,
        label: str | None = None,
        comment: str | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> defer.Deferred:
        """Place an order via ProtoOANewOrderReq.

        ``volume`` is in symbol units (not lots); protocol encodes as units*100.
        By default no stop-loss / take-profit is attached (TASK 2 policy).
        Pass stop_loss / take_profit only when explicitly required.
        """
        request = ProtoOANewOrderReq()
        request.symbolId = int(symbol_id)
        request.orderType = ProtoOAOrderType.Value(order_type.upper())
        request.tradeSide = ProtoOATradeSide.Value(trade_side.upper())
        request.volume = int(volume * 100)

        if request.orderType == ProtoOAOrderType.LIMIT:
            if price is None:
                raise ValueError("price is required for LIMIT orders")
            request.limitPrice = float(price)
        elif request.orderType == ProtoOAOrderType.STOP:
            if price is None:
                raise ValueError("price is required for STOP orders")
            request.stopPrice = float(price)

        if label:
            request.label = str(label)
        if comment:
            request.comment = str(comment)
        # Optional protection — omitted by default for TASK 2
        if stop_loss is not None:
            request.stopLoss = float(stop_loss)
        if take_profit is not None:
            request.takeProfit = float(take_profit)

        return self.send(request, client_msg_id)

    def new_market_order(
        self,
        symbol_id: int,
        trade_side: str,
        volume: float,
        client_msg_id: str | None = None,
        *,
        label: str | None = None,
        comment: str | None = None,
    ) -> defer.Deferred:
        """Market order with no SL/TP (TASK 2)."""
        return self.new_order(
            symbol_id,
            trade_side,
            volume,
            "MARKET",
            client_msg_id=client_msg_id,
            label=label,
            comment=comment,
        )

    def new_limit_order(
        self,
        symbol_id: int,
        trade_side: str,
        volume: float,
        price: float,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        return self.new_order(symbol_id, trade_side, volume, "LIMIT", price, client_msg_id)

    def new_stop_order(
        self,
        symbol_id: int,
        trade_side: str,
        volume: float,
        price: float,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        return self.new_order(symbol_id, trade_side, volume, "STOP", price, client_msg_id)

    def close_position(
        self,
        position_id: int,
        volume: float,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        request = ProtoOAClosePositionReq()
        request.positionId = int(position_id)
        request.volume = int(volume * 100)
        return self.send(request, client_msg_id)
    
    def close_all_positions(self, client_msg_id: str | None = None) -> defer.Deferred:
        """Reconcile open positions and close each one."""

        def close_positions(reconcile_res) -> defer.Deferred:
            positions = reconcile_res.position
            if not positions:
                return defer.succeed([])

            close_deferreds = []
            for pos in positions:
                volume = pos.tradeData.volume / 100.0
                close_deferreds.append(
                    self.close_position(pos.positionId, volume, client_msg_id)
                )
            return defer.DeferredList(close_deferreds, consumeErrors=True)

        return self.reconcile(client_msg_id).addCallback(close_positions)

    def cancel_order(self, order_id: int, client_msg_id: str | None = None) -> defer.Deferred:
        request = ProtoOACancelOrderReq()
        request.orderId = int(order_id)
        return self.send(request, client_msg_id)

    def get_deal_offset_list(self, deal_id: int, client_msg_id: str | None = None) -> defer.Deferred:
        request = ProtoOADealOffsetListReq()
        request.dealId = int(deal_id)
        return self.send(request, client_msg_id)

    def get_unrealized_pnl(
        self,
        client_msg_id: str | None = None,
        timeout: int = 30,
    ) -> defer.Deferred:
        """Fetch floating PnL for all open positions (ProtoOAGetPositionUnrealizedPnLReq).

        Response: positionUnrealizedPnL[] with gross/net + moneyDigits on the res.
        Scale with :func:`scale_money`. Default timeout 30s.
        """
        return self.send(
            ProtoOAGetPositionUnrealizedPnLReq(),
            client_msg_id,
            timeout=timeout,
        )

    def get_order_details(self, order_id: int, client_msg_id: str | None = None) -> defer.Deferred:
        request = ProtoOAOrderDetailsReq()
        request.orderId = int(order_id)
        return self.send(request, client_msg_id)

    def get_orders_by_position(
        self,
        position_id: int,
        from_timestamp: int | None = None,
        to_timestamp: int | None = None,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        request = ProtoOAOrderListByPositionIdReq()
        request.positionId = int(position_id)
        return self.send(request, client_msg_id)

    # ------------------------------------------------------- session build
    def _authenticate_application(self) -> defer.Deferred:
        request = ProtoOAApplicationAuthReq()
        request.clientId = self.config.client_id
        request.clientSecret = self.config.client_secret
        return self._client.send(request, responseTimeoutInSeconds=AUTH_TIMEOUT).addCallback(
            self._check_auth_response, "application", ProtoOAApplicationAuthRes
        )

    def _authenticate_account(self) -> defer.Deferred:
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self._access_token
        return self._client.send(request, responseTimeoutInSeconds=AUTH_TIMEOUT).addCallback(
            self._check_auth_response, "account", ProtoOAAccountAuthRes
        )

    @staticmethod
    def _check_auth_response(message: Any, stage: str, expected: Any) -> Any:
        res = Protobuf.extract(message)
        if message.payloadType != expected().payloadType:
            raise AuthError(
                stage,
                str(getattr(res, "errorCode", "?")),
                str(getattr(res, "description", "")),
            )
        return res

    def _build_session(self) -> defer.Deferred:
        """application auth → (refresh if due) → account auth (refresh + retry
        once on a token error) → ready."""

        def _account(_):
            d = self._authenticate_account()

            def _token_retry(failure):
                failure.trap(AuthError)
                if failure.value.code not in TOKEN_ERRORS or not self._refresh_token:
                    return failure
                self._status(f"⚠️  Account auth: {failure.value.code} — refreshing token")
                return self.refresh_access_token().addCallback(
                    lambda _: self._authenticate_account()
                )

            return d.addErrback(_token_retry)

        def _maybe_refresh(_):
            exp = self._token_expires_at
            if exp is not None and exp - time.time() < TOKEN_REFRESH_MARGIN and self._refresh_token:
                self._status("🔑 Access token near expiry — refreshing before account auth")
                return self.refresh_access_token().addErrback(
                    lambda f: self._status(f"⚠️  Token refresh failed: {f.getErrorMessage()}")
                )
            return None

        gen = self._connects
        d = self._authenticate_application()
        d.addCallback(lambda _: self._status("✅ Application authenticated — authenticating account..."))
        d.addCallback(_maybe_refresh)
        d.addCallback(_account)
        d.addCallbacks(
            self._on_session_ready, self._on_session_failed,
            callbackArgs=(gen,), errbackArgs=(gen,),
        )
        return d

    def _on_session_ready(self, response: Any, gen: int | None = None) -> None:
        if gen is not None and gen != self._connects:
            return  # result of a connection that is already gone
        self._current_account_id = int(response.ctidTraderAccountId)
        self._ready = True
        self._auth_failures = 0
        again = self._ever_ready
        self._ever_ready = True
        self._status(
            f"✅ Account {self._current_account_id} authenticated — session ready"
            + (f" (reconnect #{self._connects - 1})" if again else "")
        )
        if self.on_ready:
            self.on_ready()

    def _on_session_failed(self, failure: Any, gen: int | None = None) -> None:
        if self._shutting_down or (gen is not None and gen != self._connects):
            return  # e.g. auth timeout of a connection that already dropped
        self._auth_failures += 1
        why = failure.getErrorMessage()
        self._status(f"❌ Session build failed ({self._auth_failures}/{MAX_AUTH_FAILURES}): {why}")
        if failure.check(AuthError) and not self._ever_ready:
            # Never had a session: wrong credentials, not a network blip.
            self._fail_fatal(why)
        elif self._auth_failures >= MAX_AUTH_FAILURES:
            self._fail_fatal(f"{self._auth_failures} consecutive session build failures")
        else:
            self._drop_connection("session build failed")

    # ---------------------------------------------------- connection events
    def _on_connected(self, _client: Client) -> None:
        self._connects += 1
        self._connected_at = self._last_rx = time.time()
        self._probe_inflight = False
        self._status(
            "✅ TCP/TLS connected — authenticating application..."
            if self._connects == 1
            else f"🔌 Reconnected (TCP/TLS #{self._connects}) — rebuilding session..."
        )
        self._down_since = None
        self._build_session()

    def _on_disconnected(self, _client: Client, reason: Any) -> None:
        self._ready = False
        self._protocol = None
        self._down_since = time.time()
        if self._shutting_down:
            return
        msg = reason.getErrorMessage() if hasattr(reason, "getErrorMessage") else str(reason)
        up = f" after {time.time() - self._connected_at:.0f}s" if self._connected_at else ""
        self._status(f"⚠️  Disconnected{up}: {msg} — reconnecting with backoff")
        if self.on_disconnected:
            self.on_disconnected(reason)

    def _drop_connection(self, why: str) -> None:
        """Abort the socket; ClientService reconnects and the session rebuilds."""
        self._ready = False
        transport = getattr(self._protocol, "transport", None)
        if transport is None:
            return
        self._status(f"⚠️  Dropping connection: {why}")
        try:
            transport.abortConnection()
        except Exception:  # noqa: BLE001 — fall back to a normal close
            transport.loseConnection()

    # ------------------------------------------------- heartbeat / watchdog
    def _send_heartbeat(self) -> None:
        if self._protocol is not None and self._client.isConnected:
            try:
                self._protocol.heartbeat()
            except Exception as e:  # noqa: BLE001 — watchdog handles a dead socket
                self._status(f"⚠️  Heartbeat send failed: {e}")

    def _watchdog_tick(self) -> None:
        now = time.time()
        jump = now - self._last_tick - WATCHDOG_INTERVAL
        self._last_tick = now
        if not self._client.isConnected:
            down = now - self._down_since if self._down_since else 0.0
            if down > 60.0 and now - self._down_logged > 60.0 and not self._shutting_down:
                self._down_logged = now
                self._status(f"⏳ Still reconnecting ({down:.0f}s without a connection)")
            return
        if jump > SLEEP_JUMP:
            self._drop_connection(f"host sleep / stall detected (clock jumped {jump:.0f}s)")
            return
        idle = now - self._last_rx
        if idle > DEAD_AFTER:
            self._drop_connection(f"no inbound data for {idle:.0f}s")
        elif idle > PROBE_AFTER and self._ready and not self._probe_inflight:
            self._probe_inflight = True
            d = self._client.send(ProtoOAVersionReq(), responseTimeoutInSeconds=int(DEAD_AFTER - PROBE_AFTER))

            def _probe_done(_):
                self._probe_inflight = False

            d.addBoth(_probe_done)

    def _token_tick(self) -> None:
        exp = self._token_expires_at
        if self._ready and exp is not None and exp - time.time() < TOKEN_REFRESH_MARGIN:
            # The live session stays valid; the new token serves the next auth.
            self.refresh_access_token().addErrback(
                lambda f: self._status(f"⚠️  Proactive token refresh failed: {f.getErrorMessage()}")
            )

    # --------------------------------------------------------------- inbound
    def _on_message(self, _client: Client, message: Any) -> None:
        self._last_rx = time.time()
        payload_type = message.payloadType

        if payload_type == ProtoHeartbeatEvent().payloadType:
            return
        # Auth / refresh responses are handled by their request Deferreds
        if payload_type in _SESSION_PAYLOADS:
            return

        if payload_type == ProtoOAErrorRes().payloadType:
            self._emit_error(Protobuf.extract(message))
            return

        if self._shutting_down and payload_type in _SESSION_EVENTS:
            return  # our own logout triggers these

        if payload_type == ProtoOAAccountsTokenInvalidatedEvent().payloadType:
            res = Protobuf.extract(message)
            self._status(f"⚠️  Access token invalidated by server: {res.reason or '?'} — refreshing")
            self._ready = False
            self.refresh_access_token().addBoth(
                lambda _r: self._drop_connection("token invalidated")
            )
            return

        if payload_type == ProtoOAClientDisconnectEvent().payloadType:
            res = Protobuf.extract(message)
            self._status(f"⚠️  Server is disconnecting this client: {res.reason or '?'}")
            return

        if payload_type == ProtoOAAccountDisconnectEvent().payloadType:
            self._status("⚠️  Server logged the account out — rebuilding session")
            self._drop_connection("account disconnected by server")
            return

        if self.on_message:
            self.on_message(Protobuf.extract(message))

    def _emit_error(self, failure: Any) -> None:
        """Log a ProtoOAErrorRes. Auth errors are handled by _build_session;
        nothing here stops the reactor any more (V1-T42)."""
        code = getattr(failure, "errorCode", "") or ""
        desc = getattr(failure, "description", "") or ""
        print(f"❌ Open API error: {code} {desc}".rstrip())
        if self.on_error:
            self.on_error(failure)

    @staticmethod
    def _timestamp_now() -> int:
        return int(calendar.timegm(datetime.datetime.utcnow().utctimetuple())) * 1000

    @staticmethod
    def _timestamp_days_ago(days: int) -> int:
        start = datetime.datetime.utcnow() - datetime.timedelta(days=int(days))
        return int(calendar.timegm(start.utctimetuple())) * 1000

    @staticmethod
    def _timestamp_weeks_ago(weeks: int) -> int:
        start = datetime.datetime.utcnow() - datetime.timedelta(weeks=int(weeks))
        return int(calendar.timegm(start.utctimetuple())) * 1000

_SESSION_EVENTS = frozenset(
    {
        ProtoOAAccountsTokenInvalidatedEvent().payloadType,
        ProtoOAClientDisconnectEvent().payloadType,
        ProtoOAAccountDisconnectEvent().payloadType,
    }
)


def _extract_or_raise(message: Any) -> Any:
    res = Protobuf.extract(message)
    if message.payloadType == ProtoOAErrorRes().payloadType:
        raise ApiError(str(getattr(res, "errorCode", "?")), str(getattr(res, "description", "")))
    return res


_SESSION_PAYLOADS = frozenset(
    {
        ProtoOAApplicationAuthRes().payloadType,
        ProtoOAAccountAuthRes().payloadType,
        ProtoOARefreshTokenRes().payloadType,
    }
)


def _enable_keepalive(protocol: Any) -> None:
    """TCP keepalive on the socket under TLS (second layer under heartbeats)."""
    transport = getattr(protocol, "transport", None)
    tcp = getattr(transport, "transport", transport)  # TLS wraps the TCP transport
    try:
        tcp.setTcpKeepAlive(1)
        sock = tcp.getHandle()
        for opt, val in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
            if hasattr(socket, opt):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
    except Exception:  # noqa: BLE001 — keepalive is best effort
        pass
