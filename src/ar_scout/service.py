"""High-level operations shared by the CLI, MCP server and web dashboard."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import db, labels, management, scoring
from .config import settings
from .http import SourceError, TransientSourceError
from .sources import deezer, instagram, lastfm, musicbrainz, spotify, wikipedia, youtube

SNAPSHOT_FRESH_HOURS = 12
# A bulk run skips artists snapshotted this recently. Short enough that an evening refresh can't block the next
# morning's daily run (it was 20h, and a 6 PM refresh made the 09:00 run skip everyone), long enough to avoid
# re-pulling the same numbers twice in an afternoon.
STALE_HOURS = 6
# Written by scripts/install-daily-snapshot.sh
DAILY_JOB_PLIST = Path.home() / "Library/LaunchAgents/com.ar-scout.snapshot.plist"
AUDIENCE_METRICS = {"deezer": "deezer.fans", "lastfm": "lastfm.listeners", "youtube": "youtube.subscribers",
                    "instagram": "instagram.followers", "tiktok": "tiktok.followers",
                    "spotify": "spotify.monthly_listeners"}

# Numbers with no free API, typed in from the public profile. Instagram is manual only until the API is set up.
MANUAL_METRICS = {
    "tiktok.followers": "TikTok followers",
    "tiktok.likes": "TikTok likes",
    "spotify.monthly_listeners": "Spotify monthly listeners",
    "instagram.followers": "Instagram followers",
}


class _Errors(list):
    """Error messages from one operation; `transient` is set when any came from a network hiccup."""
    transient = False


def _try(errors: list[str], label: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except SourceError as exc:
        errors.append(f"{label}: {exc}")
        if isinstance(exc, TransientSourceError) and isinstance(errors, _Errors):
            errors.transient = True
    except Exception as exc:  # keep one flaky source from sinking the whole run
        errors.append(f"{label}: {type(exc).__name__}: {exc}")
    return None


# --------------------------------------------------------------------------- identity

def _pick_deezer(name: str) -> dict | None:
    results = deezer.search_artists(name, limit=10)
    exact = [r for r in results if r["name"].lower() == name.lower()]
    pool = exact or results
    return max(pool, key=lambda r: r.get("nb_fan", 0)) if pool else None


def _release_info(deezer_id: int) -> dict:
    albums = sorted(deezer.albums(deezer_id), key=lambda a: a.get("release_date", ""), reverse=True)
    if not albums:
        return {}
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    latest = albums[0]
    full = deezer.album(latest["id"]) or {}
    return {
        "latest_release": latest.get("release_date"),
        "latest_title": latest.get("title"),
        "latest_type": latest.get("record_type"),
        "latest_label": full.get("label"),
        "releases_last_year": sum(1 for a in albums if a.get("release_date", "") >= cutoff),
        "first_release": albums[-1].get("release_date"),
        "recent": [{"title": a["title"], "date": a.get("release_date"), "type": a.get("record_type")}
                   for a in albums[:8]],
    }


def track_light(dz: dict, via: str | None = None) -> int:
    """Register a Deezer artist and snapshot cheap metrics (Deezer + Last.fm). Used for discovery."""
    existing = db.find_by_deezer(dz["id"])
    errors: list[str] = []
    rel = _try(errors, "deezer releases", lambda: _release_info(dz["id"])) or {}
    fields = {
        "name": dz["name"], "deezer_id": dz["id"],
        "image_url": dz.get("picture_medium"),
        "latest_label": rel.get("latest_label"), "latest_release": rel.get("latest_release"),
    }
    if not existing:
        fields["discovered_via"] = via
    artist_id = db.upsert_artist(fields, existing["id"] if existing else None)

    metrics: dict[str, float | None] = {"deezer.fans": dz.get("nb_fan"), "deezer.albums": dz.get("nb_album"),
                                        "releases_last_year": rel.get("releases_last_year")}
    if lastfm.available():
        manual_lf = existing and existing.get("lastfm_source") == "manual"
        lf_name = existing["lastfm_name"] if manual_lf else dz["name"]
        info = _try(errors, "lastfm", lambda: lastfm.artist_info(lf_name, autocorrect=not manual_lf))
        if info:
            metrics["lastfm.listeners"] = info["listeners"]
            metrics["lastfm.playcount"] = info["playcount"]
            if not manual_lf:
                db.upsert_artist({"lastfm_name": info["name"], "mbid": info["mbid"],
                                  "genres": info["tags"][:6]}, artist_id)
    db.add_snapshot(artist_id, metrics)
    return artist_id


def _youtube_from_link(errors: list[str], url: str, name: str) -> tuple[str | None, str | None]:
    """Use the MusicBrainz YouTube link only if it looks like the artist's channel (has videos, title names
    the artist). Those links are community-edited and sometimes point at someone else entirely; otherwise
    search by name (100 units) and fall back to the link if the search finds nothing better."""
    channel = _try(errors, "youtube", lambda: youtube.channel_from_url(url))
    if not channel:
        return None, None
    stats = _try(errors, "youtube", lambda: youtube.channel_stats(channel))
    if stats and stats["videos"] and youtube.title_matches(stats["title"] or "", name):
        return channel, "musicbrainz"
    found = _try(errors, "youtube", lambda: youtube.search_channel(name))
    if found and found != channel:
        title = stats["title"] if stats else "an unavailable channel"
        errors.append(f"youtube: MusicBrainz links '{title}', which doesn't look like {name}'s channel; "
                      "used the search match instead")
        return found, "search"
    return channel, "musicbrainz"


def _switch_youtube(artist: dict, channel: str, source: str) -> None:
    """Point an artist at a different channel. Old YouTube snapshots belong to the old channel, so they're
    dropped; keeping them would read as a huge jump in growth."""
    if artist.get("youtube_channel") != channel:
        db.delete_metrics(artist["id"], "youtube.")
    db.upsert_artist({"youtube_channel": channel, "youtube_source": source}, artist["id"])


def _instagram_username(artist: dict) -> str | None:
    """Stored username, else the MusicBrainz link (artists resolved before Instagram support only have that)."""
    return artist.get("instagram_username") or instagram.username_from_url((artist.get("links") or {}).get("instagram", ""))


def record_manual(artist_id: int, metric: str, value: float) -> dict:
    """Save a number read off a public profile (TikTok followers, Spotify monthly listeners...) as a dated snapshot,
    so it builds a trend like any other platform."""
    if metric not in MANUAL_METRICS:
        raise ValueError(f"metric must be one of: {', '.join(MANUAL_METRICS)}")
    if metric == "instagram.followers" and instagram.available():
        raise ValueError("Instagram followers come from the Instagram API; fix the account with Change instead")
    if value is None or value < 0 or value > 2_000_000_000:
        raise ValueError("Enter a whole number of people, e.g. 12500")
    if not db.get_artist(artist_id):
        raise SourceError(f"No artist with id {artist_id}")
    db.add_snapshot(artist_id, {metric: int(round(value))})
    return {"artist_id": artist_id, "metric": metric, "label": MANUAL_METRICS[metric], "value": int(round(value))}


def set_instagram_account(artist_id: int, handle: str) -> dict:
    """Manually set an artist's Instagram username (profile URL, @handle or bare username), then snapshot.
    With Instagram configured, the account must be one Business Discovery can read."""
    artist = db.get_artist(artist_id)
    if not artist:
        raise SourceError(f"No artist with id {artist_id}")
    username = instagram.username_from_url(handle)
    if not username:
        raise SourceError("Not an Instagram profile. Use instagram.com/name, @name or name")
    stats = instagram.profile(username) if instagram.available() else None
    if _instagram_username(artist) != username:
        db.delete_metrics(artist_id, "instagram.")
    db.upsert_artist({"instagram_username": username, "instagram_source": "manual"}, artist_id)
    if stats:
        snapshot(artist_id)
    return {"artist": artist["name"], "username": username,
            "followers": stats["followers"] if stats else None, "media_count": stats["media_count"] if stats else None}


def set_lastfm_artist(artist_id: int, link: str) -> dict:
    """Point an artist at the right Last.fm artist (a last.fm/music/... link or the exact name), drop the old
    artist's Last.fm history and snapshot. Kept even when later lookups would pick someone else."""
    artist = db.get_artist(artist_id)
    if not artist:
        raise SourceError(f"No artist with id {artist_id}")
    name = lastfm.name_from_url(link)
    if not name:
        raise SourceError("Not a Last.fm artist link. Use last.fm/music/Artist+Name or the exact name")
    info = lastfm.artist_info(name, autocorrect=False)
    if not info or not info["listeners"]:
        raise SourceError(f"Last.fm has no listeners for “{name}”. Check the spelling or the link")
    if info["name"] != artist.get("lastfm_name"):
        db.delete_metrics(artist_id, "lastfm.")
    db.upsert_artist({"lastfm_name": info["name"], "lastfm_source": "manual", "genres": info["tags"][:6]}, artist_id)
    snapshot(artist_id)
    return {"artist": artist["name"], "lastfm_name": info["name"], "listeners": info["listeners"],
            "playcount": info["playcount"], "url": info["url"]}


