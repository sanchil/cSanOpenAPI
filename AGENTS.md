# grokApp_v6 — Project Instructions

Python trading app that ports an MQ4 / cBot strategy stack onto the **cTrader Open API** (`ctrader-open-api` + Twisted). Goal: migrate only the **current execution path** from the reference codebases, not the full MQ4 bloat.

## Mission

1. **Signals (TASK 1 — done):** Port `kineticAccelerationSIG`, `fuseSIG`, `slopeVal` / `computeStencilKinematics`, `fastSlowSIG`, and `HSIG::initSIG` path into Python.
2. **Execution (TASK 2 — in progress):** Single market trade per symbol, no pyramid, no SL/TP; close on CLOSE or opposite signal.

Authoritative product notes live in `design.txt`. Reference sources:

- `code_references/mq4/code_base_v98.txt` — original MQ4 (start at `HSIG::initSIG`)
- `code_references/cBot/` — C# cBot port (`PhyBot.cs`, `CSignal.cs`, `CStrategies.cs`, `CStats.cs`, `CTypes.cs`, …)

Prefer matching **behavior** of the live path over line-by-line dumps of unused helpers.

## Layout

| Path | Role |
|------|------|
| `main.py` | Primary entry: `CTraderApp` connect + run |
| `main_twisted.py` | Twisted HTTP server exposing `/ohlcv` over the same API client |
| `main_fastapi.py` | Experimental FastAPI shell (placeholder OHLCV; incomplete) |
| `basic.py` | Minimal SDK sample (not production) |
| `ctrader_api/` | Open API client, config, types, app loop, order executor |
| `cindicators/` | Pure-Python TA (MA/ATR/ADX/StdDev) + once-per-cycle `IndData` fill |
| `csignals/` | `SanSignals` primitives + stats/kinematics |
| `cstrategies/` | Strategy_1..4 over `T_SIG` |
| `code_references/` | Read-only migration sources (do not treat as runtime deps) |
| `ctypes/` | Empty placeholder — **real types are** `ctrader_api/ctypes.py` |
| `.env` | Credentials (never commit secrets) |
| `requirements.txt` | Runtime deps |

## Runtime stack

- **Python 3.11+** (workspace also has 3.13 bytecode)
- **Twisted** reactor (blocking `reactor.run()` via `CTraderOpenAPI.run()`)
- **ctrader-open-api** protobuf TCP client
- **python-dotenv** for config

Install:

```bash
pip install -r requirements.txt
```

Run trading app:

```bash
python main.py
```

Optional OHLCV HTTP (Twisted, port 8080):

```bash
python main_twisted.py
# http://localhost:8080/ohlcv?symbol_id=1&period=M1&weeks=2
```

### Environment (`.env`)

Required:

- `CTRADER_CLIENT_ID`
- `CTRADER_CLIENT_SECRET`
- `CTRADER_ACCESS_TOKEN`
- `CTRADER_ACCOUNT_ID`

Optional:

- `CTRADER_REFRESH_TOKEN`
- `CTRADER_REDIRECT_URI`
- `CTRADER_HOST` — `demo` (default) or `live`

Loaded by `ctrader_api.config.load_config()` from project-root `.env`.

## Architecture

### Per-bar pipeline (once per cycle)

```
IndData (OHLCV deques)
  → cindicators.snapshot.compute_indicators   # MA/ATR/ADX/StdDev into IndData
  → cstrategies.CStrategies.evaluate          # SanSignals.init_sig → Strategy_N
  → OrderExecutor.handle_signal               # open / close / no-op
```

Implemented in `CTraderApp.run_signal_cycle()`. Historical seed calls it with `execute=False`; live bars use `execute=True` after `_hist_ready`.

**Rule:** compute indicators and signals **once per bar**. Downstream code reads `IndData` / `T_SIG` / `last_trade_sig` — do not re-call indicator modules repeatedly in the same cycle.

### Package responsibilities

**`ctrader_api`**

- `client.CTraderOpenAPI` — thin Twisted wrapper over Open API (auth, symbols, trendbars, orders, spots)
- `ctraderapp.CTraderApp` — app lifecycle: spots → symbols → hist seed → reconcile → live bars → signal cycle
- `execution.OrderExecutor` — single-position market execution policy
- `ctypes` — shared `SIG`, `DTYPE`, `T_SIG`, `IndData`, `decode_trendbar`, price helpers
- `config` — env-backed `CTraderConfig`
- Lazy `CTraderApp` import in `__init__` avoids cycles with indicators/signals

