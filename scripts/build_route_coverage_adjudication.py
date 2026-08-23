#!/usr/bin/env python3
"""Build a non-materializable review queue for abstained question routes."""
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.route_coverage_adjudication import build_route_coverage_adjudication


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--routes-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_route_coverage_adjudication(
        routes=args.routes,
        routes_manifest=args.routes_manifest,
        output_dir=args.output_dir,
    )
    print(result["manifest_path"])


if __name__ == "__main__":
    main()
