from ar_scout import tags


def test_drops_junk_and_places_and_merges_spellings():
    raw = ["Lo-Fi", "british", "funk_add_to_lidarr_batch_23", "indie rock", "trip hop", "Trip-Hop", "3",
           "seen", "texas country", "USA", "singer-songwriters"]
    assert tags.clean(raw) == ["lo-fi", "indie rock", "trip-hop", "texas country", "singer-songwriter"]


def test_limit_applies_after_cleaning():
    assert tags.clean(["usa", "uk", "a", "b", "c", "d", "e", "f", "g"]) == ["a", "b", "c", "d", "e", "f"]


def test_electronic_reads_the_top_three_tags():
    from ar_scout.tags import is_electronic
    assert is_electronic(["dubstep", "riddim"]) and is_electronic(["deep house", "ambient"])
    assert is_electronic(["pop", "uk garage", "electronic"]) and is_electronic(["garage", "house"])
    assert not is_electronic(["garage rock", "psychedelic rock"])  # The Dirty Turkeys
    assert not is_electronic(["indie pop", "electropop", "pop"])
    assert not is_electronic(["indie rock", "indie", "alternative", "electronic"])  # 4th tag doesn't count
    assert not is_electronic([]) and not is_electronic(None)
