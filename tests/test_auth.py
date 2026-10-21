import dataclasses

import pytest
from fastapi.testclient import TestClient

from ar_scout import config, db, service
from ar_scout.web import app as webapp, auth


@pytest.fixture
def client(tmp_path, monkeypatch):
    def use(**passwords):
        s = dataclasses.replace(config.settings, session_secret="test-secret", **passwords)  # paths from conftest
        for mod in (config, db, service):
            monkeypatch.setattr(mod, "settings", s)
        auth._failures.clear()
        return TestClient(webapp.app)
    return use


def test_no_passwords_means_no_login(client):
    c = client()
    assert c.get("/api/status").json()["role"] == "local"
    assert c.post("/api/relink").status_code == 200  # admin actions allowed locally


def test_sign_in_required_once_a_password_is_set(client):
    c = client(team_password="team-pw", admin_password="admin-pw")
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/api/leaderboard").status_code == 401
    assert c.get("/login").status_code == 200
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401


def test_team_can_scout_but_not_run_admin_actions(client):
    c = client(team_password="team-pw", admin_password="admin-pw")
    assert c.post("/api/login", json={"password": "team-pw"}).json() == {"role": "team"}
    assert c.get("/api/leaderboard").status_code == 200
    assert c.get("/api/status").json()["role"] == "team"
    for method, path in (("post", "/api/relink"), ("post", "/api/snapshot"), ("delete", "/api/artist/1"),
                         ("post", "/api/artist/1/youtube")):
        kw = {"json": {"url": "UC" + "a" * 22}} if path.endswith("youtube") else {}
        assert getattr(c, method)(path, **kw).status_code == 403, path


def test_admin_can_run_admin_actions(client):
    c = client(team_password="team-pw", admin_password="admin-pw")
    c.post("/api/login", json={"password": "admin-pw"})
    assert c.post("/api/relink").status_code == 200


def test_forged_or_expired_cookies_are_rejected(client):
    client(team_password="team-pw")
    good = auth.make_token("team")
    role, expires, sig = good.split(".")
    assert auth.read_token(good) == "team"
    assert auth.read_token(f"admin.{expires}.{sig}") is None        # role swapped
    assert auth.read_token(auth.make_token("team", days=-1)) is None  # expired
    assert auth.read_token("garbage") is None


def test_repeated_wrong_passwords_pause_sign_in(client):
    c = client(team_password="team-pw")
    for _ in range(auth.MAX_FAILURES):
        c.post("/api/login", json={"password": "wrong"})
    assert c.post("/api/login", json={"password": "team-pw"}).status_code == 429


def test_guesses_from_many_addresses_also_pause(client):
    client(team_password="team-pw")
    for i in range(auth.MAX_FAILURES_ALL):
        auth.record_failure(f"10.0.0.{i}")
    assert auth.too_many_failures("10.9.9.9")


def test_admin_can_upload_a_database_and_team_cannot(client, tmp_path):
    import sqlite3
    c = client(team_password="team-pw", admin_password="admin-pw")
    db.upsert_artist({"name": "Already here"})
    laptop = tmp_path / "laptop.db"
    with sqlite3.connect(laptop) as conn:
        conn.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT NOT NULL, deezer_id INTEGER UNIQUE, "
                     "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE snapshots (artist_id INTEGER, taken_at TEXT, metric TEXT, value REAL)")
        conn.execute("INSERT INTO artists (name, created_at, updated_at) VALUES ('From laptop', 'x', 'x')")
    body = laptop.read_bytes()

    c.post("/api/login", json={"password": "team-pw"})
    assert c.post("/api/admin/database", content=body).status_code == 403

    c.post("/api/login", json={"password": "admin-pw"})
    assert c.post("/api/admin/database", content=b"not a database").status_code == 400
    r = c.post("/api/admin/database", content=body)
    assert r.status_code == 200 and r.json()["artists"] == 1
    assert [a["name"] for a in db.all_artists()] == ["From laptop"]  # schema upgraded on the way in
