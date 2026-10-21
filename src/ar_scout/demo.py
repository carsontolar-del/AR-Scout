"""Build a static, read-only demo of the dashboard (e.g. for GitHub Pages) from a snapshot of the data.

Leaves out what is the team's own work rather than public music data: watchlisted artists (who the team is
pursuing), notes, pipeline fields, hand-set management status and Instagram bios. No API keys are involved:
the demo makes no live calls.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Callable

from . import db, service

STATIC = Path(__file__).parent / "web" / "static"
PRIVATE_ROW_FIELDS = ("status", "tags", "owner", "next_step", "next_date", "status_since")
UNKNOWN_MGMT = {"status": None, "label": "Unknown", "source": None, "evidence": None}


def _public_management(info: dict | None) -> dict:
    """A person's own call about management is team knowledge; what an artist's public bio says is not."""
    return info if info and info.get("source") == "instagram bio" else UNKNOWN_MGMT


def _public_row(row: dict, metrics: dict, genres: list[str]) -> dict:
    out = {k: v for k, v in row.items() if k not in PRIVATE_ROW_FIELDS}
    out.update(status=None, tags=[], management=_public_management(row.get("management")),
               _audience={k: metrics.get(m) for k, m in service.AUDIENCE_METRICS.items()}, _genres=genres)
    return out


def _public_report(rep: dict) -> dict:
    a = {k: v for k, v in rep["artist"].items()
         if k not in ("management", "instagram_bio", "mgmt_evidence", "watching")}
    a["management_info"] = _public_management(rep["artist"].get("management_info"))
    return rep | {"artist": a, "watchlist": None, "notes": []}


def export(out_dir: Path, include_watchlist: bool = False, repo: str | None = None, credit: str | None = None,
           progress: Callable[[str], None] | None = None) -> dict:
    out_dir = Path(out_dir)
    data_dir = out_dir / "demo-data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    (data_dir / "artists").mkdir(parents=True)

    watched = {a["id"] for a in db.all_artists(watchlist_only=True)}
    scored = [(r, m, g) for r, m, g in service._scored(False, 30, False) if include_watchlist or r["id"] not in watched]
    rows = [_public_row(r, m, g) for r, m, g in scored]
    ids = {r["id"] for r in rows}

    for r in rows:
        if progress:
            progress(r["name"])
        rep = service.report(r["name"], refresh=False)
        rep["history"] = db.history(r["id"])
        (data_dir / "artists" / f"{r['id']}.json").write_text(json.dumps(_public_report(rep), default=str))

    health = service.data_health() | {"daily_job": False, "last_backup": None, "unlinked": 0, "artists": len(rows),
                                       "with_growth": sum(1 for r in rows if r["components"]["momentum"] is not None)}
    board = {
        "meta": {"snapshot": date.today().isoformat(), "repo": repo, "credit": credit, "artists": len(rows)},
        "status": service.source_status() | {"role": "demo", "login": False},
        "health": health,
        "rows": rows,
        "review": [r for r in rows if r["review"] and not r["verified"]],
    }
    (data_dir / "board.json").write_text(json.dumps(board, default=str))

    page = (STATIC / "index.html").read_text()
    page = page.replace('href="/favicon.svg"', 'href="favicon.svg"').replace(
        "<script>\n", "<script>window.AR_DEMO = true;</script>\n<script>\n", 1)
    (out_dir / "index.html").write_text(page)
    shutil.copy(STATIC / "favicon.svg", out_dir / "favicon.svg")
    (out_dir / ".nojekyll").write_text("")  # serve files as they are on GitHub Pages
    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    return {"artists": len(rows), "left_out_watchlist": 0 if include_watchlist else len(watched & set(service._all_ids())),
            "path": str(out_dir), "size_mb": round(size / 1e6, 1)}
