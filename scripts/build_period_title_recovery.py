#!/usr/bin/env python3
"""Build exact-source-title recovery packets without changing V1/V2 inputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.period_title_recovery import materialize_period_title_recovery


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-packets", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--route-packets", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()
    result = materialize_period_title_recovery(
        base_packets=args.base_packets.resolve(),
        base_manifest=args.base_manifest.resolve(),
        route_packets=args.route_packets.resolve(),
        structured_tables=args.structured_tables.resolve(),
        evidence_context=args.evidence_context.resolve(),
        output=args.output.resolve(),
        audit_output=args.audit_output.resolve(),
    )
    print(json.dumps({
        "recovered_question_count": result["recovered_question_count"],
        "recovered_question_ids": result["recovered_question_ids"],
        "recovery_reason_counts": result["recovery_reason_counts"],
        "packet_status_counts": result["packet_status_counts"],
        "output_sha256": result["outputs"]["period_packets"]["sha256"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

