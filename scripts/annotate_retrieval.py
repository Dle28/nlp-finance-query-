#!/usr/bin/env python3
"""Interactively create verified question-to-table labels.

The annotator retrieves candidates, shows provenance and previews, and writes
internal table UIDs selected by the reviewer. It never invents gold labels.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.config import ProjectPaths
from finance_query.pipeline import ViFinQARetrievalPipeline, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data/ViFinQA/questions/questions.jsonl"),
    )
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.yaml"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/labels/retriever_verified.jsonl"),
    )
    parser.add_argument("--start-id", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--no-dense", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=None)
    return parser.parse_args()


def iter_questions(path: Path):
    with path.open(encoding="utf-8-sig") as file:
        for line in file:
            if line.strip():
                yield json.loads(line)


def load_completed(path: Path) -> set[int]:
    if not path.is_file():
        return set()
    completed: set[int] = set()
    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                completed.add(int(json.loads(line)["id"]))
    return completed


def main() -> None:
    args = parse_args()
    paths = ProjectPaths.from_repository(args.repo_root)
    pipeline = ViFinQARetrievalPipeline(
        paths=paths,
        config=load_config(args.config),
        use_dense=not args.no_dense,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    completed = load_completed(args.output)
    reviewed = 0

    with args.output.open("a", encoding="utf-8") as output:
        for row in iter_questions(args.questions):
            question_id = int(row["id"])
            if question_id < args.start_id or question_id in completed:
                continue
            if args.limit is not None and reviewed >= args.limit:
                break

            question = str(row["question"])
            result = pipeline.retrieve(question, question_id)
            candidates = result["retrieved_tables"][: args.top_k]

            print("\n" + "=" * 100)
            print(f"Question {question_id}: {question}")
            print("Plan:")
            print(json.dumps(result["question_plan"], ensure_ascii=False, indent=2))

            for index, candidate in enumerate(candidates, start=1):
                print("\n" + "-" * 100)
                print(
                    f"[{index}] {candidate['document_id']} | "
                    f"year={candidate['report_year']} | scope={candidate['scope']} | "
                    f"lex={candidate['lexical_rank']} | dense={candidate['dense_rank']} | "
                    f"score={candidate['fused_score']:.6f}"
                )
                print(candidate["preview"])

            command = input(
                "\nSelect relevant candidate numbers separated by commas; "
                "s=skip, n=no relevant candidate, q=quit: "
            ).strip().casefold()

            if command == "q":
                break
            if command == "s":
                continue

            selected_uids: list[str] = []
            status = "verified_no_candidate" if command == "n" else "verified"
            if command not in {"n", ""}:
                try:
                    indices = sorted({int(value.strip()) for value in command.split(",")})
                except ValueError:
                    print("Invalid selection; question skipped.")
                    continue
                if any(index < 1 or index > len(candidates) for index in indices):
                    print("Selection outside candidate range; question skipped.")
                    continue
                selected_uids = [candidates[index - 1]["internal_table_uid"] for index in indices]

            annotation = {
                "id": question_id,
                "question": question,
                "positive_table_uids": selected_uids,
                "annotation_status": status,
                "question_plan": result["question_plan"],
            }
            output.write(json.dumps(annotation, ensure_ascii=False) + "\n")
            output.flush()
            reviewed += 1

    print(f"Annotations written to {args.output}")


if __name__ == "__main__":
    main()
