"""Rebuild current-period packets from source-title facts, never old answers.

Some OCR tables label their value column only as ``Năm nay`` or ``Số cuối
năm``.  A legacy research candidate can point to a current V2 cell, but this
module treats it only as a navigation hint.  It rechecks the V2/V3 table,
header, source title, date, unit and numeric-cell reliability before emitting
a candidate-only period packet.  It never copies values, formulas, approvals,
or answer fields from the legacy record.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit, sha_json
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_source_title_period_recheck_v1"
TARGET_BLOCKER = "PERIOD_HEADER_NOT_EXTRACTED"
PERIOD_PROTOCOL = "period_column_candidate_packets_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
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
FORBIDDEN = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "raw_source_cell",
        "raw_source_row",
        "rows",
        "raw_decimal_candidate",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
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


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join("".join(char for char in normalized if not unicodedata.combining(char)).split())


_NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_VIETNAMESE_DATE_RE = re.compile(r"(?:ngay\s+)?(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s*(?:[|·:]\s*)?(\d{4})")
_DOCUMENT_PERIOD_DATE_RE = re.compile(
    r"(?:cho\s+nam(?:\s+tai\s+chinh)?\s+ket\s+thuc\s+ngay|tai\s+ngay)\s+"
    r"(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s*(?:[|·:]\s*)?(\d{4})"
)


def _dates_in_text(value: object) -> list[date]:
    values: list[date] = []
    for pattern in (_NUMERIC_DATE_RE, _VIETNAMESE_DATE_RE):
        for day, month, year in pattern.findall(_fold(value)):
            try:
                parsed = date(int(year), int(month), int(day))
            except ValueError:
                continue
            if parsed not in values:
                values.append(parsed)
    return values


def _document_header_period_marker(
    *,
    table: Mapping[str, Any],
    requested_year: int,
) -> dict[str, Any] | None:
    """Recover a report-period date from a bounded, hash-bound file prefix.

    A few OCR tables repeat only ``Số cuối năm``/``Số đầu năm`` and omit the
    report year from their local title.  This helper reads no data rows and
    never uses the filename as evidence: it accepts only a printed Vietnamese
    period phrase in the source prefix preceding the table, with the source
    SHA-256 and prefix extent recorded for replay.
    """
    provenance = table.get("source_provenance") or {}
    source_path = Path(str(provenance.get("source_path") or ""))
    expected_source_sha = str(provenance.get("source_sha256") or "")
    if not source_path.is_file() or not expected_source_sha:
        return None
    try:
        source_bytes = source_path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(source_bytes).hexdigest() != expected_source_sha:
        return None
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        source_text = source_bytes.decode("utf-8", errors="replace")
    try:
        table_start = max(0, int(provenance.get("char_start") or 0))
    except (TypeError, ValueError):
        table_start = 0
    prefix_end = min(table_start, 20000) if table_start else min(len(source_text), 20000)
    prefix = source_text[:prefix_end]
    folded_prefix = _fold(prefix)
    dates: list[date] = []
    for day, month, year in _DOCUMENT_PERIOD_DATE_RE.findall(folded_prefix):
        try:
            parsed = date(int(year), int(month), int(day))
        except ValueError:
            continue
        if parsed not in dates:
            dates.append(parsed)
    matching = [value for value in dates if value.year == requested_year]
    if len(matching) != 1:
        return None
    return {
        "protocol": "v2_document_header_period_marker_v1",
        "document_id": table.get("document_id"),
        "internal_table_uid": table.get("internal_table_uid"),
        "source_sha256": expected_source_sha,
        "source_prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
        "source_prefix_char_end": prefix_end,
        "source_date": matching[0].isoformat(),
    }


def _aligned(table: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
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


def _source_contract_is_non_authorizing(record: Mapping[str, Any]) -> bool:
    contract = record.get("source_contract") or {}
    return bool(
        contract.get("research_only") is True
        and contract.get("evidence_eligible") is False
        and contract.get("may_materialize_answer") is False
        and contract.get("submission_eligible") is False
    )


def _title_unit_marker(*, table: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any] | None:
    title = str(((context.get("context_trace") or {}).get("source_title")) or "")
    unit, _, multiplier = resolve_source_unit([{"raw_source_cell": title}])
    source = table.get("source_provenance") or {}
    if not title or unit is None or multiplier is None:
        return None
    return {
        "protocol": PROTOCOL,
        "document_id": table.get("document_id"),
        "internal_table_uid": table.get("internal_table_uid"),
        "source_sha256": source.get("source_sha256"),
        "table_sha256": source.get("table_sha256"),
        "evidence_context_row_sha256": sha_json(context),
        "source_title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest(),
        "source_unit": unit,
        "source_to_vnd_multiplier": format(multiplier, "f"),
        "raw_unit_label": unit,
    }


def _header_period_years(header: Mapping[str, Any], *, extra_values: Iterable[object] = ()) -> set[int]:
    """Return years explicitly printed in one V3 header column.

    ``period_labels`` is the structured representation, while ``source_label``
    is retained as a second check because OCR occasionally leaves the year in
    the label but drops it from the extracted period list.  We intentionally
    do not infer a year from a filename or from a neighbouring column.
    """
    values = [str(value) for value in header.get("period_labels") or []]
    values.append(str(header.get("source_label") or ""))
    values.extend(str(value) for value in extra_values)
    years: set[int] = set()
    for value in values:
        for match in re.finditer(r"(?<!\d)(?:19|20)\d{2}(?!\d)", _fold(value)):
            years.add(int(match.group(0)))
    return years


def _augment_header_source_cells(
    *,
    table: Mapping[str, Any],
    header: Mapping[str, Any],
    column_index: int,
    value_row_index: int,
) -> list[dict[str, int]]:
    """Recover a directly adjacent OCR header row omitted by V3.

    Some two-line headers are classified by V3 using only the first row
    (``Tại ngày``), while the second row carries the actual date and unit.
    We add only adjacent, provenance-backed cells that visibly contain a date,
    year, or unit; no neighbouring data row is searched.
    """
    source_rows = table.get("rows") or []
    provenance = table.get("cell_provenance") or []
    coordinates: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for item in header.get("header_source_cells") or []:
        if not isinstance(item, Mapping):
            continue
        row_index, item_column = item.get("row_index"), item.get("column_index")
        if not isinstance(row_index, int) or not isinstance(item_column, int):
            continue
        if (row_index, item_column) in seen:
            continue
        if (
            row_index < 0
            or item_column < 0
            or row_index >= len(source_rows)
            or row_index >= len(provenance)
            or item_column >= len(source_rows[row_index])
            or item_column >= len(provenance[row_index])
            or not isinstance(provenance[row_index][item_column], Mapping)
        ):
            continue
        coordinates.append({"row_index": row_index, "column_index": item_column})
        seen.add((row_index, item_column))
    start = max((item["row_index"] for item in coordinates), default=-1) + 1
    stop = min(value_row_index, start + 2)
    for row_index in range(start, stop):
        if row_index < 0 or row_index >= len(source_rows) or row_index >= len(provenance):
            continue
        if column_index < 0 or column_index >= len(source_rows[row_index]) or column_index >= len(provenance[row_index]):
            continue
        raw = str(source_rows[row_index][column_index])
        has_date_or_year = bool(_dates_in_text(raw) or re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", _fold(raw)))
        has_unit = resolve_source_unit([{"raw_source_cell": raw}])[2] is not None
        if (has_date_or_year or has_unit) and (row_index, column_index) not in seen and isinstance(provenance[row_index][column_index], Mapping):
            coordinates.append({"row_index": row_index, "column_index": column_index})
            seen.add((row_index, column_index))
    return coordinates


def _header_unit_marker(
    *,
    table: Mapping[str, Any],
    header: Mapping[str, Any],
    source_rows: list[list[Any]],
    provenance: list[list[Any]],
) -> dict[str, Any] | None:
    """Resolve a unit from the selected header's own provenance cells.

    This is deliberately narrower than the execution-time table-wide unit
    fallback.  A source-title repair may use the title marker, or this exact
    header marker, but never a data row or another table.
    """
    anchors: list[dict[str, Any]] = []
    for coordinate in header.get("header_source_cells") or []:
        if not isinstance(coordinate, Mapping):
            continue
        row_index = coordinate.get("row_index")
        column_index = coordinate.get("column_index")
        if (
            not isinstance(row_index, int)
            or not isinstance(column_index, int)
            or row_index < 0
            or column_index < 0
            or row_index >= len(source_rows)
            or row_index >= len(provenance)
            or column_index >= len(source_rows[row_index])
            or column_index >= len(provenance[row_index])
            or not isinstance(provenance[row_index][column_index], Mapping)
        ):
            continue
        anchors.append(
            {
                "row_index": row_index,
                "column_index": column_index,
                "raw_source_cell": str(source_rows[row_index][column_index]),
                "cell_provenance": provenance[row_index][column_index],
            }
        )
    unit, _, multiplier = resolve_source_unit(anchors)
    if unit is None or multiplier is None:
        return None
    source = table.get("source_provenance") or {}
    return {
        "protocol": PROTOCOL,
        "document_id": table.get("document_id"),
        "internal_table_uid": table.get("internal_table_uid"),
        "source_sha256": source.get("source_sha256"),
        "table_sha256": source.get("table_sha256"),
        "header_source_cells": [
            {"row_index": anchor["row_index"], "column_index": anchor["column_index"]}
            for anchor in anchors
        ],
        "evidence_context_row_sha256": sha_json(header),
        "source_unit": unit,
        "source_to_vnd_multiplier": format(multiplier, "f"),
    }


def _table_function_matches(
    *,
    table_function: str,
    expected_types: set[str],
    discovery: Mapping[str, Any],
) -> bool:
    """Apply only an explicitly declared, narrow table-family alias."""
    if table_function in expected_types:
        return True
    alias = str(discovery.get("expected_table_type_alias") or "")
    if alias == "notes":
        return "notes" in expected_types and table_function in {"financial_note", "financial_note_detail"}
    if alias == "income_statement_schedule":
        return "income_statement" in expected_types and table_function == "financial_data_schedule"
    if alias == "balance_sheet_schedule":
        return "balance_sheet" in expected_types and table_function == "financial_data_schedule"
    if alias == "balance_sheet_note":
        return "balance_sheet" in expected_types and table_function == "financial_note"
    if alias == "related_party_schedule":
        return "notes" in expected_types and table_function == "related_party_schedule"
    return False


def _candidate(
    *,
    discovery: Mapping[str, Any],
    packet: Mapping[str, Any],
    route: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    cells = discovery.get("source_value_cells") or []
    checks: dict[str, bool] = {
        "legacy_record_non_authorizing": _source_contract_is_non_authorizing(discovery),
        "exactly_one_discovery_value_cell": isinstance(cells, list) and len(cells) == 1 and isinstance(cells[0], Mapping),
        "route_complete": route.get("route_status") == "route_complete",
        "v2_v3_table_present": table is not None and context is not None,
    }
    if not all(checks.values()) or table is None or context is None:
        return None, checks
    cell = cells[0]
    row_index = _int(cell.get("row_index"), label="discovery row index")
    column_index = _int(cell.get("column_index"), label="discovery column index")
    checks["discovery_document_matches_v2"] = cell.get("document_uid") == table.get("document_id")
    checks["discovery_table_matches_v2"] = cell.get("internal_table_uid") == table.get("internal_table_uid")
    checks["v2_v3_source_aligned"] = _aligned(table, context)
    source_rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    checks["value_coordinate_has_v2_provenance"] = bool(
        0 <= row_index < len(source_rows)
        and 0 <= column_index < len(source_rows[row_index])
        and row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    stages = [stage for stage in packet.get("stages") or [] if stage.get("stage_id") == discovery.get("stage_id")]
    checks["one_matching_packet_stage"] = len(stages) == 1
    if not all(checks.values()):
        return None, checks
    operands = stages[0].get("required_operands") or []
    checks["one_matching_packet_operand"] = len(operands) == 1 and isinstance(operands[0], Mapping)
    if not checks["one_matching_packet_operand"]:
        return None, checks
    operand = operands[0]
    headers = [
        header
        for header in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(header, Mapping) and header.get("column_index") == column_index
    ]
    checks["one_v3_header_for_value_column"] = len(headers) == 1
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    checks["selected_cell_reliable_numeric"] = bool(
        len(profiles) == 1
        and column_index in set(profiles[0].get("numeric_columns") or [])
        and column_index not in set(profiles[0].get("unreliable_numeric_columns") or [])
    )
    required_checks = {
        key: value
        for key, value in checks.items()
        if key not in {"current_header_semantics", "explicit_header_period_year"}
    }
    if not all(required_checks.values()):
        return None, checks
    header = headers[0]
    header_cells = [
        {"row_index": _int(item.get("row_index"), label="header row"), "column_index": _int(item.get("column_index"), label="header column")}
        for item in header.get("header_source_cells") or []
        if isinstance(item, Mapping)
    ]
    header_cells = _augment_header_source_cells(
        table=table,
        header=header,
        column_index=column_index,
        value_row_index=row_index,
    )
    checks["header_coordinates_present"] = bool(header_cells)
    checks["header_coordinates_valid"] = bool(
        header_cells
        and all(
            0 <= item["row_index"] < len(source_rows)
            and 0 <= item["column_index"] < len(source_rows[item["row_index"]])
            and item["row_index"] < len(provenance)
            and item["column_index"] < len(provenance[item["row_index"]])
            and isinstance(provenance[item["row_index"]][item["column_index"]], Mapping)
            for item in header_cells
        )
    )
    requested_year = _int(((packet.get("question_context") or {}).get("years") or [None])[0], label="requested year")
    title = str(((context.get("context_trace") or {}).get("source_title")) or "")
    matching_dates = [item for item in _dates_in_text(title) if item.year == requested_year]
    period_recovery_mode = str(discovery.get("period_recovery_mode") or "")
    document_period_marker = (
        _document_header_period_marker(table=table, requested_year=requested_year)
        if period_recovery_mode == "document_header_current"
        else None
    )
    checks["document_header_period"] = (
        document_period_marker is not None
        if period_recovery_mode == "document_header_current"
        else True
    )
    table_function = str(((context.get("table_function") or {}).get("kind")) or "")
    expected_types = set(operand.get("expected_table_types") or [])
    checks["table_function_matches_operand"] = _table_function_matches(
        table_function=table_function,
        expected_types=expected_types,
        discovery=discovery,
    )
    header_raw_values = [
        str(source_rows[item["row_index"]][item["column_index"]])
        for item in header_cells
        if 0 <= item["row_index"] < len(source_rows)
        and 0 <= item["column_index"] < len(source_rows[item["row_index"]])
    ]
    header_years = _header_period_years(header, extra_values=header_raw_values)
    explicit_header_period = header_years == {requested_year}
    checks["explicit_header_period_year"] = explicit_header_period
    checks["source_title_year_not_contradictory"] = not _dates_in_text(title) or bool(matching_dates)
    label = _fold(header.get("source_label"))
    if period_recovery_mode == "document_header_current":
        checks["current_header_semantics"] = label.startswith("so cuoi nam")
    elif table_function == "balance_sheet":
        checks["current_header_semantics"] = label == "so cuoi nam"
    elif table_function in {"income_statement", "cash_flow_statement"}:
        checks["current_header_semantics"] = label == "nam nay" and bool(
            re.search(r"(?:cho|trong)\s+nam(?:\s+tai\s+chinh)?(?:\s+(?:19|20)\d{2})?\s+ket\s+thuc\s+(?:tai\s+)?ngay", _fold(title))
        )
    else:
        checks["current_header_semantics"] = False
    title_marker = _title_unit_marker(table=table, context=context)
    header_for_facts = dict(header)
    header_for_facts["header_source_cells"] = header_cells
    header_marker = _header_unit_marker(
        table=table,
        header=header_for_facts,
        source_rows=source_rows,
        provenance=provenance,
    )
    checks["unique_unit_in_source_title_or_header"] = title_marker is not None or header_marker is not None
    # A current-only header still needs the source-title date and current
    # semantics.  An explicitly dated/year-labelled header can stand on its
    # own; the title is only required not to contradict it.
    checks["one_requested_year_date_in_source_title"] = (
        checks["document_header_period"]
        if period_recovery_mode == "document_header_current"
        else (len(matching_dates) == 1 if not explicit_header_period else checks["source_title_year_not_contradictory"])
    )
    checks["current_header_or_explicit_period"] = (
        (checks["current_header_semantics"] and checks["document_header_period"])
        if period_recovery_mode == "document_header_current"
        else (explicit_header_period or checks["current_header_semantics"])
    )
    required_checks = {
        key: value
        for key, value in checks.items()
        if key not in {"current_header_semantics", "explicit_header_period_year"}
    }
    if not all(required_checks.values()):
        return None, checks
    period_method = (
        "v2_exact_document_header_current_header_v1"
        if period_recovery_mode == "document_header_current"
        else (
            "v2_exact_header_period_v1"
            if explicit_header_period
            else "v2_exact_source_title_current_header_v1"
        )
    )
    period_source_date = (
        str(document_period_marker.get("source_date"))
        if document_period_marker is not None
        else (matching_dates[0].isoformat() if matching_dates else None)
    )
    return {
        "stage_id": discovery.get("stage_id"),
        "role": operand.get("role"),
        "internal_table_uid": table.get("internal_table_uid"),
        "row_index": row_index,
        "column_index": column_index,
        "header_source_cells": header_cells,
        "period_labels": [str(requested_year)],
        "source_label": header.get("source_label"),
        "unit_labels": list(header.get("unit_labels") or []),
        "requested_year": requested_year,
        "period_resolution_method": period_method,
        "period_source_title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest() if title else None,
        "period_source_date": period_source_date,
        "discovery_candidate_sha256": hashlib.sha256(
            json.dumps(
                {
                    "question_id": discovery.get("question_id"),
                    "stage_id": discovery.get("stage_id"),
                    "source_value_cells": [
                        {
                            key: cell.get(key)
                            for key in ("document_uid", "internal_table_uid", "row_index", "column_index", "raw_text_sha256")
                        }
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        **(
            {"period_document_header_marker": document_period_marker}
            if document_period_marker is not None
            else {}
        ),
        **({"unit_source_title_recheck": title_marker} if title_marker is not None else {}),
    }, checks


def _patch_packet(packet: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(packet))
    matches = [stage for stage in output.get("stages") or [] if stage.get("stage_id") == candidate["stage_id"]]
    if len(matches) != 1 or len(matches[0].get("required_operands") or []) != 1:
        raise ValueError("period title recheck packet stage is no longer unique")
    operand = matches[0]["required_operands"][0]
    if operand.get("role") != candidate["role"]:
        raise ValueError("period title recheck operand is stale")
    operand.update(
        {
            "column_status": "unique_period_column_candidate",
            "navigation_row_count": 1,
            "period_column_candidate_count": 1,
            "column_candidate_reason_counts": {
                "V3_SOURCE_TITLE_CURRENT_HEADER_RECHECK_CANDIDATE_ONLY": 1
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
                        "period_resolution_method",
                "period_source_title_sha256",
                "period_source_date",
            )
            }
                | ({"period_document_header_marker": candidate["period_document_header_marker"]}
                   if "period_document_header_marker" in candidate else {})
                | ({"unit_source_title_recheck": candidate["unit_source_title_recheck"]}
                   if "unit_source_title_recheck" in candidate else {})
                | {
                    "reason_codes": ["V3_SOURCE_TITLE_CURRENT_HEADER_RECHECK_CANDIDATE_ONLY"],
                    "source_contract": dict(PERIOD_CONTRACT),
                }
            ],
        }
    )
    output.update(
        {
            "protocol": PERIOD_PROTOCOL,
            "input_packet_status": "machine_source_title_period_recheck_candidate",
            "packet_status": "unique_period_column_candidate",
            "route_status": "route_complete",
            "source_contract": dict(PERIOD_CONTRACT),
        }
    )
    return output


def build_source_title_period_recheck(
    *,
    triage_path: Path,
    base_period_packets_path: Path,
    base_period_manifest_path: Path,
    route_overlay_path: Path,
    route_overlay_manifest_path: Path,
    discovery_candidates_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Create immutable, candidate-only source-title period repairs."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    period_manifest = _read_json(base_period_manifest_path)
    route_manifest = _read_json(route_overlay_manifest_path)
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(base_period_packets_path) != ((period_manifest.get("outputs") or {}).get("period_packets") or {}).get("sha256"):
        raise ValueError("base period packets hash mismatch")
    if sha256_file(route_overlay_path) != ((route_manifest.get("outputs") or {}).get("overlay") or {}).get("sha256"):
        raise ValueError("route overlay hash mismatch")
    if sha256_file(structured_tables_path) != ((period_manifest.get("inputs") or {}).get("structured_tables_v2") or {}).get("sha256"):
        raise ValueError("structured V2 tables hash mismatch")
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    target_ids = {
        _int(row.get("question_id"), label="triage question id")
        for row in _rows(triage_path)
        if row.get("primary_blocker") == TARGET_BLOCKER
    }
    periods = {_int(row.get("question_id"), label="period question id"): row for row in _rows(base_period_packets_path)}
    routes = {_int(row.get("question_id"), label="route question id"): row for row in _rows(route_overlay_path)}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(periods) != expected_ids or set(routes) != expected_ids:
        raise ValueError("period title recheck inputs have incomplete question coverage")
    discoveries_by_question: dict[int, list[dict[str, Any]]] = {}
    for record in _rows(discovery_candidates_path):
        question_id = _int(record.get("question_id"), label="discovery question id")
        if question_id in target_ids:
            discoveries_by_question.setdefault(question_id, []).append(record)
    tables = {str(row.get("internal_table_uid") or ""): row for row in _rows(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _rows(evidence_context_path)}
    revised: dict[int, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for question_id in sorted(target_ids):
        candidates: list[tuple[dict[str, Any], dict[str, bool]]] = []
        for discovery in discoveries_by_question.get(question_id, []):
            cells = discovery.get("source_value_cells") or []
            uid = str(cells[0].get("internal_table_uid") or "") if len(cells) == 1 and isinstance(cells[0], Mapping) else ""
            candidate, checks = _candidate(
                discovery=discovery,
                packet=periods[question_id],
                route=routes[question_id],
                table=tables.get(uid),
                context=contexts.get(uid),
            )
            if candidate is not None:
                candidates.append((candidate, checks))
        unique = len(candidates) == 1
        if unique:
            candidate, checks = candidates[0]
            revised[question_id] = _patch_packet(periods[question_id], candidate)
        else:
            candidate, checks = None, {}
        status = "MATERIALIZED_SOURCE_TITLE_PERIOD_CANDIDATE" if unique else "QUARANTINED_SOURCE_TITLE_PERIOD_CANDIDATE"
        row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "period_recheck_status": status,
            "candidate_count": len(candidates),
            "checks": checks,
            "candidate_navigation": None
            if candidate is None
            else {
                key: candidate[key]
                for key in (
                    "internal_table_uid",
                    "row_index",
                    "column_index",
                    "period_labels",
                    "requested_year",
                    "period_resolution_method",
                    "period_source_title_sha256",
                    "period_source_date",
                    "discovery_candidate_sha256",
                )
            },
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(row):
            raise ValueError("period title recheck audit contains forbidden content")
        audit.append(row)
    output_periods = [revised.get(question_id, periods[question_id]) for question_id in range(1, expected_question_count + 1)]
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(target_ids),
        "materialized_question_count": len(revised),
        "status_counts": dict(sorted(Counter(row["period_recheck_status"] for row in audit).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        period_path = temporary / "period_column_candidate_packets_v1.jsonl"
        audit_path = temporary / "source_title_period_recheck_audit_v1.jsonl"
        summary_path = temporary / "source_title_period_recheck_summary_v1.json"
        _write_jsonl(period_path, output_periods)
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "triage": triage_path,
            "base_period_packets": base_period_packets_path,
            "base_period_manifest": base_period_manifest_path,
            "route_overlay": route_overlay_path,
            "route_overlay_manifest": route_overlay_manifest_path,
            "discovery_candidates": discovery_candidates_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
        }
        manifest = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
            "outputs": {
                "period_packets": {"path": period_path.name, "sha256": sha256_file(period_path)},
                "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
            },
            "source_contract": dict(CONTRACT),
        }
        _write_json(temporary / "period_column_candidate_packets_v1.manifest.json", manifest)
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_source_title_period_recheck(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Validate immutable coverage and that only audited packets changed."""
    manifest = _read_json(artifact_dir / "period_column_candidate_packets_v1.manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected period title recheck protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = (artifact_dir / str(descriptor.get("path") or "")) if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("period title recheck hash mismatch")
    periods = {_int(row.get("question_id"), label="output period question id"): row for row in _rows(artifact_dir / "period_column_candidate_packets_v1.jsonl")}
    audit = _rows(artifact_dir / "source_title_period_recheck_audit_v1.jsonl")
    expected_ids = set(range(1, expected_question_count + 1))
    if set(periods) != expected_ids:
        raise ValueError("period title recheck question coverage mismatch")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        or "human_verified" in row
        for row in audit
    ):
        raise ValueError("period title recheck audit lost non-authorizing boundary")
    materialized = {
        _int(row.get("question_id"), label="audit question id")
        for row in audit
        if row.get("period_recheck_status") == "MATERIALIZED_SOURCE_TITLE_PERIOD_CANDIDATE"
    }
    base_path = Path(str(((manifest.get("inputs") or {}).get("base_period_packets") or {}).get("path") or ""))
    base = {_int(row.get("question_id"), label="base period question id"): row for row in _rows(base_path)}
    for question_id in expected_ids - materialized:
        if sha_json(periods[question_id]) != sha_json(base[question_id]):
            raise ValueError("non-materialized period packet changed")
    for question_id in materialized:
        if periods[question_id].get("packet_status") != "unique_period_column_candidate":
            raise ValueError("materialized source-title period packet is incomplete")
    summary = _read_json(artifact_dir / "source_title_period_recheck_summary_v1.json")
    if summary.get("materialized_question_count") != len(materialized) or summary.get("target_question_count") != len(audit):
        raise ValueError("period title recheck summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "target_question_count": len(audit),
        "materialized_question_count": len(materialized),
        "answer_eligible": False,
        "submission_eligible": False,
    }
