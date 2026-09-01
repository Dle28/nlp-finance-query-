#!/usr/bin/env python3
"""Merge independently rechecked candidate period packets into a base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.period_packet_union import build_period_packet_union


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in (
        "base-period-packets",
        "base-period-manifest",
        "repair-period-packets",
        "repair-period-manifest",
        "repair-audit",
        "route-overlay",
        "route-overlay-manifest",
        "output-dir",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    result = build_period_packet_union(
        base_period_packets=args.base_period_packets,
        base_period_manifest=args.base_period_manifest,
        repair_period_packets=args.repair_period_packets,
        repair_period_manifest=args.repair_period_manifest,
        repair_audit=args.repair_audit,
        route_overlay=args.route_overlay,
        route_overlay_manifest=args.route_overlay_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
