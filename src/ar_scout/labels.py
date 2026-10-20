"""Sort a release's label line into who controls the artist's records: major, independent, self-released or DIY."""

from __future__ import annotations

import re

from .sources.deezer import is_diy_label

# The three majors (plus BMG) and their imprints. Matched as whole words in the label line, which often lists
# several companies ("Ourness/Capitol Records", "Thrivin Here Records/Warner Records Nashville").
MAJOR = (
    # Universal
    "universal", "umg", "island", "amf records", "fiction records", "polydor", "virgin records", "capitol",
    "interscope", "republic records", "def jam", "geffen", "mca nashville", "mercury", "decca", "motown", "emi",
    # Sony
    "sony", "columbia", "rca", "epic records", "arista", "legacy recordings",
    # Warner
    "warner", "atlantic records", "elektra", "parlophone", "asylum", "reprise", "wm australia", "nonesuch", "sire",
    "300 entertainment",
    # largest independent, effectively a major
    "bmg",
)
_MAJOR_RE = re.compile(r"\b(" + "|".join(re.escape(m) for m in MAJOR) + r")\b", re.I)

CLASSES = {
    "diy": "DIY distributor",
    "self": "Self-released",
    "indie": "Independent label",
    "major": "Major label",
}


# Words a self-released label line adds to the artist's name ("Tow'rs Music LLC").
_GENERIC = ("recordings", "records", "music", "entertainment", "enterprises", "productions", "company", "group",
            "label", "media", "llc", "inc", "ltd", "pty", "the", "co")


def _key(s: str) -> str:
    return re.sub(r"[\W_]", "", s.casefold().replace("&", "and"))


def classify(label: str | None, artist: str | None = None) -> str | None:
    """'diy', 'major', 'self' or 'indie'; None when there's no label to go on."""
    if not label or not label.strip():
        return None
    if is_diy_label(label):
        return "diy"
    if _MAJOR_RE.search(label):
        return "major"
    a = _key(artist or "")
    rest = _key(label).replace(a, "", 1) if len(a) >= 3 and a in _key(label) else None
    if rest is not None:
        for word in _GENERIC:
            rest = rest.replace(word, "")
        if not rest:  # nothing but the artist's name: "The Clause", "Tow'rs Music LLC"
            return "self"
    return "indie"
