"""Measure source completeness for questions whose operation graph is missing.

The E2E triage label ``COMPOSITION_GRAPH_NOT_MATERIALIZED`` combines several
different situations: some questions need several independently grounded
operands, while others use a non-lookup operator over one or more periods.
This audit tests every required operand against the same strict, value-blind
V2/V3 source gates used for direct lookup repair.  It is diagnostic only and
cannot create a route, evidence binding, answer, or submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.full_corpus_candidate_retrieval import validate_candidate_artifact
from finance_query.research.full_corpus_direct_lookup_adapter import _candidate as _full_corpus_candidate
from finance_query.research.machine_direct_lookup_route_materialization import _candidate_from_diagnostic
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_composition_operand_source_gap_audit_v1"
TARGET_BLOCKER = "COMPOSITION_GRAPH_NOT_MATERIALIZED"
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
        "human_verified",
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


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _subplan(*, question_id: int, operand: Mapping[str, Any]) -> dict[str, Any] | None:
    """Make a one-operand lookup-shaped plan solely for strict source checks."""
    years = operand.get("years") or []
    ticker = str(operand.get("ticker") or "").strip().upper()
    entity = str(operand.get("entity") or ticker).strip()
    if len(years) != 1 or not ticker or not entity:
        return None
    try:
        year = _int(years[0], label="operand year")
    except ValueError:
        return None
    return {
        "question_id": question_id,
        "decomposition_status": "complete",
        "effective_family": "direct_lookup",
        "operation_ast": {"op": "lookup", "args": [str(operand.get("operand_id") or "")]},
        "operands": [dict(operand)],
        "entities": [entity],
        "years": [year],
    }


def _diagnostic_from_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a first-stage source candidate into a no-value recheck record."""
    return {
        "diagnostic_id": _sha_json(
            {
                "question_id": candidate["question_id"],
                "table": candidate["internal_table_uid"],
                "row": candidate["row_index"],
                "column": candidate["column_index"],
            }
        ),
        "question_id": candidate["question_id"],
        "document_id": candidate["document_id"],
        "internal_table_uid": candidate["internal_table_uid"],
        "requested_year": candidate["requested_year"],
        "exact_table_locator": candidate["exact_table_locator"],
        "exact_table_locator_sha256": candidate["exact_table_locator_sha256"],
        "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
        "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
        "scope_status": candidate["scope_status"],
        "source_unit_candidate": candidate["source_unit_candidate"],
        "matching_year_column_indices": [candidate["column_index"]],
        "column_headers": [{"column_index": candidate["column_index"], "header_sha256": candidate["header_sha256"]}],
    }


