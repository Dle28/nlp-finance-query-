#!/usr/bin/env python3
"""Train the native candidate-validity model used by the primary submission.

Input JSONL rows must contain an explicit ``label`` (0/1) and either a
``features`` object or an ``item``/``candidate`` pair.  The script refuses to
invent labels from model scores or from an answer produced by the submission
builder.  A trained model only ranks candidates; V2 coordinate replay remains
the value/provenance gate.

Example row using a precomputed vector:

    {"question_id": 7, "label": 1, "features": {"rank_reciprocal": 1.0, ...}}

Example row using the current bundle shape:

    {"question_id": 7, "label": 1, "item": {...}, "candidate": {...}, "table": {...}}
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from finance_query.e2e.core.candidate_validity import (
    CANDIDATE_VALIDITY_PROTOCOL,
    FEATURE_NAMES,
    CandidateValidityModel,
    candidate_feature_map,
    save_candidate_validity_model,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=500)
    parser.add_argument("--learning-rate", type=float, default=0.12)
    parser.add_argument("--l2", type=float, default=0.01)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Training row at {path}:{line_number} must be an object")
            row["_line_number"] = line_number
            rows.append(row)
    return rows


def build_dataset(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, float]], list[int], list[float], Counter[str]]:
    features: list[dict[str, float]] = []
    labels: list[int] = []
    weights: list[float] = []
    groups: Counter[str] = Counter()
    for row in rows:
        line_number = row.get("_line_number")
        if "label" not in row:
            raise ValueError(f"training row {line_number} has no explicit label")
        try:
            label = int(row["label"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"training row {line_number} label must be 0 or 1") from exc
        if label not in (0, 1):
            raise ValueError(f"training row {line_number} label must be 0 or 1")

        raw_features = row.get("features")
        if isinstance(raw_features, dict):
            feature_map = {str(key): float(value) for key, value in raw_features.items()}
        else:
            item = row.get("item")
            candidate = row.get("candidate")
            if not isinstance(item, dict) or not isinstance(candidate, dict):
                raise ValueError(
                    f"training row {line_number} needs a features object or item/candidate objects"
                )
            table = row.get("table")
            if table is not None and not isinstance(table, dict):
                raise ValueError(f"training row {line_number} table must be an object")
            feature_map = candidate_feature_map(item, candidate, table)
        missing = [name for name in FEATURE_NAMES if name not in feature_map]
        if missing:
            raise ValueError(
                f"training row {line_number} is missing features {missing}; use the current FEATURE_NAMES"
            )
        features.append({name: float(feature_map[name]) for name in FEATURE_NAMES})
        labels.append(label)
        try:
            weight = float(row.get("sample_weight", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"training row {line_number} sample_weight must be numeric") from exc
        if weight <= 0:
            raise ValueError(f"training row {line_number} sample_weight must be positive")
        weights.append(weight)
        groups[str(row.get("question_id", row.get("group_id", "ungrouped")))] += 1
    return features, labels, weights, groups


def main() -> None:
    args = parse_args()
    input_path = args.training_jsonl.expanduser().resolve()
    rows = read_rows(input_path)
    features, labels, weights, groups = build_dataset(rows)
    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if positive_count == 0 or negative_count == 0:
        raise RuntimeError("candidate-validity training requires both positive and negative labels")
    model = CandidateValidityModel.fit(
        features,
        labels,
        feature_names=FEATURE_NAMES,
        sample_weights=weights,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        l2=args.l2,
    )
    training = {
        "protocol": CANDIDATE_VALIDITY_PROTOCOL,
        "input_path": str(input_path),
        "row_count": len(rows),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "question_group_count": len(groups),
        "question_groups": sorted(groups),
        "label_policy": "explicit_labels_only; no_prediction_or_answer_pseudo_labels",
        "top_k_policy": {"direct_lookup": 10, "complex_or_multi_operand": 20},
        "training_parameters": {
            "epochs": args.epochs,
            "learning_rate": args.learning_rate,
            "l2": args.l2,
        },
    }
    save_candidate_validity_model(args.output.expanduser().resolve(), model, training=training)
    print(json.dumps({"output": str(args.output.expanduser().resolve()), **training}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
