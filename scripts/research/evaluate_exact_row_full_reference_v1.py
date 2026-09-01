#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.exact_row_metric_linking import evaluate_exact_row_full_reference
from finance_query.research.exact_row_pre_unseen import validate_exact_row_pre_unseen
from finance_query.research.full_dataset_compare import sha256_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finqa-root", type=Path, required=True)
    parser.add_argument("--split-artifact", type=Path, required=True)
    parser.add_argument("--pre-unseen-freeze", type=Path, required=True)
    parser.add_argument("--untouched-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite output directory: {args.output_dir}")
    if validate_exact_row_pre_unseen(args.pre_unseen_freeze, REPO_ROOT)["status"] != "PASS":
        raise SystemExit("pre-unseen receipt failed validation")
    receipt = json.loads(args.pre_unseen_freeze.read_text(encoding="utf-8"))
    untouched = json.loads(args.untouched_report.read_text(encoding="utf-8"))
    predictions = receipt["frozen_untouched_predictions"]
    candidate = untouched["arms"]["B_A_plus_normalized_unique_margin_linker"]
    outcomes = untouched["candidate_outcome_counts"]
    metric = predictions["metric_holdout_prediction"]
    composition = predictions["composition_holdout_prediction"]
    observed = {
        "newly_correct": int(outcomes.get("IMPROVED", 0)),
        "precision_on_predictions": candidate["precision_on_predictions"],
        "false_confident_candidate": int(candidate["false_confident_count"]),
        "regressed_baseline_predictions": int(outcomes.get("REGRESSED", 0)),
        "metric_holdout_improved": int((untouched["metric_outcome_counts"].get(metric["family"]) or {}).get("IMPROVED", 0)),
        "composition_holdout_improved": int((untouched["composition_outcome_counts"].get(composition["signature"]) or {}).get("IMPROVED", 0)),
    }
    passed = (
        observed["newly_correct"] >= predictions["minimum_newly_correct_untouched"]
        and observed["precision_on_predictions"] == predictions["minimum_precision_on_predictions"]
        and observed["false_confident_candidate"] <= predictions["maximum_false_confident_candidate"]
        and observed["regressed_baseline_predictions"] <= predictions["maximum_regressed_baseline_predictions"]
        and observed["metric_holdout_improved"] >= metric["minimum_improved"]
        and observed["composition_holdout_improved"] >= composition["minimum_improved"]
    )
    if not passed:
        raise SystemExit("untouched exact-row gate failed")
    hypothesis_path = args.split_artifact / "hypothesis_freeze_v1.json"
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    source_paths = {split: args.finqa_root / f"{split}.json" for split in ("train", "dev", "test")}
    rows, report = evaluate_exact_row_full_reference(
        finqa_items_by_split={split: json.loads(path.read_text(encoding="utf-8")) for split, path in source_paths.items()},
        seed=hypothesis["split_design"]["seed"],
    )
    report["untouched_gate"] = {"status": "PASS", "observed": observed}
    args.output_dir.mkdir(parents=True)
    rows_path = args.output_dir / "evaluation_rows_v1.jsonl"
    rows_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    report_path = args.output_dir / "evaluation_report_v1.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "protocol": "exact_row_metric_linking_full_external_reference_manifest_v1",
        "schema_version": 1,
        "inputs": {
            "pre_unseen_freeze": {"path": str(args.pre_unseen_freeze), "sha256": sha256_file(args.pre_unseen_freeze)},
            "untouched_report": {"path": str(args.untouched_report), "sha256": sha256_file(args.untouched_report)},
            "hypothesis_freeze": {"path": str(hypothesis_path), "sha256": sha256_file(hypothesis_path)},
            **{split: {"path": str(path), "sha256": sha256_file(path)} for split, path in source_paths.items()},
        },
        "outputs": {rows_path.name: {"sha256": sha256_file(rows_path)}, report_path.name: {"sha256": sha256_file(report_path)}},
        "untouched_gate": report["untouched_gate"],
        "source_contract": report["source_contract"],
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
