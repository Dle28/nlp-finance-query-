#!/usr/bin/env python3
"""Build non-authorizing numeric candidates from machine-reviewed sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.multi_operand_machine_diagnostic import (  # noqa: E402
    build_multi_operand_machine_diagnostic,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine-review-dir", type=Path, required=True)
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = build_multi_operand_machine_diagnostic(
        machine_review_dir=args.machine_review_dir,
        plans_path=args.plans,
        assets_path=args.assets,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
