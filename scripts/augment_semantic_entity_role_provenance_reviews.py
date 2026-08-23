#!/usr/bin/env python3
"""Attach approved ChatGPT document-line role reviews to semantic decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.entity_role_provenance_augmentation import augment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-queue", type=Path, required=True)
    parser.add_argument("--prior-semantic-decisions", type=Path, required=True)
    parser.add_argument("--provenance-queue", type=Path, required=True)
    parser.add_argument("--provenance-decisions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(augment(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