def set_deezer_artist(artist_id: int, link: str) -> dict:
    """Point an artist at the right Deezer artist (a deezer.com/artist/... link or its number), drop the old
    artist's Deezer history and snapshot."""
    artist = db.get_artist(artist_id)
    if not artist:
        raise SourceError(f"No artist with id {artist_id}")
    dz_id = deezer.id_from_url(link)
    if not dz_id:
        raise SourceError("Not a Deezer artist link. Use deezer.com/artist/12345")
    dz = deezer.artist(dz_id)
    if not dz:
        raise SourceError(f"Deezer has no artist {dz_id}")
    other = db.find_by_deezer(dz_id)
    if other and other["id"] != artist_id:
        raise SourceError(f"That Deezer page is already tracked as “{other['name']}”")
    if dz_id != artist.get("deezer_id"):
        db.delete_metrics(artist_id, "deezer.")
        db.delete_metrics(artist_id, "releases_last_year")
    db.upsert_artist({"deezer_id": dz_id, "deezer_source": "manual", "image_url": dz.get("picture_medium")}, artist_id)
    snapshot(artist_id)
    return {"artist": artist["name"], "deezer_id": dz_id, "deezer_name": dz.get("name"), "fans": dz.get("nb_fan")}


def set_youtube_channel(artist_id: int, url: str) -> dict:
    """Manually set an artist's YouTube channel from a channel URL, @handle URL or channel ID, then snapshot.
    Manual choices are never overwritten by recheck_youtube()."""
    artist = db.get_artist(artist_id)
    if not artist:
        raise SourceError(f"No artist with id {artist_id}")
    channel = youtube.channel_from_url(url)
    if not channel:
        raise SourceError("Not a YouTube channel link. Use youtube.com/@handle, youtube.com/channel/UC… or a UC… ID")
    stats = youtube.channel_stats(channel)
    if not stats:
        raise SourceError(f"YouTube has no channel {channel}")
    _switch_youtube(artist, channel, "manual")
    snapshot(artist_id)
    return {"artist": artist["name"], "channel": channel, "title": stats["title"],
            "subscribers": stats["subscribers"], "videos": stats["videos"]}


