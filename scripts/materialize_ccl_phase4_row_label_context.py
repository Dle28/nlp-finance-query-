#!/usr/bin/env python3
"""Materialize exact row-label fallback context for heading-less CCL Phase 4 claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase4_row_label_context import materialize_phase4_row_label_context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--phase45-assertions", type=Path, required=True)
    parser.add_argument("--phase45-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = materialize_phase4_row_label_context(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "context_count": result.context_count,
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
