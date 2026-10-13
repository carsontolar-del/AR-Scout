"""Last.fm API (free key). Listeners, scrobbles, tags, similar artists, tag/geo charts."""

from __future__ import annotations

from typing import Any

from ..config import settings
from ..http import SourceError, get_json

BASE = "https://ws.audioscrobbler.com/2.0/"


def available() -> bool:
    return bool(settings.lastfm_api_key)


def _call(method: str, **params: Any) -> dict:
    if not available():
        raise SourceError("LASTFM_API_KEY not set")
    payload = get_json(
        BASE,
        params={"method": method, "api_key": settings.lastfm_api_key, "format": "json", **params},
    )
    if not payload or "error" in payload:
        return {}
    return payload


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def artist_info(name: str) -> dict | None:
    data = _call("artist.getinfo", artist=name, autocorrect=1).get("artist")
    if not data:
        return None
    stats = data.get("stats", {})
    return {
        "name": data.get("name"),
        "mbid": data.get("mbid") or None,
        "url": data.get("url"),
        "listeners": int(stats.get("listeners", 0) or 0),
        "playcount": int(stats.get("playcount", 0) or 0),
        "tags": [t["name"] for t in _as_list(data.get("tags", {}).get("tag"))],
        "bio": (data.get("bio", {}).get("summary") or "").split("<a href")[0].strip(),
    }


def similar(name: str, limit: int = 50) -> list[dict]:
    data = _call("artist.getsimilar", artist=name, autocorrect=1, limit=limit)
    return [
        {"name": a["name"], "mbid": a.get("mbid") or None, "match": float(a.get("match", 0))}
        for a in _as_list(data.get("similarartists", {}).get("artist"))
    ]


def tag_top_artists(tag: str, page: int = 1, limit: int = 50) -> list[dict]:
    data = _call("tag.gettopartists", tag=tag, page=page, limit=limit)
    return [{"name": a["name"], "mbid": a.get("mbid") or None}
            for a in _as_list(data.get("topartists", {}).get("artist"))]


def geo_top_artists(country: str, page: int = 1, limit: int = 50) -> list[dict]:
    data = _call("geo.gettopartists", country=country, page=page, limit=limit)
    return [{"name": a["name"], "mbid": a.get("mbid") or None, "listeners": int(a.get("listeners", 0))}
            for a in _as_list(data.get("topartists", {}).get("artist"))]