def recheck_youtube(progress: Callable[[str], None] | None = None) -> dict:
    """Search again (100 quota units each) for artists whose linked channel looks wrong: no videos, or far
    fewer subscribers than their Last.fm audience. Channels set by hand are left alone."""
    changed, unchanged, errors = [], [], []
    for a in db.all_artists():
        if not a.get("youtube_channel") or a.get("youtube_source") == "manual":
            continue
        before = db.latest_metrics(a["id"])
        if not scoring.youtube_suspect(before):
            continue
        if progress:
            progress(a["name"])
        found = _try(errors, f"{a['name']}: youtube", lambda: youtube.search_channel(a["name"]))
        if not found or found == a["youtube_channel"]:
            unchanged.append(a["name"])
            continue
        _switch_youtube(a, found, "search")
        errors += [f"{a['name']}: {e}" for e in snapshot(a["id"])["errors"]]
        changed.append({"name": a["name"], "channel": found,
                        "subscribers_before": before.get("youtube.subscribers"),
                        "subscribers_after": db.latest_metrics(a["id"]).get("youtube.subscribers")})
    return {"changed": changed, "unchanged": unchanged, "errors": errors}


def resolve(name: str, deep: bool = True) -> dict:
    """Find or create an artist and link IDs across every configured source."""
    errors: list[str] = []
    artist = db.find_artist(name)
    # Discovery (track_light) already stores a Last.fm mbid, so only a completed deep lookup counts as resolved.
    if artist and (not deep or artist.get("resolved_at")):
        return artist

    fields: dict[str, Any] = {}
    dz = None
    if artist and artist.get("deezer_id"):
        dz = _try(errors, "deezer", lambda: deezer.artist(artist["deezer_id"]))
    else:
        dz = _try(errors, "deezer", lambda: _pick_deezer(name))
        if dz and (existing := db.find_by_deezer(dz["id"])):
            artist = existing
    if dz:
        fields.update(name=dz["name"], deezer_id=dz["id"], image_url=dz.get("picture_medium"))
    display = fields.get("name") or name

    manual_lf = (artist or {}).get("lastfm_source") == "manual"
    lf = _try(errors, "lastfm", lambda: lastfm.artist_info(display)) if lastfm.available() and not manual_lf else None
    if lf:
        fields.update(lastfm_name=lf["name"], mbid=lf["mbid"], genres=lf["tags"][:6])

    if deep:
        mbid = fields.get("mbid") or (artist or {}).get("mbid")
        if not mbid:
            hits = _try(errors, "musicbrainz", lambda: musicbrainz.search(display)) or []
            hit = next((h for h in hits if h.get("score", 0) >= 95 and h["name"].lower() == display.lower()), None)
            mbid = hit["id"] if hit else None
        mb = _try(errors, "musicbrainz", lambda: musicbrainz.lookup(mbid)) if mbid else None
        if mb:
            fields.update(mbid=mbid, country=mb["country"], career_start=mb["begin"], links=mb["links"])
            if mb["genres"] and not fields.get("genres"):
                fields["genres"] = mb["genres"][:6]
            ig = instagram.username_from_url(mb["links"].get("instagram", ""))
            if ig and (artist or {}).get("instagram_source") != "manual":
                fields.update(instagram_username=ig, instagram_source="musicbrainz")
            if wd := mb["links"].get("wikidata"):
                fields["wiki_title"] = _try(errors, "wikidata", lambda: wikipedia.enwiki_title_from_wikidata(wd))

        if youtube.available():
            yt_url = (mb or {}).get("links", {}).get("youtube", "")
            channel, source = _youtube_from_link(errors, yt_url, display) if yt_url else (None, None)
            if not channel:
                channel = _try(errors, "youtube", lambda: youtube.search_channel(display))
                source = "search" if channel else None
            if artist and artist.get("youtube_channel") and channel and channel != artist["youtube_channel"]:
                db.delete_metrics(artist["id"], "youtube.")
            fields.update(youtube_channel=channel, youtube_source=source)
        fields["resolved_at"] = db.now()

        if spotify.available():
            sp = _try(errors, "spotify", lambda: spotify.search_artist(display))
            if sp:
                fields["spotify_id"] = sp["id"]
                if sp.get("genres"):
                    fields["genres"] = list(dict.fromkeys([*sp["genres"], *(fields.get("genres") or [])]))[:8]
                if not fields.get("image_url") and sp.get("images"):
                    fields["image_url"] = sp["images"][0]["url"]

    if not fields and not artist:
        raise SourceError(f"Could not find '{name}' on any source. " + "; ".join(errors))
    artist_id = db.upsert_artist(fields, artist["id"] if artist else None)
    result = db.get_artist(artist_id)
    result["_errors"] = errors
    result["_deep_resolved"] = deep
    return result


# --------------------------------------------------------------------------- snapshots

