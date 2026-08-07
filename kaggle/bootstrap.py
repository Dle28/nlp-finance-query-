#!/usr/bin/env python3
"""Bootstrap a Kaggle notebook from completed local baseline artifacts.

Kaggle may expose an uploaded tar.gz either as the original archive or as an
already-extracted directory tree. This script supports both layouts.

Typical flow:

1. Add the private `vifinqa-baseline-artifacts` Kaggle Dataset as notebook input.
2. Clone this repository into `/kaggle/working/AI_guru`.
3. Run `python kaggle/bootstrap.py`.

The script then either:

- verifies and extracts `vifinqa_baseline_artifacts.tar.gz`, or
- discovers an extracted `artifacts/` directory under `/kaggle/input` and copies
  those read-only Kaggle input files into the writable repository `artifacts/`
  directory.

Finally it validates that the table asset inventory and dense UID list contain
the same number of records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path


DEFAULT_INPUT_ROOT = Path("/kaggle/input")
DEFAULT_REPO_ROOT = Path("/kaggle/working/AI_guru")
ARCHIVE_NAME = "vifinqa_baseline_artifacts.tar.gz"

REQUIRED_NAMES = (
    "table_assets.jsonl",
    "lexical_index.sqlite3",
    "dense.index",
    "dense_uids.jsonl",
)
OPTIONAL_NAMES = (
    "dense.index.meta.json",
)
REQUIRED = tuple(Path("artifacts") / name for name in REQUIRED_NAMES)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--archive", type=Path, default=None)
    parser.add_argument(
        "--artifact-source",
        type=Path,
        default=None,
        help="Explicit already-extracted artifacts directory under /kaggle/input.",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def locate_archive(input_root: Path) -> Path | None:
    matches = sorted(input_root.rglob(ARCHIVE_NAME))
    if not matches:
        return None
    if len(matches) > 1:
        print("Multiple baseline archives found; using the first:")
        for match in matches:
            print(f"  {match}")
    return matches[0]


def is_artifact_dir(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in REQUIRED_NAMES)


def locate_artifact_dir(input_root: Path) -> Path | None:
    """Find an already-extracted Kaggle artifacts directory.

    Searching for the relatively small dense UID file is cheaper than walking
    and stat'ing every directory under `/kaggle/input` multiple times.
    """
    candidates: list[Path] = []
    for uid_file in input_root.rglob("dense_uids.jsonl"):
        parent = uid_file.parent
        if is_artifact_dir(parent):
            candidates.append(parent)

    candidates = sorted(set(candidates))
    if not candidates:
        return None
    if len(candidates) > 1:
        print("Multiple extracted baseline artifact directories found; using the first:")
        for candidate in candidates:
            print(f"  {candidate}")
    return candidates[0]


def verify_sidecar(archive: Path) -> None:
    sidecar = archive.parent / "sha256sums.txt"
    if not sidecar.is_file():
        print("No sha256sums.txt beside archive; skipping archive checksum verification.")
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


def copy_extracted_artifacts(source: Path, repo_root: Path) -> None:
    """Copy Kaggle's read-only dataset files into writable working storage."""
    destination = repo_root / "artifacts"
    destination.mkdir(parents=True, exist_ok=True)

    names = (*REQUIRED_NAMES, *OPTIONAL_NAMES)
    for name in names:
        src = source / name
        if not src.is_file():
            if name in REQUIRED_NAMES:
                raise FileNotFoundError(f"Missing required Kaggle artifact: {src}")
            continue
        dst = destination / name
        print(
            f"Copying {src} -> {dst} "
            f"({src.stat().st_size / (1024 * 1024):.1f} MiB)"
        )
        shutil.copy2(src, dst)


def count_lines(path: Path) -> int:
    with path.open("rb") as file:
        return sum(1 for line in file if line.strip())


def validate(repo_root: Path) -> dict[str, object]:
    missing = [str(path) for path in REQUIRED if not (repo_root / path).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing restored artifacts: {missing}")

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
    input_root = args.input_root.resolve()
    repo_root.mkdir(parents=True, exist_ok=True)

    artifacts_dir = repo_root / "artifacts"
    if args.force and artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)

    archive = args.archive.resolve() if args.archive else locate_archive(input_root)
    source_dir = (
        args.artifact_source.resolve()
        if args.artifact_source
        else locate_artifact_dir(input_root)
    )

    if archive is not None:
        print(f"Using baseline archive: {archive}")
        verify_sidecar(archive)
        safe_extract(archive, repo_root)
        restore_mode = "archive"
    elif source_dir is not None:
        print(f"Archive not present; using Kaggle-extracted artifacts: {source_dir}")
        copy_extracted_artifacts(source_dir, repo_root)
        restore_mode = "extracted_dataset"
    else:
        raise FileNotFoundError(
            "Could not find either the baseline archive or an extracted artifacts "
            f"directory under {input_root}. Attach the private baseline-artifact "
            "Kaggle Dataset to the notebook."
        )

    summary = validate(repo_root)
    summary["restore_mode"] = restore_mode
    summary["input_source"] = str(archive or source_dir)
    print(json.dumps(summary, indent=2))

    print("\nBootstrap complete.")
    print("Next recommended checks:")
    print("  python -m unittest discover -s tests -v")
    print("  finance-query retrieve --config configs/baseline.yaml --question-id 1 --question '...'")
    print("  python scripts/benchmark_runtime.py --assets artifacts/table_assets.jsonl --model BAAI/bge-m3 --batch-size 4")


if __name__ == "__main__":
    main()
