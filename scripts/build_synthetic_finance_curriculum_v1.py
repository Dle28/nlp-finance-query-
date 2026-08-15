#!/usr/bin/env python3
"""Build a hash-bound, deterministic synthetic finance training curriculum."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance_query.synthetic_curriculum import (
    SYNTHETIC_CURRICULUM_PROTOCOL,
    build_synthetic_curriculum,
    load_table_assets,
    manifest_payload,
    sha256_file,
    verify_synthetic_example,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-examples", type=int, default=10_000)
    parser.add_argument("--max-cells-per-table", type=int, default=4)
    parser.add_argument("--hard-negatives-per-example", type=int, default=6)
    parser.add_argument("--min-hard-negatives", type=int, default=2)
    parser.add_argument("--validation-percent", type=int, default=10)
    parser.add_argument("--test-percent", type=int, default=10)
    parser.add_argument(
        "--source-data-role",
        choices=["unknown", "permitted_report_corpus"],
        default="unknown",
        help=(
            "Explicitly attest that the source report corpus is permitted for "
            "training.  Benchmark questions are never an input to this script."
        ),
    )
    return parser.parse_args()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def main() -> None:
    args = parse_args()
    if args.source_data_role != "permitted_report_corpus":
        raise ValueError(
            "Refusing to build training data until --source-data-role "
            "permitted_report_corpus is explicitly supplied."
        )
    if not args.tables.is_file():
        raise FileNotFoundError(args.tables)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(
            f"Output directory must be new or empty to preserve immutable artifacts: {args.output_dir}"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    tables_sha256 = sha256_file(args.tables)
    assets = load_table_assets(args.tables)
    examples, summary = build_synthetic_curriculum(
        assets,
        source_tables_sha256=tables_sha256,
        max_examples=args.max_examples,
        max_cells_per_table=args.max_cells_per_table,
        hard_negatives_per_example=args.hard_negatives_per_example,
        min_hard_negatives=args.min_hard_negatives,
        validation_percent=args.validation_percent,
        test_percent=args.test_percent,
    )
    if not examples:
        raise RuntimeError("No synthetic examples passed source and hard-negative gates.")
    replay_errors = {
        row["curriculum_id"]: verify_synthetic_example(row)
        for row in examples
        if verify_synthetic_example(row)
    }
    if replay_errors:
        raise RuntimeError(f"Synthetic replay validation failed: {replay_errors}")

    examples_path = args.output_dir / "synthetic_finance_curriculum_v1.jsonl"
    manifest_path = args.output_dir / "synthetic_finance_curriculum_v1.manifest.json"
    write_jsonl(examples_path, examples)
    examples_sha256 = sha256_file(examples_path)
    manifest = manifest_payload(
        tables_path=args.tables,
        tables_sha256=tables_sha256,
        examples_path=examples_path,
        examples_sha256=examples_sha256,
        generation_config={
            "max_examples": args.max_examples,
            "max_cells_per_table": args.max_cells_per_table,
            "hard_negatives_per_example": args.hard_negatives_per_example,
            "min_hard_negatives": args.min_hard_negatives,
            "validation_percent": args.validation_percent,
            "test_percent": args.test_percent,
            "source_data_role": args.source_data_role,
        },
        summary=summary,
    )
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        json.dumps(
            {
                "protocol": SYNTHETIC_CURRICULUM_PROTOCOL,
                "output": str(examples_path),
                "manifest": str(manifest_path),
                "examples_sha256": examples_sha256,
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
