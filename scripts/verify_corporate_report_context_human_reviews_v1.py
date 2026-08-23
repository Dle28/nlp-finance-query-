#!/usr/bin/env python3
"""Verify completed human responses for the non-materializable context queue."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.corporate_report_context_review import verify_corporate_report_context_human_reviews  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--completed-responses", type=Path, required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    queue_dir = args.queue_dir.resolve()
    result = verify_corporate_report_context_human_reviews(
        queue=queue_dir / "corporate_report_context_review_queue_v1.jsonl",
        queue_manifest=queue_dir / "corporate_report_context_review_queue_v1.manifest.json",
        completed_responses=args.completed_responses.resolve(),
        reviewer_id=args.reviewer_id,
        output=args.output.resolve(),
    )
    print(result)


if __name__ == "__main__":
    main()
