#!/usr/bin/env python3
"""Validate strict full-corpus direct-lookup diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.full_corpus_direct_lookup_adapter import validate_full_corpus_direct_lookup_adapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    print(
        json.dumps(
            validate_full_corpus_direct_lookup_adapter(
                args.artifact_dir,
                expected_question_count=args.expected_question_count,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
