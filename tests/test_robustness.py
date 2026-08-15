"""Milestone M1.4 -- hostile inputs.

The requirement throughout is that moonbuggy **degrades visibly rather than
lying**. A wrong status is far worse than a refusal: a false SURVIVED is
indistinguishable from a real finding, so it costs the user an investigation
that ends nowhere, and it costs the tool its credibility for every other
finding in the same report.

Each test below is one row of the M1.4 table. They are marked slow because each
drives the real CLI in a subprocess -- which is the point, since several of the
behaviours under test are properties of process lifecycle rather than of any
function moonbuggy could call in-process.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import (
    assert_no_traceback,
    moonbuggy,
    records,
    status_of,
    status_of_mutation,
    write_project,
)

pytestmark = pytest.mark.slow


# --------------------------------------------------------------------------
# M1.4.1 -- a source file with a syntax error
# --------------------------------------------------------------------------

def test_syntax_error_names_the_file_and_the_rest_still_runs(tmp_path):
    project = write_project(tmp_path, {
        "good.py": "def double(x):\n    return x * 2\n",
        "broken.py": "def oops(:\n    return\n",
        "test_good.py": "from good import double\n\ndef test_double():\n    assert double(2) == 4\n",
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert "broken.py" in proc.stderr
    assert "syntax error at line 1" in proc.stderr
    # The whole point: one broken file is not a reason to abandon the others.
    assert {r["file"] for r in records(project)} == {"good.py"}


def test_a_project_of_only_broken_files_exits_two(tmp_path):
    project = write_project(tmp_path, {
        "broken.py": "def oops(:\n",
        "test_x.py": "def test_nothing():\n    assert True\n",
    })

    proc = moonbuggy(cwd=project, expect=2)

    assert_no_traceback(proc)
    # Distinct from "no mutants generated", which would read as good news.
    assert "nothing to mutate" in proc.stderr
    assert "broken.py" in proc.stderr


# --------------------------------------------------------------------------
# M1.4.2 -- a module with import-time side effects
# --------------------------------------------------------------------------

SIDE_EFFECTS_AT_IMPORT = '''\
import socket
from pathlib import Path

# Both of the side effects the milestone names, at import time: a file write
# and an open socket. Every mutant re-imports or re-executes this module, so
# anything that leaks per import shows up multiplied by the mutant count.
_LOG = Path(__file__).with_name("import-log.txt")
_LOG.write_text(_LOG.read_text() + "x" if _LOG.exists() else "x")

_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_SOCKET.bind(("127.0.0.1", 0))

BASE = 4 + 1


def scaled(value):
    return value * BASE
'''


def test_import_time_side_effects_do_not_corrupt_statuses(tmp_path):
    project = write_project(tmp_path, {
        "sideff.py": SIDE_EFFECTS_AT_IMPORT,
        "test_sideff.py": (
            "from sideff import scaled\n\n"
            "def test_scaled():\n    assert scaled(2) == 10\n"
        ),
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr
    # BASE = 4 + 1 is module level, so selection widens to the whole suite and
    # the mutant must be caught. Getting SURVIVED here would mean the mutation
    # never reached the process that ran the test.
    assert status_of_mutation(project, "BASE = 4 + 1", "BASE = 4 - 1") == "KILLED"


# --------------------------------------------------------------------------
# M1.4.3 -- a genuinely flaky test
# --------------------------------------------------------------------------

FLAKY_TESTS = '''\
from pathlib import Path

from unstable import add, multiply

COUNTER = Path(__file__).with_name("counter.txt")


def test_add_but_flaky():
    """Fails every other run. Deterministically alternating rather than random,
    so this test suite is itself reproducible -- a probabilistic fixture would
    make the assertion below flaky, which is a poor way to test flake handling.
    Alternating is the worst case for a single probe, not the easiest: it
    guarantees the two baseline runs disagree, which is exactly the signal.
    """
    seen = int(COUNTER.read_text()) if COUNTER.exists() else 0
    COUNTER.write_text(str(seen + 1))
    assert add(1, 2) == 3
    assert seen % 2 == 0, "unstable by design"


def test_multiply_is_stable():
    assert multiply(3, 4) == 12
'''


def test_flaky_tests_make_their_mutants_suspicious_not_confident(tmp_path):
    project = write_project(tmp_path, {
        "unstable.py": "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n",
        "test_unstable.py": FLAKY_TESTS,
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr
    # `a + b` is covered only by the flaky test, so no confident claim about it
    # is available -- SUSPICIOUS is the honest answer.
    assert status_of_mutation(project, "return a + b", "return a - b") == "SUSPICIOUS"
    # `a * b` is covered only by the stable test, so flakiness elsewhere must
    # not contaminate it. A tool that gave up on the whole run would be safe
    # and useless.
    assert status_of_mutation(project, "return a * b", "return a / b") == "KILLED"


def test_disabling_the_probe_gives_up_the_guarantee_and_says_so(tmp_path):
    """--flaky-probe 0 is allowed, and then the flake is undetectable.

    Recorded as a test because it is the cost of the flag: without a second
    baseline run there is no evidence of flakiness, and the status reverts to a
    confident one that may be wrong.
    """
    project = write_project(tmp_path, {
        "unstable.py": "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n",
        "test_unstable.py": FLAKY_TESTS,
    })

    moonbuggy("--flaky-probe", "0", cwd=project)

    assert status_of_mutation(project, "return a + b", "return a - b") != "SUSPICIOUS"


# --------------------------------------------------------------------------
# M1.4.4 -- a suite that is already red
# --------------------------------------------------------------------------

def test_red_baseline_is_refused_with_no_results_claimed(tmp_path):
    project = write_project(tmp_path, {
        "lib.py": "def double(x):\n    return x * 2\n",
        "test_lib.py": (
            "from lib import double\n\n"
            "def test_passes():\n    assert double(2) == 4\n\n"
            "def test_already_broken():\n    assert double(2) == 5\n"
        ),
    })

    proc = moonbuggy(cwd=project, expect=2)

    assert_no_traceback(proc)
    assert "already failing" in proc.stderr
    assert "test_already_broken" in proc.stderr
    assert "No mutation results were produced." in proc.stderr
    # Nothing may be reported: a mutant covered by a permanently failing test
    # would be KILLED regardless of the mutation, which flatters the score.
    assert not (project / ".moonbuggy" / "results.jsonl").read_text().strip()


# --------------------------------------------------------------------------
# M1.4.5 -- code under test spawning threads
# --------------------------------------------------------------------------

THREADED = '''\
import threading


def count_to(limit):
    """Counts in a worker thread. A mutation that makes the loop never
    terminate leaves a live non-daemon thread and a join that never returns --
    the shape of hang that has to be bounded by the timeout rather than by
    luck."""
    reached = []

    def work():
        n = 0
        while n < limit:
            n += 1
        reached.append(n)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    return reached[0]
'''


def test_threaded_code_completes_and_hangs_are_bounded_by_the_timeout(tmp_path):
    project = write_project(tmp_path, {
        "threaded.py": THREADED,
        "test_threaded.py": (
            "from threaded import count_to\n\n"
            "def test_counts():\n    assert count_to(5) == 5\n"
        ),
    })

    started = time.monotonic()
    proc = moonbuggy("--timeout", "5", cwd=project, timeout=120)
    elapsed = time.monotonic() - started

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr
    # `n += 1` -> `n -= 1` never reaches the limit. It must be reported, and
    # reported as a timeout rather than as a survivor.
    assert status_of_mutation(project, "n += 1", "n -= 1") == "TIMEOUT"
    # The parent exiting at all is the leaked-thread check: a non-daemon thread
    # inherited into the reporting process would keep the interpreter alive.
    assert elapsed < 90, f"run took {elapsed:.1f}s, which is not a bounded hang"


# --------------------------------------------------------------------------
# M1.4.6 -- a test that calls sys.exit() / os._exit()
# --------------------------------------------------------------------------

EXITING = '''\
import os
import sys


def guard(value, scale):
    # Unmutated, 3 * 1 is not greater than 3 and the process survives. Swap the
    # comparison and the code under test kills its own interpreter outright --
    # no exception, no exit code, nothing for pytest to report.
    if value * scale > 3:
        os._exit(0)
    return value + scale


def bail(value):
    if value < 0:
        sys.exit("negative")
    return value
'''


def test_a_mutant_that_kills_its_own_process_is_still_classified(tmp_path):
    project = write_project(tmp_path, {
        "exiting.py": EXITING,
        "test_exiting.py": (
            "import pytest\n\n"
            "from exiting import bail, guard\n\n"
            "def test_guard():\n    assert guard(3, 1) == 4\n\n"
            "def test_bail_exits():\n"
            "    with pytest.raises(SystemExit):\n        bail(-1)\n\n"
            "def test_bail_passes_through():\n    assert bail(2) == 2\n"
        ),
    })

    proc = moonbuggy("--timeout", "10", cwd=project, timeout=180)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr

    # os._exit leaves no exit code and no output. The only honest reading is
    # "we could not tell" -- but a reading there must be, and every mutant must
    # still appear in the report.
    assert status_of_mutation(
        project, "if value * scale > 3:", "if value * scale >= 3:"
    ) == "SUSPICIOUS"
    ids = {r["id"] for r in records(project)}
    assert any("exiting.py:15" in i for i in ids), "the sys.exit() branch went missing"
    assert all(r["status"] in {
        "KILLED", "SURVIVED", "TIMEOUT", "SUSPICIOUS", "SKIPPED"
    } for r in records(project))


# --------------------------------------------------------------------------
# M1.4.7 -- non-UTF8 / unusual encoding
# --------------------------------------------------------------------------

LATIN1_SOURCE = (
    "# -*- coding: latin-1 -*-\n"
    "CURRENCY = '\u00a3'\n"
    "\n"
    "def price(pence):\n"
    "    return pence * 2\n"
).encode("latin-1")


def test_a_declared_latin1_module_is_mutated_correctly(tmp_path):
    """Declared encodings are handled, not refused.

    The dangerous failure here is silent: read as UTF-8 the pound sign is a
    decode error, and read with `errors='replace'` it would be mutated into a
    different character. Either way the mutant's source is not the user's
    source, and the status it produces is about a file that does not exist.
    """
    project = write_project(tmp_path, {
        "money.py": LATIN1_SOURCE,
        "test_money.py": (
            "from money import CURRENCY, price\n\n"
            "def test_price():\n    assert price(3) == 6\n\n"
            "def test_currency_symbol_survives():\n"
            "    assert CURRENCY == '\\u00a3'\n"
        ),
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert proc.returncode in (0, 1), proc.stderr
    assert status_of_mutation(project, "return pence * 2", "return pence / 2") == "KILLED"
    # And the file is left exactly as it was found.
    assert (project / "money.py").read_bytes() == LATIN1_SOURCE


def test_undeclared_non_utf8_bytes_are_refused_by_name(tmp_path):
    project = write_project(tmp_path, {
        # No coding cookie, so PEP 263 says UTF-8, and these bytes are not.
        "mojibake.py": b"VALUE = '\xff\xfe'\n\n\ndef double(x):\n    return x * 2\n",
        "good.py": "def triple(x):\n    return x * 3\n",
        "test_good.py": "from good import triple\n\ndef test_triple():\n    assert triple(2) == 6\n",
    })

    proc = moonbuggy(cwd=project)

    assert_no_traceback(proc)
    assert "mojibake.py" in proc.stderr
    assert "cannot decode" in proc.stderr
    assert {r["file"] for r in records(project)} == {"good.py"}


# --------------------------------------------------------------------------
# M1.4.9 -- empty project / no tests / no source
# --------------------------------------------------------------------------

def test_an_empty_directory_exits_two(tmp_path):
    proc = moonbuggy(cwd=tmp_path, expect=2)

    assert_no_traceback(proc)
    assert "does not look like a pytest project" in proc.stderr


def test_a_project_with_no_source_exits_two(tmp_path):
    project = write_project(tmp_path, {
        "test_only.py": "def test_nothing():\n    assert True\n",
    })

    proc = moonbuggy(cwd=project, expect=2)

    assert_no_traceback(proc)
    assert "No Python source found" in proc.stderr


def test_a_project_with_no_tests_exits_two(tmp_path):
    project = write_project(tmp_path, {
        "lib.py": "def double(x):\n    return x * 2\n",
    })

    proc = moonbuggy(cwd=project, expect=2)

    assert_no_traceback(proc)
    assert "no tests ran" in proc.stderr


# --------------------------------------------------------------------------
# M1.4.10 -- conftest fixtures with side effects between tests
# --------------------------------------------------------------------------

FIXTURE_CONFTEST = '''\
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from widgets import prepare

TRAIL = Path(__file__).with_name("fixture-trail.txt")


@pytest.fixture
def widget(tmp_path):
    """A fixture with a real side effect between tests: it appends to a file
    that outlives the test. The line it calls in `widgets` executes during
    setup, never inside a test body -- so a coverage map that only credited
    test-body execution would attribute it to nothing and report SURVIVED."""
    with TRAIL.open("a") as handle:
        handle.write("setup\\n")
    return prepare(tmp_path / "log.txt")
'''


def test_selection_is_still_correct_under_side_effecting_fixtures(tmp_path):
    """M1.4.10, checked against the naive oracle rather than against intuition.

    The naive runner shares no selection logic with the fast path -- it copies
    the tree, edits the file and runs the entire suite -- so agreement between
    them is evidence about selection specifically.
    """
    project = write_project(tmp_path, {
        "widgets.py": (
            "def prepare(path):\n"
            "    path.write_text('start')\n"
            "    return {'size': 4 + 1}\n"
            "\n\n"
            "def consume(widget):\n"
            "    return widget['size'] + 1\n"
        ),
        "conftest.py": FIXTURE_CONFTEST,
        "test_widgets.py": (
            "from widgets import consume\n\n"
            "def test_size(widget):\n    assert widget['size'] == 5\n\n"
            "def test_consume(widget):\n    assert consume(widget) == 6\n"
        ),
    })

    proc = moonbuggy(cwd=project)
    assert_no_traceback(proc)
    fast = {r["id"]: r["status"] for r in records(project)}

    assert fast, "no mutants were reported"
    assert _naive_statuses(project) == fast


def _naive_statuses(project):
    """Run every mutant the slow, obvious way, in a subprocess.

    Out of process because the naive runner copies the project tree and runs
    pytest inside it; doing that from the test runner's own process would put
    two conflicting conftest files on the path.
    """
    script = '''
import json, sys
from pathlib import Path
sys.path.insert(0, %r)
from moonbuggy.generate import generate_mutants
from moonbuggy.naive import run_naive
from moonbuggy.srcio import read_source

project = Path(%r)
mutants = []
for path in sorted(project.glob("*.py")):
    if path.name.startswith("test_") or path.name in {"conftest.py", "setup.py"}:
        continue
    mutants += generate_mutants(read_source(path), module=path.name)
statuses = run_naive(project, mutants)
print(json.dumps({m.id: s for m, s in statuses.items()}))
''' % (str(Path(__file__).resolve().parents[1] / "src"), str(project))

    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------
# M1.4.13 -- crash recovery
# --------------------------------------------------------------------------

def test_a_killed_run_leaves_a_readable_jsonl_and_a_usable_cache(tmp_path):
    """Kill the process mid-flight; both artifacts must survive it.

    `SIGKILL` rather than `SIGTERM` deliberately: a handler would let moonbuggy
    tidy up, and the claim being tested is that the on-disk state is valid at
    every instant without any tidying.
    """
    project = _slow_project(tmp_path)

    moonbuggy(cwd=project)  # A complete run first, so there is a cache to damage.
    assert (project / ".moonbuggy" / "cache.json").exists()

    proc = subprocess.Popen(
        [sys.executable, "-m", "moonbuggy.cli", "--clear-cache"],
        cwd=project, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    jsonl = project / ".moonbuggy" / "results.jsonl"
    _wait_for_a_line(jsonl, proc)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)

    # Whatever made it to disk must parse. Partial is fine; truncated is not.
    for line in jsonl.read_text().splitlines():
        if line.strip():
            assert json.loads(line)["status"]

    # And the next run must not trip over anything left behind.
    after = moonbuggy(cwd=project)
    assert_no_traceback(after)
    assert after.returncode in (0, 1), after.stderr


def _slow_project(root):
    """A project whose mutants take long enough to be interrupted partway."""
    functions = "\n\n".join(
        f"def step_{n}(x):\n    return x + {n}" for n in range(25)
    )
    tests = "import time\n\nimport slowlib\n\n" + "\n\n".join(
        f"def test_step_{n}():\n    time.sleep(0.05)\n"
        f"    assert slowlib.step_{n}(1) == {n + 1}"
        for n in range(25)
    )
    return write_project(root, {"slowlib.py": functions + "\n", "test_slow.py": tests + "\n"})


def _wait_for_a_line(path, proc, limit=60.0):
    """Wait until the results file has at least one whole record, or give up.

    Giving up is not a failure: an empty file is also a valid partial JSONL,
    and asserting on timing here would make this test the flaky one.
    """
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.05)
