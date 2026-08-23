from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from finance_query.semantic_approvals import SEMANTIC_REVIEW_PROTOCOL, canonical_sha256


spec = importlib.util.spec_from_file_location(
    "build_semantic_binding_human_handoff",
    Path(__file__).parents[1] / "scripts" / "build_semantic_binding_human_handoff.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _write_queue(tmp_path: Path) -> tuple[Path, Path]:
    payload = {
        "protocol": SEMANTIC_REVIEW_PROTOCOL,
        "question_id": 1,
        "stage_id": "stage_1",
        "role": "metric",
        "candidate_variable_id": "metric",
        "requested_entity": "AAA",
        "requested_scope": "separate",
        "document_uid": "doc",
        "internal_table_uid": "table",
        "value_cell": {"row_index": 2, "column_index": 3, "raw_text_sha256": "a" * 64},
        "row_label_candidates": [],
        "source_title": "Report",
    }
    row = {**payload, "queue_item_sha256": canonical_sha256(payload)}
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps(row) + "\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "protocol": SEMANTIC_REVIEW_PROTOCOL,
        "outputs": {"queue": {"sha256": module.sha256_file(queue)}},
    }))
    return queue, manifest


def test_build_handoff_remains_blank_and_non_authorizing(tmp_path: Path) -> None:
    queue, manifest = _write_queue(tmp_path)
    result = module.build_handoff(queue=queue, queue_manifest=manifest, output_dir=tmp_path / "out")
    response = json.loads(Path(result["outputs"]["response_template"]["path"]).read_text())
    assert response["decision"] is None
    assert response["decision_provenance"]["reviewer_type"] is None
    assert response["is_blank_human_template"] is True
    assert result["counts"]["human_decision_count"] == 0
    assert result["source_contract"]["authorization_allowed"] is False


def test_build_handoff_rejects_stale_queue(tmp_path: Path) -> None:
    queue, manifest = _write_queue(tmp_path)
    queue.write_text(queue.read_text() + "\n")
    try:
        module.build_handoff(queue=queue, queue_manifest=manifest, output_dir=tmp_path / "out")
    except ValueError as exc:
        assert "lineage" in str(exc)
    else:
        raise AssertionError("stale queue must be rejected")
