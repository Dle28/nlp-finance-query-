#!/usr/bin/env python3
"""Run exact-source comparison for lexical versus lexical+dense navigation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.staged_formula_hybrid_source_probe import build_staged_formula_hybrid_source_probe


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "plans", "targets", "hybrid-artifact-dir", "assets", "structured-tables",
        "evidence-context", "evidence-context-manifest", "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--minimum-row-jaccard", type=float, default=0.9)
    parser.add_argument("--minimum-row-margin", type=float, default=0.2)
    parser.add_argument("--maximum-row-candidates", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(build_staged_formula_hybrid_source_probe(
        plans_path=args.plans,
        targets_path=args.targets,
        hybrid_artifact_dir=args.hybrid_artifact_dir,
        assets_path=args.assets,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        evidence_context_manifest_path=args.evidence_context_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
        minimum_row_jaccard=args.minimum_row_jaccard,
        minimum_row_margin=args.minimum_row_margin,
        maximum_row_candidates=args.maximum_row_candidates,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
