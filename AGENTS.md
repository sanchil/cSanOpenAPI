# grokApp_v6 — Project Instructions

Python trading app that ports an MQ4 / cBot strategy stack onto the **cTrader Open API** (`ctrader-open-api` + Twisted). Migrate only the **current live execution path**, not full MQ4 bloat.

Authoritative product notes: `design.txt`. Reference sources (read-only):

- `code_references/mq4/code_base_v98.txt` — start at `HSIG::initSIG`
- `code_references/cBot/` — `PhyBot.cs`, `CSignal.cs`, `CStrategies.cs`, `CStats.cs`, `CTypes.cs`

Prefer matching **behavior** of the live path over dumping unused helpers.

---

## Architecture (two layers)

```
capp  →  csys     # business may import system
csys  ↛  capp     # system must NOT import business
```

| Layer | Package | Responsibility |
|-------|---------|----------------|
| **System** | `csys/` | Open API proto client, config, types, indicators, order plumbing |
| **App** | `capp/` | Signals, strategies, physics metrics, bar-cycle orchestration (`CTraderApp`) |

### Layout

```
csys/
  ctrader_api/          # client, config, execution, canonical ctypes
  cindicators/         # MA/ATR/ADX/StdDev + snapshot.compute_indicators
  ctypes.py            # facade → csys.ctrader_api.ctypes
capp/
  ctraderapp.py         # CTraderApp
  signals/             # SanSignals, stats (slopeVal / stencil)
  strategies/          # Strategy_1..4
  physics/             # NumPy/SciPy math + series metrics (V1-T24+)
main.py                # primary entry
main_twisted.py        # optional OHLCV HTTP (Twisted)
main_fastapi.py        # incomplete experiment — prefer main.py
code_references/       # migration sources only
design.txt
.env                   # credentials (never commit secrets)
```

### Types (single source of truth)

Canonical: `csys/ctrader_api/ctypes.py`  
Import via: `from csys.ctypes import SIG, IndData, DTYPE, T_SIG` (or `from csys import …`)

Do **not** add a second types module under `capp`.

### Per-bar pipeline (once per cycle)

```
IndData (OHLCV, oldest→newest deques)
  → csys.cindicators.snapshot.compute_indicators
  → capp.strategies.CStrategies.evaluate   # SanSignals.init_sig → Strategy_N
  → csys.ctrader_api.execution.OrderExecutor.handle_signal
  → CTraderApp._sync_position_state
```

Implemented in `capp.ctraderapp.CTraderApp.run_signal_cycle()`.

- History seed: `execute=False`
- Live bars after `_hist_ready`: `execute=True`
- **Rule:** indicators + signals once per bar; rest of cycle only reads `IndData` / `T_SIG` / `last_trade_sig`

### Imports (preferred style)

```python
# System layer
from csys.ctrader_api import CTraderOpenAPI, OrderExecutor, load_config
from csys.ctypes import IndData, SIG, T_SIG, DTYPE
from csys.cindicators import compute_indicators

# Application layer
from capp.signals import SanSignals
from capp.strategies import CStrategies
from capp.physics import covariance_matrix_determinant
from capp import CTraderApp
```

---

## Mission status (design.txt)

| Task | Status | Summary |
|------|--------|---------|
| TASK 1 | Done | `kineticAccelerationSIG`, `fuseSIG`, `slopeVal`, `computeStencilKinematics`, `fastSlowSIG`, `init_sig` path |
| TASK 2 | Done | Single market trade/symbol; no pyramid; no SL/TP; close on CLOSE or opposite |
| TASK 3 | Done | Split into `csys` + `capp` |
| TASK 4 | Done | Types facade, rename `signals`/`strategies`, import organization, `_sync_position_state` |

---

## Execution policy (TASK 2)

| Condition | Action |
|-----------|--------|
| BUY/SELL while flat | Open one market order |
| BUY/SELL same side | No-op |
| Opposite signal | Close only (no reverse open same cycle) |
| CLOSE | Close open position |
| SL / TP | Never attached |
| Pyramid | Forbidden (max 1 position per symbol) |

`OrderExecutor`: `dry_run`, `enabled`, label `GrokApp`. Reconcile on startup.

Defaults: `fxpair_arr=[1,4]` (broker-dependent IDs), period **M1**, capacity **500** bars, volume **10000** units.

### `SIG` enum

`HOLD=101`, `BUY=102`, `SELL=103`, `CLOSE=104`, `TRADE=105`, `NOTRADE=106`, `SIDEWAYS=107`, `NOSIG=108`.

