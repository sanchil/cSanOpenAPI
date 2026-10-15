#!/usr/bin/env python3
"""
Offline signal evaluation over MLRecorder feature CSVs (V1-T37, TASK 13).

Scores every closed bar's signal against what price did next, instead of
waiting for dozens of closed trades. Input: logs/ml/features_<day>.csv
written by ``python main.py --ml-capture`` (capp/tradelog/ml.py columns).

Labels per row i (ATR units, NaN across gaps / missing ATR):
  fwd_h     (close[i+h] - close[i]) / atr[i]            h in --horizons
  mfe/mae   best / worst move over rows i+1..i+N         N = --early
            (from bar high / low), signed by the signal's direction

Views per signal (BUY = +1, SELL = -1, everything else non-directional):
  bar    every bar the signal is BUY / SELL (many samples, heavily overlapped)
  onset  first bar of each new BUY / SELL run — a trade-entry proxy and the
         honest sample size

Metrics: n, mean signed fwd_h, hit rate P(signed fwd_h > 0), edge = mean
d·(fwd_h - drift) with drift = the symbol's mean fwd_h over all bars,
t-stat (onset view), mean MFE / MAE over the early window, and wrong-early
rate P(signed MAE <= -adverse ATR).

Extra sections:
  gate   onset metrics split by --gate == NOTRADE (default slope_dist_sig):
         would the whipsaw veto have helped the other signals?
  flips  base_slope_sig_v2 onsets where the 5-bar tail points against the
         16-bar history (old = (21·s - 5·f)/16, as in
         SanSignals.kinetic_acceleration_sig_v2), bucketed by |f|/|old|:
         data for a possible flip guard.

Standalone on purpose: stdlib csv + NumPy only, no capp / Open API imports.

Usage (from claudeApp_v1/):
  python tools/signal_eval.py logs/ml/features_*.csv
  python tools/signal_eval.py logs/ml/*.csv --horizons 5 15 30 --early 5 \\
      --adverse 0.5 --by-symbol --out report.csv
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np

DEFAULT_SIGNALS = ("base_slope_sig_v2", "peak_trough_sig", "slope_dist_sig")
MIN_ONSETS = 30
NAN = float("nan")


# ------------------------------------------------------------------ loading
def _float(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return NAN


def _epoch(s: str) -> float:
    """close_time is naive UTC (bar times); aware strings keep their offset."""
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return NAN
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def load(paths: Sequence[str]) -> tuple[Dict[str, Dict[str, list]], dict]:
    """Rows grouped by symbol, deduped on close_time, sorted by time."""
    by_sym: Dict[str, Dict[float, dict]] = defaultdict(dict)
    n_raw = 0
    columns: List[str] = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for c in reader.fieldnames or []:
                if c not in columns:
                    columns.append(c)
            for row in reader:
                n_raw += 1
                t = _epoch(row.get("close_time", ""))
                if t == t:
                    by_sym[row.get("symbol") or row.get("symbol_id", "?")][t] = row
    data: Dict[str, Dict[str, list]] = {}
    for sym, rows in by_sym.items():
        times = sorted(rows)
        data[sym] = {"_t": times, "_rows": [rows[t] for t in times]}
    n = sum(len(d["_t"]) for d in data.values())
    return data, {"files": len(paths), "raw": n_raw, "rows": n, "dupes": n_raw - n, "columns": columns}


# ------------------------------------------------------------------ labels
class SymbolFrame:
    """Per-symbol arrays + forward labels."""

    def __init__(self, sym: str, block: Dict[str, list], horizons: Sequence[int], early: int) -> None:
        self.sym = sym
        rows = block["_rows"]
        self.t = np.asarray(block["_t"], dtype=float)
        self.rows = rows
        col = lambda name: np.array([_float(r.get(name, "")) for r in rows], dtype=float)
        self.close, self.high, self.low, self.atr = col("close"), col("high"), col("low"), col("atr")
        n = len(rows)
        steps = np.diff(self.t)
        self.period = float(np.median(steps)) if steps.size else 60.0
        self.gaps = int(np.sum(np.abs(steps - self.period) > 1e-6)) if steps.size else 0
        self.atr_ok = np.isfinite(self.atr) & (self.atr > 0)

        def contiguous(h: int) -> np.ndarray:
            ok = np.zeros(n, dtype=bool)
            if h < n:
                ok[: n - h] = np.abs((self.t[h:] - self.t[:-h]) - h * self.period) < 1e-6
            return ok

        self.fwd: Dict[int, np.ndarray] = {}
        self.drift: Dict[int, float] = {}
        for h in horizons:
            f = np.full(n, NAN)
            ok = contiguous(h) & self.atr_ok
            idx = np.flatnonzero(ok)
            f[idx] = (self.close[idx + h] - self.close[idx]) / self.atr[idx]
            self.fwd[h] = f
            self.drift[h] = float(np.nanmean(f)) if np.isfinite(f).any() else 0.0

        # early window highs / lows over rows i+1..i+N
        self.max_h = np.full(n, NAN)
        self.min_l = np.full(n, NAN)
        ok = contiguous(early) & self.atr_ok
        if early >= 1 and n > early:
            from numpy.lib.stride_tricks import sliding_window_view

            hw = sliding_window_view(self.high[1:], early)   # window k covers rows k+1..k+early
            lw = sliding_window_view(self.low[1:], early)
            m = hw.shape[0]
            self.max_h[:m] = hw.max(axis=1)
            self.min_l[:m] = lw.min(axis=1)
        self.max_h[~ok] = NAN
        self.min_l[~ok] = NAN

        # segment starts (first row, or first row after a gap) for onset detection
        self.seg_start = np.ones(n, dtype=bool)
        if n > 1:
            self.seg_start[1:] = np.abs(steps - self.period) > 1e-6

    def strings(self, name: str) -> np.ndarray:
        return np.array([r.get(name, "") for r in self.rows], dtype=object)

    def direction(self, name: str) -> np.ndarray:
        s = self.strings(name)
        return np.where(s == "BUY", 1, np.where(s == "SELL", -1, 0)).astype(int)

    def onsets(self, d: np.ndarray) -> np.ndarray:
        prev = np.r_[0, d[:-1]]
        return (d != 0) & ((d != prev) | self.seg_start)

    def excursions(self, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Signed MFE / MAE in ATR units for the early window."""
        up = (self.max_h - self.close) / self.atr
        dn = (self.min_l - self.close) / self.atr
        mfe = np.where(d > 0, up, np.where(d < 0, -dn, NAN))
        mae = np.where(d > 0, dn, np.where(d < 0, -up, NAN))
        return mfe, mae


