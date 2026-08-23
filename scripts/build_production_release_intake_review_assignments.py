#!/usr/bin/env python3
"""Create two blind, non-materializing reviewer assignments for one release intake."""
from __future__ import annotations

import argparse
from pathlib import Path

from verify_production_release_intake_reviews import build_assignments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake", type=Path, required=True)
    parser.add_argument("--intake-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build_assignments(**vars(args))["manifest_path"])


if __name__ == "__main__":
    main()