---

## Runtime

- Python **3.11+**, **Twisted** Deferreds (not asyncio on the trading path)
- `ctrader-open-api==0.9.2`, `python-dotenv`, `twisted`

```bash
pip install -r requirements.txt
python main.py
# optional OHLCV:
python main_twisted.py   # http://localhost:8080/ohlcv?symbol_id=1&period=M1&weeks=2
```

### `.env` (project root)

Required: `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_ACCESS_TOKEN`, `CTRADER_ACCOUNT_ID`  
Optional: `CTRADER_REFRESH_TOKEN`, `CTRADER_REDIRECT_URI`, `CTRADER_HOST` (`demo`|`live`)

Loaded by `csys.ctrader_api.config.load_config()`.

---

## Package map

**`csys.ctrader_api`**

- `client.CTraderOpenAPI` — auth, symbols, trendbars, spots, orders
- `execution.OrderExecutor` — single-position market policy
- `ctypes` — `SIG`, `DTYPE`, `T_SIG`, `IndData`, `decode_trendbar`
- `config` — `CTraderConfig` / `load_config`

**`csys.cindicators`**

- Pure series functions; `snapshot.compute_indicators` fills `IndData`
- Series: **oldest → newest**

**`capp.signals`**

- `SanSignals`: `fast_slow_sig`, `kinetic_acceleration_sig`, `fuse_sig`, `init_sig`
- `stats`: `slope_val`, `compute_stencil_kinematics`; MQL shift via `mql_at` (0 = newest)

**`capp.strategies`**

- `CStrategies.evaluate(ind)` → `SIG`
- Strategy 1 (default): micWave alignment → BUY/SELL else CLOSE
- Strategies 2–4: baseSlope / slope30 variants + optional profit close

**`capp.physics`**

- NumPy/SciPy foundation for analysis ports from `code_references` (Stats / MarketMetrics / Physics)
- `math`: `covariance_matrix_2x2`, `matrix_determinant`
- `metrics`: `covariance_matrix_determinant(A, B, n)` — det of 2×2 sample cov over last `n` bars
- Series: **oldest → newest**; no Open API / Twisted imports

**`capp.ctraderapp`**

- Lifecycle: spots → symbols → hist seed → reconcile → live trendbars → signal cycle

---

## Coding conventions

- Python 3.11+: `from __future__ import annotations`, dataclasses, enums, type hints
- Prefer **snake_case**; keep camelCase aliases for MQ4/cBot parity (`fastSlowSIG`, `slopeVal`)
- Shared types only from `csys.ctypes` / `csys.ctrader_api.ctypes`
- Trading path uses **Twisted Deferreds**, not asyncio, unless design changes
- Match MQ4 slope/signal semantics; document intentional deviations
- Logs: keep ✅ / ❌ / ⚠️ style for ops visibility
- Do not print or commit `.env` secrets
- Avoid bloat from `code_references/`

## Working rules for agents

1. Read `design.txt` before changing signal or execution behavior.
2. When porting MQ4/cBot, align with **named functions** in design.txt.
3. Preserve once-per-cycle indicator/signal computation.
4. Honor single-trade / no SL-TP unless user redesigns TASK 2.
5. Put system code in `csys/`, business code in `capp/`; never import `capp` from `csys`.
6. Prefer extending existing modules over new top-level packages.
7. Prefer `main.py` / `main_twisted.py` over incomplete `main_fastapi.py`.
8. No automated test suite yet — pure functions + `OrderExecutor(dry_run=True)` for checks.
9. Tree may not be a git repo; do not assume remotes/branches.

## Key entry points

| Concern | Path |
|---------|------|
| Start | `main.py` → `capp.CTraderApp` |
| Connect | `csys.ctrader_api.client.CTraderOpenAPI` |
| Bar → trade | `capp.ctraderapp.CTraderApp.run_signal_cycle` |
| Orders | `csys.ctrader_api.execution.OrderExecutor.handle_signal` |
| Indicators | `csys.cindicators.snapshot.compute_indicators` |
| Signals | `capp.signals.signals.SanSignals.init_sig` |
| Strategies | `capp.strategies.strategies.CStrategies.evaluate` |
| Physics metrics | `capp.physics.metrics.covariance_matrix_determinant` |

## Dependencies

```
ctrader-open-api==0.9.2
python-dotenv>=1.0.0
twisted>=24.3.0
inputimeout>=1.0.4
numpy>=1.26.0
scipy>=1.11.0
```
