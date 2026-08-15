# Architecture

**Audience:** contributors. What the pipeline is, why each part exists, and
which invariant each one is protecting.

Read this before changing anything in `src/moonbuggy/`. Most of the design is
defensive, and the thing being defended against — a confidently wrong status —
is not obvious from the code alone.

## The invariant everything serves

> A wrong status is worse than no status.

A false `SURVIVED` is indistinguishable from a real finding. It costs the user
an investigation that ends nowhere, and it costs every other finding in the
report its credibility. So throughout the codebase, the choice between "fast and
occasionally wrong" and "slower and always right" goes the same way, and the
choice between "guess" and "refuse" goes to refuse.

Most of what looks like paranoia below is one of those two choices.

## The pipeline

```{code-block} text
   discover      which files are source, which directory is the project
      |
   generate      source -> AST -> one Mutant per mutable site
      |
   baseline      run the suite (a) under coverage, (b) once more
      |            -> line -> covering-tests map
      |            -> is it green? is any of it flaky?
      |
   plan          per mutant: cached? suppressed? flaky? or needs running
      |
   execute       fork one child per mutant from a warm host
      |
   report        JSONL first, plaintext derived from it
```

### discover — {mod}`moonbuggy.discover`

Zero configuration, and conservative about it. A `src/` layout with one package
is unambiguous and wins before any heuristic runs. Where the layout cannot be
identified, it raises with the flag to pass rather than picking something
plausible, because silently mutating a project's own test suite or its vendored
dependencies wastes a long run and produces nonsense.

### generate — {mod}`moonbuggy.generate`, {mod}`moonbuggy.operators`

Source is parsed to an AST, walked, and each registered operator is offered
every node. This is AST-based rather than textual for one specific reason:
mutations cannot land inside string literals or comments, and that falls out for
free rather than needing a rule.

Two things here are load-bearing and easy to break:

**Mutant ids must be stable.** The results cache keys on them, so an id that
shifts between runs silently serves the wrong cached status. The id is
`file:line:operator:index`, the walk is depth-first left-to-right, and operators
are sorted by name — all so the same source always produces the same sequence.

**The walk is iterative.** A recursive walk raises `RecursionError` on deeply
nested source at a depth well below what CPython itself parses, and the
traceback names moonbuggy rather than the user's file.

`module_level` is the subtle field. It records whether a line runs at import
time, and selection uses it to widen a mutant's test set to the whole suite —
because a line executing at import time is attributed to no test by a map built
from test-body execution, and "no covering tests" is indistinguishable from
"genuinely uncovered". Getting it wrong in that direction is a false `SURVIVED`,
which is why it is deliberately conservative and why a property test checks it
against CPython's own line tables.

### baseline — {mod}`moonbuggy.baseline`, {mod}`moonbuggy.coverage_pass`

One instrumented run of the suite produces the line → covering-tests map, using
pytest-cov's per-test contexts because they record real pytest node ids, and
node ids are what selection has to hand back to pytest.

Correctness here is asymmetric and every judgement favours the larger set. A map
*missing* a covering test makes moonbuggy run too few tests and report a false
`SURVIVED`. A map with a spurious extra test only costs time.

A second, uninstrumented run of the same suite answers two questions at once:

- a test that fails in **both** runs means the suite is already red, and
  mutation results against a red baseline are not weak but *flattering* — every
  mutant those tests cover is `KILLED` regardless of the mutation. moonbuggy
  refuses and exits 2.
- a test whose outcome **differs** between runs is flaky, and every mutant that
  selects it is settled `SUSPICIOUS` without being run, because neither
  `KILLED` nor `SURVIVED` would be supportable.

### execute — {mod}`moonbuggy.forkserver`, {mod}`moonbuggy.runner`

The expensive part of a mutant run is not the tests. It is importing the test
modules, rewriting their assertions and collecting them — about 90ms per mutant,
against 12ms for `pytest.main()` in a process where that work is already done.

So one forked **host** process runs the suite once (which is also the coverage
pass), and then forks a **grandchild** per mutant. Each grandchild inherits a
process with everything imported, applies its mutation, and runs only its own
tests.

Three rules govern this, and all three have been violated at some point:

**The parent must never import the module under test.** If it did, every child
would inherit an already-imported unmutated module, and mutations would silently
do nothing — a false `SURVIVED` from a third direction.

**A mutation that cannot be applied is an error, never a survivor.** {mod}`moonbuggy.codeswap`
raises `SwapFailed` rather than guessing, and the caller falls back to the
import-hook path. Silence here is the failure mode the whole design exists to
avoid.

**Statuses stream out as each mutant settles.** A run killed mid-flight leaves a
JSONL file that is valid at every instant, rather than nothing.

### mutation application — {mod}`moonbuggy.codeswap`, {mod}`moonbuggy.inmemory`

Two mechanisms, because there are two shapes of mutation.

{mod}`moonbuggy.codeswap` mutates an **already-imported** module in place. A test
module that did `from app.thing import compute` holds a reference to the
function object, so replacing that object's `__code__` changes what the test
calls with no re-import. For a line inside a function it swaps the code object;
for a module-level line it re-executes that statement in the module's namespace.

The subtlety: for a mutation inside a *nested* function it swaps the
**outermost** enclosing function. A closure has no live object to replace —
`outer.inner` is not an attribute of anything, it is a code object rebuilt on
every call — but recompiling the enclosing function produces a code object whose
nested constants are already mutated.

{mod}`moonbuggy.inmemory` is the fallback: a meta-path finder that serves mutated
bytes for exactly one file and is invisible for everything else. It can only
mutate a module that has not been imported yet, which is why the warm path needs
`codeswap`.

The loader deliberately refuses to write bytecode. A `SourceFileLoader` writes
`.pyc` files stamped with the *real* file's mtime, so a mutated `.pyc` looks
valid for unmutated source — and the user's next plain `pytest` runs mutations
they never asked for, with every `.py` byte-identical and nothing pointing at
us. That bug shipped once; `tests/test_cli.py` has the regression test.

### report — {mod}`moonbuggy.report`

JSONL is canonical; the plaintext view is **derived from the JSONL that was just
written**, not from the in-memory results, so the two artifacts cannot drift
apart.

The format assumes a reader that greps rather than one that reads a dashboard:
a fixed leading status keyword, `key=value` tokens, exactly one line per mutant.
The diff is deliberately not inlined — `moonbuggy show <id>` retrieves it —
because a multi-line record breaks `grep`, `awk` and `wc -l` at once.

## Where to be careful

| if you are changing… | the invariant you might break |
|---|---|
| `generate.py` ordering | mutant id stability, and therefore every cache entry |
| `module_level` classification | false `SURVIVED` on import-time lines |
| anything in `forkserver.py` | the parent staying clean of the module under test |
| `codeswap.py` | a mutation that silently does not apply |
| pytest arguments anywhere | node id resolution; see `_base_args` and `tests/test_rootdir.py` |
| the cache key | a stale hit reporting a gap the user already closed |

Each of those has a test that fails loudly. If you are changing one and nothing
goes red, the test is missing rather than the risk being absent.

## Next

[Writing an operator](writing-an-operator.md) is the seam most contributions
land on, and it needs no changes to any of the above.
