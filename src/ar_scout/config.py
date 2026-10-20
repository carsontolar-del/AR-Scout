"""Settings loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    lastfm_api_key: str | None
    youtube_api_key: str | None
    spotify_client_id: str | None
    spotify_client_secret: str | None
    instagram_access_token: str | None
    instagram_business_id: str | None
    instagram_graph_version: str | None
    contact_email: str
    db_path: Path
    backup_dir: Path

    @property
    def user_agent(self) -> str:
        return f"ar-scout/0.1 ({self.contact_email or 'no-contact-set'})"

    def source_status(self) -> dict[str, bool]:
        return {
            "deezer": True,
            "musicbrainz": True,
            "wikipedia": True,
            "lastfm": bool(self.lastfm_api_key),
            "youtube": bool(self.youtube_api_key),
            "spotify": bool(self.spotify_client_id and self.spotify_client_secret),
            "instagram": bool(self.instagram_access_token and self.instagram_business_id),
        }


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def load_settings() -> Settings:
    db = _env("AR_SCOUT_DB")
    return Settings(
        lastfm_api_key=_env("LASTFM_API_KEY"),
        youtube_api_key=_env("YOUTUBE_API_KEY"),
        spotify_client_id=_env("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=_env("SPOTIFY_CLIENT_SECRET"),
        instagram_access_token=_env("INSTAGRAM_ACCESS_TOKEN"),
        instagram_business_id=_env("INSTAGRAM_BUSINESS_ID"),
        instagram_graph_version=_env("INSTAGRAM_GRAPH_VERSION"),
        contact_email=_env("CONTACT_EMAIL") or "",
        db_path=Path(db) if db else PROJECT_ROOT / "data" / "ar_scout.db",
        backup_dir=Path(_env("AR_SCOUT_BACKUP_DIR") or PROJECT_ROOT / "data" / "backups").expanduser(),
    )


settings = load_settings()
