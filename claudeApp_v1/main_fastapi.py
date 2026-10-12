#!/usr/bin/env python3
from fastapi import FastAPI, HTTPException
import asyncio
import uvicorn
from multiprocessing import Process
import time

from csys import CTraderOpenAPI, load_config

app = FastAPI(title="cTrader Data API")

ctrader_process = None
ctrader_app = None   # We'll communicate via other means later

class CTraderProcess:
    def run(self):
        config = load_config()
        app = CTraderApp(config)
        app.run()

class CTraderApp:
    def __init__(self, config):
        self.api = CTraderOpenAPI(config)
        self.api.on_ready = self.on_ready

    def on_ready(self):
        print(f"✅ cTrader connected! Account: {self.api.account_id}")

    def run(self):
        self.api.run()

@app.on_event("startup")
async def startup():
    global ctrader_process
    ctrader_process = Process(target=CTraderProcess().run, daemon=True)
    ctrader_process.start()
    print("cTrader process started in background")

@app.on_event("shutdown")
async def shutdown():
    if ctrader_process and ctrader_process.is_alive():
        ctrader_process.terminate()

@app.get("/ohlcv/{symbol_id}")
async def get_ohlcv(symbol_id: int, period: str = "M1", weeks: int = 4):
    # For now, this is placeholder
    return {
        "symbol_id": symbol_id,
        "message": "cTrader is running in background process",
        "note": "Full integration coming next"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)