"""Adapt uniquely rechecked full-corpus rows into value-blind diagnostics.

This adapter is deliberately narrower than retrieval: it does not search for
an answer and it does not mutate E2E packets.  It converts only a full-corpus
row candidate that has a high lexical row match, exact V2/V3 source lineage,
one V3 year header, an explicit header unit, and a reliable numeric coordinate.
The existing direct-lookup materializer remains the sole consumer.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit
from finance_query.research.machine_direct_lookup_route_materialization import (
    _simple_plan,
    _source_aligned,
)
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_full_corpus_direct_lookup_adapter_v1"
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
FORBIDDEN = frozenset({"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "rows", "raw_source_cell", "raw_source_row"})


def _rows(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


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
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _table_function_matches_operand(operand: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """Respect an operand's statement-type contract when it is declared."""
    allowed = {str(value) for value in operand.get("allowed_table_functions") or []}
    if not allowed:
        # Older direct-lookup plans do not carry this contract.  Preserve their
        # existing routing behaviour while enforcing it for typed staged plans.
        return True
    observed = str(((context.get("table_function") or {}).get("kind")) or "")
    normalized = {
        "financial_note": "financial_note_detail",
        "financial_note_detail": "financial_note_detail",
    }.get(observed, observed)
    return normalized in allowed


