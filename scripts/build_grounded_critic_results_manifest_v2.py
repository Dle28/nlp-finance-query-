#!/usr/bin/env python3
"""Hash-bind validated closed-world critic dry-run/model results V2."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from finance_query.grounded_critic_protocol import validate_critic_response


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packets-manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--run-mode", choices=("dry_run", "qwen14b_4bit"), required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--gpu", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    packet_manifest = json.loads(args.packets_manifest.read_text(encoding="utf-8"))
    if sha256_file(args.packets) != packet_manifest["outputs"]["packets"]["sha256"]:
        raise ValueError("SHA-256 mismatch for critic packets")
    packets = {row["question_id"]: row for row in map(json.loads, args.packets.read_text(encoding="utf-8").splitlines()) if row}
    results = [row for row in map(json.loads, args.results.read_text(encoding="utf-8").splitlines()) if row]
    if {row.get("question_id") for row in results} != set(packets):
        raise ValueError("Critic result question ID coverage mismatch")
    # The runner writes its validator-added source contract.  Revalidate the
    # original model-response fields rather than treating that fixed wrapper as
    # a model-supplied schema field.
    validated = [
        validate_critic_response(
            packets[row["question_id"]],
            {key: value for key, value in row.items() if key != "source_contract"},
        )
        for row in results
    ]
    if any(row.get("provenance") != "machine_provisional" for row in validated):
        raise ValueError("Critic result provenance must remain machine_provisional")
    result = {
        "schema_version": 2,
        "protocol": "grounded_critic_results_v2",
        "run_mode": args.run_mode,
        "runtime": args.runtime,
        "gpu": args.gpu,
        "model_revision": args.model_revision,
        "inputs": {
            "packets": {"path": str(args.packets), "sha256": sha256_file(args.packets)},
            "packets_manifest": {"path": str(args.packets_manifest), "sha256": sha256_file(args.packets_manifest)},
        },
        "outputs": {"results": {"path": str(args.results), "sha256": sha256_file(args.results)}},
        "counts": {
            "packet_count": len(packets),
            "result_count": len(validated),
            "status_counts": dict(sorted(Counter(row["status"] for row in validated).items())),
            "schema_valid_count": len(validated),
            "invalid_schema_count": 0,
            "external_evidence_reference_count": 0,
            "numeric_invention_count": 0,
            "candidate_selection_count": 0,
            "machine_provisional_count": len(validated),
            "reason_code_counts": dict(sorted(Counter(code for row in validated for code in (row.get("feedback") or {}).get("reason_codes") or []).items())),
        },
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
            "may_select_final_candidate": False,
            "may_select_value": False,
            "may_execute_formula": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
