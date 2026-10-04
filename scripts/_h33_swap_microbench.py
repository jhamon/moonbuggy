"""h33 microbench: cost split of apply_in_place's stages on a generated module.

Usage: .venv/bin/python scripts/_h33_swap_microbench.py
Builds one fast-tests-like module, then times read_source/replace_line,
strip_coding_cookie, ast.parse, _enclosing_function_path, compile and the
__code__ swap separately, plus the whole apply_in_place for reference.
"""

import ast
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
import workloads  # noqa: E402

from moonbuggy.codeswap import (  # noqa: E402
    _enclosing_function_path,
    apply_in_place,
)
from moonbuggy.srcio import read_source, replace_line, strip_coding_cookie  # noqa: E402


def bench(fn, repeats=200):
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1e6)
    return statistics.median(times)


with tempfile.TemporaryDirectory() as root:
    project = workloads.build(root, "fast-tests")
    target = str(Path(project) / "app" / "mod_0.py")
    source = read_source(target)
    mutated = replace_line(source, 9, "total = 1")  # a body line
    stripped = strip_coding_cookie(mutated)
    tree = ast.parse(stripped)

    import importlib.util

    spec = importlib.util.spec_from_file_location("mod0bench", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    print(f"module: {len(source)} chars, {source.count(chr(10))} lines")
    print(
        "read+replace:      "
        f"{bench(lambda: replace_line(read_source(target), 9, 'total = 1')):7.1f} us"
    )
    print(f"strip cookie:      {bench(lambda: strip_coding_cookie(mutated)):7.1f} us")
    print(f"ast.parse:         {bench(lambda: ast.parse(stripped)):7.1f} us")
    print(
        f"enclosing path:    {bench(lambda: _enclosing_function_path(tree, 9)):7.1f} us"
    )
    print(
        f"compile:           {bench(lambda: compile(stripped, target, 'exec')):7.1f} us"
    )
    print(
        "apply_in_place:    "
        f"{bench(lambda: apply_in_place(module, target, 9, 'total = 1')):7.1f} us"
    )
