from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from finance_query.e2e.core.dense_retrieval import build_dense_index
from finance_query.research.hybrid_retrieval_analysis import (
    _max_row_label_jaccard,
    build_hybrid_retrieval_analysis,
    validate_hybrid_retrieval_analysis,
)


def test_hybrid_proxy_scores_metric_cell_when_code_precedes_the_label() -> None:
    assert _max_row_label_jaccard(
        {"rows": [["01", "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "100"]]},
        "lưu chuyển tiền thuần từ hoạt động kinh doanh",
    ) == 1.0


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class _Encoder:
    def encode(self, sentences: list[str], **_: object) -> np.ndarray:
        values = []
        for sentence in sentences:
            seed = float(sum(ord(character) for character in sentence) % 23 + 1)
            vector = np.asarray([seed, seed + 1, seed + 2, seed + 3], dtype=np.float32)
            values.append(vector / np.linalg.norm(vector))
        return np.stack(values)


def test_hybrid_analysis_is_source_bound_and_non_authorizing(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    asset_rows: list[dict[str, object]] = [
        {
            "internal_table_uid": "u1",
            "document_id": "AAA_2023_consolidated",
            "ticker": "AAA",
            "report_year": 2023,
            "scope": "consolidated",
            "headers": ["Chỉ tiêu", "2023"],
            "row_paths": ["Doanh thu thuần > 100"],
            "rows": [["Doanh thu thuần", "100"]],
            "source_path": "/source/AAA.txt",
            "source_sha256": "a" * 64,
            "table_sha256": "b" * 64,
            "local_ordinal": 1,
            "char_start": 10,
            "page_no": 2,
        },
        {
            "internal_table_uid": "u2",
            "document_id": "AAA_2023_separate",
            "ticker": "AAA",
            "report_year": 2023,
            "scope": "separate",
            "headers": ["Chỉ tiêu", "2023"],
            "row_paths": ["Lợi nhuận sau thuế > 20"],
            "rows": [["Lợi nhuận sau thuế", "20"]],
            "source_path": "/source/AAA_separate.txt",
            "source_sha256": "c" * 64,
            "table_sha256": "d" * 64,
            "local_ordinal": 2,
            "char_start": 20,
            "page_no": 3,
        },
    ]
    _write_jsonl(assets, asset_rows)
    source_closure = tmp_path / "source_closure.jsonl"
    _write_jsonl(
        source_closure,
        [
            {"document_id": "AAA_2023_consolidated", "table_count": 1},
            {"document_id": "AAA_2023_separate", "table_count": 1},
        ],
    )
    contract = {
        "expected_asset_sha256": hashlib.sha256(assets.read_bytes()).hexdigest(),
        "expected_source_closure_sha256": hashlib.sha256(source_closure.read_bytes()).hexdigest(),
        "expected_table_count": 2,
        "expected_document_count": 2,
        "expected_source_report_count": 2,
        "expected_zero_table_report_count": 0,
        "expected_ticker_count": 1,
    }
    dense = tmp_path / "dense"
    encoder = _Encoder()
    build_dense_index(
        asset_path=assets,
        source_closure_path=source_closure,
        output_dir=dense,
        contract=contract,
        model_name="fake-model",
        requested_device="cpu",
        batch_size=2,
        encoder_factory=lambda _model, _device: encoder,
    )

    plans = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans,
        [
            {
                "question_id": 1,
                "question": "Doanh thu thuần AAA năm 2023?",
                "decomposition_status": "complete",
                "operands": [
                    {
                        "operand_id": "x0",
                        "ticker": "AAA",
                        "years": [2023],
                        "scope": "consolidated",
                        "metric_hints": ["Doanh thu thuần"],
                    }
                ],
            },
            {
                "question_id": 2,
                "question": "Thiếu kế hoạch",
                "decomposition_status": "abstain",
                "operands": [],
            },
        ],
    )
    lexical = tmp_path / "lexical.jsonl"
    _write_jsonl(
        lexical,
        [
            {
                "question_id": 1,
                "operand_id": "x0",
                "report_year": 2023,
                "candidate_rank": 1,
                "internal_table_uid": "u1",
                "document_id": "AAA_2023_consolidated",
                "scope": "consolidated",
                "query_lane": "metric_core_all",
            },
            {
                "question_id": 1,
                "operand_id": "x0",
                "report_year": 2023,
                "candidate_rank": 2,
                "internal_table_uid": "u2",
                "document_id": "AAA_2023_separate",
                "scope": "separate",
                "query_lane": "operand_any_recall",
            },
        ],
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "vifinqa_hybrid_retrieval_analysis_v1",
                "expected_question_count": 2,
                "expected_route_count": 1,
                "eligible_decomposition_statuses": ["complete"],
                "retrieval": {
                    "top_k_per_route": 2,
                    "rrf_constant": 60,
                    "encode_batch_size": 8,
                },
                "authorization": {
                    "navigation_metadata_only": True,
                    "may_authorize_answer": False,
                    "may_authorize_evidence": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "hybrid"
    coverage = build_hybrid_retrieval_analysis(
        config_path=config,
        plans_path=plans,
        lexical_candidates_path=lexical,
        dense_index_dir=dense,
        assets_path=assets,
        output_dir=output,
        encoder_factory=lambda _model, _device: encoder,
    )
    candidates = list(
        map(json.loads, (output / "hybrid_table_candidates_v1.jsonl").read_text().splitlines())
    )
    assert coverage["route_count"] == 1
    assert coverage["ineligible_question_count"] == 1
    assert {row["internal_table_uid"] for row in candidates} == {"u1", "u2"}
    assert all(row["exact_table_locator_sha256"] for row in candidates)
    assert all(row["may_authorize_answer"] is False for row in candidates)
    assert all("rows" not in row and "raw_value" not in row for row in candidates)
    assert all("human_verified" not in row for row in candidates)
    review_queue = list(
        map(json.loads, (output / "hybrid_review_queue_v1.jsonl").read_text().splitlines())
    )
    assert len(review_queue) == 1
    assert len(review_queue[0]["candidates"]) == 2
    assert review_queue[0]["raw_numeric_values_included"] is False
    assert "human_verified" not in review_queue[0]
    assert validate_hybrid_retrieval_analysis(
        output, expected_question_count=2, expected_route_count=1
    )["status"] == "PASS"