def _candidate(
    *,
    row: Mapping[str, Any],
    plan: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    minimum_row_jaccard: float,
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    operand = (plan.get("operands") or [None])[0]
    checks: dict[str, bool] = {
        "one_operand_direct_lookup_plan": isinstance(operand, Mapping),
        "row_score_threshold": float(row.get("row_token_jaccard") or 0.0) >= minimum_row_jaccard,
        "v2_v3_table_present": table is not None and context is not None,
    }
    if not all(checks.values()) or table is None or context is None or not isinstance(operand, Mapping):
        return None, checks
    requested_year = _int((plan.get("years") or [None])[0], label="plan year")
    row_index = _int(row.get("row_index"), label="review row index")
    checks["review_year_matches_plan"] = _int(row.get("report_year"), label="review report year") == requested_year
    checks["review_ticker_matches_plan"] = str(row.get("ticker") or "").upper() == str(operand.get("ticker") or "").upper()
    checks["v2_v3_source_aligned"] = _source_aligned(table, context)
    requested_scope = operand.get("scope")
    checks["scope_compatible"] = requested_scope is None or row.get("observed_scope") == requested_scope
    checks["table_function_matches_operand"] = _table_function_matches_operand(operand, context)
    headers = [
        header
        for header in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(header, Mapping)
        and [str(value) for value in header.get("period_labels") or []] == [str(requested_year)]
    ]
    checks["one_exact_v3_year_header"] = len(headers) == 1
    if not all(checks.values()):
        return None, checks
    header = headers[0]
    column_index = _int(header.get("column_index"), label="V3 header column")
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    header_cells = [
        {"row_index": _int(item.get("row_index"), label="header row"), "column_index": _int(item.get("column_index"), label="header column")}
        for item in header.get("header_source_cells") or []
        if isinstance(item, Mapping)
    ]
    checks["header_coordinates_valid"] = bool(
        header_cells
        and all(
            0 <= item["row_index"] < len(rows)
            and 0 <= item["column_index"] < len(rows[item["row_index"]])
            and item["row_index"] < len(provenance)
            and item["column_index"] < len(provenance[item["row_index"]])
            and isinstance(provenance[item["row_index"]][item["column_index"]], Mapping)
            for item in header_cells
        )
    )
    source_unit, _, multiplier = resolve_source_unit([{"raw_source_cell": str(header.get("source_label") or "")}])
    checks["unique_header_unit"] = source_unit is not None and multiplier is not None
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    checks["selected_cell_reliable_numeric"] = bool(
        len(profiles) == 1
        and column_index in set(profiles[0].get("numeric_columns") or [])
        and column_index not in set(profiles[0].get("unreliable_numeric_columns") or [])
    )
    checks["value_coordinate_has_v2_provenance"] = bool(
        0 <= row_index < len(rows)
        and 0 <= column_index < len(rows[row_index])
        and row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    if not all(checks.values()):
        return None, checks
    source = table.get("source_provenance") or {}
    locator = {
        key: source.get(key) if key not in {"local_ordinal", "page_no"} else table.get(key)
        for key in ("source_path", "source_sha256", "table_sha256", "local_ordinal", "char_start", "page_no")
    }
    if not all(locator.get(key) is not None for key in ("source_path", "source_sha256", "table_sha256", "local_ordinal", "char_start")):
        raise ValueError("V2 full-corpus candidate lacks an exact table locator")
    header_texts = [str(rows[item["row_index"]][item["column_index"]]) for item in header_cells]
    return {
        "question_id": _int(row.get("question_id"), label="review question id"),
        "internal_table_uid": table.get("internal_table_uid"),
        "document_id": table.get("document_id"),
        "requested_year": requested_year,
        "source_unit_candidate": source_unit,
        "scope_status": "SCOPE_NOT_REQUESTED" if requested_scope is None else "SCOPE_MATCH",
        "exact_table_locator": locator,
        "exact_table_locator_sha256": _sha_json(locator),
        "column_index": column_index,
        "header_sha256": hashlib.sha256("\n".join(header_texts).encode("utf-8")).hexdigest(),
        "row_index": row_index,
        "row_label": row.get("row_label"),
        "row_label_sha256": hashlib.sha256(str(row.get("row_label") or "").encode("utf-8")).hexdigest(),
        "row_label_token_jaccard": float(row.get("row_token_jaccard") or 0.0),
        "numeric_column_indices": list(row.get("numeric_cell_indices") or []),
        "review_packet_id": row.get("review_packet_id"),
        "header_source_cells": header_cells,
    }, checks


def build_full_corpus_direct_lookup_adapter(
    *,
    triage_path: Path,
    plans_path: Path,
    base_route_overlay_path: Path,
    row_review_queue_path: Path,
    full_corpus_manifest_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
) -> dict[str, Any]:
    """Emit diagnostics only for strict full-corpus direct-lookup candidates."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0:
        raise ValueError("minimum row Jaccard must be between zero and one")
    manifest = _json(full_corpus_manifest_path)
    if manifest.get("protocol") != "vifinqa_full_corpus_candidate_retrieval_v1":
        raise ValueError("unexpected full-corpus retrieval protocol")
    expected_queue_sha = ((manifest.get("outputs") or {}).get(row_review_queue_path.name) or {}).get("sha256")
    if sha256_file(row_review_queue_path) != expected_queue_sha:
        raise ValueError("full-corpus row review queue hash mismatch")
    context_manifest = _json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    triage = _rows(triage_path)
    plans = {_int(row.get("question_id"), label="plan question id"): row for row in _rows(plans_path)}
    routes = {_int(row.get("question_id"), label="route question id"): row for row in _rows(base_route_overlay_path)}
    target_ids = {
        _int(row.get("question_id"), label="triage question id")
        for row in triage
        if row.get("primary_blocker") == TARGET_BLOCKER
        and _int(row.get("question_id"), label="triage question id") in plans
        and _int(row.get("question_id"), label="triage question id") in routes
        and _simple_plan(plans[_int(row.get("question_id"), label="triage question id")], routes[_int(row.get("question_id"), label="triage question id")])
    }
    tables = {str(row.get("internal_table_uid") or ""): row for row in _rows(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _rows(evidence_context_path)}
    raw_by_question: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in _rows(row_review_queue_path):
        question_id = _int(row.get("question_id"), label="review question id")
        if question_id in target_ids:
            raw_by_question[question_id].append(row)
    diagnostics: list[dict[str, Any]] = []
    row_candidates: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for question_id in sorted(target_ids):
        accepted: list[tuple[dict[str, Any], dict[str, bool], Mapping[str, Any]]] = []
        all_rows_by_uid: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in raw_by_question.get(question_id, []):
            all_rows_by_uid[str(row.get("internal_table_uid") or "")].append(row)
        for row in raw_by_question.get(question_id, []):
            uid = str(row.get("internal_table_uid") or "")
            candidate, checks = _candidate(
                row=row,
                plan=plans[question_id],
                table=tables.get(uid),
                context=contexts.get(uid),
                minimum_row_jaccard=minimum_row_jaccard,
            )
            if candidate is not None:
                accepted.append((candidate, checks, row))
        unique = len(accepted) == 1
        if unique:
            candidate, checks, source_row = accepted[0]
            diagnostic_id = _sha_json({"question_id": question_id, "review_packet_id": candidate["review_packet_id"], "column_index": candidate["column_index"]})
            diagnostics.append(
                {
                    "protocol": PROTOCOL,
                    "diagnostic_id": diagnostic_id,
                    "question_id": question_id,
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
                    "source_contract": dict(CONTRACT),
                }
            )
            rows_for_table = all_rows_by_uid[candidate["internal_table_uid"]]
            for source in rows_for_table:
                row_candidates.append(
                    {
                        "protocol": PROTOCOL,
                        "diagnostic_id": diagnostic_id,
                        "row_index": _int(source.get("row_index"), label="review row index"),
                        "row_rank": _int(source.get("row_rank"), label="review row rank"),
                        "row_label": source.get("row_label"),
                        "row_label_sha256": hashlib.sha256(str(source.get("row_label") or "").encode("utf-8")).hexdigest(),
                        "row_label_token_jaccard": float(source.get("row_token_jaccard") or 0.0),
                        "numeric_column_indices": list(source.get("numeric_cell_indices") or []),
                        "source_contract": dict(CONTRACT),
                    }
                )
        else:
            checks = {}
        audit_row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "adapter_status": "UNIQUE_FULL_CORPUS_CANDIDATE" if unique else "QUARANTINED_FULL_CORPUS_CANDIDATE",
            "accepted_candidate_count": len(accepted),
            "checks": checks,
            "navigation": None
            if not unique
            else {
                key: candidate[key]
                for key in ("internal_table_uid", "row_index", "column_index", "requested_year", "review_packet_id")
            },
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(audit_row):
            raise ValueError("full-corpus adapter audit contains forbidden content")
        audit.append(audit_row)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(target_ids),
        "diagnostic_count": len(diagnostics),
        "row_candidate_count": len(row_candidates),
        "status_counts": dict(sorted(Counter(row["adapter_status"] for row in audit).items())),
        "minimum_row_jaccard": minimum_row_jaccard,
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        diagnostics_path = temporary / "table_diagnostics_v1.jsonl"
        rows_path = temporary / "row_candidates_v1.jsonl"
        audit_path = temporary / "full_corpus_direct_lookup_adapter_audit_v1.jsonl"
        summary_path = temporary / "full_corpus_direct_lookup_adapter_summary_v1.json"
        _write_jsonl(diagnostics_path, diagnostics)
        _write_jsonl(rows_path, row_candidates)
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "triage": triage_path,
            "plans": plans_path,
            "base_route_overlay": base_route_overlay_path,
            "row_review_queue": row_review_queue_path,
            "full_corpus_manifest": full_corpus_manifest_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
        }
        manifest_output = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
            "outputs": {
                "table_diagnostics": {"path": diagnostics_path.name, "sha256": sha256_file(diagnostics_path)},
                "row_candidates": {"path": rows_path.name, "sha256": sha256_file(rows_path)},
                "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
            },
            "source_contract": dict(CONTRACT),
        }
        _write_json(temporary / "manifest.json", manifest_output)
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_full_corpus_direct_lookup_adapter(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected full-corpus adapter protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = (artifact_dir / str(descriptor.get("path") or "")) if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("full-corpus adapter hash mismatch")
    audit = _rows(artifact_dir / "full_corpus_direct_lookup_adapter_audit_v1.jsonl")
    diagnostics = _rows(artifact_dir / "table_diagnostics_v1.jsonl")
    row_candidates = _rows(artifact_dir / "row_candidates_v1.jsonl")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        or "human_verified" in row
        for row in audit
    ):
        raise ValueError("full-corpus adapter audit lost non-authorizing boundary")
    unique_ids = {row["question_id"] for row in audit if row.get("adapter_status") == "UNIQUE_FULL_CORPUS_CANDIDATE"}
    if len(diagnostics) != len(unique_ids) or {row["question_id"] for row in diagnostics} != unique_ids:
        raise ValueError("full-corpus adapter diagnostics are not one-to-one with unique candidates")
    diagnostic_ids = {row["diagnostic_id"] for row in diagnostics}
    if any(row.get("diagnostic_id") not in diagnostic_ids or row.get("source_contract") != CONTRACT for row in row_candidates):
        raise ValueError("full-corpus adapter rows have invalid diagnostic lineage")
    summary = _json(artifact_dir / "full_corpus_direct_lookup_adapter_summary_v1.json")
    if summary.get("target_question_count") != len(audit) or summary.get("diagnostic_count") != len(diagnostics):
        raise ValueError("full-corpus adapter summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "target_question_count": len(audit),
        "diagnostic_count": len(diagnostics),
        "answer_eligible": False,
        "submission_eligible": False,
    }
