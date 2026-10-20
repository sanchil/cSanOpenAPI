# grokApp_v6 — Project Instructions

Python trading app that ports an MQ4 / cBot strategy stack onto the **cTrader Open API** (`ctrader-open-api` + Twisted). Migrate only the **current live execution path** (MQ4 `HSIG::initSIG`), not full MQ4 bloat.

Authoritative product notes: **`design_v1.txt`** (as-built architecture, bring-up runbook §1.0, gaps G1–G22, forward tasks `V1-Tnn` + status board, invariants). `design.txt` is the historical task log (TASK 1–11) and is kept for provenance only. When docs disagree: code > `design_v1.txt` > this file > `design.txt`.

Reference sources (read-only):

- `code_references/mq4/code_base_v98.txt` — start at `HSIG::initSIG` (parity ground truth)
- `code_references/cBot/` — `PhyBot.cs`, `CSignal.cs`, `CStrategies.cs`, `CStats.cs`, `CTypes.cs`, `PhysicsEngine.cs` (partial, divergent port; where it disagrees with MQ4, MQ4 wins)

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
| **App** | `capp/` | Signals, strategies, PnL averaging, physics metrics, bar-cycle orchestration (`CTraderApp`) |

### Layout

```
csys/
  ctrader_api/
    client.py          # CTraderOpenAPI; is_spot_event, scale_money
    config.py          # CTraderConfig / load_config (.env)
    ctypes.py          # CANONICAL types + constants
    execution.py       # OrderExecutor (single-position policy)
  cindicators/         # SMA/EMA/SMMA/WMA, ATR, ADX, StdDev + snapshot.compute_indicators
  ctypes.py            # facade → csys.ctrader_api.ctypes
capp/
  ctraderapp.py        # CTraderApp (lifecycle + run_signal_cycle + PnL fetch)
  signals/             # SanSignals, stats (slope_val / stencil / mql_at)
  strategies/          # CStrategies Strategy_1..8 (6 = MQ4 parity, 8 = live)
  utils.py             # SanUtils + PositionPnL (pure PnL average)
  physics/             # NumPy math + series metrics (V1-T24, extrema V1-T28)
  tradelog/            # TradeLogAgent: daily trade logs, signal comparison, shadow books, ML CSV (V1-T35)
main.py                # primary entry (active_strategy=8, --dry-run)
main_twisted.py        # optional OHLCV HTTP (Twisted)
main_fastapi.py        # incomplete experiment — do not use
basic.py               # raw SDK sketch — do not use
_requirements.txt      # minimal app dependencies
design_v1.txt          # authoritative blueprint
design.txt             # historical task log
ctraderapp_flow.txt    # lifecycle call graph
code_references/       # migration sources only
.env                   # credentials (never commit / print)
```

### Types (single source of truth)

Canonical: `csys/ctrader_api/ctypes.py`
Import via: `from csys.ctypes import SIG, IndData, DTYPE, T_SIG, SymbolMeta` (or `from csys import …`)

Constants: `DEFAULT_BAR_CAPACITY=500` (IndData default), `DEFAULT_HISTORY_BARS=2000` (live capacity + seed count), `PRICE_SCALE=100_000`.

Do **not** add a second types module under `capp`.

### Per-bar pipeline (once per cycle)

```
IndData (OHLCV, oldest→newest deques)
  → csys.cindicators.snapshot.compute_indicators
  → [live only] CTraderApp.fetch_position_pnls        # single-flight reconcile → unrealized PnL
        → capp.utils.SanUtils.get_total_trade_profits # multi-asset avg float PnL
  → capp.strategies.CStrategies.evaluate              # SanSignals.init_sig → Strategy_N
  → csys.ctrader_api.execution.OrderExecutor.handle_signal
  → CTraderApp._sync_position_state
```

Implemented in `capp.ctraderapp.CTraderApp.run_signal_cycle()`.

- History seed: `execute=False` (no PnL fetch, no orders; Strategy_6 sticky state is still updated; Strategy_7 / Strategy_8 / Strategy_9 are stateless)
- Live bars after `_hist_ready`: `execute=True`
- `dry_run`: PnL fetch returns `[]` (avg 0), orders simulated, startup reconcile skipped
- **Rule:** indicators + signals once per bar; rest of cycle only reads `IndData` / `T_SIG` / `last_trade_sig` / `last_total_trade_profits`

### Inbound messages

The SDK forwards **every** inbound message (including RPC responses like `GetTrendbarsRes`, and events like `SymbolChangedEvent` with repeated `symbolId`) to `on_message` before the request Deferred fires. `on_tick` therefore ignores everything except `ProtoOASpotEvent` (`is_spot_event`) and casts `symbolId` to `int`. Keep that gate in any new handler.

