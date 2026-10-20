"""Shared HTTP client with per-host rate limiting, retries and a small response cache."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from .config import settings

# Minimum seconds between requests per host (be polite; MusicBrainz enforces 1 req/s).
_MIN_INTERVAL = {
    "musicbrainz.org": 1.1,
    "api.deezer.com": 0.12,
    "ws.audioscrobbler.com": 0.2,
    "graph.facebook.com": 0.5,
}
_CACHE_TTL = 15 * 60

_lock = threading.Lock()
_last_call: dict[str, float] = {}
_cache: dict[str, tuple[float, Any]] = {}
_client: httpx.Client | None = None


class SourceError(RuntimeError):
    pass


class TransientSourceError(SourceError):
    """The network or the service hiccupped (no connection, timeout, 5xx/429 after retries): worth retrying later."""


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=20,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
    return _client


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host, 0.0)
    if not interval:
        return
    with _lock:
        wait = _last_call.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    cache: bool = True,
) -> Any:
    request = client().build_request("GET", url, params=params, headers=headers)
    key = str(request.url)
    if cache and key in _cache and time.time() - _cache[key][0] < _CACHE_TTL:
        return _cache[key][1]

    for attempt in range(4):
        _throttle(request.url.host)
        try:
            resp = client().send(request)
        except httpx.TransportError as exc:
            if attempt == 3:
                raise TransientSourceError(f"{request.url.host}: {exc}") from exc
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(float(resp.headers.get("Retry-After", 1.5 * (attempt + 1))))
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            kind = TransientSourceError if resp.status_code in (429, 500, 502, 503, 504) else SourceError
            raise kind(f"{request.url.host} HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if cache:
            _cache[key] = (time.time(), data)
        return data
    raise TransientSourceError(f"{request.url.host}: retries exhausted")
