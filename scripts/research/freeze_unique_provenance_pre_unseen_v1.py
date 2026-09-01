#!/usr/bin/env python3
"""Freeze the unique-provenance decision gate before unseen evaluation."""

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

    hypothesis_path = args.split_artifact / "hypothesis_freeze_v1.json"
    split_manifest_path = args.split_artifact / "manifest.json"
    split_summary_path = args.split_artifact / "split_summary_v1.json"
    discovery_manifest_path = args.discovery_artifact / "manifest.json"
    discovery_report_path = args.discovery_artifact / "evaluation_report_v1.json"
    development_manifest_path = args.development_artifact / "manifest.json"
    development_report_path = args.development_artifact / "evaluation_report_v1.json"
    required = (
        args.source_hypothesis,
        hypothesis_path,
        split_manifest_path,
        split_summary_path,
        discovery_manifest_path,
        discovery_report_path,
        development_manifest_path,
        development_report_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"missing inputs: {missing}")

    hypothesis = read_json(hypothesis_path)
    split_summary = read_json(split_summary_path)
    development = read_json(development_report_path)
    thresholds = hypothesis["decision_thresholds"]
    candidate = development["arms"]["B_A_plus_ticker_year_form_unique_gate"]
    outcomes = development["candidate_outcome_counts"]
    observed = {
        "newly_correct": int(outcomes.get("IMPROVED", 0)),
        "precision_on_predictions": candidate["precision_on_predictions"],
        "false_confident_candidate": int(candidate["false_confident_count"]),
        "regressed_baseline_predictions": int(outcomes.get("REGRESSED", 0)),
    }
    gate_passed = (
        observed["newly_correct"] >= thresholds["minimum_newly_correct_development"]
        and observed["precision_on_predictions"] == thresholds["minimum_precision_on_predictions"]
        and observed["false_confident_candidate"] <= thresholds["maximum_false_confident_candidate"]
        and observed["regressed_baseline_predictions"] <= thresholds["maximum_regressed_baseline_predictions"]
    )
    if not gate_passed:
        raise SystemExit("development gate failed; unseen evaluation remains locked")

    metric_holdout = split_summary["metric_holdout"]
    composition_holdout = split_summary["composition_holdout"]
    receipt = {
        "protocol": "unique_provenance_evidence_pre_unseen_freeze_v1",
        "schema_version": 1,
        "experiment_id": hypothesis["experiment_id"],
        "freeze_stage": "AFTER_DEVELOPMENT_BEFORE_UNTOUCHED",
        "inputs": {
            "source_hypothesis_config": descriptor(args.source_hypothesis),
            "hypothesis_freeze": descriptor(hypothesis_path),
            "split_manifest": descriptor(split_manifest_path),
            "split_summary": descriptor(split_summary_path),
            "discovery_manifest": descriptor(discovery_manifest_path),
            "discovery_report": descriptor(discovery_report_path),
            "development_manifest": descriptor(development_manifest_path),
            "development_report": descriptor(development_report_path),
        },
        "development_gate": {"status": "PASS", "observed": observed},
        "frozen_untouched_predictions": {
            "minimum_newly_correct_untouched": thresholds["minimum_newly_correct_untouched"],
            "minimum_precision_on_predictions": thresholds["minimum_precision_on_predictions"],
            "maximum_false_confident_candidate": thresholds["maximum_false_confident_candidate"],
            "maximum_regressed_baseline_predictions": thresholds["maximum_regressed_baseline_predictions"],
            "metric_holdout_prediction": {
                "family": metric_holdout["family"],
                "available_untouched_records": metric_holdout["untouched_count"],
                "development_records": metric_holdout["development_count"],
                "minimum_improved": thresholds["minimum_metric_holdout_improved"],
            },
            "composition_holdout_prediction": {
                "signature": composition_holdout["signature"],
                "available_untouched_records": composition_holdout["untouched_count"],
                "development_records": composition_holdout["development_count"],
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
