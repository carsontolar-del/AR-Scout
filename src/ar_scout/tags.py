"""Genre tag cleanup. Last.fm tags are user-entered, so they mix genres with places, opinions and library junk."""

from __future__ import annotations

import re

# Not genres: opinions, listening habits, instruments, platform and library bookkeeping.
_JUNK = {
    "all", "beer", "chilled", "collection", "favorite", "favorites", "favourite", "favourites", "fun", "ignorant",
    "jam", "melancholic", "quirky", "relaxing", "rutracker", "seen", "seen live", "spotify", "vocal",
    "male vocalists", "female vocalists", "cello", "drums", "american idol", "albums i own", "under 2000 listeners",
}

# Places and nationalities: the artist's country is shown separately. Exact matches only, so genres that name a
# place ("texas country", "uk bass", "midwest emo") stay.
_PLACES = {
    "america", "american", "usa", "united states", "us", "uk", "united kingdom", "british", "england", "english",
    "scottish", "scotland", "irish", "ireland", "welsh", "canada", "canadian", "australia", "australian",
    "new zealand", "norway", "norwegian", "finland", "finnish", "danish", "denmark", "french", "france", "german",
    "germany", "polish", "poland", "swedish", "sweden", "korean", "japanese", "emirati", "united arab emirates",
    "alabama", "texas", "maryland", "maine", "new york", "chicago", "seattle", "austin", "philadelphia",
    "brookline", "toronto", "manchester", "london", "los angeles", "nashville",
}

# One spelling per genre.
_SAME = {
    "trip hop": "trip-hop", "hip hop": "hip-hop", "singer-songwriters": "singer-songwriter",
    "singer songwriter": "singer-songwriter", "rnb": "r&b", "alt country": "alt-country", "lofi": "lo-fi",
    "lo fi": "lo-fi", "kpop": "k-pop", "post punk": "post-punk", "synth pop": "synthpop", "electro pop": "electropop",
}


def clean(tags: list[str] | None, limit: int = 6) -> list[str]:
    """Lowercased, de-duplicated genre tags with junk and place names removed, in their original order."""
    out: list[str] = []
    for raw in tags or []:
        t = re.sub(r"\s+", " ", str(raw)).strip().lower()
        if not t or "_" in t or not re.search(r"[a-z]", t) or t in _JUNK or t in _PLACES:
            continue
        t = _SAME.get(t, t)
        if t not in out:
            out.append(t)
    return out[:limit]
