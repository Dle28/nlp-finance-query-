#!/usr/bin/env python3
"""Rebind human semantic decisions and add authorized ChatGPT role reviews."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.entity_role_chatgpt_reviews import augment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-queue", type=Path, required=True)
    parser.add_argument("--prior-decisions", type=Path, required=True)
    parser.add_argument("--current-queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(augment(**vars(args)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
