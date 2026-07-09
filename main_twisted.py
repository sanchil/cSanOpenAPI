#!/usr/bin/env python3
from twisted.web.server import Site
from twisted.web.resource import Resource
from twisted.internet import reactor, defer
from twisted.web import server
import json

from ctrader_api import CTraderOpenAPI, load_config

class CTraderApp:
    def __init__(self, config):
        self.api = CTraderOpenAPI(config)
        self.api.on_ready = self.on_ready

    def on_ready(self):
        print(f"✅ cTrader connected! Account: {self.api.account_id}")

    def run(self):
        self.api.run()

class OHLCVResource(Resource):
    def __init__(self, ctrader_app):
        super().__init__()
        self.ctrader_app = ctrader_app

    def render_GET(self, request):
        try:
            symbol_id = int(request.args.get(b'symbol_id', [b'1'])[0])
            period = request.args.get(b'period', [b'M1'])[0].decode()
            weeks = int(request.args.get(b'weeks', [b'4'])[0])

            print(f"→ Fetching {period} bars for symbol {symbol_id} ({weeks} weeks)")

            d = self.ctrader_app.api.get_trendbars(
                symbol_id=symbol_id, 
                period=period, 
                weeks=weeks
            )

            # Increase timeout to 60 seconds
            timeout = 60
            timeout_d = defer.Deferred()
            reactor.callLater(timeout, timeout_d.errback, Exception(f"Request timeout after {timeout}s"))

            def on_success(response):
                bars = getattr(response, 'trendbar', [])
                print(f"✅ Received {len(bars)} bars")

                ohlcv = []
                for bar in bars:
                    ohlcv.append({
                        "timestamp": getattr(bar, 'timestamp', None),
                        "open": getattr(bar, 'open', 0) / 100000.0,
                        "high": getattr(bar, 'high', 0) / 100000.0,
                        "low": getattr(bar, 'low', 0) / 100000.0,
                        "close": getattr(bar, 'close', 0) / 100000.0,
                        "volume": getattr(bar, 'volume', 0) / 100.0,
                    })
                
                response_data = {
                    "symbol_id": symbol_id,
                    "period": period,
                    "bars_count": len(ohlcv),
                    "bars": ohlcv[-200:]   # Last 200 bars to keep response reasonable
                }
                request.write(json.dumps(response_data, indent=2).encode())
                request.finish()

            
            def on_error(failure):
                print("❌ Error:", failure)
                if not request.finished:
                    request.setResponseCode(500)
                    request.write(json.dumps({"error": str(failure)}).encode())
                    request.finish()

            d.addCallback(on_success)
            d.addErrback(on_error)

            return server.NOT_DONE_YET

        except Exception as e:
            print("Exception:", e)
            if not request.finished:
                request.setResponseCode(500)
                return json.dumps({"error": str(e)}).encode()
            return b''       

    

if __name__ == "__main__":
    config = load_config()
    app = CTraderApp(config)
    
    root = Resource()
    root.putChild(b"ohlcv", OHLCVResource(app))
    
    site = Site(root)
    
    reactor.listenTCP(8080, site)
    print("Twisted Web Server running on http://0.0.0.0:8080")
    print("Test: http://localhost:8080/ohlcv?symbol_id=1&period=M1&weeks=2")
    
    app.run()
    reactor.run()