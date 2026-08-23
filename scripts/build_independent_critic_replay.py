#!/usr/bin/env python3
"""Build a hash-bound critic replay without any machine-review input."""
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
from finance_query.independent_critic import (  # noqa: E402
    INDEPENDENT_CRITIC_PROTOCOL,
    INDEPENDENT_CRITIC_SCHEMA_VERSION,
    critique_direct_evidence,
)
from finance_query.plan_overrides import apply_plan_overrides, validate_plan_overrides  # noqa: E402


def _write(path: Path, payload: Any, *, jsonl: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        if jsonl:
            for row in payload:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        else:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _validate_direct_manifest(bundle: Path, evidence: Path) -> dict[str, Any]:
    manifest_path = evidence.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "bundle_review_items_sha256": bundle / "review_items.jsonl",
        "bundle_tables_sha256": bundle / "tables.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
    }
    for key, path in expected.items():
        if manifest.get(key) != sha256_file(path):
            raise ValueError(f"Direct evidence lineage mismatch: {key}")
    if manifest.get("sidecar_sha256") != sha256_file(evidence):
        raise ValueError("Direct evidence sidecar hash mismatch")
    return manifest


def build(
    bundle: Path,
    evidence_path: Path,
    context_path: Path,
    output: Path,
    override_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    direct_manifest = _validate_direct_manifest(bundle, evidence_path)
    items = load_jsonl(bundle / "review_items.jsonl")
    override_name = str(direct_manifest.get("question_plan_override_file") or "")
    if override_name:
        override_path = (override_path or bundle / override_name).resolve()
        expected_override_hash = str(direct_manifest.get("question_plan_overrides_sha256") or "")
        if override_path.name != override_name or sha256_file(override_path) != expected_override_hash:
            raise ValueError("Question-plan override file does not match direct-evidence lineage")
    elif override_path is not None:
        raise ValueError("Override supplied but direct-evidence manifest has no override dependency")
    overrides = validate_plan_overrides(items, load_jsonl(override_path)) if override_path else {}
    effective_items = apply_plan_overrides(items, overrides)
    item_by_id = {int(item["id"]): item for item in effective_items}
    evidence_rows = load_jsonl(evidence_path)
    tables = load_v2_tables(bundle)
    contexts = load_evidence_contexts(bundle, context_path)
    rows = []
    for evidence in evidence_rows:
        qid = int(evidence["id"])
        item = item_by_id.get(qid)
        if item is None:
            raise ValueError(f"Direct evidence Q{qid} is absent from review_items")
        plan = item.get("question_plan") or {}
        rows.append(critique_direct_evidence(evidence, plan, tables, contexts))
    _write(output, rows, jsonl=True)
    manifest = {
        "schema_version": INDEPENDENT_CRITIC_SCHEMA_VERSION,
        "protocol": INDEPENDENT_CRITIC_PROTOCOL,
        "question_count": len(rows),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "raw_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(bundle / "tables_structured_v2.jsonl"),
        "evidence_context_sha256": sha256_file(context_path),
        "direct_evidence_sha256": sha256_file(evidence_path),
        "direct_evidence_manifest_sha256": sha256_file(evidence_path.with_suffix(".manifest.json")),
        "question_plan_overrides_sha256": sha256_file(override_path) if override_path else None,
        "machine_reviews_sha256": None,
        "reviewer_inputs_used": [],
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "sidecar_sha256": sha256_file(output),
    }
    _write(output.with_suffix(".manifest.json"), manifest)
    return rows, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--direct-evidence", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path)
    parser.add_argument("--question-plan-overrides", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    context = (args.evidence_context or bundle / "tables_evidence_context_v3.jsonl").resolve()
    if context.parent != bundle:
        raise ValueError("Evidence context must be bundle-local")
    rows, manifest = build(
        bundle,
        args.direct_evidence.resolve(),
        context,
        args.output.resolve(),
        args.question_plan_overrides.resolve() if args.question_plan_overrides else None,
    )
    print(json.dumps({"rows": len(rows), **manifest["status_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
