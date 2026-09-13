"""The `moonbuggy export` subcommand: the last run's findings as one frozen file."""

import argparse
import sys
from pathlib import Path

from ..export import write_export
from ..report import read_jsonl
from .common import _display_path


def _export(args: argparse.Namespace) -> int:
    """Write the last run's findings to a frozen JSONL export file.

    Args:
        args: the parsed `moonbuggy export` command line.

    Returns:
        0 when the export was written (even with zero findings -- an empty
        export is a fact about the run, not an error), 2 when there are no
        run artifacts to export from.
    """
    results_path = (
        Path(args.project if hasattr(args, "project") else ".")
        / args.output_dir
        / "results.jsonl"
    )
    if not results_path.exists():
        results_path = Path(args.output_dir) / "results.jsonl"
    if not results_path.exists():
        print(
            f"moonbuggy: no results at {results_path}. Run moonbuggy first.",
            file=sys.stderr,
        )
        return 2

    records = read_jsonl(results_path)
    export_path = Path(args.path)
    findings = write_export(records, export_path)
    print(
        f"moonbuggy: exported {findings} "
        f"{'finding' if findings == 1 else 'findings'} "
        f"from {len(records)} records to {_display_path(export_path, Path('.'))}"
    )
    return 0
