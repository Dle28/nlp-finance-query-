from __future__ import annotations

import json
from pathlib import Path

from finance_query.training.synthetic_curriculum import build_curriculum


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_curriculum_is_table_only_and_ticker_disjoint(tmp_path: Path) -> None:
    tables = tmp_path / "tables.jsonl"
    rows = []
    for ticker in ("AAA", "BBB", "CCC"):
        for ordinal, metric in enumerate(("Doanh thu thuần", "Lợi nhuận sau thuế")):
            rows.append(
                {
                    "internal_table_uid": f"{ticker}-{ordinal}",
                    "document_id": f"{ticker}_financial_statements_2024_consolidated",
                    "table_section": "Báo cáo kết quả kinh doanh",
                    "table_purpose": "reported values",
                    "column_labels": ["Chỉ tiêu", "2024 VND", "2023 VND"],
                    "rows": [
                        ["Chỉ tiêu", "2024 VND", "2023 VND"],
                        [metric, "1.200.000", "1.000.000"],
                    ],
                }
            )
    _write_jsonl(tables, rows)
    config = {
        "source": {"expected_sha256": "unused-in-smoke", "expected_table_count": len(rows)},
        "split": {"train_percent": 80, "validation_percent": 10, "seed": 20260828},
        "curriculum": {"max_rows_per_table": 2, "max_row_labels_in_passage": 12},
    }
    output = tmp_path / "curriculum"
    manifest = build_curriculum(
        tables_path=tables,
        output_dir=output,
        config=config,
        max_tables=len(rows),
    )

    assert manifest["competition_questions_read"] == 0
    assert manifest["counts"]["retrieval_triplets"]["train"] > 0
    assert not any(manifest["split_ticker_overlap"].values())
    assert (output / "retrieval_triplets_train.jsonl").exists()
    assert (output / "reranker_pairs_train.jsonl").exists()
    assert (output / "program_sft_train.jsonl").exists()

    sft_rows = [
        json.loads(line)
        for line in (output / "program_sft_train.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert sft_rows
    assistant = sft_rows[0]["messages"][-1]
    assert assistant["role"] == "assistant"
    assert json.loads(assistant["content"])["operation"] in {
        "lookup",
        "subtract",
        "percentage_change",
    }
