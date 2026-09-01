#!/usr/bin/env python3
"""Validate a source-sidecar coverage audit artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.source_sidecar_coverage_audit import validate_source_sidecar_coverage_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_source_sidecar_coverage_audit(args.artifact_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
