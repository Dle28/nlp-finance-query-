#!/usr/bin/env python3
"""Run a read-only completion gate for a ViFinQA submission artifact.

The gate checks the producer contract around an already-materialized
``--output-dir``.  It does not execute the pipeline, replay answers, inspect
gold labels, inspect hidden files, alter any artifact, or promote a candidate
to ``VERIFIED``.  A completed best-effort run may therefore pass this
packaging/completeness gate while still containing ``PARTIAL`` or
``UNRESOLVED`` prediction records; those statuses are intentionally not
rewritten or treated as strict answer verification.

The expected layout is the layout written by
``scripts/e2e/build_competition_submission_v1.py``::

    <output-dir>/build_report.json
    <output-dir>/submission.json
    <output-dir>/diagnostics.jsonl
    <output-dir>/best_surviving_candidates_v1.jsonl
    <output-dir>/prediction_audit_ledger_v1.jsonl
    <output-dir>.zip

The sibling ZIP is checked without extracting it.  The checker reads only
the five named artifacts and the ZIP payload required by this contract.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


DEFAULT_EXPECTED_COUNT = 1012

JSONL_ID_FIELDS = {
    "diagnostics.jsonl": "id",
    "best_surviving_candidates_v1.jsonl": "question_id",
    "prediction_audit_ledger_v1.jsonl": "question_id",
}

# These are lifecycle fields only.  Do not inspect ``verification_class`` or
# nested prediction classes: a completed best-effort artifact is allowed to
# contain PARTIAL/UNRESOLVED predictions, but a RUNNING/PARTIAL build is not a
# completed package.
LIFECYCLE_FIELDS = (
    "status",
    "state",
    "run_status",
    "run_state",
    "build_status",
    "completion_status",
)
INCOMPLETE_LIFECYCLE_VALUES = {
    "RUNNING",
    "PARTIAL",
    "IN_PROGRESS",
    "INCOMPLETE",
}


def _canonical_id(value: Any) -> str | None:
    """Return a stable comparison key for a public question identifier."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float)):
        # JSON question IDs are normally strings or integers.  Avoid turning
        # a finite integral float into ``1.0`` when comparing copied artifacts.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value).strip() or None


def _empty_record_check(path: Path, id_field: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "id_field": id_field,
        "exists": path.is_file(),
        "records": 0,
        "unique_ids": 0,
        "duplicate_ids": [],
        "missing_id_records": 0,
        "invalid_json_lines": 0,
        "blank_lines": 0,
        "ids": set(),
        "fallback_ids": set(),
    }


def _scan_jsonl(path: Path, id_field: str, errors: list[str]) -> dict[str, Any]:
    """Parse one required JSONL artifact and collect IDs without mutating it."""

    result = _empty_record_check(path, id_field)
    if not path.is_file():
        errors.append(f"missing required artifact: {path}")
        return result

    ids: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    result["blank_lines"] += 1
                    errors.append(f"{path}: blank JSONL line {line_number}")
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    result["invalid_json_lines"] += 1
                    errors.append(f"{path}: invalid JSON at line {line_number}: {exc}")
                    continue
                if not isinstance(row, dict):
                    result["invalid_json_lines"] += 1
                    errors.append(f"{path}: line {line_number} is not a JSON object")
                    continue
                result["records"] += 1
                identifier = _canonical_id(row.get(id_field))
                if identifier is None:
                    result["missing_id_records"] += 1
                    errors.append(
                        f"{path}: line {line_number} has no usable {id_field}"
                    )
                else:
                    ids.append(identifier)
                    if (
                        path.name == "diagnostics.jsonl"
                        and (
                            str(row.get("tier") or "").strip().lower() == "fallback_zero"
                            or str(row.get("answer_status") or "").strip().upper() == "FALLBACK"
                        )
                    ):
                        result["fallback_ids"].add(identifier)
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot read {path}: {exc}")
        return result

    result["ids"] = set(ids)
    result["unique_ids"] = len(result["ids"])
    result["duplicate_ids"] = sorted(
        identifier for identifier in set(ids) if ids.count(identifier) > 1
    )
    if result["duplicate_ids"]:
        errors.append(f"{path}: duplicate IDs {result['duplicate_ids'][:10]}")
    return result


