"""Command-line interface: `ar-scout --help`."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import db, service

app = typer.Typer(help="A&R scouting: discover and track up-and-coming artists.", no_args_is_help=True)
watch_app = typer.Typer(help="Manage the watchlist.", no_args_is_help=True)
discover_app = typer.Typer(help="Find new artists.", no_args_is_help=True)
app.add_typer(watch_app, name="watch")
app.add_typer(discover_app, name="discover")
console = Console()


def _n(v) -> str:
    if v is None:
        return "-"
    v = float(v)
    for div, suf in ((1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


def _table(rows: list[dict], title: str) -> None:
    t = Table(title=title, header_style="bold")
    t.add_column("#", justify="right")
    t.add_column("Artist", no_wrap=True, min_width=14)
    t.add_column("Score", justify="right")
    for col in ("Deezer", "Last.fm", "YouTube", "Growth/mo"):
        t.add_column(col, justify="right")
    t.add_column("Latest", no_wrap=True)
    t.add_column("Label", max_width=22, overflow="ellipsis", no_wrap=True)
    t.add_column("Status")
    for i, r in enumerate(rows, 1):
        g = r["monthly_growth"]
        growth = max(g.values()) if g else None
        t.add_row(str(i), r["name"], f"{r['score']:.0f}" + ("" if r["confidence"] >= 0.6 else "[dim]*[/]"),
                  _n(r["deezer_fans"]), _n(r["lastfm_listeners"]), _n(r["youtube_subscribers"]),
                  _pct(growth), r["latest_release"] or "-", (r["label"] or "-")[:24], r["status"] or "")
    console.print(t)
    if any(r["confidence"] < 0.6 for r in rows):
        console.print("[dim]* low confidence: under 60% of the model had data (usually under a week of growth history)[/]")


def _progress(name: str) -> None:
    console.print(f"  [dim]evaluating {name}[/dim]")


@app.command()
def status():
    """Show which data sources are configured."""
    for src, ok in service.source_status().items():
        console.print(f"  {'[green]✓' if ok else '[red]✗'}[/] {src}")


@app.command()
def report(name: str, as_json: bool = typer.Option(False, "--json")):
    """Full report on an artist."""
    with console.status(f"Researching {name}…"):
        r = service.report(name)
    if as_json:
        console.print_json(json.dumps(r, default=str))
        return
    a, s = r["artist"], r["score"]
    console.rule(f"[bold]{a['name']}[/]  breakout {s['score']:.0f}/100  (confidence {s['confidence']:.0%})")
    console.print(f"Genres: {', '.join(a.get('genres') or []) or '-'}   Country: {a.get('country') or '-'}   "
                  f"Active since: {a.get('career_start') or r['releases'].get('first_release') or '-'}")
    console.print("Metrics: " + "  ".join(f"{k}={_n(v)}" for k, v in sorted(s["metrics"].items())))
    console.print("Components: " + "  ".join(f"{k}={'-' if v is None else f'{v:.2f}'}" for k, v in s["components"].items()))
    for k, g in s["growth"].items():
        console.print(f"  {k}: {_n(g['start'])} → {_n(g['end'])} over {g['days']}d ({_pct(g['monthly_rate'])}/mo)")
    rel = r["releases"]
    if rel:
        console.print(f"Latest: {rel.get('latest_title')} ({rel.get('latest_type')}, {rel.get('latest_release')}) "
                      f"on {rel.get('latest_label') or '?'}; {rel.get('releases_last_year', 0)} releases in 12mo")
    for f in s["flags"]:
        console.print(f"  [yellow]• {f}[/]")
    if r["similar_artists"]:
        console.print("Similar: " + ", ".join(f"{x['name']} ({_n(x['deezer_fans'])})" for x in r["similar_artists"]))
    if links := a.get("links"):
        console.print("Links: " + "  ".join(links.values()))
    for n in r["notes"]:
        console.print(f"  [cyan]{n['created_at'][:10]}[/] {n['body']}")
    if r["errors"]:
        console.print(f"[dim]Source errors: {'; '.join(r['errors'])}[/]")


@app.command()
def top(limit: int = 25, watchlist: bool = False, window: int = 30,
        max_audience: Optional[int] = None, genre: Optional[str] = None,
        platform: str = typer.Option("any", help="Apply --max-audience to: any | youtube | lastfm | deezer | instagram")):
    """Leaderboard of tracked artists by breakout score."""
    _table(service.leaderboard(limit, watchlist, window, max_audience, genre, platform), "Breakout leaderboard")


@app.command()
def snapshot(scope: str = typer.Argument("watchlist", help="watchlist | all"), stale_hours: float = service.STALE_HOURS):
    """Take metric snapshots (run daily via cron/launchd to build growth history)."""
    res = service.snapshot_many(scope, stale_hours, progress=_progress)
    console.print(f"Snapshotted {res['snapshotted']}, skipped {res['skipped_fresh']} fresh, "
                  f"retried {res['retried']} after network errors.")
    if scope == "all":  # the daily job's run: keep a dated copy of the history
        b = service.backup_database()
        console.print(f"Backed up to {b['path']} ({b['size_mb']} MB, keeping {b['kept']})")
    for name, errs in res["errors"].items():
        console.print(f"  [red]{name}[/]: {'; '.join(errs)}")


@app.command()
def backup():
    """Save a dated copy of the database now (the daily snapshot does this automatically)."""
    b = service.backup_database()
    console.print(f"Backed up to {b['path']} ({b['size_mb']} MB, keeping {b['kept']})")


@app.command()
def relink():
    """Link YouTube/Wikipedia/MusicBrainz for artists added by discovery that never had a full lookup."""
    res = service.relink_all(progress=_progress)
    console.print(f"Relinked {res['relinked']} artists")
    for e in res["errors"]:
        console.print(f"[yellow]{e}[/]")


@app.command("recheck-youtube")
def recheck_youtube():
    """Re-search YouTube for artists whose linked channel looks wrong (100 quota units each)."""
    res = service.recheck_youtube(progress=_progress)
    for c in res["changed"]:
        console.print(f"{c['name']}: {_n(c['subscribers_before'])} -> {_n(c['subscribers_after'])} subscribers")
    if res["unchanged"]:
        console.print(f"[yellow]No better channel found for: {', '.join(res['unchanged'])}[/]")
    for e in res["errors"]:
        console.print(f"[red]{e}[/]")


@app.command("set-instagram")
def set_instagram(name: str, handle: str):
    """Set an artist's Instagram account by hand (instagram.com/name, @name or name)."""
    a = service.resolve(name, deep=True)
    res = service.set_instagram_account(a["id"], handle)
    followers = f"{_n(res['followers'])} followers" if res["followers"] is not None else "saved; counts need Instagram set up"
    console.print(f"{res['artist']} -> @{res['username']} ({followers})")