def snapshot(artist_id: int) -> dict:
    """Pull current metrics from every linked source and store a timestamped snapshot."""
    a = db.get_artist(artist_id)
    errors = _Errors()
    m: dict[str, float | None] = {}
    rel: dict = {}

    if a.get("deezer_id"):
        dz = _try(errors, "deezer", lambda: deezer.artist(a["deezer_id"]))
        if dz:
            m["deezer.fans"], m["deezer.albums"] = dz.get("nb_fan"), dz.get("nb_album")
        top = _try(errors, "deezer top", lambda: deezer.top_tracks(a["deezer_id"], 5)) or []
        if top:
            m["deezer.top_track_rank"] = max(t.get("rank", 0) for t in top)
        rel = _try(errors, "deezer releases", lambda: _release_info(a["deezer_id"])) or {}
        m["releases_last_year"] = rel.get("releases_last_year")
        db.upsert_artist({"latest_label": rel.get("latest_label"),
                          "latest_release": rel.get("latest_release")}, artist_id)

    if lastfm.available():
        exact = a.get("lastfm_source") == "manual"
        lf = _try(errors, "lastfm", lambda: lastfm.artist_info(a.get("lastfm_name") or a["name"], autocorrect=not exact))
        if lf:
            m["lastfm.listeners"], m["lastfm.playcount"] = lf["listeners"], lf["playcount"]

    if youtube.available() and a.get("youtube_channel"):
        yt = _try(errors, "youtube", lambda: youtube.channel_stats(a["youtube_channel"]))
        if yt:
            m["youtube.subscribers"], m["youtube.views"], m["youtube.videos"] = (
                yt["subscribers"], yt["views"], yt["videos"])
            if yt.get("title") and yt["title"] != a.get("youtube_title"):
                db.upsert_artist({"youtube_title": yt["title"]}, artist_id)

    if instagram.available() and (ig_user := _instagram_username(a)):
        ig = _try(errors, "instagram", lambda: instagram.profile(ig_user))
        if ig:
            m["instagram.followers"], m["instagram.media_count"] = ig["followers"], ig["media_count"]
            found = management.from_bio(ig.get("biography"), ig.get("website"))
            if found:
                db.upsert_artist({"instagram_bio": ig.get("biography"), "mgmt_auto": found["status"],
                                  "mgmt_evidence": found["evidence"] or ""}, artist_id)

    if a.get("wiki_title"):
        m["wikipedia.pageviews_30d"] = _try(errors, "wikipedia", lambda: wikipedia.pageviews(a["wiki_title"]))

    if spotify.available() and a.get("spotify_id"):
        sp = _try(errors, "spotify", lambda: spotify.artist(a["spotify_id"]))
        if sp:  # only present for Extended Quota Mode apps
            m["spotify.followers"] = (sp.get("followers") or {}).get("total")
            m["spotify.popularity"] = sp.get("popularity")

    db.add_snapshot(artist_id, m)
    return {"artist_id": artist_id, "metrics": {k: v for k, v in m.items() if v is not None},
            "releases": rel, "errors": list(errors), "transient": errors.transient}


def relink_all(progress: Callable[[str], None] | None = None) -> dict:
    """Run the deep cross-source lookup for artists that never had it, then re-snapshot them.
    YouTube channel search costs 100 quota units per artist without a MusicBrainz YouTube link."""
    done, errors = 0, []
    for a in db.all_artists():
        if a.get("resolved_at"):
            continue
        if progress:
            progress(a["name"])
        try:
            r = resolve(a["name"], deep=True)
            errors += [f"{a['name']}: {e}" for e in r.get("_errors", [])]
            errors += [f"{a['name']}: {e}" for e in snapshot(r["id"])["errors"]]
            done += 1
        except SourceError as exc:
            errors.append(f"{a['name']}: {exc}")
    return {"relinked": done, "errors": errors}


def _span_days(cov: dict) -> float:
    if not cov.get("first"):
        return 0.0
    return (datetime.fromisoformat(cov["last"]) - datetime.fromisoformat(cov["first"])).total_seconds() / 86400


BACKUPS_KEPT = 14


def backup_database(keep: int = BACKUPS_KEPT) -> dict:
    """Save today's dated copy of the database (replacing an earlier one from today) and keep the newest `keep`."""
    folder = settings.backup_dir
    dest = folder / f"ar_scout-{date.today().isoformat()}.db"
    db.backup(dest)
    copies = sorted(folder.glob("ar_scout-????-??-??.db"))
    for old in copies[:-keep]:
        old.unlink()
    return {"path": str(dest), "kept": min(len(copies), keep), "size_mb": round(dest.stat().st_size / 1e6, 1)}


UPLOAD_MAX_BYTES = 500 * 1024 * 1024


