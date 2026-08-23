#!/usr/bin/env python3
"""Build immutable source-first CCL Phase 5 semantic routes; never run an LLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_semantic_routing import (
    build_phase5_source_first_semantic_routes,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase4-heading-spans", type=Path, required=True)
    parser.add_argument("--phase4-manifest", type=Path, required=True)
    parser.add_argument("--phase5-profiles", type=Path, required=True)
    parser.add_argument("--phase5-manifest", type=Path, required=True)
    parser.add_argument("--phase4-row-label-contexts", type=Path, required=True)
    parser.add_argument("--phase4-row-label-context-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = build_phase5_source_first_semantic_routes(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "route_count": result.route_count,
                "deterministic_source_profile_count": result.deterministic_source_profile_count,
                "deterministic_note_context_component_count": result.deterministic_note_context_component_count,
                "deterministic_row_label_context_count": result.deterministic_row_label_context_count,
                "unresolved_source_context_count": result.unresolved_source_context_count,
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
