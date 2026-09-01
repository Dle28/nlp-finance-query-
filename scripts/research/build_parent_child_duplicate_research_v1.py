#!/usr/bin/env python3
"""Build the bounded Agent 2 parent-child/duplicate research artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.parent_child_duplicate_research import (
    build_parent_child_duplicate_research,
)


DEFAULT_BUNDLE = REPO_ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts/research/parent_child_duplicate_research_v1_20260827_r1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_parent_child_duplicate_research(
        bundle_dir=args.bundle_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
