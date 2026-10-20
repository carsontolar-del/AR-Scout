"""SQLite storage: artist identities, time-series metric snapshots, watchlist, notes."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Iterator

from . import tags
from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    deezer_id       INTEGER UNIQUE,
    lastfm_name     TEXT,
    mbid            TEXT,
    spotify_id      TEXT,
    youtube_channel TEXT,
    wiki_title      TEXT,
    country         TEXT,
    career_start    TEXT,
    genres          TEXT DEFAULT '[]',
    links           TEXT DEFAULT '{}',
    image_url       TEXT,
    latest_label    TEXT,
    latest_release  TEXT,
    discovered_via  TEXT,
    resolved_at     TEXT,           -- set once the deep cross-source lookup (MusicBrainz, YouTube...) has run
    youtube_source  TEXT,           -- musicbrainz | search | manual
    instagram_username TEXT,
    instagram_source   TEXT,        -- musicbrainz | manual
    youtube_title   TEXT,           -- channel name, to spot a channel that belongs to someone else
    verified_at     TEXT,           -- when a person confirmed this artist's linked accounts are right
    instagram_bio   TEXT,
    mgmt_auto       TEXT,           -- listed | not_listed, read from the Instagram bio
    mgmt_evidence   TEXT,           -- the bio line that mentions management
    management      TEXT,           -- managed | unmanaged, set by a person (wins over mgmt_auto)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artists_name ON artists(lower(name));

CREATE TABLE IF NOT EXISTS snapshots (
    artist_id  INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    taken_at   TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(artist_id, metric, taken_at);

CREATE TABLE IF NOT EXISTS watchlist (
    artist_id  INTEGER PRIMARY KEY REFERENCES artists(id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'watching',  -- watching | contacted | meeting | passed | signed
    tags       TEXT DEFAULT '[]',
    added_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    artist_id  INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

JSON_FIELDS = ("genres", "links")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _prepare(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_prepared: set[str] = set()
_prepare_lock = threading.Lock()


def _prepare(conn: sqlite3.Connection) -> None:
    """Create and upgrade the schema once per database per process. The dashboard opens several connections at
    once, so without the lock two of them could both try to add the same new column."""
    key = str(settings.db_path)
    if key in _prepared:
        return
    with _prepare_lock:
        if key in _prepared:
            return
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _prepared.add(key)


_ADDED_COLUMNS = ("resolved_at", "youtube_source", "instagram_username", "instagram_source", "youtube_title",
                  "verified_at", "instagram_bio", "mgmt_auto", "mgmt_evidence", "management")


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(artists)")}
    for col in _ADDED_COLUMNS:
        if col in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE artists ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError as exc:  # another process (e.g. the daily job) added it first
            if "duplicate column" not in str(exc):
                raise


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d and isinstance(d[f], str):
            d[f] = json.loads(d[f] or ("[]" if f == "genres" else "{}"))
    return d


def get_artist(artist_id: int) -> dict | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone())


def find_artist(name: str) -> dict | None:
    with connect() as c:
        return _row(c.execute(
            "SELECT * FROM artists WHERE lower(name) = lower(?) OR lower(lastfm_name) = lower(?) "
            "ORDER BY id LIMIT 1", (name, name)).fetchone())


def find_by_deezer(deezer_id: int) -> dict | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM artists WHERE deezer_id = ?", (deezer_id,)).fetchone())


def upsert_artist(fields: dict[str, Any], artist_id: int | None = None) -> int:
    if fields.get("genres") is not None:
        fields = {**fields, "genres": tags.clean(fields["genres"])}
    fields = {k: (json.dumps(v) if k in JSON_FIELDS else v) for k, v in fields.items() if v is not None}
    fields["updated_at"] = now()
    with connect() as c:
        if artist_id is None:
            fields.setdefault("created_at", fields["updated_at"])
            cols = ", ".join(fields)
            cur = c.execute(f"INSERT INTO artists ({cols}) VALUES ({', '.join('?' * len(fields))})",
                            list(fields.values()))
            return cur.lastrowid
        sets = ", ".join(f"{k} = ?" for k in fields)
        c.execute(f"UPDATE artists SET {sets} WHERE id = ?", [*fields.values(), artist_id])
        return artist_id


def add_snapshot(artist_id: int, metrics: dict[str, float | None], taken_at: str | None = None) -> None:
    taken_at = taken_at or now()
    rows = [(artist_id, taken_at, m, float(v)) for m, v in metrics.items() if v is not None]
    with connect() as c:
        c.executemany("INSERT INTO snapshots VALUES (?, ?, ?, ?)", rows)


def history(artist_id: int, metric: str | None = None) -> list[dict]:
    q = "SELECT taken_at, metric, value FROM snapshots WHERE artist_id = ?"
    args: list[Any] = [artist_id]
    if metric:
        q += " AND metric = ?"
        args.append(metric)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY taken_at", args)]


def set_management(artist_id: int, status: str | None) -> None:
    with connect() as c:
        c.execute("UPDATE artists SET management = ?, updated_at = ? WHERE id = ?", (status, now(), artist_id))


def set_verified(artist_id: int, when: str | None) -> None:
    """upsert_artist skips None, so clearing needs its own statement."""
    with connect() as c:
        c.execute("UPDATE artists SET verified_at = ?, updated_at = ? WHERE id = ?", (when, now(), artist_id))


def delete_metrics(artist_id: int, prefix: str) -> None:
    """Drop an artist's snapshots for metrics starting with `prefix` (e.g. 'youtube.' after a channel change)."""
    with connect() as c:
        c.execute("DELETE FROM snapshots WHERE artist_id = ? AND substr(metric, 1, ?) = ?",
                  (artist_id, len(prefix), prefix))