# ------------------------------------------------------------------ metrics
class Pool:
    """Concatenates per-symbol samples for one (signal, view, side, split)."""

    def __init__(self, horizons: Sequence[int]) -> None:
        self.h = list(horizons)
        self.signed = {h: [] for h in horizons}
        self.adj = {h: [] for h in horizons}
        self.mfe: list = []
        self.mae: list = []

    def add(self, fr: SymbolFrame, mask: np.ndarray, d: np.ndarray, mfe: np.ndarray, mae: np.ndarray) -> None:
        if not mask.any():
            return
        dm = d[mask]
        for h in self.h:
            f = fr.fwd[h][mask]
            self.signed[h].append(dm * f)
            self.adj[h].append(dm * (f - fr.drift[h]))
        self.mfe.append(mfe[mask])
        self.mae.append(mae[mask])

    def metrics(self, adverse: float, tstat: bool) -> dict:
        cat = lambda xs: np.concatenate(xs) if xs else np.empty(0)
        out: dict = {"n": int(sum(len(x) for x in self.mfe))}
        for h in self.h:
            s, a = cat(self.signed[h]), cat(self.adj[h])
            s, a = s[np.isfinite(s)], a[np.isfinite(a)]
            out[f"fwd{h}"] = float(s.mean()) if s.size else NAN
            out[f"hit{h}"] = float((s > 0).mean()) if s.size else NAN
            out[f"edge{h}"] = float(a.mean()) if a.size else NAN
            if tstat and s.size > 1 and s.std(ddof=1) > 0:
                out[f"t{h}"] = float(s.mean() / (s.std(ddof=1) / math.sqrt(s.size)))
            else:
                out[f"t{h}"] = NAN
        mfe, mae = cat(self.mfe), cat(self.mae)
        mfe, mae = mfe[np.isfinite(mfe)], mae[np.isfinite(mae)]
        out["mfe"] = float(mfe.mean()) if mfe.size else NAN
        out["mae"] = float(mae.mean()) if mae.size else NAN
        out["wrong"] = float((mae <= -adverse).mean()) if mae.size else NAN
        return out


