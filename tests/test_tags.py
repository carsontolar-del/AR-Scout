from ar_scout import tags


def test_drops_junk_and_places_and_merges_spellings():
    raw = ["Lo-Fi", "british", "funk_add_to_lidarr_batch_23", "indie rock", "trip hop", "Trip-Hop", "3",
           "seen", "texas country", "USA", "singer-songwriters"]
    assert tags.clean(raw) == ["lo-fi", "indie rock", "trip-hop", "texas country", "singer-songwriter"]


def test_limit_applies_after_cleaning():
    assert tags.clean(["usa", "uk", "a", "b", "c", "d", "e", "f", "g"]) == ["a", "b", "c", "d", "e", "f"]
