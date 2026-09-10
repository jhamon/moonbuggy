"""The survivor export: findings as one frozen, machine-readable file (C3 Phase A).

A run's findings -- the SURVIVED and NO_COVERAGE records that say something
about the tests rather than about the run -- are the tool's product. Until now
they lived only inside ``results.jsonl``, mixed with every other verdict, and
the contract for handing them to an agent was "grep the plaintext, or filter
the JSONL yourself". This module is the missing surface: one command
(``moonbuggy export``), one file, one record per finding, each record shaped by
the frozen schema at ``schemas/survivor-export.v1.schema.json``.

The record is the :func:`moonbuggy.report.record_for` envelope **verbatim** --
every key a schema-4 record carries, including ``original``/``mutated``/``diff``
(the plaintext line deliberately withholds those, but a machine consumer
re-injecting the mutant needs them) -- plus the export's own fields: the
version pin, the export provenance, and ``survival_reason``. Reusing the
envelope rather than projecting a narrower row is the point: a consumer that
can parse ``results.jsonl`` can parse an export with the same code, and
``moonbuggy run <id>`` round-trips straight off the ``id`` the line carries.

``survival_reason`` is the one field the envelope does not carry, and in v1 it
is **always null**: the survival-reason vocabulary ("why did this survive?")
is C3 Phase B and does not exist yet. The field is reserved and required --
a Phase B bump widens the type, and every v1 line already carries the slot --
but no v1 producer may invent a token, and no v1 consumer may read null as one.

Like the harness-output contract, this is a versioned contract: the shape
changes only by a version bump on the schema file plus a reviewed diff, never
an in-place edit. The tests freeze the required field set, the ``const`` pins,
and a golden record, so drift fails CI rather than reaching a consumer.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .report import FINDING_STATUSES, RECORD_SCHEMA, Record

# The frozen contract, shipped beside the code that writes it -- unlike the
# D2 numbers-pipe schema, which lives under scripts/ because only the bench
# harness reads it, this schema travels with the CLI: an agent consuming an
# export on another machine has the package, not this repository.
SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "survivor-export.v1.schema.json"
)

# The version this module writes into every record's `schema` field, in
# lockstep with the schema's `schema` const -- a mismatch is a bug here, and
# the contract test catches it.
SCHEMA_VERSION = 1

# The envelope version the embedded finding fields carry. The export reads
# the pin off each record rather than restating a number, so a record written
# by a different RECORD_SCHEMA version exports under the pin it actually
# carries and validation -- not memory -- catches a mismatch.
EMBEDDED_RECORD_SCHEMA = RECORD_SCHEMA

# The one field the export adds to the record envelope, and the only place
# C3 Phase B will land: the stable survival-reason token, not yet in
# vocabulary. Always null in v1 -- see the module docstring.
SURVIVAL_REASON = "survival_reason"


def load_schema() -> dict[str, Any]:
    """The frozen contract, as a parsed dict.

    Returns:
        The ``survivor-export.v1`` JSON Schema.
    """
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def record_for_finding(record: Record) -> dict[str, Any]:
    """One exported record for one finding record.

    Args:
        record: a schema-4 record from ``results.jsonl`` whose status is a
            finding (SURVIVED or NO_COVERAGE). The caller filters; this
            function builds, and asserts the precondition rather than
            silently exporting a verdict the contract does not cover.

    Returns:
        The export record: the envelope verbatim, plus `schema`, `exported`,
        `moonbuggy`, `record_schema` and `survival_reason`. The envelope's own
        `schema` pin is not spread through -- the export pin owns that key, and
        the envelope's version is carried explicitly in `record_schema`.
    """
    assert record["status"] in FINDING_STATUSES, (
        f"export covers findings only, got {record['status']}"
    )
    from . import __version__

    envelope = {key: value for key, value in record.items() if key != "schema"}
    return {
        "schema": SCHEMA_VERSION,
        "exported": datetime.now(UTC).isoformat(timespec="seconds"),
        "moonbuggy": __version__,
        "record_schema": record["schema"],
        "survival_reason": None,
        **envelope,
    }


def export_findings(records: list[Record]) -> list[dict[str, Any]]:
    """The export document for a run: one record per finding, in record order.

    Args:
        records: every record from the run, as :func:`moonbuggy.report.read_jsonl`
            returns them. Anything that is not a finding is skipped, so the
            caller hands over the whole run and the findings-only rule lives
            in exactly one place.

    Returns:
        One export record per finding, in the order the run reported them.
    """
    return [
        record_for_finding(record)
        for record in records
        if record["status"] in FINDING_STATUSES
    ]


def validate(record: dict[str, Any]) -> list[str]:
    """Check one export record against the frozen schema.

    Validates the constraints the v1 contract actually exercises -- the
    required set, ``enum``/``const``/``minLength``/``minimum``/type rules,
    and ``additionalProperties: false`` -- using the same hand-rolled
    validator approach as the harness-output contract: the schema file is the
    single source of truth and this function reads its rules rather than
    restating them.

    Args:
        record: the export record to check.

    Returns:
        A list of human-readable violations; empty when the record conforms.
    """
    schema = load_schema()
    if not isinstance(record, dict):
        return ["record is not a JSON object"]

    errors: list[str] = []
    for key in schema["required"]:
        if key not in record:
            errors.append(f"missing required field: {key}")

    extra = set(record) - set(schema["properties"])
    if extra:
        errors.append(f"unknown fields: {sorted(extra)}")

    for key, rule in schema["properties"].items():
        if key not in record:
            continue
        errors.extend(_check_rule(key, record[key], rule))
    return errors


def _check_rule(key: str, value: Any, rule: dict[str, Any]) -> list[str]:
    """One field against one schema rule.

    Args:
        key: the field name, for the message.
        value: the field's value.
        rule: the schema's rule object for the field.

    Returns:
        The violations for this field; usually empty.
    """
    errors: list[str] = []
    types = rule.get("type")
    if types is not None and not _type_ok(value, types):
        errors.append(f"{key}: expected type {types}, got {type(value).__name__}")
        return errors
    if "const" in rule and value != rule["const"]:
        errors.append(f"{key}: must be {rule['const']!r}, got {value!r}")
    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{key}: must be one of {rule['enum']}, got {value!r}")
    if isinstance(value, str):
        if "minLength" in rule and len(value) < rule["minLength"]:
            errors.append(f"{key}: shorter than minLength {rule['minLength']}")
        if rule.get("format") == "date-time" and not _is_datetime(value):
            errors.append(f"{key}: not an ISO-8601 timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in rule and value < rule["minimum"]:
            errors.append(f"{key}: below minimum {rule['minimum']}")
    return errors


def _type_ok(value: Any, types: str | list[str]) -> bool:
    """Whether a value matches a JSON-Schema ``type`` (or list of them).

    Args:
        value: the value to check.
        types: the schema type name or names.

    Returns:
        True when the value is of one of the named types.
    """
    if isinstance(types, str):
        types = [types]
    checks: dict[str, Any] = {
        "object": lambda v: isinstance(v, dict),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    return any(checks[t](value) for t in types)


def _is_datetime(value: str) -> bool:
    """Whether a string parses as the ISO-8601 timestamps the export writes.

    Args:
        value: the string to check.

    Returns:
        True when :meth:`datetime.fromisoformat` accepts it.
    """
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def write_export(
    records: list[Record], path: str | Path
) -> int:
    """Write the run's findings to ``path`` as JSONL, and return the count.

    Args:
        records: every record from the run, as
            :func:`moonbuggy.report.read_jsonl` returns them.
        path: where to write the export.

    Returns:
        How many records were written -- the finding count, which the CLI
        reports so a caller can distinguish "no survivors" from "empty file
        by accident" without re-reading the file.
    """
    exports = export_findings(records)
    with open(path, "w", encoding="utf-8") as handle:
        for export in exports:
            handle.write(json.dumps(export, sort_keys=True) + "\n")
    return len(exports)
