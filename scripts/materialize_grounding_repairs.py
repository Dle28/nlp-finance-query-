#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.grounding_repairs import materialize_grounding_repairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize reviewed grounding repairs as an overlay.")
    parser.add_argument("--metadata-queue", type=Path, required=True)
    parser.add_argument("--routing-queue", type=Path, required=True)
    parser.add_argument("--period-queue", type=Path, required=True)
    parser.add_argument("--adjudication-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = materialize_grounding_repairs(
        metadata_queue_path=args.metadata_queue, routing_queue_path=args.routing_queue,
        period_queue_path=args.period_queue, adjudication_manifest_path=args.adjudication_manifest,
        output_dir=args.output_dir,
    )
    print(result["manifest_path"])


if __name__ == "__main__":
    main()
