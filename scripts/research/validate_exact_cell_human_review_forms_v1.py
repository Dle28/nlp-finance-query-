#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.exact_cell_human_review import (  # noqa: E402
    validate_exact_cell_human_review_forms,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-review-sample-count", type=int, default=150)
    args = parser.parse_args()
    result = validate_exact_cell_human_review_forms(
        args.artifact_dir,
        expected_review_sample_count=args.expected_review_sample_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
