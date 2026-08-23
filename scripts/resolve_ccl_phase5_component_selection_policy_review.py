#!/usr/bin/env python3
"""Validate a human Phase 5 policy response and record its dispatch gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.certified_canonical.phase5_component_selection_policy import (
    resolve_phase5_component_selection_policy_review,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-review", type=Path, required=True)
    parser.add_argument("--policy-template", type=Path, required=True)
    parser.add_argument("--policy-manifest", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(json.dumps(resolve_phase5_component_selection_policy_review(**vars(parser.parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
