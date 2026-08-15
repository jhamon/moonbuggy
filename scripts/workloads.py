"""The three workload shapes M2.1.3 requires, in one place.

The bottleneck demonstrably moves between these. Tuning against a single
generated workload is how a tool ends up optimised for a shape nobody has
(risk 2 in the milestone plan), so the profiler reports all three separately
and the A/B harness refuses to call a win on one of them a win.

    fast tests    many trivial tests. Per-mutant process handling dominates;
                  selection has almost nothing to save. This is the shape the
                  Phase 1 fixture has, and the shape that made moonbuggy look
                  12x slower than mutmut before forking.
    slow tests    fewer tests, each doing real work. Test execution dominates,
                  which is where coverage-guided selection pays and where the
                  G2 verdict is taken.
    many files    lots of small modules with a handful of tests each.
                  Generation, discovery and collection scale with file count
                  rather than with test time.

Shared by `profile_run.py` and `ab_compare.py` so a profile and an A/B result
are always about the same programs. Two harnesses with their own idea of "the
slow workload" would produce numbers nobody could put side by side.
"""

from pathlib import Path

SHAPES = ("fast-tests", "slow-tests", "many-files")


def build(root, shape):
    """Materialise one workload shape under `root`.

    :param root: a directory to create the project in.
    :param shape: one of :data:`SHAPES`.
    :returns: the project root, ready to run moonbuggy in.
    :raises ValueError: for an unknown shape.
    """
    if shape == "fast-tests":
        return _build(root, modules=3, functions=4, tests_per_module=30, iterations=0)
    if shape == "slow-tests":
        return _build(root, modules=3, functions=4, tests_per_module=30, iterations=6000)
    if shape == "many-files":
        return _build(root, modules=40, functions=2, tests_per_module=3, iterations=0)
    raise ValueError(f"unknown workload shape: {shape}")


def _build(root, modules, functions, tests_per_module, iterations):
    project = Path(root)
    (project / "app").mkdir(parents=True, exist_ok=True)
    (project / "tests").mkdir(exist_ok=True)
    (project / "app" / "__init__.py").write_text("")

    for module in range(modules):
        source = []
        for function in range(functions):
            source += [
                f"def compute_{function}(n):",
                "    total = 0",
                "    for i in range(n):",
                f"        if i % {function + 2} == 0:",
                "            total += i",
                "    return total",
                "",
            ]
        (project / "app" / f"mod_{module}.py").write_text("\n".join(source))

        tests = [f"from app.mod_{module} import *", ""]
        for index in range(tests_per_module):
            function = index % functions
            tests += [
                f"def test_{module}_{index}():",
                f"    assert compute_{function}({iterations}) >= 0",
                "",
            ]
        (project / "tests" / f"test_mod_{module}.py").write_text("\n".join(tests))

    (project / "pytest.ini").write_text("[pytest]\ntestpaths = tests\npythonpath = .\n")
    return project


def describe(shape):
    """A one-line description, for report headers."""
    return {
        "fast-tests": "3 modules, 90 trivial tests -- process handling dominates",
        "slow-tests": "3 modules, 90 tests doing real work -- execution dominates",
        "many-files": "40 modules, 120 trivial tests -- file count dominates",
    }[shape]
