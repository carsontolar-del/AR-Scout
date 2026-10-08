from datetime import date, timedelta

from ar_scout import scoring


def _series(start, end, days=30):
    t0 = "2026-09-01T00:00:00+00:00"
    t1 = f"2026-09-{1 + days:02d}T00:00:00+00:00" if days < 30 else "2026-10-01T00:00:00+00:00"
    return [(t0, start), (t1, end)]


def test_growth_monthly_rate():
    g = scoring.growth(_series(1000, 1300))
    assert abs(g["monthly_rate"] - 0.30) < 0.01
    assert g["delta"] == 300


def test_growth_needs_two_points():
    assert scoring.growth([("2026-09-01T00:00:00+00:00", 5)]) is None


def test_headroom_takes_most_conservative_platform():
    assert scoring.headroom_score({"deezer.fans": 5_000}) == 1.0
    big_on_lastfm = scoring.headroom_score({"deezer.fans": 5_000, "lastfm.listeners": 3_000_000})
    assert big_on_lastfm == 0.0
    assert scoring.headroom_score({}) is None


def test_fast_growing_unsigned_beats_flat_signed():
    recent = (date.today() - timedelta(days=10)).isoformat()
    hot = scoring.score(
        {"deezer.fans": 4_000, "lastfm.listeners": 20_000, "lastfm.playcount": 800_000},
        {"deezer.fans": _series(2_500, 4_000), "lastfm.listeners": _series(12_000, 20_000)},
        latest_release=recent, releases_last_year=6, diy_label=True)
    flat = scoring.score(
        {"deezer.fans": 4_000, "lastfm.listeners": 20_000, "lastfm.playcount": 120_000},
        {"deezer.fans": _series(4_000, 4_020), "lastfm.listeners": _series(20_000, 20_050)},
        latest_release="2024-01-01", releases_last_year=0, diy_label=False)
    assert hot["score"] > 80 > 40 > flat["score"]
    assert hot["confidence"] == 1.0
    assert any("unsigned" in f for f in hot["flags"])


def test_missing_components_renormalise():
    s = scoring.score({"deezer.fans": 5_000}, {})
    assert s["components"]["momentum"] is None
    assert s["score"] == 100.0 and s["confidence"] == 0.2
