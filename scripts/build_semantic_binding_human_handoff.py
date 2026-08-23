#!/usr/bin/env python3
"""Build a hash-bound, blank human-review handoff from a semantic queue.

This script never creates approvals. The response template is deliberately
invalid for authorization until a human fills every required provenance field.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.semantic_approvals import (
    SEMANTIC_DECISION_PROTOCOL,
    SEMANTIC_REVIEW_PROTOCOL,
    canonical_sha256,
)


HANDOFF_PROTOCOL = "vifinqa_semantic_binding_human_handoff_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        result.append(row)
    return result


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_handoff(*, queue: Path, queue_manifest: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite human handoff: {output_dir}")
    manifest = json.loads(queue_manifest.read_text(encoding="utf-8"))
    expected_queue_sha = ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256")
    queue_sha = sha256_file(queue)
    if manifest.get("protocol") != SEMANTIC_REVIEW_PROTOCOL or expected_queue_sha != queue_sha:
        raise ValueError("semantic review queue lineage is invalid")

    items = _load_jsonl(queue)
    seen: set[str] = set()
    responses: list[dict[str, Any]] = []
    for item in items:
        item_sha = str(item.get("queue_item_sha256") or "")
        payload = {key: value for key, value in item.items() if key != "queue_item_sha256"}
        if not item_sha or item_sha in seen or item_sha != canonical_sha256(payload):
            raise ValueError("semantic review queue item identity is invalid")
        seen.add(item_sha)
        responses.append(
            {
                "schema_version": 1,
                "protocol": SEMANTIC_DECISION_PROTOCOL,
                "queue_item_sha256": item_sha,
                "source_review_queue_sha256": queue_sha,
                "decision": None,
                "approved_variable_id": None,
                "approved_entity": None,
                "approved_entity_role": None,
                "approved_scope": None,
                "entity_role_evidence_checked": False,
                "entity_role_source_title_sha256": None,
                "selected_row_label": None,
                "source_coordinates_checked": False,
                "decision_provenance": {"reviewer_type": None, "reviewer_id": None},
                "reviewed_at": None,
                "notes": "",
                "is_blank_human_template": True,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    response_path = output_dir / "semantic_binding_human_response_template_v1.jsonl"
    review_path = output_dir / "semantic_binding_human_review_sheet_v1.md"
    _write_jsonl(response_path, responses)
    lines = [
        "# Semantic binding human review sheet",
        "",
        "This packet is candidate-only. It contains no human approval and cannot authorize an answer.",
        "For each item, inspect the immutable source coordinates, choose approve/reject/uncertain, and fill the matching JSONL response row. An approval additionally requires a row-label candidate, checked coordinates, approved variable/entity/scope, reviewer ID, timestamp, and notes.",
        "",
    ]
    for index, item in enumerate(items, 1):
        value = item.get("value_cell") or {}
        lines.extend(
            [
                f"## {index}. Question {item.get('question_id')} / {item.get('stage_id')} / {item.get('role')}",
                "",
                f"- Queue item SHA-256: `{item['queue_item_sha256']}`",
                f"- Candidate variable: `{item.get('candidate_variable_id')}`",
                f"- Requested entity/scope: `{item.get('requested_entity')}` / `{item.get('requested_scope')}`",
                f"- Source title: {item.get('source_title') or '(missing)'}",
                f"- Document/table: `{item.get('document_uid')}` / `{item.get('internal_table_uid')}`",
                f"- Value coordinates: row `{value.get('row_index')}`, column `{value.get('column_index')}`, raw SHA-256 `{value.get('raw_text_sha256')}`",
                "- Row-label candidates:",
            ]
        )
        for candidate in item.get("row_label_candidates") or []:
            lines.append(
                f"  - row `{candidate.get('row_index')}`, column `{candidate.get('column_index')}`: "
                f"`{candidate.get('raw_text')}` (SHA-256 `{candidate.get('raw_text_sha256')}`)"
            )
        lines.extend(["", "Decision: `[ ] approve  [ ] reject  [ ] uncertain`", ""])
    review_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = {
        "schema_version": 1,
        "protocol": HANDOFF_PROTOCOL,
        "status": "blank_human_review_handoff_not_authorized",
        "inputs": {
            "queue": {"path": str(queue), "sha256": queue_sha},
            "queue_manifest": {"path": str(queue_manifest), "sha256": sha256_file(queue_manifest)},
        },
        "outputs": {
            "response_template": {"path": str(response_path), "sha256": sha256_file(response_path)},
            "review_sheet": {"path": str(review_path), "sha256": sha256_file(review_path)},
        },
        "counts": {
            "review_item_count": len(items),
            "blank_response_count": len(responses),
            "human_decision_count": 0,
        },
        "source_contract": {
            "candidate_only": True,
            "human_verified": False,
            "authorization_allowed": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    manifest_path = output_dir / "semantic_binding_human_handoff_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {**result, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--queue-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_handoff(queue=args.queue, queue_manifest=args.queue_manifest, output_dir=args.output_dir), indent=2))


if __name__ == "__main__":
    main()
