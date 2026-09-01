#!/usr/bin/env python3
"""Validate an Agent 1 source-period recheck artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.research.source_period_recheck_agent1 import validate_source_period_recheck_agent1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_source_period_recheck_agent1(args.artifact_dir), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

