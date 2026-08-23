"""Recover generic current-period columns from exact, hash-bound source titles.

The V1 period enumerator intentionally requires a literal year in a canonical
header.  Some Vietnamese statements instead print ``Năm nay`` or
``Số cuối năm`` while the exact reporting date appears only in the source
title.  This module adds a narrow V2 overlay for that layout.  It never changes
an existing candidate and remains research-only/non-promotable.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Mapping

from .exact_cell_bindings import parse_vietnamese_numeric_candidate
from .financial_taxonomy import normalize_label


PROTOCOL = "period_column_candidate_packets_v2_exact_source_title"
RECOVERY_METHOD = "v2_exact_source_title_current_header_v1"
_NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_VIETNAMESE_DATE_RE = re.compile(
    r"(?:ngay\s+)?(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s*(?:[|·:]\s*)?(\d{4})"
)
_GENERIC_ENTITY_TOKENS = frozenset(
    {
        "bao", "cao", "bang", "can", "chinh", "cho", "cong", "cua", "cuoi",
        "cung", "doanh", "doan", "don", "dong", "giai", "hoat", "ket", "kinh", "me",
        "nam", "ngay", "nhuan", "nuoc", "phan", "phat", "qua", "rieng", "tai",
        "tap", "thang", "thue", "tinh", "tong", "trong", "truoc", "ty", "vao",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_column": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        "".join(character for character in normalized if not unicodedata.combining(character)).split()
    )


def _dates_in_text(value: object) -> list[date]:
    folded = _fold(value)
    values: list[date] = []
    for pattern in (_NUMERIC_DATE_RE, _VIETNAMESE_DATE_RE):
        for day, month, year in pattern.findall(folded):
            try:
                parsed = date(int(year), int(month), int(day))
            except ValueError:
                continue
            if parsed not in values:
                values.append(parsed)
    return values


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _index(rows: list[dict[str, Any]], field: str, label: str) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for row in rows:
        value = row.get(field)
        if value in result or value in {None, ""}:
            raise ValueError(f"{label} requires unique {field}")
        result[value] = row
    return result


def _require_hash(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def _distinctive_entity_overlap(question: str, source_title: str) -> list[str]:
    # Use the project's Vietnamese fold so ``đ`` remains part of the word
    # (plain NFKD plus an ASCII regex would turn ``động`` into the false token
    # ``ong`` and could accidentally corroborate unrelated issuers).
    normalized_question = normalize_label(question)
    normalized_title = normalize_label(source_title)
    question_tokens = {
        token for token in re.findall(r"[a-z0-9]+", normalized_question)
        if len(token) >= 3 and token not in _GENERIC_ENTITY_TOKENS and not token.isdigit()
    }
    title_tokens = {
        token for token in re.findall(r"[a-z0-9]+", normalized_title)
        if len(token) >= 3 and token not in _GENERIC_ENTITY_TOKENS and not token.isdigit()
    }
    return sorted(question_tokens.intersection(title_tokens))


def _entity_corroboration(
    *, question: str, entities: object, source_title: str
) -> dict[str, Any] | None:
    folded_question = _fold(question)
    literal_entities = [
        str(entity) for entity in entities if str(entity).strip()
    ] if isinstance(entities, list) else []
    literal_matches = [
        entity for entity in literal_entities
        if re.search(rf"(?<![a-z0-9]){re.escape(_fold(entity))}(?![a-z0-9])", folded_question)
    ]
    overlap = _distinctive_entity_overlap(question, source_title)
    if not literal_matches and not overlap:
        return None
    return {
        "literal_route_entities_in_question": literal_matches,
        "distinctive_question_title_tokens": overlap,
    }


def _context_header(context: Mapping[str, Any], column_index: int) -> dict[str, Any] | None:
    matches = [
        dict(column)
        for column in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(column, Mapping) and column.get("column_index") == column_index
    ]
    return matches[0] if len(matches) == 1 else None


def _recover_candidate(
    *, packet: Mapping[str, Any], base_operand: Mapping[str, Any], route_operand: Mapping[str, Any],
    table: Mapping[str, Any], context: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    if base_operand.get("column_status") != "no_period_column":
        return None, "BASE_OPERAND_NOT_NO_PERIOD_COLUMN"
    navigation = list(route_operand.get("navigation_candidates") or [])
    if len(navigation) != 1:
        return None, "NAVIGATION_CANDIDATE_NOT_UNIQUE"
    nav = navigation[0]
    if nav.get("internal_table_uid") != table.get("internal_table_uid"):
        return None, "NAVIGATION_TABLE_MISMATCH"
    if table.get("internal_table_uid") != context.get("internal_table_uid"):
        return None, "CONTEXT_TABLE_MISMATCH"
    if table.get("document_id") != context.get("document_id"):
        return None, "CONTEXT_DOCUMENT_MISMATCH"
    if table.get("source_provenance") != context.get("source_provenance"):
        return None, "CONTEXT_SOURCE_LINEAGE_MISMATCH"
    grid = context.get("grid") or {}
    if grid.get("rectangular") is not True or grid.get("provenance_complete") is not True:
        return None, "CONTEXT_GRID_NOT_REVIEW_READY"
    if (context.get("quality") or {}).get("status") != "review_ready":
        return None, "CONTEXT_QUALITY_NOT_REVIEW_READY"

    years = list((packet.get("question_context") or {}).get("years") or [])
    if len(years) != 1 or isinstance(years[0], bool):
        return None, "REQUESTED_YEAR_NOT_UNIQUE"
    try:
        requested_year = int(years[0])
    except (TypeError, ValueError):
        return None, "REQUESTED_YEAR_INVALID"
    question = str(packet.get("question") or "")
    source_title = str((context.get("context_trace") or {}).get("source_title") or "")
    corroboration = _entity_corroboration(
        question=question,
        entities=(packet.get("question_context") or {}).get("entities") or [],
        source_title=source_title,
    )
    if corroboration is None:
        return None, "QUESTION_SOURCE_ENTITY_NOT_CORROBORATED"

    period_type = str(route_operand.get("period_type") or "")
    table_function = str((context.get("table_function") or {}).get("kind") or "")
    title_dates = [value for value in _dates_in_text(source_title) if value.year == requested_year]
    if len(title_dates) != 1:
        return None, "SOURCE_TITLE_PERIOD_DATE_NOT_UNIQUE"
    source_date = title_dates[0]
    folded_title = _fold(source_title)
    if period_type == "duration":
        if table_function not in {"income_statement", "cash_flow_statement"}:
            return None, "DURATION_TABLE_FUNCTION_MISMATCH"
        if not re.search(r"(?:cho|trong)\s+nam(?:\s+tai\s+chinh)?\s+ket\s+thuc\s+ngay", folded_title):
            return None, "DURATION_SOURCE_TITLE_MISSING"
        expected_header = "nam nay"
    elif period_type == "instant":
        if table_function != "balance_sheet":
            return None, "INSTANT_TABLE_FUNCTION_MISMATCH"
        question_dates = _dates_in_text(question)
        if question_dates and question_dates != [source_date]:
            return None, "QUESTION_SOURCE_DATE_MISMATCH"
        if not question_dates and source_date.month != 12:
            return None, "NON_YEAR_END_DATE_NOT_EXPLICIT_IN_QUESTION"
        expected_header = "so cuoi nam"
    else:
        return None, "UNSUPPORTED_PERIOD_TYPE"

    row_index = nav.get("row_index")
    if not isinstance(row_index, int):
        return None, "NAVIGATION_ROW_INVALID"
    profiles = [
        profile for profile in context.get("row_profiles") or []
        if isinstance(profile, Mapping) and profile.get("row_index") == row_index
    ]
    if len(profiles) != 1:
        return None, "ROW_PROFILE_NOT_UNIQUE"
    profile = profiles[0]
    numeric_columns = {int(value) for value in profile.get("numeric_columns") or []}
    unreliable_columns = {int(value) for value in profile.get("unreliable_numeric_columns") or []}
    matching_headers = [
        dict(column)
        for column in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(column, Mapping)
        and normalize_label(column.get("source_label") or "") == expected_header
        and column.get("role") == "value_or_text"
    ]
    if len(matching_headers) != 1:
        return None, "CURRENT_PERIOD_HEADER_NOT_UNIQUE"
    header = matching_headers[0]
    column_index = header.get("column_index")
    if not isinstance(column_index, int):
        return None, "CURRENT_PERIOD_COLUMN_INVALID"
    if column_index not in numeric_columns or column_index in unreliable_columns:
        return None, "CURRENT_PERIOD_CELL_NOT_RELIABLE_NUMERIC"
    rows = table.get("rows") or []
    provenance = table.get("cell_provenance") or []
    if row_index >= len(rows) or row_index >= len(provenance):
        return None, "CURRENT_PERIOD_ROW_OUT_OF_RANGE"
    if column_index >= len(rows[row_index]) or column_index >= len(provenance[row_index]):
        return None, "CURRENT_PERIOD_COLUMN_OUT_OF_RANGE"
    raw_source_cell = str(rows[row_index][column_index])
    parsed_status, _decimal, _policy = parse_vietnamese_numeric_candidate(raw_source_cell)
    if parsed_status != "parsed_decimal_candidate":
        return None, "CURRENT_PERIOD_CELL_NOT_PARSEABLE_DECIMAL"
    header_cells = list(header.get("header_source_cells") or [])
    if not header_cells:
        return None, "CURRENT_PERIOD_HEADER_ANCHOR_MISSING"
    if _context_header(context, column_index) != header:
        return None, "CURRENT_PERIOD_HEADER_CONTEXT_MISMATCH"

    candidate = {
        "internal_table_uid": str(table["internal_table_uid"]),
        "row_index": row_index,
        "column_index": column_index,
        "requested_year": requested_year,
        "source_label": str(header.get("source_label") or ""),
        "header_source_cells": header_cells,
        "period_labels": [str(requested_year)],
        "unit_labels": list(header.get("unit_labels") or []),
        "raw_source_cell": raw_source_cell,
        "cell_provenance": dict(provenance[row_index][column_index]),
        "period_resolution_method": RECOVERY_METHOD,
        "period_source_title_sha256": hashlib.sha256(source_title.encode("utf-8")).hexdigest(),
        "period_source_date": source_date.isoformat(),
        "period_entity_corroboration": corroboration,
        "reason_codes": [
            "EXACT_REQUESTED_YEAR_IN_SOURCE_TITLE",
            "CURRENT_PERIOD_HEADER_SEMANTICS",
            "QUESTION_SOURCE_ENTITY_CORROBORATED",
        ],
        "source_contract": _contract(),
    }
    return candidate, "RECOVERED_EXACT_SOURCE_TITLE_CURRENT_HEADER"


def _route_operand(route_packet: Mapping[str, Any], stage_id: str, operand: Mapping[str, Any]) -> dict[str, Any]:
    stages = [stage for stage in route_packet.get("stages") or [] if stage.get("stage_id") == stage_id]
    if len(stages) != 1:
        raise ValueError(f"Route stage not unique for Q{route_packet.get('question_id')} {stage_id}")
    matches = [
        value for value in stages[0].get("required_operands") or []
        if value.get("role") == operand.get("role") and value.get("concept_id") == operand.get("concept_id")
    ]
    if len(matches) != 1:
        raise ValueError(f"Route operand not unique for Q{route_packet.get('question_id')} {stage_id}")
    return dict(matches[0])


def materialize_period_title_recovery(
    *, base_packets: Path, base_manifest: Path, route_packets: Path,
    structured_tables: Path, evidence_context: Path, output: Path, audit_output: Path,
) -> dict[str, Any]:
    """Create a hash-bound V2 packet set plus a complete recovery audit."""
    manifest = json.loads(base_manifest.read_text(encoding="utf-8"))
    _require_hash(base_packets, ((manifest.get("outputs") or {}).get("period_packets") or {}).get("sha256"), "base period packets")
    inputs = manifest.get("inputs") or {}
    _require_hash(route_packets, (inputs.get("route_packets") or {}).get("sha256"), "route packets")
    _require_hash(structured_tables, (inputs.get("structured_tables_v2") or {}).get("sha256"), "structured tables")
    _require_hash(evidence_context, (inputs.get("evidence_context_v3") or {}).get("sha256"), "evidence context")

    base_rows = _rows(base_packets)
    routes = _index(_rows(route_packets), "question_id", "route packets")
    tables = _index(_rows(structured_tables), "internal_table_uid", "structured tables")
    contexts = _index(_rows(evidence_context), "internal_table_uid", "evidence context")
    if [row.get("question_id") for row in base_rows] != sorted(routes):
        raise ValueError("Base/route question coverage mismatch")

    output_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    recovery_reasons: Counter[str] = Counter()
    recovered_question_ids: list[int] = []
    for base in base_rows:
        packet = deepcopy(base)
        question_id = int(packet["question_id"])
        route_packet = routes[question_id]
        packet["schema_version"] = 2
        packet["protocol"] = PROTOCOL
        recovered_in_packet = 0
        for stage in packet.get("stages") or []:
            stage_id = str(stage.get("stage_id") or "")
            for operand in stage.get("required_operands") or []:
                if operand.get("column_status") != "no_period_column":
                    continue
                route_operand = _route_operand(route_packet, stage_id, operand)
                navigation = list(route_operand.get("navigation_candidates") or [])
                candidate = None
                reason = "NAVIGATION_CANDIDATE_NOT_UNIQUE"
                if len(navigation) == 1:
                    uid = str(navigation[0].get("internal_table_uid") or "")
                    if uid not in tables or uid not in contexts:
                        reason = "NAVIGATION_SOURCE_NOT_FOUND"
                    else:
                        candidate, reason = _recover_candidate(
                            packet=route_packet,
                            base_operand=operand,
                            route_operand=route_operand,
                            table=tables[uid],
                            context=contexts[uid],
                        )
                recovery_reasons[reason] += 1
                audit_rows.append({
                    "schema_version": 1,
                    "protocol": "period_title_recovery_audit_v1",
                    "question_id": question_id,
                    "stage_id": stage_id,
                    "role": operand.get("role"),
                    "concept_id": operand.get("concept_id"),
                    "status": "recovered" if candidate is not None else "blocked",
                    "reason_code": reason,
                    "candidate": candidate,
                    "source_contract": _contract(),
                })
                if candidate is not None:
                    operand["period_column_candidates"] = [candidate]
                    operand["period_column_candidate_count"] = 1
                    operand["column_status"] = "unique_period_column_candidate"
                    operand["column_candidate_reason_counts"] = {
                        **dict(operand.get("column_candidate_reason_counts") or {}),
                        "RECOVERED_EXACT_SOURCE_TITLE_CURRENT_HEADER": 1,
                    }
                    recovered_in_packet += 1
        statuses = [
            str(operand.get("column_status") or "")
            for stage in packet.get("stages") or []
            for operand in stage.get("required_operands") or []
        ]
        if statuses and all(status == "unique_period_column_candidate" for status in statuses):
            packet["packet_status"] = "unique_period_column_candidate"
        if recovered_in_packet:
            recovered_question_ids.append(question_id)
        output_rows.append(packet)

    if len(recovered_question_ids) != len(set(recovered_question_ids)):
        raise ValueError("A question recovered more than once; require explicit multi-operand policy")
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    audit_output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in audit_rows),
        encoding="utf-8",
    )
    status_counts = Counter(str(row.get("packet_status") or "") for row in output_rows)
    result = {
        "schema_version": 2,
        "protocol": PROTOCOL,
        "question_count": len(output_rows),
        "recovered_question_count": len(recovered_question_ids),
        "recovered_question_ids": recovered_question_ids,
        "audit_record_count": len(audit_rows),
        "recovery_reason_counts": dict(sorted(recovery_reasons.items())),
        "packet_status_counts": dict(sorted(status_counts.items())),
        "inputs": {
            **inputs,
            "base_period_packets": {"path": str(base_packets), "sha256": sha256_file(base_packets)},
            "base_period_manifest": {"path": str(base_manifest), "sha256": sha256_file(base_manifest)},
        },
        "outputs": {
            "period_packets": {"path": str(output), "sha256": sha256_file(output)},
            "recovery_audit": {"path": str(audit_output), "sha256": sha256_file(audit_output)},
        },
        "source_contract": _contract(),
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_column": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
