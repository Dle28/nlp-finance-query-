#!/usr/bin/env python3
"""Validate the Agent 3 multi-column semantic diagnostic sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.multicol_semantic_diagnostic import (  # noqa: E402
    validate_multicol_semantic_diagnostic,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--question-id", type=int, action="append")
    args = parser.parse_args()
    question_ids = args.question_id or [156, 242, 263]
    report = validate_multicol_semantic_diagnostic(
        args.artifact_dir,
        expected_question_ids=question_ids,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
