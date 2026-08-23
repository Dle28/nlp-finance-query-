#!/usr/bin/env python3
"""Ablate hierarchy RRF on immutable issuer-held-out retrieval rankings.

The script reorders only each saved top-K pool. It does not rebuild an index,
read benchmark questions, train a model, or promote a retriever.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from finance_query.hierarchical_retrieval import hierarchy_candidate_score
from finance_query.synthetic_curriculum import (
    SYNTHETIC_CURRICULUM_PROTOCOL,
    SYNTHETIC_PROVENANCE,
)


PROTOCOL = "vifinqa_hierarchy_retrieval_ablation_v1"
EVALUATION_PROTOCOL = "synthetic_issuer_heldout_retriever_evaluation_v1"
HELD_OUT_SPLITS = ("validation", "test")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def rerank_pool(
    question: str,
    ranked_uids: list[str],
    assets: Mapping[str, Mapping[str, Any]],
    *,
    rrf_k: int,
) -> tuple[list[str], dict[str, float]]:
    """Fuse the immutable base rank with one hierarchy rank channel."""

    if rrf_k < 1 or len(ranked_uids) != len(set(ranked_uids)):
        raise ValueError("rrf_k must be positive and ranking UIDs unique")
    missing = [uid for uid in ranked_uids if uid not in assets]
    if missing:
        raise ValueError(f"ranking references missing assets: {missing[:3]}")
    hierarchy_scores = {
        uid: hierarchy_candidate_score(question, assets[uid]) for uid in ranked_uids
    }
    hierarchy_order = sorted(
        (uid for uid in ranked_uids if hierarchy_scores[uid] > 0),
        key=lambda uid: (-hierarchy_scores[uid], ranked_uids.index(uid), uid),
    )
    hierarchy_rank = {uid: rank for rank, uid in enumerate(hierarchy_order, start=1)}
    fused = {
        uid: 1.0 / (rrf_k + base_rank)
        + (0.0 if uid not in hierarchy_rank else 1.0 / (rrf_k + hierarchy_rank[uid]))
        for base_rank, uid in enumerate(ranked_uids, start=1)
    }
    reranked = sorted(
        ranked_uids,
        key=lambda uid: (-fused[uid], ranked_uids.index(uid), uid),
    )
    return reranked, hierarchy_scores


def explicit_context_pool(
    row: Mapping[str, Any],
    ranked_uids: list[str],
    assets: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Apply only deterministic issuer/year/scope fields from the held-out row."""

    target = row.get("planner_target") or {}
    ticker = str(target.get("ticker") or "")
    scope = str(target.get("scope") or "")
    year = target.get("report_year")
    return [
        uid
        for uid in ranked_uids
        if (not ticker or str(assets[uid].get("ticker") or "") == ticker)
        and (not scope or str(assets[uid].get("scope") or "") == scope)
        and (not isinstance(year, int) or assets[uid].get("report_year") == year)
    ]


def _error_bucket(
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


def summarise(
    rows: Iterable[Mapping[str, Any]],
    rankings: Mapping[str, list[str]],
    assets: Mapping[str, Mapping[str, Any]],
    *,
    ks: Iterable[int],
) -> dict[str, Any]:
    ordered_ks = tuple(sorted({int(k) for k in ks if int(k) > 0}))
    if not ordered_ks:
        raise ValueError("at least one positive K is required")
    hit_counts = Counter()
    ndcg_sums = Counter()
    reciprocal_sum = 0.0
    errors = Counter()
    count = 0
    for row in rows:
        row_id = str(row.get("curriculum_id") or "")
        positives = {str(uid) for uid in row.get("positive_table_uids") or []}
        ranking = rankings.get(row_id)
        if not row_id or not positives or ranking is None:
            raise ValueError(f"incomplete held-out ranking for {row_id or 'unknown row'}")
        first = next((rank for rank, uid in enumerate(ranking, start=1) if uid in positives), None)
        reciprocal_sum += 0.0 if first is None else 1.0 / first
        for k in ordered_ks:
            hit_counts[k] += int(first is not None and first <= k)
            dcg = sum(
                1.0 / math.log2(rank + 1)
                for rank, uid in enumerate(ranking[:k], start=1)
                if uid in positives
            )
            ideal_hits = min(len(positives), k)
            ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
            ndcg_sums[k] += dcg / ideal if ideal else 0.0
        if ranking:
            errors[_error_bucket(next(iter(positives)), ranking[0], assets)] += 1
        else:
            errors["no_candidate"] += 1
        count += 1
    if count == 0:
        raise ValueError("no held-out rows")
    return {
        "questions": count,
        "mrr": reciprocal_sum / count,
        "recall_at_k": {str(k): hit_counts[k] / count for k in ordered_ks},
        "ndcg_at_k": {str(k): ndcg_sums[k] / count for k in ordered_ks},
        "top1_error_counts": dict(sorted(errors.items())),
        "top1_error_rates": {key: value / count for key, value in sorted(errors.items())},
    }


def assess(
    baseline: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Mapping[str, Any]],
    *,
    required_k: int,
) -> dict[str, Any]:
    split_results: dict[str, Any] = {}
    no_quality_regression = True
    no_context_regression = True
    strict_improvement = False
    for split in HELD_OUT_SPLITS:
        base, cand = baseline[split], candidate[split]
        base_recall = float(base["recall_at_k"][str(required_k)])
        cand_recall = float(cand["recall_at_k"][str(required_k)])
        base_ndcg = float(base["ndcg_at_k"][str(required_k)])
        cand_ndcg = float(cand["ndcg_at_k"][str(required_k)])
        recall_ok, ndcg_ok = cand_recall >= base_recall, cand_ndcg >= base_ndcg
        context = {}
        for bucket in ("wrong_year", "wrong_scope"):
            base_rate = float((base.get("top1_error_rates") or {}).get(bucket, 0.0))
            candidate_rate = float((cand.get("top1_error_rates") or {}).get(bucket, 0.0))
            context[bucket] = {"baseline": base_rate, "candidate": candidate_rate}
            no_context_regression &= candidate_rate <= base_rate
        no_quality_regression &= recall_ok and ndcg_ok
        strict_improvement |= cand_recall > base_recall or cand_ndcg > base_ndcg
        split_results[split] = {
            "baseline_recall": base_recall,
            "candidate_recall": cand_recall,
            "baseline_ndcg": base_ndcg,
            "candidate_ndcg": cand_ndcg,
            "recall_not_worse": recall_ok,
            "ndcg_not_worse": ndcg_ok,
            "context_error_rates": context,
        }
    eligible = no_quality_regression and no_context_regression and strict_improvement
    return {
        "required_k": required_k,
        "split_results": split_results,
        "no_quality_regression": no_quality_regression,
        "wrong_year_scope_not_worse": no_context_regression,
        "strict_improvement_on_at_least_one_split": strict_improvement,
        "status": (
            "eligible_for_followup_experiment_not_promoted"
            if eligible
            else "rejected_or_inconclusive_not_promoted"
        ),
    }


