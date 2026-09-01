#!/usr/bin/env python3
"""Discover a small, explicit set of source cells for period recheck.

This is intentionally a whitelist, not a general row matcher.  Each target
declares the report, table family, row label, and whether the selected header
must be a current-column label or an explicitly printed year.  The output is
navigation/provenance only; it never contains a number, answer, or reviewer
decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.source_title_period_recheck import (
    _aligned,
    _augment_header_source_cells,
    _document_header_period_marker,
    _dates_in_text,
    _fold,
    _header_period_years,
)


PROTOCOL = "vifinqa_direct_lookup_source_title_discovery_v2"
CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "submission_eligible": False,
    "training_eligible": False,
    "promotion_allowed": False,
}


# These are deliberately boring, source-specific assertions.  A target is
# admitted only when all assertions match exactly and one value column remains.
TARGETS: dict[int, dict[str, Any]] = {
    70: {"role": "current_receivables", "row_label": "Công ty Cổ phần Bao bì Dầu khí Việt Nam", "document_id": "DCM_financial_statements_2019_separate", "uid_prefix": "1c24680c", "table_function": "related_party_schedule", "expected_table_type_alias": "related_party_schedule", "header_mode": "document_header_current", "document_header_period": True, "fixed_row_index": 3, "parent_row_index": 2, "parent_label": "Phải thu ngắn hạn của khách hàng", "parent_label_column_index": 0},
    2: {"role": "loans_to_customers", "row_label": "Thương mại", "document_id": "ACB_financial_statements_2022_separate", "uid_prefix": "1bb1a025", "table_function": "financial_note_detail", "expected_table_type_alias": "notes", "header_mode": "explicit_year"},
    14: {"role": "general_and_administrative_expense", "row_label": "9. Chi phí quản lý doanh nghiệp", "document_id": "ASM_financial_statements_2025_separate", "uid_prefix": "049c9335", "table_function": "income_statement", "header_mode": "current"},
    28: {"role": "current_receivables", "row_label": "Phải thu ngắn hạn khác", "document_id": "NVL_financial_statements_2016_separate", "uid_prefix": "eb34639f", "table_function": "balance_sheet", "header_mode": "explicit_year"},
    45: {"role": "liabilities", "row_label": "TỔNG NỘ PHẢI TRẢ", "document_id": "BAB_financial_statements_2020_separate", "uid_prefix": "f5c075b0", "table_function": "balance_sheet", "header_mode": "explicit_year"},
    67: {"role": "cost_of_goods_sold", "row_label": "4. Giá vốn hàng bán và dịch vụ cung cấp", "document_id": "VPI_financial_statements_2025_separate", "uid_prefix": "b7d8cb74", "table_function": "income_statement", "header_mode": "current"},
    118: {"role": "loans_to_customers", "row_label": "Dự phòng chung cho vay khách hàng (Trích lập) dự phòng (xem Thuyết minh 9)", "document_id": "VCB_financial_statements_2015_separate", "uid_prefix": "c62f5eb8", "table_function": "financial_note", "expected_table_type_alias": "notes", "header_mode": "explicit_year"},
    131: {"role": "cash_and_cash_equivalents", "row_label": "Tiền và tương đương tiền cuối năm", "document_id": "HUT_financial_statements_2025_separate", "uid_prefix": "4e7bc316", "table_function": "cash_flow_statement", "header_mode": "current"},
    132: {"role": "interest_expense", "row_label": "Chi phí lãi vay", "document_id": "SNZ_financial_statements_2021_separate", "uid_prefix": "6aa48d96", "table_function": "financial_note_detail", "expected_table_type_alias": "notes", "title_contains": "5.4. Chi phí tài chính", "header_mode": "explicit_year"},
    135: {"role": "current_receivables", "row_label": "Bên thứ ba", "document_id": "GAS_financial_statements_2021_separate", "uid_prefix": "29ee5c41", "table_function": "financial_note_detail", "expected_table_type_alias": "notes", "title_contains": "5 PHẢI THU NGẮN HẠN CỦA KHÁCH HÀNG", "header_mode": "explicit_year"},
    148: {"role": "cash_at_end", "row_label": "Tiền và các khoản tương đương tiền cuối năm\\( (70 = 50 + 60 + 61) \\) (Thuyết minh 4)", "document_id": "VSC_financial_statements_2019_separate", "uid_prefix": "d6946e0d", "table_function": "cash_flow_statement", "header_mode": "explicit_year"},
    167: {"role": "interest_expense", "row_label": "Chi phí lãi vay", "document_id": "MSR_financial_statements_2025_separate", "uid_prefix": "2ca8b0e0", "table_function": "financial_note_detail", "expected_table_type_alias": "notes", "title_contains": "16. Chi phí tài chính", "header_mode": "explicit_year"},
    192: {"role": "profit_before_tax", "row_label": "(Lỗ)/lợi nhuận kế toán trước thuế (50 = 30 + 40)", "document_id": "MML_financial_statements_2017_separate", "uid_prefix": "6ea38c12", "table_function": "income_statement", "header_mode": "explicit_year"},
    198: {"role": "deferred_income_tax_expense", "row_label": "Chi phí thuế thu nhập doanh nghiệp hoãn lại", "document_id": "ACB_financial_statements_2020_separate", "uid_prefix": "a326f2f3", "table_function": "income_statement", "header_mode": "explicit_year"},
    201: {"role": "current_receivables", "row_label": "1. Phải thu ngắn hạn của khách hàng", "document_id": "HSG_financial_statements_2017_separate", "uid_prefix": "9e2d0ab2", "table_function": "balance_sheet", "header_mode": "current"},
    231: {"role": "profit_before_tax", "row_label": "14. Tổng lợi nhuận kế toán trước thuế TNDN", "document_id": "SCR_financial_statements_2023_separate", "uid_prefix": "7e00afc7", "table_function": "income_statement", "header_mode": "current"},
    264: {"role": "loans_to_customers", "row_label": "Cho vay đối với các tổ chức, cá nhân nước ngoài", "document_id": "SSB_financial_statements_2025_separate", "uid_prefix": "4a23b063", "table_function": "financial_note", "expected_table_type_alias": "notes", "header_mode": "explicit_year"},
    284: {"role": "share_capital", "row_label": "Vôn điều lệ", "document_id": "EIB_financial_statements_2023_separate", "uid_prefix": "b2104039", "table_function": "balance_sheet", "header_mode": "explicit_year"},
    302: {"role": "share_capital", "row_label": "Vốn góp của chủ sở hữu", "document_id": "HHV_financial_statements_2023_separate", "uid_prefix": "dc1ec0ed", "table_function": "balance_sheet", "header_mode": "explicit_year", "header_text_contains": "31.12.2023"},
    329: {"role": "net_income", "row_label": "Lợi nhuận sau thuế", "document_id": "ACB_financial_statements_2024_separate", "uid_prefix": "d85d9511", "table_function": "financial_data_schedule", "expected_table_type_alias": "income_statement_schedule", "title_contains": "b03", "header_mode": "explicit_year"},
    337: {"role": "cash_at_end", "row_label": "Tiền và các khoản tương đương tiền cuối năm", "document_id": "BAF_financial_statements_2023_separate", "uid_prefix": "21bd90a5", "table_function": "cash_flow_statement", "header_mode": "current"},
    341: {"role": "profit_before_tax", "row_label": "14. Tổng lợi nhuận.kế toán trước thuế", "document_id": "GVR_financial_statements_2024_separate", "uid_prefix": "cc89d828", "table_function": "income_statement", "header_mode": "explicit_year"},
    356: {"role": "loans_to_customers", "row_label": "Dự phòng rủi ro cho vay khách hàng", "document_id": "CTG_financial_statements_2019_separate", "uid_prefix": "51134c04", "table_function": "balance_sheet", "header_mode": "explicit_year"},
    361: {"role": "operating_cash_flow", "row_label": "Lưu chuyển tiền thuần từ (sử dụng vào) hoạt động kinh doanh", "document_id": "SCR_financial_statements_2020_separate", "uid_prefix": "5a3f8b8b", "table_function": "cash_flow_statement", "header_mode": "current"},
    84: {"role": "cash_and_cash_equivalents", "row_label": "Tiền và các khoản tương đương tiền", "document_id": "DTK_financial_statements_2024_separate", "uid_prefix": "ae9df3f4", "table_function": "balance_sheet", "header_mode": "current"},
    152: {"role": "total_assets", "row_label": "TỔNG TÀI SẢN CÓ", "document_id": "SGB_financial_statements_2023_consolidated", "uid_prefix": "91de3f39", "table_function": "balance_sheet", "header_mode": "explicit_year"},
    239: {"role": "property_plant_equipment", "row_label": "Nguyên giá", "document_id": "EIB_financial_statements_2024_separate", "uid_prefix": "d837f6cb", "table_function": "balance_sheet", "header_mode": "explicit_year", "fallback_row_index": 24, "row_label_column_index": 1, "parent_row_index": 23, "parent_label": "Tài sản cố định hữu hình", "parent_label_column_index": 1},
    272: {"role": "total_assets", "row_label": "TỔNG TẢI SẢN CÓ", "document_id": "MBB_financial_statements_2020_separate", "uid_prefix": "f98763ab", "table_function": "financial_data_schedule", "expected_table_type_alias": "balance_sheet_schedule", "title_contains": "b02/tctd", "header_mode": "explicit_year", "fallback_row_index": 35, "row_label_column_index": 0},
    315: {"role": "total_assets", "row_label": "Tổng tài sản thuế thu nhập hoãn lại", "document_id": "SAB_financial_statements_2023_separate", "uid_prefix": "cfee2c25", "table_function": "financial_note", "expected_table_type_alias": "balance_sheet_note", "header_mode": "explicit_year", "header_text_contains": "31/12/2023"},
}


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _stage_for_role(packet: Mapping[str, Any], role: str) -> str | None:
    matches = [
        stage.get("stage_id")
        for stage in packet.get("stages") or []
        if sum(1 for operand in stage.get("required_operands") or [] if operand.get("role") == role) == 1
        for operand in stage.get("required_operands") or []
        if operand.get("role") == role
    ]
    return str(matches[0]) if len(matches) == 1 else None


def build(*, row_review_queue: Path, packets: Path, structured_tables: Path, evidence_context: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    queue = _rows(row_review_queue)
    packet_rows = {int(row["question_id"]): row for row in _rows(packets)}
    tables = {str(row.get("internal_table_uid") or ""): row for row in _rows(structured_tables)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _rows(evidence_context)}
    discoveries: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    triage: list[dict[str, Any]] = []
    for question_id, target in sorted(TARGETS.items()):
        packet = packet_rows[question_id]
        years = (packet.get("question_context") or {}).get("years") or []
        year = int(years[0]) if len(years) == 1 else None
        stage_id = _stage_for_role(packet, str(target["role"]))
        matches: list[dict[str, Any]] = []
        selected_source: dict[str, Any] | None = None
        if year is not None and stage_id is not None:
            row_candidates = [
                row
                for row in queue
                if int(row.get("question_id") or 0) == question_id
            ]
            # A top-k retrieval queue may omit the exact value row even when
            # the immutable V2 table is known.  A fallback is admitted only
            # for a target that pins the document, table UID prefix, row and
            # (when needed) its parent row; it is not a general row search.
            if target.get("fallback_row_index") is not None:
                for table in tables.values():
                    if table.get("document_id") != target["document_id"]:
                        continue
                    if not str(table.get("internal_table_uid") or "").startswith(target["uid_prefix"]):
                        continue
                    row_candidates.append(
                        {
                            "question_id": question_id,
                            "requested_scope": (packet.get("question_context") or {}).get("scope"),
                            "observed_scope": (packet.get("question_context") or {}).get("scope"),
                            "document_id": table.get("document_id"),
                            "internal_table_uid": table.get("internal_table_uid"),
                            "row_index": target["fallback_row_index"],
                            "row_label": target["row_label"],
                            "_fallback": True,
                        }
                    )
            for row in row_candidates:
                if int(row.get("question_id") or 0) != question_id:
                    continue
                expected_scope = (packet.get("question_context") or {}).get("scope")
                if row.get("requested_scope") != expected_scope or row.get("observed_scope") != expected_scope:
                    continue
                if str(row.get("document_id") or "") != target["document_id"]:
                    continue
                if not str(row.get("internal_table_uid") or "").startswith(target["uid_prefix"]):
                    continue
                if _fold(row.get("row_label")) != _fold(target["row_label"]):
                    continue
                if target.get("fixed_row_index") is not None and int(row.get("row_index")) != int(target["fixed_row_index"]):
                    continue
                table = tables.get(str(row.get("internal_table_uid") or ""))
                context = contexts.get(str(row.get("internal_table_uid") or ""))
                if table is None or context is None or not _aligned(table, context):
                    continue
                if (context.get("table_function") or {}).get("kind") != target["table_function"]:
                    continue
                source_rows = table.get("rows") or []
                row_index = int(row["row_index"])
                row_label_column_index = int(target.get("row_label_column_index", 1))
                if row.get("_fallback"):
                    if row_index < 0 or row_index >= len(source_rows) or row_label_column_index >= len(source_rows[row_index]):
                        continue
                    if _fold(source_rows[row_index][row_label_column_index]) != _fold(target["row_label"]):
                        continue
                if target.get("parent_row_index") is not None:
                    parent_index = int(target["parent_row_index"])
                    parent_column_index = int(target.get("parent_label_column_index", row_label_column_index))
                    if (
                        parent_index < 0
                        or parent_index >= len(source_rows)
                        or parent_column_index < 0
                        or parent_column_index >= len(source_rows[parent_index])
                        or _fold(source_rows[parent_index][parent_column_index]) != _fold(target.get("parent_label"))
                    ):
                        continue
                title = str(((context.get("context_trace") or {}).get("source_title")) or "")
                if any(_fold(anchor) not in _fold(title) for anchor in [target.get("title_contains")] if anchor):
                    continue
                headers = [header for header in ((context.get("canonical_headers") or {}).get("columns") or []) if isinstance(header, Mapping)]
                document_period_marker: dict[str, Any] | None = None
                if target["header_mode"] == "current":
                    wanted = "so cuoi nam" if target["table_function"] == "balance_sheet" else "nam nay"
                    headers = [header for header in headers if _fold(header.get("source_label")) == wanted]
                    dates = [value for value in _dates_in_text(title) if value.year == year]
                    if len(dates) != 1:
                        continue
                elif target["header_mode"] == "document_header_current":
                    headers = [
                        header
                        for header in headers
                        if _fold(header.get("source_label")).startswith("so cuoi nam")
                    ]
                    document_period_marker = _document_header_period_marker(
                        table=table,
                        requested_year=year,
                    )
                    if document_period_marker is None:
                        continue
                else:
                    dated_headers: list[dict[str, Any]] = []
                    for header in headers:
                        column_index = int(header.get("column_index"))
                        header_cells = _augment_header_source_cells(
                            table=table,
                            header=header,
                            column_index=column_index,
                            value_row_index=row_index,
                        )
                        raw_header_values = [
                            str(source_rows[item["row_index"]][item["column_index"]])
                            for item in header_cells
                            if 0 <= item["row_index"] < len(source_rows)
                            and 0 <= item["column_index"] < len(source_rows[item["row_index"]])
                        ]
                        if _header_period_years(header, extra_values=raw_header_values) == {year}:
                            dated_headers.append(
                                {
                                    **header,
                                    "header_source_cells": header_cells,
                                    "_raw_header_values": raw_header_values,
                                }
                            )
                    headers = dated_headers
                    if target.get("header_text_contains"):
                        headers = [
                            header
                            for header in headers
                            if _fold(target["header_text_contains"]) in _fold(
                                " ".join([str(header.get("source_label") or "")] + list(header.get("_raw_header_values") or []))
                            )
                        ]
                profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row.get("row_index")]
                if len(headers) != 1 or len(profiles) != 1:
                    continue
                header = headers[0]
                column_index = int(header["column_index"])
                profile = profiles[0]
                if column_index not in set(profile.get("numeric_columns") or []) or column_index in set(profile.get("unreliable_numeric_columns") or []):
                    continue
                source_rows = table.get("rows") or []
                provenance = table.get("cell_provenance") or []
                if row_index < 0 or row_index >= len(source_rows) or row_index >= len(provenance) or column_index < 0 or column_index >= len(source_rows[row_index]) or column_index >= len(provenance[row_index]):
                    continue
                raw_cell = str(source_rows[row_index][column_index])
                match = {
                    "document_id": table.get("document_id"),
                    "internal_table_uid": table.get("internal_table_uid"),
                    "row_index": row_index,
                    "column_index": column_index,
                    "header_source_cells": header.get("header_source_cells") or [],
                }
                if document_period_marker is not None:
                    match["document_header_period_marker"] = document_period_marker
                # The retrieval queue can repeat the same row with different
                # navigation hashes.  Repetition of one immutable coordinate
                # is not ambiguity; a second coordinate is.
                if match in matches:
                    continue
                matches.append(match)
                if len(matches) == 1:
                    selected_source = {
                        "document_uid": table.get("document_id"),
                        "internal_table_uid": table.get("internal_table_uid"),
                        "row_index": row_index,
                        "column_index": column_index,
                        "raw_text_sha256": hashlib.sha256(raw_cell.encode("utf-8")).hexdigest(),
                    }
                    if document_period_marker is not None:
                        selected_source["document_header_period_marker"] = document_period_marker
        if len(matches) == 1 and selected_source is not None:
            discoveries.append({
                "schema_version": 2,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "stage_id": stage_id,
                "role": target["role"],
                "expected_table_type_alias": target.get("expected_table_type_alias"),
                "period_recovery_mode": target.get("header_mode"),
                **(
                    {"document_header_period_marker": selected_source.get("document_header_period_marker")}
                    if selected_source.get("document_header_period_marker") is not None
                    else {}
                ),
                "source_value_cells": [{
                    **selected_source,
                }],
                "source_contract": dict(CONTRACT),
            })
        triage.append({"question_id": question_id, "primary_blocker": "PERIOD_HEADER_NOT_EXTRACTED", "source_contract": dict(CONTRACT)})
        audit.append({
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "candidate_count": len(matches),
            "selected": matches[0] if len(matches) == 1 else None,
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        })
    output_dir.mkdir(parents=True)
    _write_jsonl(output_dir / "source_title_discovery_candidates_v2.jsonl", discoveries)
    _write_jsonl(output_dir / "source_title_discovery_triage_v2.jsonl", triage)
    _write_jsonl(output_dir / "source_title_discovery_audit_v2.jsonl", audit)
    summary = {
        "schema_version": 2,
        "protocol": PROTOCOL,
        "target_question_count": len(TARGETS),
        "discovery_candidate_count": len(discoveries),
        "unique_question_count": sum(1 for row in audit if row["candidate_count"] == 1),
        "quarantined_question_count": sum(1 for row in audit if row["candidate_count"] != 1),
        "source_contract": dict(CONTRACT),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--row-review-queue", type=Path, required=True)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(
        row_review_queue=args.row_review_queue,
        packets=args.packets,
        structured_tables=args.structured_tables,
        evidence_context=args.evidence_context,
        output_dir=args.output_dir,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
