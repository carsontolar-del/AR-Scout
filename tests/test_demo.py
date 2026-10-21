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
