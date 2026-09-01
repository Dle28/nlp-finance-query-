"""Materialize only source-rechecked one-cell lookup routes into E2E inputs.

This module repairs a precise gap: a typed one-value lookup can be complete
while the legacy route overlay still says ``route_incomplete``.  It does not
turn retrieval scores into answers.  A route is revised only when a single
candidate table, row, header/year, unit and V2/V3 source lineage agree.

Outputs remain candidate-only.  The normal E2E evidence gate must still
resolve entity, scope, variable semantics and every other required field.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_machine_direct_lookup_route_materialization_v1"
SCHEMA_VERSION = 1
TARGET_BLOCKER = "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"
PERIOD_PROTOCOL = "period_column_candidate_packets_v1"
ROUTE_PROTOCOL = "route_completeness_overlay_v3"
PERIOD_CONTRACT = {
    "candidate_only": True,
    "evidence_eligible": False,
    "may_execute_formula": False,
    "may_select_final_column": False,
    "may_select_value": False,
    "promotion_allowed": False,
    "submission_eligible": False,
    "training_eligible": False,
}
ROUTE_CONTRACT = {
    "evidence_eligible": False,
    "may_execute_formula": False,
    "may_select_value": False,
    "navigation_metadata_only": True,
    "promotion_allowed": False,
    "submission_eligible": False,
    "training_eligible": False,
}
MACHINE_CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN_AUDIT_KEYS = frozenset(
    {
        "answer",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "rows",
        "raw_source_row",
        "raw_source_cell",
        "cell_provenance",
    }
)


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain JSON objects")
        values.append(value)
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not an integer") from error


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in FORBIDDEN_AUDIT_KEYS or _contains_forbidden(child)
            for key, child in value.items()
        )
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_output(manifest: Mapping[str, Any], *, key: str, path: Path, label: str) -> None:
    descriptor = (manifest.get("outputs") or {}).get(key) or {}
    if not isinstance(descriptor, Mapping) or sha256_file(path) != descriptor.get("sha256"):
        raise ValueError(f"SHA-256 mismatch for {label}")


def _source_aligned(table: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    source, context_source = table.get("source_provenance") or {}, context.get("source_provenance") or {}
    return bool(
        table.get("internal_table_uid") == context.get("internal_table_uid")
        and table.get("document_id") == context.get("document_id")
        and source.get("source_sha256") == context_source.get("source_sha256")
        and source.get("table_sha256") == context_source.get("table_sha256")
        and (context.get("grid") or {}).get("rectangular") is True
        and (context.get("grid") or {}).get("provenance_complete") is True
        and (context.get("quality") or {}).get("status") == "review_ready"
    )


def _source_matches_diagnostic(table: Mapping[str, Any], diagnostic: Mapping[str, Any]) -> bool:
    locator, source = diagnostic.get("exact_table_locator"), table.get("source_provenance")
    if not isinstance(locator, Mapping) or not isinstance(source, Mapping):
        return False
    return bool(
        table.get("document_id") == diagnostic.get("document_id")
        and table.get("local_ordinal") == locator.get("local_ordinal")
        and all(source.get(key) == locator.get(key) for key in ("source_path", "source_sha256", "table_sha256", "char_start"))
    )


def _v3_header_is_strict_redaction_subset(
    *,
    context: Mapping[str, Any],
    diagnostic_header: Mapping[str, Any],
    header: Mapping[str, Any],
    header_texts: list[str],
) -> bool:
    """Allow only V3's provenance-backed removal of OCR redaction noise.

    The V2 diagnostic may treat a masked body row as an additional header row.
    This reconciliation is valid only when V3 keeps a strict subset of the
    raw header rows and the rejected diagnostic labels contain no text beyond
    that same header plus the fixed OCR-redaction marker.
    """
    canonical = context.get("canonical_headers") or {}
    selected_rows = {value for value in canonical.get("header_row_indices") or [] if isinstance(value, int)}
    raw_rows = {value for value in canonical.get("raw_header_row_indices") or [] if isinstance(value, int)}
    source_label = str(header.get("source_label") or "")
    labels = [str(value).strip() for value in diagnostic_header.get("header_labels") or []]
    if (
        len(header_texts) != 1
        or header_texts[0] != source_label
        or not source_label
        or not selected_rows
        or not (selected_rows < raw_rows)
        or source_label not in labels
    ):
        return False
    redaction = "[SỐ_ĐÃ_ẨN]"
    for label in labels:
        if label == source_label or label == redaction:
            continue
        if not label.startswith(source_label):
            return False
        suffix = label[len(source_label) :].strip()
        if not re.fullmatch(r"(?:·\s*)?\[SỐ_ĐÃ_ẨN\](?:\s*·\s*\[SỐ_ĐÃ_ẨN\])*", suffix):
            return False
    return True


def _header_period_matches_requested_year(
    *,
    header: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
    requested_year: int,
) -> bool:
    """Accept the canonical year or one explicitly verified 31/12 date.

    V3 represents some statement columns as ``31/12/YYYY`` rather than the
    bare year.  The latter is safe only for the narrow date recheck produced
    by the exact-ticker adapter; a generic date-looking label must not bypass
    the period gate.
    """
    period_labels = [str(value) for value in header.get("period_labels") or []]
    if period_labels == [str(requested_year)]:
        return True
    if diagnostic.get("period_resolution") != "explicit_year_end_header" or len(period_labels) != 1:
        return False
    label = period_labels[0]
    dates = re.findall(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)", label)
    return len(dates) == 1 and int(dates[0][0]) == 31 and int(dates[0][1]) == 12 and int(dates[0][2]) == requested_year


def _simple_plan(plan: Mapping[str, Any], overlay: Mapping[str, Any]) -> bool:
    ast = plan.get("operation_ast") or {}
    return bool(
        plan.get("decomposition_status") == "complete"
        and plan.get("effective_family") == "direct_lookup"
        and isinstance(ast, Mapping)
        and ast.get("op") == "lookup"
        and len(plan.get("operands") or []) == 1
        and len(plan.get("entities") or []) == 1
        and len(plan.get("years") or []) == 1
        and overlay.get("route_status") == "route_incomplete"
        and overlay.get("required_operations") == ["reported_value"]
        and overlay.get("missing_operations") == ["reported_value"]
    )


def _candidate_from_diagnostic(
    *,
    diagnostic: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    minimum_row_jaccard: float,
    minimum_row_margin: float,
    allow_v3_header_recovery: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    checks: dict[str, bool] = {
        "scope_compatible": diagnostic.get("scope_status") in {"SCOPE_MATCH", "SCOPE_NOT_REQUESTED"},
        "v2_v3_table_present": table is not None and context is not None,
    }
    if table is None or context is None:
        return None, checks
    checks["source_locator_matches_v2"] = _source_matches_diagnostic(table, diagnostic)
    checks["v2_v3_source_aligned"] = _source_aligned(table, context)
    if not all(checks.values()):
        return None, checks
    requested_year = _int(diagnostic.get("requested_year"), label="diagnostic requested year")
    columns = diagnostic.get("matching_year_column_indices") or []
    headers = [
        header
        for header in diagnostic.get("column_headers") or []
        if isinstance(header, Mapping)
    ]
    diagnostic_navigation_available = bool(
        diagnostic.get("period_status") == "UNIQUE_YEAR_HEADER_CANDIDATE"
        and diagnostic.get("unit_status") == "UNIQUE_HEADER_UNIT_CANDIDATE"
        and isinstance(columns, list)
        and len(columns) == 1
    )
    if diagnostic_navigation_available:
        column_index = _int(columns[0], label="diagnostic selected column")
        selected_diagnostic_headers = [
            header for header in headers if header.get("column_index") == column_index
        ]
        checks["diagnostic_header_navigation"] = len(selected_diagnostic_headers) == 1
        if not checks["diagnostic_header_navigation"]:
            return None, checks
        selected_diagnostic_header = selected_diagnostic_headers[0]
        expected_header_sha = selected_diagnostic_header.get("header_sha256")
        header_selection_method = "diagnostic_unique_year_and_unit_v1"
    else:
        v3_exact_headers = [
            header
            for header in ((context.get("canonical_headers") or {}).get("columns") or [])
            if isinstance(header, Mapping)
            and [str(value) for value in header.get("period_labels") or []]
            == [str(requested_year)]
        ]
        checks["v3_exact_header_recovery"] = bool(
            allow_v3_header_recovery and len(v3_exact_headers) == 1
        )
        if not checks["v3_exact_header_recovery"]:
            return None, checks
        column_index = _int(v3_exact_headers[0].get("column_index"), label="V3 recovered column")
        expected_header_sha = None
        header_selection_method = "v3_exact_year_header_recovery_v1"
    v3_headers = [
        header
        for header in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(header, Mapping) and header.get("column_index") == column_index
    ]
    checks["v3_header_unique"] = len(v3_headers) == 1
    if not all(checks.values()):
        return None, checks
    header = v3_headers[0]
    coordinates = [
        {"row_index": _int(cell.get("row_index"), label="header row index"), "column_index": _int(cell.get("column_index"), label="header column index")}
        for cell in header.get("header_source_cells") or []
        if isinstance(cell, Mapping)
    ]
    source_rows = table.get("rows") or []
    header_texts: list[str] = []
    for coordinate in coordinates:
        row_index, header_column = coordinate["row_index"], coordinate["column_index"]
        if row_index < 0 or header_column < 0 or row_index >= len(source_rows) or header_column >= len(source_rows[row_index]):
            continue
        header_texts.append(str(source_rows[row_index][header_column]))
    checks["v3_header_coordinates_valid"] = len(header_texts) == len(coordinates) and bool(header_texts)
    if expected_header_sha is not None:
        exact_header_match = bool(
            checks["v3_header_coordinates_valid"]
            and hashlib.sha256("\n".join(header_texts).encode("utf-8")).hexdigest()
            == expected_header_sha
        )
        reconciled_header_match = bool(
            not exact_header_match
            and allow_v3_header_recovery
            and _v3_header_is_strict_redaction_subset(
                context=context,
                diagnostic_header=selected_diagnostic_header,
                header=header,
                header_texts=header_texts,
            )
        )
        checks["v2_header_hash_matches_v3"] = exact_header_match or reconciled_header_match
        if reconciled_header_match:
            header_selection_method = "v3_canonical_header_subset_reconciliation_v1"
    checks["v3_header_has_requested_year"] = _header_period_matches_requested_year(
        header=header,
        diagnostic=diagnostic,
        requested_year=requested_year,
    )
    source_label = str(header.get("source_label") or "")
    source_unit, _, multiplier = resolve_source_unit([{"raw_source_cell": source_label}])
    checks["v3_header_unit_matches_diagnostic"] = (
        source_unit == diagnostic.get("source_unit_candidate") and multiplier is not None
    )
    usable = [
        row
        for row in rows
        if column_index in {_int(value, label="row numeric column") for value in row.get("numeric_column_indices") or []}
    ]
    usable.sort(
        key=lambda row: (
            -float(row.get("row_label_token_jaccard") or 0.0),
            _int(row.get("row_rank"), label="row rank"),
        )
    )
    checks["row_candidate_present"] = bool(usable)
    if usable:
        top_score = float(usable[0].get("row_label_token_jaccard") or 0.0)
        second_score = float(usable[1].get("row_label_token_jaccard") or 0.0) if len(usable) > 1 else 0.0
    else:
        top_score, second_score = 0.0, 0.0
    checks["row_label_score_threshold"] = top_score >= minimum_row_jaccard
    checks["row_label_margin_threshold"] = top_score - second_score >= minimum_row_margin
    if not all(checks.values()):
        return None, checks
    top = usable[0]
    row_index = _int(top.get("row_index"), label="row candidate index")
    profile = [
        item
        for item in context.get("row_profiles") or []
        if isinstance(item, Mapping) and item.get("row_index") == row_index
    ]
    checks["v3_row_profile_unique"] = len(profile) == 1
    checks["selected_cell_reliable_numeric"] = bool(
        len(profile) == 1
        and column_index in set(profile[0].get("numeric_columns") or [])
        and column_index not in set(profile[0].get("unreliable_numeric_columns") or [])
    )
    if not all(checks.values()):
        return None, checks
    provenance = (table.get("cell_provenance") or [])
    checks["v2_cell_provenance_present"] = bool(
        row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    if not all(checks.values()):
        return None, checks
    return {
        "internal_table_uid": diagnostic["internal_table_uid"],
        "row_index": row_index,
        "column_index": column_index,
        "header_source_cells": coordinates,
        "period_labels": [str(value) for value in header.get("period_labels") or []],
        "source_label": source_label,
        "unit_labels": list(header.get("unit_labels") or []),
        "requested_year": requested_year,
        "row_label": top["row_label"],
        "row_label_sha256": top["row_label_sha256"],
        "row_label_token_jaccard": top_score,
        "row_score_margin": top_score - second_score,
        "exact_table_locator_sha256": diagnostic["exact_table_locator_sha256"],
        "source_cell_provenance_sha256": _sha_json(provenance[row_index][column_index]),
        "header_selection_method": header_selection_method,
    }, checks


def _stage_id(question_id: int, plan: Mapping[str, Any]) -> str:
    fingerprint = str(plan.get("plan_fingerprint") or "")
    return f"machine_lookup_{question_id}_{fingerprint[:12]}"


def _period_packet(base: Mapping[str, Any], *, plan: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(base))
    question_id = _int(plan.get("question_id"), label="plan question id")
    operand = (plan.get("operands") or [])[0]
    entity, year = (plan.get("entities") or [])[0], _int((plan.get("years") or [])[0], label="plan year")
    role = str(operand.get("role") or operand.get("operand_id") or "reported_value")
    output.update(
        {
            "protocol": PERIOD_PROTOCOL,
            "question_id": question_id,
            "input_packet_status": "machine_direct_lookup_recheck_candidate",
            "packet_status": "unique_period_column_candidate",
            "route_status": "route_complete",
            "question_context": {
                "entities": [str(entity)],
                "scope": operand.get("scope"),
                "source": "typed_plan_machine_direct_lookup_recheck",
                "years": [year],
            },
            "stages": [
                {
                    "stage_id": _stage_id(question_id, plan),
                    "route_kind": "reported_concept",
                    "metric_id": None,
                    "concept_id": role,
                    "required_operands": [
                        {
                            "role": role,
                            "concept_id": role,
                            "expected_table_types": [],
                            "period_type": "unresolved_machine_candidate",
                            "column_status": "unique_period_column_candidate",
                            "navigation_row_count": 1,
                            "period_column_candidate_count": 1,
                            "column_candidate_reason_counts": {
                                "EXACT_REQUESTED_YEAR": 1,
                                "MACHINE_DIRECT_LOOKUP_RECHECK_CANDIDATE_ONLY": 1,
                            },
                            "row_status_counts": {"unique_period_column_candidate": 1},
                            "period_column_candidates": [
                                {
                                    key: candidate[key]
                                    for key in (
                                        "internal_table_uid",
                                        "row_index",
                                        "column_index",
                                        "header_source_cells",
                                        "period_labels",
                                        "source_label",
                                        "unit_labels",
                                        "requested_year",
                                    )
                                }
                                | {
                                    "reason_codes": [
                                        "EXACT_REQUESTED_YEAR",
                                        "MACHINE_DIRECT_LOOKUP_RECHECK_CANDIDATE_ONLY",
                                    ],
                                    "source_contract": dict(PERIOD_CONTRACT),
                                }
                            ],
                        }
                    ],
                }
            ],
            "source_contract": dict(PERIOD_CONTRACT),
        }
    )
    return output


def _route_overlay(base: Mapping[str, Any], *, plan: Mapping[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(base))
    operand = (plan.get("operands") or [])[0]
    entity, year = (plan.get("entities") or [])[0], _int((plan.get("years") or [])[0], label="plan year")
    output.update(
        {
            "protocol": ROUTE_PROTOCOL,
            "route_status": "route_complete",
            "covered_operations": ["reported_value"],
            "missing_operations": [],
            "reason_codes": ["MACHINE_DIRECT_LOOKUP_ROUTE_RECHECK_CANDIDATE_ONLY"],
            "question_context": {
                "entities": [str(entity)],
                "scope": operand.get("scope"),
                "source": "typed_plan_machine_direct_lookup_recheck",
                "years": [year],
            },
            "machine_plan_fingerprint": plan.get("plan_fingerprint"),
            "source_contract": dict(ROUTE_CONTRACT),
        }
    )
    return output


def build_machine_direct_lookup_route_materialization(
    *,
    triage_path: Path,
    plans_path: Path,
    base_period_packets_path: Path,
    base_period_manifest_path: Path,
    base_route_overlay_path: Path,
    base_route_overlay_manifest_path: Path,
    table_diagnostics_path: Path,
    row_candidates_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.8,
    minimum_row_margin: float = 0.2,
    allow_v3_header_recovery: bool = False,
) -> dict[str, Any]:
    """Create immutable E2E inputs for uniquely rechecked direct lookups."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("row-score thresholds must be between zero and one")
    period_manifest, overlay_manifest, context_manifest = (
        _load_json(base_period_manifest_path),
        _load_json(base_route_overlay_manifest_path),
        _load_json(evidence_context_manifest_path),
    )
    _require_output(period_manifest, key="period_packets", path=base_period_packets_path, label="base period packets")
    _require_output(overlay_manifest, key="overlay", path=base_route_overlay_path, label="base route overlay")
    if sha256_file(structured_tables_path) != ((period_manifest.get("inputs") or {}).get("structured_tables_v2") or {}).get("sha256"):
        raise ValueError("SHA-256 mismatch for structured V2 tables")
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("SHA-256 mismatch for V3 evidence context")

    target_ids = {
        _int(row.get("question_id"), label="triage question id")
        for row in _load_jsonl(triage_path)
        if row.get("primary_blocker") == TARGET_BLOCKER
    }
    plans = {_int(row.get("question_id"), label="plan question id"): row for row in _load_jsonl(plans_path)}
    periods = {_int(row.get("question_id"), label="period question id"): row for row in _load_jsonl(base_period_packets_path)}
    overlays = {_int(row.get("question_id"), label="overlay question id"): row for row in _load_jsonl(base_route_overlay_path)}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(periods) != expected_ids or set(overlays) != expected_ids or not target_ids <= set(plans):
        raise ValueError("direct lookup materialization inputs have incomplete question coverage")
    diagnostics_by_question: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    diagnostics_by_id: dict[str, dict[str, Any]] = {}
    for diagnostic in _load_jsonl(table_diagnostics_path):
        diagnostic_id = str(diagnostic.get("diagnostic_id") or "")
        if not diagnostic_id or diagnostic_id in diagnostics_by_id:
            raise ValueError("table diagnostics require unique diagnostic IDs")
        diagnostics_by_id[diagnostic_id] = diagnostic
        question_id = _int(diagnostic.get("question_id"), label="diagnostic question id")
        if question_id in target_ids:
            diagnostics_by_question[question_id].append(diagnostic)
    rows_by_diagnostic: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _load_jsonl(row_candidates_path):
        diagnostic_id = str(row.get("diagnostic_id") or "")
        if diagnostic_id in diagnostics_by_id:
            rows_by_diagnostic[diagnostic_id].append(row)
    tables = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(evidence_context_path)}

    revised_periods: dict[int, dict[str, Any]] = {}
    revised_overlays: dict[int, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for question_id in sorted(target_ids):
        plan, overlay = plans[question_id], overlays[question_id]
        checks: dict[str, bool] = {"simple_direct_lookup_plan": _simple_plan(plan, overlay)}
        candidates: list[tuple[dict[str, Any], dict[str, bool]]] = []
        if checks["simple_direct_lookup_plan"]:
            for diagnostic in diagnostics_by_question.get(question_id, []):
                uid = str(diagnostic.get("internal_table_uid") or "")
                candidate, candidate_checks = _candidate_from_diagnostic(
                    diagnostic=diagnostic,
                    rows=rows_by_diagnostic[str(diagnostic["diagnostic_id"])],
                    table=tables.get(uid),
                    context=contexts.get(uid),
                    minimum_row_jaccard=minimum_row_jaccard,
                    minimum_row_margin=minimum_row_margin,
                    allow_v3_header_recovery=allow_v3_header_recovery,
                )
                if candidate is not None:
                    candidates.append((candidate, candidate_checks))
        checks["exactly_one_source_candidate"] = len(candidates) == 1
        if all(checks.values()):
            candidate, candidate_checks = candidates[0]
            checks.update(candidate_checks)
        materialize = all(checks.values())
        status = "MATERIALIZED_DIRECT_LOOKUP_EXECUTION_CANDIDATE" if materialize else (
            "QUARANTINED_NOT_SIMPLE_DIRECT_LOOKUP" if not checks["simple_direct_lookup_plan"] else "QUARANTINED_SOURCE_CANDIDATE_NOT_UNIQUE_OR_INSUFFICIENT"
        )
        if materialize:
            revised_periods[question_id] = _period_packet(periods[question_id], plan=plan, candidate=candidate)
            revised_overlays[question_id] = _route_overlay(overlays[question_id], plan=plan)
        audit_row = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "materialization_status": status,
            "checks": checks,
            "candidate_count": len(candidates),
            "candidate_navigation": (
                None
                if not materialize
                else {
                    key: candidate[key]
                    for key in (
                        "internal_table_uid",
                        "row_index",
                        "column_index",
                        "period_labels",
                        "unit_labels",
                        "row_label",
                        "row_label_sha256",
                        "row_label_token_jaccard",
                        "row_score_margin",
                        "exact_table_locator_sha256",
                        "source_cell_provenance_sha256",
                        "header_selection_method",
                    )
                }
            ),
            "raw_numeric_values_included": False,
            "source_contract": dict(MACHINE_CONTRACT),
        }
        if _contains_forbidden(audit_row):
            raise ValueError("direct lookup audit contains a forbidden value field")
        audit.append(audit_row)
    output_periods = [revised_periods.get(question_id, periods[question_id]) for question_id in range(1, expected_question_count + 1)]
    output_overlays = [revised_overlays.get(question_id, overlays[question_id]) for question_id in range(1, expected_question_count + 1)]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "target_question_count": len(target_ids),
        "materialized_question_count": len(revised_periods),
        "status_counts": dict(sorted(Counter(row["materialization_status"] for row in audit).items())),
        "base_period_status_counts": dict(sorted(Counter(str(row.get("packet_status") or "") for row in periods.values()).items())),
        "output_period_status_counts": dict(sorted(Counter(str(row.get("packet_status") or "") for row in output_periods).items())),
        "base_route_status_counts": dict(sorted(Counter(str(row.get("route_status") or "") for row in overlays.values()).items())),
        "output_route_status_counts": dict(sorted(Counter(str(row.get("route_status") or "") for row in output_overlays).items())),
        "thresholds": {
            "minimum_row_jaccard": minimum_row_jaccard,
            "minimum_row_margin": minimum_row_margin,
            "allow_v3_header_recovery": allow_v3_header_recovery,
        },
        "source_contract": dict(MACHINE_CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        period_path = temporary / "period_column_candidate_packets_v1.jsonl"
        overlay_path = temporary / "route_completeness_overlay_v3.jsonl"
        audit_path = temporary / "machine_direct_lookup_route_audit_v1.jsonl"
        summary_path = temporary / "machine_direct_lookup_route_summary_v1.json"
        _write_jsonl(period_path, output_periods)
        _write_jsonl(overlay_path, output_overlays)
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "triage": triage_path,
            "plans": plans_path,
            "base_period_packets": base_period_packets_path,
            "base_period_manifest": base_period_manifest_path,
            "base_route_overlay": base_route_overlay_path,
            "base_route_overlay_manifest": base_route_overlay_manifest_path,
            "table_diagnostics": table_diagnostics_path,
            "row_candidates": row_candidates_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
        }
        common = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
            "parameters": {"allow_v3_header_recovery": allow_v3_header_recovery},
            "source_contract": dict(MACHINE_CONTRACT),
        }
        period_manifest = {
            **common,
            "outputs": {"period_packets": {"path": period_path.name, "sha256": sha256_file(period_path)}},
        }
        overlay_manifest = {
            **common,
            "outputs": {"overlay": {"path": overlay_path.name, "sha256": sha256_file(overlay_path)}},
        }
        machine_manifest = {
            **common,
            "outputs": {
                "period_packets": {"path": period_path.name, "sha256": sha256_file(period_path)},
                "overlay": {"path": overlay_path.name, "sha256": sha256_file(overlay_path)},
                "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
            },
        }
        _write_json(temporary / "period_column_candidate_packets_v1.manifest.json", period_manifest)
        _write_json(temporary / "route_completeness_overlay_v3.manifest.json", overlay_manifest)
        _write_json(temporary / "manifest.json", machine_manifest)
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_machine_direct_lookup_route_materialization(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Validate hashes, complete coverage, and bounded one-route mutations."""
    manifest = _load_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != MACHINE_CONTRACT:
        raise ValueError("unexpected direct lookup materialization protocol")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("direct lookup materialization input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("direct lookup materialization output hash mismatch")
    periods = {_int(row.get("question_id"), label="output period question id"): row for row in _load_jsonl(artifact_dir / "period_column_candidate_packets_v1.jsonl")}
    overlays = {_int(row.get("question_id"), label="output overlay question id"): row for row in _load_jsonl(artifact_dir / "route_completeness_overlay_v3.jsonl")}
    audit = _load_jsonl(artifact_dir / "machine_direct_lookup_route_audit_v1.jsonl")
    summary = _load_json(artifact_dir / "machine_direct_lookup_route_summary_v1.json")
    expected_ids = set(range(1, expected_question_count + 1))
    if set(periods) != expected_ids or set(overlays) != expected_ids:
        raise ValueError("direct lookup materialization question coverage mismatch")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != MACHINE_CONTRACT
        or "human_verified" in row
        for row in audit
    ):
        raise ValueError("direct lookup materialization audit lost non-authorizing boundary")
    materialized = {
        _int(row.get("question_id"), label="audit question id")
        for row in audit
        if row.get("materialization_status") == "MATERIALIZED_DIRECT_LOOKUP_EXECUTION_CANDIDATE"
    }
    base_period_path = Path(str(((manifest.get("inputs") or {}).get("base_period_packets") or {}).get("path") or ""))
    base_overlay_path = Path(str(((manifest.get("inputs") or {}).get("base_route_overlay") or {}).get("path") or ""))
    base_periods = {_int(row.get("question_id"), label="base period question id"): row for row in _load_jsonl(base_period_path)}
    base_overlays = {_int(row.get("question_id"), label="base overlay question id"): row for row in _load_jsonl(base_overlay_path)}
    for question_id in expected_ids - materialized:
        if _sha_json(periods[question_id]) != _sha_json(base_periods[question_id]) or _sha_json(overlays[question_id]) != _sha_json(base_overlays[question_id]):
            raise ValueError("non-materialized route input changed")
    for question_id in materialized:
        if periods[question_id].get("packet_status") != "unique_period_column_candidate" or overlays[question_id].get("route_status") != "route_complete":
            raise ValueError("materialized direct lookup has incomplete E2E inputs")
    if summary.get("materialized_question_count") != len(materialized) or summary.get("target_question_count") != len(audit):
        raise ValueError("direct lookup materialization summary mismatch")
    return {"status": "PASS", "question_count": expected_question_count, "target_question_count": len(audit), "materialized_question_count": len(materialized), "answer_eligible": False, "submission_eligible": False}
