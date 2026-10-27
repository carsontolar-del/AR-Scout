import dataclasses

import pytest

from ar_scout import config, db, service
from ar_scout.http import SourceError, TransientSourceError


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


def test_network_errors_are_retried_once_at_the_end(tmp_db, monkeypatch):
    flaky = db.upsert_artist({"name": "Flaky", "deezer_id": 1})
    solid = db.upsert_artist({"name": "Solid", "deezer_id": 2})
    calls = []

    def fake_artist(deezer_id):
        calls.append(deezer_id)
        if deezer_id == 1 and calls.count(1) == 1:
            raise TransientSourceError("api.deezer.com: [Errno 8] nodename nor servname provided")
        return {"nb_fan": 100, "nb_album": 1}

    monkeypatch.setattr(service.deezer, "artist", fake_artist)
    monkeypatch.setattr(service.deezer, "top_tracks", lambda *_: [])
    monkeypatch.setattr(service, "_release_info", lambda _id: {})
    for mod in (service.lastfm, service.youtube, service.spotify, service.instagram):
        monkeypatch.setattr(mod, "available", lambda: False)

    res = service.snapshot_many("all", retry_wait=0)
    assert res["retried"] == 1 and res["errors"] == {}
    assert calls == [1, 2, 1]
    assert db.latest_metrics(flaky)["deezer.fans"] == 100
    assert db.latest_metrics(solid)["deezer.fans"] == 100


def test_permanent_errors_are_not_retried(tmp_db, monkeypatch):
    db.upsert_artist({"name": "Gone", "deezer_id": 1})
    monkeypatch.setattr(service.deezer, "artist", lambda _id: (_ for _ in ()).throw(SourceError("HTTP 400")))
    monkeypatch.setattr(service.deezer, "top_tracks", lambda *_: [])
    monkeypatch.setattr(service, "_release_info", lambda _id: {})
    for mod in (service.lastfm, service.youtube, service.spotify, service.instagram):
        monkeypatch.setattr(mod, "available", lambda: False)

    res = service.snapshot_many("all", retry_wait=0)
    assert res["retried"] == 0 and "Gone" in res["errors"]


def test_bulk_runs_skip_only_the_last_six_hours():
    assert service.STALE_HOURS == 6


def test_backup_keeps_a_dated_copy_and_prunes_old_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))
    monkeypatch.setattr(service, "settings", dataclasses.replace(config.settings, backup_dir=tmp_path / "bk"))
    artist_id = db.upsert_artist({"name": "Band"})
    db.add_snapshot(artist_id, {"deezer.fans": 5})
    (tmp_path / "bk").mkdir()
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        (tmp_path / "bk" / f"ar_scout-{day}.db").write_bytes(b"old")

    res = service.backup_database(keep=2)
    names = sorted(p.name for p in (tmp_path / "bk").iterdir())
    assert len(names) == 2 and names[-1] == res["path"].split("/")[-1]
    import sqlite3
    with sqlite3.connect(res["path"]) as c:
        assert c.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1


def test_schema_upgrade_survives_parallel_first_requests(tmp_path, monkeypatch):
    import sqlite3
    import threading
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:  # a database from before the newer columns existed
        c.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT NOT NULL, deezer_id INTEGER UNIQUE, "
                  "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=path))
    errors = []

    def hit():
        try:
            db.all_artists()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with sqlite3.connect(path) as c:
        assert "management" in {r[1] for r in c.execute("PRAGMA table_info(artists)")}


def test_requests_always_identify_a_contact():
    ua = config.settings.user_agent
    assert "github.com/carsontolar-del/AR-Scout" in ua and "no-contact" not in ua


def test_html_error_pages_shrink_to_their_message():
    import httpx
    from ar_scout import http
    page = ("<!DOCTYPE html><html><title>Wikimedia Error</title><style>* { margin: 0 }</style>"
            "<body><p>Error: 403, Please respect our robot policy</p></body></html>")
    brief = http._brief(httpx.Response(403, text=page, headers={"content-type": "text/html"}))
    assert brief.startswith("Wikimedia Error Error: 403, Please respect our robot policy") and "margin" not in brief


def test_a_failing_source_shows_up_in_health(monkeypatch):
    for i in range(10):
        db.upsert_artist({"name": f"Act {i}", "deezer_id": i + 1})
    monkeypatch.setattr(service.deezer, "artist", lambda _id: {"nb_fan": 100, "nb_album": 1})
    monkeypatch.setattr(service.deezer, "top_tracks", lambda *_: [])
    monkeypatch.setattr(service, "_release_info", lambda _id: {})
    for mod in (service.lastfm, service.youtube, service.spotify, service.instagram):
        monkeypatch.setattr(mod, "available", lambda: False)
    monkeypatch.setattr(service.wikipedia, "pageviews", lambda *a, **k: (_ for _ in ()).throw(
        SourceError("wikimedia.org HTTP 403: Please respect our robot policy")))
    for a in db.all_artists():
        db.upsert_artist({"wiki_title": a["name"]}, a["id"])
    service.snapshot_many("all", retry_wait=0)
    assert service.data_health()["source_problems"] == {"wikipedia": 10}
