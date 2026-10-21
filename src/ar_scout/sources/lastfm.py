"""Last.fm API (free key). Listeners, scrobbles, tags, similar artists, tag/geo charts."""

from __future__ import annotations

import re

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


def artist_info(name: str, autocorrect: bool = True) -> dict | None:
    """autocorrect lets Last.fm fix misspellings, but it can also swap in a different artist ("Ratboys" ->
    "Ratboy"), so names a person chose are looked up exactly."""
    data = _call("artist.getinfo", artist=name, autocorrect=1 if autocorrect else 0).get("artist")
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


def name_from_url(link: str) -> str | None:
    """'https://www.last.fm/music/The+K%27s' -> "The K's"; a plain name passes through."""
    from urllib.parse import unquote_plus
    link = link.strip()
    if "last.fm" not in link and "lastfm" not in link:
        return link or None
    m = re.search(r"/music/([^/?#]+)", link)
    return unquote_plus(m.group(1)) if m else None


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

