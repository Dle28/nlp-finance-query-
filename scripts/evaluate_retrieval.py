#!/usr/bin/env python3
"""Evaluate table retrieval against verified internal table UIDs.

Expected JSONL rows:
{"id": 1, "question": "...", "positive_table_uids": ["uid1", "uid2"]}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.config import ProjectPaths
from finance_query.pipeline import ViFinQARetrievalPipeline, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.yaml"))
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--ks", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument("--no-dense", action="store_true")
    return parser.parse_args()


def f2(precision: float, recall: float) -> float:
    if precision == 0.0 and recall == 0.0:
        return 0.0
    beta2 = 4.0
    return (1.0 + beta2) * precision * recall / (beta2 * precision + recall)


def main() -> None:
    args = parse_args()
    paths = ProjectPaths.from_repository(args.repo_root)
    pipeline = ViFinQARetrievalPipeline(
        paths=paths,
        config=load_config(args.config),
        use_dense=not args.no_dense,
    )

    rows = []
    with args.labels.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            positives = set(str(uid) for uid in row.get("positive_table_uids") or [])
            if not row.get("question") or not positives:
                raise ValueError(
                    f"Line {line_number} must contain question and positive_table_uids"
                )
            rows.append(row)

    per_k = {k: {"precision": [], "recall": [], "f2": []} for k in args.ks}
    reciprocal_ranks: list[float] = []

    for row in rows:
        result = pipeline.retrieve(str(row["question"]), row.get("id"))
        ranked = [candidate["internal_table_uid"] for candidate in result["retrieved_tables"]]
        positives = set(str(uid) for uid in row["positive_table_uids"])

        first_rank = next((index for index, uid in enumerate(ranked, start=1) if uid in positives), None)
        reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)

        for k in args.ks:
            retrieved = ranked[:k]
            true_positives = len(set(retrieved) & positives)
            precision = true_positives / len(retrieved) if retrieved else 0.0
            recall = true_positives / len(positives)
            per_k[k]["precision"].append(precision)
            per_k[k]["recall"].append(recall)
            per_k[k]["f2"].append(f2(precision, recall))

    output = {
        "questions": len(rows),
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "at_k": {
            str(k): {
                metric: sum(values) / len(values) if values else 0.0
                for metric, values in metrics.items()
            }
            for k, metrics in per_k.items()
        },
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
