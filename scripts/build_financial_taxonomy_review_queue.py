#!/usr/bin/env python3
"""Build a deterministic, non-promotable review queue for taxonomy candidates."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


PROTOCOL = "financial_taxonomy_review_queue_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def stable_key(record: dict[str, Any]) -> str:
    identity = "|".join(
        str(record.get(key) or "")
        for key in ("record_kind", "document_id", "internal_table_uid", "row_index")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def take_stratified(
    records: Iterable[dict[str, Any]],
    *,
    stratum,
    limit: int,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(stratum(record))].append(record)
    selected: list[dict[str, Any]] = []
    for key in sorted(groups):
        selected.extend(sorted(groups[key], key=stable_key)[:limit])
    return selected


def build_review_queue(
    *,
    row_candidates: Path,
    table_roles: Path,
    sectors: Path,
    output: Path,
    max_rows_per_stratum: int = 5,
    max_sectors_per_sector: int = 50,
) -> dict[str, Any]:
    rows = load_jsonl(row_candidates)
    roles = load_jsonl(table_roles)
    sector_rows = load_jsonl(sectors)

    exact_rows = [row for row in rows if row.get("match_status") == "exact_unique"]
    selected_rows = take_stratified(
        exact_rows,
        stratum=lambda row: "|".join(
            (
                str((row.get("concept_candidates") or [{}])[0].get("concept_id") or "unknown"),
                str(row.get("navigation_gate_status") or "unknown"),
            )
        ),
        limit=max_rows_per_stratum,
    )
    selected_roles = [row for row in roles if row.get("status") == "semantic_candidate"]
    selected_roles.sort(
        key=lambda row: (
            row.get("existing_table_type") == row.get("proposed_table_type"),
            stable_key(row),
        )
    )
    selected_sectors = take_stratified(
        (row for row in sector_rows if row.get("status") == "candidate"),
        stratum=lambda row: row.get("sector") or "unknown",
        limit=max_sectors_per_sector,
    )

    queue: list[dict[str, Any]] = []
    for target, review_type, questions in (
        *(
            (
                row,
                "row_concept",
                [
                    "Nhãn nguồn có biểu đạt đúng concept được đề xuất không?",
                    "Mã số, loại bảng và ngành có đủ để loại trừ nghĩa khác không?",
                ],
            )
            for row in selected_rows
        ),
        *(
            (
                row,
                "table_role",
                [
                    "Toàn bộ bảng có đúng là loại bảng được đề xuất không?",
                    "Bảng có phải thuyết minh/lịch biểu chỉ chứa các nhãn trùng tên không?",
                ],
            )
            for row in selected_roles
        ),
        *(
            (
                row,
                "document_sector",
                [
                    "Các tín hiệu có xác định đúng ngành của pháp nhân báo cáo không?",
                    "Tín hiệu có đến từ dữ liệu so sánh/đơn vị liên quan thay vì chính doanh nghiệp không?",
                ],
            )
            for row in selected_sectors
        ),
    ):
        queue.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "review_type": review_type,
                "candidate": target,
                "review_questions": questions,
                "review_decision": None,
                "reviewer_notes": None,
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in queue:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_count": len(queue),
        "review_type_counts": {
            "row_concept": len(selected_rows),
            "table_role": len(selected_roles),
            "document_sector": len(selected_sectors),
        },
        "sampling": {
            "row_strata": "concept_id x navigation_gate_status",
            "max_rows_per_stratum": max_rows_per_stratum,
            "max_sectors_per_sector": max_sectors_per_sector,
            "table_roles": "all semantic_candidate rows; disagreements sort first",
        },
        "inputs": {
            "row_candidates_sha256": sha256_file(row_candidates),
            "table_roles_sha256": sha256_file(table_roles),
            "sectors_sha256": sha256_file(sectors),
        },
        "output_sha256": sha256_file(output),
        "promotion_allowed": False,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--row-candidates", type=Path, required=True)
    parser.add_argument("--table-roles", type=Path, required=True)
    parser.add_argument("--sectors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-rows-per-stratum", type=int, default=5)
    parser.add_argument("--max-sectors-per-sector", type=int, default=50)
    args = parser.parse_args()
    if args.max_rows_per_stratum < 1 or args.max_sectors_per_sector < 1:
        raise ValueError("Sampling limits must be positive")
    manifest = build_review_queue(
        row_candidates=args.row_candidates.resolve(),
        table_roles=args.table_roles.resolve(),
        sectors=args.sectors.resolve(),
        output=args.output.resolve(),
        max_rows_per_stratum=args.max_rows_per_stratum,
        max_sectors_per_sector=args.max_sectors_per_sector,
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
