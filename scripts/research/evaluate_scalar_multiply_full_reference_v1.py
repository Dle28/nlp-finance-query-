#!/usr/bin/env python3
"""Run the full external reference only after the untouched gate has passed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.full_dataset_compare import sha256_file
from finance_query.research.scalar_multiply_eval import evaluate_scalar_multiply_full_reference
from finance_query.research.scalar_multiply_pre_unseen import validate_pre_unseen_freeze


def _untouched_gate(receipt: dict, report: dict) -> dict:
    predictions = receipt["frozen_untouched_predictions"]
    candidate = report["arms"]["B_A_plus_named_constant_or_percent_scalar"]
    outcomes = report["candidate_outcome_counts"]
    metric_prediction = predictions["metric_holdout_prediction"]
    composition_prediction = predictions["composition_holdout_prediction"]
    observed = {
        "newly_eligible": int(outcomes.get("IMPROVED", 0)),
        "execution_accuracy_on_newly_eligible": candidate["accuracy_on_eligible"],
        "false_confident_newly_eligible": int(candidate["false_confident_count"]),
        "regressed_previously_eligible": int(outcomes.get("REGRESSED", 0)),
        "metric_holdout_improved": int(
            (report["metric_outcome_counts"].get(metric_prediction["family"]) or {}).get("IMPROVED", 0)
        ),
        "composition_holdout_improved": int(
            (report["composition_outcome_counts"].get(composition_prediction["signature"]) or {}).get("IMPROVED", 0)
        ),
    }
    passed = (
        observed["newly_eligible"] >= predictions["minimum_newly_eligible_untouched"]
        and observed["execution_accuracy_on_newly_eligible"]
        == predictions["minimum_execution_accuracy_on_newly_eligible"]
        and observed["false_confident_newly_eligible"]
        <= predictions["maximum_false_confident_newly_eligible"]
        and observed["regressed_previously_eligible"]
        <= predictions["maximum_regressed_previously_eligible"]
        and observed["metric_holdout_improved"] >= metric_prediction["minimum_improved"]
        and observed["composition_holdout_improved"] >= composition_prediction["minimum_improved"]
    )
    return {"status": "PASS" if passed else "FAIL", "observed": observed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finqa-root", type=Path, required=True)
    parser.add_argument("--split-artifact", type=Path, required=True)
    parser.add_argument("--pre-unseen-freeze", type=Path, required=True)
    parser.add_argument("--untouched-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {args.output_dir}")
    receipt_validation = validate_pre_unseen_freeze(args.pre_unseen_freeze, REPO_ROOT)
    if receipt_validation["status"] != "PASS":
        raise SystemExit("pre-unseen receipt validation failed")
    receipt = json.loads(args.pre_unseen_freeze.read_text(encoding="utf-8"))
    untouched = json.loads(args.untouched_report.read_text(encoding="utf-8"))
    gate = _untouched_gate(receipt, untouched)
    if gate["status"] != "PASS":
        raise SystemExit("untouched gate failed; full external evaluation is forbidden")
    hypothesis = json.loads(
        (args.split_artifact / "hypothesis_freeze_v1.json").read_text(encoding="utf-8")
    )
    source_paths = {split: args.finqa_root / f"{split}.json" for split in ("train", "dev", "test")}
    rows, report = evaluate_scalar_multiply_full_reference(
        finqa_items_by_split={
            split: json.loads(path.read_text(encoding="utf-8"))
            for split, path in source_paths.items()
        },
        seed=hypothesis["split_design"]["seed"],
    )
    report["untouched_gate"] = gate
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
        "protocol": "dimensionless_scalar_multiply_full_external_reference_manifest_v1",
        "schema_version": 1,
        "inputs": {
            "pre_unseen_freeze": {"path": str(args.pre_unseen_freeze), "sha256": sha256_file(args.pre_unseen_freeze)},
            "untouched_report": {"path": str(args.untouched_report), "sha256": sha256_file(args.untouched_report)},
            "hypothesis_freeze": {"path": str(args.split_artifact / "hypothesis_freeze_v1.json"), "sha256": sha256_file(args.split_artifact / "hypothesis_freeze_v1.json")},
            **{split: {"path": str(path), "sha256": sha256_file(path)} for split, path in source_paths.items()},
        },
        "outputs": {
            rows_path.name: {"sha256": sha256_file(rows_path)},
            report_path.name: {"sha256": sha256_file(report_path)},
        },
        "untouched_gate": gate,
        "source_contract": report["source_contract"],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
