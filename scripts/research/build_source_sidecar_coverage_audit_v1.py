#!/usr/bin/env python3
"""Audit source reconstruction coverage for review-route tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.source_sidecar_coverage_audit import build_source_sidecar_coverage_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("row-review-queue", "full-assets", "full-assets-manifest", "structured-tables", "evidence-context", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_source_sidecar_coverage_audit(
        row_review_queue_path=args.row_review_queue,
        full_assets_path=args.full_assets,
        full_assets_manifest_path=args.full_assets_manifest,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        output_dir=args.output_dir,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
