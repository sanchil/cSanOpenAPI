"""High-level Python interface to the cTrader Open API."""

from __future__ import annotations

import calendar
import datetime
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
    ProtoOAReconcileReq,
    ProtoOASubscribeLiveTrendbarReq,
    ProtoOASubscribeSpotsReq,
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
from twisted.internet import defer, reactor

from .config import CTraderConfig, load_config

MessageCallback = Callable[[Any], None]
ErrorCallback = Callable[[Any], None]


class CTraderOpenAPI:
    """Wrapper around the official ctrader-open-api SDK."""

    def __init__(self, config: CTraderConfig | None = None) -> None:
        self.config = config or load_config()
        self._client = Client(
            self.config.protobuf_host,
            self.config.protobuf_port,
            TcpProtocol,
        )
        self._ready = False
        self._shutting_down = False
        self._current_account_id: int | None = None

        self.on_ready: Callable[[], None] | None = None
        self.on_message: MessageCallback | None = None
        self.on_error: ErrorCallback | None = None
        self.on_disconnected: Callable[[Any], None] | None = None

        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def account_id(self) -> int:
        return self._current_account_id or self.config.account_id

    def connect(self) -> None:
        """Start the TCP connection to cTrader."""
        self._client.startService()

    def run(self) -> None:
        """Connect and block on the Twisted reactor."""
        self.connect()
        reactor.run()

    def disconnect(self) -> defer.Deferred:
        """Log out and close the connection cleanly."""
        self._shutting_down = True
        self._ready = False

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

    def refresh_access_token(self) -> dict:
        """Refresh the OAuth access token using the stored refresh token."""
        if not self.config.refresh_token:
            raise ValueError("CTRADER_REFRESH_TOKEN is not set in .env")
        if not self.config.redirect_uri:
            raise ValueError("CTRADER_REDIRECT_URI is required for token refresh")

        auth = Auth(
            self.config.client_id,
            self.config.client_secret,
            self.config.redirect_uri,
        )
        return auth.refreshToken(self.config.refresh_token)

    def set_account(self, account_id: int) -> defer.Deferred:
        """Switch the active trading account."""
        if self._current_account_id is not None:
            logout = ProtoOAAccountLogoutReq()
            logout.ctidTraderAccountId = self._current_account_id
            self._client.send(logout)

        self._current_account_id = int(account_id)
        self._ready = False
        return self._authenticate_account()

    def send(self, request: Any, client_msg_id: str | None = None, timeout: int = 10) -> defer.Deferred:
        """Send a raw protobuf request and return an extracted response deferred."""
        if hasattr(request, "ctidTraderAccountId") and not getattr(request, "ctidTraderAccountId", None):
            request.ctidTraderAccountId = self.account_id
        return (
            self._client.send(request, clientMsgId=client_msg_id, responseTimeoutInSeconds=timeout)
            .addCallback(lambda message: Protobuf.extract(message))
        )

    def get_version(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOAVersionReq(), client_msg_id)

    def get_accounts(self, client_msg_id: str | None = None) -> defer.Deferred:
        request = ProtoOAGetAccountListByAccessTokenReq()
        request.accessToken = self.config.access_token
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

    def get_trader(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOATraderReq(), client_msg_id)

    def reconcile(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOAReconcileReq(), client_msg_id)

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
    ) -> defer.Deferred:
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

        return self.send(request, client_msg_id)

    def new_market_order(
        self,
        symbol_id: int,
        trade_side: str,
        volume: float,
        client_msg_id: str | None = None,
    ) -> defer.Deferred:
        return self.new_order(symbol_id, trade_side, volume, "MARKET", client_msg_id=client_msg_id)

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

    def get_unrealized_pnl(self, client_msg_id: str | None = None) -> defer.Deferred:
        return self.send(ProtoOAGetPositionUnrealizedPnLReq(), client_msg_id)

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

    def _authenticate_application(self) -> defer.Deferred:
        request = ProtoOAApplicationAuthReq()
        request.clientId = self.config.client_id
        request.clientSecret = self.config.client_secret
        return self._client.send(request)

    def _authenticate_account(self) -> defer.Deferred:
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.config.access_token
        return self._client.send(request)

    def _on_connected(self, _client: Client) -> None:
        # Auth responses are handled in _on_message, not via the send() deferred.
        self._authenticate_application()

    def _on_disconnected(self, _client: Client, reason: Any) -> None:
        self._ready = False
        if self._shutting_down or not self.on_disconnected:
            return
        self.on_disconnected(reason)

    def _on_message(self, _client: Client, message: Any) -> None:
        payload_type = message.payloadType

        if payload_type == ProtoHeartbeatEvent().payloadType:
            return

        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            self._authenticate_account()
            return

        if payload_type == ProtoOAAccountAuthRes().payloadType:
            response = Protobuf.extract(message)
            self._current_account_id = response.ctidTraderAccountId
            self._ready = True
            if self.on_ready:
                self.on_ready()
            return

        if payload_type == ProtoOAErrorRes().payloadType:
            self._emit_error(Protobuf.extract(message))
            return

        if self.on_message:
            self.on_message(Protobuf.extract(message))

    def _emit_error(self, failure: Any) -> None:
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