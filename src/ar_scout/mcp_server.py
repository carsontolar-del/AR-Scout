"""MCP server exposing A&R research tools to Claude Code (stdio transport)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import db, service

mcp = MCPServer(
    "ar-scout",
    instructions=(
        "A&R research tools. Audience numbers: Deezer fans, Last.fm listeners/plays, YouTube subscribers/views, "
        "Wikipedia pageviews. Breakout score (0-100) = momentum 40% + engagement 20% + headroom 20% + "
        "activity 10% + signability 10%; 'confidence' is the share of the model that had data. Momentum needs "
        "snapshots taken on different days, so new artists score on the other components only. Always cite the "
        "underlying numbers and flags, not just the score."
    ),
)


@mcp.tool()
def source_status() -> dict:
    """Which data sources are configured (API keys present)."""
    return service.source_status()


@mcp.tool()
def artist_report(name: str, refresh: bool = True) -> dict:
    """Full profile of one artist: linked IDs, latest metrics across platforms, growth rates, breakout score
    breakdown, recent releases and label, similar artists, watchlist status and notes. Takes a fresh snapshot
    if the last one is older than 12h (set refresh=False to skip)."""
    return service.report(name, refresh=refresh)


@mcp.tool()
def compare_artists(names: list[str]) -> list[dict]:
    """Side-by-side comparison of several artists (score, audience, engagement, growth, release activity)."""
    out = []
    for n in names:
        r = service.report(n)
        s = r["score"]
        out.append({"name": r["artist"]["name"], "score": s["score"], "confidence": s["confidence"],
                    "components": s["components"], "metrics": s["metrics"],
                    "monthly_growth": {k: g["monthly_rate"] for k, g in s["growth"].items()},
                    "latest_release": r["releases"].get("latest_release"),
                    "label": r["releases"].get("latest_label"), "flags": s["flags"]})
    return out


@mcp.tool()
def discover_similar(seed_artists: list[str], min_fans: int = 300, max_fans: int = 50_000,
                     hops: int = 1, limit: int = 20) -> list[dict]:
    """Find smaller artists in the orbit of the seeds ("who sounds like X but is still early").
    Uses Deezer related + Last.fm similar; artists linked to multiple seeds rank higher before scoring.
    Fan bounds use Deezer fan counts. hops=2 widens the search (slower). Results are saved to the database."""
    return service.discover_similar(seed_artists, min_fans, max_fans, hops, limit)


@mcp.tool()
def discover_by_tag(tag: str, min_fans: int = 300, max_fans: int = 50_000, start_page: int = 2,
                    pages: int = 4, limit: int = 20) -> list[dict]:
    """Find emerging artists for a genre/scene tag via Last.fm tag charts (e.g. 'uk drill', 'hyperpop',
    'alt-country', 'afroswing'). Deeper pages (higher start_page) surface smaller acts."""
    return service.discover_by_tag(tag, min_fans, max_fans, start_page, pages, limit)


@mcp.tool()
def discover_by_country(country: str, min_fans: int = 300, max_fans: int = 50_000, start_page: int = 3,
                        pages: int = 4, limit: int = 20) -> list[dict]:
    """Find emerging artists popular in one country via Last.fm geo charts.
    Use the full English country name (e.g. 'Nigeria', 'United Kingdom')."""
    return service.discover_by_country(country, min_fans, max_fans, start_page, pages, limit)


@mcp.tool()
def discover_new_releases(genre: str | None = None, min_fans: int = 200, max_fans: int = 50_000,
                          limit: int = 20) -> list[dict]:
    """Smaller artists from Deezer editorial selections and 'new <genre>' playlists, optionally per Deezer genre
    (e.g. 'Rap/Hip Hop', 'Pop', 'Dance', 'R&B', 'Alternative', 'Electro', 'Reggaeton', 'African Music')."""
    return service.discover_new_releases(genre, min_fans, max_fans, limit)


@mcp.tool()
def discover_playlists(query: str, playlists: int = 5, min_fans: int = 300, max_fans: int = 50000,
                       limit: int = 20) -> list[dict]:
    """Mine curated Deezer playlists matching a query (e.g. 'new rap', 'fresh indie', 'amapiano 2026',
    'bedroom pop'). Artists on several matching playlists are evaluated first. Good for scene sweeps."""
    return service.discover_playlists(query, playlists, min_fans, max_fans, limit)


@mcp.tool()
def leaderboard(limit: int = 25, watchlist_only: bool = False, window_days: int = 30,
                max_audience: int | None = None, genre: str | None = None) -> list[dict]:
    """Rank every tracked artist (or just the watchlist) by breakout score using stored snapshots.
    max_audience filters out artists already bigger than that; genre filters by tag substring."""
    return service.leaderboard(limit, watchlist_only, window_days, max_audience, genre)


@mcp.tool()
def metric_history(name: str, metric: str | None = None) -> list[dict]:
    """Raw time series for an artist (e.g. metric='deezer.fans', 'lastfm.listeners', 'youtube.subscribers')."""
    a = db.find_artist(name)
    if not a:
        return []
    return db.history(a["id"], metric)


@mcp.tool()
def watchlist_add(name: str, status: str = "watching", tags: list[str] | None = None, note: str | None = None) -> dict:
    """Add/update an artist on the watchlist. status: watching | contacted | meeting | passed | signed."""
    a = service.resolve(name, deep=True)
    a.pop("_errors", None)
    db.watch(a["id"], status, tags)
    if note:
        db.add_note(a["id"], note)
    if not db.history(a["id"]):
        service.snapshot(a["id"])
    return {"artist": a["name"], "id": a["id"], "watchlist": db.watch_entry(a["id"])}


@mcp.tool()
def watchlist_remove(name: str) -> str:
    """Remove an artist from the watchlist (history is kept)."""
    a = db.find_artist(name)
    if not a:
        return f"'{name}' is not tracked"
    db.unwatch(a["id"])
    return f"Removed {a['name']} from watchlist"


@mcp.tool()
def watchlist(status: str | None = None) -> list[dict]:
    """The watchlist ranked by breakout score, optionally filtered by status."""
    rows = service.leaderboard(limit=1000, watchlist_only=True)
    return [r for r in rows if not status or r["status"] == status]


@mcp.tool()
def add_note(name: str, note: str) -> str:
    """Attach a dated scouting note to an artist (show reports, contacts, gut feel)."""
    a = db.find_artist(name) or service.resolve(name, deep=False)
    db.add_note(a["id"], note)
    return f"Note saved for {a['name']}"


@mcp.tool()
def refresh_snapshots(scope: str = "watchlist") -> dict:
    """Take new metric snapshots. scope='watchlist' or 'all'. Skips artists refreshed in the last 20h."""
    return service.snapshot_many(scope)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
