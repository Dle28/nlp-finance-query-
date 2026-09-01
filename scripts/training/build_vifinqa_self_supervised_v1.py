#!/usr/bin/env python3
"""Build leakage-resistant retriever/reranker/program training datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.training.synthetic_curriculum import build_curriculum  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "configs/training/vifinqa_rag_finetune_v1.json")
    parser.add_argument(
        "--tables",
        type=Path,
        default=ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-tables", type=int)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    manifest = build_curriculum(
        tables_path=args.tables,
        output_dir=args.output_dir,
        config=config,
        max_tables=args.max_tables,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
