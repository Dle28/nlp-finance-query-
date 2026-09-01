#!/usr/bin/env python3
"""Freeze one hypothesis-specific discovery/development/unseen split."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.experiment_split import freeze_experiment_split


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothesis", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze_experiment_split(hypothesis=args.hypothesis, taxonomy=args.taxonomy, output_dir=args.output_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