### Imports (preferred style)

```python
# System layer
from csys.ctrader_api import CTraderOpenAPI, OrderExecutor, load_config
from csys.ctypes import IndData, SIG, T_SIG, DTYPE
from csys.cindicators import compute_indicators

# Application layer
from capp.signals import SanSignals
from capp.strategies import CStrategies
from capp.utils import SanUtils, PositionPnL
from capp.physics import covariance_matrix_determinant, find_extrema, find_extrema_scipy
from capp import CTraderApp
```

---

## Mission status

Legacy `design.txt` tasks:

| Task | Status | Summary |
|------|--------|---------|
| TASK 1 | Done | `kineticAccelerationSIG`, `fuseSIG`, `slopeVal`, `computeStencilKinematics`, `fastSlowSIG`, `init_sig` path |
| TASK 2 | Done | Single market trade/symbol; no pyramid; no SL/TP; close on CLOSE or opposite |
| TASK 3 | Done | Split into `csys` + `capp` |
| TASK 4 | Done | Types facade, rename `signals`/`strategies`, import organization, `_sync_position_state` |
| TASK 5–6 | Learning | mq4/cBot design study, MarketMetrics study (no code) |
| TASK 7 | Done | `SanUtils.get_total_trade_profits` pure average |
| TASK 8 | Done | Magic number optional; cTrader filters by label |
| TASK 9 | Done | `init_ind_data` symbol meta (digits, pip_size, point, pip_value, period seconds) |
| TASK 10 | Done | PnL wired: `fetch_position_pnls` → `SanUtils` |
| TASK 11 | Done | Single-flight PnL, sequential reconcile → unrealized PnL, degrade to 0 on failure |

`design_v1.txt` tasks (see its status board for the full list):

| Task | Status | Summary |
|------|--------|---------|
| V1-T02 | Done | Docs sync (this file, `ctraderapp_flow.txt`) |
| V1-T03 | Partial | Spot-event gate + int `symbolId` done; coerce helper pending |
| V1-T24 | Done | `capp/physics` covariance-matrix determinant |
| V1-T25 | Done | MQ4 parity: SMMA indicators (21), slopes 5/21, 2000-bar seed, Strategy_6, `--dry-run` |
| V1-T26 | Done | `kinetic_acceleration_sig_v1` + Strategy_7 (intentional MQ4 deviation; live default until V1-T30) |
| V1-T37 | Done | `tools/signal_eval.py` offline evaluation of signals / strategies over ML feature CSVs (TASK 13 critique review) |
| V1-T36 | Done | Trade journal (`capp/tradelog/journal.py`): real fills via `csys` `parse_deal_fill`, slippage vs. decision quote, spread, commission / swap / net, MFE / MAE, bars held, exit reason; `logs/trades_<day>.csv`; shadow books gain MFE / MAE |
| V1-T35 | Done | `capp/tradelog` TradeLogAgent (levels, file per UTC day, signal/strategy comparison, shadow paper books, period summaries) + MLRecorder CSV; `--log-level`, `--ml-capture` |
| V1-T34 | Done | `slope_distribution_sig` → `T_SIG.slope_dist_sig` / `slope_dist_data` (skew + median side, two-sided tail veto); no strategy |
| V1-T33 | Done | `capp/physics/metrics.py` MarketMetrics port (pure functions, not wired; supersedes V1-T11) |
| V1-T28 | Done | `capp/physics` local extrema: `find_extrema` (NumPy) / `find_extrema_scipy` (find_peaks) |
| V1-T29 | Done | `peak_trough_sig` → `T_SIG.peak_trough_sig` (higher/equal-zone/lower peaks); used by Strategy_8 |
| V1-T30 | Done | Strategy_8 on `peak_trough_sig` (SIDEWAYS → CLOSE, BUY/SELL → side, else hold) — **live default** |
| V1-T40 | Done | Strategy_8 back to `pt_mode="peaks"`, ProfitTrail opt-in (`--pt-mode`, `--profit-trail`); `peak_trough_sig_alt` shadow; `tools/replay.py` |
| V1-T42 | Done | Connection health: 10 s heartbeat, watchdog (probe / abort / sleep jump), session rebuild on every connect, in-session token refresh + `.ctrader_tokens.json`, `send()` → `NotReadyError` / `ApiError`, trading gated until reconcile, `deploy/ctrader-bot.service` |
| V1-T41 | Done | Strategy_8 defaults back to `pt_mode="structure"` + ProfitTrail (Oct 19 build); opt out with `--pt-mode peaks` / `--no-profit-trail` |
| V1-T31 | Done | `peak_trough_sig` rework: peaks + troughs equal weight (score), zone-break override, default `pt_mode="structure"` |
| V1-T27 | Done | v1 `mode="acc"` (ATR-normalized acceleration, entry margin) + real flat-regime hysteresis |
| V1-T32 | Done | `kinetic_acceleration_sig_v2` (tail vs. history, `kin_v2_period=120`) + Strategy_9 (any CLOSE/SIDEWAYS → CLOSE, no PnL gate); selectable, not live |

