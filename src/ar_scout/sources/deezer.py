"""Deezer public API (no key). Best free source of fan counts, related artists, charts and labels."""

from __future__ import annotations

from typing import Any

from ..http import get_json

BASE = "https://api.deezer.com"

# Distributors / DIY services. A release "label" matching one of these usually means unsigned.
DIY_DISTRIBUTORS = (
    "distrokid", "tunecore", "cd baby", "cdbaby", "amuse", "routenote", "ditto", "unitedmasters",
    "united masters", "emubands", "awal", "stem", "soundrop", "symphonic", "onerpm", "level music",
    "spinnup", "repost network", "landr", "too lost", "vydia", "fresh tunes", "freshtunes",
    "record union", "horus music", "independent", "self-released", "self released", "records dk",
)


def _data(path: str, **params: Any) -> list[dict]:
    payload = get_json(f"{BASE}{path}", params=params or None)
    if not payload or "error" in payload:
        return []
    return payload.get("data", [])


def search_artists(name: str, limit: int = 5) -> list[dict]:
    return _data("/search/artist", q=name, limit=limit)


def artist(deezer_id: int) -> dict | None:
    payload = get_json(f"{BASE}/artist/{deezer_id}")
    if not payload or "error" in payload:
        return None
    return payload


def related(deezer_id: int, limit: int = 50) -> list[dict]:
    return _data(f"/artist/{deezer_id}/related", limit=limit)


def albums(deezer_id: int, limit: int = 50) -> list[dict]:
    return _data(f"/artist/{deezer_id}/albums", limit=limit)


def album(album_id: int) -> dict | None:
    payload = get_json(f"{BASE}/album/{album_id}")
    if not payload or "error" in payload:
        return None
    return payload


def top_tracks(deezer_id: int, limit: int = 10) -> list[dict]:
    return _data(f"/artist/{deezer_id}/top", limit=limit)


def genres() -> list[dict]:
    return _data("/genre")


def chart_artists(genre_id: int = 0, limit: int = 100) -> list[dict]:
    return _data(f"/chart/{genre_id}/artists", limit=limit)


def editorial_selection(genre_id: int = 0) -> list[dict]:
    """Editor-picked albums (the old /editorial/{id}/releases endpoint now returns nothing)."""
    return _data(f"/editorial/{genre_id}/selection")


def search_playlists(query: str, limit: int = 10) -> list[dict]:
    return _data("/search/playlist", q=query, limit=limit)


def playlist_tracks(playlist_id: int, limit: int = 100) -> list[dict]:
    return _data(f"/playlist/{playlist_id}/tracks", limit=limit)


def find_genre_id(name: str) -> int | None:
    name = name.lower().strip()
    for g in genres():
        if g["name"].lower() == name or name in g["name"].lower():
            return g["id"]
    return None


def is_diy_label(label: str | None) -> bool:
    if not label:
        return False
    low = label.lower()
    return any(d in low for d in DIY_DISTRIBUTORS)
