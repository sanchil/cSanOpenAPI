#!/usr/bin/env python3
"""cTrader app: connect, inspect account, optionally close all positions, then shutdown."""

import sys

from twisted.internet import defer

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATradeSide

from ctrader_api import CTraderApp, CTraderOpenAPI, load_config

if __name__ == "__main__":
    # close_on_start = "--close-all" in sys.argv
    config = load_config()
    app = CTraderApp(config)
    app.run()