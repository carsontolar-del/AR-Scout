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
        latest_release=recent, releases_last_year=6, label_class="diy")
    flat = scoring.score(
        {"deezer.fans": 4_000, "lastfm.listeners": 20_000, "lastfm.playcount": 120_000},
        {"deezer.fans": _series(4_000, 4_020), "lastfm.listeners": _series(20_000, 20_050)},
        latest_release="2024-01-01", releases_last_year=0, label_class="indie")
    assert hot["score"] > 80 > 40 > flat["score"]
    assert hot["confidence"] == 1.0
    assert any("unsigned" in f for f in hot["flags"])


def test_missing_components_renormalise():
    s = scoring.score({"deezer.fans": 5_000}, {})
    assert s["components"]["momentum"] is None
    assert s["score"] == 100.0 and s["confidence"] == 0.2


def test_famous_artist_is_capped_despite_strong_engagement():
    recent = (date.today() - timedelta(days=10)).isoformat()
    common = dict(history={}, latest_release=recent, releases_last_year=8, label_class="indie")
    star = scoring.score({"lastfm.listeners": 3_000_000, "lastfm.playcount": 150_000_000}, **common)
    rising = scoring.score({"lastfm.listeners": 40_000, "lastfm.playcount": 600_000}, **common)
    assert star["components"]["headroom"] == 0.0
    assert star["score"] < 30 < rising["score"]
    assert "score capped: audience already large" in star["flags"]


def test_wikipedia_traffic_counts_against_headroom_only():
    small = {"lastfm.listeners": 20_000}
    assert scoring.headroom_score(small) == 1.0
    assert scoring.headroom_score({**small, "wikipedia.pageviews_30d": 2_000}) == 1.0
    assert scoring.headroom_score({**small, "wikipedia.pageviews_30d": 400_000}) == 0.0


def test_tiny_channel_on_one_platform_does_not_erase_traction():
    assert scoring.headroom_score({"lastfm.listeners": 140_000, "youtube.subscribers": 100}) > 0.8
    assert scoring.headroom_score({"lastfm.listeners": 100, "youtube.subscribers": 50}) == 0.0


def test_flags_youtube_channel_too_small_for_audience():
    flags = lambda m: scoring.score(m, {})["flags"]
    suspect = "YouTube count looks off: check the linked channel"
    assert suspect in flags({"lastfm.listeners": 87_000, "lastfm.playcount": 500_000, "youtube.subscribers": 0})
    assert suspect not in flags({"lastfm.listeners": 87_000, "lastfm.playcount": 500_000, "youtube.subscribers": 11_800})
    assert suspect not in flags({"lastfm.listeners": 5_000, "lastfm.playcount": 50_000, "youtube.subscribers": 10})


def test_channel_with_no_videos_is_suspect():
    assert scoring.youtube_suspect({"youtube.subscribers": 627, "youtube.videos": 0, "lastfm.listeners": 3_000})
    assert not scoring.youtube_suspect({"lastfm.listeners": 80_000})  # no channel at all isn't "wrong"


def test_no_growth_rate_from_a_single_day():
    one_day = [("2026-10-12T03:00:00+00:00", 7_730), ("2026-10-13T00:30:00+00:00", 7_770)]
    g = scoring.growth(one_day)
    assert g["delta"] == 40 and g["monthly_rate"] is None
    s = scoring.score({"youtube.subscribers": 7_770}, {"youtube.subscribers": one_day})
    assert s["components"]["momentum"] is None


def _daily(start_day, values):
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(f"{start_day}T15:00:00+00:00")
    return [((t0 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(values)]


def test_acceleration_needs_two_platforms_speeding_up():
    # Sophia Stel, Oct 13-20: slow until the Oct 16 album, then fast
    hist = {"youtube.subscribers": _daily("2026-10-13", [5390, 5410, 5440, 5490, 5710, 5830, 5900, 5970]),
            "deezer.fans": _daily("2026-10-13", [751, 753, 759, 766, 783, 791, 797, 806])}
    acc = scoring.acceleration(hist)
    assert acc["accelerating"] and set(acc["faster"]) == {"youtube.subscribers", "deezer.fans"}
    assert acc["platforms"]["youtube.subscribers"]["ratio"] > 3
    assert scoring.score({}, hist)["flags"][0].startswith("accelerating")

    one = {"youtube.subscribers": hist["youtube.subscribers"]}
    assert not scoring.acceleration(one)["accelerating"]


def test_steady_growth_is_not_acceleration():
    steady = {m: _daily("2026-10-13", [1000 + 10 * i for i in range(8)]) for m in ("deezer.fans", "lastfm.listeners")}
    assert not scoring.acceleration(steady)["accelerating"]


def test_window_change_is_actual_not_projected():
    pts = _daily("2026-10-13", [5390, 5410, 5440, 5490, 5710, 5830, 5900, 5970])
    c = scoring.window_change(pts, 7)
    assert c["delta"] == 580 and c["days"] == 7.0
    assert scoring.window_change(pts, 28) is None
