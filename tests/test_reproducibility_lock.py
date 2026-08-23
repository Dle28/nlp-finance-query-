from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.reproducibility_lock import (
    GROUNDED_LOCK_PROTOCOL,
    ReproducibilityLockError,
    verify_grounded_lock,
)


def test_lock_verifies_exact_files_and_rejects_drift(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "protocol": GROUNDED_LOCK_PROTOCOL,
                "schema_version": 1,
                "files": [
                    {
                        "name": "artifact",
                        "path": artifact.name,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        "size_bytes": artifact.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = verify_grounded_lock(lock, repository_root=tmp_path)
    assert result["status"] == "verified"
    assert result["verified_file_count"] == 1

    artifact.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ReproducibilityLockError, match="size mismatch|SHA-256 mismatch"):
        verify_grounded_lock(lock, repository_root=tmp_path)


def test_lock_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-lock-test.json"
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "protocol": GROUNDED_LOCK_PROTOCOL,
                "files": [
                    {
                        "name": "outside",
                        "path": "../outside-lock-test.json",
                        "sha256": "a" * 64,
                        "size_bytes": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReproducibilityLockError, match="escapes repository"):
        verify_grounded_lock(lock, repository_root=tmp_path)
