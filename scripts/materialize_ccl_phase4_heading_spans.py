#!/usr/bin/env python3
"""Materialize exact, literal source-heading spans for CCL Phase 4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase4_layout import materialize_phase4_heading_spans


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--phase45-assertions", type=Path, required=True)
    parser.add_argument("--phase45-manifest", type=Path, required=True)
    parser.add_argument("--ccl-input-inventory", type=Path, required=True)
    parser.add_argument("--raw-tables", type=Path, required=True)
    parser.add_argument("--normalized-tables", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = materialize_phase4_heading_spans(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "span_count": result.span_count,
                "materialized_count": result.materialized_count,
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
