#!/usr/bin/env python3
"""Build a deterministic, hash-bound minimal source bundle for the GPU critic.

The bundle deliberately contains only the code needed by the Qwen grounded
critic runner.  It excludes reports, labels, research artifacts, `.git`, and
credentials.  A Kaggle kernel verifies the archive and every expanded source
file before installing it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from typing import Any, Mapping


PROTOCOL = "kaggle_grounded_critic_source_bundle_v1"
# Kaggle expands common archive suffixes while ingesting a Dataset. The bytes
# remain a deterministic gzip tar, but this neutral suffix preserves the
# archive as one hash-verifiable input file for the kernel.
ARCHIVE_NAME = "ai_guru_grounded_critic_source_v2.bundle"
MANIFEST_NAME = "ai_guru_grounded_critic_source_v2.manifest.json"
BUNDLE_IDENTITY_NAME = "SOURCE_BUNDLE.json"
INCLUDED_PATHS = (
    "README.md",
    "pyproject.toml",
    "scripts/run_qwen_grounded_critic_v2.py",
    "scripts/build_grounded_critic_results_manifest_v2.py",
    "scripts/verify_kaggle_grounded_critic_source_bundle.py",
    "src/finance_query/__init__.py",
    "src/finance_query/schemas.py",
    "src/finance_query/grounded_critic_protocol.py",
    "src/finance_query/grounded_critic_qwen_v2.py",
    "src/finance_query/qwen_inference.py",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def git_revision(repo_root: Path) -> str:
    """Return a descriptive base revision; the file hashes remain authoritative."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    revision = completed.stdout.strip()
    return revision if len(revision) == 40 else "unavailable"


def source_identity(repo_root: Path) -> dict[str, Any]:
    """Create the non-self-referential source identity embedded in the archive."""
    files: list[dict[str, str]] = []
    for relative_name in INCLUDED_PATHS:
        relative = Path(relative_name)
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Required critic source is missing or not a regular file: {relative_name}")
        files.append({"path": relative.as_posix(), "sha256": sha256_file(path)})
    identity = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "git_revision": git_revision(repo_root),
        "files": files,
    }
    return {**identity, "source_tree_sha256": canonical_sha256(identity)}


def _tar_add_bytes(archive: tarfile.TarFile, *, name: str, value: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(value)
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    from io import BytesIO

    archive.addfile(info, BytesIO(value))


def build_source_bundle(*, repo_root: Path, output_dir: Path) -> dict[str, Any]:
    """Write one deterministic source archive and a separate verification manifest."""
    repo_root = repo_root.resolve()
    identity = source_identity(repo_root)
    archive_path = output_dir / ARCHIVE_NAME
    manifest_path = output_dir / MANIFEST_NAME
    if archive_path.exists() or manifest_path.exists():
        raise FileExistsError("Refusing to overwrite an existing grounded-critic source bundle")
    output_dir.mkdir(parents=True, exist_ok=True)

    partial = archive_path.with_name(archive_path.name + ".part")
    if partial.exists():
        raise FileExistsError(f"Refusing to overwrite partial bundle: {partial}")
    with partial.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for entry in identity["files"]:
                    relative = str(entry["path"])
                    payload = (repo_root / relative).read_bytes()
                    if sha256_bytes(payload) != entry["sha256"]:
                        raise ValueError(f"Source file changed while bundling: {relative}")
                    _tar_add_bytes(archive, name=relative, value=payload)
                _tar_add_bytes(
                    archive,
                    name=BUNDLE_IDENTITY_NAME,
                    value=(json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
                )
    partial.replace(archive_path)
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "source_bundle": identity,
        "outputs": {
            "archive": {
                "path": str(archive_path),
                "file_name": ARCHIVE_NAME,
                "sha256": sha256_file(archive_path),
            }
        },
        "source_contract": {
            "contains_raw_reports": False,
            "contains_labels": False,
            "contains_research_artifacts": False,
            "contains_credentials": False,
            "eligible_for_evidence": False,
            "eligible_for_training": False,
            "eligible_for_submission": False,
            "eligible_for_promotion": False,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build_source_bundle(repo_root=args.repo_root, output_dir=args.output_dir)["manifest_path"])


if __name__ == "__main__":
    main()
