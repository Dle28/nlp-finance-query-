#!/usr/bin/env python3
"""Train and evaluate a provenance-safe Top-K candidate reranker pilot.

This is deliberately *not* a dense-retriever training script.  It learns a
small classifier over the candidates already present in an immutable review
bundle and emits a shadow ranking for audit.  It never mutates the corpus,
lexical index, dense index, review bundle, or final labels.

Supervision policy:

* ``human_verified`` + complete V2 structure: selected candidates are
  positives and the other shown Top-K candidates are trustworthy negatives.
* ``machine_calibrated`` / ``machine_high_confidence``: selected candidates
  are weighted positive-only pseudo-labels.  Non-selected candidates remain
  unknown and are never converted into negatives.

The output is research/shadow-only until enough human-confirmed question
groups and a separate holdout evaluation justify a promotion decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from auto_review_bundle import FEATURE_NAMES, feature_vector, load_jsonl, write_jsonl


MACHINE_PSEUDO_STATUSES = {"machine_calibrated", "machine_high_confidence"}
DEFAULT_KS = (1, 3, 5, 10, 20)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument(
        "--labels", type=Path, default=Path("data/labels/retriever_labels_v2.jsonl")
    )
    parser.add_argument(
        "--ledger", type=Path, default=Path("data/labels/review_ledger_60.jsonl")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/pilot_candidate_reranker.joblib")
    )
    parser.add_argument("--ks", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument(
        "--min-human-questions",
        type=int,
        default=5,
        help="Fail closed below this many complete human question groups.",
    )
    parser.add_argument(
        "--promotion-min-human-questions",
        type=int,
        default=30,
        help="Human-group threshold for a shadow-run recommendation; never auto-promotes.",
    )
    return parser.parse_args()


def by_id(rows: Iterable[dict[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        qid = int(row["id"])
        if qid in output:
            raise ValueError(f"Duplicate question ID {qid} in {name}.")
        output[qid] = row
    return output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def numeric_weight(value: Any, *, qid: int) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Q{qid}: training_weight must be numeric.") from exc
    if not 0.0 < weight <= 1.0:
        raise ValueError(f"Q{qid}: training_weight must be in (0, 1].")
    return weight


def candidate_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    qid = int(item["id"])
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, candidate in enumerate(item.get("candidates") or []):
        uid = str(candidate.get("internal_table_uid") or "")
        if not uid:
            raise ValueError(f"Q{qid}: candidate has no internal_table_uid.")
        if uid in seen:
            raise ValueError(f"Q{qid}: duplicate candidate UID {uid} in immutable bundle.")
        seen.add(uid)
        try:
            rank = int(candidate.get("rank"))
        except (TypeError, ValueError):
            rank = position + 1
        output.append(
            {
                "uid": uid,
                "rank": rank if rank > 0 else position + 1,
                "position": position,
                "features": feature_vector(candidate, FEATURE_NAMES),
            }
        )
    return output


def validate_label_provenance(
    label: dict[str, Any], ledger_row: dict[str, Any] | None
) -> tuple[str, float]:
    """Return trusted source and weight, or reject a label with unclear provenance."""
    qid = int(label["id"])
    if ledger_row is None:
        raise ValueError(f"Q{qid}: final label is absent from the provenance ledger.")

    source = str(label.get("label_source") or "")
    status = str(label.get("annotation_status") or "")
    ledger_status = str(ledger_row.get("annotation_status") or "")
    structure = label.get("structure_validation") or {}

    if source == "human":
        if status != "human_verified" or ledger_status != "human_verified":
            raise ValueError(f"Q{qid}: human label status does not match the ledger.")
        if not bool(label.get("human_verified")):
            raise ValueError(f"Q{qid}: human label lacks human_verified=true.")
        if not bool(structure.get("complete")):
            raise ValueError(f"Q{qid}: human label lacks complete V2 structure validation.")
        if not bool(ledger_row.get("training_eligible")):
            raise ValueError(f"Q{qid}: human label is not training-eligible in the ledger.")
        return source, numeric_weight(label.get("training_weight"), qid=qid)

    if source == "machine":
        if status not in MACHINE_PSEUDO_STATUSES or ledger_status != status:
            raise ValueError(f"Q{qid}: unsupported or mismatched machine pseudo-label status.")
        if bool(label.get("human_verified")):
            raise ValueError(f"Q{qid}: machine pseudo-label cannot claim human verification.")
        if not bool(structure.get("validated")):
            raise ValueError(f"Q{qid}: machine pseudo-label lacks exact V2 row validation.")
        if not bool(ledger_row.get("training_eligible")):
            raise ValueError(f"Q{qid}: machine pseudo-label is not training-eligible in the ledger.")
        return source, numeric_weight(label.get("training_weight"), qid=qid)

    raise ValueError(
        f"Q{qid}: unsupported label_source {source!r}; only complete human or calibrated machine labels are allowed."
    )


def build_dataset(
    items: list[dict[str, Any]],
    labels: dict[int, dict[str, Any]],
    ledger: dict[int, dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[int, dict[str, Any]], dict[str, Any]]:
    """Build trusted training rows and the full candidate lists used for ranking eval."""
    items_by_id = by_id(items, "review bundle")
    unknown_ids = sorted(set(labels) - set(items_by_id))
    if unknown_ids:
        raise ValueError(f"Final labels not present in this bundle: {unknown_ids}")

    x: list[list[float]] = []
    y: list[int] = []
    sample_weights: list[float] = []
    groups: list[int] = []
    evaluation_items: dict[int, dict[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    positive_counts: Counter[str] = Counter()
    negative_counts: Counter[str] = Counter()

    for qid, label in sorted(labels.items()):
        source, weight = validate_label_provenance(label, ledger.get(qid))
        positives = {str(uid) for uid in label.get("positive_table_uids") or [] if str(uid)}
        if not positives:
            raise ValueError(f"Q{qid}: training label has no positive_table_uids.")

        candidates = candidate_rows(items_by_id[qid])
        candidate_uids = {candidate["uid"] for candidate in candidates}
        missing = sorted(positives - candidate_uids)
        if missing:
            raise ValueError(
                f"Q{qid}: selected positive UIDs are absent from this immutable Top-K: {missing}"
            )

        evaluation_items[qid] = {
            "id": qid,
            "source": source,
            "positive_uids": positives,
            "candidates": candidates,
        }
        source_counts[source] += 1

        for candidate in candidates:
            is_positive = candidate["uid"] in positives
            if source == "machine" and not is_positive:
                # Machine pseudo-labels cannot make unknown tables negative.
                continue
            x.append(candidate["features"])
            y.append(1 if is_positive else 0)
            sample_weights.append(weight)
            groups.append(qid)
            if is_positive:
                positive_counts[source] += 1
            else:
                negative_counts[source] += 1

    metadata = {
        "label_question_ids": sorted(labels),
        "source_question_counts": dict(sorted(source_counts.items())),
        "positive_candidate_counts": dict(sorted(positive_counts.items())),
        "negative_candidate_counts": dict(sorted(negative_counts.items())),
        "machine_negative_policy": "unknown_non_selected_candidates_excluded",
        "human_negative_policy": "complete_human_verified_non_selected_top_k_candidates",
    }
    return (
        np.asarray(x, dtype=np.float64),
        np.asarray(y, dtype=np.int64),
        np.asarray(sample_weights, dtype=np.float64),
        np.asarray(groups, dtype=np.int64),
        evaluation_items,
        metadata,
    )


def make_model() -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "logreg",
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    random_state=42,
                ),
            ),
        ]
    )


def fit_model(
    x: np.ndarray, y: np.ndarray, sample_weights: np.ndarray
) -> Pipeline:
    if len(np.unique(y)) < 2:
        raise ValueError("Pilot reranker requires both positive and human-confirmed negative candidates.")
    model = make_model()
    model.fit(x, y, logreg__sample_weight=sample_weights)
    return model


def candidate_order(
    candidates: list[dict[str, Any]], scores: dict[str, float] | None = None
) -> list[str]:
    if scores is None:
        ranked = sorted(candidates, key=lambda row: (int(row["rank"]), int(row["position"])))
    else:
        ranked = sorted(
            candidates,
            key=lambda row: (
                -float(scores[str(row["uid"])]),
                int(row["rank"]),
                int(row["position"]),
            ),
        )
    return [str(row["uid"]) for row in ranked]


def ranking_metrics(
    evaluation_items: dict[int, dict[str, Any]],
    *,
    ks: Iterable[int],
    scores_by_id: dict[int, dict[str, float]] | None = None,
    allowed_sources: set[str] | None = None,
) -> dict[str, Any]:
    selected = [
        item
        for item in evaluation_items.values()
        if allowed_sources is None or str(item["source"]) in allowed_sources
    ]
    reciprocal_ranks: list[float] = []
    per_k: dict[int, dict[str, list[float]]] = {
        int(k): {"recall": [], "hit_rate": []} for k in ks
    }

    for item in selected:
        qid = int(item["id"])
        scores = None if scores_by_id is None else scores_by_id.get(qid)
        if scores_by_id is not None and scores is None:
            raise ValueError(f"Q{qid}: missing out-of-fold reranker scores.")
        ordered = candidate_order(item["candidates"], scores)
        positives = set(item["positive_uids"])
        first_rank = next((rank for rank, uid in enumerate(ordered, start=1) if uid in positives), None)
        reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
        for k, values in per_k.items():
            retrieved = set(ordered[:k])
            recovered = len(retrieved & positives)
            values["recall"].append(recovered / len(positives))
            values["hit_rate"].append(1.0 if recovered else 0.0)

    return {
        "questions": len(selected),
        "question_ids": sorted(int(item["id"]) for item in selected),
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "at_k": {
            str(k): {
                metric: sum(values) / len(values) if values else 0.0
                for metric, values in metrics.items()
            }
            for k, metrics in per_k.items()
        },
    }


def grouped_oof_scores(
    x: np.ndarray,
    y: np.ndarray,
    sample_weights: np.ndarray,
    groups: np.ndarray,
    evaluation_items: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, float]], dict[str, Any]]:
    unique_groups = np.unique(groups)
    if len(unique_groups) < 5:
        raise ValueError("Pilot evaluation needs at least five query-disjoint question groups.")

    folds = min(5, len(unique_groups))
    splitter = GroupKFold(n_splits=folds)
    scores_by_id: dict[int, dict[str, float]] = {}
    fold_summary: list[dict[str, Any]] = []

    for fold, (train_idx, test_idx) in enumerate(splitter.split(x, y, groups), start=1):
        train_groups = sorted(int(group) for group in np.unique(groups[train_idx]))
        test_groups = sorted(int(group) for group in np.unique(groups[test_idx]))
        model = fit_model(x[train_idx], y[train_idx], sample_weights[train_idx])
        fold_summary.append(
            {
                "fold": fold,
                "train_question_ids": train_groups,
                "test_question_ids": test_groups,
                "train_positive_candidates": int(y[train_idx].sum()),
                "train_negative_candidates": int((y[train_idx] == 0).sum()),
            }
        )
        for qid in test_groups:
            candidates = evaluation_items[qid]["candidates"]
            matrix = np.asarray([candidate["features"] for candidate in candidates], dtype=np.float64)
            probabilities = model.predict_proba(matrix)[:, 1]
            scores_by_id[qid] = {
                str(candidate["uid"]): float(probability)
                for candidate, probability in zip(candidates, probabilities)
            }

    expected_ids = set(evaluation_items)
    if set(scores_by_id) != expected_ids:
        raise RuntimeError("Out-of-fold scoring did not cover every labelled question.")
    return scores_by_id, {
        "method": f"GroupKFold(n_splits={folds})",
        "folds": fold_summary,
        "query_disjoint": True,
    }


def shadow_rows(items: list[dict[str, Any]], model: Pipeline) -> list[dict[str, Any]]:
    """Score the immutable bundle without changing it; output is only for audit."""
    output: list[dict[str, Any]] = []
    for item in items:
        candidates = candidate_rows(item)
        if not candidates:
            continue
        matrix = np.asarray([candidate["features"] for candidate in candidates], dtype=np.float64)
        probabilities = model.predict_proba(matrix)[:, 1]
        score_map = {
            str(candidate["uid"]): float(probability)
            for candidate, probability in zip(candidates, probabilities)
        }
        order = candidate_order(candidates, score_map)
        shadow_rank = {uid: rank for rank, uid in enumerate(order, start=1)}
        output.append(
            {
                "id": int(item["id"]),
                "question": item.get("question"),
                "scope": "shadow_top_k_candidate_reranking_only",
                "candidates": [
                    {
                        "internal_table_uid": candidate["uid"],
                        "original_rank": candidate["rank"],
                        "shadow_rank": shadow_rank[candidate["uid"]],
                        "reranker_probability": score_map[candidate["uid"]],
                    }
                    for candidate in candidates
                ],
            }
        )
    return output


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def promotion_recommendation(
    human_questions: int,
    minimum: int,
    baseline_human: dict[str, Any],
    oof_human: dict[str, Any],
) -> dict[str, str]:
    if human_questions < minimum:
        return {
            "decision": "hold",
            "reason": (
                f"Only {human_questions} complete human question groups; need at least {minimum} "
                "before considering even a shadow-run promotion."
            ),
        }
    regressions: list[str] = []
    if oof_human["mrr"] < baseline_human["mrr"]:
        regressions.append("MRR")
    for k in (1, 3, 5):
        key = str(k)
        baseline_at_k = (baseline_human.get("at_k") or {}).get(key)
        oof_at_k = (oof_human.get("at_k") or {}).get(key)
        if baseline_at_k and oof_at_k and oof_at_k["recall"] < baseline_at_k["recall"]:
            regressions.append(f"recall@{k}")
    if regressions:
        return {
            "decision": "hold",
            "reason": (
                "Out-of-fold human-only ranking regressed versus immutable baseline: "
                + ", ".join(regressions)
                + "."
            ),
        }
    return {
        "decision": "shadow_run_candidate",
        "reason": "Sufficient human support and non-regressing OOF human MRR; still requires a separate holdout audit.",
    }


def main() -> None:
    args = parse_args()
    bundle_dir = args.bundle_dir.resolve()
    items_path = bundle_dir / "review_items.jsonl"
    manifest_path = bundle_dir / "manifest.json"
    if not items_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Invalid bundle: review_items.jsonl and manifest.json are required.")
    if not args.labels.is_file() or not args.ledger.is_file():
        raise FileNotFoundError("Both provenance-filtered labels and the full review ledger are required.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("error_count") or 0) != 0:
        raise RuntimeError("Refuse pilot training from a bundle with retrieval errors.")

    items = load_jsonl(items_path)
    labels = by_id(load_jsonl(args.labels), "final labels")
    ledger = by_id(load_jsonl(args.ledger), "review ledger")
    x, y, sample_weights, groups, evaluation_items, data_meta = build_dataset(items, labels, ledger)

    source_counts = data_meta["source_question_counts"]
    human_questions = int(source_counts.get("human", 0))
    if human_questions < args.min_human_questions:
        raise RuntimeError(
            f"Need at least {args.min_human_questions} complete human question groups; got {human_questions}."
        )
    if len(np.unique(groups)) < 5:
        raise RuntimeError("Need at least five total question groups for query-disjoint pilot evaluation.")
    if len(y) == 0 or int(y.sum()) == 0 or int((y == 0).sum()) == 0:
        raise RuntimeError("Pilot requires at least one positive and one human-confirmed negative candidate.")

    oof_scores, cv_meta = grouped_oof_scores(x, y, sample_weights, groups, evaluation_items)
    baseline_all = ranking_metrics(evaluation_items, ks=args.ks)
    oof_all = ranking_metrics(evaluation_items, ks=args.ks, scores_by_id=oof_scores)
    baseline_human = ranking_metrics(
        evaluation_items, ks=args.ks, allowed_sources={"human"}
    )
    oof_human = ranking_metrics(
        evaluation_items, ks=args.ks, scores_by_id=oof_scores, allowed_sources={"human"}
    )

    model = fit_model(x, y, sample_weights)
    promotion = promotion_recommendation(
        human_questions,
        args.promotion_min_human_questions,
        baseline_human,
        oof_human,
    )
    training = {
        "labels_path": str(args.labels.resolve()),
        "ledger_path": str(args.ledger.resolve()),
        "bundle_dir": str(bundle_dir),
        "bundle_questions_sha256": manifest.get("questions_sha256"),
        "input_sha256": {
            "labels": sha256_file(args.labels),
            "ledger": sha256_file(args.ledger),
            "review_items": sha256_file(items_path),
        },
        "question_count": len(evaluation_items),
        "human_question_count": human_questions,
        "machine_pseudo_question_count": int(source_counts.get("machine", 0)),
        "candidate_training_count": int(len(y)),
        "positive_candidate_count": int(y.sum()),
        "human_confirmed_negative_candidate_count": int((y == 0).sum()),
        "feature_names": FEATURE_NAMES,
        **data_meta,
    }
    evaluation = {
        "scope": "only the immutable review bundle Top-K candidates; not full-corpus retrieval recall",
        "baseline": "immutable candidate rank in review_items.jsonl",
        "oof_reranker": "query-disjoint candidate scores from GroupKFold",
        "cross_validation": cv_meta,
        "all_final_labels": {"baseline": baseline_all, "oof_reranker": oof_all},
        "human_verified_only": {"baseline": baseline_human, "oof_reranker": oof_human},
        "machine_label_note": "Machine-calibrated labels are positive-only pseudo-labels; metrics including them are not a human-gold quality claim.",
    }
    payload = {
        "schema_version": 1,
        "kind": "pilot_candidate_reranker",
        "scope": "shadow_top_k_candidate_reranking_only",
        "does_not_modify": ["corpus", "lexical_index", "dense_index", "review_bundle", "final_labels"],
        "model": model,
        "feature_names": FEATURE_NAMES,
        "training": training,
        "evaluation": evaluation,
        "promotion": promotion,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, args.output)
    metadata_path = args.output.with_suffix(args.output.suffix + ".json")
    write_json(
        metadata_path,
        {key: value for key, value in payload.items() if key != "model"},
    )
    shadow_path = args.output.with_suffix(args.output.suffix + ".shadow.jsonl")
    write_jsonl(shadow_path, shadow_rows(items, model))

    print("Pilot candidate reranker saved:", args.output)
    print("Metadata:", metadata_path)
    print("Shadow Top-K ranking (audit only):", shadow_path)
    print("Human-only baseline MRR:", f"{baseline_human['mrr']:.4f}")
    print("Human-only OOF reranker MRR:", f"{oof_human['mrr']:.4f}")
    print("Promotion decision:", promotion["decision"], "—", promotion["reason"])


if __name__ == "__main__":
    main()
