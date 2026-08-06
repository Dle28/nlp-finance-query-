#!/usr/bin/env python3
"""Fine-tune a dense table retriever from verified question/table pairs.

Required JSONL schema:

{"question": "...", "positive_table_uids": ["uid1", "uid2"]}

The public ViFinQA question file does not contain these labels. This script
therefore refuses to infer positives from question IDs or raw offsets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentence_transformers import InputExample, SentenceTransformer, losses
from torch.utils.data import DataLoader

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
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument("--device", default=None)
    return parser.parse_args()


def model_text(model_name: str, text: str, *, query: bool) -> str:
    if "e5" in model_name.casefold():
        return f"{'query' if query else 'passage'}: {text}"
    return text


def load_examples(path: Path, store: AssetStore, model_name: str) -> list[InputExample]:
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
    examples: list[InputExample] = []
    for row in rows:
        query = model_text(model_name, str(row["question"]), query=True)
        for uid in row["positive_table_uids"]:
            asset = assets.get(str(uid))
            if asset is None:
                raise KeyError(f"Positive table UID not found in asset DB: {uid}")
            passage = model_text(model_name, asset["search_text"], query=False)
            examples.append(InputExample(texts=[query, passage]))

    if not examples:
        raise ValueError("No training pairs were loaded.")
    return examples


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    store = AssetStore(args.asset_db)
    examples = load_examples(args.train_jsonl, store, args.model)

    model = SentenceTransformer(args.model, device=args.device)
    model.max_seq_length = args.max_seq_length
    train_loader = DataLoader(
        examples,
        shuffle=True,
        batch_size=args.batch_size,
        drop_last=len(examples) >= args.batch_size,
    )
    train_loss = losses.MultipleNegativesRankingLoss(model)
    warmup_steps = max(
        1,
        int(len(train_loader) * args.epochs * args.warmup_ratio),
    )

    model.fit(
        train_objectives=[(train_loader, train_loss)],
        epochs=args.epochs,
        warmup_steps=warmup_steps,
        output_path=str(args.output_dir),
        show_progress_bar=True,
        use_amp=True,
    )

    metadata = {
        "base_model": args.model,
        "training_pairs": len(examples),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "loss": "MultipleNegativesRankingLoss",
        "label_requirement": "manually verified positive_table_uids",
    }
    (args.output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