def evaluate(
    frames: Sequence[SymbolFrame],
    signals: Sequence[str],
    horizons: Sequence[int],
    adverse: float,
    gate: Optional[str],
    section: str = "signals",
) -> List[dict]:
    rows: List[dict] = []
    for name in signals:
        pools: Dict[tuple, Pool] = {}
        for fr in frames:
            d = fr.direction(name)
            on = fr.onsets(d)
            mfe, mae = fr.excursions(d)
            views = {"bar": d != 0, "onset": on}
            for view, base in views.items():
                for side, sm in (("both", d != 0), ("BUY", d > 0), ("SELL", d < 0)):
                    m = base & sm
                    if section == "gate" and gate:
                        if view != "onset":
                            continue
                        g = fr.strings(gate) == "NOTRADE"
                        for split, gm in ((f"{gate}=NOTRADE", g), (f"{gate}!=NOTRADE", ~g)):
                            pools.setdefault((view, side, split), Pool(horizons)).add(fr, m & gm, d, mfe, mae)
                    else:
                        pools.setdefault((view, side, ""), Pool(horizons)).add(fr, m, d, mfe, mae)
        for (view, side, split), pool in pools.items():
            r = {"section": section, "signal": name, "view": view, "side": side, "split": split}
            r.update(pool.metrics(adverse, tstat=(view == "onset")))
            rows.append(r)
    return rows


def flip_section(frames: Sequence[SymbolFrame], horizons: Sequence[int], adverse: float) -> List[dict]:
    """base_slope_sig_v2 onsets: tail flipped against history vs. not."""
    name = "base_slope_sig_v2"
    pools: Dict[str, Pool] = {}
    for fr in frames:
        if not fr.rows or "kin_v2_data_val1" not in fr.rows[0]:
            continue
        f = np.array([_float(r.get("kin_v2_data_val1", "")) for r in fr.rows])
        s = np.array([_float(r.get("kin_v2_data_val2", "")) for r in fr.rows])
        old = (21.0 * s - 5.0 * f) / 16.0
        d = fr.direction(name)
        on = fr.onsets(d)
        mfe, mae = fr.excursions(d)
        flipped = np.isfinite(old) & (old != 0) & (np.sign(f) != np.sign(old))
        ratio = np.where(old != 0, np.abs(f) / np.abs(np.where(old == 0, 1, old)), np.inf)
        splits = {
            "no flip": on & ~flipped,
            "flip (all)": on & flipped,
            "flip |f|/|old|<0.25": on & flipped & (ratio < 0.25),
            "flip 0.25-0.5": on & flipped & (ratio >= 0.25) & (ratio < 0.5),
            "flip >=0.5": on & flipped & (ratio >= 0.5),
        }
        for split, m in splits.items():
            pools.setdefault(split, Pool(horizons)).add(fr, m, d, mfe, mae)
    rows = []
    for split, pool in pools.items():
        r = {"section": "flips", "signal": name, "view": "onset", "side": "both", "split": split}
        r.update(pool.metrics(adverse, tstat=True))
        rows.append(r)
    return rows


# ------------------------------------------------------------------ output
def fmt(v: float, spec: str) -> str:
    return "nan" if v != v else format(v, spec)


