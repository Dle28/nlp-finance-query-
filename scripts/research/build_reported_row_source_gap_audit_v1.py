#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.reported_row_source_gap_audit import build_reported_row_source_gap_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("reclassification-dir", "full-corpus-dir", "structured-tables", "evidence-context", "evidence-context-manifest", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--expected-question-count", type=int, default=1012)
    args = parser.parse_args()
    print(json.dumps(build_reported_row_source_gap_audit(
        reclassification_dir=args.reclassification_dir,
        full_corpus_dir=args.full_corpus_dir,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        evidence_context_manifest_path=args.evidence_context_manifest,
        output_dir=args.output_dir,
        expected_question_count=args.expected_question_count,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