def run(
    *,
    curriculum: Path,
    curriculum_manifest: Path,
    table_assets: Path,
    evaluation_manifest: Path,
    rankings_dir: Path,
    labels: Iterable[str],
    output_dir: Path,
    ks: Iterable[int],
    required_k: int,
    rrf_k: int,
    enforce_explicit_context: bool = False,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite hierarchy ablation: {output_dir}")
    manifest = json.loads(curriculum_manifest.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_manifest.read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") != SYNTHETIC_CURRICULUM_PROTOCOL
        or manifest.get("provenance") != SYNTHETIC_PROVENANCE
        or (manifest.get("output") or {}).get("examples_sha256") != sha256_file(curriculum)
        or (manifest.get("input") or {}).get("tables_sha256") != sha256_file(table_assets)
    ):
        raise ValueError("synthetic curriculum lineage is invalid")
    source_contract = evaluation.get("source_contract") or {}
    if (
        evaluation.get("protocol") != EVALUATION_PROTOCOL
        or source_contract.get("benchmark_questions_read") is not False
        or source_contract.get("issuer_held_out_splits_only") is not True
        or (evaluation.get("inputs") or {}).get("curriculum_sha256") != sha256_file(curriculum)
        or (evaluation.get("inputs") or {}).get("source_tables_sha256") != sha256_file(table_assets)
    ):
        raise ValueError("base evaluation is not a matching issuer-held-out artifact")

    held_out: dict[str, list[dict[str, Any]]] = {split: [] for split in HELD_OUT_SPLITS}
    for row in load_jsonl(curriculum):
        split = str(row.get("split") or "")
        if split in held_out:
            if row.get("annotation_status") != SYNTHETIC_PROVENANCE:
                raise ValueError("held-out row is not execution verified")
            held_out[split].append(row)
    if any(not rows for rows in held_out.values()):
        raise ValueError("both issuer-held-out splits are required")

    ranking_inputs: dict[tuple[str, str], Path] = {}
    ranking_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    required_uids: set[str] = set()
    for label in labels:
        if label not in (evaluation.get("models") or {}):
            raise ValueError(f"label is absent from base evaluation: {label}")
        for split in HELD_OUT_SPLITS:
            path = rankings_dir / f"{label}_{split}_rankings.jsonl"
            values = load_jsonl(path)
            ranking_inputs[(label, split)] = path
            ranking_rows[(label, split)] = values
            required_uids.update(
                str(uid) for value in values for uid in value.get("ranked_table_uids") or []
            )
            required_uids.update(
                str(uid) for value in values for uid in value.get("positive_table_uids") or []
            )
    assets: dict[str, Mapping[str, Any]] = {}
    for asset in load_jsonl(table_assets):
        uid = str(asset.get("internal_table_uid") or "")
        if uid in required_uids:
            assets[uid] = asset
    if set(assets) != required_uids:
        raise ValueError("table assets do not cover all ablation ranking UIDs")

    output_dir.mkdir(parents=True, exist_ok=False)
    results: dict[str, Any] = {}
    output_records: dict[str, Any] = {}
    for label in labels:
        baseline_metrics: dict[str, Any] = {}
        hierarchy_metrics: dict[str, Any] = {}
        for split in HELD_OUT_SPLITS:
            base_rows = ranking_rows[(label, split)]
            base_rankings = {
                str(row["curriculum_id"]): [str(uid) for uid in row["ranked_table_uids"]]
                for row in base_rows
            }
            questions = {str(row["curriculum_id"]): row for row in held_out[split]}
            if set(base_rankings) != set(questions):
                raise ValueError(f"{label}/{split} rankings do not cover held-out rows")
            candidate_rankings: dict[str, list[str]] = {}
            detail_rows: list[dict[str, Any]] = []
            for row_id, base_ranking in base_rankings.items():
                if enforce_explicit_context:
                    base_ranking = explicit_context_pool(
                        questions[row_id], base_ranking, assets
                    )
                    if not base_ranking:
                        # Preserve an explicit miss; never fall back to a
                        # context-conflicting candidate just to fill Top-K.
                        base_rankings[row_id] = []
                        candidate_rankings[row_id] = []
                        detail_rows.append(
                            {
                                "curriculum_id": row_id,
                                "positive_table_uids": questions[row_id]["positive_table_uids"],
                                "baseline_ranked_table_uids": [],
                                "hierarchy_ranked_table_uids": [],
                                "hierarchy_scores": {},
                            }
                        )
                        continue
                    base_rankings[row_id] = base_ranking
                reranked, scores = rerank_pool(
                    str(questions[row_id]["question"]),
                    base_ranking,
                    assets,
                    rrf_k=rrf_k,
                )
                candidate_rankings[row_id] = reranked
                detail_rows.append(
                    {
                        "curriculum_id": row_id,
                        "positive_table_uids": questions[row_id]["positive_table_uids"],
                        "baseline_ranked_table_uids": base_ranking,
                        "hierarchy_ranked_table_uids": reranked,
                        "hierarchy_scores": scores,
                    }
                )
            baseline_metrics[split] = summarise(
                held_out[split], base_rankings, assets, ks=ks
            )
            hierarchy_metrics[split] = summarise(
                held_out[split], candidate_rankings, assets, ks=ks
            )
            detail_path = output_dir / f"{label}_{split}_hierarchy_ablation.jsonl"
            _write_jsonl(detail_path, detail_rows)
            output_records[f"{label}_{split}"] = {
                "path": str(detail_path),
                "sha256": sha256_file(detail_path),
            }
        results[label] = {
            "baseline": baseline_metrics,
            "hierarchy": hierarchy_metrics,
            "assessment": assess(
                baseline_metrics,
                hierarchy_metrics,
                required_k=required_k,
            ),
        }

    payload = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "issuer_heldout_ablation_complete_not_promoted",
        "inputs": {
            "curriculum": {"path": str(curriculum), "sha256": sha256_file(curriculum)},
            "curriculum_manifest": {
                "path": str(curriculum_manifest),
                "sha256": sha256_file(curriculum_manifest),
            },
            "table_assets": {"path": str(table_assets), "sha256": sha256_file(table_assets)},
            "evaluation_manifest": {
                "path": str(evaluation_manifest),
                "sha256": sha256_file(evaluation_manifest),
            },
            "rankings": {
                f"{label}_{split}": {
                    "path": str(path),
                    "sha256": sha256_file(path),
                }
                for (label, split), path in sorted(ranking_inputs.items())
            },
        },
        "configuration": {
            "labels": list(labels),
            "splits": list(HELD_OUT_SPLITS),
            "ks": sorted({int(k) for k in ks}),
            "required_k": required_k,
            "rrf_k": rrf_k,
            "candidate_pool": "immutable_saved_top_k_only",
            "explicit_context_filter": enforce_explicit_context,
        },
        "results": results,
        "outputs": output_records,
        "source_contract": {
            "benchmark_questions_read": False,
            "issuer_held_out_splits_only": True,
            "candidate_only": True,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    manifest_output = output_dir / "hierarchy_retrieval_ablation_v1.manifest.json"
    manifest_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**payload, "manifest_path": str(manifest_output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum", type=Path, required=True)
    parser.add_argument("--curriculum-manifest", type=Path, required=True)
    parser.add_argument("--table-assets", type=Path, required=True)
    parser.add_argument("--evaluation-manifest", type=Path, required=True)
    parser.add_argument("--rankings-dir", type=Path, required=True)
    parser.add_argument("--label", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ks", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument("--required-k", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--enforce-explicit-context", action="store_true")
    args = parser.parse_args()
    result = run(
        curriculum=args.curriculum.resolve(),
        curriculum_manifest=args.curriculum_manifest.resolve(),
        table_assets=args.table_assets.resolve(),
        evaluation_manifest=args.evaluation_manifest.resolve(),
        rankings_dir=args.rankings_dir.resolve(),
        labels=args.label,
        output_dir=args.output_dir.resolve(),
        ks=args.ks,
        required_k=args.required_k,
        rrf_k=args.rrf_k,
        enforce_explicit_context=args.enforce_explicit_context,
    )
    print(json.dumps({"status": result["status"], "results": result["results"]}, indent=2))


if __name__ == "__main__":
    main()