def _safe_navigation_descriptor(
    *,
    candidate: Mapping[str, Any],
    source_row: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Describe a strict candidate without exposing source content or evidence.

    The descriptor is solely for diagnosing whether a multi-candidate result
    arises from a scope choice or duplicated navigation path.  It cannot be
    replayed into a source locator, row/column coordinate, or cell value.
    """
    identity = {
        "document_id": str(candidate.get("document_id") or ""),
        "internal_table_uid": str(candidate.get("internal_table_uid") or ""),
        "row_index": _int(candidate.get("row_index"), label="candidate row index"),
        "column_index": _int(candidate.get("column_index"), label="candidate column index"),
    }
    document_id = identity["document_id"]
    return {
        "navigation_candidate_sha256": _sha_json(identity),
        "document_identity_sha256": hashlib.sha256(document_id.encode("utf-8")).hexdigest(),
        "observed_scope": source_row.get("observed_scope"),
        "table_function": str((((context or {}).get("table_function") or {}).get("kind")) or ""),
        "period_resolution": "literal_header",
    }


def _row_candidates(rows: Iterable[Mapping[str, Any]], *, table_uid: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for raw in rows:
        if str(raw.get("internal_table_uid") or "") != table_uid:
            continue
        row_index = _int(raw.get("row_index"), label="review row index")
        row_rank = _int(raw.get("row_rank"), label="review row rank")
        key = (row_index, row_rank)
        if key in seen:
            continue
        seen.add(key)
        label = str(raw.get("row_label") or "")
        selected.append(
            {
                "row_index": row_index,
                "row_rank": row_rank,
                "row_label": label,
                "row_label_sha256": hashlib.sha256(label.encode("utf-8")).hexdigest(),
                "row_label_token_jaccard": float(raw.get("row_token_jaccard") or 0.0),
                "numeric_column_indices": list(raw.get("numeric_cell_indices") or []),
            }
        )
    return selected


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Composition-operand source audit",
        "",
        "This report is value-blind and research-only. A complete source set is only a prerequisite for building an operation graph; it authorizes neither evidence nor an answer.",
        "",
        f"- Questions with a missing composition graph: {summary['question_count']}",
        f"- Required operands checked: {summary['operand_count']}",
        f"- Questions with a unique strict source candidate for every operand: {summary['all_operands_unique_count']}",
        "",
        "## Question outcomes",
        "",
    ]
    for status, count in summary["question_status_counts"].items():
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "## Operand outcomes", ""])
    for status, count in summary["operand_status_counts"].items():
        lines.append(f"- `{status}`: {count}")
    lines.append("")
    return "\n".join(lines)


def build_composition_operand_source_gap_audit(
    *,
    triage_path: Path,
    target_question_ids_path: Path | None = None,
    plans_path: Path,
    row_review_queue_path: Path,
    full_corpus_artifact_dir: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
    minimum_row_margin: float = 0.2,
) -> dict[str, Any]:
    """Audit strict source readiness of each operand in current graph blockers."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("source thresholds must be between zero and one")
    validate_candidate_artifact(full_corpus_artifact_dir, expected_question_count=expected_question_count)
    full_manifest = _read_json(full_corpus_artifact_dir / "manifest.json")
    review_descriptor = (full_manifest.get("outputs") or {}).get("row_review_queue_v1.jsonl") or {}
    if sha256_file(row_review_queue_path) != review_descriptor.get("sha256"):
        raise ValueError("row review queue does not match validated full-corpus retrieval")
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")

    triage = _read_jsonl(triage_path)
    expected_ids = set(range(1, expected_question_count + 1))
    if {_int(row.get("question_id"), label="triage question id") for row in triage} != expected_ids:
        raise ValueError("current triage must cover every question")
    if target_question_ids_path is None:
        target_ids = sorted(
            _int(row.get("question_id"), label="triage question id")
            for row in triage
            if row.get("primary_blocker") == TARGET_BLOCKER
        )
    else:
        target_ids = sorted(
            {
                _int(row.get("question_id"), label="target question id")
                for row in _read_jsonl(target_question_ids_path)
            }
        )
        if not set(target_ids) <= expected_ids:
            raise ValueError("explicit source-audit targets are outside the question population")
    plans = {_int(row.get("question_id"), label="plan question id"): row for row in _read_jsonl(plans_path)}
    if not set(target_ids) <= set(plans):
        raise ValueError("typed plans are missing a graph-blocked question")
    raw_rows_by_operand: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(row_review_queue_path):
        raw_rows_by_operand[(_int(row.get("question_id"), label="review question id"), str(row.get("operand_id") or ""))].append(row)
    tables = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(evidence_context_path)}

    operand_audit: list[dict[str, Any]] = []
    question_audit: list[dict[str, Any]] = []
    for question_id in target_ids:
        plan = plans[question_id]
        required = [operand for operand in plan.get("operands") or [] if operand.get("required") is True]
        question_operands: list[dict[str, Any]] = []
        for operand in required:
            operand_id = str(operand.get("operand_id") or "")
            source_rows = raw_rows_by_operand.get((question_id, operand_id), [])
            row: dict[str, Any] = {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "operand_id": operand_id,
                "ticker": str(operand.get("ticker") or "").upper(),
                "requested_years": list(operand.get("years") or []),
                "raw_numeric_values_included": False,
                "source_contract": dict(CONTRACT),
            }
            subplan = _subplan(question_id=question_id, operand=operand)
            if subplan is None:
                row.update(
                    {
                        "operand_status": "OPERAND_PLAN_NOT_SINGLE_ENTITY_AND_YEAR",
                        "reason_code": "STRICT_SOURCE_CHECK_REQUIRES_ONE_TICKER_AND_ONE_YEAR_PER_OPERAND",
                    }
                )
            elif not source_rows:
                row.update(
                    {
                        "operand_status": "NO_FULL_CORPUS_REVIEW_ROW",
                        "reason_code": "NO_CANDIDATE_ROW_IN_VALUE_BLIND_FULL_CORPUS_RETRIEVAL",
                        "review_row_candidate_count": 0,
                        "strict_source_candidate_count": 0,
                        "failed_source_gate_counts": {},
                    }
                )
            else:
                accepted: dict[tuple[str, int, int], tuple[dict[str, Any], Mapping[str, Any], dict[str, Any]]] = {}
                failed_gates = Counter()
                for source_row in source_rows:
                    table_uid = str(source_row.get("internal_table_uid") or "")
                    candidate, checks = _full_corpus_candidate(
                        row=source_row,
                        plan=subplan,
                        table=tables.get(table_uid),
                        context=contexts.get(table_uid),
                        minimum_row_jaccard=minimum_row_jaccard,
                    )
                    if candidate is None:
                        failed_gates.update(key for key, passed in checks.items() if passed is False)
                    else:
                        key = (
                            str(candidate["internal_table_uid"]),
                            _int(candidate["row_index"], label="candidate row index"),
                            _int(candidate["column_index"], label="candidate column index"),
                        )
                        accepted[key] = (
                            candidate,
                            source_row,
                            _safe_navigation_descriptor(
                                candidate=candidate,
                                source_row=source_row,
                                context=contexts.get(table_uid),
                            ),
                        )
                row.update(
                    {
                        "review_row_candidate_count": len(source_rows),
                        "strict_source_candidate_count": len(accepted),
                        "navigation_candidates": [accepted[key][2] for key in sorted(accepted)],
                        "failed_source_gate_counts": dict(sorted(failed_gates.items())),
                    }
                )
                if not accepted:
                    row.update(
                        {
                            "operand_status": "STRICT_SOURCE_GATES_INCOMPLETE",
                            "reason_code": "NO_UNIQUE_V2_V3_ALIGNED_SOURCE_CELL_CANDIDATE",
                        }
                    )
                elif len(accepted) > 1:
                    row.update(
                        {
                            "operand_status": "MULTIPLE_STRICT_SOURCE_CANDIDATES",
                            "reason_code": "MORE_THAN_ONE_FULL_CORPUS_SOURCE_PASSED_ALL_FIRST_STAGE_GATES",
                        }
                    )
                else:
                    candidate, source_row, _ = next(iter(accepted.values()))
                    diagnostic = _diagnostic_from_candidate(candidate)
                    final_candidate, second_checks = _candidate_from_diagnostic(
                        diagnostic=diagnostic,
                        rows=_row_candidates(source_rows, table_uid=str(candidate["internal_table_uid"])),
                        table=tables.get(str(candidate["internal_table_uid"])),
                        context=contexts.get(str(candidate["internal_table_uid"])),
                        minimum_row_jaccard=minimum_row_jaccard,
                        minimum_row_margin=minimum_row_margin,
                        allow_v3_header_recovery=False,
                    )
                    row["second_stage_failed_gate_counts"] = dict(
                        sorted(Counter(key for key, passed in second_checks.items() if passed is False).items())
                    )
                    if final_candidate is None and second_checks.get("row_label_margin_threshold") is False:
                        row.update(
                            {
                                "operand_status": "STRICT_SOURCE_ROW_MARGIN_AMBIGUITY",
                                "reason_code": "TOP_ROW_CANNOT_BE_SEPARATED_FROM_NEXT_PLAUSIBLE_ROW",
                            }
                        )
                    elif final_candidate is None:
                        row.update(
                            {
                                "operand_status": "STRICT_SOURCE_SECOND_STAGE_RECHECK_FAILED",
                                "reason_code": "SOURCE_CANDIDATE_FAILED_CURRENT_V2_V3_OR_ROW_RECHECK",
                            }
                        )
                    else:
                        row.update(
                            {
                                "operand_status": "UNIQUE_STRICT_SOURCE_ROW_RESOLVED",
                                "reason_code": "SOURCE_READY_FOR_FUTURE_NON_AUTHORIZING_GRAPH_RESEARCH",
                            }
                        )
            if _contains_forbidden(row) or "human_verified" in row:
                raise ValueError("operand source audit contains a value or approval field")
            operand_audit.append(row)
            question_operands.append(row)
        unique_count = sum(row["operand_status"] == "UNIQUE_STRICT_SOURCE_ROW_RESOLVED" for row in question_operands)
        operation = str((plan.get("operation_ast") or {}).get("op") or "")
        question_row: dict[str, Any] = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "operation": operation,
            "question_family": plan.get("effective_family"),
            "required_operand_count": len(question_operands),
            "unique_strict_operand_count": unique_count,
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if not question_operands:
            question_row.update(
                {
                    "question_status": "NO_REQUIRED_OPERANDS_IN_TYPED_PLAN",
                    "reason_code": "GRAPH_CANNOT_BE_RESEARCHED_WITHOUT_REQUIRED_OPERANDS",
                }
            )
        elif unique_count == len(question_operands):
            question_row.update(
                {
                    "question_status": "ALL_OPERANDS_HAVE_UNIQUE_STRICT_SOURCE_ROWS",
                    "reason_code": "SOURCE_COMPLETE_BUT_OPERATION_GRAPH_REMAINS_UNMATERIALIZED",
                }
            )
        else:
            question_row.update(
                {
                    "question_status": "ONE_OR_MORE_OPERANDS_LACK_UNIQUE_STRICT_SOURCE_ROWS",
                    "reason_code": "SOURCE_QUARANTINE_PRECEDES_ANY_OPERATION_GRAPH_WORK",
                }
            )
        if _contains_forbidden(question_row) or "human_verified" in question_row:
            raise ValueError("question source audit contains a value or approval field")
        question_audit.append(question_row)

    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": len(question_audit),
        "operand_count": len(operand_audit),
        "all_operands_unique_count": sum(
            row["question_status"] == "ALL_OPERANDS_HAVE_UNIQUE_STRICT_SOURCE_ROWS" for row in question_audit
        ),
        "question_status_counts": dict(sorted(Counter(row["question_status"] for row in question_audit).items())),
        "operand_status_counts": dict(sorted(Counter(row["operand_status"] for row in operand_audit).items())),
        "operation_counts": dict(sorted(Counter(str(row["operation"]) for row in question_audit).items())),
        "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin},
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        operands_path = temporary / "operand_source_gap_v1.jsonl"
        questions_path = temporary / "question_composition_source_gap_v1.jsonl"
        summary_path = temporary / "composition_operand_source_gap_summary_v1.json"
        report_path = temporary / "composition_operand_source_gap_report_v1.md"
        _write_jsonl(operands_path, operand_audit)
        _write_jsonl(questions_path, question_audit)
        _write_json(summary_path, summary)
        report_path.write_text(_report(summary), encoding="utf-8")
        inputs = {
            "current_triage": triage_path,
            "plans": plans_path,
            "row_review_queue": row_review_queue_path,
            "full_corpus_manifest": full_corpus_artifact_dir / "manifest.json",
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
        }
        if target_question_ids_path is not None:
            inputs["target_question_ids"] = target_question_ids_path
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    "operand_audit": {"path": operands_path.name, "sha256": sha256_file(operands_path)},
                    "question_audit": {"path": questions_path.name, "sha256": sha256_file(questions_path)},
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


