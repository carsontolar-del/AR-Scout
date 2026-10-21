"""MCP server exposing A&R research tools to Claude Code (stdio transport)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import db, service

mcp = MCPServer(
    "ar-scout",
    instructions=(
        "A&R research tools. Audience numbers: Deezer fans, Last.fm listeners/plays, YouTube subscribers/views, "
        "Instagram followers (professional accounts, when configured), Wikipedia pageviews. Breakout score (0-100) = momentum 40% + engagement 20% + headroom 20% + "
        "activity 10% + signability 10%, then scaled down by audience size (headroom 0 keeps 30%), so famous "
        "acts can't rank high; 'confidence' is the share of the model that had data. Momentum needs "
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
                max_audience: int | None = None, genre: str | None = None,
                audience_platform: str = "any") -> list[dict]:
    """Rank every tracked artist (or just the watchlist) by breakout score using stored snapshots.
    max_audience filters out artists already bigger than that: on any platform by default, or only on
    audience_platform='youtube' | 'lastfm' | 'deezer' | 'instagram' (artists with no data there are dropped).
    genre filters by tag substring."""
    return service.leaderboard(limit, watchlist_only, window_days, max_audience, genre, audience_platform)


@mcp.tool()
def relink_artists() -> dict:
    """Run the full cross-source lookup (MusicBrainz, YouTube, Wikipedia) for artists that discovery added
    without one, then re-snapshot them. Discovery skips YouTube to save quota, so run this before filtering
    by YouTube size. Costs up to 100 YouTube quota units per artist (of 10k/day)."""
    return service.relink_all()


@mcp.tool()
def recheck_youtube() -> dict:
    """Re-search YouTube for artists whose linked channel looks wrong (no videos, or under 1% as many
    subscribers as Last.fm listeners) and switch to a better name match. Old YouTube snapshots for a
    switched channel are deleted. Skips channels set by hand. Costs 100 quota units per suspect artist."""
    return service.recheck_youtube()


@mcp.tool()
def set_youtube_channel(name: str, url: str) -> dict:
    """Fix an artist's YouTube channel by hand (youtube.com/@handle, youtube.com/channel/UC… or a UC… ID).
    Deletes the old channel's snapshots and takes a fresh one."""
    a = service.resolve(name, deep=True)
    return service.set_youtube_channel(a["id"], url)


@mcp.tool()
def set_instagram_account(name: str, handle: str) -> dict:
    """Fix an artist's Instagram account by hand (instagram.com/name, @name or name). Follower counts come from
    Instagram Business Discovery, which only sees public professional (Business/Creator) accounts.
    Deletes the old account's Instagram history and takes a fresh snapshot."""
    a = service.resolve(name, deep=True)
    return service.set_instagram_account(a["id"], handle)


@mcp.tool()
def review_queue() -> list[dict]:
    """Artists whose linked accounts look wrong (YouTube too small or far too big, Last.fm too small, a channel
    named for someone else, a number that jumped overnight) and that nobody has verified yet. Fix links with
    set_youtube_channel / set_instagram_account, then mark_verified."""
    return [{"name": r["name"], "issues": r["review"], "status": r["status"], "score": r["score"]}
            for r in service.review_queue()]


@mcp.tool()
def mark_verified(name: str, verified: bool = True) -> dict:
    """Record that this artist's linked accounts were checked by a person (verified=False undoes it)."""
    a = service.resolve(name, deep=False)
    return service.set_verified(a["id"], verified)


@mcp.tool()
def record_manual_metric(name: str, metric: str, value: int) -> dict:
    """Save a number read off a public profile as today's snapshot. metric: tiktok.followers, tiktok.likes,
    spotify.monthly_listeners, or instagram.followers (only while the Instagram API isn't set up)."""
    a = service.resolve(name, deep=False)
    return service.record_manual(a["id"], metric, value)


@mcp.tool()
def update_pipeline(name: str, status: str | None = None, owner: str | None = None, next_step: str | None = None,
                    next_date: str | None = None) -> dict:
    """Move a watchlist artist through the pipeline (watching | contacted | meeting | signed | passed) and record
    who owns it, the next step and its due date (YYYY-MM-DD). Pass "" to clear a field; omit to leave it."""
    a = service.resolve(name, deep=False)
    fields = {k: v for k, v in (("owner", owner), ("next_step", next_step), ("next_date", next_date)) if v is not None}
    return service.update_pipeline(a["id"], status, **fields)


@mcp.tool()
def archive_artist(name: str, archived: bool = True) -> dict:
    """Hide an artist from rankings, the review queue and daily snapshots (history kept), or restore them with
    archived=False. Use for dead ends and passes; there is deliberately no delete tool."""
    a = db.find_artist(name)
    if not a:
        return {"error": f"'{name}' isn't tracked"}
    return service.set_archived(a["id"], archived)


@mcp.tool()
def set_management(name: str, status: str | None) -> dict:
    """Record whether an artist already has a manager: 'managed', 'unmanaged', or null to go back to what their
    Instagram bio says. Each leaderboard row's `management` shows the result and where it came from."""
    a = service.resolve(name, deep=False)
    return service.set_management(a["id"], status)


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
    """Take new metric snapshots. scope='watchlist' or 'all'. Skips artists refreshed in the last 6h."""
    return service.snapshot_many(scope)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
