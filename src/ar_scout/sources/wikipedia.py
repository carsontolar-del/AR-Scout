"""Wikipedia pageviews (no key) — a cheap proxy for mainstream attention."""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

from ..http import get_json


def enwiki_title_from_wikidata(wikidata_url: str) -> str | None:
    qid = wikidata_url.rstrip("/").rsplit("/", 1)[-1]
    payload = get_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    entity = (payload or {}).get("entities", {}).get(qid, {})
    return entity.get("sitelinks", {}).get("enwiki", {}).get("title")


def pageviews(title: str, days: int = 30, end: date | None = None) -> int | None:
    """Views over the `days` days ending `end` (default: yesterday, the latest full day)."""
    end = end or date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
        f"{quote(title.replace(' ', '_'), safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}"
    )
    payload = get_json(url)
    if not payload:
        return None
    return sum(item.get("views", 0) for item in payload.get("items", []))
