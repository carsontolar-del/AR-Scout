"""Local web dashboard: `ar-scout serve` then open http://127.0.0.1:8787."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db, service
from ..http import SourceError

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="AR Scout")


class WatchIn(BaseModel):
    name: str
    status: str = "watching"
    tags: list[str] = []
    note: str | None = None


class NoteIn(BaseModel):
    name: str
    body: str


class YouTubeIn(BaseModel):
    url: str


class InstagramIn(BaseModel):
    handle: str


class VerifyIn(BaseModel):
    verified: bool = True


class DiscoverIn(BaseModel):
    mode: str  # similar | tag | country | new | playlists
    query: str = ""
    min_fans: int = 300
    max_fans: int = 50_000
    limit: int = 20


@app.get("/")
def index():
    # no-cache: browsers revalidate, so an updated dashboard shows up on the next load
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    return service.source_status()


@app.get("/api/health")
def health():
    return service.data_health()


@app.get("/api/leaderboard")
def leaderboard(limit: int = 50, watchlist: bool = False, genre: str | None = None,
                max_audience: int | None = None, window: int = 30, platform: str = "any"):
    try:
        return service.leaderboard(limit, watchlist, window, max_audience, genre or None, platform)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/artist/{name}")
def artist(name: str, refresh: bool = True):
    try:
        r = service.report(name, refresh=refresh)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    r["history"] = db.history(r["artist"]["id"])
    return r


@app.post("/api/artist/{artist_id}/youtube")
def set_youtube(artist_id: int, body: YouTubeIn):
    try:
        return service.set_youtube_channel(artist_id, body.url)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/artist/{artist_id}/instagram")
def set_instagram(artist_id: int, body: InstagramIn):
    try:
        return service.set_instagram_account(artist_id, body.handle)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/review")
def review():
    return service.review_queue()


@app.post("/api/artist/{artist_id}/verify")
def verify(artist_id: int, body: VerifyIn):
    try:
        return service.set_verified(artist_id, body.verified)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/watch")
def watch(body: WatchIn):
    a = service.resolve(body.name, deep=True)
    db.watch(a["id"], body.status, body.tags)
    if body.note:
        db.add_note(a["id"], body.note)
    if not db.history(a["id"]):
        service.snapshot(a["id"])
    return db.watch_entry(a["id"])


@app.delete("/api/watch/{artist_id}")
def unwatch(artist_id: int):
    db.unwatch(artist_id)
    return {"ok": True}


@app.post("/api/note")
def note(body: NoteIn):
    a = db.find_artist(body.name) or service.resolve(body.name, deep=False)
    db.add_note(a["id"], body.body)
    return {"ok": True}


@app.post("/api/discover")
def discover(body: DiscoverIn):
    q, kw = body.query.strip(), dict(min_fans=body.min_fans, max_fans=body.max_fans, limit=body.limit)
    if body.mode == "similar":
        return service.discover_similar([s.strip() for s in q.split(",") if s.strip()], **kw)
    if body.mode == "tag":
        return service.discover_by_tag(q, **kw)
    if body.mode == "country":
        return service.discover_by_country(q, **kw)
    if body.mode == "playlists":
        return service.discover_playlists(q, **kw)
    if body.mode == "new":
        return service.discover_new_releases(q or None, **kw)
    raise HTTPException(400, f"unknown mode {body.mode}")


@app.post("/api/snapshot")
def snapshot(scope: str = "watchlist"):
    return service.snapshot_many(scope)


@app.post("/api/relink")
def relink():
    return service.relink_all()
