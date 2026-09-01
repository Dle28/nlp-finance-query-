#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.multi_operand_review import validate_multi_operand_review  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--question-id", type=int, action="append", dest="question_ids")
    args = parser.parse_args()
    result = validate_multi_operand_review(
        args.artifact_dir, expected_question_ids=args.question_ids
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