def import_database(data: bytes) -> dict:
    """Replace this server's database with an uploaded copy (e.g. a laptop's, when first hosting). The current
    database is backed up first."""
    if len(data) > UPLOAD_MAX_BYTES:
        raise ValueError("That file is larger than 500 MB")
    if not data.startswith(b"SQLite format 3\x00"):
        raise ValueError("That file isn't a SQLite database")
    backup = backup_database() if settings.db_path.exists() else None
    tmp = settings.db_path.with_name(settings.db_path.name + ".upload")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        counts = db.replace_with(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    _score_cache.clear()  # the new file's version counter may match the old one's
    return counts | {"previous_backup": backup["path"] if backup else None}


def last_backup() -> str | None:
    copies = sorted(settings.backup_dir.glob("ar_scout-????-??-??.db"))
    return copies[-1].stem.removeprefix("ar_scout-") if copies else None


def data_health() -> dict:
    """How complete the tracked data is: unlinked artists, growth coverage, last snapshot, daily job."""
    coverage = db.snapshot_summary()
    artists = db.all_artists()
    return {
        "artists": len(artists),
        "unlinked": sum(1 for a in artists if not a.get("resolved_at")),
        "with_growth": sum(1 for c in coverage.values() if _span_days(c) >= scoring.MIN_GROWTH_DAYS),
        "min_growth_days": scoring.MIN_GROWTH_DAYS,
        "last_snapshot": max((c["last"] for c in coverage.values()), default=None),
        "daily_job": DAILY_JOB_PLIST.exists() or bool(os.getenv("AR_SCOUT_DAILY_AT")),
        "daily_job_at": os.getenv("AR_SCOUT_DAILY_AT") or ("09:00" if DAILY_JOB_PLIST.exists() else None),
        "archived": len(db.all_artists(include_archived=True)) - len(artists),
        "last_backup": last_backup(),
    }


def _last_snapshot_age_hours(artist_id: int) -> float | None:
    hist = db.history(artist_id)
    if not hist:
        return None
    last = datetime.fromisoformat(hist[-1]["taken_at"])
    return (datetime.now(timezone.utc) - last).total_seconds() / 3600


def snapshot_many(scope: str = "watchlist", stale_hours: float = STALE_HOURS,
                  progress: Callable[[str], None] | None = None, retry_wait: float = 30) -> dict:
    """scope: 'watchlist' | 'all'. Skips artists snapshotted within `stale_hours`. Artists whose lookup hit a
    network hiccup are tried once more at the end, after `retry_wait` seconds, so a brief Wi-Fi drop doesn't
    leave a gap in that day's numbers."""
    artists = db.all_artists(watchlist_only=(scope == "watchlist"))
    done, skipped, errors, again = 0, 0, {}, []
    for a in artists:
        age = _last_snapshot_age_hours(a["id"])
        if age is not None and age < stale_hours:
            skipped += 1
            continue
        if progress:
            progress(a["name"])
        res = snapshot(a["id"])
        done += 1
        if res["errors"]:
            errors[a["name"]] = res["errors"]
        if res["transient"]:
            again.append(a)
    if again:
        time.sleep(retry_wait)
        for a in again:
            if progress:
                progress(f"{a['name']} (retry)")
            res = snapshot(a["id"])
            if res["errors"]:
                errors[a["name"]] = res["errors"]
            else:
                errors.pop(a["name"], None)
    return {"snapshotted": done, "skipped_fresh": skipped, "retried": len(again), "errors": errors}


# --------------------------------------------------------------------------- scoring / reports

def _history_by_metric(artist_id: int) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for h in db.history(artist_id):
        out.setdefault(h["metric"], []).append((h["taken_at"], h["value"]))
    return out


# Review checks: signs that a linked account belongs to someone else. Tuned on the Oct 2026 data, where they
# flagged 6 of 134 artists, every one a real mismatch.
YT_PER_DEEZER_MAX = 100        # median act has ~6 YouTube subscribers per Deezer fan; 100+ means a different channel
LASTFM_PER_DEEZER_MIN = 3      # median ~59 Last.fm listeners per Deezer fan; under 3 means a different Last.fm artist
JUMP_FACTOR = 3                # one number tripling (or dropping to a third) between snapshots is an account change
_JUMP_METRICS = {"deezer.fans": "Deezer fans", "lastfm.listeners": "Last.fm listeners",
                 "youtube.subscribers": "YouTube subscribers", "instagram.followers": "Instagram followers"}


def review_findings(artist: dict, metrics: dict, history: dict[str, list[tuple[str, float]]]) -> list[tuple[str, str]]:
    """(platform, plain-language reason) for each sign that a linked account belongs to someone else. Jumps from
    before the artist was marked verified are left out; the other checks always run, since verifying says they
    were looked at."""
    found: list[tuple[str, str]] = []
    dz, lf, yt = metrics.get("deezer.fans"), metrics.get("lastfm.listeners"), metrics.get("youtube.subscribers")
    if scoring.youtube_suspect(metrics):
        found.append(("youtube", "YouTube channel looks too small for this act (or has no videos)"))
    if dz and yt and yt > 20_000 and yt / dz > YT_PER_DEEZER_MAX:
        found.append(("youtube", f"YouTube channel looks far too big for this act: {yt:,.0f} subscribers vs "
                                 f"{dz:,.0f} Deezer fans"))
    if dz and lf and dz >= 500 and lf < LASTFM_PER_DEEZER_MIN * dz and artist.get("lastfm_source") != "manual":
        found.append(("lastfm", f"Last.fm looks too small for this act ({lf:,.0f} listeners vs {dz:,.0f} Deezer "
                                "fans): it may be a different artist with the same name"))
    lf_name = artist.get("lastfm_name")
    if lf_name and artist.get("lastfm_source") != "manual" and youtube._key(lf_name) != youtube._key(artist["name"]):
        found.append(("lastfm", f"Last.fm matched a different name, “{lf_name}”: Last.fm's autocorrect may have "
                                "picked another artist"))
    title = artist.get("youtube_title")
    if title and artist.get("youtube_source") != "manual" and not youtube.title_matches(title, artist["name"]):
        found.append(("youtube", f"YouTube channel is called “{title}”, which doesn't match the artist's name"))
    since = (artist.get("verified_at") or "")[:10]
    for metric, label in _JUMP_METRICS.items():
        pts = history.get(metric, [])
        for (_, before), (when, after) in zip(pts, pts[1:]):
            if before > 0 and when[:10] > since and (after / before > JUMP_FACTOR or after / before < 1 / JUMP_FACTOR):
                found.append((metric.split(".")[0], f"{label} jumped from {before:,.0f} to {after:,.0f} on "
                                                    f"{when[:10]}: likely an account change, not real growth"))
                break
    return found


def review_issues(artist: dict, metrics: dict, history: dict[str, list[tuple[str, float]]]) -> list[str]:
    return [text for _, text in review_findings(artist, metrics, history)]


PLATFORM_NAMES = {"youtube": "YouTube", "lastfm": "Last.fm", "deezer": "Deezer", "instagram": "Instagram"}


def score_artist(artist: dict, window_days: int = 30) -> dict:
    metrics = db.latest_metrics(artist["id"])
    history = _history_by_metric(artist["id"])
    label = artist.get("latest_label")
    findings = review_findings(artist, metrics, history)
    # A flagged platform's numbers stay on the page but don't count until a person fixes or verifies the artist.
    excluded = sorted({p for p, _ in findings}) if not artist.get("verified_at") else []
    keep = lambda k: not any(k.startswith(p + ".") for p in excluded)
    result = scoring.score(
        {k: v for k, v in metrics.items() if keep(k)}, {k: v for k, v in history.items() if keep(k)},
        latest_release=artist.get("latest_release"),
        releases_last_year=int(metrics["releases_last_year"]) if "releases_last_year" in metrics else None,
        label_class=labels.classify(label, artist.get("name")),
        window_days=window_days,
    )
    if excluded:
        result["flags"].insert(0, "left out of the score until checked: "
                               + ", ".join(PLATFORM_NAMES.get(p, p) for p in excluded))
    return result | {"metrics": metrics, "review": [t for _, t in findings], "excluded": excluded,
                     "change_7d": {k: scoring.window_change(history.get(m, []), 7) for k, m in MOVER_METRICS.items()}}


CHANGE_WINDOWS = (7, 28)
MOVER_METRICS = {"youtube": "youtube.subscribers", "lastfm": "lastfm.listeners", "deezer": "deezer.fans",
                 "instagram": "instagram.followers", "tiktok": "tiktok.followers", "spotify": "spotify.monthly_listeners"}


def window_changes(artist_id: int, history: dict | None = None) -> dict:
    """{metric: {"7": change or None, "28": change or None, "28_from": date the 28-day figure starts}}."""
    history = history if history is not None else _history_by_metric(artist_id)
    out = {}
    for metric, pts in history.items():
        if not pts:
            continue
        out[metric] = {str(d): scoring.window_change(pts, d) for d in CHANGE_WINDOWS}
        first = datetime.fromisoformat(pts[0][0])
        out[metric]["28_from"] = (first + timedelta(days=28)).date().isoformat()
    return out


PIPELINE_STATUSES = ("watching", "contacted", "meeting", "signed", "passed")


def update_pipeline(artist_id: int, status: str | None = None, **fields: str | None) -> dict:
    """Move a watched artist to another stage and/or set owner, next_step, next_date (YYYY-MM-DD)."""
    entry = db.watch_entry(artist_id)
    if not entry:
        raise SourceError("Add this artist to the watchlist first")
    if status is not None:
        if status not in PIPELINE_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(PIPELINE_STATUSES)}")
        db.watch(artist_id, status, [])
    due = fields.get("next_date")
    if due:
        try:
            date.fromisoformat(due)
        except ValueError:
            raise ValueError("next_date must look like 2026-11-03") from None
    db.update_pipeline(artist_id, **fields)
    return db.watch_entry(artist_id)


