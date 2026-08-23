#!/usr/bin/env python3
"""Train the question-family router from reviewed semantic labels only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from finance_query.router_model import REVIEWED_ROUTER_LABEL_SOURCE


VALID_FAMILIES = {
    "direct_lookup",
    "conditional_analytical",
    "temporal_change",
    "ratio_or_derived",
    "cross_entity_comparison",
    "multi_entity_or_period_aggregation",
    "unknown",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data/ViFinQA/questions/questions.jsonl"),
    )
    parser.add_argument(
        "--labels",
        type=Path,
        required=True,
        help=(
            "JSONL reviewed semantic-family labels. Each row must contain id or "
            "question_id, family, and family_provenance=human_verified."
        ),
    )
    parser.add_argument(
        "--model",
        default="intfloat/multilingual-e5-small",
        help="Sentence-transformer checkpoint used to encode questions.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/question_router"))
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default=None)
    return parser.parse_args()


def load_examples(
    questions_path: Path,
    labels_path: Path,
) -> tuple[list[str], list[str], list[int]]:
    questions: dict[int, str] = {}
    with questions_path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            question_id = int(row["id"])
            if question_id in questions:
                raise ValueError(f"Duplicate question id {question_id} at line {line_number}")
            questions[question_id] = str(row["question"])

    texts: list[str] = []
    labels: list[str] = []
    ids: list[int] = []
    seen_ids: set[int] = set()
    with labels_path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            raw_id = row.get("id", row.get("question_id"))
            if raw_id is None:
                raise ValueError(f"Label line {line_number} lacks id/question_id")
            question_id = int(raw_id)
            if question_id in seen_ids:
                raise ValueError(f"Duplicate reviewed label for question id {question_id}")
            seen_ids.add(question_id)
            label = str(row.get("family") or "")
            if label not in VALID_FAMILIES:
                raise ValueError(f"Label line {line_number} has invalid family: {label!r}")
            if row.get("family_provenance") != "human_verified":
                raise ValueError(
                    f"Label line {line_number} must be human_verified semantic supervision"
                )
            question = questions.get(question_id)
            if question is None:
                raise ValueError(f"Label line {line_number} references unknown question id {question_id}")
            texts.append(question)
            labels.append(label)
            ids.append(question_id)
    if not texts:
        raise ValueError(f"No reviewed semantic labels found in {labels_path}")
    return texts, labels, ids


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    texts, labels, ids = load_examples(args.questions, args.labels)
    encoder = SentenceTransformer(args.model, device=args.device)
    encoded_texts = [
        f"query: {text}" if "e5" in args.model.casefold() else text
        for text in texts
    ]
    embeddings = encoder.encode(
        encoded_texts,
        batch_size=args.batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).astype("float32")

    indices = np.arange(len(texts))
    train_idx, test_idx = train_test_split(
        indices,
        test_size=args.test_size,
        random_state=args.seed,
        stratify=labels,
    )

    classifier = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=3000,
                    class_weight="balanced",
                    random_state=args.seed,
                ),
            ),
        ]
    )
    classifier.fit(embeddings[train_idx], np.asarray(labels)[train_idx])
    predictions = classifier.predict(embeddings[test_idx])

    report = classification_report(
        np.asarray(labels)[test_idx],
        predictions,
        output_dict=True,
        zero_division=0,
    )
    matrix = confusion_matrix(
        np.asarray(labels)[test_idx],
        predictions,
        labels=sorted(set(labels)),
    ).tolist()

    joblib.dump(classifier, args.output_dir / "classifier.joblib")
    metadata = {
        "encoder_model": args.model,
        "label_source": REVIEWED_ROUTER_LABEL_SOURCE,
        "labels_path": str(args.labels),
        "question_count": len(texts),
        "train_count": len(train_idx),
        "test_count": len(test_idx),
        "labels": sorted(set(labels)),
        "classification_report": report,
        "confusion_matrix": matrix,
        "test_question_ids": [ids[index] for index in test_idx],
        "warning": "Metrics measure held-out agreement with reviewed semantic-family labels.",
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
