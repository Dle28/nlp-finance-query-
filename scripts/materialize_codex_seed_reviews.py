#!/usr/bin/env python3
"""Materialize provenance-safe Codex review rounds for the 12 seed cases.

This is intentionally a curated review specification, not a heuristic model.
Every evidence reference is copied from the immutable V3 bundle at runtime.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


SPECS: dict[int, dict[str, Any]] = {
    115: {
        "ranks": {1: [14]},
        "status": "machine_provisional",
        "completeness": "complete",
        "confidence": 0.99,
        "reason_codes": ["EXACT_METRIC_ROW", "METADATA_MATCH", "UNIT_IN_REPORT"],
        "notes": "Rank 1 là báo cáo kết quả kinh doanh riêng CTG 2019 và có exact row TỔNG CHI PHÍ HOẠT ĐỘNG.",
    },
    347: {
        "ranks": {2: [1]},
        "status": "machine_provisional",
        "completeness": "complete",
        "confidence": 0.99,
        "reason_codes": ["MACHINE_TOP1_REJECTED", "EXACT_METRIC_ROW", "METADATA_MATCH"],
        "notes": "Rank 2 chứa đúng lãi tiền gửi ngân hàng, lãi cho vay; rank 1 là biến động khoản phải trả và không liên quan.",
    },
    369: {
        "ranks": {5: [0, 1, 3, 8], 8: [0, 2]},
        "status": "needs_human",
        "completeness": "partial",
        "confidence": 0.98,
        "review_round": 2,
        "supersedes_review_round": 1,
        "reason_codes": [
            "CONTROLLED_THREE_STAGE_PLAN",
            "HSG_ICR_COMPONENTS_EXACT_ROWS",
            "MSR_GROSS_PROFIT_2022_TOTAL",
            "TOPK_STAGE_COVERAGE_INCOMPLETE",
        ],
        "notes": (
            "Đã có controlled plan Quick Ratio → ΔGPM → Interest Coverage. "
            "Rank 5 hỗ trợ hai exact rows LNTT và chi phí lãi vay HSG 2023; "
            "rank 8 hỗ trợ tổng lợi nhuận gộp MSR 2022. Coverage Top-K vẫn "
            "chỉ 0/12, 1/16, 2/8 theo ba stage nên review phải giữ partial."
        ),
    },
    598: {
        "ranks": {2: [0, 1, 2, 13], 5: [0, 1, 2, 15]},
        "status": "machine_provisional",
        "completeness": "complete",
        "confidence": 0.99,
        "reason_codes": ["MULTI_PERIOD_EVIDENCE_SET", "EXACT_TOTAL_ROWS", "SCOPE_MATCH"],
        "notes": "Rank 2 là tổng phải trả người bán ngắn hạn cuối 2020; rank 5 là cùng chỉ tiêu cuối 2021. Machine rank 1 thuộc phải trả ngắn hạn khác.",
    },
    606: {
        "ranks": {},
        "status": "retrieval_failure",
        "completeness": "missing",
        "confidence": 0.96,
        "reason_codes": ["NO_CURRENT_LIABILITIES_TOTAL", "TOPK_INCOMPLETE", "PLANNER_FAMILY_ERROR"],
        "notes": "Không candidate nào chứa tổng nợ ngắn hạn cho cả 31/12/2019 và 31/12/2023; rank 10 là tổng tài sản ngắn hạn, không phải nợ.",
        "no_candidate": True,
        "planner_issue": True,
    },
    704: {
        "ranks": {2: [0, 6, 7]},
        "status": "needs_human",
        "completeness": "partial",
        "confidence": 0.82,
        "reason_codes": ["PLANNER_FAMILY_ERROR", "DERIVED_FINANCE_RESULT", "WORDING_AMBIGUITY"],
        "notes": "Rank 2 chứa doanh thu và chi phí tài chính để suy ra kết quả tài chính thuần. Cần human xác nhận câu hỏi không định nói 'lưu chuyển tiền thuần từ hoạt động tài chính' ở rank 1.",
        "planner_issue": True,
    },
    738: {
        "ranks": {3: [0, 14, 16]},
        "status": "needs_human",
        "completeness": "partial",
        "confidence": 0.90,
        "reason_codes": ["MBB_2024_EXACT_ROWS", "CTG_OPERAND_MISSING", "PLANNER_FAMILY_ERROR"],
        "notes": "Rank 3 chứa nguyên giá, hao mòn và giá trị còn lại MBB cuối 2024. Rank 2 là bảng năm 2023; không có operand CTG 2024 trong Top-K.",
        "planner_issue": True,
    },
    743: {
        "ranks": {3: [0, 13, 15]},
        "status": "needs_human",
        "completeness": "partial",
        "confidence": 0.91,
        "reason_codes": ["SAM_TANGIBLE_ASSET_EXACT_ROWS", "GEE_OPERAND_MISSING", "MACHINE_TOP1_INTANGIBLE"],
        "notes": "Rank 3 là TSCĐ hữu hình SAM và row 15 là giá trị còn lại cuối năm. Machine rank 1 là TSCĐ vô hình GEE; operand TSCĐ hữu hình GEE chưa có.",
    },
    791: {
        "ranks": {8: [0, 1, 2]},
        "status": "needs_human",
        "completeness": "partial",
        "confidence": 0.92,
        "reason_codes": ["GEG_RAW_MATERIAL_EXACT_ROWS", "GAS_OPERAND_MISSING", "ENTITY_ROUTING_INCOMPLETE"],
        "notes": "Rank 8 chứa nguyên vật liệu tồn kho GEG cuối 2017. Planner chỉ resolve GEG và Top-K không có Tổng Công ty Khí Việt Nam/GAS.",
        "planner_issue": True,
    },
    824: {
        "ranks": {1: [3], 2: [6], 3: [5]},
        "status": "machine_provisional",
        "completeness": "complete",
        "confidence": 0.99,
        "reason_codes": ["THREE_PERIOD_EVIDENCE_SET", "EXACT_WEIGHTED_AVERAGE_ROWS", "ENTITY_MATCH"],
        "notes": "Ranks 1, 2 và 3 lần lượt chứa số cổ phiếu phổ thông bình quân gia quyền cho 2021, 2016 và 2019.",
    },
    862: {
        "ranks": {5: list(range(0, 9))},
        "status": "needs_human",
        "completeness": "partial",
        "confidence": 0.93,
        "reason_codes": ["KBC_FINANCE_COST_EXACT_TABLE", "FOUR_ENTITIES_MISSING", "TOPK_INCOMPLETE"],
        "notes": "Rank 5 chứa toàn bộ bảng chi phí tài chính KBC, tổng cộng tại row 8. Top-K chưa có bảng tương ứng cho SCR, DIG, DXG và IJC.",
    },
    960: {
        "ranks": {1: [19], 2: [18], 3: [19], 4: [19], 5: [18]},
        "status": "machine_provisional",
        "completeness": "complete",
        "confidence": 0.99,
        "reason_codes": ["FIVE_PERIOD_EVIDENCE_SET", "EXACT_CASH_FLOW_ROWS", "SCOPE_MATCH"],
        "notes": "Ranks 1-5 cung cấp exact row lưu chuyển tiền thuần từ hoạt động kinh doanh cho đủ 2020, 2021, 2019, 2017 và 2023 trên báo cáo riêng.",
        "planner_issue": True,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-round", type=int, default=1)
    parser.add_argument("--table-structure", type=Path, default=None)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8-sig") if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    items = {int(row["id"]): row for row in load_jsonl(bundle / "review_items.jsonl")}
    tables = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables.jsonl")
    }
    structure_path = args.table_structure or bundle / "tables_structured_v2.jsonl"
    structure_validated = False
    if structure_path.is_file():
        validate_structure_sidecar(bundle, structure_path)
        structures = {
            str(row["internal_table_uid"]): row
            for row in load_jsonl(structure_path)
        }
        unknown = sorted(set(structures) - set(tables))
        if unknown:
            raise RuntimeError(
                "Table-structure sidecar contains UID absent from bundle: "
                + ", ".join(unknown[:3])
            )
        for uid, structure in structures.items():
            tables[uid] = {**tables[uid], **structure}
        structure_validated = True

    output: list[dict[str, Any]] = []
    for qid, review_spec in SPECS.items():
        item = items[qid]
        candidates = {int(row["rank"]): row for row in item.get("candidates") or []}
        proposed_uids: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        for rank, row_indices in review_spec["ranks"].items():
            candidate = candidates[rank]
            uid = str(candidate["internal_table_uid"])
            table_rows = tables[uid].get("rows") or []
            proposed_uids.append(uid)
            evidence_refs.append(
                {
                    "internal_table_uid": uid,
                    "rank": rank,
                    "document_id": candidate.get("document_id"),
                    "row_indices": row_indices,
                    "source_rows": [
                        {"row_index": index, "row": table_rows[index]}
                        for index in row_indices
                    ],
                    "direct_evidence": candidate.get("direct_evidence"),
                }
            )

        output.append(
            {
                "id": qid,
                "question": item["question"],
                "reviewer_type": "codex_assisted",
                "review_round": int(
                    review_spec.get("review_round", args.review_round)
                ),
                "supersedes_review_round": review_spec.get(
                    "supersedes_review_round"
                ),
                "annotation_status": review_spec["status"],
                "human_verified": False,
                "proposed_positive_table_uids": proposed_uids,
                "proposed_ranks": sorted(review_spec["ranks"]),
                "proposed_no_candidate": bool(review_spec.get("no_candidate", False)),
                "evidence_completeness": review_spec["completeness"],
                "review_confidence": review_spec["confidence"],
                "planner_issue": bool(review_spec.get("planner_issue", False)),
                "reason_codes": review_spec["reason_codes"],
                "review_notes": review_spec["notes"],
                "requires_human_confirmation": True,
                "structure_validation": {
                    "complete": structure_validated,
                    "structure_version": 2 if structure_validated else 1,
                    "source": str(structure_path) if structure_validated else None,
                },
                "evidence_refs": evidence_refs,
            }
        )

    write_jsonl(args.output, sorted(output, key=lambda row: int(row["id"])))
    print("Codex-assisted reviews:", args.output, "count=", len(output))


if __name__ == "__main__":
    main()
