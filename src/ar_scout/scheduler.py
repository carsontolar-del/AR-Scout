"""Daily snapshot + backup inside the running dashboard, for hosts without a Mac scheduler (Render and the like).

Off unless AR_SCOUT_DAILY_AT is set (e.g. "09:00", in AR_SCOUT_TZ, default America/Denver). On a laptop the
launchd job from scripts/install-daily-snapshot.sh does this instead.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from . import db, service
from .config import settings

log = logging.getLogger("ar_scout.scheduler")
CATCH_UP_AFTER_HOURS = 26  # data this stale at start-up means a run was missed: do it now


def configured() -> tuple[dtime, ZoneInfo] | None:
    at = os.getenv("AR_SCOUT_DAILY_AT", "").strip()
    if not at:
        return None
    hour, minute = (int(x) for x in at.split(":"))
    return dtime(hour, minute), ZoneInfo(os.getenv("AR_SCOUT_TZ", "America/Denver"))


def next_run(now: datetime, at: dtime) -> datetime:
    """The next time `at` comes round, in `now`'s time zone."""
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def missed_run(now: datetime, last_snapshot: str | None) -> bool:
    if not last_snapshot:
        return True
    return (now - datetime.fromisoformat(last_snapshot)).total_seconds() / 3600 > CATCH_UP_AFTER_HOURS


def _status_path():
    return settings.db_path.parent / ".daily_job.json"


def last_status() -> dict | None:
    try:
        return json.loads(_status_path().read_text())
    except (OSError, ValueError):
        return None


def run_once() -> dict | None:
    """Snapshot everything and back up, unless another process is already doing it (then None)."""
    lock_path = settings.db_path.parent / ".daily_job.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("daily snapshot already running elsewhere; skipping")
            return None
        started = db.now()
        try:
            res = service.snapshot_many("all")
            backup = service.backup_database()
            status = {"started": started, "finished": db.now(), "ok": True, "snapshotted": res["snapshotted"],
                      "skipped": res["skipped_fresh"], "retried": res["retried"], "errors": len(res["errors"]),
                      "backup": backup["path"]}
        except Exception as exc:  # keep the scheduler alive; the next day tries again
            log.exception("daily snapshot failed")
            status = {"started": started, "finished": db.now(), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _status_path().write_text(json.dumps(status))
        log.info("daily snapshot: %s", status)
        return status


def _loop(at: dtime, tz: ZoneInfo) -> None:
    if missed_run(datetime.now(tz), service.data_health()["last_snapshot"]):
        time.sleep(60)  # let the server finish starting
        run_once()
    while True:
        due = next_run(datetime.now(tz), at)
        while datetime.now(tz) < due:  # short sleeps, so a sleeping laptop or clock change can't skip a day
            time.sleep(min(300, max(1, (due - datetime.now(tz)).total_seconds())))
        run_once()


def start() -> bool:
    """Start the daily thread if configured. Returns whether it started."""
    cfg = configured()
    if not cfg:
        return False
    threading.Thread(target=_loop, args=cfg, name="daily-snapshot", daemon=True).start()
    log.info("daily snapshot scheduled at %s %s", cfg[0].strftime("%H:%M"), cfg[1].key)
    return True
