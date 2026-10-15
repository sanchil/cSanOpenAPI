"""
Series-level physics metrics (MarketMetrics-style building blocks).

First metric (V1-T24): determinant of the 2×2 sample covariance matrix of two
series over the previous ``n`` periods.

V1-T28: local extrema (``find_extrema`` NumPy / ``find_extrema_scipy``
find_peaks) — peak/trough building block for a future support/resistance
(higher-highs / lower-lows) signal. Used by SanSignals.peak_trough_sig (V1-T29).

V1-T33 (TASKS.txt TASK 10): MarketMetrics — pure ports of MQ4
``class MarketMetrics`` (ADX / volatility physics, slope geometry, retention,
hold scores, evidence fusion, fast-vs-slow pair scores). Not consumed by any
signal or strategy yet. MQ4 deviations and skipped members are listed in the
section header below.

Series convention
-----------------
Inputs are **oldest → newest** (IndData / project standard). The analysis
window is the last ``n`` samples. Order within the window does not affect
variance or covariance. Task text that indexes newest-first (a1 at t1) maps
to the same window numerically.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Union

import numpy as np

from csys.ctypes import SIG, DataPointStats

from capp.physics.math import covariance_matrix_2x2, matrix_determinant

ArrayLike = Union[Sequence[float], np.ndarray]


def covariance_matrix_determinant(
    series_a: ArrayLike,
    series_b: ArrayLike,
    n: int,
    *,
    ddof: int = 1,
) -> float:
    """
    Determinant of the 2×2 covariance matrix of ``series_a`` and ``series_b``
    over the last ``n`` paired samples.

        det([[var(A), cov(A,B)],
             [cov(A,B), var(B)]])

    Intended to be called once per completed bar (each period uses the previous
    ``n`` periods). Returns a single float (generalized variance).

    Parameters
    ----------
    series_a, series_b :
        Paired float series, oldest → newest.
    n :
        Window length (number of previous periods).
    ddof :
        Delta degrees of freedom for covariance (default 1 = sample).

    Returns
    -------
    float
        Determinant of the sample covariance matrix, or ``nan`` if the window
        is too short or contains non-finite values.
    """
    if n < 2 or ddof < 0 or n <= ddof:
        return float("nan")

    a = np.asarray(series_a, dtype=float)
    b = np.asarray(series_b, dtype=float)

    if a.ndim != 1 or b.ndim != 1:
        raise ValueError("series_a and series_b must be 1-D")
    if a.shape[0] != b.shape[0]:
        raise ValueError(
            f"series length mismatch: {a.shape[0]} != {b.shape[0]}"
        )
    if a.shape[0] < n:
        return float("nan")

    a_win = a[-n:]
    b_win = b[-n:]
    if not (np.isfinite(a_win).all() and np.isfinite(b_win).all()):
        return float("nan")

    cov = covariance_matrix_2x2(a_win, b_win, ddof=ddof)
    return matrix_determinant(cov)


# ---------------------------------------------------------------------------
# V1-T28: local extrema (peaks / troughs) — support/resistance building block
# ---------------------------------------------------------------------------
#
# Two interchangeable backends with the same return shape
# ``(maxima, minima)``, each a list of ``(index, value)`` sorted by index:
#
#   find_extrema        NumPy, strict ``order``-neighbour dominance
#   find_extrema_scipy  scipy.signal.find_peaks (prominence / distance / width)
#
# Index is into the input series (oldest → newest). Plateaus are reported at
# their leftmost bar in both backends. With ``pip_size`` the value is
# ``(price - series[0]) / pip_size``: pips relative to the window's first bar,
# so differences between extrema are pips, but absolute values shift as a
# rolling window slides — compare extrema within one call only.
#
# Live use: an extremum at index i is only confirmed once lower (higher) data
# exists to its right, so the newest bars can never be extrema (lag of
# ``order`` distinct values / >= 1 bar). Confirmed extrema do not repaint as
# bars are appended; with a prominence filter a recent peak may appear late
# (prominence only grows as the right side develops) but is never withdrawn.

ExtremaList = list[tuple[int, float]]


def _extrema_output(
    data: np.ndarray, idx: np.ndarray, pip_size: float | None
) -> ExtremaList:
    if pip_size is None:
        return [(int(i), float(data[i])) for i in idx]
    base = data[0]
    return [(int(i), float((data[i] - base) / pip_size)) for i in idx]


def _validate_series(series: ArrayLike, pip_size: float | None) -> np.ndarray:
    data = np.asarray(series, dtype=float)
    if data.ndim != 1:
        raise ValueError("expected 1D input")
    if pip_size is not None and not pip_size > 0:
        raise ValueError("pip_size must be > 0")
    return data


def find_extrema(
    series: ArrayLike,
    order: int = 1,
    pip_size: float | None = None,
) -> tuple[ExtremaList, ExtremaList]:
    """Local maxima/minima: a bar must be strictly higher (lower) than the
    ``order`` distinct-valued neighbours on each side. Flat plateaus are
    collapsed first and reported at their leftmost bar.

    ``order`` counts distinct values (runs), not bars, so across plateaus the
    effective bar window is wider than ``order``. A NaN never equals anything,
    so it is its own run and blocks any extremum within ``order`` of it.
    Unlike ``scipy.signal.argrelextrema`` (which misses plateau tops), a flat
    top/bottom is still found.
    """
    if order < 1:
        raise ValueError("order must be >= 1")
    data = _validate_series(series, pip_size)
    if data.size == 0:
        return [], []

    # Collapse runs of equal values; pos maps compressed -> original index
    keep = np.r_[True, data[1:] != data[:-1]]
    pos = np.flatnonzero(keep)
    d = data[pos]
    m = len(d)
    if m < 2 * order + 1:
        return [], []

    s, e = order, m - order
    c = d[s:e]
    is_max = np.ones(len(c), dtype=bool)
    is_min = np.ones(len(c), dtype=bool)
    for k in range(1, order + 1):
        left, right = d[s - k:e - k], d[s + k:e + k]
        is_max &= (c > left) & (c > right)
        is_min &= (c < left) & (c < right)

    max_idx = pos[np.flatnonzero(is_max) + s]
    min_idx = pos[np.flatnonzero(is_min) + s]
    return (
        _extrema_output(data, max_idx, pip_size),
        _extrema_output(data, min_idx, pip_size),
    )


def find_extrema_scipy(
    series: ArrayLike,
    *,
    prominence: float | None = None,
    distance: int | None = None,
    width: float | None = None,
    pip_size: float | None = None,
) -> tuple[ExtremaList, ExtremaList]:
    """Local maxima/minima via ``scipy.signal.find_peaks`` (minima on the
    negated series). Plateaus are reported at their leftmost bar to match
    ``find_extrema``.

    ``prominence`` and ``width`` filter noise: prominence is the drop from the
    peak to the higher of its two surrounding bases, in **pips** when
    ``pip_size`` is given (else price units); width is in bars at half
    prominence. ``distance`` is the minimum bar gap between kept peaks (the
    lower one is dropped). With no filters every strict local extremum
    (neighbour order 1) is returned. For the zone extents behind a peak, call
    ``scipy.signal.peak_prominences`` / ``peak_widths`` on the returned
    indices.

    SciPy is imported lazily so the live path does not depend on it.
    """
    from scipy.signal import find_peaks

    data = _validate_series(series, pip_size)
    if data.size < 3:
        return [], []
    if prominence is not None and pip_size is not None:
        prominence = prominence * pip_size

    kwargs = dict(prominence=prominence, distance=distance, width=width,
                  plateau_size=1)  # plateau_size=1 → left_edges reported

    def edges(x: np.ndarray) -> np.ndarray:
        _, props = find_peaks(x, **kwargs)
        return props["left_edges"]

    return (
        _extrema_output(data, edges(data), pip_size),
        _extrema_output(data, edges(-data), pip_size),
    )


# ===========================================================================
# V1-T33: MarketMetrics — atomic market truths (MQ4 class MarketMetrics)
# ===========================================================================
#
# Pure functions ported from MQ4 ``class MarketMetrics`` (code_base v98,
# ~L9889-12255). Not wired into any signal or strategy. Conventions:
#
#   * Series are oldest → newest; ``shift`` counts back from the newest
#     (0 = newest / forming, 1 = last closed bar — the live default).
#   * MQ4 globals are parameters: ``util.getPipValue(_Symbol)`` → ``pip_size``,
#     ``_Period`` (minutes) → ``period_seconds`` (IndData.period), ``Point`` →
#     ``point``. Calls to iATR / iADX / iStdDev become value arguments.
#   * camelCase aliases (``adxPotential`` …) are kept for MQ4 parity.
#
# Deviations from MQ4 (intentional, MQ4 behaviour noted):
#   * adxVector / layeredMomentumFilter called ``adxPotential(14, 1)``, which
#     binds adx=14 → constant 0.49. So MQ4 adxVector always used 0.49 and
#     layeredMomentumFilter's ``power < 0.75`` gate always returned 0. The
#     port uses the real ADX (``adx_vector`` main; ``adx`` argument).
#   * trendQuality called ``adxKinetic(50.0, period)`` → adx=50, scale=14 →
#     force 1.0, so MQ4 trend quality was the efficiency ratio alone.
#     ``trend_quality(adx=None)`` reproduces that; pass ``adx`` for the
#     intended force × smoothness.
#   * efficiencyRatio / trendQuality defaulted to shift 0 (forming bar);
#     here shift defaults to 1 (closed bar, project invariant).
#   * vWCM returned DBL_EPSILON with no volume; here 0.0.
#   * overallMarketForce / volatilityAnomaly took (symbol, tf, period) and
#     called iStdDev / iADX; here they take the std series / ADX values.
#   * isSqueezeReversal read iATR(21, shift 0); here ``atr_pips`` is passed.
#     Its SLOPE_BOUNDARY argument was unused in MQ4 and is dropped.
#   * universalProbScore's ``baselineScale`` argument was unused; dropped.
#   * slopesPhaseScore uses the closed form sin 2θ = 2fs/(f²+s²) instead of
#     atan2 + sin (same values).
#
# Not ported (redundant or blocked):
#   * getFeatureVector / marketIntensity / marketRegime /
#     getMarketActionCombinedScore — need RSI / MFI (V1-T13) and stored hold
#     scores; intensity/regime are ``np.linalg.norm`` of that vector (V1-T12).
#   * Stats::slopeRange_v2 / histogram_Magnitude / skewness / kurtosis_v3 are
#     replaced by ``slope_range`` / ``histogram_magnitude`` / scipy.stats.

ATR_BASE_REF_PIPS = 30.0    # atrKinetic: 30 pips on M15 = max energy (1.0)
ATR_BASE_PERIOD_MIN = 15.0  # atrKinetic anchor timeframe (minutes)

# -------------------------------------------------------------------------
# Shared helpers (not ranked)
# -------------------------------------------------------------------------
def _tail(series: ArrayLike, n: int, shift: int = 1) -> np.ndarray:
    """The ``n`` values ending ``shift`` bars before the newest (oldest →
    newest). Empty when the series is too short."""
    a = np.asarray(series, dtype=float)
    end = a.shape[0] - shift
    if n < 1 or shift < 0 or end - n < 0:
        return np.empty(0)
    return a[end - n:end]


def _at(series: ArrayLike, shift: int) -> float:
    """MQL ``series[shift]`` on an oldest → newest series."""
    a = np.asarray(series, dtype=float)
    return float(a[a.shape[0] - 1 - shift])


def _minutes(period_seconds: float) -> float:
    return float(period_seconds) / 60.0


def slope_range(
    series: ArrayLike,
    n: int = 15,
    slope_denom: int = 3,
    shift: int = 1,
    pip_size: float = 1.0,
) -> np.ndarray:
    """Stats::slopeRange_v2 — the last ``n`` rolling slopes
    ``(x[t] - x[t-slope_denom]) / slope_denom / pip_size``, oldest → newest
    (MQ4 fills newest-first). Empty when the series is too short."""
    if slope_denom < 1 or not pip_size > 0:
        return np.empty(0)
    w = _tail(series, n + slope_denom, shift)
    if w.size == 0:
        return w
    return (w[slope_denom:] - w[:-slope_denom]) / (slope_denom * pip_size)


def histogram_magnitude(values: ArrayLike, bins: int = 5, min_thresh: float = 0.2) -> int:
    """Stats::histogram_Magnitude — dominant bin of |values| over
    ``bins`` equal bins on [0, max(|values|, min_thresh)]. -1 if empty."""
    v = np.abs(np.asarray(values, dtype=float))
    if v.size == 0 or bins < 1:
        return -1
    top = max(float(v.max()), min_thresh)
    if top <= 0.0:
        return 0
    counts, _ = np.histogram(v, bins=bins, range=(0.0, top))
    return int(np.argmax(counts))


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))

# Fast vs. slow pair-score core (used by the ratio scores in tier 3).
#
# Two input families: slopes (signed, scale given by baseline_scale such as
# ATR) and probabilities (0..1, centred at 0.5; the prob variants are the
# slope variants on p - 0.5 with a fixed 0.025 indecision floor).
# MQ4 "safe zones": probPhase 0.70-0.95, uniProb 0.30-0.75, normDiff
# 0.25-0.70, heat 0.30-0.80, cosineProb 0.85-1.0, kineticAlign 0.40-0.85,
# logRatio 0.50-0.85.
def _lead_ratio(fast: float, slow: float, noise_floor: float) -> float | None:
    """|f| / max(|s|, floor) - 1, or None on conflict / both under floor."""
    if fast * slow < 0:
        return None
    af, as_ = abs(fast), abs(slow)
    if af < noise_floor and as_ < noise_floor:
        return None
    return af / max(as_, noise_floor) - 1.0


# ===========================================================================
# TIER 1 — useful and solid
# Scale-free or volatility-relative truths; first candidates for wiring.
# ===========================================================================

# -------------------------------------------------------------------------
# efficiency_ratio
#
#   Kaufman net move / path length. Well established, unitless, robust across
#   symbols and timeframes, no magic constants. Best single "trending vs.
#   choppy" truth in the set.
# -------------------------------------------------------------------------
def efficiency_ratio(price: ArrayLike, period: int = 14, shift: int = 1) -> float:
    """efficiencyRatio (Kaufman ER) — |net move| / sum |bar moves| over
    ``period`` bars: 0 (rough) .. 1 (smooth). NaN if too short."""
    w = _tail(price, period + 1, shift)
    if w.size == 0:
        return float("nan")
    path = float(np.abs(np.diff(w)).sum())
    return abs(float(w[-1] - w[0])) / path if path > 0 else 0.0

# -------------------------------------------------------------------------
# volatility_anomaly
#
#   Current std / its recent average. Simple, scale-free shock detector;
#   good as a "don't enter during a spike" filter.
# -------------------------------------------------------------------------
def volatility_anomaly(std_series: ArrayLike, period: int = 20, shift: int = 1) -> float:
    """volatilityAnomaly — std[shift] / mean of the last 2·period std values
    (1.0 = normal). NaN if too short; 1.0 if the mean is not positive."""
    buf = _tail(std_series, 2 * period, shift)
    if buf.size == 0 or not np.isfinite(buf).all():
        return float("nan")
    avg = float(buf.mean())
    return float(buf[-1]) / avg if avg > 0 else 1.0

# -------------------------------------------------------------------------
# atr_scale / regime_interpolate (on atr_kinetic)
#
#   Move a threshold between a quiet and an energetic value by volatility —
#   useful for making the fixed kinetic / peak-trough thresholds adaptive.
#   Caveat: atr_kinetic is calibrated to "30 pips on M15 = max energy". On M1
#   the ceiling is ~7.75 pips, so a typical 1-3 pip M1 ATR scores only
#   ~0.02-0.15 after squaring (low end squashed).
# -------------------------------------------------------------------------
def atr_kinetic(atr: float, pip_size: float, period_seconds: float) -> float:
    """atrKinetic — is the absolute movement large enough to trade? ATR in
    pips over a √time ceiling (30 pips on M15), clipped to 0..1, squared."""
    if not pip_size > 0:
        return 0.0
    ratio = _minutes(period_seconds) / ATR_BASE_PERIOD_MIN
    if ratio <= 0:
        ratio = 1.0
    ceiling = ATR_BASE_REF_PIPS * math.sqrt(ratio)
    norm = min(max(atr / pip_size / ceiling, 0.0), 1.0)
    return norm * norm


def atr_scale(
    atr: float,
    pip_size: float,
    period_seconds: float,
    min_val: float,
    max_val: float,
    curvature: float = 1.0,
) -> float:
    """atrScale — interpolate min_val → max_val by atr_kinetic^curvature
    (curvature > 1 punishes weak moves more)."""
    if atr <= 0 or min_val >= max_val:
        return min_val
    k = atr_kinetic(atr, pip_size, period_seconds)
    if curvature != 1.0:
        k = k ** curvature
    return min_val + (max_val - min_val) * min(max(k, 0.0), 1.0)


def regime_interpolate(
    atr: float, pip_size: float, period_seconds: float, quiet: float, energetic: float
) -> float:
    """regimeInterpolate — quiet + (energetic - quiet) · atr_kinetic."""
    return quiet + (energetic - quiet) * atr_kinetic(atr, pip_size, period_seconds)

# -------------------------------------------------------------------------
# dynamic_spread_limit
#
#   Practical execution guard: spread tolerance scaled by volatility and
#   timeframe. The app has no spread check today. Confirm units (points vs.
#   pips) before use.
# -------------------------------------------------------------------------
def dynamic_spread_limit(atr: float, pip_size: float, period_seconds: float) -> int:
    """getDynamicSpreadLimit — broker spread tolerance in points:
    15 + 45·atr_kinetic·(ln(minutes) + 1), capped at 120."""
    minutes = max(_minutes(period_seconds), 1e-9)
    dynamic = atr_kinetic(atr, pip_size, period_seconds) * 45.0 * (math.log(minutes) + 1.0)
    return min(int(max(15.0, 15.0 + dynamic)), 120)

# -------------------------------------------------------------------------
# trend_accel_strength
#
#   MA second difference / ATR, tanh-squashed. Clean and unitless. Close to
#   what kinetic v2 measures but over 1-bar steps: faster, noisier.
# -------------------------------------------------------------------------
def trend_accel_strength(sig: ArrayLike, atr: float, shift: int = 1, point: float = 1e-5) -> float:
    """trendAccelStrength — tanh(2nd difference / ATR), -1..+1 (same price
    units for sig and atr). NaN if too short."""
    w = _tail(sig, 3, shift)
    if w.size == 0:
        return float("nan")
    raw = (w[2] - w[1]) - (w[1] - w[0])
    return math.tanh(raw / max(atr, point))

# -------------------------------------------------------------------------
# fractal_alignment
#
#   Grades three MA slopes for agreement (1 / 0.5 pullback / 0.25 early
#   reversal / 0). Simple, readable, fits the "slow contains fast" premise.
#   Caveat: the 0.01 flat threshold is fixed pips/bar; should be ATR-relative.
# -------------------------------------------------------------------------
def fractal_alignment(fast: float, medium: float, slow: float, flat: float = 0.01) -> float:
    """fractalAlignment — harmony of three MA slopes: 1.0 all agree, 0.5
    medium+slow agree (pullback), 0.25 fast+medium agree against slow (early
    reversal), 0.0 chaos. |slope| <= flat counts as flat."""
    def d(x: float) -> int:
        return 1 if x > flat else (-1 if x < -flat else 0)

    df, dm, ds = d(fast), d(medium), d(slow)
    if df == dm == ds != 0:
        return 1.0
    if dm == ds != 0:
        return 0.5
    if df == dm != 0:
        return 0.25
    return 0.0


# ===========================================================================
# TIER 2 — situational
# Sound, but useful only in specific setups or with extra inputs.
# ===========================================================================

# -------------------------------------------------------------------------
# slopes_phase_score / probability_phase_score
#
#   2fs/(f²+s²): bounded -1..1 agreement that peaks at fast == slow and never
#   divides by a near-zero number. Better behaved than the ratio scores.
# -------------------------------------------------------------------------
def slopes_phase_score(fast: float, slow: float) -> float:
    """slopesPhaseScore — phase harmony: sin 2θ of θ = atan2(f, s) in the
    bullish quadrant, -sin 2θ in the bearish one, 0 on conflict. Peaks at
    ±1 when f = s. Closed form: sign(f) · 2fs / (f² + s²)."""
    if fast * slow <= 0:
        return 0.0
    v = 2.0 * fast * slow / (fast * fast + slow * slow)
    return v if fast > 0 else -v


def probability_phase_score(p_fast: float, p_slow: float) -> float:
    """probabilityPhaseScore — slopes_phase_score on p - 0.5; 0 when both
    are within 0.01 of 0.5."""
    vf, vs = p_fast - 0.5, p_slow - 0.5
    if abs(vf) < 0.01 and abs(vs) < 0.01:
        return 0.0
    return slopes_phase_score(vf, vs)

# -------------------------------------------------------------------------
# overall_market_force / adx_kinetic / adx_potential (+ adx_vector, trend_quality)
#
#   ADX-based trend-strength filters. They work, but ADX lags.
#   adx_vector and trend_quality (= efficiency_ratio × adx_kinetic) belong to
#   the same ADX family (not ranked separately).
# -------------------------------------------------------------------------
def overall_market_force(adx: float, plus_di: float, minus_di: float, vol_weight: float = 1.0) -> float:
    """overallMarketForce — tanh(0.1 · (+DI - -DI) · ADX/100 · vol_weight),
    -1..+1. ``vol_weight`` is std / mean std, i.e. volatility_anomaly(std,
    period) (MQ4 computed it inline)."""
    if not vol_weight == vol_weight:
        vol_weight = 1.0
    return math.tanh((plus_di - minus_di) * (adx / 100.0) * vol_weight * 0.1)


def adx_potential(adx: float) -> float:
    """adxPotential — "how charged is the market?" ADX/20, squared below 1
    (suppress noise), linear above. 1.0 = ADX 20."""
    raw = adx / 20.0
    return raw * raw if raw < 1.0 else raw


def adx_kinetic(adx: float, scale: float = 50.0) -> float:
    """adxKinetic — momentum intensity, min(ADX/scale, 1)², 0..1."""
    norm = min(adx / scale, 1.0)
    return norm * norm


def adx_vector(main: float, plus: float, minus: float) -> float:
    """adxVector — direction (+DI - -DI) weighted by potential and DI spread,
    tanh-squashed to -1 (bearish) .. +1 (bullish). Uses adx_potential(main)
    (MQ4 used a constant 0.49, see module notes)."""
    direction = plus - minus
    spread = max(abs(main - plus), abs(main - minus))
    return math.tanh(direction * adx_potential(main) * spread * 0.01)


def trend_quality(
    sig: ArrayLike, period: int = 14, adx: float | None = None, shift: int = 1
) -> float:
    """trendQuality — force × smoothness, 0..1: adx_kinetic(adx) ×
    efficiency_ratio. ``adx=None`` reproduces MQ4 (force 1.0)."""
    force = 1.0 if adx is None else adx_kinetic(adx)
    return force * efficiency_ratio(sig, period, shift)

# -------------------------------------------------------------------------
# vwcm / vwcm_raw
#
#   Volume-weighted candle body. Only as good as tick volume is as a proxy
#   for real volume in FX.
# -------------------------------------------------------------------------
def vwcm_raw(
    open_: ArrayLike, close: ArrayLike, volume: ArrayLike, pip_size: float, n: int = 10, shift: int = 1
) -> float:
    """vWCM_Raw — volume-weighted candle body in pips over ``n`` bars
    (unbounded). 0.0 with no volume or too few bars."""
    o, c, v = _tail(open_, n, shift), _tail(close, n, shift), _tail(volume, n, shift)
    if v.size == 0 or not pip_size > 0:
        return 0.0
    total = float(v.sum())
    if total <= 0:
        return 0.0
    return float((((c - o) / pip_size) * v).sum() / total)


def vwcm(
    open_: ArrayLike, close: ArrayLike, volume: ArrayLike, pip_size: float, n: int = 10, shift: int = 1
) -> float:
    """vWCM — "is volume supporting the move?" tanh(vwcm_raw / 10), -1..+1."""
    return math.tanh(vwcm_raw(open_, close, volume, pip_size, n, shift) / 10.0)

# -------------------------------------------------------------------------
# fuse_sig_log_odds / fuse_probability (+ analyse_history)
#
#   Principled log-odds fusion; worth having if signals are ever combined.
#   Single simple signals are preferred here, and the accuracy inputs must
#   be measured, not guessed. analyse_history builds fuse_probability inputs;
#   its accuracy is a bias-ratio proxy, not a real hit rate.
# -------------------------------------------------------------------------
def analyse_history(history: Sequence[SIG]) -> DataPointStats:
    """analyseHistory — turn a signal history into one fusion input.
    value = ±(1|2|3) by dominant side share (>= 0.60 → 2, >= 0.75 → 3),
    weight = 0.5 + 2.5·consistency (1 - flips/(directional - 1)),
    accuracy = 0.5 + 0.5·(bias - 0.5). Flips count changes between
    consecutive non-NOSIG entries (as MQ4). No BUY/SELL → (0, 0.1, 0.5)."""
    buys = sum(1 for s in history if s == SIG.BUY)
    sells = sum(1 for s in history if s == SIG.SELL)
    flips = sum(
        1 for a, b in zip(history, history[1:])
        if b != a and a != SIG.NOSIG and b != SIG.NOSIG
    )
    total = buys + sells
    if total == 0:
        return DataPointStats()
    bias = max(buys, sells) / total
    consistency = 1.0 - flips / (total - 1 + 1e-9)
    magnitude = 3 if bias >= 0.75 else (2 if bias >= 0.60 else 1)
    return DataPointStats(
        value=(-1 if sells > buys else 1) * magnitude,
        weight=0.5 + consistency * 2.5,
        accuracy=0.5 + (bias - 0.5) * 0.5,
    )


def fuse_sig_log_odds(
    signals: Sequence[SIG],
    weights: Sequence[float],
    accuracies: Sequence[float],
    prior: float = 0.5,
) -> float:
    """fuseSIG_LogOdds — P(up) from voting signals in log-odds space.
    BUY adds w·logit(acc), SELL subtracts it, CLOSE damps the running logit
    by max(0.01, 1 - min(0.25w, 0.99)), NOSIG / SIDEWAYS abstain. Weights
    are renormalised over contributors (sum = count); acc clamped to
    0.01..0.99. Caller owns the thresholds (e.g. >= 0.65 long)."""
    if prior <= 0.0 or prior >= 1.0:
        return prior
    abstain = (SIG.NOSIG, SIG.SIDEWAYS)
    wsum = sum(w for s, w in zip(signals, weights) if s not in abstain)
    norm = len(signals) / wsum if wsum > 0 else 1.0
    logit = _logit(prior)
    for s, w, acc in zip(signals, weights, accuracies):
        if s in abstain:
            continue
        w *= norm
        lo = _logit(min(max(acc, 0.01), 0.99))
        if s == SIG.BUY:
            logit += w * lo
        elif s == SIG.SELL:
            logit -= w * lo
        elif s == SIG.CLOSE:
            logit *= max(0.01, 1.0 - min(w * 0.25, 0.99))
    return _sigmoid(logit)


def fuse_probability(
    values: Sequence[int],
    weights: Sequence[float],
    accuracies: Sequence[float],
    prior: float = 0.5,
    max_value: int = 3,
) -> float:
    """fuseProbability — like fuse_sig_log_odds but each vote carries a
    magnitude: logit += (value/max_value) · w · logit(acc); 0 abstains.
    Feed it analyse_history outputs."""
    if prior <= 0.0 or prior >= 1.0:
        return prior
    wsum = sum(w for v, w in zip(values, weights) if v != 0)
    norm = len(values) / wsum if wsum > 0 else 1.0
    logit = _logit(prior)
    for v, w, acc in zip(values, weights, accuracies):
        if v == 0:
            continue
        logit += (v / max_value) * w * norm * _logit(min(max(acc, 0.01), 0.99))
    return _sigmoid(logit)

# -------------------------------------------------------------------------
# volatility_efficiency
#
#   Is a breakout expanding or fading? Reasonable, but needs std_open and
#   the open-price slope.
# -------------------------------------------------------------------------
def volatility_efficiency(
    std_open: float, std_close: float, slope_open: float, slope_close: float
) -> float:
    """volatilityEfficiency — "is the breakout expanding or fake?"
    (std_close/std_open - 1) + (|slope_close|/|slope_open| - 1); <= 0 → 0,
    else tanh(2·score) signed by slope_close. -1 (valid sell) .. +1."""
    structure = std_close / max(std_open, 0.00005)
    momentum = abs(slope_close) / max(abs(slope_open), 0.00005)
    raw = (structure - 1.0) + (momentum - 1.0)
    if raw <= 0:
        return 0.0
    return math.tanh(raw * (1.0 if slope_close >= 0 else -1.0) * 2.0)

# -------------------------------------------------------------------------
# is_trend_accelerating (not ranked separately)
#
#   Boolean sign test of velocity and acceleration; trend_accel_strength
#   (tier 1) is the graded version.
# -------------------------------------------------------------------------
def is_trend_accelerating(sig: ArrayLike, shift: int = 1) -> bool:
    """isTrendAccelerating — velocity and acceleration (last 3 bars) share
    a sign, i.e. the trend is gaining strength either way."""
    w = _tail(sig, 3, shift)
    if w.size == 0:
        return False
    v_now, v_prev = w[2] - w[1], w[1] - w[0]
    acc = v_now - v_prev
    return bool((v_now > 0 and acc > 0) or (v_now < 0 and acc < 0))


# ===========================================================================
# TIER 3 — low value as they stand
# Redundant, trivial, uncalibrated or not applicable to the current policy.
# ===========================================================================

# -------------------------------------------------------------------------
# f/s ratio forms
#
#   expansion_compression_ratio, kinematic_acceleration,
#   slope_acceleration_ratio (+ fmsr_norm), universal_*_score, market_*_heat,
#   log_ratio_score, normalized_diff: all forms of the f/s ratio, which TASK 4
#   showed is unstable when s is small. Kinetic v1/v2 covers this better.
# -------------------------------------------------------------------------
def expansion_compression_ratio(fast: float, slow: float, slope_floor: float = 0.3) -> float:
    """expansionCompressionRatio — |f|/|s| mapped linearly: <= 0.5 → 0
    (compression), 1 → 0.5, >= 2 → 1 (expansion). 0 on opposite signs or
    |slow| < slope_floor (tide too flat)."""
    if fast * slow <= 0:
        return 0.0
    abs_slow = abs(slow)
    if abs_slow < 0.00001 or abs_slow < slope_floor:
        return 0.0
    return min(max((abs(fast) / abs_slow - 0.5) / 1.5, 0.0), 1.0)


def kinematic_acceleration(fast: float, slow: float, slope_floor: float = 0.3) -> float:
    """kinematicAcceleration — |f|/|s| (> 1 expansion, < 1 compression);
    0 on opposite signs or |slow| < slope_floor."""
    if fast * slow <= 0 or abs(slow) < slope_floor:
        return 0.0
    return abs(fast) / abs(slow)


def slope_acceleration_ratio(fast: float, medium: float, slow: float) -> float:
    """slopeAccelerationRatio (fMSR) — |(f/m)/(m/s)| signed by fast,
    clamped to ±5. Near-zero medium/slow are floored at ±1e-6."""
    if abs(medium) < 0.000001:
        medium = 0.000001 * (1.0 if medium >= 0 else -1.0)
    if abs(slow) < 0.000001:
        slow = 0.000001 * (1.0 if slow >= 0 else -1.0)
    ratio = abs((fast / medium) / (medium / slow))
    if fast < 0:
        ratio = -ratio
    return max(-5.0, min(5.0, ratio))


def fmsr_norm(fmsr_raw: float, expansion: float = 0.40, compression: float = 0.15) -> float:
    """neuronHoldScore's bimodal fMSR activation: |fMSR| >= expansion or
    <= compression → 1.0 (expansion / squeeze both tradeable), else 0.0."""
    a = abs(fmsr_raw)
    return 1.0 if a >= expansion or a <= compression else 0.0


def universal_slopes_score(fast: float, slow: float, baseline_scale: float, sensitivity: float = 2.0) -> float:
    """universalSlopesScore — "is the fast slope leading the slow one?"
    tanh(sensitivity · max(0, |f|/|s| - 1)) signed by fast, -1..+1. Floor
    5% of baseline_scale; 0 on conflict or when both are under it."""
    r = _lead_ratio(fast, slow, baseline_scale * 0.05)
    if r is None:
        return 0.0
    return math.tanh(max(0.0, r) * sensitivity) * (1.0 if fast >= 0 else -1.0)


def universal_prob_score(fast: float, slow: float, sensitivity: float = 2.0) -> float:
    """universalProbScore — universal_slopes_score on p - 0.5 (floor 0.025)."""
    return universal_slopes_score(fast - 0.5, slow - 0.5, 0.5, sensitivity)


def market_slopes_heat(fast: float, slow: float, baseline_scale: float, sensitivity: float = 2.0) -> float:
    """marketSlopesHeat — unsigned tanh(sensitivity · (|f|/|s| - 1)):
    > 0.85 redline (exhaustion), < 0 cooling. 0 on conflict / dormant."""
    r = _lead_ratio(fast, slow, baseline_scale * 0.05)
    return 0.0 if r is None else math.tanh(r * sensitivity)


def market_prob_heat(fast: float, slow: float, sensitivity: float = 2.0) -> float:
    """marketProbHeat — market_slopes_heat on p - 0.5 (floor 0.025)."""
    return market_slopes_heat(fast - 0.5, slow - 0.5, 0.5, sensitivity)


def log_ratio_score(fast: float, slow: float) -> float:
    """logRatioScore — tanh(ln(f/s)) for positive inputs, else 0."""
    if fast <= 0 or slow <= 0:
        return 0.0
    return math.tanh(math.log(fast / slow))


def normalized_diff(fast: float, slow: float) -> float:
    """normalizedDiff — (f - s) / (f + s); 0 when f + s == 0."""
    total = fast + slow
    return 0.0 if total == 0.0 else (fast - slow) / total

# -------------------------------------------------------------------------
# cosine_similarity / cosine_prob_similarity (+ kinetic_alignment, arc_tan_score)
#
#   For single numbers these reduce to sign(f·s). Trivial.
#   kinetic_alignment / kinetic_prob_alignment are that sign × a magnitude
#   squash; arc_tan_score is the raw phase angle (not ranked separately).
# -------------------------------------------------------------------------
def cosine_similarity(fast: float, slow: float) -> float:
    """cosineSimilarity — point-in-time cosine of two scalars, which is
    sign(f·s): +1 same direction, -1 opposed, 0 if either is ~0."""
    if abs(fast) * abs(slow) < 1e-9:
        return 0.0
    return 1.0 if fast * slow > 0 else -1.0


def cosine_prob_similarity(fast: float, slow: float) -> float:
    """cosineProbSimilarity — cosine_similarity on p - 0.5."""
    return cosine_similarity(fast - 0.5, slow - 0.5)


def kinetic_alignment(fast: float, slow: float, baseline_scale: float) -> float:
    """kineticAlignment — cosine_similarity × tanh(‖(f, s)‖ / 2·baseline);
    0 when the combined magnitude is under 5% of baseline."""
    mag = math.hypot(fast, slow)
    if mag < baseline_scale * 0.05:
        return 0.0
    return cosine_similarity(fast, slow) * math.tanh(mag / (baseline_scale * 2.0))


def kinetic_prob_alignment(fast: float, slow: float) -> float:
    """kineticProbAlignment — cosine_prob_similarity × tanh(‖v‖ / 0.5) on
    v = (p - 0.5); 0 when ‖v‖ < 0.025."""
    vf, vs = fast - 0.5, slow - 0.5
    mag = math.hypot(vf, vs)
    if mag < 0.025:
        return 0.0
    return cosine_similarity(vf, vs) * math.tanh(mag / 0.5)


def arc_tan_score(fast: float, slow: float) -> float:
    """arcTanScore — atan2(fast, slow) in degrees (-180..180)."""
    return math.degrees(math.atan2(fast, slow))

# -------------------------------------------------------------------------
# hold scores
#
#   bayesian_hold_score, neuron_hold_score, hyperbolic / cobb_douglas
#   combined scores (+ bayesian_next_bar_prob): stacks of hand-tuned
#   constants. Likelihood ratios and weights were never estimated from data;
#   don't trust them until calibrated.
# -------------------------------------------------------------------------
def bayesian_next_bar_prob(vol_high: bool, accel_strong: bool) -> float:
    """bayesianNextBarProb — fixed table: both 0.84, vol 0.58, accel 0.64,
    neither 0.40."""
    if vol_high and accel_strong:
        return 0.84
    if vol_high:
        return 0.58
    if accel_strong:
        return 0.64
    return 0.40


def bayesian_hold_score(
    ma: ArrayLike,
    close: ArrayLike,
    open_: ArrayLike,
    volume: ArrayLike,
    bars_held: int,
    atr: float,
    pip_size: float,
    *,
    anomaly: float = 1.0,
    market_force: float | None = None,
    adx: float | None = None,
    point: float = 1e-5,
    shift: int = 1,
) -> float:
    """bayesianHoldScore — P(hold) from likelihood ratios, 0..1.

    Evidence: trend_quality (> 0.20 ×2.0, > 0.12 ×1.2, < 0.08 ×0.4, else
    ×0.9), |trend_accel_strength| > 0.10 (×1.4 / ×0.8), |vwcm| > 0.15
    (×1.2 / ×0.9), anomaly > 1.5 ×0.7, market_force > 0.35 ×1.2 / < -0.35
    ×0.8 (skipped when None = MQ4 useOverallForce=false), time decay
    max(0.2, 1 - 0.02·bars). P = lr / (1 + lr).
    ``anomaly`` = volatility_anomaly(std, 20); ``market_force`` =
    overall_market_force(...); ``atr`` in price units.
    """
    tq = trend_quality(ma, adx=adx, shift=shift)
    accel_strong = abs(trend_accel_strength(ma, atr, shift, point)) > 0.10
    vol_high = abs(vwcm(open_, close, volume, pip_size, 10, shift)) > 0.15

    lr = 1.0
    if tq > 0.20:
        lr *= 2.0
    elif tq > 0.12:
        lr *= 1.2
    elif tq < 0.08:
        lr *= 0.4
    else:
        lr *= 0.9
    lr *= 1.4 if accel_strong else 0.8
    lr *= 1.2 if vol_high else 0.9
    lr *= 0.7 if anomaly > 1.5 else 1.0
    if market_force is not None:
        lr *= 1.2 if market_force > 0.35 else (0.8 if market_force < -0.35 else 1.0)
    lr *= max(0.2, 1.0 - bars_held * 0.02)
    return lr / (1.0 + lr)


def neuron_hold_score(
    ma: ArrayLike,
    close: ArrayLike,
    open_: ArrayLike,
    volume: ArrayLike,
    bars_held: int,
    atr: float,
    pip_size: float,
    period_seconds: float,
    *,
    anomaly: float = 1.0,
    market_force: float | None = None,
    adx: float | None = None,
    point: float = 1e-5,
    shift: int = 1,
) -> float:
    """neuronHoldScore — weighted sum of hold atoms through a sigmoid, 0..1.

    score = 0.40·tq + 0.25·bayes_next_bar + 0.12·(retention_v2 - 0.7)/0.7
          + 0.10·anomaly_factor + 0.08·(regime_bias - 0.9) + 0.15·fmsr_norm
          + 0.10·|vwcm|;  out = 1 / (1 + e^(-10·(score - 0.5)))
    accel strong = |trend_accel_strength| > 0.18; fMSR from ma slopes over
    3 / 10 / 30 bars at ``shift``; regime bias 1.15 / 0.75 / 1.0 on
    market_force (1.0 when None). NaN if ``ma`` is too short.
    """
    if len(ma) < shift + 31 or not pip_size > 0:
        return float("nan")
    tq = trend_quality(ma, adx=adx, shift=shift)
    accel_strong = abs(trend_accel_strength(ma, atr, shift, point)) > 0.18
    vol_raw = vwcm(open_, close, volume, pip_size, 10, shift)
    bayes_p = bayesian_next_bar_prob(abs(vol_raw) > 0.15, accel_strong)
    retention = hybrid_retention_v2(bars_held, atr, pip_size, period_seconds, tq)
    anomaly_factor = max(0.82, 1.0 - (anomaly - 1.0) * 0.22) if anomaly > 1.0 else 1.0
    regime_bias = 1.0
    if market_force is not None:
        regime_bias = 1.15 if market_force > 0.35 else (0.75 if market_force < -0.35 else 1.0)

    m0 = _at(ma, shift)
    fast = (m0 - _at(ma, shift + 3)) / (3 * pip_size)
    med = (m0 - _at(ma, shift + 10)) / (10 * pip_size)
    slow = (m0 - _at(ma, shift + 30)) / (30 * pip_size)
    fmsr = fmsr_norm(slope_acceleration_ratio(fast, med, slow))

    score = (tq * 0.40 + bayes_p * 0.25 + (retention - 0.7) / 0.7 * 0.12
             + anomaly_factor * 0.10 + (regime_bias - 0.9) * 0.08
             + fmsr * 0.15 + abs(vol_raw) * 0.10)
    return 1.0 / (1.0 + math.exp(-10.0 * (score - 0.5)))


_FMSR_EXPANSION = 0.25
_FMSR_COMPRESSION = 0.18


def hyperbolic_combined_score(b: float, n: float, f: float, fra: float) -> int:
    """getHyperbolicCombinedScore — +1 authorize / -1 veto / 0 from bayesian
    (b), neuron (n), fMSR (f), fractal alignment (fra).
    |f| >= 0.25 (or fuzzy 0.18..0.25 at half weight):
        tanh(1.5b + 1.2n + fra - 1.6) vs ±0.12;
    |f| <= 0.18 (squeeze): tanh(1.8b + 1.5n - 1.8) vs ±0.28."""
    a = abs(f)
    if a > _FMSR_COMPRESSION:
        mult = 0.5 if a < _FMSR_EXPANSION else 1.0
        score = math.tanh(b * 1.5 + n * 1.2 + fra - 1.6) * mult
        return 1 if score > 0.12 else (-1 if score < -0.12 else 0)
    score = math.tanh(b * 1.8 + n * 1.5 - 1.8)
    return 1 if score > 0.28 else (-1 if score < -0.28 else 0)


def cobb_douglas_combined_score(b: float, n: float, f: float, fra: float) -> int:
    """getCobbDouglasCombinedScore — multiplicative variant of the above.
    Trend: (n+.01)^1.2 · (b+.01)^1.5 · (fra+.01) · mult vs 0.11 / 0.035;
    squeeze (|f| <= 0.18): (n+.01)^1.2 · (b+.01)^1.5 vs 0.32 / 0.14."""
    a = abs(f)
    core = max(n + 0.01, 0.01) ** 1.2 * max(b + 0.01, 0.01) ** 1.5
    if a > _FMSR_COMPRESSION:
        conf = core * (fra + 0.01) * (0.5 if a < _FMSR_EXPANSION else 1.0)
        return 1 if conf > 0.11 else (-1 if conf < 0.035 else 0)
    return 1 if core > 0.32 else (-1 if core < 0.14 else 0)

# -------------------------------------------------------------------------
# layered_momentum_filter
#
#   Five stacked gates. Because of the MQ4 ADX bug it always returned 0
#   there, so it has never actually run.
# -------------------------------------------------------------------------
def layered_momentum_filter(
    values: ArrayLike,
    adx: float,
    pip_size: float = 1.0,
    n: int = 20,
    slope_denom: int = 3,
    shift: int = 1,
) -> float:
    """layeredMomentumFilter — +1 / -1 when the last ``n`` slopes pass all
    gates, else 0:
      >= 80% of slopes one sign; adx_potential(adx) >= 0.75 (ADX >= ~17.3);
      dominant |slope| histogram bin >= 2 of 5; |skew| <= 0.5; excess
      kurtosis <= 2 (no news spikes). See module notes on the ADX gate."""
    slopes = slope_range(values, n, slope_denom, shift, pip_size)
    if slopes.size < n:
        return 0.0
    buy, sell = int((slopes > 0).sum()), int((slopes < 0).sum())
    if buy >= 0.8 * slopes.size:
        direction = 1.0
    elif sell >= 0.8 * slopes.size:
        direction = -1.0
    else:
        return 0.0
    if adx_potential(adx) < 0.75:
        return 0.0
    if histogram_magnitude(slopes, 5, 0.2) < 2:
        return 0.0
    if float(np.var(slopes)) >= 1e-16:
        from scipy.stats import kurtosis, skew

        if abs(float(skew(slopes))) > 0.5 or float(kurtosis(slopes)) > 2.0:
            return 0.0
    return direction

# -------------------------------------------------------------------------
# retention functions
#
#   Only matter with time- or volatility-decay exits. The current policy
#   has no SL/TP or trailing exits, so they don't apply yet.
# -------------------------------------------------------------------------
def linear_time_retention(bars_held: int, decay_rate: float = 0.05, floor: float = 0.60) -> float:
    """getLinearTimeRetention — max(1 - bars·rate, floor)."""
    return max(1.0 - bars_held * decay_rate, floor)


def vol_adaptive_retention(atr: float, pip_size: float, period_seconds: float) -> float:
    """getVolAdaptiveRetention — max(0.98 - 0.16·√atr_kinetic, 0.70):
    loosens quickly once volatility starts."""
    return max(0.98 - 0.16 * math.sqrt(atr_kinetic(atr, pip_size, period_seconds)), 0.70)


def hybrid_retention(bars_held: int, atr: float, pip_size: float, period_seconds: float) -> float:
    """getHybridRetention — time (2%/bar, floor 0.80) × volatility retention."""
    return linear_time_retention(bars_held, 0.02, 0.80) * vol_adaptive_retention(atr, pip_size, period_seconds)


def hybrid_retention_v2(
    bars_held: int, atr: float, pip_size: float, period_seconds: float, trend_quality_score: float = 0.0
) -> float:
    """getHybridRetention_v2 — time (1.5%/bar, floor 0.75) × volatility
    (0.98 - 0.12·√k, floor 0.72) × trend bonus (1 + 0.35·trend quality)."""
    time_ret = max(1.0 - bars_held * 0.015, 0.75)
    vol_ret = max(0.98 - 0.12 * math.sqrt(atr_kinetic(atr, pip_size, period_seconds)), 0.72)
    return time_ret * vol_ret * (1.0 + trend_quality_score * 0.35)

# -------------------------------------------------------------------------
# geometric_fan_score
#
#   Compares slopes with atr × 0.3, which mixes units unless the caller
#   converts first.
# -------------------------------------------------------------------------
def geometric_fan_score(fast: float, medium: float, slow: float, atr: float) -> float:
    """geometricFanScore — 1.0 perfect fan (|f| >= |m| >= |s|, all same
    sign), 0.5 near-fan (ratios >= 0.8), 0.0 otherwise or |slow| < 0.3·atr.
    Slopes and atr must share units."""
    abs_slow = abs(slow)
    if abs_slow < atr * 0.30:
        return 0.0
    if not ((fast > 0 and medium > 0 and slow > 0) or (fast < 0 and medium < 0 and slow < 0)):
        return 0.0
    r1, r2 = abs(fast) / abs(medium), abs(medium) / abs_slow
    if r1 >= 1.0 and r2 >= 1.0:
        return 1.0
    if r1 >= 0.8 and r2 >= 0.8:
        return 0.5
    return 0.0

# -------------------------------------------------------------------------
# is_squeeze_reversal / atr_kinetic_norm
#
#   Niche. On M1, atr_kinetic_norm uses the full 30-pip ceiling, so it
#   reads near zero almost all the time.
# -------------------------------------------------------------------------
def is_squeeze_reversal(
    fmsr: float, base_slope: float, sig: SIG, atr_pips: float, squeeze_boundary: float = 0.4
) -> bool:
    """isSqueezeReversal — inside a squeeze (|fmsr| <= boundary) a signal
    against the macro direction (base_slope beyond ±5% of ATR pips) is a
    mean-reversion entry: BUY against a down-tide or SELL against an up-tide."""
    if abs(fmsr) > squeeze_boundary:
        return False
    th = atr_pips * 0.05
    direction = 1 if base_slope > th else (-1 if base_slope < -th else 0)
    return (direction < 0 and sig == SIG.BUY) or (direction > 0 and sig == SIG.SELL)


def atr_kinetic_norm(atr: float, pip_size: float, period_seconds: float) -> float:
    """atrKineticNorm — linear share of the timeframe's max energy, 0..1.
    Unlike atr_kinetic the time ratio is floored at 1 (M1 uses the M15
    ceiling) and there is no squaring."""
    if not pip_size > 0:
        return 0.0
    ceiling = ATR_BASE_REF_PIPS * math.sqrt(max(_minutes(period_seconds) / ATR_BASE_PERIOD_MIN, 1.0))
    return min(max(atr / pip_size / ceiling, 0.0), 1.0)


# MQ4 camelCase aliases
slopeRange = slope_range
histogramMagnitude = histogram_magnitude
adxPotential = adx_potential
adxKinetic = adx_kinetic
adxVector = adx_vector
atrKinetic = atr_kinetic
atrKineticNorm = atr_kinetic_norm
atrScale = atr_scale
regimeInterpolate = regime_interpolate
volatilityEfficiency = volatility_efficiency
volatilityAnomaly = volatility_anomaly
efficiencyRatio = efficiency_ratio
vWCM = vwcm
vWCM_Raw = vwcm_raw
trendQuality = trend_quality
isTrendAccelerating = is_trend_accelerating
trendAccelStrength = trend_accel_strength
expansionCompressionRatio = expansion_compression_ratio
slopeAccelerationRatio = slope_acceleration_ratio
geometricFanScore = geometric_fan_score
fractalAlignment = fractal_alignment
kinematicAcceleration = kinematic_acceleration
overallMarketForce = overall_market_force
layeredMomentumFilter = layered_momentum_filter
getLinearTimeRetention = linear_time_retention
getVolAdaptiveRetention = vol_adaptive_retention
getHybridRetention = hybrid_retention
getHybridRetention_v2 = hybrid_retention_v2
getDynamicSpreadLimit = dynamic_spread_limit
bayesianNextBarProb = bayesian_next_bar_prob
bayesianHoldScore = bayesian_hold_score
neuronHoldScore = neuron_hold_score
getHyperbolicCombinedScore = hyperbolic_combined_score
getCobbDouglasCombinedScore = cobb_douglas_combined_score
analyseHistory = analyse_history
fuseSIG_LogOdds = fuse_sig_log_odds
fuseProbability = fuse_probability
universalSlopesScore = universal_slopes_score
universalProbScore = universal_prob_score
marketSlopesHeat = market_slopes_heat
marketProbHeat = market_prob_heat
arcTanScore = arc_tan_score
slopesPhaseScore = slopes_phase_score
probabilityPhaseScore = probability_phase_score
normalizedDiff = normalized_diff
logRatioScore = log_ratio_score
cosineSimilarity = cosine_similarity
kineticAlignment = kinetic_alignment
cosineProbSimilarity = cosine_prob_similarity
kineticProbAlignment = kinetic_prob_alignment
isSqueezeReversal = is_squeeze_reversal
