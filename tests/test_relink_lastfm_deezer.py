import pytest

from ar_scout import db, service
from ar_scout.http import SourceError
from ar_scout.sources import deezer, lastfm


def test_links_parse():
    assert lastfm.name_from_url("https://www.last.fm/music/The+K%27s/+wiki") == "The K's"
    assert lastfm.name_from_url("Ratboys") == "Ratboys"
    assert deezer.id_from_url("https://www.deezer.com/en/artist/14799941?utm=x") == 14799941
    assert deezer.id_from_url("14799941") == 14799941
    assert deezer.id_from_url("https://open.spotify.com/artist/x") is None


@pytest.fixture
def quiet_sources(monkeypatch):
    monkeypatch.setattr(service, "snapshot", lambda artist_id: {"errors": []})


def test_manual_lastfm_replaces_history_and_sticks(quiet_sources, monkeypatch):
    a = db.upsert_artist({"name": "Ratboys", "deezer_id": 1, "lastfm_name": "Ratboys"})
    db.add_snapshot(a, {"lastfm.listeners": 2_797, "deezer.fans": 1_802})
    assert any("Last.fm looks too small" in i for i in service.score_artist(db.get_artist(a))["review"])

    asked = {}
    def fake_info(name, autocorrect=True):
        asked.update(name=name, autocorrect=autocorrect)
        return {
        "name": "Ratboys (US)", "mbid": None, "url": "https://www.last.fm/music/Ratboys+(US)",
        "listeners": 180_000, "playcount": 3_000_000, "tags": ["indie rock"], "bio": ""}
    monkeypatch.setattr(service.lastfm, "artist_info", fake_info)
    res = service.set_lastfm_artist(a, "https://www.last.fm/music/Ratboys+(US)")
    assert asked == {"name": "Ratboys (US)", "autocorrect": False}  # exactly what the person asked for
    assert res["listeners"] == 180_000
    row = db.get_artist(a)
    assert row["lastfm_name"] == "Ratboys (US)" and row["lastfm_source"] == "manual"
    assert "lastfm.listeners" not in db.latest_metrics(a)          # old artist's history gone
    assert db.latest_metrics(a)["deezer.fans"] == 1_802             # other platforms kept
    assert not any("Last.fm" in i for i in service.score_artist(row)["review"])


def test_manual_deezer_refuses_a_page_already_tracked(quiet_sources, monkeypatch):
    a = db.upsert_artist({"name": "Gretel", "deezer_id": 10})
    db.upsert_artist({"name": "Someone Else", "deezer_id": 20})
    monkeypatch.setattr(service.deezer, "artist", lambda i: {"id": i, "name": "x", "nb_fan": 900, "picture_medium": None})
    with pytest.raises(SourceError, match="already tracked"):
        service.set_deezer_artist(a, "https://www.deezer.com/artist/20")
    db.add_snapshot(a, {"deezer.fans": 1_229, "lastfm.listeners": 50_000})
    service.set_deezer_artist(a, "https://www.deezer.com/artist/30")
    assert db.get_artist(a)["deezer_id"] == 30
    assert db.latest_metrics(a) == {"lastfm.listeners": 50_000}


def test_flags_last_fm_autocorrect_to_another_name():
    a = db.upsert_artist({"name": "Ratboys", "lastfm_name": "Ratboy"})
    assert any("Ratboy" in i for i in service.score_artist(db.get_artist(a))["review"])
