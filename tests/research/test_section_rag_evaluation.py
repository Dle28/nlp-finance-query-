from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from finance_query.e2e.core.dense_retrieval import build_dense_index
from finance_query.research.section_rag_evaluation import (
    SECTION_CONTRACT,
    build_section_chunk_assets,
    build_section_rag_table_baseline,
    evaluate_section_hierarchical_rag,
    validate_section_chunk_assets,
    validate_section_rag_table_baseline,
    validate_section_hierarchical_rag,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class _Encoder:
    def encode(self, sentences: list[str], **_: object) -> np.ndarray:
        rows = []
        for sentence in sentences:
            seed = float(sum(ord(char) for char in sentence) % 17 + 1)
            vector = np.asarray([seed, seed + 1, seed + 2, seed + 3], dtype=np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.stack(rows)


def test_section_chunks_and_hierarchical_evaluation_are_value_blind(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    _write_jsonl(
        assets,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2024_consolidated",
                "ticker": "AAA",
                "report_year": 2024,
                "scope": "consolidated",
                "local_ordinal": 1,
                "table_sha256": "a" * 64,
                "source_sha256": "b" * 64,
                "char_start": 10,
                "context_before": "",
                "table_function": {"kind": "income_statement", "label": "Báo cáo kết quả kinh doanh"},
                "table_section": {"kind": "income", "label": "Kết quả kinh doanh"},
                "headers": ["Chỉ tiêu", "Năm 2024"],
                "row_paths": ["Doanh thu thuần > 100"],
                "rows": [["Doanh thu thuần", "100"]],
            },
            {
                "internal_table_uid": "u2",
                "document_id": "AAA_2024_consolidated",
                "ticker": "AAA",
                "report_year": 2024,
                "scope": "consolidated",
                "local_ordinal": 2,
                "table_sha256": "c" * 64,
                "source_sha256": "b" * 64,
                "char_start": 20,
                "context_before": "",
                "table_function": {"kind": "income_statement", "label": "Báo cáo kết quả kinh doanh"},
                "table_section": {"kind": "income", "label": "Kết quả kinh doanh"},
                "headers": ["Chỉ tiêu", "Năm 2024"],
                "row_paths": ["Chi phí bán hàng > 200"],
                "rows": [["Chi phí bán hàng", "200"]],
            },
        ],
    )
    assets_manifest = tmp_path / "assets.manifest.json"
    _write_json(assets_manifest, {"outputs": {assets.name: {"sha256": _sha(assets)}}})
    section_dir = tmp_path / "sections"
    summary = build_section_chunk_assets(
        assets_path=assets,
        assets_manifest_path=assets_manifest,
        output_dir=section_dir,
        max_tables_per_chunk=8,
    )
    assert summary["section_chunk_count"] == 1
    assert validate_section_chunk_assets(section_dir)["status"] == "PASS"
    section_row = json.loads((section_dir / "section_chunk_assets_v1.jsonl").read_text(encoding="utf-8"))
    rendered = json.dumps(section_row, ensure_ascii=False)
    assert section_row["source_contract"] == SECTION_CONTRACT
    assert "100" not in rendered and "200" not in rendered and "rows" not in rendered

    dense_dir = tmp_path / "dense"
    encoder = _Encoder()
    build_dense_index(
        asset_path=section_dir / "section_chunk_assets_v1.jsonl",
        source_closure_path=section_dir / "section_source_closure_v1.jsonl",
        output_dir=dense_dir,
        contract=json.loads((section_dir / "section_dense_contract_v1.json").read_text(encoding="utf-8")),
        model_name="fake-e5",
        requested_device="cpu",
        batch_size=2,
        encoder_factory=lambda _model, _device: encoder,
    )

    hybrid_dir = tmp_path / "hybrid"
    hybrid_dir.mkdir()
    routes = hybrid_dir / "route_comparison_v1.jsonl"
    candidates = hybrid_dir / "hybrid_table_candidates_v1.jsonl"
    _write_jsonl(
        routes,
        [{"route_id": "r1", "question_id": 1, "operand_id": "x0", "metric_core_query": "doanh thu thuần", "ticker": "AAA", "report_year": 2024, "requested_scope": "consolidated"}],
    )
    _write_jsonl(candidates, [{"route_id": "r1", "question_id": 1, "internal_table_uid": "u1", "hybrid_rank": 1, "human_verified": False}])
    _write_json(hybrid_dir / "manifest.json", {"outputs": {routes.name: {"sha256": _sha(routes)}, candidates.name: {"sha256": _sha(candidates)}}})
    baseline_dir = tmp_path / "baseline"
    baseline = build_section_rag_table_baseline(hybrid_artifact_dir=hybrid_dir, output_dir=baseline_dir)
    assert baseline["candidate_count"] == 1
    assert validate_section_rag_table_baseline(baseline_dir, expected_route_count=1)["status"] == "PASS"
    assert "human_verified" not in (baseline_dir / "hybrid_table_candidates_v1.jsonl").read_text(encoding="utf-8")
    config = tmp_path / "config.json"
    _write_json(
        config,
        {
            "protocol": "vifinqa_section_hierarchical_rag_evaluation_v1",
            "expected_route_count": 1,
            "retrieval": {"parent_top_k": 3, "output_table_top_k": 3, "rrf_constant": 60, "encode_batch_size": 8, "period_neighbor": {"enabled": True, "report_year_offset": 1, "parent_top_k": 3, "two_period_quota": {"enabled": True, "standard_hierarchical_slots": 1, "period_neighbor_slots": 2}}},
        },
    )
    evaluation_dir = tmp_path / "evaluation"
    coverage = evaluate_section_hierarchical_rag(
        config_path=config,
        section_artifact_dir=section_dir,
        section_dense_index_dir=dense_dir,
        hybrid_artifact_dir=baseline_dir,
        output_dir=evaluation_dir,
        encoder_factory=lambda _model, _device: encoder,
    )
    assert coverage["accuracy_claim"] == "NOT_AVAILABLE_WITHOUT_INDEPENDENT_SOURCE_ADJUDICATED_GOLD"
    diagnostic = coverage["diagnostic_target_recall"]
    assert diagnostic["lane_question_full_target_coverage_counts"]["table_only"] == 0
    assert diagnostic["lane_target_table_hit_counts"]["table_only"] == 0
    row = json.loads((evaluation_dir / "section_hierarchical_route_evaluation_v1.jsonl").read_text(encoding="utf-8"))
    assert row["section_only_table_uids"] == ["u1", "u2"]
    assert row["period_neighbor_section_only_table_uids"] == ["u1", "u2"]
    assert row["period_aware_quota_table_uids"] == ["u1", "u2"]
    assert row["hierarchical_table_uids"]
    assert validate_section_hierarchical_rag(evaluation_dir, expected_route_count=1)["status"] == "PASS"


def test_materialized_navigation_extension_remains_value_blind(tmp_path: Path) -> None:
    hybrid_dir = tmp_path / "hybrid"
    hybrid_dir.mkdir()
    routes = hybrid_dir / "route_comparison_v1.jsonl"
    candidates = hybrid_dir / "hybrid_table_candidates_v1.jsonl"
    _write_jsonl(
        routes,
        [{"route_id": "r1", "question_id": 1, "operand_id": "x0", "metric_core_query": "doanh thu", "ticker": "AAA", "report_year": 2024, "requested_scope": "consolidated"}],
    )
    _write_jsonl(candidates, [{"route_id": "r1", "question_id": 1, "internal_table_uid": "u1", "hybrid_rank": 1, "human_verified": False}])
    _write_json(hybrid_dir / "manifest.json", {"outputs": {routes.name: {"sha256": _sha(routes)}, candidates.name: {"sha256": _sha(candidates)}}})

    full_dir = tmp_path / "full"
    full_dir.mkdir()
    full_candidates = full_dir / "table_candidates_v1.jsonl"
    _write_jsonl(
        full_candidates,
        [{
            "question_id": 2,
            "operand_id": "other_income",
            "metric_core_query": "Thu nhập khác",
            "ticker": "AAA",
            "report_year": 2024,
            "requested_scope": "consolidated",
            "internal_table_uid": "u2",
            "candidate_rank": 1,
            "navigation_metadata_only": True,
            "may_authorize_answer": False,
            "may_authorize_evidence": False,
            "submission_eligible": False,
            "training_eligible": False,
        }],
    )
    _write_json(full_dir / "manifest.json", {"outputs": {full_candidates.name: {"sha256": _sha(full_candidates)}}})

    materialized_dir = tmp_path / "materialized"
    materialized_dir.mkdir()
    audit = materialized_dir / "single_period_subtract_route_audit_v1.jsonl"
    materialized_contract = {
        "research_only": True,
        "machine_recheck_only": True,
        "evidence_eligible": False,
        "may_materialize_answer": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    _write_jsonl(audit, [{
        "question_id": 2,
        "materialization_status": "MATERIALIZED_SINGLE_PERIOD_SUBTRACT_EXECUTION_CANDIDATE",
        "raw_numeric_values_included": False,
        "source_contract": materialized_contract,
    }])
    _write_json(materialized_dir / "manifest.json", {"source_contract": materialized_contract, "outputs": {audit.name: {"sha256": _sha(audit)}}})

    baseline_dir = tmp_path / "baseline"
    result = build_section_rag_table_baseline(
        hybrid_artifact_dir=hybrid_dir,
        output_dir=baseline_dir,
        full_corpus_artifact_dir=full_dir,
        route_materialization_dir=materialized_dir,
        navigation_extension_question_ids=[2],
    )
    assert result["route_count"] == 2
    assert result["navigation_extension_route_count"] == 1
    assert validate_section_rag_table_baseline(baseline_dir, expected_route_count=2)["status"] == "PASS"
    rendered = (baseline_dir / "route_comparison_v1.jsonl").read_text(encoding="utf-8")
    assert "human_verified" not in rendered
    assert "materialized_navigation:q2:other_income" in rendered
