#!/usr/bin/env python3
"""Build a fail-closed quality gate for machine-silver retriever training.

The gate is a decision record, not a label exporter.  It binds a proposed
training JSONL to an independent audit, a structural fingerprint census, and
human-reviewed calibration metrics.  Missing or legacy evidence is recorded as
``BLOCKED`` rather than converted into an optimistic metric.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vifinqa_retriever_training_quality_gate_v1"
SCHEMA_VERSION = 1
MIN_MACHINE_SILVER_PAIRS = 200
MIN_INDEPENDENT_AUDIT_PRECISION = 0.99
MIN_AUDIT_CI95_LOWER = 0.97
MIN_MAJOR_FINGERPRINT_COVERAGE = 0.90
MIN_MAJOR_FINGERPRINT_QUESTIONS = 10
AUDIT_PROTOCOL = "production_independent_audit_v1"
FINGERPRINT_PROTOCOL = "deterministic_question_plan_fingerprint_v1"
CALIBRATION_METRIC_PROTOCOL = "human_verified_independent_accept_precision_wilson_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _question_id(row: Mapping[str, Any]) -> int | None:
    raw_id = row.get("question_id", row.get("id"))
    try:
        value = int(raw_id)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _non_promoting_contract(contract: Any) -> bool:
    if not isinstance(contract, Mapping):
        return False
    return all(
        contract.get(key) is False
        for key in (
            "evidence_eligible",
            "training_eligible",
            "submission_eligible",
            "promotion_allowed",
        )
    )


def _non_promoting_flags(record: Mapping[str, Any]) -> bool:
    return all(
        record.get(key) is False
        for key in (
            "answer_eligible",
            "training_eligible",
            "provenance_promotion_allowed",
        )
    )


def _numeric_metric(metrics: Mapping[str, Any], key: str, reasons: list[str]) -> float | None:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _add_reason(reasons, f"CALIBRATION_{key.upper()}_MISSING")
        return None
    return float(value)


def _manifest_path(path: Path) -> Path:
    return path.with_suffix(".manifest.json")


def _validate_label_rows(rows: list[dict[str, Any]], reasons: list[str]) -> tuple[set[int], int]:
    question_ids: set[int] = set()
    pairs = 0
    for line_number, row in enumerate(rows, start=1):
        question_id = _question_id(row)
        positives = row.get("positive_table_uids")
        source_valid = (
            row.get("annotation_status") == "machine_calibrated"
            and row.get("label_source") == "machine"
            and isinstance(positives, list)
            and bool(positives)
            and bool((row.get("structure_validation") or {}).get("validated"))
            and bool((row.get("machine_self_review") or {}).get("training_eligible"))
            and (row.get("direct_replay_gate") or {}).get("training_gate_only") is True
            and (row.get("independent_critic_gate") or {}).get("training_gate_only") is True
            and row.get("answer_eligible") is not True
            and row.get("submission_eligible") is not True
            and row.get("provenance_promotion_allowed") is not True
        )
        if question_id is None:
            _add_reason(reasons, "LABEL_QUESTION_ID_INVALID")
        elif question_id in question_ids:
            _add_reason(reasons, "LABEL_QUESTION_ID_DUPLICATE")
        else:
            question_ids.add(question_id)
        if not source_valid:
            _add_reason(reasons, "LABEL_PROVENANCE_LEAKAGE")
        pairs += len(positives) if isinstance(positives, list) else 0
    if not rows:
        _add_reason(reasons, "LABELS_EMPTY")
    return question_ids, pairs


def _validate_audit(
    audit_path: Path,
    label_question_ids: set[int],
    reasons: list[str],
) -> tuple[dict[int, dict[str, Any]], str]:
    rows = load_jsonl(audit_path)
    audit_sha = sha256_file(audit_path)
    manifest_path = _manifest_path(audit_path)
    if not manifest_path.is_file():
        _add_reason(reasons, "INDEPENDENT_AUDIT_MANIFEST_MISSING")
        return {}, audit_sha
    manifest = load_json(manifest_path)
    if (
        manifest.get("protocol") != AUDIT_PROTOCOL
        or manifest.get("sidecar_sha256") != audit_sha
        or manifest.get("reviewer_inputs_used") != []
        or not _non_promoting_flags(manifest)
    ):
        _add_reason(reasons, "INDEPENDENT_AUDIT_CONTRACT_INVALID")
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = _question_id(row)
        if question_id is None or question_id in by_id:
            _add_reason(reasons, "INDEPENDENT_AUDIT_IDS_INVALID")
            continue
        by_id[question_id] = row
    if any(
        question_id not in by_id
        or by_id[question_id].get("independent_audit_status") != "passed"
        or by_id[question_id].get("answer_eligible") is not False
        or by_id[question_id].get("training_eligible") is not False
        or by_id[question_id].get("provenance_promotion_allowed") is not False
        for question_id in label_question_ids
    ):
        _add_reason(reasons, "LABELS_NOT_INDEPENDENTLY_AUDITED")
    return by_id, audit_sha


def _validate_fingerprints(
    census_path: Path,
    label_question_ids: set[int],
    reasons: list[str],
    *,
    minimum_major_questions: int,
) -> tuple[dict[str, Any], str]:
    rows = load_jsonl(census_path)
    census_sha = sha256_file(census_path)
    manifest_path = _manifest_path(census_path)
    if not manifest_path.is_file():
        _add_reason(reasons, "FINGERPRINT_MANIFEST_MISSING")
        return {"major_fingerprint_coverage": None}, census_sha
    manifest = load_json(manifest_path)
    if (
        manifest.get("protocol") != FINGERPRINT_PROTOCOL
        or int(manifest.get("schema_version") or 0) < 2
        or manifest.get("sidecar_sha256") != census_sha
        or not _non_promoting_flags(manifest)
    ):
        _add_reason(reasons, "FINGERPRINT_CENSUS_CONTRACT_INVALID")
    by_id: dict[int, str] = {}
    for row in rows:
        question_id = _question_id(row)
        fingerprint = row.get("structural_fingerprint")
        if question_id is None or question_id in by_id or not isinstance(fingerprint, str) or not fingerprint:
            _add_reason(reasons, "FINGERPRINT_ROWS_INVALID")
            continue
        by_id[question_id] = fingerprint
    counts = Counter(by_id.values())
    major = {fingerprint for fingerprint, count in counts.items() if count >= minimum_major_questions}
    covered = {
        by_id[question_id]
        for question_id in label_question_ids
        if question_id in by_id and by_id[question_id] in major
    }
    missing = sorted(question_id for question_id in label_question_ids if question_id not in by_id)
    if missing:
        _add_reason(reasons, "LABELS_MISSING_FINGERPRINT")
    if not major:
        _add_reason(reasons, "MAJOR_FINGERPRINTS_UNDEFINED")
        coverage = None
    else:
        coverage = len(covered) / len(major)
    return {
        "major_fingerprint_count": len(major),
        "covered_major_fingerprint_count": len(covered),
        "major_fingerprint_coverage": coverage,
        "missing_label_fingerprint_question_ids": missing,
    }, census_sha


def _validate_calibration(
    calibration_path: Path,
    reasons: list[str],
) -> tuple[dict[str, float | int | None], str]:
    calibration = load_json(calibration_path)
    calibration_sha = sha256_file(calibration_path)
    metrics = calibration.get("training_quality_metrics")
    if not _non_promoting_contract(calibration.get("source_contract")):
        _add_reason(reasons, "CALIBRATION_SOURCE_CONTRACT_INVALID")
    if not isinstance(metrics, Mapping):
        _add_reason(reasons, "CALIBRATION_AUDIT_METRICS_MISSING")
        return {
            "independent_audit_precision": None,
            "audit_ci95_lower": None,
            "independent_audit_sample_size": None,
        }, calibration_sha
    if metrics.get("metric_protocol") != CALIBRATION_METRIC_PROTOCOL:
        _add_reason(reasons, "CALIBRATION_METRIC_PROTOCOL_INVALID")
    precision = _numeric_metric(metrics, "independent_audit_precision", reasons)
    lower = _numeric_metric(metrics, "audit_ci95_lower", reasons)
    sample_size = metrics.get("independent_audit_sample_size")
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 1:
        _add_reason(reasons, "CALIBRATION_SAMPLE_SIZE_MISSING")
        sample_size = None
    return {
        "independent_audit_precision": precision,
        "audit_ci95_lower": lower,
        "independent_audit_sample_size": sample_size,
    }, calibration_sha


def build_quality_gate(
    train_jsonl: Path,
    independent_audit: Path,
    fingerprint_census: Path,
    calibration_scores: Path,
    output: Path,
    *,
    minimum_pairs: int = MIN_MACHINE_SILVER_PAIRS,
    minimum_major_fingerprint_questions: int = MIN_MAJOR_FINGERPRINT_QUESTIONS,
) -> dict[str, Any]:
    """Write a hash-bound decision record and return it for callers/tests."""
    for path in (train_jsonl, independent_audit, fingerprint_census, calibration_scores):
        if not path.is_file():
            raise FileNotFoundError(path)
    if minimum_pairs < 1 or minimum_major_fingerprint_questions < 1:
        raise ValueError("Quality-gate minimums must be positive")

    reasons: list[str] = []
    labels = load_jsonl(train_jsonl)
    label_question_ids, pair_count = _validate_label_rows(labels, reasons)
    _, audit_sha = _validate_audit(independent_audit, label_question_ids, reasons)
    coverage, census_sha = _validate_fingerprints(
        fingerprint_census,
        label_question_ids,
        reasons,
        minimum_major_questions=minimum_major_fingerprint_questions,
    )
    calibration, calibration_sha = _validate_calibration(calibration_scores, reasons)
    if pair_count < minimum_pairs:
        _add_reason(reasons, "INSUFFICIENT_MACHINE_SILVER_PAIRS")
    precision = calibration["independent_audit_precision"]
    if precision is not None and precision < MIN_INDEPENDENT_AUDIT_PRECISION:
        _add_reason(reasons, "INDEPENDENT_AUDIT_PRECISION_BELOW_THRESHOLD")
    audit_ci_lower = calibration["audit_ci95_lower"]
    if audit_ci_lower is not None and audit_ci_lower < MIN_AUDIT_CI95_LOWER:
        _add_reason(reasons, "AUDIT_CI95_LOWER_BELOW_THRESHOLD")
    fingerprint_coverage = coverage["major_fingerprint_coverage"]
    if (
        fingerprint_coverage is not None
        and fingerprint_coverage < MIN_MAJOR_FINGERPRINT_COVERAGE
    ):
        _add_reason(reasons, "MAJOR_FINGERPRINT_COVERAGE_BELOW_THRESHOLD")

    ready = not reasons
    labels_sha = sha256_file(train_jsonl)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "status": "READY" if ready else "BLOCKED",
        "training_eligible": ready,
        "answer_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "labels_sha256": labels_sha,
        "independent_audit_sha256": audit_sha,
        "fingerprint_census_sha256": census_sha,
        "calibration_scores_sha256": calibration_sha,
        "machine_silver_pair_count": pair_count,
        "machine_silver_question_count": len(label_question_ids),
        "provenance_leakage_check": "PASS" if "LABEL_PROVENANCE_LEAKAGE" not in reasons else "FAIL",
        "thresholds": {
            "minimum_machine_silver_pairs": minimum_pairs,
            "minimum_independent_audit_precision": MIN_INDEPENDENT_AUDIT_PRECISION,
            "minimum_audit_ci95_lower": MIN_AUDIT_CI95_LOWER,
            "minimum_major_fingerprint_coverage": MIN_MAJOR_FINGERPRINT_COVERAGE,
            "minimum_major_fingerprint_questions": minimum_major_fingerprint_questions,
        },
        "metrics": {
            **calibration,
            "major_fingerprint_coverage": fingerprint_coverage,
        },
        "fingerprint_coverage": coverage,
        "inputs": {
            "labels": {"path": str(train_jsonl), "sha256": labels_sha},
            "independent_audit": {"path": str(independent_audit), "sha256": audit_sha},
            "fingerprint_census": {"path": str(fingerprint_census), "sha256": census_sha},
            "calibration_scores": {"path": str(calibration_scores), "sha256": calibration_sha},
        },
        "reason_codes": sorted(reasons),
    }
    atomic_json(output, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-jsonl", type=Path, required=True)
    parser.add_argument("--independent-audit", type=Path, required=True)
    parser.add_argument("--fingerprint-census", type=Path, required=True)
    parser.add_argument("--calibration-scores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-pairs", type=int, default=MIN_MACHINE_SILVER_PAIRS)
    parser.add_argument(
        "--minimum-major-fingerprint-questions",
        type=int,
        default=MIN_MAJOR_FINGERPRINT_QUESTIONS,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_quality_gate(
        args.train_jsonl.resolve(),
        args.independent_audit.resolve(),
        args.fingerprint_census.resolve(),
        args.calibration_scores.resolve(),
        args.output.resolve(),
        minimum_pairs=args.minimum_pairs,
        minimum_major_fingerprint_questions=args.minimum_major_fingerprint_questions,
    )
    print(json.dumps({"output": str(args.output), "status": payload["status"], "reason_codes": payload["reason_codes"]}))


if __name__ == "__main__":
    main()
