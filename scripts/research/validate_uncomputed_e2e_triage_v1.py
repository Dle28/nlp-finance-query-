#!/usr/bin/env python3
"""Validate a research-only E2E root-cause triage artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.uncomputed_e2e_triage import validate_uncomputed_e2e_triage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    result = validate_uncomputed_e2e_triage(
        args.artifact_dir,
        expected_question_count=args.expected_question_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
