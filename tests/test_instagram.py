import dataclasses

import pytest

from ar_scout import config, db, scoring, service
from ar_scout.http import SourceError
from ar_scout.sources import instagram


@pytest.fixture
def ig_settings(monkeypatch):
    s = dataclasses.replace(config.settings, instagram_access_token="t", instagram_business_id="17841400000000000")
    monkeypatch.setattr(instagram, "settings", s)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


def test_username_from_url():
    assert instagram.username_from_url("https://www.instagram.com/OfTheTreesMusic/") == "ofthetreesmusic"
    assert instagram.username_from_url("instagram.com/red.rum_club?hl=en") == "red.rum_club"
    assert instagram.username_from_url("@kaeto") == "kaeto"
    assert instagram.username_from_url("https://www.instagram.com/p/C1abc/") is None
    assert instagram.username_from_url("https://www.youtube.com/@kaeto") is None


def test_profile_reads_business_discovery(ig_settings, monkeypatch):
    seen = {}
    def fake_get(url, params=None, **_):
        seen.update(url=url, fields=params["fields"])
        return {"business_discovery": {"username": "kaeto", "followers_count": 24_000, "media_count": 310}, "id": "1"}
    monkeypatch.setattr(instagram, "get_json", fake_get)
    assert instagram.profile("kaeto") == {"username": "kaeto", "name": None, "followers": 24_000, "media_count": 310,
                                          "biography": None, "website": None}
    assert "biography,website" in seen["fields"]
    assert seen["url"].endswith("/17841400000000000")
    assert seen["fields"].startswith("business_discovery.username(kaeto){")


@pytest.mark.parametrize("body, kind, text", [
    ('{"error":{"message":"(#110) Invalid user id","type":"OAuthException","code":110}}', instagram.NotDiscoverable, "professional"),
    ('{"error":{"message":"Error validating access token: Session has expired","code":190}}', SourceError, "expired"),
    ('{"error":{"message":"(#4) Application request limit reached","code":4}}', SourceError, "rate limit"),
    ('{"error":{"message":"(#10) Application does not have permission","code":10}}', SourceError, "permission"),
])
def test_graph_errors_are_explained(ig_settings, monkeypatch, body, kind, text):
    def fail(*_, **__):
        raise SourceError(f"graph.facebook.com HTTP 400: {body[:200]}")
    monkeypatch.setattr(instagram, "get_json", fail)
    with pytest.raises(kind, match=text):
        instagram.profile("someone")


def test_snapshot_records_followers_from_musicbrainz_link(tmp_db, monkeypatch):
    for mod in (service.lastfm, service.youtube, service.spotify):
        monkeypatch.setattr(mod, "available", lambda: False)
    monkeypatch.setattr(service.instagram, "available", lambda: True)
    monkeypatch.setattr(service.instagram, "profile", lambda u: {"username": u, "followers": 24_000, "media_count": 310})
    artist_id = db.upsert_artist({"name": "Kaeto", "links": {"instagram": "https://www.instagram.com/kaeto/"}})

    service.snapshot(artist_id)
    assert db.latest_metrics(artist_id)["instagram.followers"] == 24_000


def test_manual_account_replaces_old_history(tmp_db, monkeypatch):
    monkeypatch.setattr(service.instagram, "available", lambda: True)
    monkeypatch.setattr(service.instagram, "profile", lambda u: {"username": u, "followers": 9_000, "media_count": 40})
    monkeypatch.setattr(service, "snapshot", lambda artist_id: {"errors": []})
    artist_id = db.upsert_artist({"name": "Gretel", "instagram_username": "gracerisch", "instagram_source": "musicbrainz"})
    db.add_snapshot(artist_id, {"instagram.followers": 120_000, "lastfm.listeners": 50_000})

    res = service.set_instagram_account(artist_id, "@gretel.band")
    assert res["followers"] == 9_000
    assert db.get_artist(artist_id)["instagram_source"] == "manual"
    assert "instagram.followers" not in db.latest_metrics(artist_id)


def test_big_instagram_following_counts_as_big():
    assert scoring.headroom_score({"lastfm.listeners": 30_000}) == 1.0
    assert scoring.headroom_score({"lastfm.listeners": 30_000, "instagram.followers": 2_500_000}) == 0.0
