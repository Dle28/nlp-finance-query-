from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .config import ModelConfig, ProjectPaths
from .corpus import build_table_assets
from .grounded_e2e import load_inputs as load_grounded_e2e_inputs
from .grounded_e2e import run_grounded_e2e
from .pipeline import ViFinQARetrievalPipeline, load_config
from .questions import RuleQuestionPlanner
from .retrieval import AssetStore, DenseIndex
from .semantic_approvals import build_semantic_review_queue


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
    dense.add_argument("--encode-chunk-size", type=int, default=4096)
    dense.add_argument("--max-wall-seconds", type=float, default=None)
    dense.add_argument(
        "--progress-path",
        type=Path,
        default=None,
        help="Optional atomic JSON progress receipt, updated after each outer chunk.",
    )

    retrieve = subparsers.add_parser("retrieve", help="Plan a question and retrieve table candidates.")
    retrieve.add_argument("--question", required=True)
    retrieve.add_argument("--question-id", type=int, default=None)
    retrieve.add_argument("--config", type=Path, default=None)
    retrieve.add_argument("--no-dense", action="store_true")

    answer = subparsers.add_parser(
        "legacy-binding-probe",
        aliases=["answer-direct"],
        help="Inspect legacy binding candidates; this command never returns an answer.",
    )
    answer.add_argument("--question", required=True)
    answer.add_argument("--question-id", type=int, default=None)
    answer.add_argument("--config", type=Path, default=None)
    answer.add_argument("--no-dense", action="store_true")
    answer.add_argument("--minimum-binding-score", type=float, default=0.48)

    analyze = subparsers.add_parser("analyze-questions", help="Summarize public question routing.")
    analyze.add_argument("--output", type=Path, default=None)

    grounded_e2e = subparsers.add_parser(
        "run-grounded-e2e",
        help="Replay hash-bound route/period packets through exact cells and Decimal execution.",
    )
    grounded_e2e.add_argument("--config", type=Path, required=True)
    grounded_e2e.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New output directory only; upstream artifacts are read-only.",
    )

    semantic_review = subparsers.add_parser(
        "build-semantic-review-queue",
        help="Freeze human-review packets for exact-cell variable and issuer semantics.",
    )
    semantic_review.add_argument("--bindings", type=Path, required=True)
    semantic_review.add_argument("--bindings-manifest", type=Path, required=True)
    semantic_review.add_argument("--structured-tables", type=Path, required=True)
    semantic_review.add_argument("--evidence-context", type=Path, required=True)
    semantic_review.add_argument("--evidence-context-manifest", type=Path, required=True)
    semantic_review.add_argument("--output-dir", type=Path, required=True)

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


def command_build_dense(
    paths: ProjectPaths,
    config: ModelConfig,
    *,
    encode_chunk_size: int = 4096,
    max_wall_seconds: float | None = None,
    progress_path: Path | None = None,
) -> None:
    if not paths.table_assets_path.is_file():
        raise FileNotFoundError(
            f"Table assets not found: {paths.table_assets_path}. Run build-assets first."
        )
    dense_index_path, dense_uids_path = config.resolved_dense_paths(paths)
    index = DenseIndex(
        index_path=dense_index_path,
        uids_path=dense_uids_path,
        model_name=config.embedding_model,
        device=config.resolved_device(),
        max_sequence_length=config.max_sequence_length,
    )
    started_at = time.monotonic()

    def report_progress(indexed_tables: int, elapsed_seconds: float) -> None:
        payload = {
            "indexed_tables": indexed_tables,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "batch_size": config.embedding_batch_size,
            "encode_chunk_size": encode_chunk_size,
            "max_wall_seconds": max_wall_seconds,
        }
        print(json.dumps({"dense_progress": payload}), flush=True)
        if progress_path is not None:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = progress_path.with_suffix(progress_path.suffix + ".tmp")
            temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary_path.replace(progress_path)

    count = index.build(
        paths.table_assets_path,
        batch_size=config.embedding_batch_size,
        encode_chunk_size=encode_chunk_size,
        max_wall_seconds=max_wall_seconds,
        progress_callback=report_progress,
    )
    print(
        json.dumps(
            {
                "indexed_tables": count,
                "model": config.embedding_model,
                "device": config.resolved_device(),
                "index": str(dense_index_path),
                "uids": str(dense_uids_path),
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
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


def command_legacy_binding_probe(
    paths: ProjectPaths,
    config: ModelConfig,
    question: str,
    question_id: int | None,
    no_dense: bool,
    minimum_binding_score: float,
) -> None:
    pipeline = create_pipeline(paths, config, no_dense)
    result = pipeline.legacy_binding_probe(
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
    rows: list[dict] = []

    for record in iter_questions(paths.questions_path):
        question_id = int(record["id"])
        plan = planner.plan(str(record["question"]), question_id)
        family_counts[plan.family] = family_counts.get(plan.family, 0) + 1
        rows.append(
            {
                "id": question_id,
                "question": record["question"],
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
        "rule_router_counts": family_counts,
        "note": "Question IDs are artifact keys only and do not influence semantic planning.",
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
        command_build_dense(
            paths,
            load_config(args.config),
            encode_chunk_size=args.encode_chunk_size,
            max_wall_seconds=args.max_wall_seconds,
            progress_path=args.progress_path,
        )
    elif args.command == "retrieve":
        command_retrieve(
            paths,
            load_config(args.config),
            args.question,
            args.question_id,
            args.no_dense,
        )
    elif args.command in {"legacy-binding-probe", "answer-direct"}:
        command_legacy_binding_probe(
            paths,
            load_config(args.config),
            args.question,
            args.question_id,
            args.no_dense,
            args.minimum_binding_score,
        )
    elif args.command == "analyze-questions":
        command_analyze_questions(paths, args.output)
    elif args.command == "run-grounded-e2e":
        result = run_grounded_e2e(
            load_grounded_e2e_inputs(args.config),
            output_dir=args.output_dir,
        )
        print(
            json.dumps(
                {
                    "run_name": result["run_name"],
                    "run_id": result["run_id"],
                    "run_status": result["run_status"],
                    "receipt": str(args.output_dir / "grounded_e2e_run_v1.json"),
                    "reproducibility": result["reproducibility"],
                    "authorization": result["outputs"]["authorization"]["counts"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    elif args.command == "build-semantic-review-queue":
        result = build_semantic_review_queue(
            bindings=args.bindings,
            bindings_manifest=args.bindings_manifest,
            structured_tables=args.structured_tables,
            evidence_context=args.evidence_context,
            evidence_context_manifest=args.evidence_context_manifest,
            output_dir=args.output_dir,
        )
        print(
            json.dumps(
                {
                    "manifest": result["manifest_path"],
                    "counts": result["counts"],
                    "outputs": result["outputs"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        raise RuntimeError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
