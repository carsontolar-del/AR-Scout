import dataclasses

import pytest

from ar_scout import config, db, scoring, service


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


def test_manual_numbers_become_snapshots(tmp_db, monkeypatch):
    monkeypatch.setattr(service.instagram, "available", lambda: False)
    artist_id = db.upsert_artist({"name": "Band"})
    service.record_manual(artist_id, "spotify.monthly_listeners", 48_200)
    service.record_manual(artist_id, "tiktok.followers", 12_500.4)
    assert db.latest_metrics(artist_id) == {"spotify.monthly_listeners": 48_200, "tiktok.followers": 12_500}
    with pytest.raises(ValueError):
        service.record_manual(artist_id, "deezer.fans", 10)
    with pytest.raises(ValueError):
        service.record_manual(artist_id, "tiktok.followers", -1)


def test_manual_instagram_only_without_the_api(tmp_db, monkeypatch):
    monkeypatch.setattr(service.instagram, "available", lambda: True)
    artist_id = db.upsert_artist({"name": "Band"})
    with pytest.raises(ValueError, match="Instagram API"):
        service.record_manual(artist_id, "instagram.followers", 900)


def test_a_huge_tiktok_counts_as_big():
    assert scoring.headroom_score({"lastfm.listeners": 30_000, "tiktok.followers": 6_000_000}) == 0.0
