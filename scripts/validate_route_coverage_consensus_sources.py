#!/usr/bin/env python3
"""Reopen source locators for consensus route-review handoffs without route changes."""
from __future__ import annotations

import argparse
from pathlib import Path

from finance_query.route_coverage_source_validation import validate_consensus_handoff_sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--handoff-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(validate_consensus_handoff_sources(**vars(args))["manifest_path"])


if __name__ == "__main__":
    main()
