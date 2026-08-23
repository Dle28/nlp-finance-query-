from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "materialize_synthetic_retriever_dynamic_ladder.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_dynamic_ladder_reproduces_held_out_rankings_without_promotion(tmp_path: Path) -> None:
    curriculum = tmp_path / "curriculum.jsonl"
    source_tables = tmp_path / "table_assets.jsonl"
    rankings = tmp_path / "rankings.jsonl"
    _jsonl(
        source_tables,
        [
            {
                "internal_table_uid": "table-positive",
                "document_id": "AAA_2024_consolidated",
                "ticker": "AAA",
                "report_year": 2024,
                "scope": "consolidated",
                "page_no": 7,
                "rows": [["metric", "17"]],
            }
        ],
    )
    _jsonl(
        curriculum,
        [
            {
                "curriculum_id": "held-out-1",
                "split": "test",
                "positive_table_uids": ["table-positive"],
                "source_lineage": {
                    "document_id": "AAA_2024_consolidated",
                    "ticker": "AAA",
                    "report_year": 2024,
                    "scope": "consolidated",
                },
                "source_bindings": [
                    {"internal_table_uid": "table-positive", "row_index": 0, "column_index": 1}
                ],
            }
        ],
    )
    _jsonl(
        rankings,
        [
            {
                "curriculum_id": "held-out-1",
                "positive_table_uids": ["table-positive"],
                "ranked_table_uids": ["table-positive"],
                "first_positive_rank": 1,
            }
        ],
    )
    evaluation_manifest = tmp_path / "evaluation_manifest.json"
    _json(
        evaluation_manifest,
        {
            "protocol": "synthetic_issuer_heldout_retriever_evaluation_v1",
            "promotion_status": "offline_evaluation_complete_not_promoted",
            "source_contract": {"issuer_held_out_splits_only": True, "benchmark_questions_read": False},
            "inputs": {"curriculum_sha256": _sha(curriculum), "source_tables_sha256": _sha(source_tables)},
            "configuration": {"ks": [1]},
            "models": {
                "base": {
                    "splits": {"test": {"questions": 1, "mrr": 1.0, "recall_at_k": {"1": 1.0}}}
                }
            },
        },
    )
    output_dir = tmp_path / "dynamic-output"
    subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--curriculum",
            str(curriculum),
            "--source-tables",
            str(source_tables),
            "--evaluation-manifest",
            str(evaluation_manifest),
            "--rankings",
            str(rankings),
            "--model-label",
            "base",
            "--split",
            "test",
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        check=True,
    )
    report = json.loads((output_dir / "synthetic_retriever_dynamic_ladder_report.json").read_text())
    assert report["run_status"] == "issuer_heldout_dynamic_ladder_complete_not_promoted"
    assert report["stage_hit_rates"] == {
        "complete_operand_set": 1.0,
        "document": 1.0,
        "page": 1.0,
        "table": 1.0,
    }
    assert report["promotion_allowed"] is False
