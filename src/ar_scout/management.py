"""Does an artist already have a manager? Read from their Instagram bio, or set by a person."""

from __future__ import annotations

import re

STATUSES = {"managed": "Has management", "unmanaged": "No management", "listed": "Management listed",
            "not_listed": "No management listed"}

_MGMT = re.compile(r"\b(mgmt|mngmt|management|managed by|manager|manage(?:r|ment)?\s*[:@])", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def from_bio(bio: str | None, website: str | None = None) -> dict | None:
    """'listed' with the matching line as evidence when the bio mentions management; 'not_listed' when there is
    a bio without it; None when there's no bio to read. Booking agents alone don't count as management."""
    text = " ".join(x for x in (bio, website) if x)
    if not text.strip():
        return None
    for line in re.split(r"[\n|•·]+", text):
        if _MGMT.search(line):
            return {"status": "listed", "evidence": line.strip()[:160]}
    return {"status": "not_listed", "evidence": None}


def resolve(manual: str | None, auto: str | None, evidence: str | None) -> dict:
    """A person's choice wins over the bio."""
    if manual in ("managed", "unmanaged"):
        return {"status": manual, "label": STATUSES[manual], "source": "you", "evidence": None}
    if auto in ("listed", "not_listed"):
        return {"status": auto, "label": STATUSES[auto], "source": "instagram bio", "evidence": evidence}
    return {"status": None, "label": "Unknown", "source": None, "evidence": None}


def is_managed(info: dict) -> bool | None:
    return {"managed": True, "listed": True, "unmanaged": False, "not_listed": False}.get(info["status"])
