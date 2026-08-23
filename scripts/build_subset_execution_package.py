#!/usr/bin/env python3
"""Materialize a conservative grounded execution subset.

This is a training/evaluation sidecar, not an official full-corpus submission.
Only rows present in both a grounded execution ledger and the dual-replay
machine-silver set are retained.  Unselected questions are deliberately not
rewritten and no provenance is promoted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


PROTOCOL = "subset_execution_package_shadow_v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize(ledger_path: Path, silver_path: Path, output: Path) -> dict[str, Any]:
    ledger = {int(row["id"]): row for row in load_jsonl(ledger_path)}
    silver = {int(row["id"]): row for row in load_jsonl(silver_path)}
    selected: list[dict[str, Any]] = []
    blocked = Counter()
    for qid in sorted(silver):
        row = ledger.get(qid)
        if row is None:
            blocked["missing_execution_record"] += 1
            continue
        if row.get("execution_status") != "grounded":
            blocked["execution_not_grounded"] += 1
            continue
        if row.get("grounding_status") != "exact_rows_validated":
            blocked["grounding_not_exact"] += 1
            continue
        if row.get("provenance_status") not in {"human_verified", "machine_calibrated"}:
            blocked["provenance_not_allowed"] += 1
            continue
        if silver[qid].get("annotation_status") != "machine_calibrated" and not silver[qid].get("human_verified"):
            blocked["silver_not_dual_gated"] += 1
            continue
        selected.append(
            {
                **row,
                "subset_execution_eligible": True,
                "submission_eligible": False,
                "provenance_promotion_allowed": False,
                "subset_selection": "dual_replay_machine_silver_and_exact_grounded_ledger_v1",
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
    temporary.replace(output)
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "coverage_mode": "subset_shadow",
        "question_count": len(selected),
        "input_ledger_question_count": len(ledger),
        "input_silver_question_count": len(silver),
        "blocked_counts": dict(sorted(blocked.items())),
        "ledger_sha256": sha256_file(ledger_path),
        "silver_sha256": sha256_file(silver_path),
        "answer_eligible": False,
        "training_eligible": True,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "sidecar_sha256": sha256_file(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-ledger", type=Path, required=True)
    parser.add_argument("--machine-silver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = materialize(args.execution_ledger.resolve(), args.machine_silver.resolve(), args.output.resolve())
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
