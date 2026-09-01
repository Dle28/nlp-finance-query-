#!/usr/bin/env python3
"""Build/resume the same dense table index on CPU or CUDA/Kaggle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.dense_retrieval import build_dense_index, resolve_device  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--source-closure", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--model", help="Optional local path or open model ID override")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    dense = config["dense"]
    resolved_device = resolve_device(args.device)
    if args.batch_size is not None:
        batch_size = args.batch_size
    else:
        batch_size = int(dense["cuda_batch_size"] if resolved_device == "cuda" else dense["cpu_batch_size"])
    receipt = build_dense_index(
        asset_path=args.assets,
        source_closure_path=args.source_closure,
        output_dir=args.output_dir,
        contract=config["asset_contract"],
        model_name=str(args.model or dense["model"]),
        requested_device=args.device,
        batch_size=batch_size,
        checkpoint_every_batches=int(dense["checkpoint_every_batches"]),
    )
    print(json.dumps({"status": "BUILT", **receipt}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
