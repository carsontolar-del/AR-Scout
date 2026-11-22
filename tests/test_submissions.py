import dataclasses

import pytest
from fastapi.testclient import TestClient

from ar_scout import config, db, service, submissions
from ar_scout.web import app as webapp, auth

DEMO = {"kind": "artist", "topic": "demo", "name": "Sam Rivera", "email": "sam@example.com", "artist_name": "Glass Eyes",
        "song_title": "Low Tide", "link": "soundcloud.com/glasseyes/low-tide", "consent": True, "rights": True}
FAN = {"kind": "fan", "topic": "song", "artist_name": "Friko", "song_title": "Crimson to Chrome",
       "link": "https://open.spotify.com/track/abc", "message": "The drums!", "credit_ok": True}


@pytest.fixture
def client(monkeypatch):
    def use(**passwords):
        s = dataclasses.replace(config.settings, session_secret="test-secret", **passwords)  # paths from conftest
        for mod in (config, db, service):
            monkeypatch.setattr(mod, "settings", s)
        auth._posts.clear()
        return TestClient(webapp.app)
    return use


def test_demo_is_stored_with_a_full_link():
    row = submissions.submit("artist", "demo", DEMO)
    assert row["status"] == "new" and row["link"] == "https://soundcloud.com/glasseyes/low-tide"
    assert db.submissions(kind="artist")[0]["song_title"] == "Low Tide"


def test_missing_fields_are_named():
    with pytest.raises(ValueError, match="the song title and a link to the song"):
        submissions.submit("artist", "demo", DEMO | {"song_title": " ", "link": None})


def test_artists_must_agree_to_contact_and_rights():
    with pytest.raises(ValueError, match="contacting you"):
        submissions.submit("artist", "message", DEMO | {"topic": "message", "message": "Hi", "consent": False})
    with pytest.raises(ValueError, match="rights"):
        submissions.submit("artist", "demo", DEMO | {"rights": False})


def test_bad_email_and_link_are_refused():
    with pytest.raises(ValueError, match="email"):
        submissions.submit("artist", "demo", DEMO | {"email": "sam at example"})
    with pytest.raises(ValueError, match="link"):
        submissions.submit("fan", "song", FAN | {"link": "javascript:alert(1)"})


def test_fans_can_stay_anonymous():
    row = submissions.submit("fan", "song", FAN)
    assert row["name"] is None and row["email"] is None and row["credit_ok"] == 1


def test_status_and_note_updates():
    sid = submissions.submit("fan", "song", FAN)["id"]
    row = submissions.update(sid, "shortlisted", "Newsletter next week")
    assert (row["status"], row["team_note"]) == ("shortlisted", "Newsletter next week")
    assert submissions.counts() == {"artist": 0, "fan": 0}
    with pytest.raises(ValueError):
        submissions.update(sid, "signed")


def test_public_links_work_without_sign_in_but_the_inbox_does_not(client):
    c = client(team_password="team-pw", admin_password="admin-pw")
    assert c.get("/submit/artist").status_code == 200 and c.get("/submit/fan").status_code == 200
    assert c.post("/api/submit", json=DEMO).json() == {"ok": True}
    assert c.get("/api/submissions").status_code == 401
    assert c.patch("/api/submissions/1", json={"status": "passed"}).status_code == 401
    c.post("/api/login", json={"password": "team-pw"})
    assert c.get("/api/submissions").status_code == 403  # the inbox is the admin's
    c.post("/api/login", json={"password": "admin-pw"})
    d = c.get("/api/submissions").json()
    assert d["new"] == {"artist": 1, "fan": 0} and d["rows"][0]["email"] == "sam@example.com"


def test_bad_submissions_get_a_readable_error(client):
    c = client()
    r = c.post("/api/submit", json=FAN | {"artist_name": ""})
    assert r.status_code == 400 and "the artist" in r.json()["detail"]


def test_bots_filling_the_hidden_field_store_nothing(client):
    c = client()
    assert c.post("/api/submit", json=FAN | {"website": "spam.example"}).json() == {"ok": True}
    assert db.submissions() == []


def test_one_address_is_rate_limited(client):
    c = client()
    for _ in range(auth.MAX_SUBMISSIONS):
        assert c.post("/api/submit", json=FAN).status_code == 200
    assert c.post("/api/submit", json=FAN).status_code == 429
