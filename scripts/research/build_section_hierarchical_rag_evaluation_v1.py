#!/usr/bin/env python3
"""Evaluate table-only, section-only, and hierarchical RAG candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.section_rag_evaluation import evaluate_section_hierarchical_rag


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--section-artifact-dir", type=Path, required=True)
    parser.add_argument("--section-dense-index-dir", type=Path, required=True)
    parser.add_argument("--hybrid-artifact-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--execution", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate_section_hierarchical_rag(config_path=args.config, section_artifact_dir=args.section_artifact_dir, section_dense_index_dir=args.section_dense_index_dir, hybrid_artifact_dir=args.hybrid_artifact_dir, output_dir=args.output_dir, requested_device=args.device, bindings_path=args.bindings, execution_path=args.execution), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
