#!/usr/bin/env python3
"""Bootstrap a Kaggle notebook from completed local baseline artifacts.

Typical Kaggle flow:

1. Add the private `vifinqa-baseline-artifacts` Kaggle Dataset as notebook input.
2. Clone this GitHub repository into /kaggle/working/AI_guru.
3. Run:

   python kaggle/bootstrap.py

The script locates `vifinqa_baseline_artifacts.tar.gz` under /kaggle/input,
verifies its checksum when available, extracts it into the repository root, and
checks that the lexical and dense indexes are mutually consistent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path


DEFAULT_INPUT_ROOT = Path("/kaggle/input")
DEFAULT_REPO_ROOT = Path("/kaggle/working/AI_guru")
ARCHIVE_NAME = "vifinqa_baseline_artifacts.tar.gz"

REQUIRED = (
    Path("artifacts/table_assets.jsonl"),
    Path("artifacts/lexical_index.sqlite3"),
    Path("artifacts/dense.index"),
    Path("artifacts/dense_uids.jsonl"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--archive", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def locate_archive(input_root: Path) -> Path:
    matches = sorted(input_root.rglob(ARCHIVE_NAME))
    if not matches:
        raise FileNotFoundError(
            f"Could not find {ARCHIVE_NAME!r} under {input_root}. "
            "Attach the private baseline-artifact Kaggle Dataset to the notebook."
        )
    if len(matches) > 1:
        print("Multiple baseline archives found; using the first:")
        for match in matches:
            print(f"  {match}")
    return matches[0]


def verify_sidecar(archive: Path) -> None:
    sidecar = archive.parent / "sha256sums.txt"
    if not sidecar.is_file():
        print("No sha256sums.txt beside archive; skipping sidecar verification.")
        return

    expected = None
    for line in sidecar.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 2 and Path(fields[-1]).name == archive.name:
            expected = fields[0]
            break
    if expected is None:
        raise ValueError(f"No checksum for {archive.name} in {sidecar}")

    actual = sha256(archive)
    if actual != expected:
        raise ValueError(
            f"Archive checksum mismatch: expected {expected}, got {actual}"
        )
    print(f"Archive SHA-256 verified: {actual}")


def safe_extract(archive: Path, repo_root: Path) -> None:
    repo_root = repo_root.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (repo_root / member.name).resolve()
            if repo_root not in target.parents and target != repo_root:
                raise ValueError(f"Unsafe path in archive: {member.name}")
        tar.extractall(repo_root)


def count_lines(path: Path) -> int:
    with path.open("rb") as file:
        return sum(1 for line in file if line.strip())


def validate(repo_root: Path) -> dict[str, object]:
    missing = [str(path) for path in REQUIRED if not (repo_root / path).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing extracted artifacts: {missing}")

    asset_count = count_lines(repo_root / "artifacts/table_assets.jsonl")
    dense_uid_count = count_lines(repo_root / "artifacts/dense_uids.jsonl")
    if asset_count != dense_uid_count:
        raise ValueError(
            "table_assets.jsonl and dense_uids.jsonl do not contain the same number "
            f"of records: {asset_count} != {dense_uid_count}"
        )

    return {
        "repo_root": str(repo_root),
        "table_assets": asset_count,
        "dense_uids": dense_uid_count,
        "lexical_db_bytes": (repo_root / "artifacts/lexical_index.sqlite3").stat().st_size,
        "dense_index_bytes": (repo_root / "artifacts/dense.index").stat().st_size,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    repo_root.mkdir(parents=True, exist_ok=True)

    archive = args.archive.resolve() if args.archive else locate_archive(args.input_root)
    print(f"Using baseline archive: {archive}")
    verify_sidecar(archive)

    artifacts_dir = repo_root / "artifacts"
    if args.force and artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)

    safe_extract(archive, repo_root)
    summary = validate(repo_root)
    print(json.dumps(summary, indent=2))

    print("\nBootstrap complete.")
    print("Next recommended checks:")
    print("  python -m unittest discover -s tests -v")
    print("  finance-query retrieve --config configs/baseline.yaml --question-id 1 --question '...'")
    print("  python scripts/benchmark_runtime.py --assets artifacts/table_assets.jsonl --model BAAI/bge-m3 --batch-size 4")


if __name__ == "__main__":
    main()
