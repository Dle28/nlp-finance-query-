#!/usr/bin/env python3
"""Verify and split source-anchored merged period/unit headers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_header_components import verify_phase5_header_components


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--phase45-assertions", type=Path, required=True)
    parser.add_argument("--phase45-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = verify_phase5_header_components(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "component_count": result.component_count,
                "period_component_count": result.period_component_count,
                "unit_component_count": result.unit_component_count,
                "unresolved_count": result.unresolved_count,
                "training_eligible": False,
                "certification_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
