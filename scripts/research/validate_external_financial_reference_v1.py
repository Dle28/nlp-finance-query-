#!/usr/bin/env python3
"""Validate a generated external financial reasoning reference index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.external_financial_reference import validate_reference_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.source_config.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in args.index.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(json.dumps(validate_reference_rows(rows, config), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
