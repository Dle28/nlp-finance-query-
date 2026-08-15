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
import math
import os
from pathlib import Path
from typing import Any

from finance_query.synthetic_curriculum import (
    PASSAGE_LAYOUTS,
    PASSAGE_LAYOUT_CONTEXT_FIRST,
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
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument(
        "--steps-per-epoch",
        type=int,
        default=None,
        help="Optional bounded update count per epoch; must be positive when supplied.",
    )
    parser.add_argument(
        "--freeze-transformer-layers",
        type=int,
        default=0,
        help="Freeze this many lowest Transformer encoder layers before training.",
    )
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument(
        "--passage-layout",
        choices=sorted(PASSAGE_LAYOUTS),
        default=PASSAGE_LAYOUT_CONTEXT_FIRST,
        help="Order table evidence relative to report context; recorded in training metadata.",
    )
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
    *,
    passage_layout: str = PASSAGE_LAYOUT_CONTEXT_FIRST,
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
                    model_text(
                        model_name,
                        table_passage(assets[positive_uid], layout=passage_layout),
                        query=False,
                    ),
                    model_text(
                        model_name,
                        table_passage(assets[negative_uid], layout=passage_layout),
                        query=False,
                    ),
                ]
            )
        )
        hard_negative_count += 1
    return examples, hard_negative_count


def configure_training_environment(*, device: str | None, gpu_id: str) -> None:
    """Set deterministic runtime switches before importing training libraries."""
    if device is None or str(device).startswith("cuda"):
        os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    # SentenceTransformers delegates to Transformers callbacks.  A Kaggle GPU
    # job has no interactive W&B login, so telemetry must be disabled before
    # either package is imported.
    os.environ["WANDB_DISABLED"] = "true"
    os.environ["WANDB_MODE"] = "disabled"


def validate_training_controls(
    *,
    learning_rate: float,
    steps_per_epoch: int | None,
    freeze_transformer_layers: int,
    seed: int,
) -> None:
    """Reject unsafe or ambiguous candidate controls before model loading."""
    if not math.isfinite(learning_rate) or not 0.0 < learning_rate <= 1e-3:
        raise ValueError("learning-rate must be finite and in (0, 1e-3]")
    if steps_per_epoch is not None and steps_per_epoch < 1:
        raise ValueError("steps-per-epoch must be positive when supplied")
    if freeze_transformer_layers < 0:
        raise ValueError("freeze-transformer-layers must be non-negative")
    if seed < 0:
        raise ValueError("seed must be non-negative")


def freeze_transformer_layers(model: Any, *, count: int) -> int:
    """Freeze a bounded lower prefix and fail if the selected encoder is unknown."""
    if count == 0:
        return 0
    try:
        auto_model = model[0].auto_model
        layers = auto_model.encoder.layer
    except (AttributeError, IndexError, TypeError) as error:
        raise RuntimeError("Selected model does not expose Transformer encoder layers") from error
    if count > len(layers):
        raise ValueError(
            f"freeze-transformer-layers={count} exceeds available encoder layers={len(layers)}"
        )
    for layer in layers[:count]:
        for parameter in layer.parameters():
            parameter.requires_grad = False
    return count


def main() -> None:
    args = parse_args()
    if args.batch_size < 2:
        raise ValueError("batch-size must be at least 2 for in-batch negatives")
    if args.epochs < 1:
        raise ValueError("epochs must be positive")
    if not 0.0 <= args.warmup_ratio <= 1.0:
        raise ValueError("warmup-ratio must be in [0, 1]")
    validate_training_controls(
        learning_rate=args.learning_rate,
        steps_per_epoch=args.steps_per_epoch,
        freeze_transformer_layers=args.freeze_transformer_layers,
        seed=args.seed,
    )
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

    configure_training_environment(device=args.device, gpu_id=args.gpu_id)
    from sentence_transformers import InputExample, SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    import torch
    from torch.utils.data import DataLoader

    examples, hard_negative_count = make_training_examples(
        rows,
        assets,
        args.model,
        InputExample,
        passage_layout=args.passage_layout,
    )
    if len(examples) < args.batch_size:
        raise RuntimeError("Not enough examples for one complete training batch")
    model = SentenceTransformer(args.model, device=args.device)
    model.max_seq_length = args.max_seq_length
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    if args.gradient_checkpointing:
        auto_model = getattr(model[0], "auto_model", None)
        if auto_model is None or not hasattr(auto_model, "gradient_checkpointing_enable"):
            raise RuntimeError("Selected model does not expose gradient checkpointing")
        auto_model.gradient_checkpointing_enable()
    frozen_layer_count = freeze_transformer_layers(
        model, count=args.freeze_transformer_layers
    )
    loader_generator = torch.Generator()
    loader_generator.manual_seed(args.seed)
    train_loader = DataLoader(
        examples,
        shuffle=True,
        batch_size=args.batch_size,
        drop_last=True,
        generator=loader_generator,
    )
    train_loss = MultipleNegativesRankingLoss(model)
    effective_steps_per_epoch = args.steps_per_epoch or len(train_loader)
    warmup_steps = max(1, int(effective_steps_per_epoch * args.epochs * args.warmup_ratio))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.fit(
        train_objectives=[(train_loader, train_loss)],
        epochs=args.epochs,
        steps_per_epoch=args.steps_per_epoch,
        warmup_steps=warmup_steps,
        optimizer_params={"lr": args.learning_rate},
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
        "learning_rate": args.learning_rate,
        "steps_per_epoch": args.steps_per_epoch,
        "effective_steps_per_epoch": effective_steps_per_epoch,
        "warmup_steps": warmup_steps,
        "freeze_transformer_layers": frozen_layer_count,
        "seed": args.seed,
        "max_seq_length": args.max_seq_length,
        "passage_layout": args.passage_layout,
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
