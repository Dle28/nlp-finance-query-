#!/usr/bin/env python3
"""Measure local model throughput and extrapolate ViFinQA runtime.

The training benchmark uses synthetic query/passage pairs only to measure
hardware throughput. It is not a quality-training run and its model output is
discarded.

By default the benchmark exposes only one CUDA device. SentenceTransformers'
legacy ``fit`` path may otherwise wrap the model in DataParallel on Kaggle's
2xT4 runtime, which concentrates gradient reduction on GPU 0 and can OOM even
when aggregate VRAM appears sufficient.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from itertools import islice
from pathlib import Path

from finance_query.corpus import iter_assets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assets",
        type=Path,
        default=Path("artifacts/table_assets.jsonl"),
    )
    parser.add_argument("--model", default="intfloat/multilingual-e5-small")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="Embedding/indexing batch size.",
    )
    parser.add_argument(
        "--train-batch-size",
        type=int,
        default=2,
        help="Synthetic training batch size. Keep >=2 for in-batch negatives.",
    )
    parser.add_argument("--sample-size", type=int, default=256)
    parser.add_argument("--train-steps", type=int, default=5)
    parser.add_argument("--train-pairs", type=int, default=1000)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=512,
        help="Sequence length used for embedding/indexing throughput.",
    )
    parser.add_argument(
        "--train-max-seq-length",
        type=int,
        default=256,
        help="Sequence length used for the synthetic training benchmark.",
    )
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--gpu-id",
        default="0",
        help="Single physical GPU exposed to the process when device is CUDA.",
    )
    parser.add_argument(
        "--allow-multi-gpu",
        action="store_true",
        help="Do not mask other GPUs. Not recommended with legacy model.fit/DataParallel.",
    )
    parser.add_argument(
        "--gradient-checkpointing",
        action="store_true",
        help="Enable transformer gradient checkpointing during the training benchmark.",
    )
    parser.add_argument(
        "--encode-only",
        action="store_true",
        help="Measure only dense-index encoding throughput and skip backward/training.",
    )
    return parser.parse_args()


def model_text(model_name: str, text: str, *, query: bool) -> str:
    if "e5" in model_name.casefold():
        return f"{'query' if query else 'passage'}: {text}"
    return text


def count_assets(path: Path) -> int:
    with path.open(encoding="utf-8") as file:
        return sum(1 for line in file if line.strip())


def main() -> None:
    args = parse_args()
    if not args.assets.is_file():
        raise FileNotFoundError(
            f"Assets not found: {args.assets}. Run `finance-query build-assets` first."
        )

    # Must happen before importing torch/sentence-transformers.
    requested_cuda = args.device is None or str(args.device).startswith("cuda")
    if requested_cuda and not args.allow_multi_gpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu_id)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    from sentence_transformers import InputExample, SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from torch.utils.data import DataLoader

    total_assets = count_assets(args.assets)
    sample = list(islice(iter_assets(args.assets), args.sample_size))
    if not sample:
        raise ValueError("No table assets were loaded.")

    model = SentenceTransformer(args.model, device=args.device)
    model.max_seq_length = args.max_seq_length
    device = str(model.device)
    use_amp = device.startswith("cuda")

    passages = [
        model_text(args.model, asset.get("search_text", ""), query=False)
        for asset in sample
    ]

    model.encode(
        passages[: min(args.batch_size, len(passages))],
        batch_size=args.batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    started = time.perf_counter()
    model.encode(
        passages,
        batch_size=args.batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    encode_seconds = time.perf_counter() - started
    tables_per_second = len(passages) / encode_seconds
    estimated_index_seconds = total_assets / tables_per_second

    output: dict[str, object] = {
        "model": args.model,
        "device": device,
        "visible_cuda_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "embedding_batch_size": args.batch_size,
        "embedding_max_seq_length": args.max_seq_length,
        "asset_count": total_assets,
        "encoding_sample_size": len(sample),
        "tables_per_second": tables_per_second,
        "estimated_full_dense_index_hours": estimated_index_seconds / 3600,
    }

    if args.encode_only:
        output.update(
            {
                "training_benchmark_skipped": True,
                "warning": "Encoding benchmark only; no training-time estimate was measured.",
            }
        )
        print(json.dumps(output, indent=2))
        return

    model.max_seq_length = min(args.train_max_seq_length, args.max_seq_length)
    if args.gradient_checkpointing:
        first_module = model[0]
        auto_model = getattr(first_module, "auto_model", None)
        if auto_model is None or not hasattr(auto_model, "gradient_checkpointing_enable"):
            raise RuntimeError("Selected SentenceTransformer does not expose gradient checkpointing.")
        auto_model.gradient_checkpointing_enable()

    examples = []
    for asset in sample:
        passage = asset.get("search_text", "")
        query = asset.get("context_before") or passage[:300]
        examples.append(
            InputExample(
                texts=[
                    model_text(args.model, query, query=True),
                    model_text(args.model, passage, query=False),
                ]
            )
        )

    loader = DataLoader(
        examples,
        shuffle=True,
        batch_size=args.train_batch_size,
        drop_last=len(examples) >= args.train_batch_size,
    )
    steps = min(args.train_steps, max(1, len(loader)))
    train_loss = MultipleNegativesRankingLoss(model)

    started = time.perf_counter()
    model.fit(
        train_objectives=[(loader, train_loss)],
        epochs=1,
        steps_per_epoch=steps,
        warmup_steps=0,
        show_progress_bar=False,
        use_amp=use_amp,
    )
    train_seconds = time.perf_counter() - started
    seconds_per_step = train_seconds / steps
    target_steps = math.ceil(args.train_pairs / args.train_batch_size) * args.epochs
    estimated_train_seconds = seconds_per_step * target_steps

    output.update(
        {
            "training_benchmark_steps": steps,
            "training_batch_size": args.train_batch_size,
            "training_max_seq_length": model.max_seq_length,
            "gradient_checkpointing": args.gradient_checkpointing,
            "seconds_per_training_step": seconds_per_step,
            "target_training_pairs": args.train_pairs,
            "target_epochs": args.epochs,
            "estimated_training_hours": estimated_train_seconds / 3600,
            "warning": (
                "Training throughput uses synthetic pairs and estimates runtime only; "
                "it does not estimate retrieval quality."
            ),
        }
    )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