def print_table(title: str, rows: List[dict], horizons: Sequence[int], early: int) -> None:
    if not rows:
        return
    print(f"\n=== {title} ===")
    head = f"{'signal':24s} {'view':5s} {'side':4s} {'split':26s} {'n':>6s}"
    for h in horizons:
        head += f" | {'fwd' + str(h):>7s} {'hit':>5s} {'edge':>7s} {'t':>5s}"
    head += f" | {'MFE' + str(early):>6s} {'MAE' + str(early):>6s} {'wrong':>6s}"
    print(head)
    print("-" * len(head))
    for r in rows:
        line = f"{r['signal']:24s} {r['view']:5s} {r['side']:4s} {r['split']:26s} {r['n']:6d}"
        for h in horizons:
            line += (f" | {fmt(r[f'fwd{h}'], '+7.3f')} {fmt(100 * r[f'hit{h}'], '5.1f')}"
                     f" {fmt(r[f'edge{h}'], '+7.3f')} {fmt(r[f't{h}'], '+5.1f')}")
        line += f" | {fmt(r['mfe'], '+6.2f')} {fmt(r['mae'], '+6.2f')} {fmt(100 * r['wrong'], '5.1f')}%"
        print(line)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Offline signal evaluation over MLRecorder feature CSVs")
    ap.add_argument("files", nargs="+", help="features_*.csv (globs allowed)")
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 15, 30])
    ap.add_argument("--early", type=int, default=5, help="early window (bars) for MFE / MAE")
    ap.add_argument("--adverse", type=float, default=0.5, help="wrong-early threshold, ATR units")
    ap.add_argument("--signals", nargs="+", default=None,
                    help="signal columns (default: kinetic v2, peak/trough, slope dist, out_Strategy_*, trade_sig)")
    ap.add_argument("--gate", default="slope_dist_sig", help="column whose NOTRADE splits onsets ('' = off)")
    ap.add_argument("--by-symbol", action="store_true")
    ap.add_argument("--out", default=None, help="write all table rows to this CSV")
    args = ap.parse_args(argv)

    paths = sorted({p for pat in args.files for p in (glob.glob(pat) or [pat])})
    data, info = load(paths)
    if not data:
        print("no rows loaded", file=sys.stderr)
        return 1
    cols = info["columns"]
    signals = args.signals or (
        [c for c in DEFAULT_SIGNALS if c in cols]
        + sorted(c for c in cols if c.startswith("out_Strategy_"))
        + (["trade_sig"] if "trade_sig" in cols else [])
    )
    missing = [s for s in signals if s not in cols]
    if missing:
        print(f"⚠️  columns not found, skipped: {missing}")
        signals = [s for s in signals if s in cols]
    gate = args.gate if args.gate and args.gate in cols else None

    frames = [SymbolFrame(sym, block, args.horizons, args.early) for sym, block in sorted(data.items())]
    t_all = np.concatenate([f.t for f in frames])
    print(f"files={info['files']} rows={info['rows']} (dupes dropped {info['dupes']}) "
          f"range={datetime.fromtimestamp(t_all.min(), timezone.utc):%Y-%m-%d %H:%M} → "
          f"{datetime.fromtimestamp(t_all.max(), timezone.utc):%Y-%m-%d %H:%M} UTC")
    for f in frames:
        drift = " ".join(f"drift{h}={f.drift[h]:+.3f}" for h in args.horizons)
        print(f"  {f.sym}: rows={len(f.t)} period={f.period:.0f}s gaps={f.gaps} {drift}")
    print(f"signals: {', '.join(signals)}; gate: {gate or 'off'}; "
          f"early={args.early} bars, wrong = MAE <= -{args.adverse} ATR")

    groups = [("ALL", frames)] + ([(f.sym, [f]) for f in frames] if args.by_symbol and len(frames) > 1 else [])
    report: List[dict] = []
    for label, frs in groups:
        sig_rows = evaluate(frs, signals, args.horizons, args.adverse, None)
        gate_rows = evaluate(frs, [s for s in signals if s != gate], args.horizons, args.adverse, gate, "gate") if gate else []
        flip_rows = flip_section(frs, args.horizons, args.adverse) if "base_slope_sig_v2" in signals else []
        few = sorted({r["signal"] for r in sig_rows if r["view"] == "onset" and r["side"] == "both" and r["n"] < MIN_ONSETS})
        # rows with no samples are dropped from both the tables and --out
        sig_rows, gate_rows, flip_rows = ([r for r in rows if r["n"]] for rows in (sig_rows, gate_rows, flip_rows))
        print_table(f"{label}: signals (fwd / edge / MFE / MAE in ATR units)", sig_rows, args.horizons, args.early)
        print_table(f"{label}: gate {gate} (onsets)", gate_rows, args.horizons, args.early)
        print_table(f"{label}: kinetic v2 tail flips (onsets)", flip_rows, args.horizons, args.early)
        for r in sig_rows + gate_rows + flip_rows:
            r["group"] = label
        report += sig_rows + gate_rows + flip_rows

        if few:
            print(f"⚠️  [{label}] fewer than {MIN_ONSETS} onsets (results are noise): {', '.join(few)}")

    if args.out:
        keys = ["group", "section", "signal", "view", "side", "split", "n"]
        keys += [f"{m}{h}" for h in args.horizons for m in ("fwd", "hit", "edge", "t")] + ["mfe", "mae", "wrong"]
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(report)
        print(f"\nwrote {len(report)} rows → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
