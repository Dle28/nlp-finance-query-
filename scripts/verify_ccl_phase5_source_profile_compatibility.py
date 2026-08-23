#!/usr/bin/env python3
"""Verify non-promotable CCL model/source-profile compatibility diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_semantic_compatibility import (
    verify_phase5_source_profile_compatibility,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase45-assertions", type=Path, required=True)
    parser.add_argument("--phase45-manifest", type=Path, required=True)
    parser.add_argument("--phase4-heading-spans", type=Path, required=True)
    parser.add_argument("--phase4-manifest", type=Path, required=True)
    parser.add_argument("--phase5-profiles", type=Path, required=True)
    parser.add_argument("--phase5-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = verify_phase5_source_profile_compatibility(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "compatibility_count": result.compatibility_count,
                "compatible_context_count": result.compatible_context_count,
                "unresolved_count": result.unresolved_count,
                "campaign_candidate_allowed": False,
                "training_eligible": False,
                "certification_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
