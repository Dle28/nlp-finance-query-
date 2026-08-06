from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ModelConfig, ProjectPaths
from .corpus import build_table_assets
from .pipeline import ViFinQARetrievalPipeline, load_config
from .questions import RuleQuestionPlanner, weak_family_from_id
from .retrieval import AssetStore, DenseIndex


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="finance-query")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root. Defaults to the installed source location.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("build-assets", help="Extract immutable table assets from OCR reports.")
    subparsers.add_parser("build-lexical", help="Build SQLite FTS lexical index.")

    dense = subparsers.add_parser("build-dense", help="Build sentence-transformer FAISS index.")
    dense.add_argument("--config", type=Path, default=None)

    retrieve = subparsers.add_parser("retrieve", help="Plan a question and retrieve table candidates.")
    retrieve.add_argument("--question", required=True)
    retrieve.add_argument("--question-id", type=int, default=None)
    retrieve.add_argument("--config", type=Path, default=None)
    retrieve.add_argument("--no-dense", action="store_true")

    answer = subparsers.add_parser(
        "answer-direct",
        help="Run the conservative direct-lookup binding baseline.",
    )
    answer.add_argument("--question", required=True)
    answer.add_argument("--question-id", type=int, default=None)
    answer.add_argument("--config", type=Path, default=None)
    answer.add_argument("--no-dense", action="store_true")
    answer.add_argument("--minimum-binding-score", type=float, default=0.48)

    analyze = subparsers.add_parser("analyze-questions", help="Summarize public question routing.")
    analyze.add_argument("--output", type=Path, default=None)

    return parser.parse_args()


def iter_questions(path: Path):
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if "id" not in record or "question" not in record:
                raise ValueError(f"Invalid question record at line {line_number}")
            yield record


def command_build_assets(paths: ProjectPaths) -> None:
    if not paths.reports_root.is_dir():
        raise FileNotFoundError(f"Reports root not found: {paths.reports_root}")
    result = build_table_assets(paths.reports_root, paths.table_assets_path)
    print(json.dumps({**result, "output": str(paths.table_assets_path)}, indent=2))


def command_build_lexical(paths: ProjectPaths) -> None:
    if not paths.table_assets_path.is_file():
        raise FileNotFoundError(
            f"Table assets not found: {paths.table_assets_path}. Run build-assets first."
        )
    count = AssetStore(paths.lexical_db_path).build(paths.table_assets_path)
    print(json.dumps({"indexed_tables": count, "database": str(paths.lexical_db_path)}, indent=2))


def command_build_dense(paths: ProjectPaths, config: ModelConfig) -> None:
    if not paths.table_assets_path.is_file():
        raise FileNotFoundError(
            f"Table assets not found: {paths.table_assets_path}. Run build-assets first."
        )
    index = DenseIndex(
        index_path=paths.dense_index_path,
        uids_path=paths.dense_uids_path,
        model_name=config.embedding_model,
        device=config.resolved_device(),
        max_sequence_length=config.max_sequence_length,
    )
    count = index.build(paths.table_assets_path, batch_size=config.embedding_batch_size)
    print(
        json.dumps(
            {
                "indexed_tables": count,
                "model": config.embedding_model,
                "device": config.resolved_device(),
                "index": str(paths.dense_index_path),
            },
            indent=2,
        )
    )


def create_pipeline(
    paths: ProjectPaths,
    config: ModelConfig,
    no_dense: bool,
) -> ViFinQARetrievalPipeline:
    if not paths.lexical_db_path.is_file():
        raise FileNotFoundError(
            f"Lexical index not found: {paths.lexical_db_path}. Run build-lexical first."
        )
    return ViFinQARetrievalPipeline(paths, config, use_dense=not no_dense)


def command_retrieve(
    paths: ProjectPaths,
    config: ModelConfig,
    question: str,
    question_id: int | None,
    no_dense: bool,
) -> None:
    pipeline = create_pipeline(paths, config, no_dense)
    print(json.dumps(pipeline.retrieve(question, question_id), ensure_ascii=False, indent=2))


def command_answer_direct(
    paths: ProjectPaths,
    config: ModelConfig,
    question: str,
    question_id: int | None,
    no_dense: bool,
    minimum_binding_score: float,
) -> None:
    pipeline = create_pipeline(paths, config, no_dense)
    result = pipeline.answer_direct(
        question,
        question_id,
        minimum_binding_score=minimum_binding_score,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_analyze_questions(paths: ProjectPaths, output: Path | None) -> None:
    if not paths.questions_path.is_file():
        raise FileNotFoundError(f"Questions file not found: {paths.questions_path}")

    planner = RuleQuestionPlanner(paths.dataset_root / "code_stock.csv")
    family_counts: dict[str, int] = {}
    weak_counts: dict[str, int] = {}
    rows: list[dict] = []

    for record in iter_questions(paths.questions_path):
        question_id = int(record["id"])
        plan = planner.plan(str(record["question"]), question_id)
        family_counts[plan.family] = family_counts.get(plan.family, 0) + 1
        weak = weak_family_from_id(question_id) or "unknown"
        weak_counts[weak] = weak_counts.get(weak, 0) + 1
        rows.append(
            {
                "id": question_id,
                "question": record["question"],
                "weak_range_family": weak,
                "rule_family": plan.family,
                "rule_confidence": plan.family_confidence,
                "tickers": plan.tickers,
                "years": plan.years,
                "scope": plan.scope,
                "unit": plan.requested_unit,
                "warnings": plan.warnings,
            }
        )

    payload = {
        "question_count": len(rows),
        "observed_range_family_counts": weak_counts,
        "rule_router_counts": family_counts,
        "note": "Range families are weak observational labels, not organizer gold labels.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    paths = ProjectPaths.from_repository(args.repo_root)

    if args.command == "build-assets":
        command_build_assets(paths)
    elif args.command == "build-lexical":
        command_build_lexical(paths)
    elif args.command == "build-dense":
        command_build_dense(paths, load_config(args.config))
    elif args.command == "retrieve":
        command_retrieve(
            paths,
            load_config(args.config),
            args.question,
            args.question_id,
            args.no_dense,
        )
    elif args.command == "answer-direct":
        command_answer_direct(
            paths,
            load_config(args.config),
            args.question,
            args.question_id,
            args.no_dense,
            args.minimum_binding_score,
        )
    elif args.command == "analyze-questions":
        command_analyze_questions(paths, args.output)
    else:
        raise RuntimeError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
