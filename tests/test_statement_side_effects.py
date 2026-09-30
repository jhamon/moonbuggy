"""Regression: codeswap must refuse to re-execute a statement that binds nothing.

Finding 5 (M1.1 spike, docs/development/spike-selfmutation-findings.md): the
boundary-fixed fork-path rig died "silently" at the end of every full run --
`results.jsonl` left with a ~400KB NUL prefix, no traceback, five-for-five.

Root cause, proven from the audit-hook log of a reproducer run: the mutant on
`cli/__init__.py`'s `if __name__ == "__main__": run()` is a *module-level*
statement, so the warm-path grandchild applies it with
`codeswap._exec_module_level`, which re-executes the whole statement in the
module's namespace. In a forked child `__name__` is `"moonbuggy.cli"`, so the
*inverted* guard `if not __name__ == '__main__':` is TRUE and `run()` executes
-- a full recursive moonbuggy campaign on the inherited argv, in a process
that inherited the parent's open `results.jsonl` file descriptor. The campaign
truncates that file (`StreamingJSONL` opens it `mode='w'`), the parent's
writes at the inherited offset then re-create the data as a NUL hole, and the
nested `run()` ends in `os._exit`, killing the grandchild before it can report
a verdict -- hence SUSPICIOUS and the "silent" death.

Re-execution exists to rebind names; a statement that binds nothing
(`__all__ +=`, `del`-only, or a bare `if`/`try` with no `def`/assign/for
targets) has no names to rebind, so re-running it has one effect and one
effect only: a second, live execution of its side effects in a process that
cannot afford them. `apply_in_place` now refuses those statements with
`SwapFailed`, and the existing UNAPPLIED machinery re-runs the mutant coldly,
where the import hook handles it correctly (the module is re-imported from
mutated source, so the guard is evaluated once, at import, against a fresh
`__name__`).

The tests below compile-and-exec the mutated statement in an engine-shaped
namespace to pin the failure mode itself, plus the refusal contract on
`apply_in_place`.
"""

import sys

import pytest

from moonbuggy.codeswap import SwapFailed, _statement_at, apply_in_place

pytestmark = pytest.mark.slow


def _entry_guard_statements() -> tuple[str, str]:
    """The real mutant shape: (original statement, mutated statement).

    Mirror of `src/moonbuggy/cli/__init__.py`'s entry guard and the mutation
    `condition_negation` generates for it, kept as literals so the test cannot
    drift with the engine's own source.
    """
    return (
        'if __name__ == "__main__":\n    run()',
        "if not __name__ == '__main__':\n    run()",
    )


def test_the_entry_guard_statement_is_found_whole():
    """`_statement_at` must return the guard AND its body as one statement.

    The whole hazard lives in the body travelling with the condition when the
    statement is re-executed. If this ever starts returning only the `if`
    line, the reproduction below changes meaning -- update it, do not trust it.
    """
    source, _ = _entry_guard_statements()
    module_source = f"import runlib\n\nrunlib.setup()\n\n\n{source}\n"
    found = _statement_at(module_source, 6)
    assert found is not None
    assert "run()" in found
    assert "__main__" in found


def test_inverted_guard_fires_outside_main_in_a_child():
    """The failing condition itself: the mutated guard is TRUE in a child.

    In a forked grandchild `__name__` is the module's import name, so the
    negated `__main__` comparison is true and the body runs. This is why
    compiling the statement correctly cannot defuse it -- the guard is doing
    exactly what the mutation asked, from the child's point of view. The fix
    has to be at the re-execution decision, not in the compilation.
    """
    _, mutated = _entry_guard_statements()
    executed: list[str] = []
    namespace: dict[str, object] = {
        "__name__": "moonbuggy.cli",
        "run": lambda: executed.append("run fired"),
    }
    exec(compile(mutated, "<moonbuggy>", "exec"), namespace)  # noqa: S102
    assert executed == ["run fired"], (
        "precondition changed: the inverted guard no longer fires outside "
        "__main__, so the Finding 5 mechanism needs re-deriving"
    )


def test_statement_binding_nothing_is_refused(tmp_path, monkeypatch):
    """`apply_in_place` must refuse a module-level statement that binds nothing.

    Re-execution rebinds names; with no names bound, running the statement
    again is a pure side-effect re-trigger -- the exact recursion that killed
    Finding 5's runs. Refusal sends the mutant to the cold path, where the
    import hook applies it at import time and nothing executes twice.
    """
    module = tmp_path / "entry.py"
    source, mutated = _entry_guard_statements()
    module.write_text(source + "\n")

    namespace: dict[str, object] = {
        "__name__": "moonbuggy.cli",
        "run": lambda: None,  # would start the campaign if the body ran
    }
    # importlib-style module the way codeswap expects to find one.
    import types

    fake = types.ModuleType("entry")
    fake.__dict__.update(namespace)
    fake.__file__ = str(module)
    monkeypatch.setitem(sys.modules, "entry", fake)

    with pytest.raises(SwapFailed):
        apply_in_place(fake, module, 1, mutated.splitlines()[0])


def test_statement_binding_names_still_applies(tmp_path, monkeypatch):
    """Control: a name-binding module-level statement is still mutated.

    The refusal must be narrow. A plain assignment (the common case) keeps
    today's behaviour -- exec, then rebind, then return normally with no
    exception.
    """
    module = tmp_path / "consts.py"
    module.write_text("LIMIT = 3 + 4\n")

    import types

    fake = types.ModuleType("consts")
    fake.__file__ = str(module)
    monkeypatch.setitem(sys.modules, "consts", fake)

    apply_in_place(fake, module, 1, "LIMIT = 3 - 4")
    assert fake.LIMIT == -1  # type: ignore[attr-defined]
