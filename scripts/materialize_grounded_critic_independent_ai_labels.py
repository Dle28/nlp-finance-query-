#!/usr/bin/env python3
"""Materialize fail-closed, blind AI source reviews for critic V2 assignments.

This verifies the immutable packet against the original structured table,
canonical header context, question candidate metadata, and hash-bound raw
statement file.  It deliberately does not read Qwen's decision or write an
answer/value/candidate.  Any failed check aborts without producing labels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.exact_cell_bindings import parse_vietnamese_numeric_candidate
from finance_query.financial_taxonomy import normalize_label


ASSIGNMENT_PROTOCOL = "grounded_critic_independent_review_assignment_v1"
LABEL_PROTOCOL = "grounded_critic_independent_label_v1"
REVIEW_PROTOCOL = "grounded_critic_independent_ai_source_review_v1"
OPERATING_CASH_FLOW = normalize_label("Lưu chuyển tiền thuần từ hoạt động kinh doanh")
ROLE_ROW_MARKERS = {
    "operating_cash_flow": (OPERATING_CASH_FLOW,),
    "cash_and_cash_equivalents": (normalize_label("Tiền và các khoản tương đương tiền"),),
    "share_capital": (normalize_label("Vốn cổ phần"), "411"),
    "liabilities": (normalize_label("Nợ phải trả"), "300"),
    "net_income": (normalize_label("Lợi nhuận sau thuế"), "60"),
    "other_income": (normalize_label("Thu nhập khác"), "31"),
    "gross_revenue": (normalize_label("Doanh thu bán hàng và cung cấp dịch vụ"), "01"),
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_false_contract(contract: object, label: str) -> None:
    if not isinstance(contract, Mapping):
        raise ValueError(f"{label} has no source contract")
    for key in ("evidence_eligible", "training_eligible", "submission_eligible", "promotion_allowed"):
        if contract.get(key) is not False:
            raise ValueError(f"{label} must keep {key}=false")


def source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def resolve_source_path(source_path: object, source_root: Path) -> Path:
    candidate = Path(str(source_path or ""))
    resolved = candidate.resolve() if candidate.is_absolute() else (source_root / candidate).resolve()
    try:
        resolved.relative_to(source_root)
    except ValueError as error:
        raise ValueError("Raw statement source lies outside --source-root") from error
    if not resolved.is_file():
        raise ValueError(f"Raw statement source is missing: {resolved}")
    return resolved


def decimal_from_cell(value: object, *, label: str) -> Decimal:
    parsed, decimal, _policy = parse_vietnamese_numeric_candidate(str(value))
    if parsed != "parsed_decimal_candidate" or decimal is None:
        raise ValueError(f"{label} is not a parseable numeric source cell")
    return Decimal(str(decimal))


def decimal_from_packet(value: object, *, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as error:  # Decimal exposes several exception types.
        raise ValueError(f"{label} is not a decimal") from error


def validate_question(
    *,
    question_id: int,
    context: Mapping[str, Any],
    table_uid: str,
    review_item: Mapping[str, Any],
) -> None:
    question = normalize_label(review_item.get("question") or "")
    entities = [str(value) for value in context.get("entities") or []]
    years = [int(value) for value in context.get("years") or []]
    scope = str(context.get("scope") or "")
    if not question or not entities or not years or not scope:
        raise ValueError(f"Q{question_id}: packet question context is incomplete")
    if any(normalize_label(entity) not in question for entity in entities):
        raise ValueError(f"Q{question_id}: question text does not contain packet entity")
    if any(str(year) not in question for year in years):
        raise ValueError(f"Q{question_id}: question text does not contain packet year")
    candidates = [candidate for candidate in review_item.get("candidates") or [] if candidate.get("internal_table_uid") == table_uid]
    if not candidates:
        raise ValueError(f"Q{question_id}: source table is absent from the original review item")
    # The reporting year names the statement document, not necessarily every
    # column in it. A comparative column can therefore be the exact requested
    # period in a later report. Entity and scope must agree here; the requested
    # year is independently required from the selected header cell, with raw
    # source coordinates, in validate_operand below.
    if not any(
        candidate.get("ticker") in entities
        and candidate.get("scope") == scope
        and candidate.get("ticker_match") is True
        and candidate.get("scope_match") is True
        for candidate in candidates
    ):
        raise ValueError(f"Q{question_id}: source table candidate contradicts packet entity/scope")


def validate_operand(
    *,
    question_id: int,
    packet: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
    source_root: Path,
) -> dict[str, Any]:
    excerpts = packet.get("bounded_source_excerpts") or []
    trace = packet.get("deterministic_execution_trace") or []
    if len(excerpts) != 1 or len(trace) != 1:
        raise ValueError(f"Q{question_id}: this independent source review requires exactly one bounded operand")
    excerpt = excerpts[0]
    trace_row = trace[0]
    uid = str(excerpt.get("internal_table_uid") or "")
    row_index, column_index = excerpt.get("row_index"), excerpt.get("column_index")
    if uid != str(table.get("internal_table_uid") or ""):
        raise ValueError(f"Q{question_id}: structured table UID mismatch")
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        raise ValueError(f"Q{question_id}: source coordinate must be integer")
    rows = table.get("rows") or []
    if row_index < 0 or row_index >= len(rows) or column_index < 0 or column_index >= len(rows[row_index]):
        raise ValueError(f"Q{question_id}: source coordinate is out of bounds")
    role = str(excerpt.get("role") or "")
    markers = ROLE_ROW_MARKERS.get(role)
    if not markers:
        raise ValueError(f"Q{question_id}: unsupported source role {role!r}")
    row_text = "|".join(normalize_label(str(cell)) for cell in rows[row_index][:3])
    if not any(marker in row_text for marker in markers):
        raise ValueError(f"Q{question_id}: {role} row label mismatch")
    observed = decimal_from_cell(rows[row_index][column_index], label=f"Q{question_id} source cell")
    raw_expected = decimal_from_packet(excerpt.get("raw_value_decimal"), label=f"Q{question_id} bounded value")
    if observed != raw_expected:
        raise ValueError(f"Q{question_id}: bounded raw value contradicts the source cell")
    sources = trace_row.get("operand_sources") or []
    if len(sources) != 1:
        raise ValueError(f"Q{question_id}: trace must contain exactly one source")
    source = sources[0]
    for key in ("internal_table_uid", "row_index", "column_index", "role"):
        if source.get(key) != excerpt.get(key):
            raise ValueError(f"Q{question_id}: trace source differs from the bounded excerpt")
    base_value = decimal_from_packet(source.get("base_vnd_value_decimal"), label=f"Q{question_id} base VND value")
    multiplier = decimal_from_packet(source.get("source_to_vnd_multiplier"), label=f"Q{question_id} VND multiplier")
    if observed * multiplier != base_value:
        raise ValueError(f"Q{question_id}: source-unit conversion contradicts base VND trace")
    requested = trace_row.get("requested_output_unit") or {}
    divisor = decimal_from_packet(requested.get("vnd_to_output_divisor"), label=f"Q{question_id} output divisor")
    converted = decimal_from_packet(trace_row.get("converted_output_decimal"), label=f"Q{question_id} converted trace value")
    if base_value / divisor != converted:
        raise ValueError(f"Q{question_id}: deterministic output conversion does not replay")
    columns = ((context.get("canonical_headers") or {}).get("columns") or [])
    by_index = {column.get("column_index"): column for column in columns}
    header = by_index.get(column_index)
    question_context = packet.get("question_context") or {}
    years = {str(year) for year in question_context.get("years") or []}
    period_labels = {str(value) for value in (header.get("period_labels") or [])} if isinstance(header, Mapping) else set()
    period_years = {label[-4:] for label in period_labels if len(label) >= 4 and label[-4:].isdigit()}
    if not isinstance(header, Mapping) or not years.intersection(period_labels | period_years):
        raise ValueError(f"Q{question_id}: source column does not carry the packet period")
    header_units = [str(unit) for unit in (header.get("unit_labels") or [])]
    table_unit_anchors: list[dict[str, Any]] = []
    if not any("VND" in unit.upper() for unit in header_units):
        table_unit_anchors = [
            dict(source_cell)
            for candidate_header in columns
            if any("VND" in str(unit).upper() for unit in (candidate_header.get("unit_labels") or []))
            for source_cell in (candidate_header.get("header_source_cells") or [])
            if isinstance(source_cell, Mapping)
        ]
        all_header_units = [str(unit) for candidate_header in columns for unit in (candidate_header.get("unit_labels") or [])]
        if not any("VND" in unit.upper() for unit in all_header_units):
            raise ValueError(f"Q{question_id}: source table does not carry VND unit provenance")
        if not table_unit_anchors:
            raise ValueError(f"Q{question_id}: table-level VND provenance has no header coordinates")
        for unit_anchor in table_unit_anchors:
            unit_row, unit_column = unit_anchor.get("row_index"), unit_anchor.get("column_index")
            if not isinstance(unit_row, int) or not isinstance(unit_column, int):
                raise ValueError(f"Q{question_id}: table-level VND provenance has invalid coordinates")
            if unit_row < 0 or unit_row >= len(rows) or unit_column < 0 or unit_column >= len(rows[unit_row]):
                raise ValueError(f"Q{question_id}: table-level VND provenance is out of bounds")
            if "VND" not in str(rows[unit_row][unit_column]).upper():
                raise ValueError(f"Q{question_id}: table-level VND provenance contradicts raw header cell")
    provenance = table.get("source_provenance") or {}
    raw_path = resolve_source_path(provenance.get("source_path"), source_root)
    raw_sha = require_hash(raw_path, provenance.get("source_sha256"), f"Q{question_id} raw statement")
    char_start = provenance.get("char_start")
    if not isinstance(char_start, int) or char_start < 0:
        raise ValueError(f"Q{question_id}: raw statement provenance has invalid char_start")
    raw_window = raw_path.read_text(encoding="utf-8")[char_start : char_start + 20000]
    for required_text in (str(rows[0][column_index]), str(rows[row_index][0]), str(rows[row_index][column_index])):
        if required_text not in raw_window:
            raise ValueError(f"Q{question_id}: raw statement window does not contain the checked source cell")
    return {
        "internal_table_uid": uid,
        "document_id": table.get("document_id"),
        "row_index": row_index,
        "column_index": column_index,
        "raw_statement_path": str(raw_path.relative_to(source_root)),
        "raw_statement_sha256": raw_sha,
        "raw_statement_char_start": char_start,
        "header_source_label": header.get("source_label"),
        "period_labels": list(header.get("period_labels") or []),
        "unit_labels": header_units,
        "table_header_unit_labels": sorted(set(all_header_units)) if not any("VND" in unit.upper() for unit in header_units) else [],
        "table_header_unit_anchors": table_unit_anchors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--assignment-manifest", type=Path, required=True)
    parser.add_argument("--reviewer-slot", choices=("reviewer_a", "reviewer_b"), default="reviewer_a")
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--review-items", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reviewer-id", default="codex-independent-source-audit-v1")
    parser.add_argument("--reviewed-at", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise ValueError("--source-root must be an existing directory")
    assignment_manifest = load_json(args.assignment_manifest)
    if assignment_manifest.get("protocol") != ASSIGNMENT_PROTOCOL:
        raise ValueError("Unexpected assignment manifest protocol")
    if assignment_manifest.get("blind_to_qwen_decision") is not True or assignment_manifest.get("labels_prepopulated") is not False:
        raise ValueError("Assignment is not a blind, unpopulated reviewer task")
    require_false_contract(assignment_manifest.get("source_contract"), "assignment manifest")
    output_meta = (assignment_manifest.get("outputs") or {}).get(args.reviewer_slot) or {}
    assignment_sha = require_hash(args.assignment, output_meta.get("assignment_sha256"), "review assignment")

    assignments = load_jsonl(args.assignment)
    if not assignments or len({row.get("question_id") for row in assignments}) != len(assignments):
        raise ValueError("Review assignment has no rows or duplicate question IDs")
    tables = {str(row.get("internal_table_uid")): row for row in load_jsonl(args.structured_tables)}
    contexts = {str(row.get("internal_table_uid")): row for row in load_jsonl(args.evidence_context)}
    questions = {int(row["id"]): row for row in load_jsonl(args.review_items)}
    if len(tables) == 0 or len(contexts) == 0 or len(questions) == 0:
        raise ValueError("Original source snapshot is empty")
    reviewed_at = args.reviewed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not str(args.reviewer_id).strip() or not str(reviewed_at).strip():
        raise ValueError("Reviewer identity and review time are required")

    labels: list[dict[str, Any]] = []
    for assignment in sorted(assignments, key=lambda row: int(row["question_id"])):
        question_id = int(assignment["question_id"])
        packet = assignment.get("packet") or {}
        if assignment.get("protocol") != ASSIGNMENT_PROTOCOL or assignment.get("reviewer_slot") != args.reviewer_slot:
            raise ValueError(f"Q{question_id}: invalid reviewer assignment")
        if "critic_result" in assignment or "status" in packet:
            raise ValueError(f"Q{question_id}: assignment leaks a Qwen decision")
        require_false_contract(assignment.get("source_contract"), f"Q{question_id} assignment")
        if assignment.get("immutable_packet_sha256") != canonical_sha256(packet):
            raise ValueError(f"Q{question_id}: immutable packet hash mismatch")
        excerpt = (packet.get("bounded_source_excerpts") or [{}])[0]
        uid = str(excerpt.get("internal_table_uid") or "")
        if uid not in tables or uid not in contexts or question_id not in questions:
            raise ValueError(f"Q{question_id}: original source snapshot is incomplete")
        validate_question(
            question_id=question_id,
            context=packet.get("question_context") or {},
            table_uid=uid,
            review_item=questions[question_id],
        )
        check = validate_operand(
            question_id=question_id,
            packet=packet,
            table=tables[uid],
            context=contexts[uid],
            source_root=source_root,
        )
        labels.append(
            {
                "schema_version": 1,
                "protocol": LABEL_PROTOCOL,
                "review_protocol": REVIEW_PROTOCOL,
                "assignment_id": assignment.get("assignment_id"),
                "question_id": question_id,
                "immutable_packet_sha256": assignment.get("immutable_packet_sha256"),
                "status": "accept",
                "provenance": "independent_ai_source_review",
                "reviewer_id": args.reviewer_id,
                "reviewed_at": reviewed_at,
                "source_coordinates_checked": True,
                "source_coordinate_agree": True,
                "unit_period_agree": True,
                "deterministic_replay_agree": True,
                "unsupported_evidence": False,
                "blind_to_qwen_decision": True,
                "source_checks": check,
                "notes": "Independent AI source audit verified only the bounded deterministic trace; it does not create an answer or promotion.",
                "source_contract": source_contract(),
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"grounded_critic_{args.reviewer_slot}_independent_ai_labels_v1.jsonl"
    write_jsonl(output_path, labels)
    manifest = {
        "schema_version": 1,
        "protocol": REVIEW_PROTOCOL,
        "blind_to_qwen_decision": True,
        "reviewer_slot": args.reviewer_slot,
        "reviewer_id": args.reviewer_id,
        "reviewed_at": reviewed_at,
        "inputs": {
            "assignment": {"path": str(args.assignment), "sha256": assignment_sha},
            "assignment_manifest": {"path": str(args.assignment_manifest), "sha256": sha256_file(args.assignment_manifest)},
            "structured_tables": {"path": str(args.structured_tables), "sha256": sha256_file(args.structured_tables)},
            "evidence_context": {"path": str(args.evidence_context), "sha256": sha256_file(args.evidence_context)},
            "review_items": {"path": str(args.review_items), "sha256": sha256_file(args.review_items)},
        },
        "outputs": {"labels": {"path": str(output_path), "sha256": sha256_file(output_path)}},
        "counts": {"label_count": len(labels), "accept_count": len(labels)},
        "source_contract": source_contract(),
    }
    manifest_path = args.output_dir / f"grounded_critic_{args.reviewer_slot}_independent_ai_labels_v1.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()