def validate_composition_operand_source_gap_audit(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Validate source-audit coverage, hashes, and its non-authorizing boundary."""
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected composition source-gap audit protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("composition source-gap audit hash mismatch")
    triage = _read_jsonl(Path(str(((manifest.get("inputs") or {}).get("current_triage") or {}).get("path") or "")))
    expected_ids = set(range(1, expected_question_count + 1))
    if {_int(row.get("question_id"), label="triage question id") for row in triage} != expected_ids:
        raise ValueError("composition source-gap triage coverage mismatch")
    target_descriptor = (manifest.get("inputs") or {}).get("target_question_ids")
    if target_descriptor is None:
        target_ids = {
            _int(row.get("question_id"), label="triage question id")
            for row in triage
            if row.get("primary_blocker") == TARGET_BLOCKER
        }
    else:
        target_path = Path(str(target_descriptor.get("path") or ""))
        target_ids = {
            _int(row.get("question_id"), label="target question id")
            for row in _read_jsonl(target_path)
        }
    questions = _read_jsonl(artifact_dir / "question_composition_source_gap_v1.jsonl")
    operands = _read_jsonl(artifact_dir / "operand_source_gap_v1.jsonl")
    if {row.get("question_id") for row in questions} != target_ids or len(questions) != len(target_ids):
        raise ValueError("composition source-gap question coverage mismatch")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        or "human_verified" in row
        for row in [*questions, *operands]
    ):
        raise ValueError("composition source-gap audit lost its non-authorizing boundary")
    summary = _read_json(artifact_dir / "composition_operand_source_gap_summary_v1.json")
    question_counts = dict(sorted(Counter(row.get("question_status") for row in questions).items()))
    operand_counts = dict(sorted(Counter(row.get("operand_status") for row in operands).items()))
    if (
        summary.get("question_count") != len(questions)
        or summary.get("operand_count") != len(operands)
        or summary.get("question_status_counts") != question_counts
        or summary.get("operand_status_counts") != operand_counts
    ):
        raise ValueError("composition source-gap summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "target_question_count": len(questions),
        "operand_count": len(operands),
        "answer_eligible": False,
        "submission_eligible": False,
    }
