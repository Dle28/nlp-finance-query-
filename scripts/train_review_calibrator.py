#!/usr/bin/env python3
"""Train a small candidate-correctness calibrator from human-reviewed seed cases.

The calibrator learns only from candidate/retrieval/evidence features already
present in the review bundle. It does not replace the verifier or provenance
rules; ``auto_review_bundle.py`` still abstains when evidence gates fail.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from auto_review_bundle import FEATURE_NAMES, feature_vector, load_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--human-labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-questions", type=int, default=8)
    return parser.parse_args()


def human_annotation_map(path: Path) -> dict[int, dict[str, Any]]:
    rows = load_jsonl(path)
    return {int(row["id"]): row for row in rows}


def build_dataset(
    items: list[dict[str, Any]],
    annotations: dict[int, dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    x: list[list[float]] = []
    y: list[int] = []
    groups: list[int] = []

    used_questions: set[int] = set()
    positive_not_in_bundle: list[int] = []
    skipped_status: list[int] = []
    skipped_unvalidated_structure: list[int] = []
    partial_positive_only: list[int] = []
    partial_positive_count = 0

    for item in items:
        qid = int(item["id"])
        annotation = annotations.get(qid)
        if annotation is None:
            continue

        status = str(annotation.get("annotation_status") or "")
        is_complete_or_negative = status in {
            "verified",
            "verified_assisted",
            "human_verified",
            "verified_no_candidate",
        }
        is_partial = status == "human_verified_partial"
        if not is_complete_or_negative and not is_partial:
            skipped_status.append(qid)
            continue

        positives = set(annotation.get("positive_table_uids") or [])
        if positives and not bool(
            (annotation.get("structure_validation") or {}).get("complete")
        ):
            skipped_unvalidated_structure.append(qid)
            continue
        candidates = list(item.get("candidates") or [])
        candidate_uids = {str(c["internal_table_uid"]) for c in candidates}

        if positives and not (positives & candidate_uids):
            # Human says a positive exists but it is not inside this bundle Top-K.
            # Do not teach the candidate classifier that every retrieved table is
            # negative for this question; record it as a retrieval miss instead.
            positive_not_in_bundle.append(qid)
            continue

        if not candidates:
            continue

        used_questions.add(qid)
        if is_partial:
            # A partial EvidenceSet confirms only its selected exact V2 tables.
            # The omitted Top-K candidates are unknown, not negatives.  They
            # must never be used as negative supervision for the calibrator.
            partial_positive_only.append(qid)
            for candidate in candidates:
                candidate_uid = str(candidate["internal_table_uid"])
                if candidate_uid not in positives:
                    continue
                x.append(feature_vector(candidate, FEATURE_NAMES))
                y.append(1)
                groups.append(qid)
                partial_positive_count += 1
            continue

        for candidate in candidates:
            candidate_uid = str(candidate["internal_table_uid"])
            x.append(feature_vector(candidate, FEATURE_NAMES))
            y.append(1 if candidate_uid in positives else 0)
            groups.append(qid)

    metadata = {
        "used_question_ids": sorted(used_questions),
        "positive_not_in_bundle": sorted(set(positive_not_in_bundle)),
        "skipped_status_question_ids": sorted(set(skipped_status)),
        "skipped_unvalidated_structure_question_ids": sorted(
            set(skipped_unvalidated_structure)
        ),
        "partial_positive_only_question_ids": sorted(set(partial_positive_only)),
        "partial_positive_only_candidate_count": partial_positive_count,
    }

    return (
        np.asarray(x, dtype=np.float64),
        np.asarray(y, dtype=np.int64),
        np.asarray(groups, dtype=np.int64),
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


def grouped_cv(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
) -> dict[str, Any]:
    unique_groups = np.unique(groups)
    if len(unique_groups) < 5:
        return {
            "available": False,
            "reason": "Need >=5 human-reviewed question groups for GroupKFold diagnostics.",
        }

    folds = min(5, len(unique_groups))
    splitter = GroupKFold(n_splits=folds)
    probabilities = np.full(len(y), np.nan, dtype=np.float64)

    for train_idx, test_idx in splitter.split(x, y, groups):
        y_train = y[train_idx]
        if len(np.unique(y_train)) < 2:
            continue
        model = make_model()
        model.fit(x[train_idx], y_train)
        probabilities[test_idx] = model.predict_proba(x[test_idx])[:, 1]

    mask = ~np.isnan(probabilities)
    if mask.sum() == 0:
        return {"available": False, "reason": "No valid grouped fold contained both classes."}

    y_eval = y[mask]
    p_eval = probabilities[mask]
    predictions = (p_eval >= 0.5).astype(int)

    result: dict[str, Any] = {
        "available": True,
        "evaluated_candidate_count": int(mask.sum()),
        "classification_report": classification_report(
            y_eval,
            predictions,
            output_dict=True,
            zero_division=0,
        ),
    }
    if len(np.unique(y_eval)) == 2:
        result["roc_auc"] = float(roc_auc_score(y_eval, p_eval))
    else:
        result["roc_auc"] = None
    return result


def main() -> None:
    args = parse_args()
    bundle_dir = args.bundle_dir.resolve()

    manifest_path = bundle_dir / "manifest.json"
    items_path = bundle_dir / "review_items.jsonl"
    if not manifest_path.is_file() or not items_path.is_file():
        raise FileNotFoundError("Invalid bundle: manifest.json/review_items.jsonl missing.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("error_count") or 0) != 0:
        raise RuntimeError("Refuse to train calibrator from a bundle with retrieval errors.")

    items = load_jsonl(items_path)
    annotations = human_annotation_map(args.human_labels)
    x, y, groups, data_meta = build_dataset(items, annotations)

    question_count = len(set(groups.tolist())) if len(groups) else 0
    positive_count = int(y.sum()) if len(y) else 0
    negative_count = int((y == 0).sum()) if len(y) else 0

    print("Human-reviewed questions usable for calibration:", question_count)
    print("Candidate samples:", len(y))
    print("Positive candidates:", positive_count)
    print("Negative candidates:", negative_count)
    print("Positive human cases outside bundle Top-K:", data_meta["positive_not_in_bundle"])
    print(
        "Legacy positives skipped until V2 revalidation:",
        data_meta["skipped_unvalidated_structure_question_ids"],
    )
    print(
        "V2-verified partial reviews used as positive-only evidence:",
        data_meta["partial_positive_only_question_ids"],
    )

    if question_count < args.min_questions:
        raise RuntimeError(
            f"Need at least {args.min_questions} usable human-reviewed questions; got {question_count}."
        )
    if positive_count == 0 or negative_count == 0:
        raise RuntimeError("Calibration requires both positive and negative candidate examples.")

    diagnostics = grouped_cv(x, y, groups)
    print("Grouped diagnostics:")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))

    model = make_model()
    model.fit(x, y)

    payload = {
        "schema_version": 1,
        "model": model,
        "feature_names": FEATURE_NAMES,
        "training": {
            "human_labels": str(args.human_labels),
            "bundle_git_commit": manifest.get("git_commit"),
            "bundle_questions_sha256": manifest.get("questions_sha256"),
            "question_count": question_count,
            "candidate_count": int(len(y)),
            "positive_count": positive_count,
            "negative_count": negative_count,
            **data_meta,
            "grouped_cv": diagnostics,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, args.output)

    metadata_path = args.output.with_suffix(args.output.suffix + ".json")
    metadata_path.write_text(
        json.dumps(payload["training"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Calibrator saved:", args.output)
    print("Metadata saved:", metadata_path)


if __name__ == "__main__":
    main()
