"""Web dashboard: `ar-scout serve` then open http://127.0.0.1:8787. Set AR_SCOUT_TEAM_PASSWORD /
AR_SCOUT_ADMIN_PASSWORD to require sign-in (see web/auth.py); without them there is no login."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from contextlib import asynccontextmanager

from .. import db, scheduler, service
from ..http import SourceError
from . import auth

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.start()  # only when AR_SCOUT_DAILY_AT is set (on a host); a laptop uses its own scheduler
    yield


app = FastAPI(title="AR Scout", lifespan=lifespan)

# Reachable without signing in.
PUBLIC = {"/login", "/api/login", "/favicon.svg", "/favicon.ico"}


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    if not auth.enabled():
        request.state.role = "admin"  # local use: no login, everything allowed
        return await call_next(request)
    request.state.role = auth.read_token(request.cookies.get(auth.COOKIE))
    if request.state.role or request.url.path in PUBLIC:
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in first"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def admin_only(request: Request) -> None:
    """Actions that spend API quota, rewrite linked accounts or delete data."""
    if request.state.role != "admin":
        raise HTTPException(403, "Only an admin can do that. Sign in with the admin password.")


class LoginIn(BaseModel):
    password: str


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


class PipelineIn(BaseModel):
    status: str | None = None
    owner: str | None = None
    next_step: str | None = None
    next_date: str | None = None


class ArchiveIn(BaseModel):
    archived: bool = True


class ManagementIn(BaseModel):
    status: str | None = None


class ManualIn(BaseModel):
    metric: str
    value: float


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


@app.get("/favicon.svg")
@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    addr = request.client.host if request.client else "?"
    if auth.too_many_failures(addr):
        raise HTTPException(429, "Too many wrong passwords. Wait 15 minutes and try again.")
    role = auth.check_password(body.password)
    if not role:
        auth.record_failure(addr)
        raise HTTPException(401, "That password isn't right.")
    resp = JSONResponse({"role": role})
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(auth.COOKIE, auth.make_token(role), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=https)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/status")
def status(request: Request):
    return service.source_status() | {"role": request.state.role if auth.enabled() else "local", "login": auth.enabled()}


@app.get("/api/health")
def health():
    return service.data_health()


@app.get("/api/leaderboard")
def leaderboard(limit: int = 50, watchlist: bool = False, genre: str | None = None,
                max_audience: int | None = None, window: int = 30, platform: str = "any", archived: bool = False):
    try:
        return service.leaderboard(limit, watchlist, window, max_audience, genre or None, platform, archived)
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


@app.post("/api/artist/{artist_id}/youtube", dependencies=[Depends(admin_only)])
def set_youtube(artist_id: int, body: YouTubeIn):
    try:
        return service.set_youtube_channel(artist_id, body.url)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


class LinkIn(BaseModel):
    link: str


@app.post("/api/artist/{artist_id}/lastfm", dependencies=[Depends(admin_only)])
def set_lastfm(artist_id: int, body: LinkIn):
    try:
        return service.set_lastfm_artist(artist_id, body.link)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/artist/{artist_id}/deezer", dependencies=[Depends(admin_only)])
def set_deezer(artist_id: int, body: LinkIn):
    try:
        return service.set_deezer_artist(artist_id, body.link)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/artist/{artist_id}/instagram", dependencies=[Depends(admin_only)])
def set_instagram(artist_id: int, body: InstagramIn):
    try:
        return service.set_instagram_account(artist_id, body.handle)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/artist/{artist_id}/manual")
def manual(artist_id: int, body: ManualIn):
    try:
        return service.record_manual(artist_id, body.metric, body.value)
    except (ValueError, SourceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/artist/{artist_id}/management")
def set_management(artist_id: int, body: ManagementIn):
    try:
        return service.set_management(artist_id, body.status or None)
    except (ValueError, SourceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/artist/{artist_id}/archive")
def archive(artist_id: int, body: ArchiveIn):
    try:
        return service.set_archived(artist_id, body.archived)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/artist/{artist_id}", dependencies=[Depends(admin_only)])
def delete(artist_id: int):
    try:
        return service.delete_artist(artist_id)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc


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


@app.patch("/api/watch/{artist_id}")
def update_pipeline(artist_id: int, body: PipelineIn):
    fields = {k: getattr(body, k) for k in ("owner", "next_step", "next_date") if k in body.model_fields_set}
    try:
        return service.update_pipeline(artist_id, body.status, **fields)
    except (ValueError, SourceError) as exc:
        raise HTTPException(400, str(exc)) from exc


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


@app.post("/api/snapshot", dependencies=[Depends(admin_only)])
def snapshot(scope: str = "watchlist"):
    return service.snapshot_many(scope)


@app.post("/api/admin/database", dependencies=[Depends(admin_only)])
async def upload_database(request: Request):
    """Body: the raw .db file. Used by `ar-scout upload-db` to move a laptop's history to a host."""
    try:
        return service.import_database(await request.body())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/relink", dependencies=[Depends(admin_only)])
def relink():
    return service.relink_all()