def set_archived(artist_id: int, archived: bool = True) -> dict:
    """Archive (hide from rankings, review and daily snapshots; keep history) or restore an artist."""
    if not db.get_artist(artist_id):
        raise SourceError(f"No artist with id {artist_id}")
    db.set_archived(artist_id, db.now() if archived else None)
    return {"artist_id": artist_id, "archived_at": db.get_artist(artist_id).get("archived_at")}


def delete_artist(artist_id: int) -> dict:
    """Permanently delete an artist and all their history. The nightly backup is the only way back."""
    a = db.get_artist(artist_id)
    if not a:
        raise SourceError(f"No artist with id {artist_id}")
    db.delete_artist(artist_id)
    return {"deleted": a["name"]}


def set_management(artist_id: int, status: str | None) -> dict:
    """status: 'managed', 'unmanaged', or None to go back to what the Instagram bio says."""
    if status not in (None, "managed", "unmanaged"):
        raise ValueError("status must be managed, unmanaged or empty")
    if not db.get_artist(artist_id):
        raise SourceError(f"No artist with id {artist_id}")
    db.set_management(artist_id, status)
    return management_info(db.get_artist(artist_id))


def management_info(a: dict) -> dict:
    return management.resolve(a.get("management"), a.get("mgmt_auto"), a.get("mgmt_evidence"))


def set_verified(artist_id: int, verified: bool = True) -> dict:
    """Record that a person checked this artist's linked accounts (or undo it)."""
    if not db.get_artist(artist_id):
        raise SourceError(f"No artist with id {artist_id}")
    db.set_verified(artist_id, db.now() if verified else None)
    return {"artist_id": artist_id, "verified_at": db.get_artist(artist_id).get("verified_at")}


def review_queue() -> list[dict]:
    """Artists whose linked accounts look wrong and that nobody has verified yet, most-watched first."""
    rows = [r for r in leaderboard(limit=100_000) if r["review"] and not r["verified"]]
    rows.sort(key=lambda r: (r["status"] is None, -r["score"]))
    return rows


