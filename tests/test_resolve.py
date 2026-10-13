import dataclasses

from ar_scout import config, db, service


def test_discovered_artist_still_gets_deep_lookup(tmp_path, monkeypatch):
    """Discovery stores a Last.fm mbid; that must not stop resolve() from linking YouTube etc."""
    settings = dataclasses.replace(config.settings, db_path=tmp_path / "t.db")
    monkeypatch.setattr(db, "settings", settings)

    artist_id = db.upsert_artist({"name": "Band", "deezer_id": 1, "mbid": "mb-1"})  # as track_light leaves it

    monkeypatch.setattr(service.deezer, "artist", lambda _id: {"id": 1, "name": "Band"})
    monkeypatch.setattr(service.lastfm, "available", lambda: False)
    monkeypatch.setattr(service.spotify, "available", lambda: False)
    monkeypatch.setattr(service.musicbrainz, "lookup", lambda _mbid: {
        "country": "GB", "begin": None, "genres": [],
        "links": {"youtube": "https://www.youtube.com/channel/UC" + "a" * 22}})
    monkeypatch.setattr(service.youtube, "available", lambda: True)
    for mod, fn in ((service.musicbrainz, "search"), (service.youtube, "search_channel")):
        monkeypatch.setattr(mod, fn, lambda *_: (_ for _ in ()).throw(AssertionError("network call")))

    a = service.resolve("Band")
    assert a["id"] == artist_id
    assert a["youtube_channel"] == "UC" + "a" * 22
    assert a["country"] == "GB"
    assert a["resolved_at"]

    # Once resolved, later calls return early without hitting the sources again.
    monkeypatch.setattr(service.musicbrainz, "lookup", lambda _mbid: (_ for _ in ()).throw(AssertionError))
    assert service.resolve("Band")["youtube_channel"] == "UC" + "a" * 22
