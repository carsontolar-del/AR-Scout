import dataclasses
from datetime import datetime, time
from zoneinfo import ZoneInfo

from ar_scout import config, db, scheduler, service

TZ = ZoneInfo("America/Denver")


def test_next_run_is_today_or_tomorrow():
    at = time(9, 0)
    assert scheduler.next_run(datetime(2026, 10, 20, 7, 0, tzinfo=TZ), at) == datetime(2026, 10, 20, 9, 0, tzinfo=TZ)
    assert scheduler.next_run(datetime(2026, 10, 20, 9, 0, tzinfo=TZ), at) == datetime(2026, 10, 21, 9, 0, tzinfo=TZ)


def test_catches_up_after_a_missed_day():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=TZ)
    assert scheduler.missed_run(now, None)
    assert scheduler.missed_run(now, "2026-10-19T08:00:00+00:00")
    assert not scheduler.missed_run(now, "2026-10-20T15:05:00+00:00")


def test_off_unless_configured(monkeypatch):
    monkeypatch.delenv("AR_SCOUT_DAILY_AT", raising=False)
    assert scheduler.start() is False


def test_run_once_snapshots_backs_up_and_records(tmp_path, monkeypatch):
    s = dataclasses.replace(config.settings, db_path=tmp_path / "t.db", backup_dir=tmp_path / "bk")
    for mod in (db, service, scheduler):
        monkeypatch.setattr(mod, "settings", s)
    monkeypatch.setattr(service, "snapshot_many", lambda scope: {"snapshotted": 3, "skipped_fresh": 0, "retried": 0, "errors": {}})
    db.upsert_artist({"name": "Band"})
    res = scheduler.run_once()
    assert res["ok"] and res["snapshotted"] == 3 and (tmp_path / "bk").exists()
    assert scheduler.last_status()["snapshotted"] == 3


def test_serve_keeps_request_urls_with_keys_out_of_logs(monkeypatch):
    import logging
    import uvicorn
    from ar_scout import cli
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    cli.serve(host="127.0.0.1", port=0)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
