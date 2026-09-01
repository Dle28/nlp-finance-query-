#!/usr/bin/env python3
"""Build a candidate-only E2E period-packet revision from source rechecks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.machine_navigation_materialization import build_machine_navigation_materialization


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-period-packets", type=Path, required=True)
    parser.add_argument("--base-period-manifest", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--proposals-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--evidence-context-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    result = build_machine_navigation_materialization(
        base_period_packets_path=args.base_period_packets,
        base_period_manifest_path=args.base_period_manifest,
        proposals_path=args.proposals,
        proposals_manifest_path=args.proposals_manifest,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        evidence_context_manifest_path=args.evidence_context_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
