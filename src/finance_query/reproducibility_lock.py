"""Portable verification for the pinned grounded replay dependency closure."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


GROUNDED_LOCK_PROTOCOL = "vifinqa_grounded_reproducibility_lock_v1"


class ReproducibilityLockError(ValueError):
    """Raised when a declared dependency is absent or differs by one byte."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_grounded_lock(lock_path: Path, *, repository_root: Path) -> dict[str, Any]:
    """Verify every file in a tracked lock without downloading or mutating it."""

    value = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("protocol") != GROUNDED_LOCK_PROTOCOL:
        raise ReproducibilityLockError("Unexpected grounded reproducibility lock protocol")
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        raise ReproducibilityLockError("Grounded reproducibility lock has no files")
    names: set[str] = set()
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ReproducibilityLockError("Grounded lock entry is not an object")
        name = str(entry.get("name") or "").strip()
        relative = str(entry.get("path") or "").strip()
        expected_sha = str(entry.get("sha256") or "").strip()
        expected_size = entry.get("size_bytes")
        if not name or name in names or not relative or len(expected_sha) != 64:
            raise ReproducibilityLockError("Grounded lock entry identity is invalid")
        names.add(name)
        candidate = (repository_root / relative).resolve()
        try:
            candidate.relative_to(repository_root.resolve())
        except ValueError as exc:
            raise ReproducibilityLockError(f"Grounded lock path escapes repository: {relative}") from exc
        if not candidate.is_file():
            raise ReproducibilityLockError(f"Grounded lock file is missing: {relative}")
        if not isinstance(expected_size, int) or candidate.stat().st_size != expected_size:
            raise ReproducibilityLockError(f"Grounded lock size mismatch: {relative}")
        actual_sha = sha256_file(candidate)
        if actual_sha != expected_sha:
            raise ReproducibilityLockError(f"Grounded lock SHA-256 mismatch: {relative}")
        verified.append(
            {
                "name": name,
                "path": relative,
                "sha256": actual_sha,
                "size_bytes": expected_size,
            }
        )
    return {
        "protocol": GROUNDED_LOCK_PROTOCOL,
        "lock_path": str(lock_path.resolve()),
        "verified_file_count": len(verified),
        "verified_bytes": sum(item["size_bytes"] for item in verified),
        "status": "verified",
        "files": verified,
    }
