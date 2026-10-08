"""Breakout score: transparent 0-100 composite of momentum, engagement, headroom, activity, signability.

Every component is 0..1 and returned alongside the total so the reasoning is auditable.
Components with no data are dropped and the remaining weights renormalised.
"""

from __future__ import annotations

import math
from datetime import date, datetime

WEIGHTS = {
    "momentum": 0.40,     # growth rate across platforms (needs >=2 snapshots)
    "engagement": 0.20,   # depth of fandom: plays per listener, views per subscriber
    "headroom": 0.20,     # sweet spot: real traction but not already big
    "activity": 0.10,     # recent and frequent releases
    "signability": 0.10,  # DIY/distributor releases suggest unsigned
}

# Metric -> weight inside the momentum component.
MOMENTUM_METRICS = {
    "deezer.fans": 1.0,
    "lastfm.listeners": 1.0,
    "youtube.subscribers": 1.0,
    "lastfm.playcount": 0.7,
    "youtube.views": 0.7,
    "spotify.followers": 1.0,
    "wikipedia.pageviews_30d": 0.3,
}

GROWTH_FOR_FULL_SCORE = 0.30  # +30% per month = momentum 1.0


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def growth(points: list[tuple[str, float]], window_days: int = 30) -> dict | None:
    """Growth for one metric. points = [(iso_ts, value)] sorted ascending.

    Uses the latest point and the oldest point within the window (falls back to the closest older
    point). Returns absolute delta, % change, and change normalised to a 30-day rate.
    """
    pts = [(_parse(t), v) for t, v in points if v is not None]
    if len(pts) < 2:
        return None
    end_t, end_v = pts[-1]
    in_window = [p for p in pts[:-1] if (end_t - p[0]).total_seconds() <= window_days * 86400]
    start_t, start_v = in_window[0] if in_window else pts[-2]
    days = (end_t - start_t).total_seconds() / 86400
    if days < 0.5:
        return None
    delta = end_v - start_v
    pct = delta / start_v if start_v > 0 else None
    monthly = None
    if start_v > 0 and end_v > 0:
        monthly = math.exp(math.log(end_v / start_v) / days * 30) - 1
    return {"start": start_v, "end": end_v, "delta": delta, "pct": pct,
            "monthly_rate": monthly, "days": round(days, 1)}


# Per-platform audience bands (low_zero, full_from, full_to, fade_to_zero). Deezer fan counts run
# ~10-20x smaller than Last.fm listeners / YouTube subs for most Western acts, so they get their own scale.
HEADROOM_BANDS = {
    "deezer.fans": (50, 500, 25_000, 500_000),
    "lastfm.listeners": (200, 2_000, 100_000, 2_000_000),
    "youtube.subscribers": (200, 2_000, 100_000, 2_000_000),
    "spotify.followers": (200, 2_000, 100_000, 2_000_000),
}


def _band(value: float, band: tuple[float, float, float, float]) -> float:
    lo, full_from, full_to, hi = (math.log10(b) for b in band)
    if value <= 0:
        return 0.0
    x = math.log10(value)
    if x < lo:
        return 0.0
    if x < full_from:
        return (x - lo) / (full_from - lo)
    if x <= full_to:
        return 1.0
    return _clamp(1 - (x - full_to) / (hi - full_to))


def headroom_score(metrics: dict[str, float]) -> float | None:
    """Sweet spot = real traction but not big yet. Takes the most conservative platform, so being
    large anywhere counts as large."""
    scores = [_band(metrics[m], b) for m, b in HEADROOM_BANDS.items() if metrics.get(m) is not None]
    return min(scores) if scores else None


def activity_score(latest_release: str | None, releases_last_year: int | None) -> float | None:
    if not latest_release:
        return None
    try:
        age = (date.today() - date.fromisoformat(latest_release[:10])).days
    except ValueError:
        return None
    recency = 1.0 if age <= 30 else 0.7 if age <= 90 else 0.4 if age <= 180 else 0.2 if age <= 365 else 0.0
    cadence = _clamp((releases_last_year or 0) / 6)  # ~6 releases/yr is a strong cadence
    return 0.7 * recency + 0.3 * cadence


def score(metrics: dict[str, float], history: dict[str, list[tuple[str, float]]],
          latest_release: str | None = None, releases_last_year: int | None = None,
          diy_label: bool | None = None, window_days: int = 30) -> dict:
    components: dict[str, float | None] = {}
    flags: list[str] = []
    growth_detail: dict[str, dict] = {}

    # Momentum
    weighted, total_w = 0.0, 0.0
    for metric, w in MOMENTUM_METRICS.items():
        g = growth(history.get(metric, []), window_days)
        if g is None or g["monthly_rate"] is None:
            continue
        growth_detail[metric] = g
        weighted += w * _clamp(g["monthly_rate"] / GROWTH_FOR_FULL_SCORE)
        total_w += w
    components["momentum"] = weighted / total_w if total_w else None
    if not total_w:
        flags.append("no growth history yet: run snapshots over several days")

    # Engagement
    eng = []
    listeners, plays = metrics.get("lastfm.listeners"), metrics.get("lastfm.playcount")
    if listeners and plays and listeners >= 50:
        ratio = plays / listeners
        eng.append(_clamp((ratio - 5) / 35))
        if ratio > 30:
            flags.append(f"deep fandom: {ratio:.0f} Last.fm plays per listener")
    subs, views = metrics.get("youtube.subscribers"), metrics.get("youtube.views")
    if subs and views and subs >= 100:
        eng.append(_clamp(math.log10(max(views / subs, 1)) / 3))  # 1000 views/sub = 1.0
    components["engagement"] = sum(eng) / len(eng) if eng else None

    components["headroom"] = headroom_score(metrics)
    if (metrics.get("deezer.fans") or 0) > 250_000 or any(
            (metrics.get(m) or 0) > 1_000_000 for m in ("lastfm.listeners", "youtube.subscribers")):
        flags.append("already established")

    components["activity"] = activity_score(latest_release, releases_last_year)

    if diy_label is None:
        components["signability"] = None
    else:
        components["signability"] = 1.0 if diy_label else 0.2
        if diy_label:
            flags.append("latest release via DIY distributor (likely unsigned)")

    usable = {k: v for k, v in components.items() if v is not None}
    w_sum = sum(WEIGHTS[k] for k in usable)
    total = 100 * sum(WEIGHTS[k] * v for k, v in usable.items()) / w_sum if w_sum else 0.0

    return {
        "score": round(total, 1),
        "components": {k: (round(v, 3) if v is not None else None) for k, v in components.items()},
        "growth": growth_detail,
        "flags": flags,
        "confidence": round(w_sum, 2),  # share of the model that had data
    }
