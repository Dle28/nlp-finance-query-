#!/usr/bin/env python3
"""Join source-first CCL routes to immutable source-grid structure; never run an LLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_table_structure_context import (
    materialize_phase5_table_structure_context,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-first-routes", type=Path, required=True)
    parser.add_argument("--source-first-manifest", type=Path, required=True)
    parser.add_argument("--phase5-note-contexts", type=Path, required=True)
    parser.add_argument("--phase5-note-context-manifest", type=Path, required=True)
    parser.add_argument("--phase4-row-label-contexts", type=Path, required=True)
    parser.add_argument("--phase4-row-label-context-manifest", type=Path, required=True)
    parser.add_argument("--ccl-input-inventory", type=Path, required=True)
    parser.add_argument("--raw-tables", type=Path, required=True)
    parser.add_argument("--normalized-tables", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = materialize_phase5_table_structure_context(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "context_count": result.context_count,
                "materialized_count": result.materialized_count,
                "llm_dispatch_allowed": False,
                "training_eligible": False,
                "certification_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
