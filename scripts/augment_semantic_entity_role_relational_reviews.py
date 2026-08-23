#!/usr/bin/env python3
"""Attach exact-consensus relational role evidence to semantic decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.entity_role_relational_reviews import augment_semantic_decisions_with_relational_roles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-queue", type=Path, required=True)
    parser.add_argument("--prior-semantic-decisions", type=Path, required=True)
    parser.add_argument("--relational-queue", type=Path, required=True)
    parser.add_argument("--reconciled-decisions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(augment_semantic_decisions_with_relational_roles(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