**`cindicators`**

- Pure functions over raw series; `snapshot.compute_indicators` fills `IndData`
- Series convention: **oldest → newest** deques

**`csignals`**

- `SanSignals`: `fast_slow_sig`, `kinetic_acceleration_sig`, `fuse_sig`, `init_sig`
- `stats`: `slope_val`, `compute_stencil_kinematics`; MQL shift bridge via `mql_at` / `mql_index` (MQL `0` = newest)

**`cstrategies`**

- `CStrategies.evaluate(ind)` → single `SIG`
- Strategy 1 (default): micWave — BUY/SELL when `fsig5 == fsig30 == microWaveSIG` directional, else CLOSE
- Strategies 2–4: baseSlope / slope30 variants with optional profit-close threshold

### Execution policy (TASK 2)

From `execution.py` / `design.txt`:

| Condition | Action |
|-----------|--------|
| BUY/SELL while flat | Open one market order |
| BUY/SELL same side | No-op (already in trade) |
| Opposite signal | Close only (no reverse open same cycle) |
| CLOSE | Close open position |
| SL / TP | Never attached |
| Pyramid / multi-trade | Forbidden (max 1 position per symbol) |

`OrderExecutor` supports `dry_run` and `enabled` flags. Positions are reconciled on startup via Open API.

Default symbols in `CTraderApp.fxpair_arr`: `[1, 4]` (EURUSD, USDJPY on many demos — **broker-dependent**). Default period: **M1**, capacity **500** bars.

### Signal enum (`SIG`)

`HOLD=101`, `BUY=102`, `SELL=103`, `CLOSE=104`, `TRADE=105`, `NOTRADE=106`, `SIDEWAYS=107`, `NOSIG=108`.

## Coding conventions

- **Python 3.11+** style: `from __future__ import annotations`, dataclasses, enums, type hints.
- Prefer **snake_case** APIs; keep **camelCase aliases** where needed for MQ4/cBot parity (e.g. `fastSlowSIG`, `slopeVal`).
- Shared types only from `ctrader_api.ctypes` (or package re-exports). Do not invent a second type module.
- Async model is **Twisted Deferreds**, not asyncio, for the trading path. Do not force asyncio into `CTraderOpenAPI` without an explicit design change.
- Match MQ4 semantics for slopes/signals: use `mql_at` / shift carefully; document any intentional deviation.
- Log clearly with ✅ / ❌ / ⚠️ prefixes already used in the app; keep startup and bar logs useful for live ops.
- Avoid code bloat from `code_references/mq4` — only migrate what the live path needs.
- Do not print or commit secrets from `.env`.

## Working rules for agents

1. Read `design.txt` before changing signal or execution behavior.
2. When porting MQ4/cBot, compare against the **named functions** in design.txt and keep numerical behavior aligned.
3. Preserve the once-per-cycle pipeline; do not scatter indicator recalculation.
4. Execution changes must honor single-trade / no SL-TP policy unless the user explicitly redesigns TASK 2.
5. Prefer extending existing modules over new top-level packages.
6. `main_fastapi.py` is incomplete; prefer `main.py` / `main_twisted.py` unless the user asks for FastAPI work.
7. No automated test suite yet — when adding logic, prefer small pure functions testable without the live API; use `dry_run=True` for executor checks.
8. This tree is **not a git repo** currently; do not assume remotes/branches exist.

## Key entry points (quick map)

- App start: `main.py` → `CTraderApp(config).run()`
- Auth/connect: `CTraderOpenAPI` in `ctrader_api/client.py`
- Bar → trade: `CTraderApp.run_signal_cycle` in `ctrader_api/ctraderapp.py`
- Orders: `OrderExecutor.handle_signal` in `ctrader_api/execution.py`
- Indicators: `cindicators/snapshot.py::compute_indicators`
- Signals: `csignals/signals.py::SanSignals.init_sig`
- Strategies: `cstrategies/strategies.py::CStrategies.evaluate`

## Dependencies (requirements.txt)

```
ctrader-open-api==0.9.2
python-dotenv>=1.0.0
twisted>=24.3.0
inputimeout>=1.0.4
```

(`main_fastapi.py` would need `fastapi` / `uvicorn` separately if revived.)
