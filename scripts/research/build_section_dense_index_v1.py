#!/usr/bin/env python3
"""Build/resume the E5 section index on CPU or CUDA/Kaggle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.dense_retrieval import build_dense_index, resolve_device


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--section-artifact-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="auto")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--model", help="Optional local open-model path or model ID override")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    device = resolve_device(args.device)
    dense = config["dense"]
    batch_size = args.batch_size or int(dense["cuda_batch_size"] if device == "cuda" else dense["cpu_batch_size"])
    root = args.section_artifact_dir
    receipt = build_dense_index(
        asset_path=root / "section_chunk_assets_v1.jsonl",
        source_closure_path=root / "section_source_closure_v1.jsonl",
        output_dir=args.output_dir,
        contract=json.loads((root / "section_dense_contract_v1.json").read_text(encoding="utf-8")),
        model_name=str(args.model or dense["model"]),
        requested_device=args.device,
        batch_size=batch_size,
        checkpoint_every_batches=int(dense["checkpoint_every_batches"]),
    )
    print(json.dumps({"status": "BUILT", **receipt}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
