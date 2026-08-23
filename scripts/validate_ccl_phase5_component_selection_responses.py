#!/usr/bin/env python3
"""Fail-closed validator for CCL Phase 5 closed-world component selections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_component_selection import validate_phase5_component_selection_responses


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packet-manifest", type=Path, required=True)
    parser.add_argument("--raw-responses", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args()
    result = validate_phase5_component_selection_responses(**vars(args))
    print(json.dumps({"output_dir": str(result.output_dir), "valid_selection_count": result.valid_selection_count, "abstention_count": result.abstention_count, "invalid_count": result.invalid_count, "training_eligible": False, "certification_allowed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
