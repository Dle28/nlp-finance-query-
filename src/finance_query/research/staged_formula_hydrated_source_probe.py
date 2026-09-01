"""Test whether missing V2/V3 sidecars block staged-formula operands.

Missing tables are reconstructed only in memory from immutable OCR slices and
are evaluated under the same strict source gates as the baseline.  The result
is a value-blind research diagnostic; it cannot create an E2E route, evidence,
answer, training record, promotion, or submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.composition_operand_source_gap_audit import (
    _diagnostic_from_candidate,
    _row_candidates,
    _safe_navigation_descriptor,
    _subplan,
)
from finance_query.research.full_corpus_candidate_retrieval import validate_candidate_artifact
from finance_query.research.full_corpus_direct_lookup_adapter import _candidate
from finance_query.research.hydrated_source_gate_probe import _hydrate, _require_asset_manifest
from finance_query.research.machine_direct_lookup_route_materialization import _candidate_from_diagnostic
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_staged_formula_hydrated_source_probe_v1"
CONTRACT = {
    "research_only": True,
    "in_memory_sidecar_probe_only": True,
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
        "human_verified",
        "raw_source_cell",
        "raw_source_row",
        "rows",
        "cell_provenance",
        "context_before",
        "source_label",
        "row_label",
        "exact_table_locator",
        "column_index",
        "row_index",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _strict_status(
    *,
    source_rows: Iterable[Mapping[str, Any]],
    plan: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    minimum_row_jaccard: float,
    minimum_row_margin: float,
) -> dict[str, Any]:
    """Apply first- and second-stage source gates for one one-year operand."""
    rows = list(source_rows)
    candidates: dict[tuple[str, int, int], tuple[dict[str, Any], Mapping[str, Any]]] = {}
    first_failed = Counter()
    for source_row in rows:
        table_uid = str(source_row.get("internal_table_uid") or "")
        candidate, checks = _candidate(
            row=source_row,
            plan=plan,
            table=tables.get(table_uid),
            context=contexts.get(table_uid),
            minimum_row_jaccard=minimum_row_jaccard,
        )
        if candidate is None:
            first_failed.update(name for name, passed in checks.items() if passed is False)
            continue
        key = (str(candidate["internal_table_uid"]), int(candidate["row_index"]), int(candidate["column_index"]))
        candidates[key] = (candidate, source_row)
    descriptors = [
        _safe_navigation_descriptor(
            candidate=candidate,
            source_row=source_row,
            context=contexts.get(str(candidate["internal_table_uid"])),
        )
        for _, (candidate, source_row) in sorted(candidates.items())
    ]
    result: dict[str, Any] = {
        "strict_source_candidate_count": len(candidates),
        "navigation_candidates": descriptors,
        "first_stage_failed_gate_counts": dict(sorted(first_failed.items())),
        "second_stage_failed_gate_counts": {},
    }
    if not candidates:
        return {
            **result,
            "status": "STRICT_SOURCE_GATES_INCOMPLETE",
            "reason_code": "NO_CANDIDATE_PASSED_ALL_V2_V3_SOURCE_GATES",
        }
    if len(candidates) > 1:
        return {
            **result,
            "status": "MULTIPLE_STRICT_SOURCE_CANDIDATES",
            "reason_code": "MORE_THAN_ONE_SOURCE_CELL_PASSED_FIRST_STAGE_GATES",
        }
    candidate, _ = next(iter(candidates.values()))
    table_uid = str(candidate["internal_table_uid"])
    final, checks = _candidate_from_diagnostic(
        diagnostic=_diagnostic_from_candidate(candidate),
        rows=_row_candidates(rows, table_uid=table_uid),
        table=tables.get(table_uid),
        context=contexts.get(table_uid),
        minimum_row_jaccard=minimum_row_jaccard,
        minimum_row_margin=minimum_row_margin,
        allow_v3_header_recovery=False,
    )
    result["second_stage_failed_gate_counts"] = dict(
        sorted(Counter(name for name, passed in checks.items() if passed is False).items())
    )
    if final is not None:
        return {
            **result,
            "status": "UNIQUE_STRICT_SOURCE_ROW_RESOLVED",
            "reason_code": "ONE_SOURCE_CELL_PASSED_BOTH_SOURCE_STAGES",
        }
    if checks.get("row_label_margin_threshold") is False:
        return {
            **result,
            "status": "STRICT_SOURCE_ROW_MARGIN_AMBIGUITY",
            "reason_code": "TOP_ROW_CANNOT_BE_SEPARATED_FROM_NEXT_PLAUSIBLE_ROW",
        }
    return {
        **result,
        "status": "STRICT_SOURCE_SECOND_STAGE_RECHECK_FAILED",
        "reason_code": "ONLY_FIRST_STAGE_CANDIDATE_FAILED_SECOND_STAGE_RECHECK",
    }


def build_staged_formula_hydrated_source_probe(
    *,
    plans_path: Path,
    targets_path: Path,
    candidate_artifact_dir: Path,
    full_assets_path: Path,
    full_assets_manifest_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
    minimum_row_margin: float = 0.2,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("source thresholds must be between zero and one")
    validate_candidate_artifact(candidate_artifact_dir, expected_question_count=expected_question_count)
    candidate_manifest = _read_json(candidate_artifact_dir / "manifest.json")
    queue_path = candidate_artifact_dir / "row_review_queue_v1.jsonl"
    queue_descriptor = (candidate_manifest.get("outputs") or {}).get(queue_path.name) or {}
    if sha256_file(queue_path) != queue_descriptor.get("sha256"):
        raise ValueError("full-corpus row queue does not match its manifest")
    _require_asset_manifest(full_assets_path, full_assets_manifest_path)
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    targets = {int(row["question_id"]) for row in _read_jsonl(targets_path)}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(plans) != expected_ids or not targets <= expected_ids:
        raise ValueError("plans or targets do not match the expected population")
    if any(plans[question_id].get("decomposition_status") != "typed_non_executable" for question_id in targets):
        raise ValueError("hydrated staged-formula targets must be typed staged plans")

    rows_by_operand: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    requested_uids: set[str] = set()
    for row in _read_jsonl(queue_path):
        question_id = int(row.get("question_id") or 0)
        if question_id not in targets:
            continue
        operand_id = str(row.get("operand_id") or "")
        rows_by_operand[(question_id, operand_id)].append(row)
        if table_uid := str(row.get("internal_table_uid") or ""):
            requested_uids.add(table_uid)
    current_tables = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(structured_tables_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    current_contexts = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(evidence_context_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    current_complete_ids = set(current_tables) & set(current_contexts)
    hydrate_ids = requested_uids - current_complete_ids
    assets = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(full_assets_path)
        if str(row.get("internal_table_uid") or "") in hydrate_ids
    }
    if hydrate_ids - set(assets):
        raise ValueError("a requested full-corpus table is missing from immutable assets")
    source_cache: dict[Path, tuple[str, str]] = {}
    hydrated_tables: dict[str, dict[str, Any]] = {}
    hydrated_contexts: dict[str, dict[str, Any]] = {}
    hydration_failures = Counter()
    for table_uid in sorted(hydrate_ids):
        try:
            table, context = _hydrate(assets[table_uid], source_cache=source_cache)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            hydration_failures[str(error)] += 1
            continue
        hydrated_tables[table_uid] = table
        hydrated_contexts[table_uid] = context
    merged_tables = {**current_tables, **hydrated_tables}
    merged_contexts = {**current_contexts, **hydrated_contexts}

    audit: list[dict[str, Any]] = []
    for question_id in sorted(targets):
        for operand in plans[question_id].get("operands") or []:
            if operand.get("required") is not True:
                continue
            operand_id = str(operand.get("operand_id") or "")
            subplan = _subplan(question_id=question_id, operand=operand)
            if subplan is None:
                baseline = hydrated = {
                    "status": "OPERAND_PLAN_NOT_SINGLE_ENTITY_AND_YEAR",
                    "reason_code": "STRICT_SOURCE_CHECK_REQUIRES_ONE_TICKER_AND_ONE_YEAR_PER_OPERAND",
                    "strict_source_candidate_count": 0,
                    "navigation_candidates": [],
                    "first_stage_failed_gate_counts": {},
                    "second_stage_failed_gate_counts": {},
                }
            else:
                source_rows = rows_by_operand[(question_id, operand_id)]
                baseline = _strict_status(
                    source_rows=source_rows,
                    plan=subplan,
                    tables=current_tables,
                    contexts=current_contexts,
                    minimum_row_jaccard=minimum_row_jaccard,
                    minimum_row_margin=minimum_row_margin,
                )
                hydrated = _strict_status(
                    source_rows=source_rows,
                    plan=subplan,
                    tables=merged_tables,
                    contexts=merged_contexts,
                    minimum_row_jaccard=minimum_row_jaccard,
                    minimum_row_margin=minimum_row_margin,
                )
            record = {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "operand_id": operand_id,
                "baseline": baseline,
                "hydrated": hydrated,
                "improved_to_unique": (
                    baseline["status"] != "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"
                    and hydrated["status"] == "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"
                ),
                "raw_numeric_values_included": False,
                "source_contract": dict(CONTRACT),
            }
            if _contains_forbidden(record):
                raise ValueError("hydrated staged-formula probe leaked a forbidden field")
            audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(targets),
        "operand_count": len(audit),
        "requested_table_count": len(requested_uids),
        "current_v2_v3_table_count": len(current_complete_ids),
        "hydrated_table_count": len(hydrated_tables),
        "hydration_failure_counts": dict(sorted(hydration_failures.items())),
        "baseline_status_counts": dict(sorted(Counter(row["baseline"]["status"] for row in audit).items())),
        "hydrated_status_counts": dict(sorted(Counter(row["hydrated"]["status"] for row in audit).items())),
        "improved_to_unique_count": sum(bool(row["improved_to_unique"]) for row in audit),
        "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin},
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "staged_formula_hydrated_operand_probe_v1.jsonl"
        summary_path = temporary / "staged_formula_hydrated_source_probe_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "plans": plans_path,
            "targets": targets_path,
            "candidate_manifest": candidate_artifact_dir / "manifest.json",
            "row_review_queue": queue_path,
            "full_assets": full_assets_path,
            "full_assets_manifest": full_assets_manifest_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
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
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_staged_formula_hydrated_source_probe(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected staged-formula hydrated source probe contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("staged-formula hydrated source probe hash mismatch")
    targets_path = Path(str(((manifest.get("inputs") or {}).get("targets") or {}).get("path") or ""))
    targets = {int(row["question_id"]) for row in _read_jsonl(targets_path)}
    if not targets <= set(range(1, expected_question_count + 1)):
        raise ValueError("hydrated staged-formula targets are outside the expected population")
    audit = _read_jsonl(artifact_dir / "staged_formula_hydrated_operand_probe_v1.jsonl")
    if {int(row["question_id"]) for row in audit} != targets:
        raise ValueError("hydrated staged-formula probe target coverage mismatch")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        for row in audit
    ):
        raise ValueError("hydrated staged-formula probe lost its non-authorizing boundary")
    summary = _read_json(artifact_dir / "staged_formula_hydrated_source_probe_summary_v1.json")
    baseline_counts = dict(sorted(Counter(row["baseline"]["status"] for row in audit).items()))
    hydrated_counts = dict(sorted(Counter(row["hydrated"]["status"] for row in audit).items()))
    if (
        summary.get("operand_count") != len(audit)
        or summary.get("baseline_status_counts") != baseline_counts
        or summary.get("hydrated_status_counts") != hydrated_counts
        or summary.get("improved_to_unique_count") != sum(bool(row["improved_to_unique"]) for row in audit)
    ):
        raise ValueError("hydrated staged-formula probe summary mismatch")
    return {
        "status": "PASS",
        "target_question_count": len(targets),
        "operand_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
