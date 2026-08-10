#!/usr/bin/env python3
"""Fine-tune a dense table retriever from provenance-validated question/table pairs.

Required JSONL schema:

{"question": "...", "positive_table_uids": ["uid1", "uid2"]}

The public ViFinQA question file does not contain these labels. This script
therefore refuses to infer positives from question IDs or raw offsets.
``machine_silver`` mode accepts only V4 autonomous labels carrying the raw-V2
canonical-context consensus protocol; provisional machine guesses are refused.

On multi-GPU notebook runtimes the script exposes a single GPU by default. The
legacy SentenceTransformers ``fit`` path otherwise uses DataParallel, which can
concentrate gradient reduction on GPU 0 and cause avoidable OOMs on 2xT4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from finance_query.evidence_context import AUTONOMOUS_REVIEW_PROTOCOL
from finance_query.direct_replay import DIRECT_REPLAY_PROTOCOL
from finance_query.retrieval import AssetStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-jsonl", type=Path, required=True)
    parser.add_argument(
        "--asset-db",
        type=Path,
        default=Path("artifacts/lexical_index.sqlite3"),
    )
    parser.add_argument(
        "--bundle-tables",
        type=Path,
        default=None,
        help="Immutable bundle tables.jsonl; use this when its UID namespace differs from asset-db.",
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
    parser.add_argument(
        "--label-provenance",
        choices=["human_verified", "machine_silver"],
        default="human_verified",
        help="Validate the declared supervision source before training.",
    )
    parser.add_argument(
        "--direct-replay",
        type=Path,
        default=None,
        help="Replay sidecar whose SHA must match every machine-silver label gate.",
    )
    parser.add_argument(
        "--min-pairs",
        type=int,
        default=1,
        help="Refuse/defer training below this many source-validated pairs.",
    )
    parser.add_argument(
        "--defer-below-min",
        action="store_true",
        help="Report insufficient input without writing a model.",
    )
    return parser.parse_args()


def model_text(model_name: str, text: str, *, query: bool) -> str:
    if "e5" in model_name.casefold():
        return f"{'query' if query else 'passage'}: {text}"
    return text


def validate_provenance(
    row: dict,
    provenance: str,
    line_number: int,
    *,
    expected_replay_sha: str | None = None,
) -> None:
    if provenance == "human_verified":
        if str(row.get("annotation_status") or "") != "human_verified":
            raise ValueError(f"Line {line_number} is not a complete human_verified label")
        if str(row.get("label_source") or "") != "human":
            raise ValueError(f"Line {line_number} does not declare human label_source")
        if not bool((row.get("structure_validation") or {}).get("complete")):
            raise ValueError(f"Line {line_number} lacks complete V2 human validation")
        return

    self_review = row.get("machine_self_review") or {}
    if str(row.get("annotation_status") or "") != "machine_calibrated":
        raise ValueError(f"Line {line_number} is not machine_calibrated silver")
    if str(row.get("label_source") or "") != "machine":
        raise ValueError(f"Line {line_number} does not declare machine label_source")
    if not bool((row.get("structure_validation") or {}).get("validated")):
        raise ValueError(f"Line {line_number} lacks exact V2 row validation")
    if str(self_review.get("protocol") or "") != AUTONOMOUS_REVIEW_PROTOCOL:
        raise ValueError(
            f"Line {line_number} lacks the numeric-safe autonomous V4 source protocol"
        )
    if not bool(self_review.get("training_eligible")):
        raise ValueError(f"Line {line_number} is not training-eligible autonomous silver")
    replay = row.get("direct_replay_gate") or {}
    replay_sha = str(replay.get("replay_artifact_sha256") or "")
    if (
        str(replay.get("protocol") or "") != DIRECT_REPLAY_PROTOCOL
        or str(replay.get("status") or "") != "shadow_replay_ready"
        or int(replay.get("question_id") or -1) != int(row.get("id") or -2)
        or str(replay.get("machine_selected_uid") or "")
        not in {str(value) for value in row.get("positive_table_uids") or []}
        or len(replay_sha) != 64
        or any(character not in "0123456789abcdef" for character in replay_sha)
        or replay_sha != expected_replay_sha
        or replay.get("training_gate_only") is not True
    ):
        raise ValueError(f"Line {line_number} lacks a valid independent direct replay gate")


def load_label_rows(
    path: Path,
    provenance: str,
    *,
    expected_replay_sha: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
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
            validate_provenance(
                row,
                provenance,
                line_number,
                expected_replay_sha=expected_replay_sha,
            )
            rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_search_text(table: dict) -> str:
    rows = table.get("rows") or []
    return " ".join(
        str(value)
        for value in [
            table.get("document_id"),
            table.get("ticker"),
            table.get("report_year"),
            table.get("scope"),
            table.get("context_before"),
            *(table.get("headers") or table.get("column_labels") or []),
            *(cell for row in rows for cell in row),
        ]
        if value is not None
    )


def bundle_assets(path: Path) -> dict[str, dict]:
    output: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            uid = str(row.get("internal_table_uid") or "")
            if not uid:
                raise ValueError(f"Bundle table line {line_number} lacks internal_table_uid")
            if uid in output:
                raise ValueError(f"Duplicate table UID {uid} in {path}")
            output[uid] = {"search_text": bundle_search_text(row)}
    return output


def load_examples(
    rows: list[dict],
    store: AssetStore | None,
    bundle_tables: Path | None,
    model_name: str,
    input_example_cls,
) -> list:
    required_uids = [
        str(uid) for row in rows for uid in row.get("positive_table_uids") or []
    ]
    if bundle_tables is not None:
        assets = bundle_assets(bundle_tables)
    elif store is not None:
        assets = store.get_assets(required_uids)
    else:  # pragma: no cover - guarded by the CLI default
        raise ValueError("Either asset-db or bundle-tables must be available")

    examples: list = []
    for row in rows:
        query = model_text(model_name, str(row["question"]), query=True)
        for uid in row["positive_table_uids"]:
            asset = assets.get(str(uid))
            if asset is None:
                raise KeyError(f"Positive table UID not found in the selected source: {uid}")
            passage = model_text(model_name, asset["search_text"], query=False)
            examples.append(input_example_cls(texts=[query, passage]))

    if not examples:
        raise ValueError("No training pairs were loaded.")
    return examples


def main() -> None:
    args = parse_args()

    expected_replay_sha: str | None = None
    if args.label_provenance == "machine_silver":
        if args.direct_replay is None or not args.direct_replay.is_file():
            raise FileNotFoundError("Machine-silver training requires --direct-replay")
        expected_replay_sha = sha256_file(args.direct_replay)
        manifest_path = args.direct_replay.with_suffix(".manifest.json")
        if not manifest_path.is_file():
            raise FileNotFoundError("Direct replay manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(manifest.get("sidecar_sha256") or "") != expected_replay_sha:
            raise ValueError("Direct replay hash differs from its manifest")
    label_rows = load_label_rows(
        args.train_jsonl,
        args.label_provenance,
        expected_replay_sha=expected_replay_sha,
    )
    pair_count = sum(
        len(row.get("positive_table_uids") or []) for row in label_rows
    )
    if pair_count < args.min_pairs:
        message = (
            f"Need at least {args.min_pairs} {args.label_provenance} pairs; "
            f"only {pair_count} passed provenance validation."
        )
        if args.defer_below_min:
            print(json.dumps({"status": "deferred", "reason": message}, ensure_ascii=False))
            return
        raise RuntimeError(message)

    requested_cuda = args.device is None or str(args.device).startswith("cuda")
    if requested_cuda and not args.allow_multi_gpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu_id)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    from sentence_transformers import InputExample, SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from torch.utils.data import DataLoader

    store = None if args.bundle_tables else AssetStore(args.asset_db)
    examples = load_examples(
        label_rows,
        store,
        args.bundle_tables,
        args.model,
        InputExample,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

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
        "label_provenance": args.label_provenance,
        "min_pairs": args.min_pairs,
        "bundle_tables": str(args.bundle_tables) if args.bundle_tables else None,
        "direct_replay_sha256": expected_replay_sha,
    }
    (args.output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
