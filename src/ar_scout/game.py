"""The public discovery app (/app): spin a genre wheel, hear a 30-second clip the team picked, keep or pass.

Listeners are anonymous: the app makes a random id in the browser and sends it with each call. That's enough to
remember someone's crate and what they've heard, not to prove who they are, so nothing private hangs off it.
Public comments sit under each pick; the team's own comment threads (on picks and submissions) never leave the
dashboard.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone

from . import db
from .http import SourceError
from .sources import deezer

# The wheel's slices and their colors (the app's genre color system).
WHEEL = [
    ("Indie Rock", "#ff7a59"), ("Indie Pop & Dream Pop", "#ff5fa2"), ("Post-punk & Shoegaze", "#b07cff"),
    ("Folk & Songwriters", "#e8b04a"), ("Country & Americana", "#f2884b"), ("Electronic", "#4fd1c5"),
    ("R&B, Soul & Hip-hop", "#7c9cff"), ("Experimental", "#c6e85a"),
]
WHEEL_NAMES = [name for name, _ in WHEEL]
COMMENTS_PER_DAY = 30
MAX_COMMENT = 1000
MAX_AUTHOR = 40
LISTENER = re.compile(r"^[a-f0-9]{16,64}$")
# The "found them at" number: Last.fm listeners where known (closest free number to what fans see), else Deezer.
AUDIENCE = (("lastfm.listeners", "Last.fm listeners"), ("deezer.fans", "Deezer fans"))


def _check_listener(listener: str) -> str:
    if not LISTENER.match(listener or ""):
        raise ValueError("Missing or malformed listener id. Reload the page and try again.")
    return listener


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")


def _audience(artist_id: int) -> tuple[float | None, str | None]:
    latest = db.latest_metrics(artist_id)
    for metric, label in AUDIENCE:
        if latest.get(metric):
            return latest[metric], label
    return None, None


def _public(pick: dict) -> dict:
    """What a listener sees of a pick: no curator workflow fields."""
    keep = ("id", "title", "artist_name", "wheel_genre", "cover_url", "deezer_link", "isrc", "curator", "note")
    return {k: pick.get(k) for k in keep}


# ---------------------------------------------------------------- team side

def artist_tracks(artist_id: int, limit: int = 10) -> list[dict]:
    """An artist's top Deezer tracks, for choosing what to feature."""
    a = db.get_artist(artist_id)
    if not a or not a.get("deezer_id"):
        raise ValueError("This artist has no Deezer link, so there are no clips to feature.")
    picked = {p["deezer_track_id"] for p in db.picks_with_stats()}
    return [{"id": t["id"], "title": t["title"], "has_preview": bool(t.get("preview")),
             "cover_url": (t.get("album") or {}).get("cover_medium"), "album": (t.get("album") or {}).get("title"),
             "already_picked": t["id"] in picked}
            for t in deezer.top_tracks(a["deezer_id"], limit)]


def add_pick(artist_id: int, deezer_track_id: int, wheel_genre: str, curator: str | None = None,
             note: str | None = None, submission_id: int | None = None) -> dict:
    a = db.get_artist(artist_id)
    if not a:
        raise ValueError("That artist isn't in AR Scout.")
    if wheel_genre not in WHEEL_NAMES:
        raise ValueError(f"Pick a wheel genre: {', '.join(WHEEL_NAMES)}.")
    if any(p["deezer_track_id"] == deezer_track_id for p in db.picks_with_stats()):
        raise ValueError("That song is already a pick.")
    t = deezer.track(deezer_track_id)
    if not t:
        raise ValueError("Deezer doesn't have that track.")
    if not t.get("preview"):
        raise ValueError("Deezer has no preview clip for that track, so it can't play in the app.")
    pid = db.add_pick({
        "artist_id": artist_id, "deezer_track_id": deezer_track_id, "title": t["title"], "artist_name": a["name"],
        "wheel_genre": wheel_genre, "cover_url": (t.get("album") or {}).get("cover_xl"), "deezer_link": t.get("link"),
        "isrc": t.get("isrc"), "curator": (curator or "").strip()[:60] or None, "note": (note or "").strip()[:300] or None,
        "submission_id": submission_id})
    return db.get_pick(pid)


def update_pick(pick_id: int, **fields) -> dict:
    if not db.get_pick(pick_id):
        raise ValueError("That pick no longer exists.")
    if "status" in fields and fields["status"] not in ("live", "retired"):
        raise ValueError("Status must be live or retired.")
    if "wheel_genre" in fields and fields["wheel_genre"] not in WHEEL_NAMES:
        raise ValueError(f"Pick a wheel genre: {', '.join(WHEEL_NAMES)}.")
    db.update_pick(pick_id, **fields)
    return db.get_pick(pick_id)


def picks() -> list[dict]:
    out = []
    for p in db.picks_with_stats():
        p["keep_rate"] = p["keeps"] / p["plays"] if p["plays"] else None
        out.append(p)
    return out


# ---------------------------------------------------------------- listener side

