#!/usr/bin/env python3
"""Trading app entry: csys connectivity + capp business loop.

Usage:
    python main.py              # live: sends market orders
    python main.py --dry-run    # signals + simulated orders, nothing sent
    CTRADER_DRY_RUN=1 python main.py   # same, via env / .env
    python main.py --log-level debug --ml-capture
                                # trade log level (off|bar|signals|debug, default
                                # signals; env CTRADER_LOG_LEVEL) → logs/trade_<UTC day>.log;
                                # --ml-capture (env CTRADER_ML_CAPTURE=1) → logs/ml/*.csv
    python main.py --pt-mode structure --profit-trail
                                # Strategy_8 variant: default swing, no trail
                                # (V1-T44); env CTRADER_PT_MODE / CTRADER_PROFIT_TRAIL=1
"""

import argparse
import os
import sys

from csys import load_config
from capp import CTraderApp
from capp.tradelog import LEVELS, TradeLogConfig


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="cTrader Open API trading app")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log signals and simulated orders; send no orders (also: CTRADER_DRY_RUN=1)",
    )
    parser.add_argument(
        "--log-level",
        choices=tuple(LEVELS),
        default=os.getenv("CTRADER_LOG_LEVEL", "signals"),
        help="trade log level (off | bar | signals | debug); files in logs/, one per UTC day",
    )
    parser.add_argument(
        "--ml-capture",
        action="store_true",
        help="also write per-bar feature rows to logs/ml/features_<day>.csv",
    )
    parser.add_argument(
        "--pt-mode",
        choices=("swing", "structure", "peaks"),
        default=None,
        help="peak_trough_sig mode for Strategy_8 (default swing; env CTRADER_PT_MODE)",
    )
    parser.add_argument(
        "--profit-trail",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Strategy_8 ATR trailing profit lock (default off; env CTRADER_PROFIT_TRAIL=1 enables)",
    )
    args = parser.parse_args()

    config = load_config()  # loads .env, so CTRADER_DRY_RUN may come from there
    dry_run = args.dry_run or _env_flag("CTRADER_DRY_RUN")
    print("⚠️  DRY RUN — no orders will be sent" if dry_run else "✅ LIVE — orders will be sent")
    # Resolved after load_config() so .env values count (flag > env > default)
    pt_mode = args.pt_mode or os.getenv("CTRADER_PT_MODE", "swing").strip().lower()
    if pt_mode not in ("swing", "structure", "peaks"):
        parser.error(f"CTRADER_PT_MODE must be swing, structure or peaks, got {pt_mode!r}")
    profit_trail = args.profit_trail
    if profit_trail is None:
        profit_trail = os.getenv("CTRADER_PROFIT_TRAIL", "0").strip().lower() in ("1", "true", "yes", "on")
    print(f"   Strategy_8: pt_mode={pt_mode} profit_trail={'on' if profit_trail else 'off'}")

    trade_log = TradeLogConfig(
        level=args.log_level,
        ml_capture=args.ml_capture or _env_flag("CTRADER_ML_CAPTURE"),
    )
    app = CTraderApp(
        config, active_strategy=8, dry_run=dry_run, trade_log=trade_log,
        pt_mode=pt_mode, profit_trail=profit_trail,
    )  # peak/trough (7 = baseSlope v1, 6 = MQ4 parity)
    app.run()
    # Non-zero on an unrecoverable session error so a supervisor
    # (deploy/ctrader-bot.service, Restart=always) restarts us (V1-T42)
    sys.exit(1 if app.api.fatal else 0)
