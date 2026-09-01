from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.e2e.core.table_retrieval import build_lexical_index
from finance_query.research.full_corpus_candidate_retrieval import (
    _metric_query,
    _row_candidates,
    build_candidate_artifact,
    metric_core_query,
    validate_candidate_artifact,
)


def test_row_candidate_uses_metric_cell_when_first_column_is_a_code() -> None:
    candidates = _row_candidates(
        {"rows": [["01", "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "100"]]},
        "lưu chuyển tiền thuần từ hoạt động kinh doanh",
        maximum=1,
    )
    assert candidates == [
        {
            "row_rank": 1,
            "row_index": 0,
            "row_label": "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
            "row_label_column_index": 1,
            "row_token_jaccard": 1.0,
            "numeric_cell_indices": [2],
            "numeric_cell_sha256": [hashlib.sha256(b"100").hexdigest()],
            "row_sha256": hashlib.sha256(
                json.dumps(["01", "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "100"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
    ]


def test_row_candidate_keeps_a_text_label_with_report_reference_numbers() -> None:
    candidates = _row_candidates(
        {"rows": [["Lợi nhuận kế toán trước thuế (50 = 30 + 40)", "100"]]},
        "lợi nhuận kế toán trước thuế",
        maximum=1,
    )
    assert candidates[0]["row_label_column_index"] == 0
    assert candidates[0]["row_token_jaccard"] == 1.0


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_candidate_queue_is_scope_visible_and_value_blind(tmp_path: Path) -> None:
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
            "rows": [["Doanh thu thuần", "100"], ["Chi phí", "20"]],
            "source_path": "/source/AAA.txt",
            "source_sha256": "a" * 64,
            "table_sha256": "b" * 64,
            "local_ordinal": 4,
            "char_start": 120,
            "page_no": 7,
        },
        {
            "internal_table_uid": "u2",
            "document_id": "AAA_2023_separate",
            "ticker": "AAA",
            "report_year": 2023,
            "scope": "separate",
            "headers": ["Chỉ tiêu", "2023"],
            "row_paths": ["Doanh thu thuần > 90"],
            "rows": [["Doanh thu thuần", "90"]],
            "source_path": "/source/AAA_separate.txt",
            "source_sha256": "c" * 64,
            "table_sha256": "d" * 64,
            "local_ordinal": 2,
            "char_start": 55,
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
    index = tmp_path / "lexical.sqlite"
    build_lexical_index(
        asset_path=assets,
        source_closure_path=source_closure,
        index_path=index,
        contract=contract,
        progress_every=0,
    )
    plans = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans,
        [
            {
                "question_id": 1,
                "question": "Doanh thu thuần của AAA năm 2023?",
                "decomposition_status": "complete",
                "operands": [
                    {
                        "operand_id": "x0",
                        "ticker": "AAA",
                        "years": [2023],
                        "scope": "consolidated",
                        "metric_hints": ["Doanh thu thuần thương hiệu không có trong bảng"],
                    }
                ],
            },
            {
                "question_id": 2,
                "question": "Thiếu metadata",
                "decomposition_status": "abstain",
                "operands": [],
            },
        ],
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "vifinqa_full_corpus_candidate_retrieval_v1",
                "expected_question_count": 2,
                "eligible_decomposition_statuses": ["complete"],
                "retrieval": {
                    "match_mode": "any",
                    "top_k_tables_per_operand_year": 2,
                    "max_row_candidates_per_table": 1,
                    "required_filters": ["ticker", "report_year"],
                    "scope_policy": "explicit_first_then_tagged_unscoped_supplement",
                },
                "review": {
                    "include_raw_numeric_values": False,
                    "exact_column_unresolved": True,
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
    output = tmp_path / "output"
    coverage = build_candidate_artifact(
        config_path=config,
        plans_path=plans,
        lexical_index_path=index,
        assets_path=assets,
        output_dir=output,
    )
    candidates = [
        json.loads(line)
        for line in (output / "table_candidates_v1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["retrieval_lane"] for row in candidates] == [
        "explicit_scope",
        "unscoped_supplement",
    ]
    assert [row["scope_match"] for row in candidates] == [True, False]
    packets = [
        json.loads(line)
        for line in (output / "row_review_queue_v1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert packets
    assert all(packet["raw_numeric_values_included"] is False for packet in packets)
    assert all("raw_value" not in packet and "answer" not in packet for packet in packets)
    assert all("human_verified" not in packet for packet in packets)
    assert all("numeric_cell_sha256" in packet for packet in packets)
    assert coverage["question_count"] == 2
    assert coverage["ineligible_question_count"] == 1
    result = validate_candidate_artifact(output, expected_question_count=2)
    assert result["status"] == "PASS"


def test_metric_core_query_removes_trailing_issuer_but_keeps_metric() -> None:
    assert metric_core_query(
        "Lãi tiền gửi năm CTCP Hàng không Vietjet", ticker="VJC"
    ) == "Lãi tiền gửi năm"
    assert metric_core_query(
        "Số dư cho vay khách hàng ngành Thương mại Ngân hàng TMCP Á Châu cuối năm",
        ticker="ACB",
    ) == "Số dư cho vay khách hàng ngành Thương mại"


def test_metric_query_keeps_one_canonical_row_label_not_all_alias_tokens() -> None:
    assert _metric_query(
        {"question": "Câu hỏi dài không được dùng khi đã có metric hint."},
        {
            "ticker": "BSR",
            "metric_hints": [
                "BSR — Lợi nhuận sau thuế 2019",
                "lợi nhuận sau thuế",
                "lợi nhuận ròng",
            ],
        },
    ) == "Lợi nhuận sau thuế"
