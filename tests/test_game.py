import dataclasses

import pytest
from fastapi.testclient import TestClient

from ar_scout import config, db, game, service
from ar_scout.sources import deezer
from ar_scout.web import app as webapp, auth

ME, YOU = "a" * 32, "b" * 32


@pytest.fixture(autouse=True)
def fake_deezer(monkeypatch):
    tracks = {
        11: {"id": 11, "title": "Low Tide", "preview": "https://cdn.example/11.mp3?exp=1", "link": "https://deezer.com/track/11",
             "isrc": "USX1", "album": {"title": "Tides", "cover_xl": "https://img.example/11.jpg", "cover_medium": "m"}},
        12: {"id": 12, "title": "High Tide", "preview": "https://cdn.example/12.mp3", "link": "https://deezer.com/track/12",
             "isrc": "USX2", "album": {"title": "Tides", "cover_xl": "https://img.example/12.jpg"}},
        13: {"id": 13, "title": "No Clip", "preview": "", "album": {}},
    }
    monkeypatch.setattr(deezer, "track", lambda tid, fresh=False: tracks.get(tid))
    monkeypatch.setattr(deezer, "top_tracks", lambda did, limit=10: list(tracks.values()))


@pytest.fixture
def artist():
    aid = db.upsert_artist({"name": "Glass Eyes", "deezer_id": 999})
    db.add_snapshot(aid, {"lastfm.listeners": 4200, "deezer.fans": 300})
    return aid


@pytest.fixture
def client(monkeypatch):
    def use(**passwords):
        s = dataclasses.replace(config.settings, session_secret="test-secret", **passwords)
        for mod in (config, db, service):
            monkeypatch.setattr(mod, "settings", s)
        auth._posts.clear()
        return TestClient(webapp.app)
    return use


def test_pick_needs_a_preview_clip_and_a_wheel_genre(artist):
    with pytest.raises(ValueError, match="no preview"):
        game.add_pick(artist, 13, "Indie Rock")
    with pytest.raises(ValueError, match="wheel genre"):
        game.add_pick(artist, 11, "Polka")
    p = game.add_pick(artist, 11, "Indie Rock", "Sam", "Wait for the chorus.")
    assert (p["title"], p["artist_name"], p["isrc"]) == ("Low Tide", "Glass Eyes", "USX1")
    with pytest.raises(ValueError, match="already"):
        game.add_pick(artist, 11, "Indie Rock")
    assert [t["already_picked"] for t in game.artist_tracks(artist)] == [True, False, False]


def test_spin_keep_and_crate(artist):
    p = game.add_pick(artist, 11, "Indie Rock")
    st = game.state(ME)
    assert st["unheard"] == 1
    assert next(w for w in st["wheel"] if w["name"] == "Indie Rock")["unheard"] == 1
    got = game.spin(ME, "Indie Rock")
    assert got["id"] == p["id"] and got["preview"].startswith("https://cdn.example/11")
    assert "artist_id" not in got and "status" not in got  # only what listeners need
    r = game.react(ME, p["id"], "keep", 21_000)
    assert (r["audience"], r["audience_label"]) == (4200, "Last.fm listeners")
    db.add_snapshot(artist, {"lastfm.listeners": 6300}, taken_at="2099-01-01T00:00:00+00:00")
    crate = game.crate(ME)
    assert crate[0]["title"] == "Low Tide" and crate[0]["growth"] == pytest.approx(0.5)
    with pytest.raises(ValueError, match="heard every"):
        game.spin(ME)
    assert game.spin(YOU)["id"] == p["id"]  # someone else hasn't heard it


def test_no_spin_limit(artist, monkeypatch):
    monkeypatch.setattr(deezer, "track", lambda tid, fresh=False: {"id": tid, "title": f"Song {tid}",
                                                                   "preview": f"https://cdn.example/{tid}.mp3", "album": {}})
    for n in range(20, 28):
        game.add_pick(artist, n, "Indie Rock")
    for _ in range(7):  # more than any old daily cap
        game.react(ME, game.spin(ME)["id"], "pass")
    assert game.state(ME)["unheard"] == 1
    assert game.spin(ME)["id"]


def test_retired_picks_leave_the_wheel(artist):
    p = game.add_pick(artist, 11, "Indie Rock")
    game.update_pick(p["id"], status="retired")
    with pytest.raises(ValueError, match="heard every"):
        game.spin(ME)
    with pytest.raises(ValueError, match="closed"):
        game.add_public_comment(ME, p["id"], "Ana", "Great")


def test_listener_ids_are_checked():
    with pytest.raises(ValueError, match="listener"):
        game.state("not-a-real-id")


