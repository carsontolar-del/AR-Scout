"""YouTube Data API v3 (free key, 10k units/day). search = 100 units, channels = 1 unit."""

from __future__ import annotations

import re

from ..config import settings
from ..http import SourceError, get_json

BASE = "https://www.googleapis.com/youtube/v3"


def available() -> bool:
    return bool(settings.youtube_api_key)


def _get(path: str, **params) -> dict:
    if not available():
        raise SourceError("YOUTUBE_API_KEY not set")
    return get_json(f"{BASE}/{path}", params={"key": settings.youtube_api_key, **params}) or {}


def channel_id_from_url(url: str) -> str | None:
    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", url)
    return m.group(1) if m else None


def handle_from_url(url: str) -> str | None:
    m = re.search(r"youtube\.com/@([\w.-]+)", url)
    return m.group(1) if m else None


def channel_by_handle(handle: str) -> str | None:
    items = _get("channels", part="id", forHandle=handle).get("items", [])
    return items[0]["id"] if items else None


def channel_from_url(url: str) -> str | None:
    """Channel ID from a /channel/ URL, an @handle URL (1 quota unit) or a bare UC... ID."""
    url = url.strip()
    if re.fullmatch(r"UC[\w-]{22}", url):
        return url
    if channel := channel_id_from_url(url):
        return channel
    if handle := handle_from_url(url):
        return channel_by_handle(handle)
    return None


def _key(s: str) -> str:
    return re.sub(r"[\W_]", "", s.casefold().replace("&", "and"))


def title_matches(title: str, artist: str) -> bool:
    """Channel title names the artist, ignoring case, spaces and punctuation ('Of The Trees' ~ 'ofthetrees'),
    with suffixes allowed ('Of The Trees Official', 'OfTheTreesVEVO'). Topic channels never match."""
    if title.rstrip().casefold().endswith("- topic"):
        return False
    t, a = _key(title), _key(artist)
    return bool(a) and t.startswith(a)


def search_channel(name: str) -> str | None:
    """Costs 100 quota units. Takes the first result whose title names the artist, skipping Topic channels."""
    items = _get("search", part="snippet", type="channel", q=name, maxResults=5).get("items", [])
    for item in items:
        if title_matches(item["snippet"]["channelTitle"], name):
            return item["snippet"]["channelId"]
    return None


def channel_stats(channel_id: str) -> dict | None:
    items = _get("channels", part="statistics,snippet", id=channel_id).get("items", [])
    if not items:
        return None
    stats, snip = items[0]["statistics"], items[0]["snippet"]
    return {
        "channel_id": channel_id,
        "title": snip.get("title"),
        "subscribers": None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0)),
        "views": int(stats.get("viewCount", 0)),
        "videos": int(stats.get("videoCount", 0)),
        "country": snip.get("country"),
    }