@app.command("instagram-test")
def instagram_test(handle: str = typer.Argument("instagram", help="Any public professional account")):
    """Check the Instagram Business Discovery setup by looking up one account."""
    from .sources import instagram
    if not instagram.available():
        console.print("[red]Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ID in .env (see .env.example)[/]")
        raise typer.Exit(1)
    p = instagram.profile(instagram.username_from_url(handle) or handle)
    console.print(f"[green]Working:[/] @{p['username']} has {_n(p['followers'])} followers, {p['media_count']} posts")


@app.command("set-youtube")
def set_youtube(name: str, url: str):
    """Set an artist's YouTube channel by hand (channel URL, @handle URL or UC… ID)."""
    a = service.resolve(name, deep=True)
    res = service.set_youtube_channel(a["id"], url)
    console.print(f"{res['artist']} -> {res['title']} ({_n(res['subscribers'])} subscribers, {res['videos']} videos)")


@discover_app.command("similar")
def d_similar(seeds: list[str], min_fans: int = 300, max_fans: int = 50_000, hops: int = 1, limit: int = 20):
    """Smaller artists in the orbit of one or more seed artists."""
    _table(service.discover_similar(seeds, min_fans, max_fans, hops, limit, _progress), f"Similar to {', '.join(seeds)}")


@discover_app.command("tag")
def d_tag(tag: str, min_fans: int = 300, max_fans: int = 50_000, start_page: int = 2, pages: int = 4, limit: int = 20):
    """Emerging artists for a Last.fm tag / scene."""
    _table(service.discover_by_tag(tag, min_fans, max_fans, start_page, pages, limit, _progress), f"Tag: {tag}")


@discover_app.command("country")
def d_country(country: str, min_fans: int = 300, max_fans: int = 50_000, start_page: int = 3, pages: int = 4, limit: int = 20):
    """Emerging artists charting in a country (Last.fm geo)."""
    _table(service.discover_by_country(country, min_fans, max_fans, start_page, pages, limit, _progress), f"Country: {country}")


@discover_app.command("new")
def d_new(genre: Optional[str] = None, min_fans: int = 200, max_fans: int = 50_000, limit: int = 20):
    """Smaller artists from Deezer editorial selections and 'new <genre>' playlists."""
    _table(service.discover_new_releases(genre, min_fans, max_fans, limit, _progress), f"New releases: {genre or 'all'}")


@discover_app.command("playlists")
def d_playlists(query: str, playlists: int = 5, min_fans: int = 300, max_fans: int = 50000, limit: int = 20):
    """Mine curated Deezer playlists matching a query (e.g. 'new rap', 'fresh indie')."""
    _table(service.discover_playlists(query, playlists, min_fans, max_fans, limit, _progress), f"Playlists: {query}")


@watch_app.command("add")
def w_add(name: str, status: str = "watching", tag: list[str] = typer.Option([], "--tag"), note: Optional[str] = None):
    """Add an artist to the watchlist."""
    a = service.resolve(name, deep=True)
    db.watch(a["id"], status, tag)
    if note:
        db.add_note(a["id"], note)
    if not db.history(a["id"]):
        service.snapshot(a["id"])
    console.print(f"[green]Watching {a['name']}[/] ({status})")


@watch_app.command("rm")
def w_rm(name: str):
    """Remove an artist from the watchlist."""
    a = db.find_artist(name)
    if a:
        db.unwatch(a["id"])
        console.print(f"Removed {a['name']}")


@watch_app.command("ls")
def w_ls(status: Optional[str] = None):
    """Show the watchlist ranked by score."""
    rows = [r for r in service.leaderboard(1000, watchlist_only=True) if not status or r["status"] == status]
    _table(rows, "Watchlist")


@app.command()
def note(name: str, body: str):
    """Add a scouting note to an artist."""
    a = db.find_artist(name) or service.resolve(name, deep=False)
    db.add_note(a["id"], body)
    console.print(f"Saved note for {a['name']}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8787):
    """Run the web dashboard."""
    import uvicorn
    console.print(f"Dashboard at http://{host}:{port}")
    uvicorn.run("ar_scout.web.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
