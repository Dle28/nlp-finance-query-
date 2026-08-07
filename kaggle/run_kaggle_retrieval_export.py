#!/usr/bin/env python3
"""Controlled Kaggle runner for heavy retrieval artifacts + review bundle export.

Default behavior is conservative: validate existing artifacts and export only.
Heavy rebuilds happen only with explicit ``--build-missing``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


Q13_TEXT = "tiền và các khoản tương đương tiền"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("data/labels/annotation_questions_60.jsonl"),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/annotation_baseline.yaml"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--min-asset-count", type=int, default=100_000)
    parser.add_argument("--build-missing", action="store_true")
    parser.add_argument("--no-dense", action="store_true")
    parser.add_argument("--force-export", action="store_true")
    return parser.parse_args()


def count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as file:
        return sum(1 for line in file if line.strip())


def q13_exists(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                row.get("ticker") == "SAB"
                and row.get("report_year") == 2016
                and row.get("scope") == "separate"
                and Q13_TEXT in str(row.get("search_text") or "").casefold()
            ):
                return True
    return False


def lexical_count(path: Path) -> int:
    if not path.is_file():
        return 0
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
    finally:
        connection.close()


def dense_ntotal(path: Path) -> int:
    if not path.is_file():
        return 0
    import faiss
    return int(faiss.read_index(str(path)).ntotal)


def health(repo_root: Path, min_asset_count: int, require_dense: bool) -> dict:
    artifacts = repo_root / "artifacts"
    assets = artifacts / "table_assets.jsonl"
    lexical = artifacts / "lexical_index.sqlite3"
    dense_uids = artifacts / "dense_uids.jsonl"
    dense = artifacts / "dense.index"

    assets_n = count_jsonl(assets)
    lexical_n = lexical_count(lexical)
    uid_n = count_jsonl(dense_uids)
    dense_n = dense_ntotal(dense) if dense.is_file() else 0
    oracle = q13_exists(assets)

    valid_assets = assets_n >= min_asset_count and oracle
    valid_lexical = valid_assets and lexical_n == assets_n
    valid_dense = (uid_n == assets_n and dense_n == assets_n) if require_dense else True

    return {
        "asset_count": assets_n,
        "lexical_count": lexical_n,
        "dense_uid_count": uid_n,
        "dense_ntotal": dense_n,
        "q13_oracle": oracle,
        "valid_assets": valid_assets,
        "valid_lexical": valid_lexical,
        "valid_dense": valid_dense,
        "valid": valid_assets and valid_lexical and valid_dense,
    }


def remove(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()
            print("Removed stale artifact:", path)


def run(command: list[str], repo_root: Path) -> None:
    print("\n$", " ".join(command))
    subprocess.run(command, cwd=repo_root, check=True)


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    artifacts = repo_root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    if not args.questions.is_absolute():
        args.questions = repo_root / args.questions
    if not args.config.is_absolute():
        args.config = repo_root / args.config

    if not args.questions.is_file():
        raise FileNotFoundError(args.questions)
    if not args.config.is_file():
        raise FileNotFoundError(args.config)

    require_dense = not args.no_dense
    state = health(repo_root, args.min_asset_count, require_dense)
    print("INITIAL ARTIFACT HEALTH")
    print(json.dumps(state, indent=2))

    if not state["valid"] and not args.build_missing:
        raise RuntimeError(
            "Artifacts are not ready. Re-run with --build-missing only if you explicitly want Kaggle to do heavy work."
        )

    if args.build_missing:
        if not state["valid_assets"]:
            print("\nSTAGE A — rebuild table_assets")
            remove([
                artifacts / "table_assets.jsonl",
                artifacts / "lexical_index.sqlite3",
                artifacts / "lexical_index.sqlite3-wal",
                artifacts / "lexical_index.sqlite3-shm",
                artifacts / "dense.index",
                artifacts / "dense_uids.jsonl",
                artifacts / "dense.index.meta.json",
            ])
            run(["finance-query", "build-assets"], repo_root)
            state = health(repo_root, args.min_asset_count, require_dense=False)
            print(json.dumps(state, indent=2))
            if not state["valid_assets"]:
                raise RuntimeError("table_assets gate failed after rebuild.")

        if not state["valid_lexical"]:
            print("\nSTAGE B — rebuild lexical index")
            remove([
                artifacts / "lexical_index.sqlite3",
                artifacts / "lexical_index.sqlite3-wal",
                artifacts / "lexical_index.sqlite3-shm",
            ])
            run(["finance-query", "build-lexical"], repo_root)
            state = health(repo_root, args.min_asset_count, require_dense=False)
            print(json.dumps(state, indent=2))
            if not state["valid_lexical"]:
                raise RuntimeError("lexical gate failed after rebuild.")

        if require_dense:
            state = health(repo_root, args.min_asset_count, require_dense=True)
            if not state["valid_dense"]:
                print("\nSTAGE C — rebuild dense index (GPU-heavy)")
                remove([
                    artifacts / "dense.index",
                    artifacts / "dense_uids.jsonl",
                    artifacts / "dense.index.meta.json",
                ])
                run(
                    [
                        "finance-query",
                        "build-dense",
                        "--config",
                        str(args.config),
                    ],
                    repo_root,
                )

    state = health(repo_root, args.min_asset_count, require_dense)
    print("\nFINAL ARTIFACT HEALTH")
    print(json.dumps(state, indent=2))
    if not state["valid"]:
        raise RuntimeError("Final artifact integrity gate FAILED; export refused.")

    print("\nSTAGE D — export immutable local-review bundle")
    command = [
        sys.executable,
        str(repo_root / "scripts/build_review_bundle.py"),
        "--questions", str(args.questions),
        "--config", str(args.config),
        "--output-dir", str(args.output_dir),
        "--top-k", str(args.top_k),
        "--repo-root", str(repo_root),
        "--min-asset-count", str(args.min_asset_count),
    ]
    if args.no_dense:
        command.append("--no-dense")
    if args.force_export:
        command.append("--force")

    run(command, repo_root)

    print("\nDONE")
    print("Kaggle heavy stages end here.")
    print("Copy/download this bundle to local:", args.output_dir / "vifinqa_review_bundle.tar.gz")
    print("Run multi-agent review locally; do not review against live Kaggle indices.")


if __name__ == "__main__":
    main()
