import dataclasses

import pytest

from ar_scout import config, db, service


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


def test_archived_artists_leave_rankings_and_snapshots(tmp_db):
    keep = db.upsert_artist({"name": "Keep", "deezer_id": 1})
    gone = db.upsert_artist({"name": "Viola Beach", "deezer_id": 2})
    for a in (keep, gone):
        db.add_snapshot(a, {"deezer.fans": 100})
    service.set_archived(gone)

    assert [r["name"] for r in service.leaderboard()] == ["Keep"]
    assert [r["name"] for r in service.leaderboard(archived=True)] == ["Viola Beach"]
    assert service.data_health()["archived"] == 1
    assert db.find_artist("Viola Beach")  # still found by name, so lookups don't recreate them

    service.set_archived(gone, False)
    assert len(service.leaderboard()) == 2


def test_delete_removes_everything(tmp_db):
    a = db.upsert_artist({"name": "Mistake", "deezer_id": 3})
    db.add_snapshot(a, {"deezer.fans": 5})
    db.watch(a)
    db.add_note(a, "wrong search")
    assert service.delete_artist(a) == {"deleted": "Mistake"}
    assert db.get_artist(a) is None and db.history(a) == [] and db.watch_entry(a) is None and db.notes(a) == []
