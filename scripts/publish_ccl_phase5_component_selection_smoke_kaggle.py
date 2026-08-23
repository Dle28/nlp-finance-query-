#!/usr/bin/env python3
"""Validate and optionally publish the exact policy-authorized Phase 5 smoke."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_kaggle_publisher import publish_phase5_kaggle_handoff


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle-dir", type=Path, required=True)
    parser.add_argument("--job-dataset-dir", type=Path, required=True)
    parser.add_argument("--kernel-package-dir", type=Path, required=True)
    parser.add_argument("--policy-decision", type=Path, required=True)
    parser.add_argument("--policy-decision-manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Create the two private datasets and push the private kernel")
    print(json.dumps(publish_phase5_kaggle_handoff(**vars(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
