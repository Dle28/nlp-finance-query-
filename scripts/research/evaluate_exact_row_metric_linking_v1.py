#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.exact_row_metric_linking import evaluate_exact_row_stage, load_jsonl
from finance_query.research.exact_row_pre_unseen import validate_exact_row_pre_unseen


STAGE_SPLIT = {"discovery": "train", "development": "dev", "untouched_evaluation": "test"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-artifact", type=Path, required=True)
    parser.add_argument("--finqa-root", type=Path, required=True)
    parser.add_argument("--stage", choices=sorted(STAGE_SPLIT), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pre-unseen-freeze", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite output directory: {args.output_dir}")
    if args.stage == "untouched_evaluation":
        if args.pre_unseen_freeze is None or not args.pre_unseen_freeze.is_file():
            raise SystemExit("untouched exact-row evaluation requires a pre-unseen receipt")
        receipt = json.loads(args.pre_unseen_freeze.read_text(encoding="utf-8"))
        if (
            receipt.get("protocol") != "exact_row_metric_linking_pre_unseen_freeze_v1"
            or receipt.get("untouched_evaluation_allowed") is not True
        ):
            raise SystemExit("invalid exact-row pre-unseen receipt")
        if validate_exact_row_pre_unseen(args.pre_unseen_freeze, REPO_ROOT)["status"] != "PASS":
            raise SystemExit("exact-row pre-unseen receipt validation failed")
    hypothesis_path = args.split_artifact / "hypothesis_freeze_v1.json"
    hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
    assignments_path = args.split_artifact / "split_assignments_v1.jsonl"
    split = STAGE_SPLIT[args.stage]
    source_path = args.finqa_root / f"{split}.json"
    rows, report = evaluate_exact_row_stage(
        assignments=load_jsonl(assignments_path),
        finqa_items=json.loads(source_path.read_text(encoding="utf-8")),
        stage=args.stage,
        split=split,
        seed=hypothesis["split_design"]["seed"],
    )
    args.output_dir.mkdir(parents=True)
    rows_path = args.output_dir / "evaluation_rows_v1.jsonl"
    rows_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    report_path = args.output_dir / "evaluation_report_v1.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "protocol": "exact_row_metric_linking_evaluation_manifest_v1",
        "schema_version": 1,
        "inputs": {
            "assignments": {"path": str(assignments_path), "sha256": _sha(assignments_path)},
            "hypothesis_freeze": {"path": str(hypothesis_path), "sha256": _sha(hypothesis_path)},
            "source_split": {"path": str(source_path), "sha256": _sha(source_path)},
        },
        "outputs": {rows_path.name: {"sha256": _sha(rows_path)}, report_path.name: {"sha256": _sha(report_path)}},
        "benchmark_stage": args.stage,
        "question_or_row_materialized": False,
        "full_vifinqa_dataset_allowed": False,
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
