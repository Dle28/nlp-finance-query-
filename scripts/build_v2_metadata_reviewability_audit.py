#!/usr/bin/env python3
"""Audit the reviewability contract of a V2 metadata queue."""
from __future__ import annotations

import argparse
from pathlib import Path
from finance_query.v2_reviewability import build_v2_reviewability_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(build_v2_reviewability_audit(queue_path=args.queue, output_dir=args.output_dir)["manifest_path"])


if __name__ == "__main__":
    main()
