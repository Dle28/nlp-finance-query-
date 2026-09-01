#!/usr/bin/env python3
"""Build the Agent 3 multi-column semantic diagnostic sidecar."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.multicol_semantic_diagnostic import (  # noqa: E402
    build_multicol_semantic_diagnostic,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--document-metadata", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = build_multicol_semantic_diagnostic(
        config_path=args.config,
        questions_path=args.questions,
        structured_tables_path=args.structured_tables,
        evidence_context_path=args.evidence_context,
        document_metadata_path=args.document_metadata,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
