"""Instagram follower counts via the Graph API's Business Discovery (free, needs a Meta app).

Reads public stats of any Instagram *professional* (Business or Creator) account by username, queried through
your own professional account. Personal and private accounts can't be looked up.
Needs a Facebook User access token with instagram_basic, instagram_manage_insights and pages_read_engagement.
"""

from __future__ import annotations

import re

from ..config import settings
from ..http import SourceError, get_json

# Paths on instagram.com that aren't profiles.
_RESERVED = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "about", "developer", "legal"}


class NotDiscoverable(SourceError):
    """The username doesn't exist, or isn't a professional account Business Discovery can see."""


def available() -> bool:
    return bool(settings.instagram_access_token and settings.instagram_business_id)


def _base() -> str:
    version = settings.instagram_graph_version
    return f"https://graph.facebook.com/{version}" if version else "https://graph.facebook.com"


def username_from_url(url: str) -> str | None:
    """'https://www.instagram.com/ofthetreesmusic/' or '@ofthetreesmusic' -> 'ofthetreesmusic'."""
    url = url.strip()
    if m := re.fullmatch(r"@?([A-Za-z0-9._]{1,30})", url):
        return m.group(1).lower()
    m = re.search(r"instagram\.com/([A-Za-z0-9._]{1,30})/?(?:[?#]|$)", url)
    if not m or m.group(1).lower() in _RESERVED:
        return None
    return m.group(1).lower()


def profile(username: str) -> dict:
    """Follower and post counts for a professional account. Raises NotDiscoverable for personal/missing
    accounts and SourceError for setup problems (expired token, missing permission, rate limit)."""
    if not available():
        raise SourceError("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ID not set")
    fields = f"business_discovery.username({username}){{username,name,followers_count,media_count,biography,website}}"
    try:
        data = get_json(f"{_base()}/{settings.instagram_business_id}",
                        params={"fields": fields, "access_token": settings.instagram_access_token})
    except SourceError as exc:
        raise _explain(exc, username) from exc
    bd = (data or {}).get("business_discovery")
    if not bd:
        raise NotDiscoverable(f"@{username} returned no data")
    return {
        "username": bd.get("username", username),
        "name": bd.get("name"),
        "followers": bd.get("followers_count"),
        "media_count": bd.get("media_count"),
        "biography": bd.get("biography"),
        "website": bd.get("website"),
    }


def _explain(exc: SourceError, username: str) -> SourceError:
    msg = str(exc)
    # Graph errors look like {"error":{"message":"(#110) Invalid user id",...,"code":110}}; the HTTP helper
    # truncates the body, so the "(#110)" prefix is the reliable place to read the code from.
    m = re.search(r'\(#(\d+)\)|"code":\s*(\d+)', msg)
    code = int(m.group(1) or m.group(2)) if m else None
    if code == 110 or "Invalid user id" in msg or "Cannot find User" in msg:
        return NotDiscoverable(f"@{username} isn't a public professional account (or doesn't exist)")
    if code == 190 or "Error validating access token" in msg or "Invalid OAuth access token" in msg:
        return SourceError("Instagram access token expired or invalid: generate a new one (see .env.example)")
    if code in (4, 17, 32, 613):
        return SourceError("Instagram rate limit reached: try again in an hour")
    if code in (10, 200):
        return SourceError("Instagram token is missing a permission: needs instagram_basic, "
                           "instagram_manage_insights and pages_read_engagement")
    return exc
