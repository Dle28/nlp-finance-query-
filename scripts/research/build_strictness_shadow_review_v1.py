#!/usr/bin/env python3
"""Create a non-submittable value-blind strictness review ZIP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.strictness_shadow_review import build_strictness_shadow_review


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e2e-run-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-question-count", default=1012, type=int)
    args = parser.parse_args()
    result = build_strictness_shadow_review(
        e2e_run_manifest_path=args.e2e_run_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
