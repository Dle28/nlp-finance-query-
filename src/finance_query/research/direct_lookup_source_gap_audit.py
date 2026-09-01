"""Explain why strict source checks cannot repair a missing direct-lookup route.

This is a research-only audit over the *current* E2E triage.  It never reads
or emits a selected financial value.  Its purpose is to distinguish a genuine
source gap from a question that needs a different operation, and to preserve
the exact gate that kept an apparently plausible table from entering E2E.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.full_corpus_direct_lookup_adapter import (
    _candidate as _full_corpus_candidate,
    validate_full_corpus_direct_lookup_adapter,
)
from finance_query.research.machine_direct_lookup_route_materialization import (
    _candidate_from_diagnostic,
    _simple_plan,
    validate_machine_direct_lookup_route_materialization,
)
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_direct_lookup_source_gap_audit_v1"
TARGET_BLOCKER = "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "rows",
        "raw_source_row",
        "raw_source_cell",
        "source_label",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not an integer") from exc


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _manifest_output_path(artifact_dir: Path, manifest: Mapping[str, Any], key: str) -> Path:
    descriptor = (manifest.get("outputs") or {}).get(key) or {}
    path = artifact_dir / str(descriptor.get("path") or "")
    if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
        raise ValueError(f"invalid {key} output in {artifact_dir}")
    return path


def _verify_artifact_linkage(
    *,
    adapter_dir: Path,
    adapter_manifest: Mapping[str, Any],
    materialization_dir: Path,
    materialization_manifest: Mapping[str, Any],
) -> None:
    adapter_outputs = adapter_manifest.get("outputs") or {}
    materialization_inputs = materialization_manifest.get("inputs") or {}
    for output_key, input_key in (("table_diagnostics", "table_diagnostics"), ("row_candidates", "row_candidates")):
        adapter_hash = ((adapter_outputs.get(output_key) or {}).get("sha256"))
        materialized_hash = ((materialization_inputs.get(input_key) or {}).get("sha256"))
        if not adapter_hash or adapter_hash != materialized_hash:
            raise ValueError("materialization is not linked to the strict full-corpus adapter")
    if not adapter_dir.is_dir() or not materialization_dir.is_dir():
        raise ValueError("source audit artifacts are missing")


def _gate_counts(
    *,
    question_id: int,
    rows: Iterable[Mapping[str, Any]],
    plan: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    minimum_row_jaccard: float,
) -> tuple[int, int, dict[str, int]]:
    review_count, accepted_count = 0, 0
    failed = Counter()
    for row in rows:
        review_count += 1
        table_uid = str(row.get("internal_table_uid") or "")
        candidate, checks = _full_corpus_candidate(
            row=row,
            plan=plan,
            table=tables.get(table_uid),
            context=contexts.get(table_uid),
            minimum_row_jaccard=minimum_row_jaccard,
        )
        if candidate is not None:
            accepted_count += 1
        else:
            failed.update(key for key, passed in checks.items() if passed is False)
    return review_count, accepted_count, dict(sorted(failed.items()))


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Direct-lookup source-gap audit",
        "",
        "This report is value-blind and research-only. It does not authorize evidence, answers, or submission.",
        "",
        f"- Current missing-route questions: {summary['target_question_count']}",
        f"- Single-cell direct lookups checked against the full corpus: {summary['simple_direct_lookup_count']}",
        f"- Questions needing another operation or more than one operand: {summary['non_simple_question_count']}",
        "",
        "## Quarantine outcomes",
        "",
    ]
    for status, count in summary["status_counts"].items():
        lines.append(f"- `{status}`: {count}")
    lines.extend(
        [
            "",
            "A strict source candidate is still rejected when the best row cannot be separated from the next plausible row by the configured margin. That is an intentional ambiguity quarantine, not a missing formula.",
            "",
        ]
    )
    return "\n".join(lines)


def build_direct_lookup_source_gap_audit(
    *,
    triage_path: Path,
    plans_path: Path,
    base_route_overlay_path: Path,
    row_review_queue_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    full_adapter_artifact_dir: Path,
    materialization_artifact_dir: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Produce one source-backed quarantine record per current missing route."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    validate_full_corpus_direct_lookup_adapter(full_adapter_artifact_dir, expected_question_count=expected_question_count)
    validate_machine_direct_lookup_route_materialization(materialization_artifact_dir, expected_question_count=expected_question_count)
    adapter_manifest = _read_json(full_adapter_artifact_dir / "manifest.json")
    materialization_manifest = _read_json(materialization_artifact_dir / "manifest.json")
    _verify_artifact_linkage(
        adapter_dir=full_adapter_artifact_dir,
        adapter_manifest=adapter_manifest,
        materialization_dir=materialization_artifact_dir,
        materialization_manifest=materialization_manifest,
    )
    adapter_audit_path = _manifest_output_path(full_adapter_artifact_dir, adapter_manifest, "audit")
    diagnostics_path = _manifest_output_path(full_adapter_artifact_dir, adapter_manifest, "table_diagnostics")
    row_candidates_path = _manifest_output_path(full_adapter_artifact_dir, adapter_manifest, "row_candidates")
    materialization_audit_path = _manifest_output_path(materialization_artifact_dir, materialization_manifest, "audit")
    adapter_summary = _read_json(full_adapter_artifact_dir / "full_corpus_direct_lookup_adapter_summary_v1.json")
    materialization_summary = _read_json(materialization_artifact_dir / "machine_direct_lookup_route_summary_v1.json")
    minimum_row_jaccard = float(adapter_summary.get("minimum_row_jaccard"))
    thresholds = materialization_summary.get("thresholds") or {}
    minimum_row_margin = float(thresholds.get("minimum_row_margin"))
    allow_v3_header_recovery = thresholds.get("allow_v3_header_recovery") is True

    triage = _read_jsonl(triage_path)
    triage_ids = {_int(row.get("question_id"), label="triage question id") for row in triage}
    expected_ids = set(range(1, expected_question_count + 1))
    if triage_ids != expected_ids:
        raise ValueError("current triage must cover every question")
    target_ids = sorted(
        _int(row.get("question_id"), label="triage question id")
        for row in triage
        if row.get("primary_blocker") == TARGET_BLOCKER
    )
    plans = {_int(row.get("question_id"), label="plan question id"): row for row in _read_jsonl(plans_path)}
    routes = {_int(row.get("question_id"), label="route question id"): row for row in _read_jsonl(base_route_overlay_path)}
    if not set(target_ids) <= set(plans) or not set(target_ids) <= set(routes):
        raise ValueError("plans or route overlay missing a current target question")
    review_rows_by_question: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(row_review_queue_path):
        review_rows_by_question[_int(row.get("question_id"), label="review question id")].append(row)
    tables = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(evidence_context_path)}
    adapter_audit = {_int(row.get("question_id"), label="adapter question id"): row for row in _read_jsonl(adapter_audit_path)}
    diagnostics_by_question: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    diagnostics_by_id: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(diagnostics_path):
        diagnostic_id = str(row.get("diagnostic_id") or "")
        if not diagnostic_id or diagnostic_id in diagnostics_by_id:
            raise ValueError("strict source diagnostics require unique IDs")
        diagnostics_by_id[diagnostic_id] = row
        diagnostics_by_question[_int(row.get("question_id"), label="diagnostic question id")].append(row)
    rows_by_diagnostic: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(row_candidates_path):
        diagnostic_id = str(row.get("diagnostic_id") or "")
        if diagnostic_id in diagnostics_by_id:
            rows_by_diagnostic[diagnostic_id].append(row)
    materialization_audit = {
        _int(row.get("question_id"), label="materialization question id"): row
        for row in _read_jsonl(materialization_audit_path)
    }

    audit: list[dict[str, Any]] = []
    for question_id in target_ids:
        plan, route = plans[question_id], routes[question_id]
        simple = _simple_plan(plan, route)
        row: dict[str, Any] = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "audit_scope": "SINGLE_CELL_DIRECT_LOOKUP" if simple else "OTHER_OPERATION_OR_OPERAND_STRUCTURE",
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if not simple:
            row.update(
                {
                    "quarantine_status": "NOT_SINGLE_CELL_DIRECT_LOOKUP",
                    "reason_code": "QUESTION_REQUIRES_DIFFERENT_OPERATION_OR_OPERAND_STRUCTURE",
                    "question_family": plan.get("effective_family"),
                    "required_operations": list(route.get("missing_operations") or []),
                }
            )
        else:
            if question_id not in adapter_audit:
                raise ValueError("strict adapter lacks a direct-lookup target from the current triage")
            review_count, strict_count, failed_gate_counts = _gate_counts(
                question_id=question_id,
                rows=review_rows_by_question.get(question_id, []),
                plan=plan,
                tables=tables,
                contexts=contexts,
                minimum_row_jaccard=minimum_row_jaccard,
            )
            adapter_count = _int(adapter_audit[question_id].get("accepted_candidate_count"), label="adapter accepted count")
            if strict_count != adapter_count:
                raise ValueError("current source-gate replay disagrees with the immutable full-corpus adapter")
            row.update(
                {
                    "review_row_candidate_count": review_count,
                    "strict_table_candidate_count": strict_count,
                    "failed_source_gate_counts": failed_gate_counts,
                }
            )
            if review_count == 0:
                row.update(
                    {
                        "quarantine_status": "NO_FULL_CORPUS_REVIEW_ROW",
                        "reason_code": "NO_CANDIDATE_ROW_IN_VALUE_BLIND_FULL_CORPUS_RETRIEVAL",
                    }
                )
            elif strict_count == 0:
                row.update(
                    {
                        "quarantine_status": "STRICT_SOURCE_GATES_INCOMPLETE",
                        "reason_code": "NO_UNIQUE_V2_V3_ALIGNED_SOURCE_CELL_CANDIDATE",
                    }
                )
            elif strict_count > 1:
                row.update(
                    {
                        "quarantine_status": "MULTIPLE_STRICT_SOURCE_CANDIDATES",
                        "reason_code": "MORE_THAN_ONE_FULL_CORPUS_SOURCE_PASSED_ALL_FIRST_STAGE_GATES",
                    }
                )
            else:
                diagnostic = diagnostics_by_question[question_id]
                if len(diagnostic) != 1:
                    raise ValueError("unique strict source candidate lacks one diagnostic")
                candidate, second_stage_checks = _candidate_from_diagnostic(
                    diagnostic=diagnostic[0],
                    rows=rows_by_diagnostic[str(diagnostic[0]["diagnostic_id"])],
                    table=tables.get(str(diagnostic[0].get("internal_table_uid") or "")),
                    context=contexts.get(str(diagnostic[0].get("internal_table_uid") or "")),
                    minimum_row_jaccard=minimum_row_jaccard,
                    minimum_row_margin=minimum_row_margin,
                    allow_v3_header_recovery=allow_v3_header_recovery,
                )
                materialized = materialization_audit.get(question_id) or {}
                materialized_status = materialized.get("materialization_status")
                row["second_stage_failed_gate_counts"] = dict(
                    sorted(Counter(key for key, passed in second_stage_checks.items() if passed is False).items())
                )
                if candidate is None and second_stage_checks.get("row_label_margin_threshold") is False:
                    row.update(
                        {
                            "quarantine_status": "STRICT_SOURCE_ROW_MARGIN_AMBIGUITY",
                            "reason_code": "TOP_ROW_CANNOT_BE_SEPARATED_FROM_NEXT_PLAUSIBLE_ROW",
                        }
                    )
                elif candidate is None:
                    row.update(
                        {
                            "quarantine_status": "STRICT_SOURCE_SECOND_STAGE_RECHECK_FAILED",
                            "reason_code": "SOURCE_CANDIDATE_FAILED_CURRENT_V2_V3_OR_ROW_RECHECK",
                        }
                    )
                elif materialized_status == "MATERIALIZED_DIRECT_LOOKUP_EXECUTION_CANDIDATE":
                    row.update(
                        {
                            "quarantine_status": "MATERIALIZED_IN_PREVIOUS_E2E_REVISION",
                            "reason_code": "CURRENT_TRIAGE_SHOULD_BE_REFRESHED_BEFORE_FURTHER_REPAIR",
                        }
                    )
                else:
                    raise ValueError("strict source candidate should have been materialized by the linked recheck")
        if _contains_forbidden(row) or "human_verified" in row:
            raise ValueError("source-gap audit contains a value or approval field")
        audit.append(row)

    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(audit),
        "simple_direct_lookup_count": sum(row["audit_scope"] == "SINGLE_CELL_DIRECT_LOOKUP" for row in audit),
        "non_simple_question_count": sum(row["audit_scope"] != "SINGLE_CELL_DIRECT_LOOKUP" for row in audit),
        "status_counts": dict(sorted(Counter(str(row["quarantine_status"]) for row in audit).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "question_source_gap_v1.jsonl"
        summary_path = temporary / "direct_lookup_source_gap_summary_v1.json"
        report_path = temporary / "direct_lookup_source_gap_report_v1.md"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        report_path.write_text(_report(summary), encoding="utf-8")
        inputs = {
            "current_triage": triage_path,
            "plans": plans_path,
            "base_route_overlay": base_route_overlay_path,
            "row_review_queue": row_review_queue_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "full_adapter_manifest": full_adapter_artifact_dir / "manifest.json",
            "materialization_manifest": materialization_artifact_dir / "manifest.json",
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                    "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                    "report": {"path": report_path.name, "sha256": sha256_file(report_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_direct_lookup_source_gap_audit(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Validate full target coverage, hashes, and the value-blind boundary."""
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected source-gap audit protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("source-gap audit hash mismatch")
    triage = _read_jsonl(Path(str(((manifest.get("inputs") or {}).get("current_triage") or {}).get("path") or "")))
    target_ids = {
        _int(row.get("question_id"), label="triage question id")
        for row in triage
        if row.get("primary_blocker") == TARGET_BLOCKER
    }
    if {_int(row.get("question_id"), label="triage question id") for row in triage} != set(range(1, expected_question_count + 1)):
        raise ValueError("source-gap audit triage coverage mismatch")
    audit = _read_jsonl(artifact_dir / "question_source_gap_v1.jsonl")
    if {row.get("question_id") for row in audit} != target_ids or len(audit) != len(target_ids):
        raise ValueError("source-gap audit does not cover each current missing route once")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        or "human_verified" in row
        for row in audit
    ):
        raise ValueError("source-gap audit lost its non-authorizing boundary")
    summary = _read_json(artifact_dir / "direct_lookup_source_gap_summary_v1.json")
    status_counts = dict(sorted(Counter(str(row.get("quarantine_status")) for row in audit).items()))
    if summary.get("target_question_count") != len(audit) or summary.get("status_counts") != status_counts:
        raise ValueError("source-gap audit summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "target_question_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