def test_public_and_team_comments_stay_apart(artist):
    p = game.add_pick(artist, 11, "Indie Rock")
    game.add_public_comment(ME, p["id"], "  Ana  ", "That bass line!")
    game.add_team_comment("pick", p["id"], "Sam", "Talk to their manager?")
    assert [c["body"] for c in game.public_comments(p["id"])] == ["That bass line!"]
    assert game.public_comments(p["id"])[0]["author"] == "Ana"
    assert [c["body"] for c in db.comments("pick", p["id"], "team")] == ["Talk to their manager?"]
    with pytest.raises(ValueError, match="write a comment"):
        game.add_public_comment(ME, p["id"], None, "   ")


def test_hidden_comments_vanish_from_the_app(artist):
    p = game.add_pick(artist, 11, "Indie Rock")
    c = game.add_public_comment(ME, p["id"], None, "spam spam")
    db.set_comment_hidden(c["id"], True)
    assert game.public_comments(p["id"]) == []
    assert game.moderation_feed()[0]["hidden_at"] and game.moderation_feed()[0]["song"] == "Low Tide — Glass Eyes"
    assert game.picks()[0]["comments"] == 0


def test_app_is_public_but_team_tools_are_not(client, artist):
    c = client(team_password="team-pw")
    assert c.get("/app").status_code == 200
    assert c.get(f"/api/app/state?listener={ME}").status_code == 200
    for method, path in (("get", "/api/picks"), ("get", "/api/comments/public"), ("post", "/api/comments"),
                         ("patch", "/api/comments/1"), ("get", f"/api/artist/{artist}/tracks")):
        assert getattr(c, method)(path).status_code == 401, path
    c.post("/api/login", json={"password": "team-pw"})
    p = c.post("/api/picks", json={"artist_id": artist, "deezer_track_id": 11, "wheel_genre": "Indie Rock"}).json()
    r = c.post(f"/api/app/picks/{p['id']}/comments", json={"listener": ME, "author": "Ana", "body": "Love it"})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert c.delete(f"/api/comments/{cid}").status_code == 400  # public comments are hidden, not deleted
    assert c.patch(f"/api/comments/{cid}", json={"hidden": True}).json()["hidden_at"]
    assert c.get(f"/api/app/picks/{p['id']}/comments").json() == []


def test_comment_rate_limit(client, artist):
    c = client()
    p = game.add_pick(artist, 11, "Indie Rock")
    per_addr = auth.PUBLIC_LIMITS["comment"][0]
    for i in range(per_addr):
        assert c.post(f"/api/app/picks/{p['id']}/comments", json={"listener": ME, "body": f"c{i}"}).status_code == 200
    assert c.post(f"/api/app/picks/{p['id']}/comments", json={"listener": ME, "body": "one more"}).status_code == 429


def test_spin_stays_inside_the_vibe(artist):
    rock = game.add_pick(artist, 11, "Indie Rock")
    game.add_pick(artist, 12, "Electronic")
    for _ in range(5):
        assert game.spin(ME, genres=["Indie Rock", "Folk & Songwriters"])["id"] == rock["id"]
    game.react(ME, rock["id"], "pass")
    with pytest.raises(ValueError, match="every pick in this vibe"):
        game.spin(ME, genres=["Indie Rock"])
    with pytest.raises(ValueError, match="Unknown genre"):
        game.spin(ME, genres=["Polka"])


def test_bass_wheel_is_its_own_room(artist):
    rock = game.add_pick(artist, 11, "Indie Rock")
    dub = game.add_pick(artist, 12, "Dubstep")
    assert [w["name"] for w in game.state(ME, "bass")["wheel"]][:2] == ["Dubstep", "Riddim"]
    assert game.state(ME, "bass")["unheard"] == 1 and game.state(ME)["unheard"] == 1
    for _ in range(5):
        assert game.spin(ME, wheel="bass")["id"] == dub["id"]
        assert game.spin(ME)["id"] == rock["id"]
    with pytest.raises(ValueError, match="Unknown wheel"):
        game.state(ME, "polka")


def test_fill_wheel_tops_up_genres_from_tag_charts(artist, monkeypatch):
    other = db.upsert_artist({"name": "Low End", "deezer_id": 998})
    calls = []
    def fake_discover(tag, **kw):
        calls.append(tag)
        return [{"id": artist}, {"id": other}] if tag == "riddim" else []
    monkeypatch.setattr(service, "discover_by_tag", fake_discover)
    added = game.fill_wheel("bass", per_genre=1, curator="auto", genres=["Riddim", "Techno"])
    assert added == {"Riddim": 1}
    assert calls == ["riddim", "techno", "hard techno"]
    assert [(p["wheel_genre"], p["curator"]) for p in game.picks()] == [("Riddim", "auto")]
    assert game.fill_wheel("bass", per_genre=1, genres=["Riddim"]) == {}  # already full
