#!/usr/bin/env python3
"""Compare current V2/V3 coverage against in-memory reconstructed sidecars."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.hydrated_source_gate_probe import build_hydrated_source_gate_probe


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("triage", "plans", "route-overlay", "row-review-queue", "full-assets", "full-assets-manifest", "structured-tables", "evidence-context", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--minimum-row-jaccard", type=float, default=0.9)
    args = parser.parse_args()
    print(json.dumps(build_hydrated_source_gate_probe(
        triage_path=args.triage,
        plans_path=args.plans,
        route_overlay_path=args.route_overlay,
        row_review_queue_path=args.row_review_queue,
        full_assets_path=args.full_assets,
        full_assets_manifest_path=args.full_assets_manifest,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
        minimum_row_jaccard=args.minimum_row_jaccard,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
