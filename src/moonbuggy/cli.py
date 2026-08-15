"""Command line interface.

Low floor, high ceiling (6.2): bare `moonbuggy` in a pytest project runs
end to end with no flags and no config file. Everything else is available and
nothing else is required.

Two artifacts are written per run, per 5.2: results.jsonl is canonical, and
results.txt is derived from it rather than authored alongside it, so they cannot
drift apart.
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .cache import ResultCache
from .coverage_pass import CoveragePassError, run_coverage_pass
from .discover import LayoutError, find_source_dir, find_source_files, looks_like_pytest_project
from .generate import generate_mutants
from .report import (
    find_record,
    plaintext_from_records,
    read_jsonl,
    record_for,
    render_line,
    summarise,
    write_jsonl,
)
from .runner import run_mutants, run_session

DEFAULT_OUTPUT_DIR = ".moonbuggy"


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            return _show(args)
        return _run(args)
    except (LayoutError, CoveragePassError) as error:
        # Criterion H5: an actionable message, not a traceback.
        print(f"moonbuggy: {error}", file=sys.stderr)
        return 2


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="moonbuggy",
        description="Fast, agent-first mutation testing for Python.",
    )
    parser.add_argument("--version", action="version", version=f"moonbuggy {__version__}")
    parser.set_defaults(command="run")

    _add_run_arguments(parser)

    sub = parser.add_subparsers(dest="command")
    show = sub.add_parser("show", help="print the full record for one mutant id")
    show.add_argument("mutant_id")
    show.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    show.set_defaults(command="show")
    return parser


def _add_run_arguments(parser):
    parser.add_argument("--project", default=".", help="project root (default: cwd)")
    parser.add_argument("--source", default=None, help="directory to mutate (default: discovered)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="seconds before a mutant is called TIMEOUT (default: 30)")
    parser.add_argument("--operators", default=None,
                        help="comma-separated operator names to use (default: all)")
    parser.add_argument("--include", action="append", default=[],
                        help="only mutate paths containing this fragment (repeatable)")
    parser.add_argument("--exclude", action="append", default=[],
                        help="skip paths containing this fragment (repeatable)")
    parser.add_argument("--jobs", type=int, default=0,
                        help="mutants to run concurrently (default: CPU count - 1)")
    parser.add_argument("-n", "--workers", type=int, default=0,
                        help="pytest-xdist workers per mutant run (default: 0, serial)")
    parser.add_argument("--no-cache", action="store_true", help="ignore and do not update the cache")
    parser.add_argument("--clear-cache", action="store_true", help="delete the cache, then run")
    parser.add_argument("--quiet", action="store_true", help="only print the summary line")


def _run(args):
    project_dir = Path(args.project).resolve()

    if not looks_like_pytest_project(project_dir):
        print(
            f"moonbuggy: {project_dir} does not look like a pytest project "
            "(no pytest.ini, pyproject.toml, conftest.py or test_*.py found). "
            "Run moonbuggy from your project root, or pass --project.",
            file=sys.stderr,
        )
        return 2

    source_dir = Path(args.source).resolve() if args.source else find_source_dir(project_dir)
    source_files = find_source_files(source_dir, project_dir, args.include, args.exclude)
    if not source_files:
        print("moonbuggy: no source files to mutate after filtering.", file=sys.stderr)
        return 2

    output_dir = project_dir / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = _prepare_cache(args, output_dir)

    wanted = set(args.operators.split(",")) if args.operators else None
    mutants = []
    for relative in source_files:
        found = generate_mutants((project_dir / relative).read_text(), module=relative)
        mutants.extend(m for m in found if wanted is None or m.operator in wanted)

    if not mutants:
        print("moonbuggy: no mutants generated.", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"moonbuggy: {len(mutants)} mutants across {len(source_files)} files", file=sys.stderr)
        print("moonbuggy: running coverage pass...", file=sys.stderr)

    if args.workers:
        # xdist needs real subprocesses, so the warm single-pass session does
        # not apply; fall back to the separate coverage pass and cold forks.
        linemap = run_coverage_pass(project_dir, source_dir)
        results = run_mutants(
            project_dir, mutants, linemap,
            timeout=args.timeout, xdist_workers=args.workers, cache=cache,
            jobs=args.jobs or None,
        )
    else:
        _, results = run_session(
            project_dir, mutants, source_dir,
            timeout=args.timeout, cache=cache, jobs=args.jobs or None,
        )

    jsonl_path = output_dir / "results.jsonl"
    write_jsonl(results, jsonl_path)

    # Derived from the JSONL that was just written, not from the in-memory
    # results, so the two artifacts cannot disagree (criterion E3).
    records = read_jsonl(jsonl_path)
    text_path = output_dir / "results.txt"
    text_path.write_text(plaintext_from_records(records) + "\n")

    if not args.quiet:
        for record in records:
            print(render_line(record))

    if cache is not None:
        cache.save()

    counts = summarise(records)
    print(
        "moonbuggy: "
        + "  ".join(f"{status}={count}" for status, count in sorted(counts.items()))
        + f"  cached={sum(1 for r in results if r.from_cache)}"
        + f"  -> {jsonl_path.relative_to(project_dir)}",
        file=sys.stderr,
    )
    return 1 if counts["SURVIVED"] else 0


def _prepare_cache(args, output_dir):
    cache = ResultCache(output_dir / "cache.json")
    if args.clear_cache:
        cache.clear()
    return None if args.no_cache else cache


def _show(args):
    path = Path(args.output_dir) / "results.jsonl"
    if not path.exists():
        path = Path(".") / args.output_dir / "results.jsonl"
    if not path.exists():
        print(f"moonbuggy: no results at {path}. Run moonbuggy first.", file=sys.stderr)
        return 2

    record = find_record(read_jsonl(path), args.mutant_id)
    if record is None:
        print(f"moonbuggy: no mutant with id {args.mutant_id}", file=sys.stderr)
        return 2

    # The diff lives here rather than in the plaintext view, which stays one
    # line per mutant (criterion E5/E7).
    print(f"id           {record['id']}")
    print(f"status       {record['status']}")
    print(f"location     {record['file']}:{record['line']}")
    print(f"operator     {record['operator']}")
    print(f"nearest_test {record['nearest_test'] or '-'}")
    print(f"tests_run    {record['tests_run']}")
    print("diff")
    for line in record["diff"].splitlines():
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
