#!/usr/bin/env python3
"""Fine-tune a dense table retriever from verified question/table pairs.

Required JSONL schema:

{"question": "...", "positive_table_uids": ["uid1", "uid2"]}

The public ViFinQA question file does not contain these labels. This script
therefore refuses to infer positives from question IDs or raw offsets.

On multi-GPU notebook runtimes the script exposes a single GPU by default. The
legacy SentenceTransformers ``fit`` path otherwise uses DataParallel, which can
concentrate gradient reduction on GPU 0 and cause avoidable OOMs on 2xT4.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from finance_query.retrieval import AssetStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-jsonl", type=Path, required=True)
    parser.add_argument(
        "--asset-db",
        type=Path,
        default=Path("artifacts/lexical_index.sqlite3"),
    )
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/retriever_finetuned"))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="Keep >=2 for MultipleNegativesRankingLoss in-batch negatives.",
    )
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--max-seq-length", type=int, default=256)
    parser.add_argument("--device", default=None)
    parser.add_argument("--gpu-id", default="0")
    parser.add_argument(
        "--allow-multi-gpu",
        action="store_true",
        help="Do not mask other GPUs. Prefer proper DDP instead of legacy DataParallel.",
    )
    parser.add_argument(
        "--gradient-checkpointing",
        action="store_true",
        help="Reduce activation memory at the cost of extra compute.",
    )
    return parser.parse_args()


def model_text(model_name: str, text: str, *, query: bool) -> str:
    if "e5" in model_name.casefold():
        return f"{'query' if query else 'passage'}: {text}"
    return text


def load_examples(path: Path, store: AssetStore, model_name: str, input_example_cls) -> list:
    rows: list[dict] = []
    required_uids: list[str] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            positives = row.get("positive_table_uids") or []
            if not row.get("question") or not positives:
                raise ValueError(
                    f"Line {line_number} must contain question and positive_table_uids"
                )
            rows.append(row)
            required_uids.extend(str(uid) for uid in positives)

    assets = store.get_assets(required_uids)
    examples: list = []
    for row in rows:
        query = model_text(model_name, str(row["question"]), query=True)
        for uid in row["positive_table_uids"]:
            asset = assets.get(str(uid))
            if asset is None:
                raise KeyError(f"Positive table UID not found in asset DB: {uid}")
            passage = model_text(model_name, asset["search_text"], query=False)
            examples.append(input_example_cls(texts=[query, passage]))

    if not examples:
        raise ValueError("No training pairs were loaded.")
    return examples


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    requested_cuda = args.device is None or str(args.device).startswith("cuda")
    if requested_cuda and not args.allow_multi_gpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu_id)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    from sentence_transformers import InputExample, SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from torch.utils.data import DataLoader

    store = AssetStore(args.asset_db)
    examples = load_examples(args.train_jsonl, store, args.model, InputExample)

    model = SentenceTransformer(args.model, device=args.device)
    model.max_seq_length = args.max_seq_length
    if args.gradient_checkpointing:
        first_module = model[0]
        auto_model = getattr(first_module, "auto_model", None)
        if auto_model is None or not hasattr(auto_model, "gradient_checkpointing_enable"):
            raise RuntimeError("Selected SentenceTransformer does not expose gradient checkpointing.")
        auto_model.gradient_checkpointing_enable()

    train_loader = DataLoader(
        examples,
        shuffle=True,
        batch_size=args.batch_size,
        drop_last=len(examples) >= args.batch_size,
    )
    train_loss = MultipleNegativesRankingLoss(model)
    warmup_steps = max(
        1,
        int(len(train_loader) * args.epochs * args.warmup_ratio),
    )
    use_amp = str(model.device).startswith("cuda")

    model.fit(
        train_objectives=[(train_loader, train_loss)],
        epochs=args.epochs,
        warmup_steps=warmup_steps,
        output_path=str(args.output_dir),
        show_progress_bar=True,
        use_amp=use_amp,
    )

    metadata = {
        "base_model": args.model,
        "training_pairs": len(examples),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "max_seq_length": args.max_seq_length,
        "gradient_checkpointing": args.gradient_checkpointing,
        "visible_cuda_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "loss": "MultipleNegativesRankingLoss",
        "mixed_precision": use_amp,
        "label_requirement": "manually verified positive_table_uids",
    }
    (args.output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
