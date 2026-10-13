"""High-level operations shared by the CLI, MCP server and web dashboard."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import db, scoring
from .config import settings
from .http import SourceError
from .sources import deezer, lastfm, musicbrainz, spotify, wikipedia, youtube

SNAPSHOT_FRESH_HOURS = 12
# Written by scripts/install-daily-snapshot.sh
DAILY_JOB_PLIST = Path.home() / "Library/LaunchAgents/com.ar-scout.snapshot.plist"
AUDIENCE_METRICS = {"deezer": "deezer.fans", "lastfm": "lastfm.listeners", "youtube": "youtube.subscribers"}


def _try(errors: list[str], label: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except SourceError as exc:
        errors.append(f"{label}: {exc}")
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
        info = _try(errors, "lastfm", lambda: lastfm.artist_info(dz["name"]))
        if info:
            metrics["lastfm.listeners"] = info["listeners"]
            metrics["lastfm.playcount"] = info["playcount"]
            db.upsert_artist({"lastfm_name": info["name"], "mbid": info["mbid"],
                              "genres": info["tags"][:6]}, artist_id)
    db.add_snapshot(artist_id, metrics)
    return artist_id


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

    lf = _try(errors, "lastfm", lambda: lastfm.artist_info(display)) if lastfm.available() else None
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
            if wd := mb["links"].get("wikidata"):
                fields["wiki_title"] = _try(errors, "wikidata", lambda: wikipedia.enwiki_title_from_wikidata(wd))

        if youtube.available():
            yt_url = (mb or {}).get("links", {}).get("youtube", "")
            channel = youtube.channel_id_from_url(yt_url)
            if not channel and (handle := youtube.handle_from_url(yt_url)):
                channel = _try(errors, "youtube", lambda: youtube.channel_by_handle(handle))
            if not channel:
                channel = _try(errors, "youtube", lambda: youtube.search_channel(display))
            fields["youtube_channel"] = channel
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
    errors: list[str] = []
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
        lf = _try(errors, "lastfm", lambda: lastfm.artist_info(a.get("lastfm_name") or a["name"]))
        if lf:
            m["lastfm.listeners"], m["lastfm.playcount"] = lf["listeners"], lf["playcount"]

    if youtube.available() and a.get("youtube_channel"):
        yt = _try(errors, "youtube", lambda: youtube.channel_stats(a["youtube_channel"]))
        if yt:
            m["youtube.subscribers"], m["youtube.views"], m["youtube.videos"] = (
                yt["subscribers"], yt["views"], yt["videos"])

    if a.get("wiki_title"):
        m["wikipedia.pageviews_30d"] = _try(errors, "wikipedia", lambda: wikipedia.pageviews(a["wiki_title"]))

    if spotify.available() and a.get("spotify_id"):
        sp = _try(errors, "spotify", lambda: spotify.artist(a["spotify_id"]))
        if sp:  # only present for Extended Quota Mode apps
            m["spotify.followers"] = (sp.get("followers") or {}).get("total")
            m["spotify.popularity"] = sp.get("popularity")

    db.add_snapshot(artist_id, m)
    return {"artist_id": artist_id, "metrics": {k: v for k, v in m.items() if v is not None},
            "releases": rel, "errors": errors}


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


def data_health() -> dict:
    """How complete the tracked data is: unlinked artists, growth coverage, last snapshot, daily job."""
    coverage = db.snapshot_summary()
    artists = db.all_artists()
    return {
        "artists": len(artists),
        "unlinked": sum(1 for a in artists if not a.get("resolved_at")),
        "with_growth": sum(1 for c in coverage.values() if c["days"] >= 2),
        "last_snapshot": max((c["last"] for c in coverage.values()), default=None),
        "daily_job": DAILY_JOB_PLIST.exists(),
    }


def _last_snapshot_age_hours(artist_id: int) -> float | None:
    hist = db.history(artist_id)
    if not hist:
        return None
    last = datetime.fromisoformat(hist[-1]["taken_at"])
    return (datetime.now(timezone.utc) - last).total_seconds() / 3600


def snapshot_many(scope: str = "watchlist", stale_hours: float = 20,
                  progress: Callable[[str], None] | None = None) -> dict:
    """scope: 'watchlist' | 'all'. Skips artists snapshotted within `stale_hours`."""
    artists = db.all_artists(watchlist_only=(scope == "watchlist"))
    done, skipped, errors = 0, 0, {}
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
    return {"snapshotted": done, "skipped_fresh": skipped, "errors": errors}


# --------------------------------------------------------------------------- scoring / reports

def _history_by_metric(artist_id: int) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for h in db.history(artist_id):
        out.setdefault(h["metric"], []).append((h["taken_at"], h["value"]))
    return out


def score_artist(artist: dict, window_days: int = 30) -> dict:
    metrics = db.latest_metrics(artist["id"])
    label = artist.get("latest_label")
    return scoring.score(
        metrics, _history_by_metric(artist["id"]),
        latest_release=artist.get("latest_release"),
        releases_last_year=int(metrics["releases_last_year"]) if "releases_last_year" in metrics else None,
        diy_label=deezer.is_diy_label(label) if label else None,
        window_days=window_days,
    ) | {"metrics": metrics}


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
        "errors": errors,
    }


def leaderboard(limit: int = 25, watchlist_only: bool = False, window_days: int = 30,
                max_audience: float | None = None, genre: str | None = None,
                audience_platform: str = "any") -> list[dict]:
    """audience_platform: 'any' applies max_audience to the largest of Deezer fans, Last.fm listeners and
    YouTube subscribers; 'youtube' / 'lastfm' / 'deezer' apply it to that platform only and drop artists
    with no data there (unknown size isn't 'under')."""
    if audience_platform != "any" and audience_platform not in AUDIENCE_METRICS:
        raise ValueError(f"audience_platform must be 'any' or one of {', '.join(AUDIENCE_METRICS)}")
    coverage = db.snapshot_summary()
    rows = []
    for a in db.all_artists(watchlist_only=watchlist_only):
        if genre and not any(genre.lower() in g.lower() for g in a.get("genres") or []):
            continue
        s = score_artist(a, window_days)
        if max_audience:
            if audience_platform == "any":
                audience = max((s["metrics"].get(k) or 0) for k in AUDIENCE_METRICS.values())
            else:
                audience = s["metrics"].get(AUDIENCE_METRICS[audience_platform])
            if audience is None or audience > max_audience:
                continue
        cov = coverage.get(a["id"], {})
        rows.append({
            "id": a["id"], "name": a["name"], "status": a.get("status"), "genres": (a.get("genres") or [])[:3],
            "country": a.get("country"), "score": s["score"], "confidence": s["confidence"],
            "components": s["components"], "deezer_fans": s["metrics"].get("deezer.fans"),
            "lastfm_listeners": s["metrics"].get("lastfm.listeners"),
            "youtube_subscribers": s["metrics"].get("youtube.subscribers"),
            "youtube_views": s["metrics"].get("youtube.views"),
            "lastfm_playcount": s["metrics"].get("lastfm.playcount"),
            "image_url": a.get("image_url"),
            "monthly_growth": {k: round(g["monthly_rate"], 3) for k, g in s["growth"].items() if g["monthly_rate"] is not None},
            "latest_release": a.get("latest_release"), "label": a.get("latest_label"),
            "flags": s["flags"], "discovered_via": a.get("discovered_via"),
            "tags": json.loads(a.get("watch_tags") or "[]"),
            "linked": bool(a.get("resolved_at")), "youtube_linked": bool(a.get("youtube_channel")),
            "snapshot_days": cov.get("days", 0), "last_snapshot": cov.get("last"),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]


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
