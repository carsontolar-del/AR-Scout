import dataclasses

import pytest

from ar_scout import config, db, service


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


def issues(artist=None, **metrics):
    return service.review_issues({"name": "Band", **(artist or {})}, metrics, {})


def test_flags_accounts_that_belong_to_someone_else():
    assert any("far too big" in i for i in issues(**{"deezer.fans": 483, "youtube.subscribers": 98_600}))
    assert any("Last.fm looks too small" in i for i in issues(**{"deezer.fans": 1_812, "lastfm.listeners": 2_798}))
    assert any("Grace Risch" in i for i in issues({"youtube_title": "Grace Risch"}))
    assert issues({"youtube_title": "Grace Risch", "youtube_source": "manual"}) == []


def test_normal_act_has_no_issues():
    assert issues({"name": "Red Rum Club", "youtube_title": "Red Rum Club"}, **{"deezer.fans": 1_278, "lastfm.listeners": 40_117,
                                                       "youtube.subscribers": 6_460, "youtube.videos": 50}) == []


def test_verifying_clears_queue_and_old_jumps_only(tmp_db):
    artist_id = db.upsert_artist({"name": "sundots"})
    db.add_snapshot(artist_id, {"youtube.subscribers": 99}, taken_at="2026-10-17T15:00:00+00:00")
    db.add_snapshot(artist_id, {"youtube.subscribers": 8_000}, taken_at="2026-10-18T15:00:00+00:00")
    assert [r["name"] for r in service.review_queue()] == ["sundots"]

    service.set_verified(artist_id)
    assert service.review_queue() == []
    assert service.score_artist(db.get_artist(artist_id))["review"] == []  # the jump predates verification

    service.set_verified(artist_id, False)
    assert db.get_artist(artist_id)["verified_at"] is None


def test_flagged_platform_is_left_out_of_the_score_until_verified():
    a = db.upsert_artist({"name": "Snuggle"})
    db.add_snapshot(a, {"deezer.fans": 483, "lastfm.listeners": 84_000, "youtube.subscribers": 98_600})
    s = service.score_artist(db.get_artist(a))
    assert s["excluded"] == ["youtube"] and s["flags"][0].startswith("left out of the score")
    assert s["metrics"]["youtube.subscribers"] == 98_600            # still shown

    without_yt = db.upsert_artist({"name": "Snuggle copy"})
    db.add_snapshot(without_yt, {"deezer.fans": 483, "lastfm.listeners": 84_000})
    assert s["score"] == service.score_artist(db.get_artist(without_yt))["score"]

    service.set_verified(a)
    assert service.score_artist(db.get_artist(a))["excluded"] == []
