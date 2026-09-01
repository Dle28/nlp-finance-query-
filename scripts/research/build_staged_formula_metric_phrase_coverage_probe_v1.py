#!/usr/bin/env python3
"""Build a coverage-first, value-blind phrase recheck for staged formulas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.staged_formula_metric_phrase_coverage_probe import (
    build_staged_formula_metric_phrase_coverage_probe,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "config", "plans", "candidate-artifact-dir", "baseline-phrase-artifact-dir", "full-assets",
        "full-assets-manifest", "structured-tables", "evidence-context", "evidence-context-manifest", "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--minimum-row-jaccard", type=float, default=0.8)
    parser.add_argument("--minimum-row-margin", type=float, default=0.1)
    args = parser.parse_args()
    print(json.dumps(build_staged_formula_metric_phrase_coverage_probe(
        config_path=args.config,
        plans_path=args.plans,
        candidate_artifact_dir=args.candidate_artifact_dir,
        baseline_phrase_artifact_dir=args.baseline_phrase_artifact_dir,
        full_assets_path=args.full_assets,
        full_assets_manifest_path=args.full_assets_manifest,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        evidence_context_manifest_path=args.evidence_context_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
        minimum_row_jaccard=args.minimum_row_jaccard,
        minimum_row_margin=args.minimum_row_margin,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
