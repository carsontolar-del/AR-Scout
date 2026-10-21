import sqlite3

from ar_scout import db, service


def test_scores_are_reused_until_the_data_changes():
    a = db.upsert_artist({"name": "Band"})
    db.add_snapshot(a, {"deezer.fans": 100})
    first = service._scored(False, 30, False)
    assert service._scored(False, 30, False) is first          # nothing changed: reused

    db.watch(a)                                                 # a write through the app
    assert service.leaderboard()[0]["status"] == "watching"


def test_a_write_from_another_process_is_noticed():
    a = db.upsert_artist({"name": "Band"})
    db.add_snapshot(a, {"deezer.fans": 100})
    assert service.leaderboard()[0]["deezer_fans"] == 100
    with sqlite3.connect(db.settings.db_path) as other:         # e.g. the daily job or the MCP server
        other.execute("INSERT INTO snapshots (artist_id, taken_at, metric, value) VALUES (?, ?, 'deezer.fans', 150)",
                      (a, "2099-01-01T00:00:00+00:00"))
    assert service.leaderboard()[0]["deezer_fans"] == 150
