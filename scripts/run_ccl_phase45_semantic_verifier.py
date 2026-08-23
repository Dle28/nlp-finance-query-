#!/usr/bin/env python3
"""Run the fail-closed, deterministic CCL Phase 4-5 verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase45 import verify_phase45_proposals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--validated-proposals", type=Path, required=True)
    parser.add_argument("--proposal-validation-manifest", type=Path, required=True)
    parser.add_argument("--phase3-receipt", type=Path, required=True)
    parser.add_argument("--phase3-audit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = verify_phase45_proposals(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "assertion_count": result.assertion_count,
                "source_bound_count": result.source_bound_count,
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
