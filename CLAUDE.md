# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this workspace is

The goal is to port the **live execution path** of an MQ4 trading EA (and its partial C# cBot port) to Python on the **cTrader Open API**. Port the behavior, not every line. MQ4 has a lot of unused code, and only the path that starts at `HSIG::initSIG` matters.

- `DESIGN.txt`: the current top-level task for this workspace.
- `code_base/mq4_code_base_v98.txt` (~18k lines): the original MQ4 source and the reference for behavior. It is identical to `claudeApp_v1/code_references/mq4/code_base_v98.txt`. Useful anchors: `class SanStrategies`, `class SanSignals`, `class HSIG`, `HSIG::initSIG`, `class MarketMetrics`, `class Stats` (`slopeVal`, `computeStencilKinematics`), `class SanUtils` (`getTotalTradeProfits`), `class CircularBuffer`. Use grep to find these. The file is too large to read whole.
- `code_base/cb_v1_v2.txt`: the C# cBot port concatenated into one file. The per-file version is in `claudeApp_v1/code_references/cBot/` (`PhyBot.cs`, `CSignal.cs`, `CStrategies.cs`, `CStats.cs`, `CTypes.cs`, `PhysicsEngine.cs`).
- `claudeApp_v1/`: the Python app. Its folder name differs from its internal name, "grokApp_v6". Trade label is `cSanOpenAPI` (set by `CTraderApp`; `OrderExecutor`'s own default `GrokApp` is overridden).

The reference sources are read-only. They are not a git repo, and the app has no git remote either.

## Project docs inside claudeApp_v1 (read before changing behavior)

- `design_v1.txt`: the **authoritative blueprint**. It covers the as-built architecture, the bring-up runbook (§1.0), the gaps (G1–G22), the forward task list `V1-T01..T25` with a status board, the invariants (I1–I10), and the "definition of done". When a task lands, update its status line there.
- `design.txt`: historical task log (TASK 1–11) with the original MQ4 function signatures being ported.
- `AGENTS.md`: agent conventions, synced with code on 2026-10-12 (V1-T02). `main.py` uses `active_strategy=6` (the ported MQ4 live baseSlope composite, with sticky per-symbol state). Prefer `design_v1.txt` and the code where they disagree. `design_v1.txt` §1.0 is the bring-up runbook.
- `ctraderapp_flow.txt`: the startup and live-loop call graph of `CTraderApp` (synced 2026-10-12).

## Commands

Run everything from `claudeApp_v1/`. Imports are top-level `csys` / `capp` and there is no package install.


```bash
conda activate genai
```

```bash

cd claudeApp_v1
pip install -r _requirements.txt        # minimal deps: ctrader-open-api==0.9.2, twisted, python-dotenv, numpy (+ fastapi/uvicorn for the experiment)
python main.py                          # live trading app (connects to cTrader and places real orders)
python main.py --dry-run                # same, but orders are simulated (or set CTRADER_DRY_RUN=1 in env/.env)
python main_twisted.py                  # optional OHLCV HTTP helper: http://localhost:8080/ohlcv?symbol_id=1&period=M1&weeks=2
```


`requirements.txt` is a full `pip freeze` of a large ML environment (torch, etc.) and is not the app's minimal set. Python 3.11+. `main_fastapi.py` and `basic.py` are incomplete experiments, so don't treat them as entry points.

There is **no test suite or linter**. Check pure functions (indicators, signals, stats, utils, physics) directly with `python -c` using small series. Check order logic with `OrderExecutor(..., dry_run=True)`. Running `main.py` connects to a real account, so don't run it to verify changes unless the user asks. `.env` in `claudeApp_v1/` provides `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_ACCESS_TOKEN`, `CTRADER_ACCOUNT_ID` (optional: `CTRADER_REFRESH_TOKEN`, `CTRADER_REDIRECT_URI`, `CTRADER_HOST=demo|live`), loaded by `csys.ctrader_api.config.load_config()`. Never print its contents.

## Architecture

There are two layers with a strict dependency rule: **`capp` may import `csys`, and `csys` must never import `capp`.**

- `csys/` (system): `ctrader_api/client.py` `CTraderOpenAPI` is the protobuf client built on Twisted Deferreds. `execution.py` `OrderExecutor` holds the order policy. `config.py` loads settings. `ctypes.py` holds the **canonical** types `SIG`, `DTYPE`, `T_SIG`, `IndData`, `SymbolMeta`, `decode_trendbar`. `cindicators/` holds pure MA/ATR/ADX/StdDev functions plus `snapshot.compute_indicators`, which fills an `IndData`. `csys/ctypes.py` is only a re-export facade. Never add another types module under `capp`.
- `capp/` (business): `ctraderapp.py` `CTraderApp` is the orchestrator (~800 lines). `signals/` holds `SanSignals.init_sig`, which builds a `T_SIG`, and `stats.py` (`slope_val`, `compute_stencil_kinematics`, `mql_at`). `strategies/` holds `CStrategies.evaluate` with Strategy_1..6. `utils.py` holds `SanUtils.get_total_trade_profits`, which is pure math only. `physics/` holds NumPy/SciPy helpers such as `covariance_matrix_determinant`.

**Per-bar pipeline** (`CTraderApp.run_signal_cycle`): `compute_indicators(ind)` → (live only) `fetch_position_pnls()` → `CStrategies.evaluate(ind, total_trade_profits)` (`init_sig` → `Strategy_N` → `SIG`) → `OrderExecutor.handle_signal` → `_sync_position_state`. Indicators and signals are computed **once per bar**. The rest of the cycle only reads `IndData` / `T_SIG`. The history seed runs with `execute=False`, and orders go out only after `_hist_ready`.

**Lifecycle:** auth → `on_init` → subscribe spots → symbols → `get_symbols_by_id` (SymbolMeta) → `init_ind_data` seeds trendbars for each symbol → reconcile positions → subscribe live trendbars → `on_message` loop. A new bar is detected when the live trendbar minute rolls. Spot mid-price synthetic bars are the fallback.

## Invariants and gotchas

- **Series indexing:** `IndData` deques run oldest → newest (`[-1]` is newest). MQ4/cBot `shift` counts from the other end (0 = newest). Always convert with `mql_at` / `mql_index`. Live analysis uses shift=1, the last closed bar.
- **Execution policy:** at most one position per symbol under the trade label, with no pyramiding and **no SL/TP**. A BUY/SELL while flat opens a position. The same side is a no-op. An **opposite signal only closes**, with no reversal in the same cycle. CLOSE closes. Changing any of this requires an explicit redesign task (V1-T18/T19).
- **Async:** the trading path uses Twisted Deferreds, never asyncio. The PnL fetch is single-flight (`reconcile` then `get_unrealized_pnl` in sequence, and concurrent symbols join one in-flight Deferred). Joiner Deferreds must not chain onto the master (`_share_pnl_result`). On failure the average PnL degrades to 0. Network calls belong in `CTraderApp`, never in `utils.py`.
- Protobuf fields like `symbolId` can come back as repeated containers. Coerce them to `int` before using them as dict keys. The SDK also forwards every RPC response and event to `on_message`, so `on_tick` only handles `ProtoOASpotEvent` (`is_spot_event`); keep that gate in any new handler (V1-T03, partial).
- `IndData.period` is in **seconds** (M1 → 60), via `period_seconds()`.
- The hardcoded defaults are not yet config-driven: `fxpair_arr=[1,4]` (broker symbol IDs), M1, 2000-bar capacity/seed (`DEFAULT_HISTORY_BARS`), 10000 volume, strategy 6, `mq4_profit_close_threshold=2.0` (Strategy_6) / `profit_close_threshold=4.0` (Strategy_2..5).
- Style: `from __future__ import annotations`, dataclasses and type hints, snake_case with camelCase aliases kept for MQ4 parity (`fastSlowSIG`, `slopeVal`). Logs use ✅/❌/⚠️ prefixes. Document any intentional deviation from MQ4/cBot in the module docstring.
