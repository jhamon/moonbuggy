from widget import clamp


def test_clamp_negative():
    assert clamp(-3) == 0


def test_clamp_positive():
    assert clamp(7) == 7


# Same function name as a test in test_shout.py. This is the humanize 4.16.0
# shape: the coverage recorder records `tests/test_dup.py::test_clamp_negative`
# (and the twin from test_shout.py); pytest 9's perform_collect, given both
# ids at once, resolves the duplicate name against the first module it
# collected and raises "not found" for the second -- precollect cannot build
# a session, and before the deregister fix every mutant fell back into a
# crashed grandchild (SUSPICIOUS for the whole run).
def test_clamp_negative():
    assert clamp(-1) == 0
