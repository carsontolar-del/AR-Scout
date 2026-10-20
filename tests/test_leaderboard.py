import dataclasses

import pytest

from ar_scout import config, db, service


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


def _artist(name, deezer_id, **metrics):
    artist_id = db.upsert_artist({"name": name, "deezer_id": deezer_id})
    db.add_snapshot(artist_id, metrics)
    return artist_id


def test_audience_platform_filters_on_one_platform(tmp_db):
    _artist("Small YT big Last.fm", 1, **{"lastfm.listeners": 120_000, "youtube.subscribers": 6_000})
    _artist("Small everywhere", 2, **{"lastfm.listeners": 9_000, "youtube.subscribers": 4_000})
    _artist("No YouTube", 3, **{"lastfm.listeners": 9_000})

    names = lambda **kw: {r["name"] for r in service.leaderboard(limit=10, max_audience=20_000, **kw)}
    assert names() == {"Small everywhere", "No YouTube"}
    assert names(audience_platform="youtube") == {"Small YT big Last.fm", "Small everywhere"}
    with pytest.raises(ValueError):
        names(audience_platform="myspace")


def test_rows_carry_tags_link_state_and_coverage(tmp_db):
    artist_id = _artist("Band", 1, **{"lastfm.listeners": 9_000})
    db.watch(artist_id, tags=["uk indie"])
    row = service.leaderboard()[0]
    assert row["tags"] == ["uk indie"]
    assert row["linked"] is False and row["youtube_linked"] is False
    assert row["snapshot_days"] == 1 and row["last_snapshot"]
    assert service.data_health()["unlinked"] == 1
