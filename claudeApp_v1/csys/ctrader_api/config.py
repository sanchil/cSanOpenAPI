"""Configuration loader for cTrader Open API credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ctrader_open_api import EndPoints


@dataclass(frozen=True)
class CTraderConfig:
    client_id: str
    client_secret: str
    access_token: str
    account_id: int
    refresh_token: str | None = None
    redirect_uri: str | None = None
    host: str = "demo"

    @property
    def protobuf_host(self) -> str:
        if self.host.lower() == "live":
            return EndPoints.PROTOBUF_LIVE_HOST
        return EndPoints.PROTOBUF_DEMO_HOST

    @property
    def protobuf_port(self) -> int:
        return EndPoints.PROTOBUF_PORT


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_config(env_path: str | Path | None = None) -> CTraderConfig:
    """Load cTrader credentials from a .env file.

    Default path: project root ``.env`` (csys/ctrader_api/config.py → ../../..).
    """
    if env_path is None:
        # csys/ctrader_api/config.py → project root
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    else:
        env_path = Path(env_path)

    load_dotenv(dotenv_path=env_path)

    return CTraderConfig(
        client_id=_require("CTRADER_CLIENT_ID"),
        client_secret=_require("CTRADER_CLIENT_SECRET"),
        access_token=_require("CTRADER_ACCESS_TOKEN"),
        account_id=int(_require("CTRADER_ACCOUNT_ID")),
        refresh_token=os.getenv("CTRADER_REFRESH_TOKEN"),
        redirect_uri=os.getenv("CTRADER_REDIRECT_URI"),
        host=os.getenv("CTRADER_HOST", "demo"),
    )