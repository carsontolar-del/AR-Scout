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
    "signability": 0.10,  # self-released or DIY: likely unsigned; a major: already taken
}

# Metric -> weight inside the momentum component.
MOMENTUM_METRICS = {
    "deezer.fans": 1.0,
    "lastfm.listeners": 1.0,
    "youtube.subscribers": 1.0,
    "lastfm.playcount": 0.7,
    "youtube.views": 0.7,
    "instagram.followers": 1.0,
    "spotify.followers": 1.0,
    "wikipedia.pageviews_30d": 0.3,
}

SIGNABILITY = {"self": 1.0, "diy": 1.0, "indie": 0.4, "major": 0.0}
LABEL_FLAGS = {
    "diy": "latest release via DIY distributor (likely unsigned)",
    "self": "self-released (likely unsigned)",
    "major": "on a major label or imprint",
}

GROWTH_FOR_FULL_SCORE = 0.30  # +30% per month = momentum 1.0
# Shortest span projected to a monthly rate. Over a day, ordinary noise (+40 subs, a Wikipedia blip, API
# rounding) compounds to hundreds of percent a month and swamps the score.
MIN_GROWTH_DAYS = 7


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def growth(points: list[tuple[str, float]], window_days: int = 30) -> dict | None:
    """Growth for one metric. points = [(iso_ts, value)] sorted ascending.

    Uses the latest point and the oldest point within the window (falls back to the closest older
    point). Returns absolute delta, % change, and change normalised to a 30-day rate; the rate is None
    until the points span MIN_GROWTH_DAYS.
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
    if start_v > 0 and end_v > 0 and days >= MIN_GROWTH_DAYS:
        monthly = math.exp(math.log(end_v / start_v) / days * 30) - 1
    return {"start": start_v, "end": end_v, "delta": delta, "pct": pct,
            "monthly_rate": monthly, "days": round(days, 1)}


# Per-platform audience bands (low_zero, full_from, full_to, fade_to_zero). Deezer fan counts run
# ~10-20x smaller than Last.fm listeners / YouTube subs for most Western acts, so they get their own scale.
HEADROOM_BANDS = {
    "deezer.fans": (50, 500, 25_000, 500_000),
    "lastfm.listeners": (200, 2_000, 100_000, 1_000_000),
    "youtube.subscribers": (200, 2_000, 100_000, 1_000_000),
    "spotify.followers": (200, 2_000, 100_000, 1_000_000),
    # Instagram follows come cheaper than listens, so an act needs a larger following to count as big.
    "instagram.followers": (500, 5_000, 200_000, 2_000_000),
}

# Fame signals that only count against an artist: having no Wikipedia article is normal for an emerging act,
# but heavy traffic means mainstream crossover. (full_to, fade_to_zero)
HEADROOM_CEILINGS = {
    "wikipedia.pageviews_30d": (15_000, 300_000),
}

# Headroom also caps the whole score, so a famous act can't ride engagement and activity to the top:
# total *= SIZE_GATE_FLOOR + (1 - SIZE_GATE_FLOOR) * headroom. Headroom 0 keeps 30% of the score.
SIZE_GATE_FLOOR = 0.3

# A real main channel almost never has under 1% as many subscribers as the act has Last.fm listeners; when it
# does, the link is usually a Topic/YouTube Music channel or a different artist with the same name.
SUSPECT_YT_MIN_LISTENERS = 20_000
SUSPECT_YT_RATIO = 0.01


def youtube_suspect(metrics: dict[str, float]) -> bool:
    """The linked channel probably isn't the artist's main one: no videos, or far too few subscribers."""
    subs, listeners = metrics.get("youtube.subscribers"), metrics.get("lastfm.listeners")
    if subs is None:
        return False
    if metrics.get("youtube.videos") == 0:
        return True
    return bool(listeners and listeners >= SUSPECT_YT_MIN_LISTENERS and subs < listeners * SUSPECT_YT_RATIO)


def _traction(value: float, lo: float, full_from: float) -> float:
    """0 below `lo`, ramping (log scale) to 1 at `full_from`."""
    if value < lo:
        return 0.0
    return _clamp((math.log10(value) - math.log10(lo)) / (math.log10(full_from) - math.log10(lo)))


def _size_room(value: float, full_to: float, hi: float) -> float:
    """1 up to `full_to`, fading (log scale) to 0 at `hi`."""
    if value <= full_to:
        return 1.0
    return _clamp(1 - (math.log10(value) - math.log10(full_to)) / (math.log10(hi) - math.log10(full_to)))


def headroom_score(metrics: dict[str, float]) -> float | None:
    """Sweet spot = real traction but not big yet. Traction counts from the strongest platform (a tiny
    or mismatched YouTube channel shouldn't erase real Last.fm numbers); size counts from the largest,
    so being big anywhere counts as big."""
    present = {m: b for m, b in HEADROOM_BANDS.items() if metrics.get(m) is not None}
    if not present:
        return None
    traction = max(_traction(metrics[m], lo, full_from) for m, (lo, full_from, _, _) in present.items())
    room = [_size_room(metrics[m], full_to, hi) for m, (_, _, full_to, hi) in present.items()]
    room += [_size_room(metrics[m], full_to, hi) for m, (full_to, hi) in HEADROOM_CEILINGS.items() if metrics.get(m)]
    return min(traction, *room)


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
          label_class: str | None = None, window_days: int = 30) -> dict:
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
        flags.append(f"no growth rate yet: needs {MIN_GROWTH_DAYS}+ days of snapshots")

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
    if youtube_suspect(metrics):
        flags.append("YouTube count looks off: check the linked channel")
    components["engagement"] = sum(eng) / len(eng) if eng else None

    components["headroom"] = headroom_score(metrics)
    if (metrics.get("deezer.fans") or 0) > 250_000 or any(
            (metrics.get(m) or 0) > 1_000_000 for m in ("lastfm.listeners", "youtube.subscribers")) or (
            metrics.get("wikipedia.pageviews_30d") or 0) > 100_000:
        flags.append("already established")

    components["activity"] = activity_score(latest_release, releases_last_year)

    # label_class from labels.classify: who controls the latest release.
    components["signability"] = SIGNABILITY.get(label_class) if label_class else None
    if label_class in LABEL_FLAGS:
        flags.append(LABEL_FLAGS[label_class])

    usable = {k: v for k, v in components.items() if v is not None}
    w_sum = sum(WEIGHTS[k] for k in usable)
    total = 100 * sum(WEIGHTS[k] * v for k, v in usable.items()) / w_sum if w_sum else 0.0
    headroom = components["headroom"]
    if headroom is not None and headroom < 1:
        total *= SIZE_GATE_FLOOR + (1 - SIZE_GATE_FLOOR) * headroom
        if headroom < 0.5:
            flags.append("score capped: audience already large")

    return {
        "score": round(total, 1),
        "components": {k: (round(v, 3) if v is not None else None) for k, v in components.items()},
        "growth": growth_detail,
        "flags": flags,
        "confidence": round(w_sum, 2),  # share of the model that had data
    }
