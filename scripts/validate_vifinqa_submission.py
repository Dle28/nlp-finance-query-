#!/usr/bin/env python3
"""Validate a ViFinQA submission ZIP against the official evidence contract."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from finance_query.submission import validate_submission_zip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data/ViFinQA/questions/questions.jsonl"),
    )
    return parser.parse_args()


def load_questions(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main() -> None:
    args = parse_args()
    temporary = Path(tempfile.mkdtemp(prefix="vifinqa_submission_validate_"))
    try:
        result = validate_submission_zip(args.archive, load_questions(args.questions), temporary)
    finally:
        shutil.rmtree(temporary)
    print(json.dumps({"status": "valid", **result.to_dict()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
