from __future__ import annotations

import json
from pathlib import Path

from finance_query.review_sidecar_handoff import build_review_sidecar_handoff_audit, sha256_file


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_handoff_never_carries_v1_decision_forward(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.jsonl"; period = tmp_path / "period.jsonl"; v2 = tmp_path / "v2.jsonl"; manifest = tmp_path / "manifest.json"
    _jsonl(metadata, [{"question_id": 1, "stage_id": "s", "role": "r", "source_diagnosis": "ENTITY_ALIAS_CONFLICT", "decision": "reject_repair"}])
    _jsonl(period, [{"question_id": 2, "decision": "accept_repair"}])
    _jsonl(v2, [{"question_id": 1, "stage_id": "s", "role": "r", "diagnosis": "TARGET_ROW_UNMAPPED"}])
    manifest.write_text(json.dumps({"outputs": {"metadata_reviews": {"sha256": sha256_file(metadata)}, "period_reviews": {"sha256": sha256_file(period)}}}), encoding="utf-8")
    result = build_review_sidecar_handoff_audit(review_manifest_path=manifest, metadata_reviews_path=metadata, period_reviews_path=period, v2_metadata_queue_path=v2, output_dir=tmp_path / "out")
    rows = [json.loads(line) for line in Path(result["outputs"]["handoff_audit"]["path"]).read_text().splitlines()]
    assert rows[0]["handoff_status"] == "SEMANTIC_DIAGNOSIS_CHANGED_REVIEW_REQUIRED"
    assert rows[1]["handoff_status"] == "NO_V2_PERIOD_QUEUE_REVIEW_REQUIRED"
    assert all(row["eligible_for_materialization"] is False for row in rows)
