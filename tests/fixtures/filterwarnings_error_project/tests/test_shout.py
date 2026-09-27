from widget import clamp


def test_clamp_negative():
    assert clamp(-3) == 0


def test_clamp_positive():
    assert clamp(7) == 7
