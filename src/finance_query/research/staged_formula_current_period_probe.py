"""Recheck current-period headers for new staged-formula operands.

This research probe addresses a specific V3 limitation: a report's current
column can be labelled ``Năm nay`` or ``Số cuối năm`` rather than a literal
year.  It may form a navigation candidate only when the source title has one
matching date, the current-header semantics are exact for the statement type,
the source title has a unit, and the existing V2/V3/row-margin gates pass.
It never emits a value, an evidence binding, an answer, or a submission.
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
from finance_query.research.composition_operand_source_gap_audit import _row_candidates
from finance_query.research.machine_direct_lookup_route_materialization import _source_aligned
from finance_query.research.machine_exact_cell_proposals import sha256_file
from finance_query.research.source_title_period_recheck import _dates_in_text, _fold


PROTOCOL = "vifinqa_staged_formula_current_period_probe_v1"
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


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _locator(table: Mapping[str, Any]) -> dict[str, Any] | None:
    source = table.get("source_provenance") or {}
    locator = {
        "source_path": source.get("source_path"),
        "source_sha256": source.get("source_sha256"),
        "table_sha256": source.get("table_sha256"),
        "local_ordinal": table.get("local_ordinal"),
        "char_start": source.get("char_start"),
        "page_no": table.get("page_no"),
    }
    required = ("source_path", "source_sha256", "table_sha256", "local_ordinal", "char_start")
    return locator if all(locator.get(key) is not None for key in required) else None


def _header_coordinates_valid(
    *, table: Mapping[str, Any], header: Mapping[str, Any]
) -> tuple[list[dict[str, int]], list[str]]:
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    coordinates: list[dict[str, int]] = []
    texts: list[str] = []
    for cell in header.get("header_source_cells") or []:
        if not isinstance(cell, Mapping):
            return [], []
        try:
            row_index, column_index = int(cell.get("row_index")), int(cell.get("column_index"))
        except (TypeError, ValueError):
            return [], []
        if not (
            0 <= row_index < len(rows)
            and 0 <= column_index < len(rows[row_index])
            and row_index < len(provenance)
            and column_index < len(provenance[row_index])
            and isinstance(provenance[row_index][column_index], Mapping)
        ):
            return [], []
        coordinates.append({"row_index": row_index, "column_index": column_index})
        texts.append(str(rows[row_index][column_index]))
    return coordinates, texts


def _allowed_table_function(operand: Mapping[str, Any], kind: str) -> bool:
    expected = {str(value) for value in operand.get("allowed_table_functions") or []}
    normalized = {
        "balance_sheet": "balance_sheet",
        "income_statement": "income_statement",
        "cash_flow_statement": "cash_flow_statement",
        "financial_note": "financial_note_detail",
    }.get(kind)
    return bool(normalized and normalized in expected)


def _current_header_candidate(
    *,
    row: Mapping[str, Any],
    operand: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    minimum_row_jaccard: float,
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    requested_year = int((operand.get("years") or [0])[0])
    row_index = int(row["row_index"]) if row.get("row_index") is not None else -1
    checks: dict[str, bool] = {
        "row_score_threshold": float(row.get("row_token_jaccard") or 0.0) >= minimum_row_jaccard,
        "v2_v3_table_present": table is not None and context is not None,
        "review_year_matches_operand": int(row.get("report_year") or 0) == requested_year,
        "review_ticker_matches_operand": str(row.get("ticker") or "").upper() == str(operand.get("ticker") or "").upper(),
    }
    if not all(checks.values()) or table is None or context is None:
        return None, checks
    checks["v2_v3_source_aligned"] = _source_aligned(table, context)
    requested_scope = operand.get("scope")
    checks["scope_compatible"] = requested_scope is None or row.get("observed_scope") == requested_scope
    kind = str(((context.get("table_function") or {}).get("kind")) or "")
    checks["table_function_matches_operand"] = _allowed_table_function(operand, kind)
    title = str(((context.get("context_trace") or {}).get("source_title")) or "")
    dates = [value for value in _dates_in_text(title) if value.year == requested_year]
    checks["one_requested_year_date_in_source_title"] = len(dates) == 1
    source_unit, _, multiplier = resolve_source_unit([{"raw_source_cell": title}])
    checks["unique_unit_in_source_title"] = source_unit is not None and multiplier is not None
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    numeric_columns = set(profiles[0].get("numeric_columns") or []) if len(profiles) == 1 else set()
    reliable_columns = set(profiles[0].get("unreliable_numeric_columns") or []) if len(profiles) == 1 else set()
    checks["selected_row_has_v3_profile"] = len(profiles) == 1
    headers = [
        header
        for header in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(header, Mapping) and int(header.get("column_index") or -1) in numeric_columns
    ]
    current_headers: list[Mapping[str, Any]] = []
    for header in headers:
        label = _fold(header.get("source_label"))
        if kind == "balance_sheet" and label in {"so cuoi nam", "so cuoi namvnd"}:
            current_headers.append(header)
        elif kind in {"income_statement", "cash_flow_statement"} and label == "nam nay":
            current_headers.append(header)
    checks["one_current_header_with_known_semantics"] = len(current_headers) == 1
    if not all(checks.values()):
        return None, checks
    header = current_headers[0]
    column_index = int(header.get("column_index") or -1)
    checks["selected_cell_reliable_numeric"] = column_index in numeric_columns and column_index not in reliable_columns
    coordinates, header_texts = _header_coordinates_valid(table=table, header=header)
    checks["header_coordinates_valid"] = bool(coordinates)
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    checks["value_coordinate_has_v2_provenance"] = bool(
        0 <= row_index < len(rows)
        and 0 <= column_index < len(rows[row_index])
        and row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    locator = _locator(table)
    checks["exact_table_locator_available"] = locator is not None
    if not all(checks.values()) or locator is None:
        return None, checks
    return {
        "question_id": int(row["question_id"]),
        "internal_table_uid": str(table["internal_table_uid"]),
        "document_id": str(table["document_id"]),
        "requested_year": requested_year,
        "scope_status": "SCOPE_NOT_REQUESTED" if requested_scope is None else "SCOPE_MATCH",
        "exact_table_locator": locator,
        "exact_table_locator_sha256": hashlib.sha256(json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "matching_year_column_indices": [column_index],
        "column_headers": [{"column_index": column_index, "header_sha256": hashlib.sha256("\n".join(header_texts).encode("utf-8")).hexdigest()}],
    }, checks


def _current_header_row_margin_candidate(
    *,
    candidate: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    minimum_row_jaccard: float,
    minimum_row_margin: float,
) -> tuple[tuple[str, int, int] | None, dict[str, bool]]:
    """Apply the normal row-margin gate without re-requiring a V3 year label.

    The preceding candidate has already proved the period from one source-title
    date plus a statement-specific current header.  Requiring V3 to carry the
    literal year again would discard exactly the narrow recheck being tested.
    """
    checks: dict[str, bool] = {
        "scope_compatible": candidate.get("scope_status") in {"SCOPE_MATCH", "SCOPE_NOT_REQUESTED"},
        "v2_v3_table_present": table is not None and context is not None,
    }
    if table is None or context is None or not all(checks.values()):
        return None, checks
    column_values = candidate.get("matching_year_column_indices") or []
    checks["one_rechecked_current_column"] = isinstance(column_values, list) and len(column_values) == 1
    if not checks["one_rechecked_current_column"]:
        return None, checks
    column_index = int(column_values[0])
    usable = [
        row
        for row in rows
        if column_index in {int(value) for value in row.get("numeric_column_indices") or []}
    ]
    usable.sort(key=lambda row: (-float(row.get("row_label_token_jaccard") or 0.0), int(row.get("row_rank") or 0)))
    checks["row_candidate_present"] = bool(usable)
    top_score = float(usable[0].get("row_label_token_jaccard") or 0.0) if usable else 0.0
    second_score = float(usable[1].get("row_label_token_jaccard") or 0.0) if len(usable) > 1 else 0.0
    checks["row_label_score_threshold"] = top_score >= minimum_row_jaccard
    checks["row_label_margin_threshold"] = top_score - second_score >= minimum_row_margin
    if not all(checks.values()):
        return None, checks
    row_index = int(usable[0]["row_index"]) if usable[0].get("row_index") is not None else -1
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    checks["v3_row_profile_unique"] = len(profiles) == 1
    checks["selected_cell_reliable_numeric"] = bool(
        len(profiles) == 1
        and column_index in set(profiles[0].get("numeric_columns") or [])
        and column_index not in set(profiles[0].get("unreliable_numeric_columns") or [])
    )
    provenance = table.get("cell_provenance") or []
    checks["v2_cell_provenance_present"] = bool(
        0 <= row_index < len(provenance)
        and 0 <= column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    if not all(checks.values()):
        return None, checks
    return (str(candidate["internal_table_uid"]), row_index, column_index), checks


def _safe_navigation_descriptor(
    *,
    candidate: Mapping[str, Any],
    source_row: Mapping[str, Any],
    context: Mapping[str, Any],
    final: tuple[str, int, int],
) -> dict[str, Any]:
    """Return only navigation metadata for a rechecked source candidate.

    This deliberately omits row/column coordinates, labels, locator data, and
    all cell content.  It permits a later audit to distinguish same-scope
    duplicates from a separate/consolidated choice without becoming evidence.
    """
    table_uid, row_index, column_index = final
    identity = f"{table_uid}:{row_index}:{column_index}"
    document_id = str(candidate.get("document_id") or "")
    return {
        "navigation_candidate_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "document_identity_sha256": hashlib.sha256(document_id.encode("utf-8")).hexdigest(),
        "observed_scope": source_row.get("observed_scope"),
        "table_function": str(((context.get("table_function") or {}).get("kind")) or ""),
        "period_resolution": "source_title_plus_current_header",
    }


def _current_period_status(
    *,
    source_rows: Iterable[Mapping[str, Any]],
    operand: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    minimum_row_jaccard: float,
    minimum_row_margin: float,
) -> dict[str, Any]:
    """Apply the current-period navigation check against one table coverage view."""
    rows = list(source_rows)
    all_rows_by_uid: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for source_row in rows:
        all_rows_by_uid[str(source_row.get("internal_table_uid") or "")].append(source_row)
    first_stage: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
    failed = Counter()
    for source_row in rows:
        uid = str(source_row.get("internal_table_uid") or "")
        candidate, checks = _current_header_candidate(
            row=source_row,
            operand=operand,
            table=tables.get(uid),
            context=contexts.get(uid),
            minimum_row_jaccard=minimum_row_jaccard,
        )
        if candidate is None:
            failed.update(name for name, passed in checks.items() if passed is False)
        else:
            first_stage.append((candidate, source_row))
    accepted: dict[tuple[str, int, int], dict[str, Any]] = {}
    second_failed = Counter()
    for candidate, source_row in first_stage:
        uid = str(candidate["internal_table_uid"])
        final, checks = _current_header_row_margin_candidate(
            candidate=candidate,
            rows=_row_candidates(all_rows_by_uid[uid], table_uid=uid),
            table=tables.get(uid),
            context=contexts.get(uid),
            minimum_row_jaccard=minimum_row_jaccard,
            minimum_row_margin=minimum_row_margin,
        )
        if final is None:
            second_failed.update(name for name, passed in checks.items() if passed is False)
        else:
            accepted[final] = _safe_navigation_descriptor(
                candidate=candidate,
                source_row=source_row,
                context=contexts[uid],
                final=final,
            )
    status = (
        "UNIQUE_CURRENT_PERIOD_RECHECK_CANDIDATE"
        if len(accepted) == 1
        else "MULTIPLE_CURRENT_PERIOD_RECHECK_CANDIDATES"
        if len(accepted) > 1
        else "CURRENT_PERIOD_RECHECK_INCOMPLETE"
    )
    return {
        "operand_status": status,
        "first_stage_current_header_candidate_count": len(first_stage),
        "strict_source_candidate_count": len(accepted),
        "navigation_candidates": [accepted[key] for key in sorted(accepted)],
        "first_stage_failed_gate_counts": dict(sorted(failed.items())),
        "second_stage_failed_gate_counts": dict(sorted(second_failed.items())),
    }


def build_staged_formula_current_period_probe(
    *,
    plans_path: Path,
    targets_path: Path,
    candidate_artifact_dir: Path,
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
    candidate_manifest = _read_json(candidate_artifact_dir / "manifest.json")
    queue_path = candidate_artifact_dir / "row_review_queue_v1.jsonl"
    descriptor = (candidate_manifest.get("outputs") or {}).get(queue_path.name) or {}
    if sha256_file(queue_path) != descriptor.get("sha256"):
        raise ValueError("full-corpus row queue does not match its manifest")
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    targets = {int(row["question_id"]) for row in _read_jsonl(targets_path)}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(plans) != expected_ids or not targets <= expected_ids:
        raise ValueError("plans or targets do not match the expected population")
    if any(plans[question_id].get("decomposition_status") != "typed_non_executable" for question_id in targets):
        raise ValueError("current-period probe targets must be typed staged plans")
    tables = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(evidence_context_path)}
    raw_by_operand: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(queue_path):
        key = (int(row.get("question_id") or 0), str(row.get("operand_id") or ""))
        if key[0] in targets:
            raw_by_operand[key].append(row)

    audit: list[dict[str, Any]] = []
    for question_id in sorted(targets):
        for operand in plans[question_id].get("operands") or []:
            operand_id = str(operand.get("operand_id") or "")
            source_rows = raw_by_operand[(question_id, operand_id)]
            all_rows_by_uid: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for source_row in source_rows:
                all_rows_by_uid[str(source_row.get("internal_table_uid") or "")].append(source_row)
            first_stage: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
            failed = Counter()
            for source_row in source_rows:
                uid = str(source_row.get("internal_table_uid") or "")
                candidate, checks = _current_header_candidate(
                    row=source_row,
                    operand=operand,
                    table=tables.get(uid),
                    context=contexts.get(uid),
                    minimum_row_jaccard=minimum_row_jaccard,
                )
                if candidate is None:
                    failed.update(name for name, passed in checks.items() if passed is False)
                else:
                    first_stage.append((candidate, source_row))
            accepted: dict[tuple[str, int, int], dict[str, Any]] = {}
            second_failed = Counter()
            for candidate, source_row in first_stage:
                uid = str(candidate["internal_table_uid"])
                final, checks = _current_header_row_margin_candidate(
                    candidate=candidate,
                    rows=_row_candidates(all_rows_by_uid[uid], table_uid=uid),
                    table=tables.get(uid),
                    context=contexts.get(uid),
                    minimum_row_jaccard=minimum_row_jaccard,
                    minimum_row_margin=minimum_row_margin,
                )
                if final is None:
                    second_failed.update(name for name, passed in checks.items() if passed is False)
                else:
                    accepted[final] = _safe_navigation_descriptor(
                        candidate=candidate,
                        source_row=source_row,
                        context=contexts[uid],
                        final=final,
                    )
            status = (
                "UNIQUE_CURRENT_PERIOD_RECHECK_CANDIDATE"
                if len(accepted) == 1
                else "MULTIPLE_CURRENT_PERIOD_RECHECK_CANDIDATES"
                if len(accepted) > 1
                else "CURRENT_PERIOD_RECHECK_INCOMPLETE"
            )
            record = {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "operand_id": operand_id,
                "requested_years": list(operand.get("years") or []),
                "operand_status": status,
                "first_stage_current_header_candidate_count": len(first_stage),
                "strict_source_candidate_count": len(accepted),
                "navigation_candidates": [accepted[key] for key in sorted(accepted)],
                "first_stage_failed_gate_counts": dict(sorted(failed.items())),
                "second_stage_failed_gate_counts": dict(sorted(second_failed.items())),
                "raw_numeric_values_included": False,
                "source_contract": dict(CONTRACT),
            }
            if _contains_forbidden(record):
                raise ValueError("current-period probe leaked a forbidden field")
            audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(targets),
        "operand_count": len(audit),
        "operand_status_counts": dict(sorted(Counter(row["operand_status"] for row in audit).items())),
        "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin},
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "current_period_operand_probe_v1.jsonl"
        summary_path = temporary / "current_period_probe_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "plans": plans_path,
            "targets": targets_path,
            "candidate_manifest": candidate_artifact_dir / "manifest.json",
            "row_review_queue": queue_path,
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


def validate_staged_formula_current_period_probe(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected current-period probe contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("current-period probe hash mismatch")
    audit = _read_jsonl(artifact_dir / "current_period_operand_probe_v1.jsonl")
    target_path = Path(str(((manifest.get("inputs") or {}).get("targets") or {}).get("path") or ""))
    target_ids = {int(row["question_id"]) for row in _read_jsonl(target_path)}
    if not target_ids <= set(range(1, expected_question_count + 1)) or {int(row["question_id"]) for row in audit} != target_ids:
        raise ValueError("current-period probe target coverage mismatch")
    if any(_contains_forbidden(row) or row.get("raw_numeric_values_included") is not False or row.get("source_contract") != CONTRACT for row in audit):
        raise ValueError("current-period probe lost its non-authorizing boundary")
    summary = _read_json(artifact_dir / "current_period_probe_summary_v1.json")
    counts = dict(sorted(Counter(row["operand_status"] for row in audit).items()))
    if summary.get("operand_count") != len(audit) or summary.get("operand_status_counts") != counts:
        raise ValueError("current-period probe summary mismatch")
    return {
        "status": "PASS",
        "target_question_count": len(target_ids),
        "operand_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
