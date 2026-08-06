#!/usr/bin/env python3
"""Train a table reranker from verified query/table relevance labels.

Expected JSONL rows:
{"question": "...", "table_uid": "...", "label": 1.0}

Use hard negatives from the same company/year/scope whenever possible.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentence_transformers import CrossEncoder, InputExample
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
    parser.add_argument("--model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/reranker_finetuned"))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--device", default=None)
    return parser.parse_args()


def load_examples(path: Path, store: AssetStore) -> list[InputExample]:
    rows: list[dict] = []
    uids: list[str] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("question") or not row.get("table_uid") or "label" not in row:
                raise ValueError(
                    f"Line {line_number} must contain question, table_uid, and label"
                )
            rows.append(row)
            uids.append(str(row["table_uid"]))

    assets = store.get_assets(uids)
    examples: list[InputExample] = []
    for row in rows:
        uid = str(row["table_uid"])
        asset = assets.get(uid)
        if asset is None:
            raise KeyError(f"Table UID not found in asset DB: {uid}")
        examples.append(
            InputExample(
                texts=[str(row["question"]), asset["search_text"]],
                label=float(row["label"]),
            )
        )
    return examples


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    examples = load_examples(args.train_jsonl, AssetStore(args.asset_db))
    if not examples:
        raise ValueError("No reranker examples were loaded.")

    model = CrossEncoder(
        args.model,
        num_labels=1,
        max_length=args.max_length,
        device=args.device,
    )
    train_loader = DataLoader(examples, shuffle=True, batch_size=args.batch_size)
    warmup_steps = max(1, int(len(train_loader) * args.epochs * 0.1))
    use_amp = str(model.device).startswith("cuda")
    model.fit(
        train_dataloader=train_loader,
        epochs=args.epochs,
        warmup_steps=warmup_steps,
        output_path=str(args.output_dir),
        show_progress_bar=True,
        use_amp=use_amp,
    )

    metadata = {
        "base_model": args.model,
        "training_examples": len(examples),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "mixed_precision": use_amp,
        "labels": "verified relevance scores",
    }
    (args.output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
