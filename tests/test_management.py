import dataclasses

import pytest

from ar_scout import config, db, management, service


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "settings", dataclasses.replace(config.settings, db_path=tmp_path / "t.db"))


@pytest.mark.parametrize("bio, status", [
    ("new album out now\nmgmt: jane@northstar.co", "listed"),
    ("Management - Northstar Artists | booking: agent@caa.com", "listed"),
    ("managed by @northstar", "listed"),
    ("bookings: agent@wme.com", "not_listed"),       # a booking agent isn't a manager
    ("london based songwriter", "not_listed"),
    ("", None),
])
def test_reads_management_from_bio(bio, status):
    found = management.from_bio(bio)
    assert (found or {}).get("status") == status


def test_a_person_overrides_the_bio(tmp_db, monkeypatch):
    for mod in (service.lastfm, service.youtube, service.spotify):
        monkeypatch.setattr(mod, "available", lambda: False)
    monkeypatch.setattr(service.instagram, "available", lambda: True)
    monkeypatch.setattr(service.instagram, "profile", lambda u: {
        "username": u, "followers": 5_000, "media_count": 40, "biography": "mgmt: jane@northstar.co", "website": None})
    artist_id = db.upsert_artist({"name": "Band", "instagram_username": "band"})

    service.snapshot(artist_id)
    info = service.management_info(db.get_artist(artist_id))
    assert info["status"] == "listed" and "northstar" in info["evidence"]

    assert service.set_management(artist_id, "unmanaged")["source"] == "you"
    assert service.set_management(artist_id, None)["status"] == "listed"
    with pytest.raises(ValueError):
        service.set_management(artist_id, "maybe")
