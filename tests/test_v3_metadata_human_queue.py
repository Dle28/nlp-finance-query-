from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.v3_metadata_human_queue import build_human_approval_queue


def test_queue_contains_only_unapproved_metadata_proposals(tmp_path: Path) -> None:
    reviews = tmp_path / "reviews.jsonl"
    rows = [{"decision": "accept_repair", "proposed_patch": {"scope": "metadata"}, "question_id": number, "stage_id": "s", "role": "r", "authoritative_sources": [], "reason_codes": []} for number in range(1, 28)]
    rows.append({"decision": "reject_repair", "question_id": 99})
    reviews.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = tmp_path / "reviews.manifest.json"
    manifest.write_text(json.dumps({"outputs": {"metadata_reviews": {"sha256": hashlib.sha256(reviews.read_bytes()).hexdigest()}}}), encoding="utf-8")
    result = build_human_approval_queue(review_manifest_path=manifest, metadata_reviews_path=reviews, output_dir=tmp_path / "out")
    values = [json.loads(line) for line in Path(result["outputs"]["queue"]["path"]).read_text().splitlines()]
    assert len(values) == 27
    assert all(row["human_verified"] is False and row["eligible_for_materialization"] is False for row in values)
