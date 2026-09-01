#!/usr/bin/env python3
"""Build the Agent 1 candidate-only source-period recheck artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.research.source_period_recheck_agent1 import build_source_period_recheck_agent1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--period-packets", type=Path, required=True)
    parser.add_argument("--period-manifest", type=Path, required=True)
    parser.add_argument("--e2e-candidates", type=Path, required=True)
    parser.add_argument("--e2e-receipt", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--evidence-context-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = build_source_period_recheck_agent1(
        config=args.config,
        period_packets=args.period_packets,
        period_manifest=args.period_manifest,
        e2e_candidates=args.e2e_candidates,
        e2e_receipt=args.e2e_receipt,
        structured_tables_v2=args.structured_tables,
        evidence_context_v3=args.evidence_context,
        evidence_context_manifest_v3=args.evidence_context_manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

