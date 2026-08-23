#!/usr/bin/env python3
"""Materialize a conservative, non-production execution subset.

This artifact is for training/evaluation coverage only.  It intentionally
does not change provenance and marks every emitted row as ineligible for the
full submission compiler.  A row must be present in the dual-gated silver
selection, pass the independent audit, and have an exact grounded execution
record.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from typing import Any


PROTOCOL = "shadow_subset_execution_ledger_v1"
SCHEMA_VERSION = 1


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index(rows: list[dict[str, Any]], name: str, key: str = "id") -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        raw_id = row.get(key, row.get("question_id"))
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            raise ValueError(f"{name} contains invalid question id: {raw_id!r}")
        if raw_id in output:
            raise ValueError(f"{name} contains duplicate question id: {raw_id}")
        output[raw_id] = row
    return output


def build_subset(
    execution_ledger: Path,
    selection: Path,
    audit: Path,
    output: Path,
) -> dict[str, Any]:
    ledger = index(load_jsonl(execution_ledger), "execution ledger")
    selected = index(load_jsonl(selection), "dual-gated selection")
    audited = index(load_jsonl(audit), "independent audit")
    reasons: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for qid, label in sorted(selected.items()):
        if str(label.get("annotation_status") or "") != "machine_calibrated":
            reasons["selection_not_machine_calibrated"] += 1
            continue
        critic = label.get("independent_critic_gate") or {}
        replay = label.get("direct_replay_gate") or {}
        if str(critic.get("status") or "") != "independent_ready":
            reasons["independent_critic_gate_failed"] += 1
            continue
        if str(replay.get("status") or "") != "shadow_replay_ready":
            reasons["direct_replay_gate_failed"] += 1
            continue
        audit_row = audited.get(qid)
        if audit_row is None or str(audit_row.get("independent_audit_status") or "") != "passed":
            reasons["production_audit_not_passed"] += 1
            continue
        execution = ledger.get(qid)
        if execution is None or str(execution.get("execution_status") or "") != "grounded":
            reasons["exact_execution_not_grounded"] += 1
            continue
        # Preserve the original ledger/provenance, but make the non-production
        # boundary explicit so this file cannot be passed to the full compiler.
        rows.append(
            {
                **execution,
                "coverage_mode": PROTOCOL,
                "subset_selection_source": selection.name,
                "submission_eligible": False,
                "training_eligible": True,
                "production_eligibility": {
                    "status": "shadow_subset_only",
                    "independent_audit_status": "passed",
                },
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(output)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "coverage_mode": "subset_training_evaluation",
        "source_question_count": len(ledger),
        "dual_gated_selection_count": len(selected),
        "subset_question_count": len(rows),
        "excluded_count": len(selected) - len(rows),
        "excluded_reason_counts": dict(sorted(reasons.items())),
        "submission_eligible": False,
        "training_eligible": True,
        "provenance_promotion_allowed": False,
        "execution_ledger_sha256": sha256_file(execution_ledger),
        "selection_sha256": sha256_file(selection),
        "audit_sha256": sha256_file(audit),
        "sidecar_sha256": sha256_file(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-ledger", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_subset(args.execution_ledger, args.selection, args.audit, args.output)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
