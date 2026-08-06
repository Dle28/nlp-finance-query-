#!/usr/bin/env python3
"""Train the question-family router from observed public ID blocks.

These labels are weak observational labels, not organizer-provided gold labels.
Use the model as a routing baseline and replace the labels with manually reviewed
families before treating the reported score as a scientific result.
"""

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

from finance_query.questions import weak_family_from_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data/ViFinQA/questions/questions.jsonl"),
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


def load_examples(path: Path) -> tuple[list[str], list[str], list[int]]:
    texts: list[str] = []
    labels: list[str] = []
    ids: list[int] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            question_id = int(row["id"])
            label = weak_family_from_id(question_id)
            if label is None:
                continue
            texts.append(str(row["question"]))
            labels.append(label)
            ids.append(question_id)
    if not texts:
        raise ValueError(f"No trainable questions found in {path}")
    return texts, labels, ids


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    texts, labels, ids = load_examples(args.questions)
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
        "label_source": "observed_public_question_id_ranges_weak_supervision",
        "question_count": len(texts),
        "train_count": len(train_idx),
        "test_count": len(test_idx),
        "labels": sorted(set(labels)),
        "classification_report": report,
        "confusion_matrix": matrix,
        "test_question_ids": [ids[index] for index in test_idx],
        "warning": (
            "Metrics measure agreement with weak range labels, not official semantic gold labels."
        ),
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
