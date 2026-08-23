#!/usr/bin/env python3
"""Fail closed while verifying and extracting the minimal Kaggle critic source.

This has no project imports, so a Kaggle kernel can run it before installing
the extracted package or downloading the Qwen model.  It validates the outer
manifest, gzip-tar bytes, embedded source identity and every declared source
file before extracting the strictly-declared regular files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
from typing import Any, Mapping


PROTOCOL = "kaggle_grounded_critic_source_bundle_v1"
IDENTITY_NAME = "SOURCE_BUNDLE.json"
CONTRACT_KEYS = (
    "contains_raw_reports",
    "contains_labels",
    "contains_research_artifacts",
    "contains_credentials",
    "eligible_for_evidence",
    "eligible_for_training",
    "eligible_for_submission",
    "eligible_for_promotion",
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
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def require_identity(value: object) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    if not isinstance(value, dict) or value.get("protocol") != PROTOCOL:
        raise ValueError("Source bundle identity has unsupported protocol")
    tree_sha = value.get("source_tree_sha256")
    base = {key: item for key, item in value.items() if key != "source_tree_sha256"}
    if not isinstance(tree_sha, str) or len(tree_sha) != 64 or canonical_sha256(base) != tree_sha:
        raise ValueError("Source bundle source tree hash is invalid")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("Source bundle has no declared source files")
    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in raw_files:
        if not isinstance(entry, dict):
            raise ValueError("Source bundle has malformed source entry")
        name, digest = entry.get("path"), entry.get("sha256")
        path = Path(name) if isinstance(name, str) else None
        if (
            path is None
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != name
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or name in seen
        ):
            raise ValueError("Source bundle has invalid source path or SHA-256")
        seen.add(name)
        files.append((name, digest))
    return value, files


def verify_and_extract(*, manifest_path: Path, archive_path: Path, output_dir: Path) -> dict[str, object]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing source output directory: {output_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("protocol") != PROTOCOL:
        raise ValueError("Source bundle has unsupported protocol")
    archive = ((manifest.get("outputs") or {}).get("archive") or {})
    if not isinstance(archive, dict) or archive.get("file_name") != archive_path.name:
        raise ValueError("Source bundle archive name is invalid")
    archive_sha = sha256_file(archive_path)
    if archive_sha != archive.get("sha256"):
        raise ValueError("Source bundle archive SHA-256 mismatch")
    contract = manifest.get("source_contract") or {}
    if any(contract.get(key) is not False for key in CONTRACT_KEYS):
        raise ValueError("Source bundle violates isolation contract")
    identity, files = require_identity(manifest.get("source_bundle"))
    expected_names = [name for name, _ in files] + [IDENTITY_NAME]
    with tarfile.open(archive_path, mode="r:*") as bundle:
        members = bundle.getmembers()
        if [member.name for member in members] != expected_names or not all(member.isfile() for member in members):
            raise ValueError("Source bundle archive members differ from its identity")
        identity_handle = bundle.extractfile(IDENTITY_NAME)
        if identity_handle is None or json.loads(identity_handle.read()) != identity:
            raise ValueError("Source bundle embedded identity differs from manifest")
        for name, digest in files:
            handle = bundle.extractfile(name)
            if handle is None or sha256_bytes(handle.read()) != digest:
                raise ValueError("Source bundle member SHA-256 mismatch")
        output_dir.mkdir(parents=True)
        # Write only the regular members already checked against the exact,
        # hash-bound identity. This avoids relying on Python 3.12's tarfile
        # extraction filter in Kaggle's runtime image.
        for member in members:
            handle = bundle.extractfile(member)
            if handle is None:  # defensive: members were verified regular files
                raise ValueError("Source bundle archive member cannot be read")
            destination = output_dir / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(handle.read())
    for name, digest in files:
        if sha256_file(output_dir / name) != digest:
            raise ValueError("Extracted source bundle member SHA-256 mismatch")
    return {
        "source_bundle_manifest_sha256": sha256_file(manifest_path),
        "source_bundle_archive_sha256": archive_sha,
        "source_tree_sha256": identity["source_tree_sha256"],
        "git_revision": identity.get("git_revision"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            verify_and_extract(
                manifest_path=args.manifest,
                archive_path=args.archive,
                output_dir=args.output_dir,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    main()
