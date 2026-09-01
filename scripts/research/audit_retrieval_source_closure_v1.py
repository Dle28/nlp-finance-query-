#!/usr/bin/env python3
"""Run the value-blind retrieval/source-UID closure A/B audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.retrieval_source_closure_audit import (  # noqa: E402
    build_retrieval_source_closure_audit,
    validate_retrieval_source_closure_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--control-artifact-dir", type=Path, required=True)
    parser.add_argument("--candidate-artifact-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    summary = build_retrieval_source_closure_audit(
        submission_path=args.submission,
        evidence_dir=args.evidence_dir,
        baseline_artifact_dir=args.control_artifact_dir,
        candidate_artifact_dir=args.candidate_artifact_dir,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    )
    if args.validate:
        validation = validate_retrieval_source_closure_audit(args.output_dir)
        summary = {"build": summary, "validation": validation}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
