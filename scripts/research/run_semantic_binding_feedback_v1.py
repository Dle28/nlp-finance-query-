#!/usr/bin/env python3
"""Run a value-free semantic binding feedback audit on a full population.

The audit is deliberately separate from answer selection.  It consumes the
source-bound union candidate and the frozen control, reports reusable
metric/row and unit-binding signals, and writes no numeric answer, raw cell,
gold answer, model output, or research answer into the feedback JSONL.
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from zipfile import ZipFile

from finance_query.research.semantic_binding_feedback import (
    SEMANTIC_BINDING_FEEDBACK_PROTOCOL,
    SEMANTIC_BINDING_FEEDBACK_SCHEMA_VERSION,
    build_feedback_record,
)
from finance_query.research.source_bound_plan_union import (
    SourceBoundPlanUnionError,
    canonical_sha256,
    load_jsonl,
    load_review_items,
    load_tables_for_uids,
    sha256_file,
)


def _path(value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty path")
    return Path(value).expanduser().resolve()


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _sign(value: object) -> int | None:
    result = _decimal(value)
    if result is None:
        return None
    return (result > 0) - (result < 0)


def _same_numeric(left: object, right: object) -> bool:
    left_decimal = _decimal(left)
    right_decimal = _decimal(right)
    if left_decimal is None or right_decimal is None:
        return False
    return float(left_decimal) == float(right_decimal)


def _index(rows: Sequence[Mapping[str, Any]], key: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = _int(row.get(key))
        if question_id is None or question_id in indexed:
            raise SourceBoundPlanUnionError(f"rows must contain unique integer {key}")
        indexed[question_id] = dict(row)
    return indexed


def _load_submission(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SourceBoundPlanUnionError("submission must be a list of objects")
    return value


def _collect_uids(
    selected_rows: Mapping[int, Mapping[str, Any]],
    control_rows: Mapping[int, Mapping[str, Any]],
) -> set[str]:
    uids: set[str] = set()
    for row in [*selected_rows.values(), *control_rows.values()]:
        evidence = row.get("evidence")
        if isinstance(evidence, (list, tuple)):
            for item in evidence:
                if isinstance(item, Mapping):
                    uid = str(
                        item.get("internal_table_uid")
                        or item.get("table_uid")
                        or item.get("source_uid")
                        or ""
                    ).strip()
                    if uid:
                        uids.add(uid)
        plan = row.get("selected_plan")
        if isinstance(plan, Mapping):
            for item in plan.get("selection_evidence") or []:
                if isinstance(item, Mapping):
                    uid = str(
                        item.get("internal_table_uid")
                        or item.get("table_uid")
                        or item.get("source_uid")
                        or ""
                    ).strip()
                    if uid:
                        uids.add(uid)
            for operand in plan.get("operands") or []:
                if not isinstance(operand, Mapping):
                    continue
                source = operand.get("source")
                source = source if isinstance(source, Mapping) else operand
                uid = str(
                    source.get("source_uid")
                    or source.get("table_uid")
                    or source.get("internal_table_uid")
                    or ""
                ).strip()
                if uid:
                    uids.add(uid)
    return uids


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _zip_score_receipt(
    *,
    score_zip: Path,
    prediction_zip: Path,
    matched_submission: Path,
) -> dict[str, Any]:
    with ZipFile(score_zip) as archive:
        score_text = archive.read("scores.txt")
        score_values = json.loads(score_text.decode("utf-8"))
        if not isinstance(score_values, Mapping):
            raise SourceBoundPlanUnionError("scores.txt must contain a JSON object")
        metadata = archive.read("metadata") if "metadata" in archive.namelist() else b""
    with ZipFile(prediction_zip) as archive:
        prediction_submission = archive.read("submission.json")
    local_submission = matched_submission.read_bytes()
    prediction_hash = hashlib.sha256(prediction_submission).hexdigest()
    local_hash = hashlib.sha256(local_submission).hexdigest()
    return {
        "score_zip_path": str(score_zip),
        "score_zip_sha256": sha256_file(score_zip),
        "prediction_zip_path": str(prediction_zip),
        "prediction_zip_sha256": sha256_file(prediction_zip),
        "scores_sha256": hashlib.sha256(score_text).hexdigest(),
        "metadata_sha256": hashlib.sha256(metadata).hexdigest(),
        "prediction_submission_member_sha256": prediction_hash,
        "matched_local_submission_path": str(matched_submission),
        "matched_local_submission_sha256": local_hash,
        "submission_member_matches_local_artifact": prediction_hash == local_hash,
        "score_values": dict(score_values),
        "scorer_identity": "external_scoring_result_scores_txt",
        "scorer_command": "NOT_AVAILABLE_IN_RECEIPT",
        "scorer_exit_status": "NOT_AVAILABLE_IN_RECEIPT",
        "used_for_candidate_selection": False,
        "used_as_local_gold": False,
    }


def _aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    family: dict[str, Counter[str]] = {}
    reason_counts: Counter[str] = Counter()
    semantic_counts: Counter[str] = Counter()
    unit_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    changed_by_family: Counter[str] = Counter()
    sign_transition_by_family: Counter[str] = Counter()
    for record in records:
        family_name = str(record.get("family") or "UNKNOWN")
        bucket = family.setdefault(family_name, Counter())
        bucket["population"] += 1
        bucket[f"semantic_{record.get('semantic_status', 'UNKNOWN')}"] += 1
        bucket[f"unit_{record.get('unit_status', 'UNKNOWN')}"] += 1
        if record.get("output_changed"):
            bucket["output_changed"] += 1
            changed_by_family[family_name] += 1
        if record.get("output_sign_transition"):
            bucket["output_sign_transition"] += 1
            sign_transition_by_family[family_name] += 1
        semantic_counts[str(record.get("semantic_status") or "UNKNOWN")] += 1
        unit_counts[str(record.get("unit_status") or "UNKNOWN")] += 1
        source_counts[str(record.get("selected_source_class") or "UNKNOWN")] += 1
        reason_counts.update(str(reason) for reason in record.get("reason_codes") or [])
    return {
        "family_level": {
            family_name: dict(sorted(bucket.items()))
            for family_name, bucket in sorted(family.items())
        },
        "semantic_status_counts": dict(sorted(semantic_counts.items())),
        "unit_status_counts": dict(sorted(unit_counts.items())),
        "selected_source_class_counts": dict(sorted(source_counts.items())),
        "changed_output_by_family": dict(sorted(changed_by_family.items())),
        "sign_transition_by_family": dict(sorted(sign_transition_by_family.items())),
        "reason_counts": dict(reason_counts.most_common()),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    selected_path = _path(args.selected_ledger, name="selected-ledger")
    control_ledger_path = _path(args.control_ledger, name="control-ledger")
    control_submission_path = _path(args.control_submission, name="control-submission")
    review_path = _path(args.review_items, name="review-items")
    tables_path = _path(args.structured_tables, name="structured-tables")
    output_dir = _path(args.output_dir, name="output-dir")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")

    selected_rows = _index(load_jsonl(selected_path), "question_id")
    control_rows = _index(load_jsonl(control_ledger_path), "question_id")
    review_items = load_review_items(review_path)
    control_submission = _load_submission(control_submission_path)
    control_submission_by_id = _index(control_submission, "id")
    population = sorted(review_items)
    expected_count = args.expected_question_count
    if expected_count is not None and len(population) != expected_count:
        raise SourceBoundPlanUnionError(
            f"review population count {len(population)} != expected {expected_count}"
        )
    population_set = set(population)
    if (
        set(selected_rows) != population_set
        or set(control_rows) != population_set
        or set(control_submission_by_id) != population_set
    ):
        raise SourceBoundPlanUnionError("feedback inputs do not share one population")

    tables_by_uid = load_tables_for_uids(
        tables_path, _collect_uids(selected_rows, control_rows)
    )
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for question_id in population:
        selected = selected_rows[question_id]
        control = control_rows[question_id]
        control_submission_row = control_submission_by_id[question_id]
        selected_answer = selected.get("selected_answer_decimal")
        if selected_answer is None:
            selected_answer = selected.get("answer_decimal")
        control_answer = control_submission_row.get("answer")
        output_changed = not _same_numeric(control_answer, selected_answer)
        control_sign = _sign(control_answer)
        selected_sign = _sign(selected_answer)
        sign_transition = (
            output_changed
            and control_sign is not None
            and selected_sign is not None
            and control_sign != selected_sign
        )
        try:
            record = build_feedback_record(
                question_id=question_id,
                review_item=review_items[question_id],
                selected_row=selected,
                control_row=control,
                output_changed=output_changed,
                sign_transition=sign_transition,
                tables_by_uid=tables_by_uid,
            )
        except (TypeError, ValueError, KeyError) as error:
            errors.append(f"question_{question_id}:{type(error).__name__}")
            continue
        records.append(record)

    external_feedback: dict[str, Any] | None = None
    if args.external_score_zip and args.external_prediction_zip and args.external_matched_submission:
        external_feedback = _zip_score_receipt(
            score_zip=_path(args.external_score_zip, name="external-score-zip"),
            prediction_zip=_path(args.external_prediction_zip, name="external-prediction-zip"),
            matched_submission=_path(
                args.external_matched_submission, name="external-matched-submission"
            ),
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    feedback_path = output_dir / "semantic_binding_feedback_v1.jsonl"
    _write_jsonl(feedback_path, records)
    priority_records = [
        record
        for record in records
        if record.get("output_changed")
        or record.get("semantic_status") in {"FAIL", "REVIEW"}
        or record.get("unit_status") == "MISMATCH"
    ]
    priority_records.sort(
        key=lambda record: (
            0 if record.get("output_changed") else 1,
            0 if record.get("semantic_status") == "FAIL" else 1,
            0 if record.get("unit_status") == "MISMATCH" else 1,
            int(record.get("question_id") or 0),
        )
    )
    priority_path = output_dir / "semantic_binding_priority_queue_v1.jsonl"
    _write_jsonl(priority_path, priority_records)

    aggregate = _aggregate(records)
    errors.extend(
        [
            "selected_population_mismatch"
            if len(records) != len(population)
            else "",
        ]
    )
    errors = [error for error in errors if error]
    union_report_path = selected_path.parent / "source_bound_plan_union_ab_report_v1.json"
    union_report_hash = sha256_file(union_report_path) if union_report_path.is_file() else None
    report = {
        "schema_version": SEMANTIC_BINDING_FEEDBACK_SCHEMA_VERSION,
        "protocol": SEMANTIC_BINDING_FEEDBACK_PROTOCOL,
        "status": "COMPLETE_DIAGNOSTIC" if not errors else "FAILED",
        "hypothesis": (
            "direct_lookup_metric_row_binding_and_source_unit_consistency_are_"
            "population_level_feedback_signals"
        ),
        "control": {
            "ledger_path": str(control_ledger_path),
            "ledger_sha256": sha256_file(control_ledger_path),
            "submission_path": str(control_submission_path),
            "submission_sha256": sha256_file(control_submission_path),
            "fingerprint": canonical_sha256(
                {
                    "ledger": sha256_file(control_ledger_path),
                    "submission": sha256_file(control_submission_path),
                }
            ),
        },
        "candidate": {
            "selected_ledger_path": str(selected_path),
            "selected_ledger_sha256": sha256_file(selected_path),
            "structured_tables_path": str(tables_path),
            "structured_tables_sha256": sha256_file(tables_path),
            "review_items_path": str(review_path),
            "review_items_sha256": sha256_file(review_path),
            "union_report_path": str(union_report_path),
            "union_report_sha256": union_report_hash,
            "fingerprint": canonical_sha256(
                {
                    "protocol": SEMANTIC_BINDING_FEEDBACK_PROTOCOL,
                    "selected_ledger": sha256_file(selected_path),
                    "structured_tables": sha256_file(tables_path),
                    "review_items": sha256_file(review_path),
                }
            ),
        },
        "population": {
            "expected_question_count": expected_count,
            "question_count": len(population),
            "processed_records": len(records),
            "missing_records": len(population) - len(records),
            "errors": errors,
            "split_policy": "full_population_frozen_control_candidate_no_tuning",
        },
        "scorer_gold": {
            "identity": "NOT_AVAILABLE_FOR_THIS_CANDIDATE_AB",
            "command": "NOT_RUN",
            "exit_status": "NOT_RUN",
        },
        "ANSWER_ACCURACY": {
            "control": "NOT_MEASURED",
            "candidate": "NOT_MEASURED",
            "delta": "NOT_MEASURED",
        },
        "EXECUTION_ACCURACY": {
            "control": "NOT_MEASURED",
            "candidate": "NOT_MEASURED",
            "delta": "NOT_MEASURED",
        },
        "diagnostic_metrics": {
            "value_free_feedback_record_count": len(records),
            "priority_queue_count": len(priority_records),
            "source_table_uid_count": len(tables_by_uid),
            "answer_diff_is_not_accuracy": True,
            "replay_is_not_answer_accuracy": True,
            **aggregate,
        },
        "external_feedback_receipt": external_feedback,
        "policy": {
            "feedback_output_value_free": True,
            "gold_consumed_for_selection": False,
            "model_output_consumed_for_selection": False,
            "external_score_consumed_for_selection": False,
            "question_id_exceptions": False,
            "authority_status": "CANDIDATE_ONLY",
        },
        "decision": {
            "value": "INVESTIGATE_FURTHER",
            "reason": (
                "The audit supplies a reusable semantic row/unit feedback gate "
                "for the complete population. It does not establish correctness "
                "or authorize changing the candidate without an independent scorer."
            ),
        },
        "authority": {
            "status": "CANDIDATE_ONLY",
            "answer_authorized": False,
            "evidence_authorized": False,
            "submission_eligible": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
        "outputs": {
            "feedback": str(feedback_path),
            "priority_queue": str(priority_path),
        },
    }
    report_path = output_dir / "semantic_binding_feedback_ab_report_v1.json"
    _write_json(report_path, report)
    manifest = {
        "schema_version": 1,
        "protocol": f"{SEMANTIC_BINDING_FEEDBACK_PROTOCOL}_manifest",
        "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
        "outputs": {},
    }
    for output in (feedback_path, priority_path, report_path):
        manifest["outputs"][output.name] = {
            "path": str(output),
            "sha256": sha256_file(output),
            "size_bytes": output.stat().st_size,
        }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return {**report, "manifest_path": str(manifest_path)}


def configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--selected-ledger", required=True)
    parser.add_argument("--control-ledger", required=True)
    parser.add_argument("--control-submission", required=True)
    parser.add_argument("--review-items", required=True)
    parser.add_argument("--structured-tables", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-question-count", type=int, default=None)
    parser.add_argument("--external-score-zip", default=None)
    parser.add_argument("--external-prediction-zip", default=None)
    parser.add_argument("--external-matched-submission", default=None)
    return parser


def main() -> None:
    parser = configure_parser(argparse.ArgumentParser())
    args = parser.parse_args()
    try:
        report = run(args)
    except (FileNotFoundError, ValueError, SourceBoundPlanUnionError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
