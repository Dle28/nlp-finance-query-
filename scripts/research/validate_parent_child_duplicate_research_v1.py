#!/usr/bin/env python3
"""Validate the Agent 2 candidate-only research artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.parent_child_duplicate_research import (
    validate_parent_child_duplicate_research,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    result = validate_parent_child_duplicate_research(args.artifact_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
