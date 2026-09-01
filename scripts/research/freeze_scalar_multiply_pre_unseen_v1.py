#!/usr/bin/env python3
"""Freeze scalar-multiply thresholds and hashes before unseen evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def descriptor(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256_file(path)}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-hypothesis", type=Path, required=True)
    parser.add_argument("--split-artifact", type=Path, required=True)
    parser.add_argument("--discovery-artifact", type=Path, required=True)
    parser.add_argument("--development-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite: {args.output}")

    paths = {
        "source_hypothesis_config": args.source_hypothesis,
        "hypothesis_freeze": args.split_artifact / "hypothesis_freeze_v1.json",
        "split_manifest": args.split_artifact / "manifest.json",
        "split_summary": args.split_artifact / "split_summary_v1.json",
        "discovery_manifest": args.discovery_artifact / "manifest.json",
        "discovery_report": args.discovery_artifact / "evaluation_report_v1.json",
        "development_manifest": args.development_artifact / "manifest.json",
        "development_report": args.development_artifact / "evaluation_report_v1.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"missing inputs: {missing}")

    hypothesis = read_json(paths["hypothesis_freeze"])
    split_summary = read_json(paths["split_summary"])
    development = read_json(paths["development_report"])
    thresholds = hypothesis["decision_thresholds"]
    candidate = development["arms"]["B_A_plus_named_constant_or_percent_scalar"]
    outcomes = development["candidate_outcome_counts"]
    observed = {
        "newly_eligible": int(outcomes.get("IMPROVED", 0)),
        "execution_accuracy_on_newly_eligible": candidate["accuracy_on_eligible"],
        "false_confident_newly_eligible": int(candidate["false_confident_count"]),
        "regressed_previously_eligible": int(outcomes.get("REGRESSED", 0)),
    }
    passed = (
        observed["newly_eligible"] >= thresholds["minimum_newly_eligible_development"]
        and observed["execution_accuracy_on_newly_eligible"]
        == thresholds["minimum_execution_accuracy_on_newly_eligible"]
        and observed["false_confident_newly_eligible"]
        <= thresholds["maximum_false_confident_newly_eligible"]
        and observed["regressed_previously_eligible"]
        <= thresholds["maximum_regressed_previously_eligible"]
    )
    if not passed:
        raise SystemExit("development gate failed; unseen evaluation remains locked")

    metric = split_summary["metric_holdout"]
    composition = split_summary["composition_holdout"]
    receipt = {
        "protocol": "dimensionless_scalar_multiply_pre_unseen_freeze_v1",
        "schema_version": 1,
        "experiment_id": hypothesis["experiment_id"],
        "freeze_stage": "AFTER_DEVELOPMENT_BEFORE_UNTOUCHED",
        "inputs": {name: descriptor(path) for name, path in paths.items()},
        "development_gate": {"status": "PASS", "observed": observed},
        "frozen_untouched_predictions": {
            "minimum_newly_eligible_untouched": thresholds["minimum_newly_eligible_untouched"],
            "minimum_execution_accuracy_on_newly_eligible": thresholds["minimum_execution_accuracy_on_newly_eligible"],
            "maximum_false_confident_newly_eligible": thresholds["maximum_false_confident_newly_eligible"],
            "maximum_regressed_previously_eligible": thresholds["maximum_regressed_previously_eligible"],
            "metric_holdout_prediction": {
                "family": metric["family"],
                "available_untouched_records": metric["untouched_count"],
                "development_records": metric["development_count"],
                "minimum_improved": thresholds["minimum_metric_holdout_improved"],
            },
            "composition_holdout_prediction": {
                "signature": composition["signature"],
                "available_untouched_records": composition["untouched_count"],
                "development_records": composition["development_count"],
                "minimum_improved": thresholds["minimum_composition_holdout_improved"],
            },
        },
        "untouched_evaluation_allowed": True,
        "full_vifinqa_dataset_allowed": False,
        "source_contract": {
            "research_only": True,
            "evidence_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
            "vifinqa_answer_authority": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
