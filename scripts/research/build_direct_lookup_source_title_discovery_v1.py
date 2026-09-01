#!/usr/bin/env python3
"""Create source-title period-recheck discovery packets for four clear lookups.

The selector is intentionally small and explicit.  It uses only a question's
typed target, the row-review navigation metadata, and aligned V2/V3 tables;
the output remains a candidate-only input to ``source_title_period_recheck``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.source_title_period_recheck import _aligned, _dates_in_text, _fold


TARGETS = {
    10: {"role": "financial_expense", "table_function": "income_statement", "row_label": "Chi phí tài chính"},
    176: {"role": "net_revenue", "table_function": "income_statement", "row_label": "Doanh thu thuần về bán hàng và cung cấp dịch vụ"},
    184: {"role": "cash_and_cash_equivalents", "table_function": "balance_sheet", "row_label": "I. Tiền và các khoản tương đương tiền"},
    325: {"role": "net_income", "table_function": "income_statement", "row_label": "16. Lợi nhuận sau thuế TNDN"},
}


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _source_contract() -> dict[str, bool]:
    return {
        "research_only": True,
        "evidence_eligible": False,
        "may_materialize_answer": False,
        "submission_eligible": False,
        "training_eligible": False,
    }


def build(*, row_review_queue: Path, packets: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    rows = _rows(row_review_queue)
    packet_rows = {int(row["question_id"]): row for row in _rows(packets)}
    table_path = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl"
    context_path = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_evidence_context_v3.jsonl"
    tables = {str(row["internal_table_uid"]): row for row in _rows(table_path)}
    contexts = {str(row["internal_table_uid"]): row for row in _rows(context_path)}
    discoveries: list[dict] = []
    triage: list[dict] = []
    audit: list[dict] = []
    for question_id, target in sorted(TARGETS.items()):
        packet = packet_rows[question_id]
        year = int((packet.get("question_context") or {}).get("years", [0])[0])
        wanted_header = "so cuoi nam" if target["table_function"] == "balance_sheet" else "nam nay"
        matches: list[dict] = []
        for row in rows:
            if row.get("question_id") != question_id or row.get("requested_scope") != "separate" or row.get("observed_scope") != "separate":
                continue
            if _fold(row.get("row_label")) != _fold(target["row_label"]):
                continue
            table = tables.get(str(row.get("internal_table_uid") or ""))
            context = contexts.get(str(row.get("internal_table_uid") or ""))
            if table is None or context is None or not _aligned(table, context):
                continue
            if (context.get("table_function") or {}).get("kind") != target["table_function"]:
                continue
            headers = [header for header in ((context.get("canonical_headers") or {}).get("columns") or []) if isinstance(header, dict) and _fold(header.get("source_label")) == wanted_header]
            profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row.get("row_index")]
            title = str(((context.get("context_trace") or {}).get("source_title")) or "")
            matching_dates = [value for value in _dates_in_text(title) if value.year == year]
            if len(headers) != 1 or len(profiles) != 1 or len(matching_dates) != 1:
                continue
            column_index = int(headers[0]["column_index"])
            profile = profiles[0]
            if column_index not in set(profile.get("numeric_columns") or []) or column_index in set(profile.get("unreliable_numeric_columns") or []):
                continue
            raw_cell = str((table.get("rows") or [])[int(row["row_index"])][column_index])
            discoveries.append({
                "schema_version": 1,
                "protocol": "vifinqa_direct_lookup_source_title_discovery_v1",
                "question_id": question_id,
                "stage_id": (packet.get("stages") or [])[0].get("stage_id"),
                "role": target["role"],
                "source_value_cells": [{
                    "document_uid": table.get("document_id"),
                    "internal_table_uid": table.get("internal_table_uid"),
                    "row_index": int(row["row_index"]),
                    "column_index": column_index,
                    "raw_text_sha256": hashlib.sha256(raw_cell.encode("utf-8")).hexdigest(),
                }],
                "source_contract": _source_contract(),
            })
            matches.append({"document_id": table.get("document_id"), "internal_table_uid": table.get("internal_table_uid"), "row_index": int(row["row_index"]), "column_index": column_index})
        triage.append({"question_id": question_id, "primary_blocker": "PERIOD_HEADER_NOT_EXTRACTED", "source_contract": _source_contract()})
        audit.append({"question_id": question_id, "candidate_count": len(matches), "selected": matches[0] if len(matches) == 1 else None})
    output_dir.mkdir(parents=True)
    discovery_path = output_dir / "source_title_discovery_candidates_v1.jsonl"
    triage_path = output_dir / "source_title_discovery_triage_v1.jsonl"
    audit_path = output_dir / "source_title_discovery_audit_v1.jsonl"
    _write_jsonl(discovery_path, discoveries)
    _write_jsonl(triage_path, triage)
    _write_jsonl(audit_path, audit)
    summary = {"schema_version": 1, "protocol": "vifinqa_direct_lookup_source_title_discovery_v1", "target_question_count": len(TARGETS), "discovery_candidate_count": len(discoveries), "unique_question_count": sum(1 for row in audit if row["candidate_count"] == 1), "source_contract": _source_contract()}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--row-review-queue", type=Path, required=True)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(row_review_queue=args.row_review_queue, packets=args.packets, output_dir=args.output_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
