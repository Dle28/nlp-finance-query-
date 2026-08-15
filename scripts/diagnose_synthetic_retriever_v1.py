#!/usr/bin/env python3
"""Diagnose synthetic-retriever generalisation without changing promotion state.

This is deliberately separate from the issuer-held-out evaluator.  It can
include the synthetic ``train`` split only after an explicit opt-in, so it can
answer whether a failed held-out candidate learned the train distribution at
all.  It writes retrieval and query/positive/hard-negative similarity evidence
for diagnosis only; no output is eligible for benchmark evaluation, promotion,
or submission.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping

from finance_query.synthetic_curriculum import (
    SYNTHETIC_CURRICULUM_PROTOCOL,
    SYNTHETIC_PROVENANCE,
    load_jsonl,
    load_table_assets,
    sha256_file,
    table_passage,
    verify_synthetic_example,
)


DIAGNOSTIC_PROTOCOL = "synthetic_retriever_generalisation_diagnostic_v1"
ALLOWED_SPLITS = frozenset({"train", "validation", "test"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle-tables", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        metavar="LABEL=REFERENCE",
        help="Repeat, for example, base=BAAI/bge-m3 and finetuned=/path/to/model.",
    )
    parser.add_argument("--splits", nargs="+", default=["train", "validation", "test"])
    parser.add_argument(
        "--diagnostic-allow-train",
        action="store_true",
        help="Required before inspecting synthetic train rows; diagnostic use only.",
    )
    parser.add_argument("--ks", nargs="+", type=int, default=[1, 3, 5, 10, 20])
    parser.add_argument("--passage-batch-size", type=int, default=16)
    parser.add_argument("--query-batch-size", type=int, default=32)
    parser.add_argument("--max-seq-length", type=int, default=384)
    parser.add_argument("--device", default="cuda:0")
    return parser.parse_args()


def parse_models(raw_models: Iterable[str]) -> dict[str, str]:
    models: dict[str, str] = {}
    for raw in raw_models:
        label, separator, reference = raw.partition("=")
        label, reference = label.strip(), reference.strip()
        if not separator or not label or not reference:
            raise ValueError(f"Invalid --model value {raw!r}; expected LABEL=REFERENCE")
        if label in models:
            raise ValueError(f"Duplicate model label {label!r}")
        models[label] = reference
    if len(models) < 2:
        raise ValueError("Provide at least a base and a fine-tuned model for comparison")
    return models


def validate_inputs(curriculum_path: Path, manifest_path: Path, tables_path: Path) -> dict[str, Any]:
    if not curriculum_path.is_file() or not manifest_path.is_file() or not tables_path.is_file():
        raise FileNotFoundError("curriculum, manifest and bundle tables must all exist")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != SYNTHETIC_CURRICULUM_PROTOCOL:
        raise ValueError("Unexpected curriculum manifest protocol")
    if manifest.get("provenance") != SYNTHETIC_PROVENANCE:
        raise ValueError("Manifest does not declare synthetic execution verification")
    output, input_info = manifest.get("output") or {}, manifest.get("input") or {}
    if output.get("examples_sha256") != sha256_file(curriculum_path):
        raise ValueError("Curriculum JSONL hash differs from its manifest")
    if input_info.get("tables_sha256") != sha256_file(tables_path):
        raise ValueError("Bundle tables hash differs from curriculum source lineage")
    if not bool((manifest.get("source_contract") or {}).get("training_eligible")):
        raise ValueError("Manifest is not training-eligible")
    return manifest


def normalise_splits(splits: Iterable[str], *, diagnostic_allow_train: bool) -> tuple[str, ...]:
    selected = tuple(str(split) for split in splits)
    if not selected:
        raise ValueError("Select at least one split")
    if len(set(selected)) != len(selected):
        raise ValueError("Diagnostic splits must be unique")
    unsupported = set(selected) - ALLOWED_SPLITS
    if unsupported:
        raise ValueError(f"Unsupported diagnostic split(s): {sorted(unsupported)}")
    if "train" in selected and not diagnostic_allow_train:
        raise ValueError("Synthetic train split requires --diagnostic-allow-train")
    return selected


def load_diagnostic_rows(
    path: Path,
    *,
    splits: Iterable[str],
    expected_tables_sha256: str,
    diagnostic_allow_train: bool,
) -> dict[str, list[dict[str, Any]]]:
    selected = normalise_splits(splits, diagnostic_allow_train=diagnostic_allow_train)
    rows_by_split = {split: [] for split in selected}
    seen_ids: set[str] = set()
    for line_number, row in enumerate(load_jsonl(path), start=1):
        split = str(row.get("split") or "")
        if split not in rows_by_split:
            continue
        errors = verify_synthetic_example(row)
        if errors:
            raise ValueError(f"Curriculum row {line_number} failed replay: {', '.join(errors)}")
        lineage = row.get("source_lineage") or {}
        if lineage.get("tables_sha256") != expected_tables_sha256:
            raise ValueError(f"Curriculum row {line_number} has a different source tables hash")
        if row.get("annotation_status") != SYNTHETIC_PROVENANCE:
            raise ValueError(f"Curriculum row {line_number} is not synthetic_execution_verified")
        curriculum_id = str(row.get("curriculum_id") or "")
        if not curriculum_id or curriculum_id in seen_ids:
            raise ValueError(f"Duplicate or missing curriculum_id at line {line_number}")
        seen_ids.add(curriculum_id)
        rows_by_split[split].append(row)
    for split, rows in rows_by_split.items():
        if not rows:
            raise ValueError(f"No validated rows for diagnostic split={split!r}")
    return rows_by_split


def error_bucket(
    positive_uid: str,
    candidate_uid: str,
    assets: Mapping[str, Mapping[str, Any]],
) -> str:
    if candidate_uid == positive_uid:
        return "correct"
    positive, candidate = assets[positive_uid], assets[candidate_uid]
    if candidate.get("ticker") != positive.get("ticker"):
        return "wrong_entity"
    if candidate.get("report_year") != positive.get("report_year"):
        return "wrong_year"
    if candidate.get("scope") != positive.get("scope"):
        return "wrong_scope"
    if candidate.get("document_id") != positive.get("document_id"):
        return "wrong_document"
    return "same_context_wrong_table"


def summarise_rankings(
    rows: Iterable[Mapping[str, Any]],
    rankings: Mapping[str, list[str]],
    assets: Mapping[str, Mapping[str, Any]],
    *,
    ks: Iterable[int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ordered_ks = tuple(sorted({int(k) for k in ks if int(k) > 0}))
    if not ordered_ks:
        raise ValueError("At least one positive K is required")
    max_k = max(ordered_ks)
    row_results: list[dict[str, Any]] = []
    hit_counts = {k: 0 for k in ordered_ks}
    reciprocal_ranks: list[float] = []
    error_counts: Counter[str] = Counter()
    for row in rows:
        row_id = str(row.get("curriculum_id") or "")
        positives = [str(uid) for uid in row.get("positive_table_uids") or []]
        if not row_id or not positives or any(uid not in assets for uid in positives):
            raise ValueError(f"Invalid positive table binding for {row_id or 'unknown row'}")
        ranked = rankings.get(row_id)
        if ranked is None or len(ranked) < max_k or any(uid not in assets for uid in ranked[:max_k]):
            raise ValueError(f"Missing complete ranking for {row_id}")
        positive_set = set(positives)
        first_rank = next((index for index, uid in enumerate(ranked, start=1) if uid in positive_set), None)
        for k in ordered_ks:
            hit_counts[k] += int(first_rank is not None and first_rank <= k)
        reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
        bucket = error_bucket(positives[0], ranked[0], assets)
        error_counts[bucket] += 1
        row_results.append(
            {
                "curriculum_id": row_id,
                "positive_table_uids": positives,
                "ranked_table_uids": ranked[:max_k],
                "first_positive_rank": first_rank,
                "top1_error_bucket": bucket,
            }
        )
    count = len(row_results)
    if not count:
        raise ValueError("No rankings to summarise")
    return (
        {
            "questions": count,
            "mrr": sum(reciprocal_ranks) / count,
            "recall_at_k": {str(k): hit_counts[k] / count for k in ordered_ks},
            "top1_error_counts": dict(sorted(error_counts.items())),
            "top1_error_rates": {key: value / count for key, value in sorted(error_counts.items())},
        },
        row_results,
    )


def _hard_negative_uid(row: Mapping[str, Any], assets: Mapping[str, Mapping[str, Any]]) -> str:
    positive_uid = str((row.get("positive_table_uids") or [""])[0])
    candidates = [str(uid) for uid in row.get("hard_negative_table_uids") or []]
    if not candidates:
        raise ValueError(f"No hard-negative binding for {row.get('curriculum_id')!r}")
    missing = [uid for uid in candidates if uid not in assets]
    if missing:
        raise ValueError(f"Unknown hard-negative table binding for {row.get('curriculum_id')!r}")
    if candidates[0] == positive_uid:
        raise ValueError(f"Overlapping positive/hard-negative binding for {row.get('curriculum_id')!r}")
    return candidates[0]


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("Cannot summarise an empty distribution")
    return {
        "min": min(values),
        "mean": mean(values),
        "median": median(values),
        "max": max(values),
    }


def summarise_pairwise_scores(
    rows: Iterable[Mapping[str, Any]],
    query_embeddings: Mapping[str, Any],
    passage_embeddings: Any,
    passage_index: Mapping[str, int],
    assets: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    results: dict[str, dict[str, Any]] = {}
    positive_scores: list[float] = []
    negative_scores: list[float] = []
    margins: list[float] = []
    positive_beats_negative = 0
    for row in rows:
        row_id = str(row.get("curriculum_id") or "")
        positive_uid = str((row.get("positive_table_uids") or [""])[0])
        if not row_id or row_id not in query_embeddings or positive_uid not in passage_index:
            raise ValueError(f"Missing query or positive embedding for {row_id or 'unknown row'}")
        negative_uid = _hard_negative_uid(row, assets)
        query_embedding = query_embeddings[row_id]
        positive_score = float(query_embedding @ passage_embeddings[passage_index[positive_uid]])
        negative_score = float(query_embedding @ passage_embeddings[passage_index[negative_uid]])
        margin = positive_score - negative_score
        positive_scores.append(positive_score)
        negative_scores.append(negative_score)
        margins.append(margin)
        positive_beats_negative += int(margin > 0.0)
        results[row_id] = {
            "positive_table_uid": positive_uid,
            "hard_negative_table_uid": negative_uid,
            "positive_similarity": positive_score,
            "hard_negative_similarity": negative_score,
            "positive_minus_hard_negative": margin,
            "positive_beats_hard_negative": margin > 0.0,
        }
    count = len(results)
    if not count:
        raise ValueError("No rows to diagnose")
    return (
        {
            "questions": count,
            "positive_similarity": _distribution(positive_scores),
            "hard_negative_similarity": _distribution(negative_scores),
            "positive_minus_hard_negative": _distribution(margins),
            "positive_beats_hard_negative_rate": positive_beats_negative / count,
        },
        results,
    )


def _file_identity(reference: str) -> dict[str, Any]:
    path = Path(reference)
    if not path.is_dir():
        return {"reference": reference, "kind": "remote_model_reference"}
    required = ("model.safetensors", "modules.json", "training_metadata.json")
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Fine-tuned model artifact is incomplete: {missing}")
    return {
        "reference": reference,
        "kind": "local_sentence_transformer_artifact",
        "model_safetensors_sha256": sha256_file(path / "model.safetensors"),
        "training_metadata_sha256": sha256_file(path / "training_metadata.json"),
    }


def _rank_and_embed(
    model_reference: str,
    rows_by_split: Mapping[str, list[dict[str, Any]]],
    assets: Mapping[str, Mapping[str, Any]],
    *,
    max_k: int,
    passage_batch_size: int,
    query_batch_size: int,
    max_seq_length: int,
    device: str,
) -> tuple[dict[str, list[str]], dict[str, Any], Any, dict[str, int]]:
    os.environ["WANDB_DISABLED"] = "true"
    os.environ["WANDB_MODE"] = "disabled"
    import numpy as np
    from sentence_transformers import SentenceTransformer

    table_uids = tuple(sorted(assets))
    passage_index = {uid: index for index, uid in enumerate(table_uids)}
    passages = [table_passage(assets[uid]) for uid in table_uids]
    rows = [row for split_rows in rows_by_split.values() for row in split_rows]
    questions = [str(row["question"]) for row in rows]
    model = SentenceTransformer(model_reference, device=device)
    model.max_seq_length = max_seq_length
    passage_embeddings = model.encode(
        passages,
        batch_size=passage_batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    query_array = model.encode(
        questions,
        batch_size=query_batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    query_embeddings = {str(row["curriculum_id"]): query_array[index] for index, row in enumerate(rows)}
    rankings: dict[str, list[str]] = {}
    for start in range(0, len(rows), query_batch_size):
        stop = min(start + query_batch_size, len(rows))
        scores = query_array[start:stop] @ passage_embeddings.T
        for offset, row in enumerate(rows[start:stop]):
            top_indices = np.argpartition(scores[offset], -max_k)[-max_k:]
            top_indices = top_indices[np.argsort(scores[offset][top_indices])[::-1]]
            rankings[str(row["curriculum_id"])] = [table_uids[index] for index in top_indices]
    return rankings, query_embeddings, passage_embeddings, passage_index


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    args = parse_args()
    models = parse_models(args.model)
    selected_splits = normalise_splits(
        args.splits, diagnostic_allow_train=args.diagnostic_allow_train
    )
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite diagnostic artifact: {args.output_dir}")
    if args.passage_batch_size < 1 or args.query_batch_size < 1:
        raise ValueError("Encoding batch sizes must be positive")
    ordered_ks = tuple(sorted({int(k) for k in args.ks if int(k) > 0}))
    if not ordered_ks:
        raise ValueError("At least one positive K is required")
    manifest = validate_inputs(args.curriculum, args.manifest, args.bundle_tables)
    expected_tables_sha256 = str(manifest["input"]["tables_sha256"])
    rows_by_split = load_diagnostic_rows(
        args.curriculum,
        splits=selected_splits,
        expected_tables_sha256=expected_tables_sha256,
        diagnostic_allow_train=args.diagnostic_allow_train,
    )
    assets: dict[str, dict[str, Any]] = {}
    for asset in load_table_assets(args.bundle_tables):
        uid = str(asset.get("internal_table_uid") or "")
        if not uid or uid in assets:
            raise ValueError("Bundle tables contain a missing or duplicate internal_table_uid")
        assets[uid] = asset
    max_k = max(ordered_ks)
    if max_k > len(assets):
        raise ValueError("Requested K exceeds available table assets")
    model_identities = {label: _file_identity(reference) for label, reference in models.items()}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    model_results: dict[str, Any] = {}
    for label, reference in models.items():
        rankings, query_embeddings, passage_embeddings, passage_index = _rank_and_embed(
            reference,
            rows_by_split,
            assets,
            max_k=max_k,
            passage_batch_size=args.passage_batch_size,
            query_batch_size=args.query_batch_size,
            max_seq_length=args.max_seq_length,
            device=args.device,
        )
        split_metrics: dict[str, Any] = {}
        for split, rows in rows_by_split.items():
            retrieval_metrics, ranking_rows = summarise_rankings(rows, rankings, assets, ks=ordered_ks)
            pairwise_metrics, pairwise_rows = summarise_pairwise_scores(
                rows, query_embeddings, passage_embeddings, passage_index, assets
            )
            diagnostic_rows = [{**ranking, **pairwise_rows[ranking["curriculum_id"]]} for ranking in ranking_rows]
            _write_jsonl(args.output_dir / f"{label}_{split}_diagnostic.jsonl", diagnostic_rows)
            split_metrics[split] = {"retrieval": retrieval_metrics, "pairwise": pairwise_metrics}
        model_results[label] = {"identity": model_identities[label], "splits": split_metrics}
    payload = {
        "protocol": DIAGNOSTIC_PROTOCOL,
        "diagnostic_status": "complete_not_for_promotion_or_submission",
        "source_contract": {
            "benchmark_questions_read": False,
            "synthetic_train_access_explicitly_allowed": "train" in selected_splits,
            "purpose": "distinguish_train_fit_from_held_out_generalisation_failure",
            "provenance": SYNTHETIC_PROVENANCE,
        },
        "inputs": {
            "curriculum_sha256": sha256_file(args.curriculum),
            "curriculum_manifest_sha256": sha256_file(args.manifest),
            "source_tables_sha256": expected_tables_sha256,
            "script_sha256": sha256_file(Path(__file__)),
            "asset_count": len(assets),
            "split_counts": {split: len(rows) for split, rows in rows_by_split.items()},
        },
        "configuration": {
            "ks": list(ordered_ks),
            "passage_batch_size": args.passage_batch_size,
            "query_batch_size": args.query_batch_size,
            "max_seq_length": args.max_seq_length,
            "device": args.device,
            "selected_splits": list(selected_splits),
        },
        "models": model_results,
    }
    manifest_path = args.output_dir / "diagnostic_manifest.json"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
