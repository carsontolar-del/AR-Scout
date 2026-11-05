"""Web dashboard: `ar-scout serve` then open http://127.0.0.1:8787. Set AR_SCOUT_TEAM_PASSWORD /
AR_SCOUT_ADMIN_PASSWORD to require sign-in (see web/auth.py); without them there is no login."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from contextlib import asynccontextmanager

from .. import db, game, scheduler, service, submissions
from ..http import SourceError
from . import auth

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.start()  # only when AR_SCOUT_DAILY_AT is set (on a host); a laptop uses its own scheduler
    yield


app = FastAPI(title="AR Scout", lifespan=lifespan)

# Reachable without signing in: the public discovery app (/app, with its submission links) and what it calls.
PUBLIC = {"/login", "/api/login", "/favicon.svg", "/favicon.ico", "/app", "/submit/artist", "/submit/fan", "/api/submit"}
PUBLIC_PREFIX = "/api/app/"


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    if not auth.enabled():
        request.state.role = "admin"  # local use: no login, everything allowed
        return await call_next(request)
    request.state.role = auth.read_token(request.cookies.get(auth.COOKIE))
    if request.state.role or request.url.path in PUBLIC or request.url.path.startswith(PUBLIC_PREFIX):
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


@app.get("/api/digest")
def digest(days: int = 7):
    return service.weekly_digest(days)


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
    try:
        return service.discover(body.mode, body.query, body.min_fans, body.max_fans, body.limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/searches")
def searches():
    return db.saved_searches()


@app.post("/api/searches")
def save_search(body: DiscoverIn):
    try:
        return service.save_search(body.mode, body.query, body.min_fans, body.max_fans, body.limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/searches/{search_id}/run")
def run_search(search_id: int):
    try:
        return service.run_saved_search(search_id)
    except (SourceError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/searches/{search_id}")
def delete_search(search_id: int):
    db.delete_search(search_id)
    return {"ok": True}


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


# ---------------------------------------------------------------- public submission links

class SubmitIn(BaseModel):
    kind: str            # artist | fan
    topic: str           # demo | message | song
    name: str | None = None
    email: str | None = None
    artist_name: str | None = None
    song_title: str | None = None
    link: str | None = None
    genre: str | None = None
    location: str | None = None
    socials: str | None = None
    message: str | None = None
    consent: bool = False
    rights: bool = False
    credit_ok: bool = False
    website: str | None = None  # hidden field people never see; bots fill it in


class SubmissionIn(BaseModel):
    status: str | None = None
    team_note: str | None = None


@app.get("/app")
@app.get("/submit/artist")
@app.get("/submit/fan")
def app_page():
    """The discovery app. /submit/artist and /submit/fan open it on the matching Submit tab."""
    return FileResponse(STATIC / "app.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/submit")
def submit(body: SubmitIn, request: Request):
    if body.website:  # a bot: pretend it worked, store nothing
        return {"ok": True}
    addr = request.client.host if request.client else "?"
    if auth.too_many("submission", addr):
        raise HTTPException(429, "That's a lot of submissions in a short time. Please try again in an hour.")
    try:
        submissions.submit(body.kind, body.topic, body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    auth.record("submission", addr)
    return {"ok": True}  # the stored row stays private to the team


@app.get("/api/submissions")
def list_submissions(kind: str | None = None, status: str | None = None):
    return {"rows": db.submissions(kind or None, status or None), "new": submissions.counts(),
            "statuses": submissions.STATUSES}


@app.patch("/api/submissions/{submission_id}")
def update_submission(submission_id: int, body: SubmissionIn):
    try:
        return submissions.update(submission_id, body.status, body.team_note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/submissions/{submission_id}", dependencies=[Depends(admin_only)])
def delete_submission(submission_id: int):
    db.delete_submission(submission_id)
    return {"ok": True}


# ---------------------------------------------------------------- discovery app: public (no sign-in)

class SpinIn(BaseModel):
    listener: str
    genre: str | None = None
    genres: list[str] | None = Field(None, max_length=20)  # the listener's vibe: genres on their wheel
    wheel: str = "main"


class ReactIn(BaseModel):
    listener: str
    pick_id: int
    verdict: str
    listened_ms: int | None = None


class PublicCommentIn(BaseModel):
    listener: str
    author: str | None = None
    body: str
    website: str | None = None  # bots only


def _bad_request(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/app/state")
def app_state(listener: str, wheel: str = "main"):
    return _bad_request(game.state, listener, wheel)


@app.post("/api/app/spin")
def app_spin(body: SpinIn):
    return _bad_request(game.spin, body.listener, body.genre or None, body.genres or None, body.wheel)


@app.post("/api/app/react")
def app_react(body: ReactIn):
    return _bad_request(game.react, body.listener, body.pick_id, body.verdict, body.listened_ms)


@app.get("/api/app/crate")
def app_crate(listener: str):
    return _bad_request(game.crate, listener)


@app.get("/api/app/picks/{pick_id}/comments")
def app_comments(pick_id: int):
    return game.public_comments(pick_id)


@app.post("/api/app/picks/{pick_id}/comments")
def app_add_comment(pick_id: int, body: PublicCommentIn, request: Request):
    if body.website:
        return {"ok": True}
    addr = request.client.host if request.client else "?"
    if auth.too_many("comment", addr):
        raise HTTPException(429, "That's a lot of comments in a short time. Please try again in an hour.")
    out = _bad_request(game.add_public_comment, body.listener, pick_id, body.author, body.body)
    auth.record("comment", addr)
    return out


# ---------------------------------------------------------------- discovery app: team side

class PickIn(BaseModel):
    artist_id: int
    deezer_track_id: int
    wheel_genre: str
    curator: str | None = None
    note: str | None = None
    submission_id: int | None = None


class PickUpdateIn(BaseModel):
    status: str | None = None
    note: str | None = None
    wheel_genre: str | None = None
    curator: str | None = None


class TeamCommentIn(BaseModel):
    target: str        # pick | submission
    target_id: int
    author: str | None = None
    body: str


class HideIn(BaseModel):
    hidden: bool = True


@app.get("/api/picks")
def list_picks():
    return {"rows": game.picks(), "wheel": [{"name": n, "hue": h, "wheel": w} for w, slices in game.WHEELS.items()
                                            for n, h in slices]}


@app.get("/api/artist/{artist_id}/tracks")
def artist_tracks(artist_id: int):
    try:
        return game.artist_tracks(artist_id)
    except (ValueError, SourceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/picks")
def add_pick(body: PickIn):
    try:
        return game.add_pick(body.artist_id, body.deezer_track_id, body.wheel_genre, body.curator, body.note,
                             body.submission_id)
    except (ValueError, SourceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/picks/{pick_id}")
def update_pick(pick_id: int, body: PickUpdateIn):
    return _bad_request(game.update_pick, pick_id, **{k: getattr(body, k) for k in body.model_fields_set})


@app.delete("/api/picks/{pick_id}", dependencies=[Depends(admin_only)])
def delete_pick(pick_id: int):
    db.delete_pick(pick_id)
    return {"ok": True}


@app.get("/api/comments")
def list_comments(target: str, target_id: int, scope: str = "team"):
    if scope not in ("team", "public"):
        raise HTTPException(400, "scope must be team or public")
    return db.comments(target, target_id, scope, include_hidden=True)


@app.get("/api/comments/public")
def public_comment_feed():
    return game.moderation_feed()


@app.post("/api/comments")
def add_team_comment(body: TeamCommentIn):
    return _bad_request(game.add_team_comment, body.target, body.target_id, body.author, body.body)


@app.patch("/api/comments/{comment_id}")
def hide_comment(comment_id: int, body: HideIn):
    if not db.get_comment(comment_id):
        raise HTTPException(404, "That comment no longer exists.")
    db.set_comment_hidden(comment_id, body.hidden)
    return db.get_comment(comment_id)


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int):
    """Team comments only. Public comments are hidden instead, so there's a record of what was removed."""
    c = db.get_comment(comment_id)
    if c and c["scope"] != "team":
        raise HTTPException(400, "Public comments can be hidden, not deleted.")
    db.delete_comment(comment_id)
    return {"ok": True}