def state(listener: str) -> dict:
    listener = _check_listener(listener)
    live = db.unheard_picks(listener)
    return {"wheel": [{"name": n, "hue": h, "unheard": sum(p["wheel_genre"] == n for p in live)} for n, h in WHEEL],
            "unheard": len(live)}


def spin(listener: str, genre: str | None = None, genres: list[str] | None = None) -> dict:
    """A live pick this listener hasn't heard, from `genre`, from any of `genres` (the listener's vibe: the genres
    they put on their wheel), or from any genre at all. Includes a fresh preview URL, since Deezer's signed preview
    links expire."""
    listener = _check_listener(listener)
    if any(g not in WHEEL_NAMES for g in [genre or WHEEL_NAMES[0], *(genres or [])]):
        raise ValueError("Unknown genre.")
    pool = db.unheard_picks(listener, genre)
    if genres:
        pool = [p for p in pool if p["wheel_genre"] in genres]
    if not pool:
        raise ValueError(f"You've heard every {genre} pick so far. Try another genre." if genre
                         else "You've heard every pick in this vibe. Add a genre or two." if genres
                         else "You've heard every pick so far. New ones arrive every week.")
    random.shuffle(pool)
    for pick in pool:
        try:
            t = deezer.track(pick["deezer_track_id"], fresh=True)
        except SourceError:
            t = None
        if t and t.get("preview"):
            audience, label = _audience(pick["artist_id"])
            return _public(pick) | {"preview": t["preview"], "audience": audience, "audience_label": label}
    raise ValueError("Couldn't load a clip right now. Try again in a minute.")


def react(listener: str, pick_id: int, verdict: str, listened_ms: int | None = None) -> dict:
    listener = _check_listener(listener)
    if verdict not in ("keep", "pass"):
        raise ValueError("Verdict must be keep or pass.")
    pick = db.get_pick(pick_id)
    if not pick:
        raise ValueError("That song isn't in the app any more.")
    audience, label = _audience(pick["artist_id"])
    db.save_reaction(pick_id, listener, verdict, max(0, min(int(listened_ms or 0), 600_000)), audience, label)
    return {"ok": True, "audience": audience, "audience_label": label}


def crate(listener: str) -> list[dict]:
    """Kept songs, newest first, with the audience when kept and now."""
    listener = _check_listener(listener)
    out = []
    for p in db.kept(listener):
        now_value, now_label = _audience(p["artist_id"])
        growth = (now_value / p["audience_at"] - 1) if (now_value and p["audience_at"] and now_label == p["audience_metric"]) else None
        out.append(_public(p) | {"kept_at": p["kept_at"], "audience_at": p["audience_at"],
                                 "audience_label": p["audience_metric"], "audience_now": now_value, "growth": growth})
    return out


# ---------------------------------------------------------------- comments

def _clean_text(text: str | None, limit: int, what: str) -> str:
    text = re.sub(r"[ \t]+", " ", (text or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        raise ValueError(f"Please write {what}.")
    if len(text) > limit:
        raise ValueError(f"That's too long (the limit is {limit} characters).")
    return text


def public_comments(pick_id: int) -> list[dict]:
    keep = ("id", "author", "body", "created_at")
    return [{k: c[k] for k in keep} for c in db.comments("pick", pick_id, "public")]


def add_public_comment(listener: str, pick_id: int, author: str | None, body: str | None) -> dict:
    listener = _check_listener(listener)
    pick = db.get_pick(pick_id)
    if not pick or pick["status"] != "live":
        raise ValueError("Comments are closed on this song.")
    if db.comments_since(listener, _today()) >= COMMENTS_PER_DAY:
        raise ValueError("That's a lot of comments today. Try again tomorrow.")
    author = re.sub(r"\s+", " ", (author or "")).strip()[:MAX_AUTHOR] or "Anonymous listener"
    cid = db.add_comment("pick", pick_id, "public", author, _clean_text(body, MAX_COMMENT, "a comment"), listener)
    c = db.get_comment(cid)
    return {k: c[k] for k in ("id", "author", "body", "created_at")}


def add_team_comment(target: str, target_id: int, author: str | None, body: str | None) -> dict:
    if target not in ("pick", "submission"):
        raise ValueError("Team comments go on a pick or a submission.")
    exists = db.get_pick(target_id) if target == "pick" else db.get_submission(target_id)
    if not exists:
        raise ValueError(f"That {target} no longer exists.")
    author = re.sub(r"\s+", " ", (author or "")).strip()[:MAX_AUTHOR] or "Team"
    return db.get_comment(db.add_comment(target, target_id, "team", author, _clean_text(body, 4000, "a comment")))


def moderation_feed(limit: int = 200) -> list[dict]:
    """Recent public comments, hidden ones included, with the song each is under."""
    titles = {p["id"]: f"{p['title']} — {p['artist_name']}" for p in db.picks_with_stats()}
    return [c | {"song": titles.get(c["target_id"], "(removed song)")}
            for c in db.comments("pick", None, "public", include_hidden=True, limit=limit)]
