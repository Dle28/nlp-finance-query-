#!/usr/bin/env python3
"""Build non-promotable CCL Phase 5 table-topic profiles from source headings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_profiles import build_phase5_table_topic_profiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase4-heading-spans", type=Path, required=True)
    parser.add_argument("--phase4-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = build_phase5_table_topic_profiles(**vars(args))
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "profile_count": result.profile_count,
                "source_profiled_count": result.source_profiled_count,
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
