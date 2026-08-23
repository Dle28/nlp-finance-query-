#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.navigation_remediation import build_navigation_remediation_queue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conflict-workbench", type=Path, required=True)
    parser.add_argument("--conflict-manifest", type=Path, required=True)
    parser.add_argument("--no-candidate-audit", type=Path, required=True)
    parser.add_argument("--period-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_navigation_remediation_queue(**vars(args))
    print(json.dumps({"counts": result["counts"], "manifest_path": result["manifest_path"]}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

