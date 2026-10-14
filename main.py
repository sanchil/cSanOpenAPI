#!/usr/bin/env python3
"""Trading app entry: csys connectivity + capp business loop.

Usage:
    python main.py              # live: sends market orders
    python main.py --dry-run    # signals + simulated orders, nothing sent
    CTRADER_DRY_RUN=1 python main.py   # same, via env / .env
"""

import argparse
import os

from csys import load_config
from capp import CTraderApp


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="cTrader Open API trading app")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log signals and simulated orders; send no orders (also: CTRADER_DRY_RUN=1)",
    )
    args = parser.parse_args()

    config = load_config()  # loads .env, so CTRADER_DRY_RUN may come from there
    dry_run = args.dry_run or _env_flag("CTRADER_DRY_RUN")
    print("⚠️  DRY RUN — no orders will be sent" if dry_run else "✅ LIVE — orders will be sent")

    app = CTraderApp(config, active_strategy=8, dry_run=dry_run)  # peak/trough structure (7 = baseSlope v1, 6 = MQ4 parity)
    app.run()
