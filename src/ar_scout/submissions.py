"""Demo submissions and messages from artists, and songs fans want to share, sent through the public links.

Anyone can send one, so everything is checked here before it's stored: required fields, lengths, a real
http(s) link and a plausible email. Nothing is sent anywhere; the team reads them on the dashboard's
Submissions page and replies from their own email.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from . import db

KINDS = {"artist": ("demo", "message"), "fan": ("song",)}
STATUSES = ("new", "listened", "shortlisted", "replied", "passed")
GENRES = ("Indie rock", "Indie pop / dream pop", "Post-punk / shoegaze", "Folk / singer-songwriter",
          "Country / Americana", "Electronic", "R&B / soul", "Hip-hop / rap", "Pop", "Experimental", "Other")

# Longest accepted value per field; anything longer is a mistake or spam.
MAX_LEN = {"name": 120, "email": 200, "artist_name": 160, "song_title": 200, "link": 500, "genre": 60,
           "location": 120, "socials": 300, "message": 4000, "team_note": 2000}
# Required fields per (kind, topic), with the words used in error messages.
REQUIRED = {
    ("artist", "demo"): {"name": "your name", "email": "your email", "artist_name": "the artist or band name",
                         "song_title": "the song title", "link": "a link to the song"},
    ("artist", "message"): {"name": "your name", "email": "your email", "artist_name": "the artist or band name",
                            "message": "a message"},
    ("fan", "song"): {"artist_name": "the artist", "song_title": "the song title", "link": "a link to the song"},
}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[ \t]+", " ", str(value)).strip()
    return text or None


def check_link(link: str) -> str:
    """A full http(s) URL. Adds https:// when someone pastes 'soundcloud.com/...'."""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", link, re.I):
        link = "https://" + link
    u = urlparse(link)
    if u.scheme not in ("http", "https") or "." not in (u.hostname or ""):
        raise ValueError("The link doesn't look right. Paste the full address, like https://soundcloud.com/you/song.")
    return link


def submit(kind: str, topic: str, data: dict) -> dict:
    """Validate and store one submission. Raises ValueError with a message meant for the person sending it."""
    if kind not in KINDS or topic not in KINDS[kind]:
        raise ValueError("Unknown submission type.")
    fields: dict = {"kind": kind, "topic": topic}
    for key, limit in MAX_LEN.items():
        if key == "team_note":
            continue
        value = _clean(data.get(key))
        if value and len(value) > limit:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} is too long (the limit is {limit} characters).")
        fields[key] = value
    missing = [label for key, label in REQUIRED[(kind, topic)].items() if not fields.get(key)]
    if missing:
        raise ValueError("Please add " + ", ".join(missing[:-1]) + (" and " if len(missing) > 1 else "") + missing[-1] + ".")
    if fields.get("email") and not EMAIL.match(fields["email"]):
        raise ValueError("That email address doesn't look right.")
    if fields.get("link"):
        fields["link"] = check_link(fields["link"])
    if kind == "artist" and not data.get("consent"):
        raise ValueError("Please confirm you're OK with the team contacting you by email.")
    if kind == "artist" and topic == "demo" and not data.get("rights"):
        raise ValueError("Please confirm you own or control the rights to this song.")
    fields["credit_ok"] = 1 if (kind == "fan" and data.get("credit_ok")) else 0
    return db.get_submission(db.add_submission(fields))


def update(submission_id: int, status: str | None = None, team_note: str | None = None) -> dict:
    if not db.get_submission(submission_id):
        raise ValueError("That submission no longer exists.")
    fields = {}
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(STATUSES)}.")
        fields["status"] = status
    if team_note is not None:
        fields["team_note"] = (_clean(team_note) or "")[:MAX_LEN["team_note"]] or None
    db.update_submission(submission_id, **fields)
    return db.get_submission(submission_id)


def counts() -> dict:
    """New (unread) submissions per kind, for the dashboard's sidebar."""
    rows = db.submissions(status="new", limit=100_000)
    return {"artist": sum(r["kind"] == "artist" for r in rows), "fan": sum(r["kind"] == "fan" for r in rows)}
