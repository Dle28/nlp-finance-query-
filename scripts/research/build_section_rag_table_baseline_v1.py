#!/usr/bin/env python3
"""Create the sanitized table-only baseline for section RAG evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.section_rag_evaluation import build_section_rag_table_baseline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hybrid-artifact-dir", type=Path, required=True)
    parser.add_argument("--full-corpus-artifact-dir", type=Path)
    parser.add_argument("--route-materialization-dir", type=Path)
    parser.add_argument("--navigation-extension-question-id", type=int, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_section_rag_table_baseline(
        hybrid_artifact_dir=args.hybrid_artifact_dir,
        output_dir=args.output_dir,
        full_corpus_artifact_dir=args.full_corpus_artifact_dir,
        route_materialization_dir=args.route_materialization_dir,
        navigation_extension_question_ids=args.navigation_extension_question_id,
    ), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