---

## Signals & strategies

`SanSignals.init_sig` (MQ4 `SS::update` / `HSIG::initSIG` parameters):

- `base_slope_data = slope_val(ima240, 5, 21, shift=1)`, `slope30_data = slope_val(ima30, 5, 21, shift=1)`
- `base_slope_sig = kinetic_acceleration_sig(val1, val2, 0.015, -0.06)`; `slope30_sig = …(0.015, -0.2)`
- `base_slope_sig_v1` / `slope30_sig_v1` = `kinetic_acceleration_sig_v1(val1, val2, atr_pips, prev_flat=…)` via `SanSignals._kin_v1`. Flat regime with hysteresis (`|slow|/atr_pips` < 0.01 → flat, ≥ 0.02 → trending, between → previous regime, stored per symbol and series) → SIDEWAYS when flat. Trending, `kin_v1_mode="acc"` (default): `acc = sign(s)·(f-s)/atr_pips`; `f·s>0 and acc ≥ 0.002` → BUY/SELL, `f·s≤0 or acc < -0.010` (slope30 -0.015) → CLOSE, else NOSIG. `kin_v1_mode="ratio"` keeps the V1-T26 `f/s-1` gates (0 / -0.20 / -0.30) for comparison
- `peak_trough_sig` = `SanSignals.peak_trough_sig(high, low, zone=0.5·atr_pips, close=close[shift])` on the last 240 closed bars, `find_extrema(order=5)` on raw prices. Each side's last two extrema → +1 / 0 (within zone) / -1; a close beyond the last peak (trough) zone forces peaks +1 (troughs -1). `pt_mode="structure"` (V1-T31, default; restored by V1-T41): score = peaks + troughs, > 0 → BUY, < 0 → SELL, 0 → SIDEWAYS, too few extrema → NOSIG. `pt_mode="peaks"` (opt-in): peaks only. `T_SIG.peak_trough_sig_alt` holds the other mode (shadow only). Stateless; not MQ4; read by Strategy_8 (V1-T29/T30/T31)
- `base_slope_sig_v2` = `kinetic_acceleration_sig_v2(f, s, atr_pips)` on `slope_val(ima{kin_v2_period}, 5, 21)` (default ima120). `old = (21·s − 5·f)/16`; in ATR/bar: `|f| < 0.01` → SIDEWAYS if `|o| < 0.02` else CLOSE; `|f| < 0.02` → NOSIG (dead band); `sign(f)·(f−o) ≥ 0.01` → BUY/SELL by sign(f); `≤ −0.03` → CLOSE; else NOSIG. Stateless; not MQ4; read by Strategy_9 (V1-T32)
- `slope_dist_sig` = `SanSignals.slope_distribution_sig(slope_distribution(close, 60, shift))` (V1-T34, not MQ4; no strategy reads it yet). Quantile shape of 59 one-bar slopes: Kelly skew `(P90+P10−2·P50)/(P90−P10)` and per-side tail ratios. Both tail ratios > 2.6 → NOTRADE (whipsaw); Kelly ≤ −0.25 with median > 0 → BUY; Kelly ≥ +0.25 with median < 0 → SELL; else NOSIG. Data in `T_SIG.slope_dist_data`
- `fsigN = fast_slow_sig(close[1], imaN[1])`; `fast_sig = fuse(fuse(fsig5, fsig14), fsig30)` (CLOSE if NOSIG)
- `micro_wave_sig` is an interim proxy (true MQ4 `microWaveSIG` not ported)

`CStrategies` (`profit_close_threshold=4.0`, `mq4_profit_close_threshold=2.0`, `decel_profit_close_threshold=2.0`):

