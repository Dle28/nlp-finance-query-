#!/usr/bin/env python3
"""Create the explicit Person-1 review plan from frozen V1 queues."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.independent_adjudication_reviews import sha256_file
from finance_query.person1_review_plan import build_person1_review_plan


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-queue", required=True, type=Path)
    parser.add_argument("--period-queue", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = build_person1_review_plan(metadata_queue=_read_jsonl(args.metadata_queue), period_queue=_read_jsonl(args.period_queue) if args.period_queue else [])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"path": str(args.output), "sha256": sha256_file(args.output), "count": len(rows)}))


if __name__ == "__main__":
    main()
