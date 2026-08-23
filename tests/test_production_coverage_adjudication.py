from __future__ import annotations

import json
from pathlib import Path

from finance_query.production_coverage_adjudication import build


ROOT = Path(__file__).resolve().parents[1]


def test_builds_pending_fail_closed_queue(tmp_path: Path) -> None:
    root = ROOT / "artifacts/research/production_coverage_iteration_v2"
    result = build(
        bindings=root / "exact_cell_unit_binding_candidates_v2.jsonl",
        bindings_manifest=root / "exact_cell_unit_binding_candidates_v2.manifest.json",
        execution=root / "grounded_execution_replay_v2.jsonl",
        execution_manifest=root / "grounded_execution_replay_v2.manifest.json",
        period_packets=ROOT / "artifacts/research/period_column_candidates_v1/period_column_candidate_packets_v1.jsonl",
        period_manifest=ROOT / "artifacts/research/period_column_candidates_v1/period_column_candidate_packets_v1.manifest.json",
        route_overlay=ROOT / "artifacts/research/route_completeness_v2/route_completeness_overlay_v2.jsonl",
        route_overlay_manifest=ROOT / "artifacts/research/route_completeness_v2/route_completeness_overlay_v2.manifest.json",
        no_candidate_audit=ROOT / "artifacts/research/period_column_candidates_v1/route_packet_no_candidate_audit_v1.jsonl",
        output=tmp_path / "queue.jsonl",
    )
    assert result["counts"]["queue_count"] == 45
    assert result["counts"]["decision_pending_count"] == 45
    rows = [json.loads(line) for line in (tmp_path / "queue.jsonl").read_text().splitlines()]
    assert all(row["decision_contract"]["eligible_for_materialization"] is False for row in rows)
    assert all(row["source_contract"]["promotion_allowed"] is False for row in rows)
    assert {row["question_id"] for row in rows} == {2, 10, 14, 28, 34, 45, 67, 70, 98, 104, 118, 132, 135, 145, 152, 155, 156, 167, 176, 185, 192, 198, 201, 211, 231, 239, 242, 245, 263, 264, 272, 284, 292, 302, 315, 316, 325, 329, 340, 341, 350, 356, 357, 361, 730}
