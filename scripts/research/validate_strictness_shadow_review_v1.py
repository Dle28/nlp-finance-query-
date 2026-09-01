#!/usr/bin/env python3
"""Validate a non-submittable value-blind strictness review ZIP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.strictness_shadow_review import validate_strictness_shadow_review


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--expected-question-count", default=1012, type=int)
    args = parser.parse_args()
    result = validate_strictness_shadow_review(args.artifact_dir, expected_question_count=args.expected_question_count)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
