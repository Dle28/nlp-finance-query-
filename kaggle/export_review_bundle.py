#!/usr/bin/env python3
"""Single Kaggle entrypoint: validate/build retrieval artifacts and export a local-review bundle.

This is the ONLY Kaggle-facing script a reviewer needs to run.

Default behavior is safe:
- it does NOT rebuild heavy artifacts unless --build-missing is passed;
- it delegates integrity gates + retrieval export to run_kaggle_retrieval_export.py;
- it produces one easy-to-download archive in /kaggle/working;
- it writes a SHA256 file and a handoff JSON with the exact local next step;
- in Jupyter/Kaggle it renders clickable FileLink download links when possible.

Normal use (artifacts already exist):
    python kaggle/export_review_bundle.py --top-k 20

Only when artifacts are missing/stale and Kaggle should do heavy work:
    python kaggle/export_review_bundle.py --top-k 20 --build-missing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_WORKING = Path("/kaggle/working")
DEFAULT_BUNDLE_NAME = "vifinqa_review_bundle"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=None)
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
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--min-asset-count", type=int, default=100_000)
    parser.add_argument("--build-missing", action="store_true")
    parser.add_argument("--no-dense", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument(
        "--working-dir",
        type=Path,
        default=DEFAULT_WORKING,
        help="Kaggle working directory. Default: /kaggle/working",
    )
    parser.add_argument(
        "--bundle-name",
        default=DEFAULT_BUNDLE_NAME,
        help="Output bundle directory/archive base name.",
    )
    parser.add_argument("--no-download-link", action="store_true")
    return parser.parse_args()


def looks_like_repo(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "pyproject.toml").is_file()
        and (path / "src/finance_query").is_dir()
        and (path / "kaggle/run_kaggle_retrieval_export.py").is_file()
    )


def find_repo_root(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not looks_like_repo(candidate):
            raise RuntimeError(f"--repo-root is not a ViFinQA repository: {candidate}")
        return candidate

    cwd = Path.cwd().resolve()
    candidates = [
        cwd,
        *cwd.parents,
        Path("/kaggle/working/AI_guru"),
        Path("/kaggle/working/nlp-finance-query-"),
    ]
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if looks_like_repo(candidate):
            return candidate

    raise RuntimeError(
        "Cannot auto-detect repo. Run from the repository or pass "
        "--repo-root /kaggle/working/AI_guru"
    )


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def run(command: list[str], cwd: Path) -> None:
    print("\n$", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def display_download_links(archive: Path, checksum: Path, handoff: Path) -> None:
    """Best-effort clickable links for Kaggle/Jupyter; terminal output remains canonical."""
    try:
        from IPython.display import FileLink, display

        old_cwd = Path.cwd()
        try:
            os.chdir(archive.parent)
            print("\nCLICK TO DOWNLOAD:")
            display(FileLink(archive.name, result_html_prefix="Download bundle: "))
            display(FileLink(checksum.name, result_html_prefix="Download SHA256: "))
            display(FileLink(handoff.name, result_html_prefix="Download handoff info: "))
        finally:
            os.chdir(old_cwd)
    except Exception as exc:
        print(f"[INFO] Could not render clickable FileLink: {exc}")
        print("Use Kaggle Files/Output sidebar and download the paths printed below.")


def main() -> None:
    args = parse_args()
    if args.top_k <= 0:
        raise ValueError("--top-k must be > 0")

    repo_root = find_repo_root(args.repo_root)
    working_dir = args.working_dir.expanduser().resolve()
    working_dir.mkdir(parents=True, exist_ok=True)

    questions = args.questions if args.questions.is_absolute() else repo_root / args.questions
    config = args.config if args.config.is_absolute() else repo_root / args.config

    if not questions.is_file():
        raise FileNotFoundError(f"Questions file not found: {questions}")
    if not config.is_file():
        raise FileNotFoundError(f"Config file not found: {config}")

    export_root = working_dir / "vifinqa_review_export"
    bundle_dir = export_root / args.bundle_name
    download_archive = working_dir / f"{args.bundle_name}.tar.gz"
    download_sha = working_dir / f"{args.bundle_name}.tar.gz.sha256"
    handoff_path = working_dir / "vifinqa_review_handoff.json"

    if bundle_dir.exists() and any(bundle_dir.iterdir()) and not args.force:
        raise RuntimeError(
            f"Bundle output already exists: {bundle_dir}\n"
            "Re-run with --force only if you intentionally want to replace it."
        )

    if args.force:
        if export_root.exists():
            shutil.rmtree(export_root)
        for path in [download_archive, download_sha, handoff_path]:
            if path.exists():
                path.unlink()

    print("=" * 72)
    print("ViFinQA KAGGLE -> LOCAL REVIEW EXPORT")
    print("=" * 72)
    print("repo_root      :", repo_root)
    print("questions      :", questions)
    print("config         :", config)
    print("top_k          :", args.top_k)
    print("build_missing  :", args.build_missing)
    print("bundle_dir     :", bundle_dir)
    print("download target:", download_archive)

    if not args.skip_install:
        print("\nSTAGE 0 — install current repo in editable mode")
        run([sys.executable, "-m", "pip", "install", "-q", "-e", str(repo_root)], repo_root)

    print("\nSTAGE 1 — validate artifacts and export review bundle")
    command = [
        sys.executable,
        str(repo_root / "kaggle/run_kaggle_retrieval_export.py"),
        "--repo-root", str(repo_root),
        "--questions", str(questions),
        "--config", str(config),
        "--output-dir", str(bundle_dir),
        "--top-k", str(args.top_k),
        "--min-asset-count", str(args.min_asset_count),
    ]
    if args.build_missing:
        command.append("--build-missing")
    if args.no_dense:
        command.append("--no-dense")
    if args.force:
        command.append("--force-export")

    run(command, repo_root)

    produced_archive = bundle_dir / f"{args.bundle_name}.tar.gz"
    if not produced_archive.is_file():
        # Backward-compatible fallback for the canonical builder name.
        produced_archive = bundle_dir / "vifinqa_review_bundle.tar.gz"
    if not produced_archive.is_file():
        raise FileNotFoundError(
            "Export script finished but bundle archive is missing. Expected one of:\n"
            f"  {bundle_dir / (args.bundle_name + '.tar.gz')}\n"
            f"  {bundle_dir / 'vifinqa_review_bundle.tar.gz'}"
        )

    print("\nSTAGE 2 — create easy Kaggle download artifact")
    shutil.copy2(produced_archive, download_archive)
    digest = sha256_file(download_archive)
    download_sha.write_text(
        f"{digest}  {download_archive.name}\n",
        encoding="utf-8",
    )

    handoff = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(repo_root),
        "repo_root": str(repo_root),
        "bundle_dir": str(bundle_dir),
        "download_archive": str(download_archive),
        "download_sha256": str(download_sha),
        "archive_sha256": digest,
        "top_k": args.top_k,
        "next_local_steps": [
            "Download vifinqa_review_bundle.tar.gz and its .sha256 file.",
            "Verify SHA256 on local before extracting.",
            "Extract into a dedicated local directory.",
            "Run: python local/run_local_review_stage.py baseline --bundle-dir <EXTRACTED_DIR> --seed-size 12",
            "Review only the generated human seed queue.",
            "Run calibration, then review only needs_human cases.",
        ],
        "guide": "docs/KAGGLE_TO_LOCAL_REVIEW.md",
    }
    handoff_path.write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print("EXPORT SUCCESS")
    print("=" * 72)
    print("Bundle directory :", bundle_dir)
    print("DOWNLOAD FILE    :", download_archive)
    print("SHA256 FILE      :", download_sha)
    print("HANDOFF INFO     :", handoff_path)
    print("SHA256           :", digest)
    print("\nNEXT: download the .tar.gz to local. Do not review inside Kaggle.")
    print("Guide:", repo_root / "docs/KAGGLE_TO_LOCAL_REVIEW.md")

    if not args.no_download_link:
        display_download_links(download_archive, download_sha, handoff_path)


if __name__ == "__main__":
    main()