def _scan_submission_json(
    path: Path, errors: list[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read the local submission list and collect its public question IDs."""

    result: dict[str, Any] = {
        "path": str(path),
        "id_field": "id",
        "exists": path.is_file(),
        "json_type": None,
        "records": 0,
        "unique_ids": 0,
        "duplicate_ids": [],
        "missing_id_records": 0,
        "ids": set(),
    }
    if not path.is_file():
        errors.append(f"missing required artifact: {path}")
        return result, []

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot parse {path}: {exc}")
        return result, []

    result["json_type"] = type(payload).__name__
    if not isinstance(payload, list):
        errors.append(f"{path}: expected a JSON list, got {type(payload).__name__}")
        return result, []

    rows = [row for row in payload if isinstance(row, dict)]
    result["records"] = len(payload)
    if len(rows) != len(payload):
        errors.append(f"{path}: one or more records are not JSON objects")

    identifiers: list[str] = []
    for index, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            result["missing_id_records"] += 1
            continue
        identifier = _canonical_id(row.get("id"))
        if identifier is None:
            result["missing_id_records"] += 1
            errors.append(f"{path}: record {index} has no usable id")
        else:
            identifiers.append(identifier)

    result["ids"] = set(identifiers)
    result["unique_ids"] = len(result["ids"])
    result["duplicate_ids"] = sorted(
        identifier
        for identifier in set(identifiers)
        if identifiers.count(identifier) > 1
    )
    if result["duplicate_ids"]:
        errors.append(f"{path}: duplicate IDs {result['duplicate_ids'][:10]}")
    return result, rows


def _public_check(result: dict[str, Any]) -> dict[str, Any]:
    """Remove internal sets before a check is serialized as JSON."""

    return {
        key: value
        for key, value in result.items()
        if key not in {"ids", "fallback_ids"}
    }


def _require_count(
    label: str,
    result: dict[str, Any],
    expected_count: int,
    errors: list[str],
) -> None:
    if result.get("records") != expected_count:
        errors.append(
            f"{label}: record_count={result.get('records')} "
            f"expected={expected_count}"
        )
    if result.get("unique_ids") != expected_count:
        errors.append(
            f"{label}: unique_id_count={result.get('unique_ids')} "
            f"expected={expected_count}"
        )
    if result.get("missing_id_records"):
        errors.append(
            f"{label}: missing_id_records={result['missing_id_records']}"
        )


def _check_build_report(
    path: Path,
    expected_count: int,
    expected_zip_name: str,
    errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate the completion fields produced by the builder."""

    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "json_type": None,
        "validation": None,
        "lifecycle_fields": {},
    }
    if not path.is_file():
        errors.append(f"missing required artifact: {path}")
        return result, None

    try:
        with path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot parse {path}: {exc}")
        return result, None

    result["json_type"] = type(report).__name__
    if not isinstance(report, dict):
        errors.append(f"{path}: expected a JSON object")
        return result, None

    lifecycle_fields = {
        key: report[key] for key in LIFECYCLE_FIELDS if key in report
    }
    result["lifecycle_fields"] = lifecycle_fields
    for key, value in lifecycle_fields.items():
        if isinstance(value, str) and value.strip().upper() in INCOMPLETE_LIFECYCLE_VALUES:
            errors.append(
                f"{path}: lifecycle field {key}={value!r} is not complete"
            )

    # Some wrappers put the lifecycle marker under a direct completion object.
    completion = report.get("completion")
    if isinstance(completion, dict):
        for key in LIFECYCLE_FIELDS:
            value = completion.get(key)
            if isinstance(value, str) and value.strip().upper() in INCOMPLETE_LIFECYCLE_VALUES:
                errors.append(
                    f"{path}: completion.{key}={value!r} is not complete"
                )

    validation = report.get("validation")
    result["validation"] = validation
    if not isinstance(validation, dict):
        errors.append(f"{path}: missing validation object")
    else:
        if validation.get("valid") is not True:
            errors.append(f"{path}: validation.valid is not true")
        if validation.get("records") != expected_count:
            errors.append(
                f"{path}: validation.records={validation.get('records')} "
                f"expected={expected_count}"
            )
        if validation.get("queries_replayed") != expected_count:
            errors.append(
                f"{path}: validation.queries_replayed="
                f"{validation.get('queries_replayed')} expected={expected_count}"
            )
        if validation.get("errors") != []:
            errors.append(f"{path}: validation.errors is not an empty list")

    for field in ("question_count",):
        if report.get(field) != expected_count:
            errors.append(
                f"{path}: {field}={report.get(field)} expected={expected_count}"
            )

    reported_zip = report.get("zip_path")
    result["reported_zip_path"] = reported_zip
    if not isinstance(reported_zip, str) or not reported_zip.strip():
        errors.append(f"{path}: missing zip_path")
    elif Path(reported_zip).name != expected_zip_name:
        errors.append(
            f"{path}: zip_path basename={Path(reported_zip).name!r} "
            f"expected={expected_zip_name!r}"
        )
    return result, report


def _check_zip(
    zip_path: Path,
    expected_count: int,
    local_submission_ids: set[str],
    local_submission_rows: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    """Check the sibling ZIP and its embedded submission without extraction."""

    result: dict[str, Any] = {
        "path": str(zip_path),
        "exists": zip_path.is_file(),
        "readable": False,
        "integrity_test": False,
        "entry_count": 0,
        "duplicate_entries": [],
        "has_submission_json": False,
        "data_csv_count": 0,
        "embedded_submission_records": 0,
        "embedded_submission_unique_ids": 0,
    }
    if not zip_path.is_file():
        errors.append(f"missing sibling ZIP: {zip_path}")
        return result

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            result["readable"] = True
            bad_member = archive.testzip()
            if bad_member is not None:
                errors.append(f"{zip_path}: corrupt ZIP member {bad_member}")
            else:
                result["integrity_test"] = True

            names = archive.namelist()
            result["entry_count"] = len(names)
            result["duplicate_entries"] = sorted(
                name for name in set(names) if names.count(name) > 1
            )
            if result["duplicate_entries"]:
                errors.append(
                    f"{zip_path}: duplicate ZIP entries "
                    f"{result['duplicate_entries'][:10]}"
                )

            result["has_submission_json"] = names.count("submission.json") == 1
            if not result["has_submission_json"]:
                errors.append(f"{zip_path}: missing unique submission.json entry")

            data_csv_names = sorted(
                name
                for name in names
                if name.startswith("data/")
                and not name.endswith("/")
                and name.lower().endswith(".csv")
            )
            result["data_csv_count"] = len(data_csv_names)
            if len(data_csv_names) != expected_count:
                errors.append(
                    f"{zip_path}: data CSV count={len(data_csv_names)} "
                    f"expected={expected_count}"
                )

            if result["has_submission_json"]:
                try:
                    embedded = json.loads(archive.read("submission.json"))
                except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
                    errors.append(f"{zip_path}: cannot parse embedded submission.json: {exc}")
                else:
                    if not isinstance(embedded, list):
                        errors.append(
                            f"{zip_path}: embedded submission.json is not a JSON list"
                        )
                    else:
                        embedded_ids = [
                            _canonical_id(row.get("id"))
                            for row in embedded
                            if isinstance(row, dict)
                        ]
                        result["embedded_submission_records"] = len(embedded)
                        result["embedded_submission_unique_ids"] = len(
                            {identifier for identifier in embedded_ids if identifier is not None}
                        )
                        if len(embedded) != expected_count:
                            errors.append(
                                f"{zip_path}: embedded submission records="
                                f"{len(embedded)} expected={expected_count}"
                            )
                        if result["embedded_submission_unique_ids"] != expected_count:
                            errors.append(
                                f"{zip_path}: embedded submission unique IDs="
                                f"{result['embedded_submission_unique_ids']} "
                                f"expected={expected_count}"
                            )
                        if embedded_ids != [
                            _canonical_id(row.get("id")) for row in local_submission_rows
                        ]:
                            errors.append(
                                f"{zip_path}: embedded submission ID order differs "
                                "from local submission.json"
                            )
                        if {
                            identifier for identifier in embedded_ids if identifier is not None
                        } != local_submission_ids:
                            errors.append(
                                f"{zip_path}: embedded submission ID set differs "
                                "from local submission.json"
                            )

            referenced_csv_names: set[str] = set()
            for index, row in enumerate(local_submission_rows, start=1):
                evidence = row.get("evidence")
                if not isinstance(evidence, list):
                    continue
                for evidence_row in evidence:
                    if not isinstance(evidence_row, dict):
                        continue
                    csv_path = str(evidence_row.get("csv_path") or "")
                    if csv_path.startswith("data/"):
                        referenced_csv_names.add(csv_path)
            missing_referenced_csv = sorted(
                referenced_csv_names - set(data_csv_names)
            )
            result["missing_referenced_csv"] = missing_referenced_csv[:20]
            if missing_referenced_csv:
                errors.append(
                    f"{zip_path}: missing referenced data CSVs "
                    f"{missing_referenced_csv[:10]}"
                )
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        errors.append(f"cannot read ZIP {zip_path}: {exc}")
    return result


def run_gate(output_dir: Path, expected_count: int) -> dict[str, Any]:
    """Run all completion checks and return a JSON-serializable summary."""

    output_dir = output_dir.expanduser().resolve()
    errors: list[str] = []
    if not output_dir.is_dir():
        errors.append(f"output directory does not exist: {output_dir}")

    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    report_path = output_dir / "build_report.json"
    submission_path = output_dir / "submission.json"

    report_result, report = _check_build_report(
        report_path,
        expected_count,
        zip_path.name,
        errors,
    )
    submission_result, submission_rows = _scan_submission_json(
        submission_path, errors
    )
    _require_count("submission.json", submission_result, expected_count, errors)

    jsonl_results: dict[str, dict[str, Any]] = {}
    for filename, id_field in JSONL_ID_FIELDS.items():
        result = _scan_jsonl(output_dir / filename, id_field, errors)
        # The best-candidate ledger is intentionally sparse when the answer
        # policy emits a documented fallback_zero row.  Every non-fallback
        # question must still have exactly one ledger row; that invariant is
        # checked against diagnostics below.
        if filename != "best_surviving_candidates_v1.jsonl":
            _require_count(filename, result, expected_count, errors)
        jsonl_results[filename] = result

    submission_ids = submission_result.get("ids", set())
    for filename, result in jsonl_results.items():
        if filename == "best_surviving_candidates_v1.jsonl":
            continue
        artifact_ids = result.get("ids", set())
        if artifact_ids != submission_ids:
            missing = sorted(submission_ids - artifact_ids)
            extra = sorted(artifact_ids - submission_ids)
            errors.append(
                f"{filename}: ID set differs from submission.json "
                f"missing={missing[:10]} extra={extra[:10]}"
            )

    diagnostics = jsonl_results.get("diagnostics.jsonl", {})
    fallback_ids = set(diagnostics.get("fallback_ids", set()))
    candidate_result = jsonl_results.get("best_surviving_candidates_v1.jsonl", {})
    candidate_ids = set(candidate_result.get("ids", set()))
    expected_candidate_ids = set(submission_ids) - fallback_ids
    if fallback_ids - submission_ids:
        errors.append(
            "diagnostics.jsonl: fallback IDs are not present in submission.json: "
            f"{sorted(fallback_ids - submission_ids)[:10]}"
        )
    if candidate_ids != expected_candidate_ids:
        missing = sorted(expected_candidate_ids - candidate_ids)
        extra = sorted(candidate_ids - expected_candidate_ids)
        errors.append(
            "best_surviving_candidates_v1.jsonl: expected exactly one row for "
            f"each non-fallback question missing={missing[:10]} extra={extra[:10]}"
        )
    if report is not None and report.get("best_candidate_ledger_count") != len(candidate_ids):
        errors.append(
            "build_report.json: best_candidate_ledger_count="
            f"{report.get('best_candidate_ledger_count')} expected={len(candidate_ids)}"
        )

    zip_result = _check_zip(
        zip_path,
        expected_count,
        submission_ids,
        submission_rows,
        errors,
    )

    summary: dict[str, Any] = {
        "schema_version": "pipeline_completion_gate_v1",
        "gate_passed": not errors,
        "expected_count": expected_count,
        "output_dir": str(output_dir),
        "zip_path": str(zip_path),
        "fallback_question_count": len(fallback_ids),
        "expected_candidate_ledger_count": len(expected_candidate_ids),
        "checks": {
            "build_report": _public_check(report_result),
            "submission": _public_check(submission_result),
            "jsonl": {
                filename: _public_check(result)
                for filename, result in jsonl_results.items()
            },
            "zip": zip_result,
        },
        "policy": {
            "read_only": True,
            "hidden_gold_read": False,
            "candidate_verification_mutated": False,
            "running_or_partial_builds_pass": False,
            "candidate_statuses_are_not_answer_verification": True,
        },
        "errors": errors,
    }
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only completion gate for a ViFinQA pipeline output directory."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory containing build_report.json and submission artifacts.",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        default=DEFAULT_EXPECTED_COUNT,
        help=f"Expected records and unique IDs (default: {DEFAULT_EXPECTED_COUNT}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: print one JSON summary and return a gate exit code."""

    args = _build_parser().parse_args(argv)
    if args.expected_count < 1:
        _build_parser().error("--expected-count must be a positive integer")
    summary = run_gate(args.output_dir, args.expected_count)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
