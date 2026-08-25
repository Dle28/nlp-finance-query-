#!/usr/bin/env python3
"""Verify and deterministically replay an active-learning cycle artifact."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.proof_policy.active_learning import PROTOCOL, build_active_learning_cycle  # noqa: E402
from finance_query.research.proof_policy.evidence_closure import load_jsonl, sha256_file  # noqa: E402


FORBIDDEN_KEYS = {"answer", "answer_decimal", "raw_value", "parsed_value", "numeric_value"}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _scan(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                raise ValueError(f"active-learning output leaks forbidden key {key}")
            _scan(item)
    elif isinstance(value, list):
        for item in value:
            _scan(item)


def verify(manifest_path: Path) -> dict[str, Any]:
    manifest = _json(manifest_path)
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected active-learning protocol")
    if (manifest.get("release_decision") or {}).get("status") != "blocked":
        raise ValueError("active-learning cycle must remain release-blocked")
    contract = manifest.get("learning_contract") or {}
    if contract.get("online_self_training") is not False or contract.get("numeric_answer_learning") is not False:
        raise ValueError("active-learning contract permits unsafe self-training")
    if (
        contract.get("competition_model_policy") != "open_source_weights_strictly_below_14.7B_parameters"
        or contract.get("chatgpt_competition_model_eligible") is not False
        or contract.get("chatgpt_training_or_inference_allowed") is not False
        or contract.get("chatgpt_role") != "external_human_equivalent_review_only"
    ):
        raise ValueError("competition model boundary is not open-source-only and ChatGPT-excluded")
    for name, record in (manifest.get("inputs") or {}).items():
        if not isinstance(record, Mapping):
            raise ValueError(f"invalid input record {name}")
        path = Path(str(record.get("path") or ""))
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            raise ValueError(f"input hash mismatch: {name}")
    definition = manifest.get("definition_bundle") or {}
    implementation = Path(str(definition.get("implementation_path") or ""))
    if not implementation.is_file() or sha256_file(implementation) != definition.get("implementation_sha256"):
        raise ValueError("active-learning implementation hash mismatch")
    counts = manifest.get("counts") or {}
    expected_counts = {
        "cluster_inventory": counts.get("cluster_count"),
        "review_batch": counts.get("selected_review_count"),
        "trusted_training_registry": counts.get("trusted_training_record_count"),
        "learned_policy_candidates": counts.get("cluster_count"),
        "audit_ledger": counts.get("cumulative_population_audit_count"),
    }
    review_rows: list[dict[str, Any]] = []
    for name, record in (manifest.get("outputs") or {}).items():
        if not isinstance(record, Mapping):
            raise ValueError(f"invalid output record {name}")
        path = Path(str(record.get("path") or ""))
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            raise ValueError(f"output hash mismatch: {name}")
        if name == "summary":
            _scan(_json(path))
            continue
        rows = load_jsonl(path)
        if len(rows) != expected_counts.get(name):
            raise ValueError(f"output count mismatch: {name}")
        for row in rows:
            _scan(row)
            if row.get("submission_eligible") is True or row.get("promotion_allowed") is True:
                raise ValueError(f"active-learning output promotes a record: {name}")
        if name == "review_batch":
            review_rows = rows
        if name == "trusted_training_registry" and any(row.get("materialization_eligible") is not False for row in rows):
            raise ValueError("learning registry row is materializable")
    question_ids = [int(row["question_id"]) for row in review_rows]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("active-learning and population-audit lanes overlap")
    audit = [row for row in review_rows if row.get("evaluation_role") == "independent_population_audit"]
    if any("chatgpt_proposer" in (row.get("required_reviewer_types") or []) for row in review_rows):
        raise ValueError("ChatGPT appears in the competition proposal model graph")
    if len(audit) != counts.get("population_audit_review_count"):
        raise ValueError("population-audit count mismatch")
    if any(not isinstance(row.get("inclusion_probability"), float) or not 0 < row["inclusion_probability"] <= 1 for row in audit):
        raise ValueError("population-audit row lacks inclusion probability")
    prior_audit_count = int(counts.get("prior_population_audit_count") or 0)
    audit_denominator = int(counts.get("question_count") or 0) - prior_audit_count
    if audit and any(row["inclusion_probability"] != len(audit) / audit_denominator for row in audit):
        raise ValueError("population audit was not sampled from the full not-yet-audited universe")
    config = Path(str(((manifest.get("inputs") or {}).get("config") or {}).get("path") or ""))
    with tempfile.TemporaryDirectory(prefix="active-learning-verify-") as temporary:
        rebuilt_dir = Path(temporary) / "rebuilt"
        build_active_learning_cycle(config_path=config, output_dir=rebuilt_dir)
        for name, record in (manifest.get("outputs") or {}).items():
            filename = Path(str(record.get("path") or "")).name
            rebuilt = rebuilt_dir / filename
            if not rebuilt.is_file() or sha256_file(rebuilt) != record.get("sha256"):
                raise ValueError(f"deterministic replay mismatch: {name}")
    return {
        "status": "VERIFIED_ACTIVE_LEARNING_CYCLE_V1",
        "manifest_sha256": sha256_file(manifest_path),
        "outputs_replayed": True,
        "release_status": "blocked",
        "selected_review_count": counts.get("selected_review_count"),
        "population_audit_review_count": counts.get("population_audit_review_count"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.manifest.resolve()), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
