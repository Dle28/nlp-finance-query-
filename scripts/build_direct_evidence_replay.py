#!/usr/bin/env python3
"""Build a hash-bound, shadow-only independent replay for direct reviews."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from build_execution_ledger import load_evidence_contexts, load_jsonl, load_v2_tables  # noqa: E402
from finance_query.artifact_registry import sha256_file  # noqa: E402
from finance_query.direct_replay import (  # noqa: E402
    DIRECT_REPLAY_PROTOCOL,
    DIRECT_REPLAY_SCHEMA_VERSION,
    replay_direct_review,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def build_replay(bundle: Path, reviews_path: Path, context_path: Path, output: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tables = load_v2_tables(bundle)
    contexts = load_evidence_contexts(bundle, context_path)
    review_items = load_jsonl(bundle / "review_items.jsonl")
    reviews = load_jsonl(reviews_path)
    item_by_id: dict[int, dict[str, Any]] = {}
    for item in review_items:
        qid = int(item["id"])
        if qid in item_by_id:
            raise ValueError("review_items.jsonl contains duplicate question ids")
        item_by_id[qid] = item
    review_by_id: dict[int, dict[str, Any]] = {}
    for review in reviews:
        qid = int(review["id"])
        if qid in review_by_id:
            raise ValueError("Machine reviews contain duplicate question ids")
        review_by_id[qid] = review
    if set(review_by_id) != set(item_by_id):
        missing = sorted(set(item_by_id) - set(review_by_id))
        extra = sorted(set(review_by_id) - set(item_by_id))
        raise ValueError(
            f"Machine review IDs differ from bundle; missing={missing[:10]}, extra={extra[:10]}"
        )
    for qid, review in review_by_id.items():
        if str(review.get("question") or "") != str(item_by_id[qid].get("question") or ""):
            raise ValueError(f"Q{qid}: machine review question differs from bundle")
    direct_reviews = [
        review_by_id[qid]
        for qid in sorted(review_by_id)
        if str(
            (review_by_id[qid].get("effective_question_plan") or {}).get("family")
            or review_by_id[qid].get("family")
            or ""
        )
        == "direct_lookup"
    ]
    rows = [replay_direct_review(review, tables, contexts) for review in direct_reviews]
    atomic_jsonl(output, rows)
    counts = Counter(str(row["status"]) for row in rows)
    manifest = {
        "schema_version": DIRECT_REPLAY_SCHEMA_VERSION,
        "protocol": DIRECT_REPLAY_PROTOCOL,
        "direct_question_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "raw_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(bundle / "tables_structured_v2.jsonl"),
        "evidence_context_sha256": sha256_file(context_path),
        "machine_reviews_sha256": sha256_file(reviews_path),
        "answer_eligible": False,
        "training_eligible": False,
        "provenance_promotion_allowed": False,
        "sidecar_sha256": sha256_file(output),
    }
    atomic_json(output.with_suffix(".manifest.json"), manifest)
    return rows, manifest


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    context = (args.evidence_context or bundle / "tables_evidence_context_v3.jsonl").resolve()
    if context.parent != bundle:
        raise ValueError("Evidence context must be bundle-local")
    rows, manifest = build_replay(
        bundle,
        args.machine_reviews.resolve(),
        context,
        args.output.resolve(),
    )
    print(json.dumps({"output": str(args.output), "rows": len(rows), **manifest["status_counts"]}))


if __name__ == "__main__":
    main()
