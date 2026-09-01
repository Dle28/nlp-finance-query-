#!/usr/bin/env python3
"""Materialize value-blind coordinate hints from retrieval ranks 11--20."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.retrieval_tail_coordinate_materializer import (  # noqa: E402
    build_tail_coordinate_hints,
    validate_tail_coordinate_hints,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--builder", type=Path, default=REPO_ROOT / "scripts/e2e/build_competition_submission_v1.py")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--candidate-artifact-dir", type=Path, required=True)
    parser.add_argument("--structured-assets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-rank", type=int, default=11)
    parser.add_argument("--max-rank", type=int, default=20)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    summary = build_tail_coordinate_hints(
        builder_path=args.builder,
        questions_path=args.questions,
        candidate_artifact_dir=args.candidate_artifact_dir,
        structured_assets_path=args.structured_assets,
        output_dir=args.output_dir,
        min_rank=args.min_rank,
        max_rank=args.max_rank,
        expected_question_count=args.expected_question_count,
    )
    if args.validate:
        summary = {"build": summary, "validation": validate_tail_coordinate_hints(args.output_dir)}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