def latest_metrics(artist_id: int) -> dict[str, float]:
    with connect() as c:
        rows = c.execute(
            "SELECT metric, value FROM snapshots s WHERE artist_id = ? AND taken_at = "
            "(SELECT max(taken_at) FROM snapshots WHERE artist_id = s.artist_id AND metric = s.metric)",
            (artist_id,)).fetchall()
    return {r["metric"]: r["value"] for r in rows}


def backup(dest: Path) -> None:
    """Consistent copy of the database to `dest`, safe while the dashboard or a snapshot run is using it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".partial")
    src = sqlite3.connect(settings.db_path)
    try:
        out = sqlite3.connect(tmp)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    tmp.replace(dest)


def snapshot_summary() -> dict[int, dict]:
    """artist_id -> {days: distinct UTC days with snapshots, first/last: earliest/latest taken_at}."""
    with connect() as c:
        rows = c.execute("SELECT artist_id, count(DISTINCT substr(taken_at, 1, 10)) AS days, "
                         "min(taken_at) AS first, max(taken_at) AS last FROM snapshots GROUP BY artist_id").fetchall()
    return {r["artist_id"]: {"days": r["days"], "first": r["first"], "last": r["last"]} for r in rows}


def all_artists(watchlist_only: bool = False) -> list[dict]:
    q = ("SELECT a.*, w.status, w.tags AS watch_tags FROM artists a "
         + ("JOIN" if watchlist_only else "LEFT JOIN") + " watchlist w ON w.artist_id = a.id")
    with connect() as c:
        return [_row(r) for r in c.execute(q)]


def watch(artist_id: int, status: str = "watching", tags: list[str] | None = None) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO watchlist (artist_id, status, tags, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(artist_id) DO UPDATE SET status = excluded.status, "
            "tags = CASE WHEN excluded.tags = '[]' THEN watchlist.tags ELSE excluded.tags END",
            (artist_id, status, json.dumps(tags or []), now()))


def unwatch(artist_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM watchlist WHERE artist_id = ?", (artist_id,))


def watch_entry(artist_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM watchlist WHERE artist_id = ?", (artist_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["tags"] = json.loads(d["tags"] or "[]")
    return d


def add_note(artist_id: int, body: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO notes (artist_id, body, created_at) VALUES (?, ?, ?)", (artist_id, body, now()))


def notes(artist_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT body, created_at FROM notes WHERE artist_id = ? ORDER BY created_at DESC", (artist_id,))]
