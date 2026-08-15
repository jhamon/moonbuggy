"""Criterion D4 / risk 3 in section 4.2: tracebacks must quote mutated source.

Python's traceback machinery reads source lines from linecache by filename. Exec
mutated code without populating linecache and every traceback quotes the
ORIGINAL line -- so the reported line and the code that actually ran disagree,
which is worse than no source at all in a tool whose entire output is diagnostic.

Tested directly rather than through a pytest subprocess, because it needs a
frame INSIDE the mutated module. A failing assertion in a test file produces a
traceback that never enters the module under test, so it cannot see this.
"""

import sys
import traceback

import pytest

from moonbuggy.inmemory import install, uninstall_all

# The mutated line must be the line that RAISES, since that is the line a
# traceback quotes. Mutating any other line proves nothing about linecache.
SOURCE = '''\
def boom():
    raise ValueError("original")
'''
MUTATED_LINE = 'raise ValueError("mutated")'


@pytest.fixture
def probe(tmp_path):
    module = tmp_path / "linecache_probe.py"
    module.write_text(SOURCE)
    sys.path.insert(0, str(tmp_path))
    yield module
    sys.path.remove(str(tmp_path))
    sys.modules.pop("linecache_probe", None)
    uninstall_all()


def formatted_traceback_from(module_name):
    module = __import__(module_name)
    try:
        module.boom()
    except ValueError:
        return traceback.format_exc()
    raise AssertionError("probe did not raise")


def test_traceback_quotes_the_mutated_line(probe):
    # The file on disk still says "original", so the mutated text can only have
    # reached the traceback through linecache. That is the whole check.
    install(str(probe), 2, MUTATED_LINE)

    formatted = formatted_traceback_from("linecache_probe")

    assert MUTATED_LINE in formatted
    assert 'raise ValueError("original")' not in formatted


def test_mutation_actually_took_effect(probe):
    # Guards the test above: if the mutation silently did nothing, a stale
    # linecache entry could still satisfy the assertion on source text.
    install(str(probe), 2, MUTATED_LINE)

    formatted = formatted_traceback_from("linecache_probe")

    assert "ValueError: mutated" in formatted


def test_unmutated_module_is_unaffected(probe):
    formatted = formatted_traceback_from("linecache_probe")

    assert 'raise ValueError("original")' in formatted


def test_uninstall_restores_original_source(probe):
    install(str(probe), 2, MUTATED_LINE)
    formatted_traceback_from("linecache_probe")

    uninstall_all()
    sys.modules.pop("linecache_probe", None)
    formatted = formatted_traceback_from("linecache_probe")

    assert 'raise ValueError("original")' in formatted
    assert MUTATED_LINE not in formatted
