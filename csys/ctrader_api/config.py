"""Configuration loader for cTrader Open API credentials.

Token store (V1-T42): after an in-session token refresh the new access /
refresh pair is written to ``.ctrader_tokens.json`` next to ``.env`` (mode
0600) and overlays the .env values on the next load. The store remembers
which .env access token it was derived from; if .env holds a different
token (the user pasted a new one), the store is ignored.
"""

from __future__ import annotations

import json
import os
import time
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
    token_expires_at: float | None = None   # unix seconds, known after a refresh
    token_store: str | None = None          # path of the refreshed-token store
    env_access_token: str | None = None     # .env token the store derives from

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


TOKEN_STORE_NAME = ".ctrader_tokens.json"


def load_token_store(path: str | Path, env_access_token: str) -> dict | None:
    """Refreshed tokens for this .env token lineage, or None."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if data.get("source_access_token") != env_access_token:
        return None  # .env was edited by hand since the refresh
    if not data.get("access_token") or not data.get("refresh_token"):
        return None
    return data


def save_token_store(
    path: str | Path,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: float | None,
    source_access_token: str,
) -> None:
    """Atomically persist a refreshed token pair (0600)."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "refreshed_at": time.time(),
        "source_access_token": source_access_token,
    }
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


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

    access_token = env_access_token = _require("CTRADER_ACCESS_TOKEN")
    refresh_token = os.getenv("CTRADER_REFRESH_TOKEN")
    expires_at = None
    store_path = env_path.parent / TOKEN_STORE_NAME
    stored = load_token_store(store_path, access_token)
    if stored is not None:
        access_token = stored["access_token"]
        refresh_token = stored["refresh_token"]
        expires_at = stored.get("expires_at")

    return CTraderConfig(
        client_id=_require("CTRADER_CLIENT_ID"),
        client_secret=_require("CTRADER_CLIENT_SECRET"),
        access_token=access_token,
        account_id=int(_require("CTRADER_ACCOUNT_ID")),
        refresh_token=refresh_token,
        redirect_uri=os.getenv("CTRADER_REDIRECT_URI"),
        host=os.getenv("CTRADER_HOST", "demo"),
        token_expires_at=expires_at,
        token_store=str(store_path),
        env_access_token=env_access_token,
    )