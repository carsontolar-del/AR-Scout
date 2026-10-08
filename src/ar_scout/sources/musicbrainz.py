"""MusicBrainz (no key, 1 req/s). Canonical identity, origin country, career start, social links."""

from __future__ import annotations

from ..http import get_json

BASE = "https://musicbrainz.org/ws/2"


def search(name: str, limit: int = 5) -> list[dict]:
    payload = get_json(f"{BASE}/artist", params={"query": f'artist:"{name}"', "fmt": "json", "limit": limit})
    return (payload or {}).get("artists", [])


def lookup(mbid: str) -> dict | None:
    payload = get_json(f"{BASE}/artist/{mbid}", params={"inc": "url-rels+genres+tags", "fmt": "json"})
    if not payload:
        return None
    links: dict[str, str] = {}
    for rel in payload.get("relations", []):
        url = rel.get("url", {}).get("resource", "")
        for key in ("youtube", "instagram", "tiktok", "twitter", "x.com", "soundcloud",
                    "spotify", "bandcamp", "wikidata", "facebook"):
            if key in url and key not in links:
                links["twitter" if key == "x.com" else key] = url
    return {
        "mbid": mbid,
        "name": payload.get("name"),
        "type": payload.get("type"),
        "country": payload.get("country"),
        "area": (payload.get("area") or {}).get("name"),
        "begin": (payload.get("life-span") or {}).get("begin"),
        "genres": [g["name"] for g in payload.get("genres", [])],
        "links": links,
    }
