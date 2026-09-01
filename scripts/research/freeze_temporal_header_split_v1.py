#!/usr/bin/env python3
"""Freeze the temporal-header experiment split."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.temporal_header_experiment import freeze_temporal_header_split


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", type=Path, required=True)
    parser.add_argument("--finqa-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = freeze_temporal_header_split(
        hypothesis_path=args.hypothesis,
        finqa_root=args.finqa_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
