#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.hybrid_retrieval_analysis import (  # noqa: E402
    build_hybrid_retrieval_analysis,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--lexical-candidates", type=Path, required=True)
    parser.add_argument("--dense-index-dir", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    args = parser.parse_args()
    report = build_hybrid_retrieval_analysis(
        config_path=args.config,
        plans_path=args.plans,
        lexical_candidates_path=args.lexical_candidates,
        dense_index_dir=args.dense_index_dir,
        assets_path=args.assets,
        output_dir=args.output_dir,
        requested_device=args.device,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
