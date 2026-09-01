#!/usr/bin/env python3
"""Measure the current Decimal kernel on FinQA discovery/development gold programs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.finqa_dsl_eval import evaluate_finqa_items, load_json


STAGE_BY_SPLIT = {"train": "discovery", "dev": "development", "test": "untouched_evaluation"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finqa-root", type=Path, required=True)
    parser.add_argument("--split", choices=sorted(STAGE_BY_SPLIT), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-untouched-evaluation", action="store_true")
    args = parser.parse_args()
    stage = STAGE_BY_SPLIT[args.split]
    if stage == "untouched_evaluation" and not args.allow_untouched_evaluation:
        raise SystemExit("untouched evaluation remains sealed; freeze a hypothesis before opening it")
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {args.output_dir}")
    rows, report = evaluate_finqa_items(
        load_json(args.finqa_root / f"{args.split}.json"),
        source_split=args.split,
        benchmark_stage=stage,
    )
    args.output_dir.mkdir(parents=True)
    with (args.output_dir / "finqa_dsl_evaluation_v1.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (args.output_dir / "summary_v1.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
