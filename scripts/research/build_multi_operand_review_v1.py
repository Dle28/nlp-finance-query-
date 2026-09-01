#!/usr/bin/env python3
"""Build the limited multi-operand review batch following a human review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.multi_operand_review import build_multi_operand_review  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--hybrid-review-queue", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--prior-review-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_multi_operand_review(
        config_path=args.config,
        plans_path=args.plans,
        hybrid_review_queue_path=args.hybrid_review_queue,
        assets_path=args.assets,
        prior_review_receipt_path=args.prior_review_receipt,
        output_dir=args.output_dir,
        repo_root=ROOT,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
