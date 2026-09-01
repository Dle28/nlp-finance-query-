#!/usr/bin/env python3
"""Validate split hashes, disjointness and four holdout gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.experiment_split import validate_experiment_split


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_experiment_split(args.artifact_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
