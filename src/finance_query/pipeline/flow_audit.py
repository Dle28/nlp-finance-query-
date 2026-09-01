"""Read-only integrity checks for one canonical submission flow.

The flow audit is deliberately structural.  It proves that the artifacts
emitted by Proposal -> Resolve -> E2E -> Compile -> Feedback belong to one
question population and one hash-bound hand-off.  It does not decide whether
an answer is semantically correct and it never upgrades a candidate to
``VERIFIED`` or ``release_authorized``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
import hashlib
import json
from pathlib import Path
import re
import zipfile
from typing import Any

from .contracts import canonical_sha256


FLOW_AUDIT_PROTOCOL = "vifinqa_submission_flow_integrity_audit_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_ref(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    result: dict[str, Any] = {
        "path": str(resolved),
        "exists": resolved.is_file(),
    }
    if resolved.is_file():
        result.update(
            {
                "sha256": _sha256_file(resolved),
                "size_bytes": resolved.stat().st_size,
            }
        )
    return result


def _load_json(path: Path, errors: list[str], *, label: str) -> Any:
    if not path.is_file():
        errors.append(f"{label}: missing file {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"{label}: cannot parse {path}: {error}")
        return None


def _load_jsonl(
    path: Path,
    errors: list[str],
    *,
    label: str,
    required: bool = True,
) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            errors.append(f"{label}: missing file {path}")
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        errors.append(f"{label}: cannot read {path}: {error}")
        return rows
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            errors.append(f"{label}: blank line {line_number} in {path}")
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append(f"{label}: invalid JSON at {path}:{line_number}: {error}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{label}: {path}:{line_number} is not a JSON object")
            continue
        rows.append(value)
    return rows


def _id_set(
    rows: Iterable[Mapping[str, Any]],
    *,
    field: str,
    label: str,
    errors: list[str],
) -> set[int]:
    identifiers: list[int] = []
    for index, row in enumerate(rows, 1):
        value = row.get(field)
        if isinstance(value, bool):
            value = None
        try:
            identifier = int(value)
        except (TypeError, ValueError):
            errors.append(f"{label}: row {index} has invalid {field}={value!r}")
            continue
        identifiers.append(identifier)
    duplicates = sorted(identifier for identifier, count in Counter(identifiers).items() if count > 1)
    if duplicates:
        errors.append(f"{label}: duplicate {field} values {duplicates[:10]}")
    return set(identifiers)


def _id_digest(identifiers: Iterable[int]) -> str:
    return canonical_sha256(sorted(int(identifier) for identifier in identifiers))


def _check_declared_ref(
    declared: object,
    *,
    expected_path: Path,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(declared, Mapping):
        errors.append(f"{label}: missing artifact descriptor")
        return
    actual_path = expected_path.resolve()
    declared_path = declared.get("path")
    if not isinstance(declared_path, str) or Path(declared_path).resolve() != actual_path:
        errors.append(
            f"{label}: declared path={declared_path!r} expected={str(actual_path)!r}"
        )
    if not actual_path.is_file():
        errors.append(f"{label}: expected file is missing: {actual_path}")
        return
    declared_sha = declared.get("sha256")
    actual_sha = _sha256_file(actual_path)
    if declared_sha != actual_sha:
        errors.append(
            f"{label}: sha256 mismatch declared={declared_sha!r} actual={actual_sha!r}"
        )


def _check_jsonl_output(
    manifest: Mapping[str, Any],
    *,
    output_name: str,
    expected_path: Path,
    label: str,
    errors: list[str],
) -> None:
    outputs = manifest.get("outputs")
    descriptor = outputs.get(output_name) if isinstance(outputs, Mapping) else None
    _check_declared_ref(
        descriptor,
        expected_path=expected_path,
        label=f"{label}.outputs.{output_name}",
        errors=errors,
    )


def _check_protocol(row: Mapping[str, Any], *, protocol: str, label: str, errors: list[str]) -> None:
    if row.get("schema_version") != 1 or row.get("protocol") != protocol:
        errors.append(
            f"{label}: unexpected protocol/schema "
            f"schema={row.get('schema_version')!r} protocol={row.get('protocol')!r}"
        )


def _check_proposal_stage(
    proposal_dir: Path,
    *,
    errors: list[str],
) -> tuple[set[int], dict[str, Any]]:
    submission_path = proposal_dir / "submission.json"
    submission = _load_json(submission_path, errors, label="proposal.submission")
    if not isinstance(submission, list) or any(not isinstance(row, dict) for row in submission):
        errors.append("proposal.submission: expected a JSON list of objects")
        submission_rows: list[dict[str, Any]] = []
    else:
        submission_rows = [dict(row) for row in submission]
    submission_ids = _id_set(
        submission_rows,
        field="id",
        label="proposal.submission",
        errors=errors,
    )

    diagnostics = _load_jsonl(
        proposal_dir / "diagnostics.jsonl",
        errors,
        label="proposal.diagnostics",
    )
    diagnostic_ids = _id_set(
        diagnostics,
        field="id",
        label="proposal.diagnostics",
        errors=errors,
    )
    audits = _load_jsonl(
        proposal_dir / "prediction_audit_ledger_v1.jsonl",
        errors,
        label="proposal.audit",
    )
    audit_ids = _id_set(
        audits,
        field="question_id",
        label="proposal.audit",
        errors=errors,
    )
    candidates = _load_jsonl(
        proposal_dir / "best_surviving_candidates_v1.jsonl",
        errors,
        label="proposal.candidates",
        required=False,
    )
    candidate_ids = _id_set(
        candidates,
        field="question_id",
        label="proposal.candidates",
        errors=errors,
    )

    for name, identifiers in (
        ("diagnostics", diagnostic_ids),
        ("audit", audit_ids),
    ):
        if identifiers != submission_ids:
            errors.append(
                f"proposal.{name}: question IDs differ from submission "
                f"missing={sorted(submission_ids - identifiers)[:10]} "
                f"extra={sorted(identifiers - submission_ids)[:10]}"
            )

    build_report_path = proposal_dir / "build_report.json"
    build_report = _load_json(build_report_path, errors, label="proposal.build_report")
    if not isinstance(build_report, Mapping):
        build_report = {}
    validation = build_report.get("validation")
    if not isinstance(validation, Mapping):
        errors.append("proposal.build_report: missing validation object")
    else:
        if validation.get("valid") is not True:
            errors.append("proposal.build_report: validation.valid is not true")
        if validation.get("records") != len(submission_rows):
            errors.append(
                "proposal.build_report: validation.records does not match submission count"
            )
        if validation.get("queries_replayed") != len(submission_rows):
            errors.append(
                "proposal.build_report: validation.queries_replayed does not match submission count"
            )
        if validation.get("errors") != []:
            errors.append("proposal.build_report: validation.errors is not empty")
    if "question_count" in build_report and build_report.get("question_count") != len(submission_rows):
        errors.append("proposal.build_report: question_count does not match submission count")

    check = {
        "status": "PASS",
        "directory": str(proposal_dir.resolve()),
        "question_count": len(submission_rows),
        "submission_ids_sha256": _id_digest(submission_ids),
        "candidate_count": len(candidate_ids),
        "artifacts": {
            "submission": _artifact_ref(submission_path),
            "diagnostics": _artifact_ref(proposal_dir / "diagnostics.jsonl"),
            "audit": _artifact_ref(proposal_dir / "prediction_audit_ledger_v1.jsonl"),
            "candidates": _artifact_ref(proposal_dir / "best_surviving_candidates_v1.jsonl"),
            "build_report": _artifact_ref(build_report_path),
        },
    }
    return submission_ids, check


def _check_resolver_stage(
    resolver_dir: Path,
    *,
    proposal_dir: Path,
    expected_ids: set[int],
    errors: list[str],
) -> tuple[set[int], dict[str, Any]]:
    manifest_path = resolver_dir / "resolver_run.manifest.json"
    predictions_path = resolver_dir / "resolved_predictions_v1.jsonl"
    manifest = _load_json(manifest_path, errors, label="resolver.manifest")
    if not isinstance(manifest, Mapping):
        manifest = {}
    predictions = _load_jsonl(predictions_path, errors, label="resolver.predictions")
    prediction_ids = _id_set(
        predictions,
        field="question_id",
        label="resolver.predictions",
        errors=errors,
    )
    if prediction_ids != expected_ids:
        errors.append(
            "resolver.predictions: question IDs differ from proposal "
            f"missing={sorted(expected_ids - prediction_ids)[:10]} "
            f"extra={sorted(prediction_ids - expected_ids)[:10]}"
        )
    for row in predictions:
        _check_protocol(
            row,
            protocol="vifinqa_resolved_prediction_v1",
            label=f"resolver.predictions[{row.get('question_id')}]",
            errors=errors,
        )
        identifier = row.get("resolved_prediction_id")
        valid_identifier = isinstance(identifier, str) and bool(_SHA256_RE.fullmatch(identifier))
        if not valid_identifier or canonical_sha256(
            {key: value for key, value in row.items() if key != "resolved_prediction_id"}
        ) != identifier:
            errors.append(
                f"resolver.predictions[{row.get('question_id')}]: resolved_prediction_id mismatch"
            )

    inputs = manifest.get("inputs")
    if isinstance(inputs, Mapping):
        for name in (
            "submission.json",
            "diagnostics.jsonl",
            "prediction_audit_ledger_v1.jsonl",
            "best_surviving_candidates_v1.jsonl",
            "build_report.json",
        ):
            path = proposal_dir / name
            if path.is_file() and name in inputs:
                _check_declared_ref(
                    inputs.get(name),
                    expected_path=path,
                    label=f"resolver.inputs.{name}",
                    errors=errors,
                )
    _check_jsonl_output(
        manifest,
        output_name="resolved_predictions",
        expected_path=predictions_path,
        label="resolver.manifest",
        errors=errors,
    )
    counts = manifest.get("counts")
    if isinstance(counts, Mapping) and counts.get("question_count") != len(predictions):
        errors.append("resolver.manifest: counts.question_count does not match predictions")

    return prediction_ids, {
        "status": "PASS",
        "directory": str(resolver_dir.resolve()),
        "question_count": len(predictions),
        "question_ids_sha256": _id_digest(prediction_ids),
        "artifacts": {
            "manifest": _artifact_ref(manifest_path),
            "predictions": _artifact_ref(predictions_path),
        },
    }


def _check_observer_stage(
    observer_dir: Path | None,
    *,
    resolver_predictions_path: Path,
    expected_ids: set[int],
    required: bool,
    errors: list[str],
) -> tuple[set[int], dict[str, Any]]:
    if observer_dir is None:
        if required:
            errors.append("e2e_observer: required but no observer output was produced")
        return set(), {
            "status": "SKIPPED_EXPLICITLY",
            "required": required,
            "question_count": 0,
        }

    manifest_path = observer_dir / "e2e_observer.manifest.json"
    receipts_path = observer_dir / "e2e_receipts_v1.jsonl"
    run_receipt_path = observer_dir / "grounded_e2e_run_v1.json"
    manifest = _load_json(manifest_path, errors, label="e2e_observer.manifest")
    if not isinstance(manifest, Mapping):
        manifest = {}
    receipts = _load_jsonl(receipts_path, errors, label="e2e_observer.receipts")
    receipt_ids = _id_set(
        receipts,
        field="question_id",
        label="e2e_observer.receipts",
        errors=errors,
    )
    if receipt_ids != expected_ids:
        errors.append(
            "e2e_observer.receipts: question IDs differ from resolver "
            f"missing={sorted(expected_ids - receipt_ids)[:10]} "
            f"extra={sorted(receipt_ids - expected_ids)[:10]}"
        )
    for row in receipts:
        _check_protocol(
            row,
            protocol="vifinqa_e2e_receipt_v1",
            label=f"e2e_observer.receipts[{row.get('question_id')}]",
            errors=errors,
        )
        identifier = row.get("e2e_receipt_id")
        if not isinstance(identifier, str) or not _SHA256_RE.fullmatch(identifier):
            errors.append(
                f"e2e_observer.receipts[{row.get('question_id')}]: invalid e2e_receipt_id"
            )
        elif canonical_sha256(
            {key: value for key, value in row.items() if key != "e2e_receipt_id"}
        ) != identifier:
            errors.append(
                f"e2e_observer.receipts[{row.get('question_id')}]: e2e_receipt_id mismatch"
            )

    inputs = manifest.get("inputs")
    if isinstance(inputs, Mapping):
        _check_declared_ref(
            inputs.get("resolved_predictions"),
            expected_path=resolver_predictions_path,
            label="e2e_observer.inputs.resolved_predictions",
            errors=errors,
        )
    _check_jsonl_output(
        manifest,
        output_name="e2e_receipts",
        expected_path=receipts_path,
        label="e2e_observer.manifest",
        errors=errors,
    )
    outputs = manifest.get("outputs")
    _check_declared_ref(
        outputs.get("grounded_e2e_run") if isinstance(outputs, Mapping) else None,
        expected_path=run_receipt_path,
        label="e2e_observer.outputs.grounded_e2e_run",
        errors=errors,
    )
    raw_run_receipt = _load_json(run_receipt_path, errors, label="e2e_observer.run_receipt")
    if not isinstance(raw_run_receipt, Mapping):
        errors.append("e2e_observer.run_receipt: expected a JSON object")
    counts = manifest.get("counts")
    if isinstance(counts, Mapping) and counts.get("question_count") != len(receipts):
        errors.append("e2e_observer.manifest: counts.question_count does not match receipts")

    return receipt_ids, {
        "status": "PASS",
        "required": required,
        "directory": str(observer_dir.resolve()),
        "question_count": len(receipts),
        "question_ids_sha256": _id_digest(receipt_ids),
        "e2e_status_counts": dict(
            sorted(Counter(str(row.get("e2e_status") or "UNKNOWN") for row in receipts).items())
        ),
        "artifacts": {
            "manifest": _artifact_ref(manifest_path),
            "receipts": _artifact_ref(receipts_path),
            "grounded_e2e_run": _artifact_ref(run_receipt_path),
        },
    }


def _check_zip(
    archive_path: Path,
    *,
    expected_ids: set[int],
    local_submission: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(archive_path.resolve()),
        "exists": archive_path.is_file(),
        "integrity_test": False,
        "entry_count": 0,
        "data_csv_count": 0,
    }
    if not archive_path.is_file():
        errors.append(f"compiler.zip: missing archive {archive_path}")
        return result
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = archive.namelist()
            result["entry_count"] = len(names)
            result["integrity_test"] = archive.testzip() is None
            if not result["integrity_test"]:
                errors.append(f"compiler.zip: corrupt member in {archive_path}")
            if len(names) != len(set(names)):
                errors.append("compiler.zip: duplicate ZIP entries")
            if names.count("submission.json") != 1:
                errors.append("compiler.zip: expected exactly one submission.json")
            csv_names = sorted(
                name
                for name in names
                if name.startswith("data/") and name.lower().endswith(".csv")
            )
            result["data_csv_count"] = len(csv_names)
            if len(csv_names) != len(expected_ids):
                errors.append(
                    f"compiler.zip: data CSV count={len(csv_names)} expected={len(expected_ids)}"
                )
            if "submission.json" in names:
                embedded = json.loads(archive.read("submission.json"))
                if not isinstance(embedded, list) or any(not isinstance(row, dict) for row in embedded):
                    errors.append("compiler.zip: embedded submission.json is not a list of objects")
                else:
                    embedded_ids = _id_set(
                        embedded,
                        field="id",
                        label="compiler.zip.embedded_submission",
                        errors=errors,
                    )
                    if embedded_ids != expected_ids:
                        errors.append("compiler.zip: embedded submission IDs differ from flow population")
                    local_ids = [int(row["id"]) for row in local_submission]
                    embedded_order = [int(row["id"]) for row in embedded]
                    if embedded_order != local_ids:
                        errors.append("compiler.zip: embedded submission ID order differs from local output")
    except (OSError, zipfile.BadZipFile, RuntimeError, json.JSONDecodeError, TypeError, ValueError) as error:
        errors.append(f"compiler.zip: cannot inspect {archive_path}: {error}")
    return result


def _check_compiler_stage(
    compile_dir: Path,
    *,
    proposal_dir: Path,
    resolver_dir: Path,
    resolver_predictions_path: Path,
    observer_dir: Path | None,
    observer_receipts_path: Path | None,
    expected_ids: set[int],
    errors: list[str],
) -> tuple[set[int], dict[str, Any]]:
    manifest_path = compile_dir / "submission_compile.manifest.json"
    report_path = compile_dir / "submission_compile_report.json"
    submission_path = compile_dir / "submission.json"
    ledger_path = compile_dir / "submission_ledger_v1.jsonl"
    archive_path = compile_dir / "submission.zip"
    manifest = _load_json(manifest_path, errors, label="compiler.manifest")
    if not isinstance(manifest, Mapping):
        manifest = {}
    report = _load_json(report_path, errors, label="compiler.report")
    if not isinstance(report, Mapping):
        report = {}
    submission = _load_json(submission_path, errors, label="compiler.submission")
    submission_rows = (
        [dict(row) for row in submission]
        if isinstance(submission, list) and all(isinstance(row, dict) for row in submission)
        else []
    )
    submission_ids = _id_set(
        submission_rows,
        field="id",
        label="compiler.submission",
        errors=errors,
    )
    ledger = _load_jsonl(ledger_path, errors, label="compiler.ledger")
    ledger_ids = _id_set(
        ledger,
        field="question_id",
        label="compiler.ledger",
        errors=errors,
    )
    for label, identifiers in (("submission", submission_ids), ("ledger", ledger_ids)):
        if identifiers != expected_ids:
            errors.append(
                f"compiler.{label}: question IDs differ from flow population "
                f"missing={sorted(expected_ids - identifiers)[:10]} "
                f"extra={sorted(identifiers - expected_ids)[:10]}"
            )

    for payload, label in ((manifest, "compiler.manifest"), (report, "compiler.report")):
        if payload.get("question_count") != len(expected_ids):
            errors.append(f"{label}: question_count does not match flow population")
    strict_ready_count = sum(row.get("delivery_status") == "STRICT_READY" for row in ledger)
    for payload, label in ((manifest, "compiler.manifest"), (report, "compiler.report")):
        if payload.get("strict_ready_count") != strict_ready_count:
            errors.append(f"{label}: strict_ready_count does not match ledger")

    inputs = manifest.get("inputs")
    if isinstance(inputs, Mapping):
        _check_declared_ref(
            inputs.get("resolved_predictions"),
            expected_path=resolver_predictions_path,
            label="compiler.inputs.resolved_predictions",
            errors=errors,
        )
        declared_e2e = inputs.get("e2e_receipts")
        if observer_receipts_path is None:
            if declared_e2e is not None:
                errors.append("compiler.inputs.e2e_receipts: present although E2E was skipped")
        else:
            _check_declared_ref(
                declared_e2e,
                expected_path=observer_receipts_path,
                label="compiler.inputs.e2e_receipts",
                errors=errors,
            )
    outputs = manifest.get("outputs")
    for name, path in (
        ("submission", submission_path),
        ("ledger", ledger_path),
        ("archive", archive_path),
        ("report", report_path),
    ):
        _check_declared_ref(
            outputs.get(name) if isinstance(outputs, Mapping) else None,
            expected_path=path,
            label=f"compiler.outputs.{name}",
            errors=errors,
        )

    zip_result = _check_zip(
        archive_path,
        expected_ids=expected_ids,
        local_submission=submission_rows,
        errors=errors,
    )
    return ledger_ids, {
        "status": "PASS",
        "directory": str(compile_dir.resolve()),
        "question_count": len(submission_rows),
        "question_ids_sha256": _id_digest(submission_ids),
        "delivery_status_counts": dict(
            sorted(Counter(str(row.get("delivery_status") or "UNKNOWN") for row in ledger).items())
        ),
        "strict_ready_count": strict_ready_count,
        "artifacts": {
            "manifest": _artifact_ref(manifest_path),
            "report": _artifact_ref(report_path),
            "submission": _artifact_ref(submission_path),
            "ledger": _artifact_ref(ledger_path),
            "archive": _artifact_ref(archive_path),
        },
        "zip": zip_result,
    }


def _check_feedback_stage(
    feedback_dir: Path,
    *,
    compile_dir: Path,
    errors: list[str],
) -> dict[str, Any]:
    manifest_path = feedback_dir / "blocked_feedback_run.manifest.json"
    summary_path = feedback_dir / "feedback_summary.json"
    manifest = _load_json(manifest_path, errors, label="feedback.manifest")
    if not isinstance(manifest, Mapping):
        manifest = {}
    summary = _load_json(summary_path, errors, label="feedback.summary")
    if not isinstance(summary, Mapping):
        summary = {}
    output_names = {
        "context_packets": "blocked_question_context_packets_v1.jsonl",
        "feedback_records": "blocked_question_feedback_v1.jsonl",
        "improvement_records": "improvement_feedback_records_v1.jsonl",
        "improvement_plan": "improvement_plan_v1.json",
        "summary": "feedback_summary.json",
    }
    outputs = manifest.get("outputs")
    for name, filename in output_names.items():
        _check_declared_ref(
            outputs.get(name) if isinstance(outputs, Mapping) else None,
            expected_path=feedback_dir / filename,
            label=f"feedback.outputs.{name}",
            errors=errors,
        )
    inputs = manifest.get("inputs")
    if isinstance(inputs, Mapping):
        for name in (
            "submission.json",
            "diagnostics.jsonl",
            "prediction_audit_ledger_v1.jsonl",
            "build_report.json",
            "resolved_predictions_v1.jsonl",
            "e2e_receipts_v1.jsonl",
            "submission_compile.manifest.json",
        ):
            path = compile_dir / name
            if path.is_file() and name in inputs:
                _check_declared_ref(
                    inputs.get(name),
                    expected_path=path,
                    label=f"feedback.inputs.{name}",
                    errors=errors,
                )
    packets = _load_jsonl(
        feedback_dir / output_names["context_packets"],
        errors,
        label="feedback.packets",
    )
    records = _load_jsonl(
        feedback_dir / output_names["feedback_records"],
        errors,
        label="feedback.records",
    )
    improvements = _load_jsonl(
        feedback_dir / output_names["improvement_records"],
        errors,
        label="feedback.improvements",
    )
    packet_ids = _id_set(packets, field="question_id", label="feedback.packets", errors=errors)
    record_ids = _id_set(records, field="question_id", label="feedback.records", errors=errors)
    improvement_ids = _id_set(
        improvements,
        field="question_id",
        label="feedback.improvements",
        errors=errors,
    )
    if record_ids != packet_ids:
        errors.append("feedback.records: question IDs differ from context packets")
    if improvement_ids != packet_ids:
        errors.append("feedback.improvements: question IDs differ from context packets")
    for index, row in enumerate(records, 1):
        for key in (
            "answer_authorized",
            "evidence_authorized",
            "training_eligible",
            "promotion_allowed",
            "submission_eligible",
            "release_authorized",
        ):
            if row.get(key) is not False:
                errors.append(f"feedback.records[{index}]: {key} must be false")
    for payload, label in ((manifest, "feedback.manifest"), (summary, "feedback.summary")):
        for key in ("answer_authorized", "evidence_authorized", "training_eligible", "promotion_allowed", "submission_eligible", "release_authorized"):
            if key in payload and payload.get(key) is not False:
                errors.append(f"{label}: {key} must be false")
    for payload, label, key, observed in (
        (manifest, "feedback.manifest", "blocked_count", len(packets)),
        (manifest, "feedback.manifest", "feedback_record_count", len(records)),
        (manifest, "feedback.manifest", "improvement_record_count", len(improvements)),
        (summary, "feedback.summary", "blocked_count", len(packets)),
        (summary, "feedback.summary", "feedback_record_count", len(records)),
        (summary, "feedback.summary", "improvement_record_count", len(improvements)),
    ):
        if key in payload and payload.get(key) != observed:
            errors.append(f"{label}: {key} does not match emitted records")
    return {
        "status": "PASS",
        "directory": str(feedback_dir.resolve()),
        "blocked_count": len(packets),
        "feedback_record_count": len(records),
        "improvement_record_count": len(improvements),
        "question_ids_sha256": _id_digest(packet_ids),
        "artifacts": {
            "manifest": _artifact_ref(manifest_path),
            "summary": _artifact_ref(summary_path),
            **{
                name: _artifact_ref(feedback_dir / filename)
                for name, filename in output_names.items()
            },
        },
    }


def audit_submission_flow(
    *,
    proposal_dir: Path,
    resolver_dir: Path,
    observer_dir: Path | None,
    compile_dir: Path,
    feedback_dir: Path,
    expected_question_count: int | None = None,
    require_e2e: bool = True,
) -> dict[str, Any]:
    """Audit one flow's cross-stage lineage without mutating any input.

    ``expected_question_count`` is optional for development fixtures.  A
    production/full-population invocation should pass the manifest count
    explicitly (for ViFinQA this is normally 1,012).
    """

    errors: list[str] = []
    proposal_ids, proposal_check = _check_proposal_stage(
        proposal_dir.resolve(),
        errors=errors,
    )
    observed_count = len(proposal_ids)
    if expected_question_count is not None:
        if expected_question_count < 1:
            errors.append("population: expected_question_count must be positive")
        elif observed_count != expected_question_count:
            errors.append(
                f"population: observed={observed_count} expected={expected_question_count}"
            )

    resolver_predictions_path = resolver_dir.resolve() / "resolved_predictions_v1.jsonl"
    resolver_ids, resolver_check = _check_resolver_stage(
        resolver_dir.resolve(),
        proposal_dir=proposal_dir.resolve(),
        expected_ids=proposal_ids,
        errors=errors,
    )
    observer_ids, observer_check = _check_observer_stage(
        observer_dir.resolve() if observer_dir is not None else None,
        resolver_predictions_path=resolver_predictions_path,
        expected_ids=resolver_ids,
        required=require_e2e,
        errors=errors,
    )
    observer_receipts_path = (
        observer_dir.resolve() / "e2e_receipts_v1.jsonl" if observer_dir is not None else None
    )
    ledger_ids, compiler_check = _check_compiler_stage(
        compile_dir.resolve(),
        proposal_dir=proposal_dir.resolve(),
        resolver_dir=resolver_dir.resolve(),
        resolver_predictions_path=resolver_predictions_path,
        observer_dir=observer_dir.resolve() if observer_dir is not None else None,
        observer_receipts_path=observer_receipts_path,
        expected_ids=proposal_ids,
        errors=errors,
    )
    feedback_check = _check_feedback_stage(
        feedback_dir.resolve(),
        compile_dir=compile_dir.resolve(),
        errors=errors,
    )

    if resolver_ids != proposal_ids:
        errors.append("cross_stage: resolver population does not match proposal population")
    if observer_dir is not None and observer_ids != resolver_ids:
        errors.append("cross_stage: E2E receipt population does not match resolver population")
    if ledger_ids != proposal_ids:
        errors.append("cross_stage: compiler ledger population does not match proposal population")

    return {
        "schema_version": 1,
        "protocol": FLOW_AUDIT_PROTOCOL,
        "gate_passed": not errors,
        "status": "PASS" if not errors else "FAIL",
        "population": {
            "expected_question_count": expected_question_count,
            "observed_question_count": observed_count,
            "question_ids_sha256": _id_digest(proposal_ids),
        },
        "checks": {
            "proposal": proposal_check,
            "resolver": resolver_check,
            "e2e_observer": observer_check,
            "submission_compiler": compiler_check,
            "blocked_feedback": feedback_check,
        },
        "authority": {
            "candidate_output_is_not_answer_authority": True,
            "e2e_required_for_verified": True,
            "e2e_was_required_for_this_run": require_e2e,
            "release_authorized": False,
            "promotion_allowed": False,
            "accuracy_measured": False,
        },
        "errors": errors,
    }


def write_flow_audit(path: Path, audit: Mapping[str, Any]) -> Path:
    """Write one immutable audit report and return its resolved path."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
    return path


__all__ = [
    "FLOW_AUDIT_PROTOCOL",
    "audit_submission_flow",
    "write_flow_audit",
]
