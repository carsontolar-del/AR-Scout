# AR Scout

Find and track up-and-coming artists with free music data APIs. Three ways in:

- **Claude Code** (MCP server): ask things like "find artists in the orbit of Fcukers and Magdalena Bay
  with under 20k Deezer fans and tell me who's unsigned".
- **CLI**: `ar-scout discover similar "Fcukers" "Magdalena Bay"`, `ar-scout top`, `ar-scout report "Name"`.
- **Dashboard**: `ar-scout serve` → http://127.0.0.1:8787 (leaderboard, watchlist, discovery, artist pages with trend charts).

## Setup

```bash
uv sync
cp .env.example .env   # then add free keys (Last.fm, YouTube; Spotify optional)
uv run ar-scout status
```

Works with zero keys (Deezer + MusicBrainz + Wikipedia), but Last.fm unlocks tag/country discovery and
engagement scoring, and YouTube adds subscriber/view tracking.

## Growth tracking

Momentum, the heaviest part of the score, comes from comparing snapshots over time. Take one daily:

```bash
./scripts/install-daily-snapshot.sh   # macOS launchd, 09:00 every day
```

## Discovery methods

| Command | Source | Finds |
|---|---|---|
| `discover similar A B` | Deezer related + Last.fm similar | smaller artists near your references; overlap across seeds ranks first |
| `discover tag "uk drill"` | Last.fm tag charts (deep pages) | emerging acts in a scene |
| `discover playlists "new rap"` | Deezer playlist search | artists getting curator support |
| `discover new --genre "Rap/Hip Hop"` | Deezer editorial + new-music playlists | fresh releases |
| `discover country "Nigeria"` | Last.fm geo charts | regional risers |

All results are saved and scored; `ar-scout top --max-audience 50000` ranks everything you've found.

## Limits worth knowing

- Spotify Dev Mode apps no longer receive followers or popularity (Feb 2026), so Spotify is used only for IDs, genres and releases.
- There are no free TikTok or Instagram metrics APIs. Social links come from MusicBrainz for manual checks.
  For TikTok/IG/playlist-reach data at scale you'd need a paid API (Chartmetric, Soundcharts, Songstats).
  The `sources/` layout is built so one of these can drop in as another module.
