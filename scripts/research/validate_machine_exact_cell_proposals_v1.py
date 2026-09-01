#!/usr/bin/env python3
"""Validate non-authorizing machine exact-cell proposal artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.machine_exact_cell_proposals import validate_machine_exact_cell_proposals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-target-question-count", type=int)
    args = parser.parse_args()
    result = validate_machine_exact_cell_proposals(
        args.artifact_dir,
        expected_target_question_count=args.expected_target_question_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
