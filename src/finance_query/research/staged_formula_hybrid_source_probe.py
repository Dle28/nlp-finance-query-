"""Compare lexical-only and lexical+dense table routes under exact source gates.

Dense retrieval is allowed to add tables for navigation.  This probe asks the
narrower question that proxy scores cannot answer: after the same V2/V3, period,
scope and row-margin checks, does the added table set create any new unique
source route?  It is value-blind and never promotes a result into E2E.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.composition_operand_source_gap_audit import _subplan
from finance_query.research.full_corpus_candidate_retrieval import (
    _metric_query,
    _row_candidates,
)
from finance_query.research.hybrid_retrieval_analysis import validate_hybrid_retrieval_analysis
from finance_query.research.machine_exact_cell_proposals import sha256_file
from finance_query.research.staged_formula_hydrated_source_probe import _strict_status


PROTOCOL = "vifinqa_staged_formula_hybrid_source_probe_v1"
CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
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
        "source_label",
        "row_label",
        "row_index",
        "column_index",
        "exact_table_locator",
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


def _source_rows(
    *,
    table_candidates: Iterable[Mapping[str, Any]],
    assets: Mapping[str, Mapping[str, Any]],
    query: str,
    maximum_row_candidates: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in table_candidates:
        table_uid = str(candidate.get("internal_table_uid") or "")
        asset = assets.get(table_uid)
        if asset is None:
            raise ValueError("hybrid candidate table is missing from immutable assets")
        for row in _row_candidates(asset, query, maximum=maximum_row_candidates):
            rows.append(
                {
                    "question_id": int(candidate["question_id"]),
                    "operand_id": str(candidate["operand_id"]),
                    "ticker": str(candidate["ticker"]),
                    "report_year": int(candidate["report_year"]),
                    "observed_scope": candidate.get("observed_scope"),
                    "internal_table_uid": table_uid,
                    **row,
                }
            )
    return rows


def build_staged_formula_hybrid_source_probe(
    *,
    plans_path: Path,
    targets_path: Path,
    hybrid_artifact_dir: Path,
    assets_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
    minimum_row_margin: float = 0.2,
    maximum_row_candidates: int = 3,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("source thresholds must be between zero and one")
    hybrid_coverage = _read_json(hybrid_artifact_dir / "coverage_report_v1.json")
    validate_hybrid_retrieval_analysis(
        hybrid_artifact_dir,
        expected_question_count=expected_question_count,
        expected_route_count=int(hybrid_coverage.get("route_count") or 0),
    )
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    targets = {int(row["question_id"]) for row in _read_jsonl(targets_path)}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(plans) != expected_ids or not targets <= expected_ids:
        raise ValueError("plans or targets do not match the expected population")
    if any(plans[question_id].get("decomposition_status") != "typed_non_executable" for question_id in targets):
        raise ValueError("hybrid source targets must be typed staged plans")
    all_candidates: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    lexical_candidates: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    requested_uids: set[str] = set()
    for candidate in _read_jsonl(hybrid_artifact_dir / "hybrid_table_candidates_v1.jsonl"):
        question_id = int(candidate.get("question_id") or 0)
        if question_id not in targets:
            continue
        key = (question_id, str(candidate.get("operand_id") or ""))
        all_candidates[key].append(candidate)
        if candidate.get("lexical_rank") is not None:
            lexical_candidates[key].append(candidate)
        requested_uids.add(str(candidate.get("internal_table_uid") or ""))
    if not requested_uids or "" in requested_uids:
        raise ValueError("hybrid candidates contain no usable table UIDs")
    assets = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(assets_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    if requested_uids - set(assets):
        raise ValueError("hybrid candidate table is absent from the immutable asset corpus")
    tables = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(structured_tables_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    contexts = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(evidence_context_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }

    audit: list[dict[str, Any]] = []
    for question_id in sorted(targets):
        for operand in plans[question_id].get("operands") or []:
            if operand.get("required") is not True:
                continue
            operand_id = str(operand.get("operand_id") or "")
            key = (question_id, operand_id)
            subplan = _subplan(question_id=question_id, operand=operand)
            if subplan is None:
                lexical = hybrid = {
                    "status": "OPERAND_PLAN_NOT_SINGLE_ENTITY_AND_YEAR",
                    "reason_code": "STRICT_SOURCE_CHECK_REQUIRES_ONE_TICKER_AND_ONE_YEAR_PER_OPERAND",
                    "strict_source_candidate_count": 0,
                    "navigation_candidates": [],
                    "first_stage_failed_gate_counts": {},
                    "second_stage_failed_gate_counts": {},
                }
            else:
                query = _metric_query(plans[question_id], operand)
                lexical = _strict_status(
                    source_rows=_source_rows(
                        table_candidates=lexical_candidates[key],
                        assets=assets,
                        query=query,
                        maximum_row_candidates=maximum_row_candidates,
                    ),
                    plan=subplan,
                    tables=tables,
                    contexts=contexts,
                    minimum_row_jaccard=minimum_row_jaccard,
                    minimum_row_margin=minimum_row_margin,
                )
                hybrid = _strict_status(
                    source_rows=_source_rows(
                        table_candidates=all_candidates[key],
                        assets=assets,
                        query=query,
                        maximum_row_candidates=maximum_row_candidates,
                    ),
                    plan=subplan,
                    tables=tables,
                    contexts=contexts,
                    minimum_row_jaccard=minimum_row_jaccard,
                    minimum_row_margin=minimum_row_margin,
                )
            record = {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "operand_id": operand_id,
                "lexical": lexical,
                "hybrid": hybrid,
                "improved_to_unique": (
                    lexical["status"] != "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"
                    and hybrid["status"] == "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"
                ),
                "became_ambiguous": (
                    lexical["status"] == "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"
                    and hybrid["status"] == "MULTIPLE_STRICT_SOURCE_CANDIDATES"
                ),
                "raw_numeric_values_included": False,
                "source_contract": dict(CONTRACT),
            }
            if _contains_forbidden(record):
                raise ValueError("hybrid source probe leaked a forbidden field")
            audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(targets),
        "operand_count": len(audit),
        "hybrid_table_count": len(requested_uids),
        "lexical_status_counts": dict(sorted(Counter(row["lexical"]["status"] for row in audit).items())),
        "hybrid_status_counts": dict(sorted(Counter(row["hybrid"]["status"] for row in audit).items())),
        "improved_to_unique_count": sum(bool(row["improved_to_unique"]) for row in audit),
        "became_ambiguous_count": sum(bool(row["became_ambiguous"]) for row in audit),
        "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin},
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "staged_formula_hybrid_source_probe_v1.jsonl"
        summary_path = temporary / "staged_formula_hybrid_source_probe_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "plans": plans_path,
            "targets": targets_path,
            "hybrid_manifest": hybrid_artifact_dir / "manifest.json",
            "assets": assets_path,
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


def validate_staged_formula_hybrid_source_probe(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected hybrid source probe contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("hybrid source probe hash mismatch")
    targets_path = Path(str(((manifest.get("inputs") or {}).get("targets") or {}).get("path") or ""))
    targets = {int(row["question_id"]) for row in _read_jsonl(targets_path)}
    if not targets <= set(range(1, expected_question_count + 1)):
        raise ValueError("hybrid source probe targets are outside the expected population")
    audit = _read_jsonl(artifact_dir / "staged_formula_hybrid_source_probe_v1.jsonl")
    if {int(row["question_id"]) for row in audit} != targets:
        raise ValueError("hybrid source probe target coverage mismatch")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        for row in audit
    ):
        raise ValueError("hybrid source probe lost its non-authorizing boundary")
    summary = _read_json(artifact_dir / "staged_formula_hybrid_source_probe_summary_v1.json")
    lexical_counts = dict(sorted(Counter(row["lexical"]["status"] for row in audit).items()))
    hybrid_counts = dict(sorted(Counter(row["hybrid"]["status"] for row in audit).items()))
    if (
        summary.get("operand_count") != len(audit)
        or summary.get("lexical_status_counts") != lexical_counts
        or summary.get("hybrid_status_counts") != hybrid_counts
        or summary.get("improved_to_unique_count") != sum(bool(row["improved_to_unique"]) for row in audit)
        or summary.get("became_ambiguous_count") != sum(bool(row["became_ambiguous"]) for row in audit)
    ):
        raise ValueError("hybrid source probe summary mismatch")
    return {
        "status": "PASS",
        "target_question_count": len(targets),
        "operand_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
