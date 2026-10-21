#!/usr/bin/env python3
"""
Offline bar replay of strategy variants over M1 trendbars (V1-T40, TASK 16).

Answers "what would variant X have done over these days?" without waiting
for live trades, including stretches the live bot missed (host sleep).

Two steps (from claudeApp_v1/):
  python tools/replay.py --fetch [--symbol 4 --days 10]
      one read-only connection: ProtoOAGetTrendbarsReq in 2-day chunks →
      logs/bars/<symbol>_M1_<first day>_<last day>.csv. No orders, no
      executor, no reconcile.
  python tools/replay.py logs/bars/*.csv [--window 2026-10-20T01:00 2026-10-20T05:30]

Replay per bar k, mirroring the live cycle (CTraderApp._apply_live_trendbar):
  - bar k is appended as the forming bar with O=H=L=C=open[k] (live sees
    only its first update), compute_indicators runs once, and init_sig runs
    with shift=1 (bar k-1 closed). One SanSignals per pt_mode.
  - each variant runs its strategy (own avg-PnL input for Strategy_6/7), then
    ProfitTrail on bar k-1 when enabled, then the OrderExecutor policy at
    open[k]: flat + BUY/SELL opens, same side holds, opposite or CLOSE closes
    (no reversal in the same cycle, no SL/TP).
  - bar k is then completed in place for the next cycle.
The first DEFAULT_HISTORY_BARS bars are warm-up only (the live seed).

Deviations from live, on purpose: bars are bid, so BUY fills are not lifted
by the spread; costs are applied per trade instead (--spread pips +
--commission USD round turn). Weekend / data gaps are appended through, which
matches live's reseed (the reloaded history has the same hole). Positions
are held through gaps, as live does.

Variants: S8 swing / S8 swing+trail (V1-T44 default: swing, no trail) /
S8 peaks / S8 peaks+trail / S8 structure / S8 structure+trail / S7 / S6.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capp.signals import SanSignals  # noqa: E402
from capp.strategies import CStrategies, ProfitTrail  # noqa: E402
from csys.cindicators.snapshot import compute_indicators  # noqa: E402
from csys.ctypes import SIG, IndData  # noqa: E402
from csys.ctrader_api.ctypes import DEFAULT_HISTORY_BARS, decode_trendbar  # noqa: E402

NAN = float("nan")
BAR_FIELDS = ("utc_minutes", "time", "open", "high", "low", "close", "volume")


# ------------------------------------------------------------------- fetch
def fetch(symbol_id: int, days: int, out_dir: str, digits: int, chunk_days: int = 2) -> None:
    """Read-only trendbar download in chunks, newest window first."""
    from twisted.internet import defer, reactor

    from csys import CTraderOpenAPI, load_config

    api = CTraderOpenAPI(load_config())
    bars: Dict[int, dict] = {}
    now = datetime.now(timezone.utc)
    windows = []
    to = now
    start = now - timedelta(days=days)
    while to > start:
        frm = max(to - timedelta(days=chunk_days), start)
        windows.append((int(frm.timestamp() * 1000), int(to.timestamp() * 1000)))
        to = frm

    @defer.inlineCallbacks
    def run():
        try:
            for frm, to_ in windows:
                resp = yield api.get_trendbars(
                    symbol_id=symbol_id, period="M1", count=chunk_days * 1440 + 10,
                    from_timestamp=frm, to_timestamp=to_, client_msg_id=f"replay_{frm}",
                )
                got = [decode_trendbar(b, digits=digits) for b in getattr(resp, "trendbar", [])]
                for b in got:
                    bars[b["utc_minutes"]] = b
                print(f"   {datetime.fromtimestamp(frm / 1000, timezone.utc):%Y-%m-%d %H:%M} → "
                      f"{datetime.fromtimestamp(to_ / 1000, timezone.utc):%Y-%m-%d %H:%M}: {len(got)} bars")
            _write(bars, symbol_id, out_dir)
        except Exception as e:  # noqa: BLE001 — report and stop the reactor
            print(f"❌ fetch failed: {e}")
        finally:
            yield api.disconnect()  # may already stop the reactor
            if reactor.running:
                try:
                    reactor.stop()
                except Exception:  # noqa: BLE001 — ReactorNotRunning race
                    pass

    api.on_ready = lambda: run()
    api.run()


def _write(bars: Dict[int, dict], symbol_id: int, out_dir: str) -> None:
    if not bars:
        print("❌ no bars received")
        return
    rows = [bars[m] for m in sorted(bars)]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(
        out_dir, f"{symbol_id}_M1_{rows[0]['time']:%Y-%m-%d}_{rows[-1]['time']:%Y-%m-%d}.csv"
    )
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(BAR_FIELDS)
        for b in rows:
            w.writerow([b["utc_minutes"], b["time"].isoformat(), b["open"], b["high"], b["low"],
                        b["close"], b["volume"]])
    print(f"✅ {len(rows)} bars → {path}")


def load_bars(paths: List[str]) -> List[dict]:
    bars: Dict[int, dict] = {}
    for p in paths:
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                m = int(r["utc_minutes"])
                bars[m] = {
                    "utc_minutes": m,
                    "time": datetime.fromisoformat(r["time"]),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r["volume"]),
                }
    return [bars[m] for m in sorted(bars)]


# ------------------------------------------------------------------ replay
@dataclass
class Trade:
    side: SIG
    entry_time: datetime
    entry: float
    exit_time: Optional[datetime] = None
    exit: float = NAN
    bars: int = 0
    mfe: float = 0.0          # pips, best excursion
    mae: float = 0.0          # pips, worst excursion (<= 0)
    reason: str = ""

    def pips(self, price: float, pip: float) -> float:
        s = 1.0 if self.side == SIG.BUY else -1.0
        return s * (price - self.entry) / pip


@dataclass
class Variant:
    name: str
    pt_mode: str
    strategy: int
    trail: bool
    profit_trail: Optional[ProfitTrail] = None
    pos: Optional[Trade] = None
    trades: List[Trade] = field(default_factory=list)
    equity: Dict[datetime, float] = field(default_factory=dict)  # gross pips at bar close
    realized: float = 0.0

    def __post_init__(self) -> None:
        if self.trail:
            self.profit_trail = ProfitTrail()


def default_variants() -> List[Variant]:
    return [
        Variant("S8 swing", "swing", 8, False),
        Variant("S8 swing+trail", "swing", 8, True),
        Variant("S8 peaks", "peaks", 8, False),
        Variant("S8 peaks+trail", "peaks", 8, True),
        Variant("S8 structure", "structure", 8, False),
        Variant("S8 structure+trail", "structure", 8, True),
        Variant("S7", "peaks", 7, False),
        Variant("S6", "peaks", 6, False),
    ]


def _make_ind(capacity: int, pip: float, digits: int, symbol_id: int) -> IndData:
    ind = IndData(capacity=capacity)
    ind.symbol_id = symbol_id
    ind.pip_size = pip
    ind.digits = digits
    ind.point = 10.0 ** -digits
    ind.pip_position = digits - 1
    ind.dbl_epsilon = ind.point * 0.1
    ind.period = 60
    ind.current_period = 60.0
    ind.bars_held = 0
    ind.shift = 1
    ind.total_orders = 0
    return ind


def replay(bars: List[dict], *, pip: float, digits: int, symbol_id: int, warm: int,
           usd_per_pip: float) -> List[Variant]:
    variants = default_variants()
    signals = {m: SanSignals(default_shift=1, pt_mode=m) for m in ("swing", "peaks", "structure")}
    strategies = {m: CStrategies(s, active=8) for m, s in signals.items()}
    ind = _make_ind(warm, pip, digits, symbol_id)
    ind.load_bars(bars[:warm])

    t0 = time.time()
    n = len(bars)
    for k in range(warm, n):
        b = bars[k]
        o = b["open"]
        ind.append_bar(o, o, o, o, 0.0, b["time"])  # forming bar as live first sees it
        compute_indicators(ind)
        t_sigs = {m: s.init_sig(ind, shift=1) for m, s in signals.items()}
        prev_h, prev_l, prev_c = ind.high[-2], ind.low[-2], ind.close[-2]
        prev_atr = ind.atr[-2] if len(ind.atr) >= 2 else NAN

        for v in variants:
            float_usd = v.pos.pips(prev_c, pip) * usd_per_pip if v.pos else 0.0
            sig = strategies[v.pt_mode].run_strategy(
                v.strategy, t_sigs[v.pt_mode], float_usd, symbol_id=symbol_id
            )
            reason = sig.name if hasattr(sig, "name") else str(sig)
            if v.profit_trail is not None:
                side = v.pos.side if v.pos else SIG.NOSIG
                sig = v.profit_trail.apply(
                    symbol_id, sig, side, entry_price=v.pos.entry if v.pos else NAN,
                    high=prev_h, low=prev_l, close=prev_c, atr=prev_atr,
                )
                if v.profit_trail.last_fired.get(symbol_id):
                    reason = "TRAIL"
            _execute(v, sig, reason, o, b["time"], pip)

        ind.update_last_bar(o, b["high"], b["low"], b["close"], b["volume"], b["time"])
        for v in variants:
            if v.pos is not None:
                v.pos.bars += 1
                v.pos.mfe = max(v.pos.mfe, v.pos.pips(b["high"] if v.pos.side == SIG.BUY else b["low"], pip))
                v.pos.mae = min(v.pos.mae, v.pos.pips(b["low"] if v.pos.side == SIG.BUY else b["high"], pip))
            unreal = v.pos.pips(b["close"], pip) if v.pos else 0.0
            v.equity[b["time"]] = v.realized + unreal
        if (k - warm) % 1000 == 0:
            print(f"   … {k - warm}/{n - warm} bars ({time.time() - t0:.0f}s)", file=sys.stderr)
    return variants


def _execute(v: Variant, sig: SIG, reason: str, price: float, t: datetime, pip: float) -> None:
    """OrderExecutor.handle_signal policy (one position, no reversal)."""
    if v.pos is not None:
        opposite = (v.pos.side == SIG.BUY and sig == SIG.SELL) or (
            v.pos.side == SIG.SELL and sig == SIG.BUY)
        if sig == SIG.CLOSE or opposite:
            v.pos.exit, v.pos.exit_time = price, t
            v.pos.reason = f"opposite {sig.name}" if opposite else reason
            v.realized += v.pos.pips(price, pip)
            v.trades.append(v.pos)
            v.pos = None
        return
    if sig in (SIG.BUY, SIG.SELL):
        v.pos = Trade(sig, t, price)


# ------------------------------------------------------------------ report
def _net_usd(tr: Trade, pip: float, spread: float, commission: float, usd_per_pip: float) -> float:
    return (tr.pips(tr.exit, pip) - spread) * usd_per_pip - commission


def report(variants: List[Variant], *, pip: float, spread: float, commission: float,
           usd_per_pip: float, window: Optional[Tuple[datetime, datetime]]) -> None:
    days = sorted({tr.exit_time.date() for v in variants for tr in v.trades})
    print(f"\nPer UTC day (closed trades; pips gross, USD net of {spread:g}p spread + "
          f"{commission:g} USD commission, {usd_per_pip:.3f} USD/pip)")
    hdr = f"{'variant':<20} {'day':<10} {'n':>4} {'win%':>5} {'pips':>7} {'netUSD':>8} {'bars':>5} {'MFE':>5} {'MAE':>6}"
    print(hdr)
    print("-" * len(hdr))
    for v in variants:
        by_day: Dict = defaultdict(list)
        for tr in v.trades:
            by_day[tr.exit_time.date()].append(tr)
        for d in days + ["ALL"]:
            trs = v.trades if d == "ALL" else by_day.get(d, [])
            if not trs:
                continue
            p = [tr.pips(tr.exit, pip) for tr in trs]
            usd = sum(_net_usd(tr, pip, spread, commission, usd_per_pip) for tr in trs)
            print(f"{v.name:<20} {str(d):<10} {len(trs):>4} {100 * sum(x > 0 for x in p) / len(p):>5.0f} "
                  f"{sum(p):>+7.1f} {usd:>+8.2f} {sum(tr.bars for tr in trs) / len(trs):>5.0f} "
                  f"{sum(tr.mfe for tr in trs) / len(trs):>+5.1f} {sum(tr.mae for tr in trs) / len(trs):>+6.1f}")
        if v.pos is not None:
            last = max(v.equity) if v.equity else None
            print(f"{'':<20} open {v.pos.side.name} since {v.pos.entry_time:%m-%d %H:%M} "
                  f"@ {v.pos.entry:.3f}, unrealized {v.equity[last] - v.realized:+.1f}p")
        print()

    if window is None:
        return
    w0, w1 = window
    print(f"Window {w0:%Y-%m-%d %H:%M} → {w1:%Y-%m-%d %H:%M} UTC (mark-to-market pips, trades touching it)")
    for v in variants:
        ts = [t for t in v.equity if w0 <= t <= w1]
        if not ts:
            print(f"  {v.name:<20} no bars in window")
            continue
        before = [t for t in v.equity if t < w0]
        e0 = v.equity[max(before)] if before else 0.0
        e1 = v.equity[max(ts)]
        print(f"  {v.name:<20} {e1 - e0:+7.1f}p")
        trs = [tr for tr in v.trades if tr.exit_time >= w0 and tr.entry_time <= w1]
        if v.pos is not None and v.pos.entry_time <= w1:
            trs.append(v.pos)
        for tr in trs:
            ex = f"{tr.exit_time:%m-%d %H:%M} @ {tr.exit:.3f} ({tr.reason}) {tr.pips(tr.exit, pip):+.1f}p" \
                if tr.exit_time else "open"
            print(f"      {tr.side.name:<4} {tr.entry_time:%m-%d %H:%M} @ {tr.entry:.3f} → {ex}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("csv", nargs="*", help="bar CSVs from --fetch")
    ap.add_argument("--fetch", action="store_true", help="download M1 bars (read-only) and exit")
    ap.add_argument("--symbol", type=int, default=4, help="broker symbol id (4 = USDJPY demo)")
    ap.add_argument("--days", type=int, default=10, help="calendar days to fetch")
    ap.add_argument("--out-dir", default="logs/bars")
    ap.add_argument("--pip", type=float, default=0.01)
    ap.add_argument("--digits", type=int, default=3)
    ap.add_argument("--warm", type=int, default=DEFAULT_HISTORY_BARS, help="seed bars (no trading)")
    ap.add_argument("--spread", type=float, default=0.3, help="pips per round turn")
    ap.add_argument("--commission", type=float, default=0.70, help="USD per round turn")
    ap.add_argument("--usd-per-pip", type=float, default=0.633, help="10k USDJPY ≈ 100 JPY / 158")
    ap.add_argument("--window", nargs=2, metavar=("FROM", "TO"), help="UTC ISO times, e.g. 2026-10-20T01:00")
    a = ap.parse_args()

    if a.fetch:
        fetch(a.symbol, a.days, a.out_dir, a.digits)
        return
    paths = [p for pat in a.csv for p in glob.glob(pat)]
    if not paths:
        ap.error("no bar CSVs (run with --fetch first)")
    bars = load_bars(paths)
    if len(bars) <= a.warm:
        ap.error(f"need more than {a.warm} bars, got {len(bars)}")
    print(f"{len(bars)} bars {bars[0]['time']} → {bars[-1]['time']}; trading from {bars[a.warm]['time']}")
    variants = replay(bars, pip=a.pip, digits=a.digits, symbol_id=a.symbol, warm=a.warm,
                      usd_per_pip=a.usd_per_pip)
    window = (datetime.fromisoformat(a.window[0]), datetime.fromisoformat(a.window[1])) if a.window else None
    report(variants, pip=a.pip, spread=a.spread, commission=a.commission,
           usd_per_pip=a.usd_per_pip, window=window)


if __name__ == "__main__":
    main()
