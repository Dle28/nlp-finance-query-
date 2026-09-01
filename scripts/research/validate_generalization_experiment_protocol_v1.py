#!/usr/bin/env python3
"""Validate the frozen anti-overfit experiment governance contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.generalization_protocol import validate_generalization_protocol_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_generalization_protocol_file(args.config), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
