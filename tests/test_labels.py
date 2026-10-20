from ar_scout import labels, scoring


def test_classifies_labels_from_real_data():
    c = labels.classify
    assert c("AMF Records (Island EMI UK/Island UK)", "Arkayla") == "major"
    assert c("Fiction Records - Man/Woman/Chainsaw", "Man/Woman/Chainsaw") == "major"
    assert c("sweetvoiced/A24 Music/RCA Records", "girlsweetvoiced") == "major"
    assert c("Thrivin Here Records/Warner Records Nashville", "Gavin Adcock") == "major"
    assert c("The Clause", "The Clause") == "self"
    assert c("Tow'rs Music LLC", "Tow'rs") == "self"
    assert c("Tall Heights", "Henry Jamison") == "indie"
    assert c("Mushroom Music/Virgin Music Group/ Antony Szmierek", "Antony Szmierek") == "indie"
    assert c("Transgressive Records", "Greg Freeman") == "indie"
    assert c("DistroKid", "Anyone") == "diy"
    assert c(None) is None


def test_signability_follows_label_class():
    s = lambda cls: scoring.score({}, {}, label_class=cls)["components"]["signability"]
    assert (s("self"), s("diy"), s("indie"), s("major"), s(None)) == (1.0, 1.0, 0.4, 0.0, None)
