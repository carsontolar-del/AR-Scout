import dataclasses

import pytest

from ar_scout import config, db, service
from ar_scout.http import SourceError


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


def test_pipeline_moves_and_next_steps(tmp_db):
    artist_id = db.upsert_artist({"name": "Band"})
    with pytest.raises(SourceError, match="watchlist first"):
        service.update_pipeline(artist_id, "contacted")  # not on the watchlist yet
    db.watch(artist_id, tags=["uk"])
    first = db.watch_entry(artist_id)["status_since"]

    e = service.update_pipeline(artist_id, "contacted", owner="Carson", next_step="Email manager", next_date="2026-11-03")
    assert (e["status"], e["owner"], e["next_step"], e["next_date"]) == ("contacted", "Carson", "Email manager", "2026-11-03")
    assert e["status_since"] >= first and e["tags"] == ["uk"]

    e = service.update_pipeline(artist_id, next_step="")
    assert e["next_step"] is None and e["owner"] == "Carson" and e["status"] == "contacted"
    row = service.leaderboard()[0]
    assert row["owner"] == "Carson" and row["next_date"] == "2026-11-03"

    with pytest.raises(ValueError):
        service.update_pipeline(artist_id, "hired")
    with pytest.raises(ValueError):
        service.update_pipeline(artist_id, next_date="next tuesday")