def report(name: str, refresh: bool = True) -> dict:
    artist = resolve(name, deep=True)
    errors = artist.pop("_errors", [])
    just_linked = artist.pop("_deep_resolved", False)  # new IDs (e.g. YouTube) aren't in the last snapshot yet
    snap = None
    age = _last_snapshot_age_hours(artist["id"])
    if refresh and (age is None or age > SNAPSHOT_FRESH_HOURS or just_linked):
        snap = snapshot(artist["id"])
        errors += snap["errors"]
    artist = db.get_artist(artist["id"])
    artist["instagram_username"] = _instagram_username(artist)
    artist["label_class"] = labels.classify(artist.get("latest_label"), artist["name"])
    artist["management_info"] = management_info(artist)
    artist["watching"] = bool(db.watch_entry(artist["id"]))
    rel = snap["releases"] if snap else (_release_info(artist["deezer_id"]) if artist.get("deezer_id") else {})

    similar = []
    if artist.get("deezer_id"):
        similar = [{"name": r["name"], "deezer_fans": r.get("nb_fan")}
                   for r in (_try(errors, "deezer related", lambda: deezer.related(artist["deezer_id"], 12)) or [])]

    return {
        "artist": artist,
        "score": score_artist(artist),
        "releases": rel,
        "similar_artists": similar,
        "watchlist": db.watch_entry(artist["id"]),
        "notes": db.notes(artist["id"]),
        "history_points": len(db.history(artist["id"])),
        "changes": window_changes(artist["id"]),
        "errors": errors,
    }


def leaderboard(limit: int = 25, watchlist_only: bool = False, window_days: int = 30,
                max_audience: float | None = None, genre: str | None = None,
                audience_platform: str = "any", archived: bool = False) -> list[dict]:
    """audience_platform: 'any' applies max_audience to the largest of Deezer fans, Last.fm listeners,
    YouTube subscribers and Instagram followers; 'youtube' / 'lastfm' / 'deezer' / 'instagram' apply it to that
    platform only and drop artists with no data there (unknown size isn't 'under')."""
    if audience_platform != "any" and audience_platform not in AUDIENCE_METRICS:
        raise ValueError(f"audience_platform must be 'any' or one of {', '.join(AUDIENCE_METRICS)}")
    rows = []
    for row, metrics, genres in _scored(watchlist_only, window_days, archived):
        if genre and not any(genre.lower() in g.lower() for g in genres):
            continue
        if max_audience:
            if audience_platform == "any":
                audience = max((metrics.get(k) or 0) for k in AUDIENCE_METRICS.values())
            else:
                audience = metrics.get(AUDIENCE_METRICS[audience_platform])
            if audience is None or audience > max_audience:
                continue
        rows.append(row)
    return rows[:limit]


# Scoring every artist is the slow part of a page load, so it's cached until the data changes (db.data_version,
# bumped by triggers from any process) or the day turns over (release recency depends on today's date).
_score_cache: dict[tuple, list] = {}


def _all_ids() -> set[int]:
    return {a["id"] for a in db.all_artists()}


def _scored(watchlist_only: bool, window_days: int, archived: bool) -> list[tuple[dict, dict, list[str]]]:
    """(row, latest metrics, all genres) for every artist in scope, best score first."""
    version = db.data_version()
    key = (str(settings.db_path), version, date.today().isoformat(), watchlist_only, window_days, archived)
    if key in _score_cache:
        return _score_cache[key]
    for old in [k for k in _score_cache if k[:3] != key[:3]]:
        del _score_cache[old]
    coverage = db.snapshot_summary()
    out = []
    pool = db.all_artists(watchlist_only=watchlist_only, include_archived=archived)
    for a in (x for x in pool if not archived or x.get("archived_at")):
        s = score_artist(a, window_days)
        cov = coverage.get(a["id"], {})
        out.append(({
            "id": a["id"], "name": a["name"], "status": a.get("status"), "genres": (a.get("genres") or [])[:3],
            "country": a.get("country"), "score": s["score"], "confidence": s["confidence"],
            "components": s["components"], "deezer_fans": s["metrics"].get("deezer.fans"),
            "lastfm_listeners": s["metrics"].get("lastfm.listeners"),
            "youtube_subscribers": s["metrics"].get("youtube.subscribers"),
            "youtube_views": s["metrics"].get("youtube.views"),
            "instagram_followers": s["metrics"].get("instagram.followers"),
            "lastfm_playcount": s["metrics"].get("lastfm.playcount"),
            "image_url": a.get("image_url"),
            "monthly_growth": {k: round(g["monthly_rate"], 3) for k, g in s["growth"].items() if g["monthly_rate"] is not None},
            "latest_release": a.get("latest_release"), "label": a.get("latest_label"),
            "label_class": labels.classify(a.get("latest_label"), a.get("name")),
            "flags": s["flags"], "discovered_via": a.get("discovered_via"),
            "tags": json.loads(a.get("watch_tags") or "[]"),
            "owner": a.get("owner"), "next_step": a.get("next_step"), "next_date": a.get("next_date"),
            "status_since": a.get("status_since") or a.get("watch_added"),
            "review": s["review"], "verified": bool(a.get("verified_at")), "verified_at": a.get("verified_at"),
            "accelerating": s["acceleration"]["accelerating"], "archived_at": a.get("archived_at"),
            "excluded": s["excluded"],
            "management": management_info(a),
            "change_7d": {k: ({"delta": c["delta"], "pct": c["pct"], "start": c["start"]} if c else None)
                          for k, c in s["change_7d"].items()},
            "linked": bool(a.get("resolved_at")), "youtube_linked": bool(a.get("youtube_channel")),
            "snapshot_days": cov.get("days", 0), "last_snapshot": cov.get("last"),
        }, s["metrics"], a.get("genres") or []))
    out.sort(key=lambda x: x[0]["score"], reverse=True)
    _score_cache[key] = out
    return out


# --------------------------------------------------------------------------- discovery