- **Strategy 7 (live default, V1-T26)**: stateless, on `base_slope_sig_v1`. SIDEWAYS (flat) → CLOSE always; CLOSE (decel) → CLOSE only if avg PnL ≥ 2.0, else NOSIG; BUY/SELL → that side; else NOSIG
- Strategy 6 (MQ4-parity baseline): MQ4 baseSlopeSIG composite → `setSIGForStrategy_v3` → `dominantSIG`, with sticky per-symbol state (`_HSigState`, `reset_state()`); profit close at avg PnL ≥ 2.0; entry on directional baseSlope with no fsig gate
- Strategy 1: micWave alignment → BUY/SELL else CLOSE
- Strategies 2–3: cBot baseSlope variants (fsig alignment gate, profit close 4.0)
- Strategies 4–5: slope30 variants (5 is Python-only, no MQ4/cBot origin)

Changing the default strategy is a product decision — ask the user first.

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

`OrderExecutor`: `dry_run`, `enabled`, label **`cSanOpenAPI`** (set by `CTraderApp`; `OrderExecutor`'s own default `GrokApp` is overridden). Reconcile and PnL only see positions with this label. Reconcile on startup.

Defaults (hardcoded until V1-T01): `fxpair_arr=[1,4]` (broker-dependent IDs; EURUSD/USDJPY on FxPro demo), period **M1**, capacity/seed **2000** bars, volume **10000** units, strategy **6**.

Indicators (`compute_indicators`, MQ4 `RefreshPhysicsData` parity): `ima5..ima500` SMMA; ATR(21, simple mean of TR); ADX/DI(21, Wilder); StdDev(21, EMA centre, ddof 0) on close/open; `avg_std`. RSI/MFI/OBV not computed.

### `SIG` enum

`HOLD=101`, `BUY=102`, `SELL=103`, `CLOSE=104`, `TRADE=105`, `NOTRADE=106`, `SIDEWAYS=107`, `NOSIG=108`.

---

## Runtime

- Python **3.11+**, **Twisted** Deferreds (not asyncio on the trading path)
- Conda env on this host: `genai`

```bash
cd claudeApp_v1
pip install -r _requirements.txt
python main.py --dry-run          # signals + simulated orders (or CTRADER_DRY_RUN=1)
python main.py                    # LIVE: sends real orders
python tools/signal_eval.py logs/ml/features_*.csv --by-symbol   # offline signal scoring: forward returns (ATR), hit rate, edge, MFE/MAE, NOTRADE gate, kinetic v2 flips
python main.py --log-level debug --ml-capture   # trade log level off|bar|signals|debug (default signals) → logs/trade_<UTC day>.log; ML rows → logs/ml/
python main_twisted.py            # optional: http://localhost:8080/ohlcv?symbol_id=1&period=M1&weeks=2
```

`requirements.txt` / `environment.yaml` are full freezes of a large ML env, not the app's minimal set.

### `.env` (project root)

Required: `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_ACCESS_TOKEN`, `CTRADER_ACCOUNT_ID`
Optional: `CTRADER_REFRESH_TOKEN`, `CTRADER_REDIRECT_URI`, `CTRADER_HOST` (`demo`|`live`), `CTRADER_DRY_RUN` (read by `main.py`)

Loaded by `csys.ctrader_api.config.load_config()`. Full bring-up and troubleshooting: `design_v1.txt` §1.0.

---

## Package map

**`csys.ctrader_api`**

- `client.CTraderOpenAPI` — auth, symbols, trendbars, spots, orders, reconcile, unrealized PnL; stops the reactor on auth error before ready
- `client.is_spot_event`, `client.scale_money` — message gate, moneyDigits scaling
- `execution.OrderExecutor` — single-position market policy
- `ctypes` — `SIG`, `DTYPE`, `T_SIG`, `IndData`, `SymbolMeta`, `decode_trendbar`, `period_seconds`
- `config` — `CTraderConfig` / `load_config`

**`csys.cindicators`**

- Pure series functions; `snapshot.compute_indicators` fills `IndData`
- Series: **oldest → newest**; `_utils.finalize(size=None)` caps at 500 — pass explicit sizes internally

**`capp.signals`**

- `SanSignals`: `fast_slow_sig`, `kinetic_acceleration_sig`, `fuse_sig`, `init_sig`
- `stats`: `slope_val`, `compute_stencil_kinematics`; MQL shift via `mql_at` (0 = newest)

**`capp.strategies`**

- `CStrategies.evaluate(ind, total_trade_profits=…)` → `SIG`; Strategy_1..8 (see above)

**`capp.utils`**

- `SanUtils.get_total_trade_profits(positions, label=…)` — pure multi-asset average; no network

**`capp.physics`**

- `math`: `covariance_matrix_2x2`, `matrix_determinant`
- `metrics`: `covariance_matrix_determinant(A, B, n)` — det of 2×2 sample cov over last `n` bars (not wired into the bar cycle)
- `metrics`: `find_extrema(series, order, pip_size)` / `find_extrema_scipy(series, prominence=, distance=, width=, pip_size=)` → `(maxima, minima)` lists of `(index, value)`; plateaus at leftmost bar; used by `SanSignals.peak_trough_sig` (V1-T29)
- `metrics` (V1-T33): MQ4 `class MarketMetrics` as pure functions — ADX physics (`adx_potential`, `adx_kinetic`, `adx_vector`), volatility (`atr_kinetic`, `atr_scale`, `volatility_anomaly`, `efficiency_ratio`, `vwcm`, `trend_quality`, `trend_accel_strength`, `overall_market_force`), slope geometry (`expansion_compression_ratio`, `slope_acceleration_ratio`, `fractal_alignment`, `geometric_fan_score`, `layered_momentum_filter`), retention, hold scores (`bayesian_hold_score`, `neuron_hold_score`, combined scores), fusion (`analyse_history`, `fuse_sig_log_odds`, `fuse_probability`) and fast-vs-slow pair scores. `shift=1` default; MQ4 globals are parameters (`pip_size`, `period_seconds`); camelCase aliases in the module. MQ4 bugs fixed and skipped members are listed in the section header. Not wired into any signal or strategy
- Series: **oldest → newest**; no Open API / Twisted imports

**`capp.ctraderapp`**

- Lifecycle: auth → spots → symbols → symbol meta → hist seed (2000 bars) → reconcile → live trendbars → signal cycle per closed bar
- `fetch_position_pnls`: single-flight; joiners use `_share_pnl_result` (never chain onto the master)

---

## Coding conventions

- Python 3.11+: `from __future__ import annotations`, dataclasses, enums, type hints
- Prefer **snake_case**; keep camelCase aliases for MQ4/cBot parity (`fastSlowSIG`, `slopeVal`)
- Shared types only from `csys.ctypes` / `csys.ctrader_api.ctypes`
- Trading path uses **Twisted Deferreds**, not asyncio, unless design changes
- Match MQ4 slope/signal semantics; document intentional deviations in the module docstring
- Logs: keep ✅ / ❌ / ⚠️ style for ops visibility
- Do not print or commit `.env` secrets
- Avoid bloat from `code_references/`

## Working rules for agents

1. Read `design_v1.txt` before changing signal or execution behavior.
2. When porting MQ4/cBot, align with **named functions** on the MQ4 live path.
3. Preserve once-per-cycle indicator/signal computation.
4. Honor single-trade / no SL-TP unless a redesign task (V1-T18/T19) says otherwise.
5. Put system code in `csys/`, business code in `capp/`; never import `capp` from `csys`.
6. Prefer extending existing modules over new top-level packages.
7. Use `main.py` / `main_twisted.py`; not `main_fastapi.py` or `basic.py`.
8. No automated test suite yet — pure functions via `python -c` + `OrderExecutor(dry_run=True)` for checks. Don't run `main.py` live to verify changes.
9. Tree is not a git repo; do not assume remotes/branches.
10. When a change lands, update `design_v1.txt` (matching §1 subsection, gaps, status board) and this file.

## Key entry points

| Concern | Path |
|---------|------|
| Start | `main.py [--dry-run]` → `capp.CTraderApp` |
| Connect | `csys.ctrader_api.client.CTraderOpenAPI` |
| Message gate | `csys.ctrader_api.client.is_spot_event` |
| Bar → trade | `capp.ctraderapp.CTraderApp.run_signal_cycle` |
| PnL fetch | `capp.ctraderapp.CTraderApp.fetch_position_pnls` |
| Avg PnL | `capp.utils.SanUtils.get_total_trade_profits` |
| Orders | `csys.ctrader_api.execution.OrderExecutor.handle_signal` |
| Indicators | `csys.cindicators.snapshot.compute_indicators` |
| Signals | `capp.signals.signals.SanSignals.init_sig` |
| Strategies | `capp.strategies.strategies.CStrategies.evaluate` |
| Physics metrics | `capp.physics.metrics.covariance_matrix_determinant`, `find_extrema`, `find_extrema_scipy` |

## Dependencies (`_requirements.txt`)

```
ctrader_open_api==0.9.2
Twisted==26.4.0
python-dotenv==1.2.3
numpy==2.5.2
fastapi==0.141.1      # main_fastapi.py experiment only
uvicorn==0.52.4       # main_fastapi.py experiment only
scipy==1.17.1         # capp.physics find_extrema_scipy / layered_momentum_filter (lazy imports)
```

`scipy` is used only by `capp.physics.find_extrema_scipy` and `layered_momentum_filter` (imported lazily); nothing on the live path imports it.
