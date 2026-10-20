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
