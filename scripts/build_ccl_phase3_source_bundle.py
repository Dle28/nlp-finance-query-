#!/usr/bin/env python3
"""Build a minimal, deterministic source snapshot for a CCL Phase 3 GPU run.

The archive contains source code and route scripts only. It intentionally
excludes reports, labels, research artifacts, Git metadata and credentials.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile
from typing import Any, Iterable, Mapping


PROTOCOL = "kaggle_ccl_phase3_source_bundle_v1"
ARCHIVE_NAME = "ai_guru_ccl_phase3_source_v1.bundle"
MANIFEST_NAME = "ai_guru_ccl_phase3_source_v1.manifest.json"
IDENTITY_NAME = "SOURCE_BUNDLE.json"
_FIXED_PATHS = (
    "README.md",
    "pyproject.toml",
    "scripts/run_ccl_phase3_model.py",
    "scripts/validate_ccl_phase3_responses.py",
    "scripts/run_ccl_phase5_component_selection_model.py",
    "scripts/validate_ccl_phase5_component_selection_responses.py",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def git_revision(repo_root: Path) -> str:
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


def included_paths(repo_root: Path) -> tuple[Path, ...]:
    """Return the full transitive local package, with no data/artifact roots."""
    candidates: list[Path] = [Path(name) for name in _FIXED_PATHS]
    package_root = repo_root / "src" / "finance_query"
    candidates.extend(
        path.relative_to(repo_root)
        for path in sorted(package_root.rglob("*.py"))
        if "__pycache__" not in path.parts
    )
    unique = sorted(set(candidates), key=lambda item: item.as_posix())
    if not unique:
        raise ValueError("CCL source bundle has no source files")
    for relative in unique:
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required source file is missing or unsafe: {relative}")
    return tuple(unique)


def source_identity(repo_root: Path) -> dict[str, Any]:
    files = [
        {"path": relative.as_posix(), "sha256": sha256_file(repo_root / relative)}
        for relative in included_paths(repo_root)
    ]
    identity = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "git_revision": git_revision(repo_root),
        "files": files,
    }
    return {**identity, "source_tree_sha256": canonical_sha256(identity)}


def _add_bytes(archive: tarfile.TarFile, *, name: str, value: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(value)
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    archive.addfile(info, BytesIO(value))


def build_source_bundle(*, repo_root: Path, output_dir: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    identity = source_identity(repo_root)
    archive_path = output_dir / ARCHIVE_NAME
    manifest_path = output_dir / MANIFEST_NAME
    if archive_path.exists() or manifest_path.exists():
        raise FileExistsError("refusing to overwrite an existing CCL source bundle")
    output_dir.mkdir(parents=True, exist_ok=True)
    partial = archive_path.with_name(archive_path.name + ".part")
    if partial.exists():
        raise FileExistsError(f"refusing to overwrite partial source bundle: {partial}")
    with partial.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for entry in identity["files"]:
                    relative = str(entry["path"])
                    value = (repo_root / relative).read_bytes()
                    if sha256_bytes(value) != entry["sha256"]:
                        raise ValueError(f"source file changed during bundling: {relative}")
                    _add_bytes(archive, name=relative, value=value)
                _add_bytes(
                    archive,
                    name=IDENTITY_NAME,
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
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build_source_bundle(repo_root=args.repo_root, output_dir=args.output_dir)["manifest_path"])


if __name__ == "__main__":
    main()
