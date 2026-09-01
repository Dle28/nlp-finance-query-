#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.hybrid_retrieval_analysis import (  # noqa: E402
    validate_hybrid_retrieval_analysis,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    parser.add_argument("--expected-route-count", type=int, default=1232)
    args = parser.parse_args()
    result = validate_hybrid_retrieval_analysis(
        args.artifact_dir,
        expected_question_count=args.expected_question_count,
        expected_route_count=args.expected_route_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
