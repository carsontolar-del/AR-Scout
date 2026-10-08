"""Spotify Web API (optional, client-credentials).

Since Feb/Mar 2026, Development Mode apps no longer receive artist `followers` or `popularity`,
search is capped at 10 results, and top-tracks / related-artists are gone. We use Spotify only for
IDs, genres, images and release history. If your app has Extended Quota Mode, followers and
popularity come back automatically and are recorded as metrics.
"""

from __future__ import annotations

import base64
import time

import httpx

from ..config import settings
from ..http import SourceError, client, get_json

_token: tuple[str, float] | None = None


def available() -> bool:
    return bool(settings.spotify_client_id and settings.spotify_client_secret)


def _auth() -> dict[str, str]:
    global _token
    if not available():
        raise SourceError("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set")
    if _token is None or _token[1] < time.time() + 60:
        creds = base64.b64encode(f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode())
        resp = client().post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {creds.decode()}"},
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(f"Spotify auth failed: {resp.text[:200]}") from exc
        body = resp.json()
        _token = (body["access_token"], time.time() + body["expires_in"])
    return {"Authorization": f"Bearer {_token[0]}"}


def search_artist(name: str) -> dict | None:
    data = get_json("https://api.spotify.com/v1/search",
                    params={"q": name, "type": "artist", "limit": 5}, headers=_auth())
    items = (data or {}).get("artists", {}).get("items", [])
    for item in items:
        if item["name"].lower() == name.lower():
            return item
    return items[0] if items else None


def artist(spotify_id: str) -> dict | None:
    return get_json(f"https://api.spotify.com/v1/artists/{spotify_id}", headers=_auth())


def releases(spotify_id: str, limit: int = 10) -> list[dict]:
    data = get_json(f"https://api.spotify.com/v1/artists/{spotify_id}/albums",
                    params={"include_groups": "album,single", "limit": limit}, headers=_auth())
    return (data or {}).get("items", [])