def _filter_track(candidates: list[dict], via: str, min_fans: int, max_fans: int, limit: int,
                  progress: Callable[[str], None] | None) -> list[dict]:
    seen, picked = set(), []
    for dz in candidates:
        if dz["id"] in seen:
            continue
        seen.add(dz["id"])
        fans = dz.get("nb_fan")
        if fans is None:
            dz = deezer.artist(dz["id"]) or dz
            fans = dz.get("nb_fan", 0)
        if not (min_fans <= fans <= max_fans):
            continue
        known = db.find_by_deezer(dz["id"])
        if known and known.get("archived_at"):  # archived on purpose: don't bring them back
            continue
        if progress:
            progress(dz["name"])
        track_light(dz, via)
        picked.append(dz["id"])
        if len(picked) >= limit:
            break
    ids = [db.find_by_deezer(i)["id"] for i in picked]
    rows = [r for r in leaderboard(limit=10_000) if r["id"] in ids]
    return rows


def _deezer_for_names(names: list[str]) -> list[dict]:
    out = []
    for n in names:
        hit = _pick_deezer(n)
        if hit and hit["name"].lower() == n.lower():
            out.append(hit)
    return out


def discover_similar(seeds: list[str], min_fans: int = 300, max_fans: int = 50_000, hops: int = 1,
                     limit: int = 25, progress: Callable[[str], None] | None = None) -> list[dict]:
    """Artists adjacent to the seeds but smaller. Artists linked to several seeds rank first."""
    overlap: Counter[int] = Counter()
    pool: dict[int, dict] = {}
    seed_ids = set()
    frontier = []
    for s in seeds:
        dz = _pick_deezer(s)
        if dz:
            seed_ids.add(dz["id"])
            frontier.append(dz)
    for _ in range(max(1, hops)):
        nxt = []
        for node in frontier:
            neighbours = deezer.related(node["id"], 50)
            if lastfm.available():
                try:
                    names = [x["name"] for x in lastfm.similar(node["name"], 30) if x["match"] > 0.2]
                    neighbours += _deezer_for_names(names[:15])
                except SourceError:
                    pass
            for r in neighbours:
                if r["id"] in seed_ids:
                    continue
                overlap[r["id"]] += 1
                pool.setdefault(r["id"], r)
                nxt.append(r)
        frontier = nxt[:40]
    ordered = sorted(pool.values(), key=lambda r: (-overlap[r["id"]], r.get("nb_fan") or 0))
    return _filter_track(ordered, f"similar:{','.join(seeds)}", min_fans, max_fans, limit, progress)


def discover_by_tag(tag: str, min_fans: int = 300, max_fans: int = 50_000, start_page: int = 2,
                    pages: int = 4, limit: int = 25, progress: Callable[[str], None] | None = None) -> list[dict]:
    """Last.fm tag charts; deeper pages surface smaller artists. Needs LASTFM_API_KEY."""
    names = []
    for p in range(start_page, start_page + pages):
        names += [a["name"] for a in lastfm.tag_top_artists(tag, page=p)]
    return _filter_track(_deezer_for_names(names), f"tag:{tag}", min_fans, max_fans, limit, progress)


def discover_by_country(country: str, min_fans: int = 300, max_fans: int = 50_000, start_page: int = 3,
                        pages: int = 4, limit: int = 25, progress: Callable[[str], None] | None = None) -> list[dict]:
    """Last.fm geo charts (country by full English name, e.g. 'United Kingdom')."""
    names = []
    for p in range(start_page, start_page + pages):
        names += [a["name"] for a in lastfm.geo_top_artists(country, page=p)]
    return _filter_track(_deezer_for_names(names), f"country:{country}", min_fans, max_fans, limit, progress)


def discover_playlists(query: str, playlists: int = 5, min_fans: int = 300, max_fans: int = 50_000,
                       limit: int = 25, progress: Callable[[str], None] | None = None) -> list[dict]:
    """Mine Deezer playlists matching `query` (e.g. 'new rap', 'fresh indie', 'uk drill new').
    Artists appearing on several matching playlists are evaluated first."""
    counts: Counter[int] = Counter()
    pool: dict[int, dict] = {}
    for pl in deezer.search_playlists(query, limit=playlists):
        for t in deezer.playlist_tracks(pl["id"]):
            if a := t.get("artist"):
                counts[a["id"]] += 1
                pool.setdefault(a["id"], a)
    ordered = sorted(pool.values(), key=lambda a: -counts[a["id"]])
    return _filter_track(ordered, f"playlists:{query}", min_fans, max_fans, limit, progress)


def discover_new_releases(genre: str | None = None, min_fans: int = 200, max_fans: int = 50_000,
                          limit: int = 25, progress: Callable[[str], None] | None = None) -> list[dict]:
    """Smaller artists from Deezer editorial selections plus 'new <genre>' playlists."""
    gid = (deezer.find_genre_id(genre) if genre else 0) or 0
    counts: Counter[int] = Counter()
    pool: dict[int, dict] = {}
    for album in deezer.editorial_selection(gid):
        if a := album.get("artist"):
            counts[a["id"]] += 2
            pool.setdefault(a["id"], a)
    for pl in deezer.search_playlists(f"new {genre or 'music'}", limit=4):
        for t in deezer.playlist_tracks(pl["id"]):
            if a := t.get("artist"):
                counts[a["id"]] += 1
                pool.setdefault(a["id"], a)
    ordered = sorted(pool.values(), key=lambda a: -counts[a["id"]])
    return _filter_track(ordered, f"new_releases:{genre or 'all'}", min_fans, max_fans, limit, progress)


def source_status() -> dict:
    return settings.source_status()
