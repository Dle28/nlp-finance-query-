"""Materialize an intentionally empty, candidate-only semantic hand-off.

The deterministic E2E pipeline does not consume this file as an authorization
source.  It is kept separate so a future human review can be attached only
after the exact binding and V2/V3 inputs have been independently checked.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build(*, output_dir: Path, bindings: Path, bindings_manifest: Path, period_packets: Path,
          period_manifest: Path, structured_tables: Path, evidence_context: Path,
          evidence_context_manifest: Path) -> dict[str, Any]:
    inputs = {
        "bindings": bindings,
        "bindings_manifest": bindings_manifest,
        "period_packets": period_packets,
        "period_manifest": period_manifest,
        "structured_tables": structured_tables,
        "evidence_context": evidence_context,
        "evidence_context_manifest": evidence_context_manifest,
    }
    missing = [f"{name}={path}" for name, path in inputs.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing semantic queue input: " + ", ".join(missing))
    output_dir.mkdir(parents=True, exist_ok=False)
    queue = output_dir / "semantic_binding_review_queue_v1.jsonl"
    decisions = output_dir / "semantic_binding_human_decisions_v1.jsonl"
    queue.write_bytes(b"")
    decisions.write_bytes(b"")
    result: dict[str, Any] = {
        "schema_version": 1,
        "protocol": "vifinqa_semantic_binding_review_queue_v1",
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "inputs": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in inputs.items()
        },
        "outputs": {
            "queue": {"path": str(queue), "sha256": sha256_file(queue)},
            "blank_decisions": {"path": str(decisions), "sha256": sha256_file(decisions)},
        },
        "counts": {
            "review_item_count": 0,
            "human_decision_count": 0,
            "human_verified_count": 0,
            "semantic_approval_count": 0,
        },
        "status": "EMPTY_REVIEW_QUEUE",
        "source_contract": {
            "candidate_only": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
            "may_materialize_answer": False,
        },
        "authorization": {
            "eligible_for_authorization": False,
            "authorization_status": "blocked",
            "reason": "No human semantic decisions are present; this artifact cannot authorize evidence, answer, release, or submission.",
        },
    }
    manifest = output_dir / "semantic_binding_review_queue_v1.manifest.json"
    write_json(manifest, result)
    return {**result, "manifest_path": str(manifest), "manifest_sha256": sha256_file(manifest)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--bindings-manifest", type=Path, required=True)
    parser.add_argument("--period-packets", type=Path, required=True)
    parser.add_argument("--period-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--evidence-context-manifest", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        output_dir=args.output_dir,
        bindings=args.bindings,
        bindings_manifest=args.bindings_manifest,
        period_packets=args.period_packets,
        period_manifest=args.period_manifest,
        structured_tables=args.structured_tables,
        evidence_context=args.evidence_context,
        evidence_context_manifest=args.evidence_context_manifest,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
