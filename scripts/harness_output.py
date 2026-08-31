"""The D2 numbers pipe: read, build, and validate perf-harness output rows.

Every performance number moonbuggy produces -- wall-clock, mutants/sec,
memory delta, benchmarked on a named suite under a hypothesis tag -- travels as
one of these documents. The shape is frozen in
``scripts/schemas/harness-output.v1.schema.json`` (this repo's single source of
truth for perf numbers), and this module is the code that lives on both ends of
the pipe: harnesses build conforming rows with :func:`build`, and anything that
consumes or archives a row checks it with :func:`validate` first.

The contract is versioned by the ``schema`` field every row carries. A change
that adds, renames, or re-types a key raises the schema version rather than
shifting the shape out from under a reader -- the same rule the JSONL verdict
records follow, applied to the numbers that measure performance work.
"""

import json
import platform
import subprocess
from datetime import UTC
from pathlib import Path
from typing import Any

# The schema is the source of truth; its version field is what consumers key
# off. Keep the integer here in lockstep with the schema's "schema" const -- a
# mismatch is a bug in this module, and the validation test catches it.
SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "harness-output.v1.schema.json"
)


def load_schema() -> dict[str, Any]:
    """The frozen contract, as a parsed dict.

    Returns:
        The ``harness-output.v1`` JSON Schema.
    """
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def git_commit() -> str | None:
    """The tree's commit, or None when it cannot be determined.

    A perf number is a fact about a snapshot of code; the snapshot belongs in
    the row. Falling back to None (rather than a placeholder) keeps the row
    honest when we are not in a git tree.

    Returns:
        The short HEAD commit hash, or None.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip() or None


def host_os() -> str:
    """A short OS label for the row, e.g. ``Darwin 24.1.0``.

    Returns:
        ``platform.system()`` and ``platform.release()`` joined.
    """
    return f"{platform.system()} {platform.release()}"


def python_version() -> str:
    """The interpreter's ``major.minor.patch``.

    Returns:
        The implementation version, e.g. ``3.12.13``.
    """
    return platform.python_version()


def build(
    *,
    suite: str,
    purpose: str,
    harness: str,
    wall_clock: float,
    mutants: int,
    mutants_per_sec: float | None = None,
    memory_delta: float = 0.0,
    hypothesis: str,
    runs: int = 1,
    moonbuggy: str,
    commit: str | None = None,
    memory_baseline_bytes: float | None = None,
    median: float | None = None,
    min_: float | None = None,
    interval: list[float] | None = None,
) -> dict[str, Any]:
    """Build a conforming harness-output document.

    Args:
        suite: the workload name (fast-tests, slow-tests, many-files, or a fixture).
        purpose: which harness produced the row: bench, ab, or profile.
        harness: the emitting script's name, e.g. bench_mutation.py.
        wall_clock: reported run time in seconds (median where runs > 1).
        mutants: how many mutants the run measured.
        mutants_per_sec: mutants / wall_clock, or the caller's own throughput
            figure. Defaults to mutants / wall_clock.
        memory_delta: signed peak-RSS difference in bytes vs the comparator.
        hypothesis: the perf-hypotheses tag, or the literal baseline.
        runs: how many measurements the median covers (default 1).
        moonbuggy: __version__ of the measured tree.
        commit: git commit, resolved here when omitted.
        memory_baseline_bytes: the RSS value memory_delta is differenced against.
        median: pass-through per-run median when it differs from wall_clock.
        min_: fastest single run, seconds.
        interval: [low, high] 95% confidence interval on the median.

    Returns:
        A document conforming to :data:`load_schema`.
    """
    return {
        "schema": 1,
        "suite": suite,
        "hypothesis": hypothesis,
        "purpose": purpose,
        "harness": harness,
        "moonbuggy": moonbuggy,
        "commit": commit if commit is not None else git_commit(),
        "python": python_version(),
        "host": host_os(),
        "timestamp": _utc_now(),
        "wall_clock": round(wall_clock, 4),
        "runs": runs,
        "mutants": mutants,
        "mutants_per_sec": round(
            mutants_per_sec if mutants_per_sec is not None else (mutants / wall_clock),
            4,
        ),
        "memory_delta": memory_delta,
        **(
            {"memory_baseline_bytes": memory_baseline_bytes}
            if memory_baseline_bytes is not None
            else {}
        ),
        **({"median": round(median, 4)} if median is not None else {}),
        **({"min": round(min_, 4)} if min_ is not None else {}),
        **(
            {"interval": [round(i, 4) for i in interval]}
            if interval is not None
            else {}
        ),
    }


def _utc_now() -> str:
    """An ISO-8601 UTC timestamp for the row's ``timestamp`` field.

    Returns:
        The current time in UTC, ISO format.
    """
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def validate(document: dict[str, Any]) -> list[str]:
    """Check a document against the frozen schema.

    Uses the same schema a full JSON-Schema engine would, validating the
    properties the ``harness-output.v1`` contract actually exercises: the
    required set, ``minLength``/``enum``/``const``/``format`` constraints, and
    ``additionalProperties: false``. A conforming document yields ``[]``.

    Args:
        document: the row to check.

    Returns:
        A list of human-readable violations; empty when the document conforms.
    """
    schema = load_schema()
    errors: list[str] = []

    if not isinstance(document, dict):
        return ["document is not a JSON object"]

    for key in schema["required"]:
        if key not in document:
            errors.append(f"missing required field: {key}")

    if "additionalProperties" in schema:
        extra = set(document) - set(schema["properties"])
        if extra:
            errors.append(f"unknown fields: {sorted(extra)}")

    for key, rule in schema["properties"].items():
        if key not in document:
            continue
        errors.extend(_check_rule(key, document[key], rule))

    return errors


def _check_rule(key: str, value: Any, rule: dict[str, Any]) -> list[str]:
    """Validate one field against one schema property rule.

    Args:
        key: the field name, for error messages.
        value: the field's value.
        rule: the JSON-Schema property object for this field.

    Returns:
        A list of violations for this field; empty when it conforms.
    """
    errors: list[str] = []

    expected = rule.get("type")
    if expected:
        ok = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }.get(expected, True)
        if not ok:
            errors.append(f"{key}: expected {expected}, got {type(value).__name__}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "exclusiveMinimum" in rule and value <= rule["exclusiveMinimum"]:
            errors.append(f"{key}: must be > {rule['exclusiveMinimum']}")
        if "minimum" in rule and value < rule["minimum"]:
            errors.append(f"{key}: must be >= {rule['minimum']}")

    if isinstance(value, str):
        if "minLength" in rule and len(value) < rule["minLength"]:
            errors.append(f"{key}: shorter than minLength {rule['minLength']}")
        if "format" in rule and rule["format"] == "date-time":
            from datetime import datetime

            try:
                datetime.fromisoformat(value)
            except ValueError:
                errors.append(f"{key}: not an ISO-8601 date-time")

    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{key}: not one of {rule['enum']}")

    if "const" in rule and value != rule["const"]:
        errors.append(f"{key}: expected constant {rule['const']}")

    if isinstance(value, list):
        if "minItems" in rule and len(value) < rule["minItems"]:
            errors.append(f"{key}: fewer than {rule['minItems']} items")
        if "maxItems" in rule and len(value) > rule["maxItems"]:
            errors.append(f"{key}: more than {rule['maxItems']} items")
        if "items" in rule:
            for i, item in enumerate(value):
                errors.extend(_check_rule(f"{key}[{i}]", item, rule["items"]))

    return errors


def write_jsonl(document: dict[str, Any], path: str | Path) -> None:
    """Append one conforming row to a JSONL stream.

    One complete object per line, so a reader (or a dashboard) consumes rows it
    already has even if a later run is interrupted. Flushed per row for that
    same reason.

    Args:
        document: a conforming row, e.g. from :func:`build`.
        path: the output file; created and appended to.
    """
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(document, sort_keys=True) + "\n")
        handle.flush()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read back every row a JSONL stream holds.

    Args:
        path: the file written by :func:`write_jsonl`.

    Returns:
        The rows, in order.

    Raises:
        ValueError: if any line is not a JSON object.
    """
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError(f"line {line_number} is not an object: {line[:80]!r}")
        rows.append(parsed)
    return rows


def main() -> None:
    """Sanity-print one conforming row, for a human eyeballing the pipe."""
    row = build(
        suite="slow-tests",
        purpose="bench",
        harness="harness_output.demo",
        wall_clock=0.940,
        mutants=84,
        hypothesis="H21",
        moonbuggy="0.2.0",
    )
    print(json.dumps(row, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
