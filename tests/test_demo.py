import json

from ar_scout import db, demo, service


def test_demo_export_leaves_out_team_information(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "report", lambda name, refresh=True: {
        "artist": {**db.find_artist(name), "management_info": service.management_info(db.find_artist(name)),
                   "instagram_bio": "mgmt: x@y.com", "watching": False},
        "score": {}, "releases": {}, "similar_artists": [], "watchlist": {"status": "x"},
        "notes": [{"body": "private"}], "changes": {}, "history_points": 0, "errors": []})
    public = db.upsert_artist({"name": "Public Act", "deezer_id": 1})
    watched = db.upsert_artist({"name": "Our Prospect", "deezer_id": 2})
    for a in (public, watched):
        db.add_snapshot(a, {"deezer.fans": 500, "lastfm.listeners": 9_000})
    db.watch(watched)
    db.update_pipeline(watched, owner="Carson", next_step="Call manager")
    db.set_management(public, "unmanaged")
    db.add_note(public, "met them at a show")

    res = demo.export(tmp_path / "docs", repo="https://github.com/x/AR-Scout")
    board = json.loads((tmp_path / "docs/demo-data/board.json").read_text())
    assert [r["name"] for r in board["rows"]] == ["Public Act"] and res["left_out_watchlist"] == 1
    row = board["rows"][0]
    assert row["management"]["status"] is None and row["status"] is None and "owner" not in row
    artist = json.loads((tmp_path / f"docs/demo-data/artists/{public}.json").read_text())
    assert artist["notes"] == [] and artist["watchlist"] is None and "instagram_bio" not in artist["artist"]
    page = (tmp_path / "docs/index.html").read_text()
    assert "window.AR_DEMO = true" in page and 'href="favicon.svg"' in page
    assert "met them at a show" not in json.dumps(board) + json.dumps(artist)


def test_failed_export_keeps_the_published_demo(tmp_path, monkeypatch):
    from ar_scout.http import SourceError
    import pytest

    published = tmp_path / "docs/demo-data"
    published.mkdir(parents=True)
    (published / "board.json").write_text('{"old": true}')
    for i in range(5):
        db.add_snapshot(db.upsert_artist({"name": f"Act {i}", "deezer_id": i + 1}), {"deezer.fans": 500})

    def offline(name, refresh=True):
        raise SourceError("api.deezer.com: nodename nor servname provided")

    monkeypatch.setattr(service, "report", offline)
    with pytest.raises(SourceError, match="left unchanged"):
        demo.export(tmp_path / "docs")
    assert json.loads((published / "board.json").read_text()) == {"old": True}
    assert not (tmp_path / "docs/demo-data.new").exists()


def test_report_without_refresh_does_no_cross_source_lookup(monkeypatch):
    import pytest

    artist_id = db.upsert_artist({"name": "Never Linked"})
    monkeypatch.setattr(service.musicbrainz, "search", lambda name: pytest.fail("looked up MusicBrainz"))
    monkeypatch.setattr(service.youtube, "search_channel", lambda name: pytest.fail("searched YouTube"))
    rep = service.report("Never Linked", refresh=False)
    assert rep["artist"]["id"] == artist_id and rep["artist"]["resolved_at"] is None
