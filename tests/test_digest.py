from datetime import date, datetime, timedelta

from ar_scout import db, service


def _daily(artist_id, metric, values, start="2026-10-19"):
    t0 = datetime.fromisoformat(f"{start}T15:00:00+00:00")
    for i, v in enumerate(values):
        db.add_snapshot(artist_id, {metric: v}, taken_at=(t0 + timedelta(days=i)).isoformat())


def test_digest_collects_the_week_in_order():
    today = date(2026, 10, 26)
    riser = db.upsert_artist({"name": "Riser", "latest_release": "2026-10-23", "latest_title": "New Album",
                              "latest_type": "album"})
    _daily(riser, "youtube.subscribers", [5000, 5010, 5020, 5040, 5300, 5500, 5700, 5900])
    _daily(riser, "deezer.fans", [700, 701, 703, 705, 740, 770, 790, 810])
    flat = db.upsert_artist({"name": "Flat", "latest_release": "2025-01-01"})
    _daily(flat, "deezer.fans", [900] * 8)
    db.watch(flat)
    db.update_pipeline(flat, next_step="Call manager", next_date="2026-10-24")
    wrong = db.upsert_artist({"name": "Wrong Channel"})
    _daily(wrong, "youtube.subscribers", [100] * 7 + [9000])  # a jump: under review, so not a mover

    d = service.weekly_digest(today=today)
    assert [r["name"] for r in d["accelerating"]] == ["Riser"]
    assert d["movers"][0]["name"] == "Riser" and d["movers"][0]["gain"]["platform"] == "youtube"
    assert "Wrong Channel" not in [r["name"] for r in d["movers"]]
    assert [r["latest_title"] for r in d["releases"]] == ["New Album"]
    assert [r["name"] for r in d["due"]] == ["Flat"] and d["overdue"] == 1
    assert d["review_count"] == 1
