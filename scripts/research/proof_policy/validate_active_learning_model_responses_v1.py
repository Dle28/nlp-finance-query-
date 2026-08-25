#!/usr/bin/env python3
"""Validate raw open-source active-learning responses against request packets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.proof_policy.active_learning_models import validate_raw_responses  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--raw-responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_raw_responses(requests_path=args.requests.resolve(), raw_responses_path=args.raw_responses.resolve(), output_path=args.output.resolve())
    print(json.dumps({"valid_proposal_count": result.valid_count, "non_proposal_count": result.invalid_count, "output": str(result.output_path)}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
