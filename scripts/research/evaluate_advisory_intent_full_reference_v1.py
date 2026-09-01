#!/usr/bin/env python3
"""Run the full VNFinsQA reference evaluation only after the untouched gate passes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.advisory_intent_abstention import (  # noqa: E402
    evaluate_full_reference_population,
    load_jsonl,
)
from finance_query.research.advisory_intent_pre_unseen import validate_pre_unseen  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_untouched_gate(report: dict[str, object], hypothesis: dict[str, object]) -> None:
    thresholds = hypothesis["decision_thresholds"]
    candidate = report["arms"]["B_A_plus_explicit_advisory_abstention"]
    outcomes = report["candidate_outcome_counts"]
    metric_holdout = hypothesis["split_design"]["metric_holdout"]
    del metric_holdout  # The concrete frozen family is read from the split summary below.
    if outcomes.get("IMPROVED", 0) < thresholds["minimum_newly_correct_untouched"]:
        raise ValueError("untouched improvement threshold failed")
    if candidate["precision_on_predictions"] < thresholds["minimum_precision_on_predictions"]:
        raise ValueError("untouched precision threshold failed")
    if candidate["false_confident_count"] > thresholds["maximum_false_confident_candidate"]:
        raise ValueError("untouched false-confidence threshold failed")
    if outcomes.get("REGRESSED", 0) > thresholds["maximum_regressed_baseline_predictions"]:
        raise ValueError("untouched regression threshold failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", type=Path, required=True)
    parser.add_argument("--split-artifact", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--pre-unseen-freeze", type=Path, required=True)
    parser.add_argument("--untouched-artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    if validate_pre_unseen(args.pre_unseen_freeze, ROOT)["status"] != "PASS":
        raise SystemExit("pre-unseen receipt validation failed")

    hypothesis = json.loads(args.hypothesis.read_text(encoding="utf-8"))
    untouched_path = args.untouched_artifact / "evaluation_report_v1.json"
    untouched_report = json.loads(untouched_path.read_text(encoding="utf-8"))
    verify_untouched_gate(untouched_report, hypothesis)

    split_summary = json.loads(
        (args.split_artifact / "split_summary_v1.json").read_text(encoding="utf-8")
    )
    metric_family = split_summary["metric_holdout"]["family"]
    composition = split_summary["composition_holdout"]["signature"]
    metric_improved = untouched_report["metric_outcome_counts"].get(metric_family, {}).get("IMPROVED", 0)
    composition_improved = untouched_report["composition_outcome_counts"].get(composition, {}).get("IMPROVED", 0)
    thresholds = hypothesis["decision_thresholds"]
    if metric_improved < thresholds["minimum_metric_holdout_improved"]:
        raise SystemExit("metric holdout gate failed")
    if composition_improved < thresholds["minimum_composition_holdout_improved"]:
        raise SystemExit("composition holdout gate failed")

    rows, report = evaluate_full_reference_population(
        records=load_jsonl(args.dataset),
        seed=hypothesis["split_design"]["seed"],
    )
    report["untouched_gate"] = {
        "status": "PASS",
        "report_sha256": sha256(untouched_path),
        "metric_holdout_improved": metric_improved,
        "composition_holdout_improved": composition_improved,
    }
    args.output_dir.mkdir(parents=True)
    rows_path = args.output_dir / "evaluation_rows_v1.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = args.output_dir / "evaluation_report_v1.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "protocol": "explicit_advisory_intent_full_reference_manifest_v1",
        "schema_version": 1,
        "inputs": {
            "hypothesis": {"path": str(args.hypothesis), "sha256": sha256(args.hypothesis)},
            "dataset": {"path": str(args.dataset), "sha256": sha256(args.dataset)},
            "pre_unseen_freeze": {"path": str(args.pre_unseen_freeze), "sha256": sha256(args.pre_unseen_freeze)},
            "untouched_report": {"path": str(untouched_path), "sha256": sha256(untouched_path)},
        },
        "outputs": {
            rows_path.name: {"sha256": sha256(rows_path)},
            report_path.name: {"sha256": sha256(report_path)},
        },
        "source_contract": report["source_contract"],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
