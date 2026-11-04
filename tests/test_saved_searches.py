import pytest

from ar_scout import db, service


def test_save_rerun_and_count_new(monkeypatch):
    found = []
    def fake_discover(mode, query="", min_fans=300, max_fans=50_000, limit=20, progress=None):
        found.append((mode, query))
        a = db.upsert_artist({"name": f"Fresh {len(found)}", "deezer_id": len(found)})
        db.add_snapshot(a, {"deezer.fans": 500})
        return [r for r in service.leaderboard(limit=100) if r["id"] == a]
    monkeypatch.setattr(service, "discover", fake_discover)

    s = service.save_search("tag", "uk drill", 300, 50_000, 20)
    assert service.save_search("tag", "UK Drill", 300, 50_000, 20)["id"] == s["id"]  # no duplicates
    res = service.run_saved_search(s["id"])
    assert res["new"] == ["Fresh 1"] and db.saved_search(s["id"])["last_new"] == 1

    assert service.run_due_searches() == []            # ran just now: not due again for a week
    with pytest.raises(ValueError):
        service.save_search("tag", "")


def test_daily_job_reruns_due_searches(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "discover", lambda *a, **k: calls.append(a) or [])
    service.save_search("playlists", "fresh indie")
    assert len(service.run_due_searches()) == 1 and calls == [("playlists", "fresh indie", 300, 50_000, 20, None)]
