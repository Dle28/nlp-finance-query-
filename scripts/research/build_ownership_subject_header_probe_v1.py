#!/usr/bin/env python3
"""Build a navigation-only ownership subject/header probe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.ownership_subject_header_probe import build_ownership_subject_header_probe


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("subject-probe-dir", "reclassification-dir", "structured-tables", "evidence-context", "evidence-context-manifest", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    print(
        json.dumps(
            build_ownership_subject_header_probe(
                subject_probe_dir=args.subject_probe_dir,
                reclassification_dir=args.reclassification_dir,
                structured_tables_path=args.structured_tables,
                evidence_context_path=args.evidence_context,
                evidence_context_manifest_path=args.evidence_context_manifest,
                output_dir=args.output_dir,
                expected_question_count=args.expected_question_count,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
