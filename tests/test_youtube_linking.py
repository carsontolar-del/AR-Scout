import dataclasses

import pytest

from ar_scout import config, db, service
from ar_scout.sources import youtube

WRONG, RIGHT = "UC" + "w" * 22, "UC" + "r" * 22
STATS = {WRONG: {"title": "richardwattior", "subscribers": 627, "views": 0, "videos": 0},
         RIGHT: {"title": "Of The Trees", "subscribers": 39_100, "views": 9_000_000, "videos": 22}}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


@pytest.fixture
def fake_youtube(monkeypatch):
    monkeypatch.setattr(service.youtube, "available", lambda: True)
    monkeypatch.setattr(service.youtube, "channel_stats", lambda cid: STATS.get(cid))
    monkeypatch.setattr(service.youtube, "search_channel", lambda name: RIGHT)


def test_title_matches_ignores_spacing_and_topic_channels():
    assert youtube.title_matches("Of The Trees", "ofthetrees")
    assert youtube.title_matches("OfTheTreesVEVO", "Of The Trees")
    assert not youtube.title_matches("Of The Trees - Topic", "Of The Trees")
    assert not youtube.title_matches("richardwattior", "Of The Trees")


def test_wrong_musicbrainz_link_falls_back_to_search(fake_youtube):
    errors = []
    channel, source = service._youtube_from_link(errors, "https://www.youtube.com/channel/" + WRONG, "Of The Trees")
    assert (channel, source) == (RIGHT, "search")
    assert "richardwattior" in errors[0]


def test_good_musicbrainz_link_is_kept_without_search(fake_youtube, monkeypatch):
    monkeypatch.setattr(service.youtube, "search_channel", lambda name: pytest.fail("searched"))
    assert service._youtube_from_link([], "https://www.youtube.com/channel/" + RIGHT, "Of The Trees") == (RIGHT, "musicbrainz")


def test_switching_channel_drops_old_youtube_history(tmp_db, fake_youtube, monkeypatch):
    monkeypatch.setattr(service, "snapshot", lambda artist_id: {"errors": []})
    artist_id = db.upsert_artist({"name": "Of The Trees", "youtube_channel": WRONG, "youtube_source": "musicbrainz"})
    db.add_snapshot(artist_id, {"youtube.subscribers": 627, "youtube.videos": 0, "lastfm.listeners": 80_000})

    res = service.set_youtube_channel(artist_id, RIGHT)
    assert res["subscribers"] == 39_100
    assert db.get_artist(artist_id)["youtube_source"] == "manual"
    assert db.latest_metrics(artist_id) == {"lastfm.listeners": 80_000}


def test_recheck_switches_suspect_channels_but_not_manual_ones(tmp_db, fake_youtube, monkeypatch):
    monkeypatch.setattr(service, "snapshot", lambda artist_id: {"errors": []})
    auto = db.upsert_artist({"name": "Of The Trees", "youtube_channel": WRONG, "youtube_source": "musicbrainz"})
    manual = db.upsert_artist({"name": "Tiny Band", "youtube_channel": WRONG, "youtube_source": "manual"})
    for artist_id in (auto, manual):
        db.add_snapshot(artist_id, {"youtube.subscribers": 627, "youtube.videos": 0})

    res = service.recheck_youtube()
    assert [c["name"] for c in res["changed"]] == ["Of The Trees"]
    assert db.get_artist(auto)["youtube_channel"] == RIGHT
    assert db.get_artist(manual)["youtube_channel"] == WRONG
