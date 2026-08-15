#!/usr/bin/env python3
"""Fine-tune BGE-M3 from synthetic examples with explicit hard negatives.

The script accepts only ``synthetic_execution_verified`` examples emitted by
``build_synthetic_finance_curriculum_v1.py``.  It checks the JSONL hash, the
source-table hash and replays every arithmetic target before importing model
training dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from finance_query.synthetic_curriculum import (
    SYNTHETIC_CURRICULUM_PROTOCOL,
    SYNTHETIC_PROVENANCE,
    load_jsonl,
    load_table_assets,
    sha256_file,
    table_passage,
    verify_synthetic_example,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle-tables", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "validation", "test"], default="train")
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument("--device", default=None)
    parser.add_argument("--gpu-id", default="0")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    return parser.parse_args()


def model_text(model_name: str, text: str, *, query: bool) -> str:
    if "e5" in model_name.casefold():
        return f"{'query' if query else 'passage'}: {text}"
    return text


def validate_manifest(
    curriculum_path: Path,
    manifest_path: Path,
    tables_path: Path,
) -> dict[str, Any]:
    if not curriculum_path.is_file() or not manifest_path.is_file() or not tables_path.is_file():
        raise FileNotFoundError("curriculum, manifest and bundle tables must all exist")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != SYNTHETIC_CURRICULUM_PROTOCOL:
        raise ValueError("Unexpected curriculum manifest protocol")
    if manifest.get("provenance") != SYNTHETIC_PROVENANCE:
        raise ValueError("Manifest does not declare synthetic execution verification")
    output = manifest.get("output") or {}
    input_info = manifest.get("input") or {}
    if output.get("examples_sha256") != sha256_file(curriculum_path):
        raise ValueError("Curriculum JSONL hash differs from its manifest")
    if input_info.get("tables_sha256") != sha256_file(tables_path):
        raise ValueError("Bundle tables hash differs from curriculum source lineage")
    if not bool((manifest.get("source_contract") or {}).get("training_eligible")):
        raise ValueError("Manifest is not training-eligible")
    return manifest


def load_training_rows(path: Path, *, split: str, expected_tables_sha256: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, row in enumerate(load_jsonl(path), start=1):
        if row.get("split") != split:
            continue
        errors = verify_synthetic_example(row)
        if errors:
            raise ValueError(f"Curriculum row {line_number} failed replay: {', '.join(errors)}")
        lineage = row.get("source_lineage") or {}
        if lineage.get("tables_sha256") != expected_tables_sha256:
            raise ValueError(f"Curriculum row {line_number} has a different source tables hash")
        if row.get("annotation_status") != SYNTHETIC_PROVENANCE:
            raise ValueError(f"Curriculum row {line_number} is not synthetic_execution_verified")
        rows.append(row)
    if not rows:
        raise ValueError(f"No validated rows for split={split!r}")
    return rows


def make_training_examples(
    rows: list[dict[str, Any]],
    assets: dict[str, dict[str, Any]],
    model_name: str,
    input_example_cls: type,
) -> tuple[list[Any], int]:
    examples: list[Any] = []
    hard_negative_count = 0
    for row in rows:
        positive_uid = str(row["positive_table_uids"][0])
        negative_uids = [str(uid) for uid in row["hard_negative_table_uids"]]
        if positive_uid not in assets:
            raise KeyError(f"Positive table missing from bundle: {positive_uid}")
        negative_uid = next((uid for uid in negative_uids if uid in assets), None)
        if negative_uid is None:
            raise KeyError(f"No hard-negative table exists in bundle for {row['curriculum_id']}")
        if negative_uid == positive_uid:
            raise ValueError(f"Positive/negative overlap for {row['curriculum_id']}")
        examples.append(
            input_example_cls(
                texts=[
                    model_text(model_name, str(row["question"]), query=True),
                    model_text(model_name, table_passage(assets[positive_uid]), query=False),
                    model_text(model_name, table_passage(assets[negative_uid]), query=False),
                ]
            )
        )
        hard_negative_count += 1
    return examples, hard_negative_count


def main() -> None:
    args = parse_args()
    if args.batch_size < 2:
        raise ValueError("batch-size must be at least 2 for in-batch negatives")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite trained artifact: {args.output_dir}")
    manifest = validate_manifest(args.curriculum, args.manifest, args.bundle_tables)
    expected_tables_sha256 = str(manifest["input"]["tables_sha256"])
    rows = load_training_rows(
        args.curriculum,
        split=args.split,
        expected_tables_sha256=expected_tables_sha256,
    )
    assets = {
        str(asset["internal_table_uid"]): asset
        for asset in load_table_assets(args.bundle_tables)
    }

    if args.device is None or str(args.device).startswith("cuda"):
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu_id)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    from sentence_transformers import InputExample, SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from torch.utils.data import DataLoader

    examples, hard_negative_count = make_training_examples(
        rows, assets, args.model, InputExample
    )
    if len(examples) < args.batch_size:
        raise RuntimeError("Not enough examples for one complete training batch")
    model = SentenceTransformer(args.model, device=args.device)
    model.max_seq_length = args.max_seq_length
    if args.gradient_checkpointing:
        auto_model = getattr(model[0], "auto_model", None)
        if auto_model is None or not hasattr(auto_model, "gradient_checkpointing_enable"):
            raise RuntimeError("Selected model does not expose gradient checkpointing")
        auto_model.gradient_checkpointing_enable()
    train_loader = DataLoader(examples, shuffle=True, batch_size=args.batch_size, drop_last=True)
    train_loss = MultipleNegativesRankingLoss(model)
    warmup_steps = max(1, int(len(train_loader) * args.epochs * args.warmup_ratio))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.fit(
        train_objectives=[(train_loader, train_loss)],
        epochs=args.epochs,
        warmup_steps=warmup_steps,
        output_path=str(args.output_dir),
        show_progress_bar=True,
        use_amp=str(model.device).startswith("cuda"),
    )
    metadata = {
        "protocol": SYNTHETIC_CURRICULUM_PROTOCOL,
        "provenance": SYNTHETIC_PROVENANCE,
        "base_model": args.model,
        "split": args.split,
        "training_examples": len(examples),
        "explicit_hard_negative_examples": hard_negative_count,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "max_seq_length": args.max_seq_length,
        "loss": "MultipleNegativesRankingLoss(query, positive, hard_negative)",
        "curriculum_sha256": sha256_file(args.curriculum),
        "source_tables_sha256": expected_tables_sha256,
        "manifest_path": str(args.manifest),
    }
    (args.output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
