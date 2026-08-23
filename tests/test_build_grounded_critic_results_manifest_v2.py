from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "grounded_critic_results_manifest",
    ROOT / "scripts" / "build_grounded_critic_results_manifest_v2.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

SOURCE_SPEC = importlib.util.spec_from_file_location(
    "kaggle_grounded_critic_source_bundle",
    ROOT / "scripts" / "build_kaggle_grounded_critic_source_bundle.py",
)
assert SOURCE_SPEC is not None and SOURCE_SPEC.loader is not None
SOURCE_MODULE = importlib.util.module_from_spec(SOURCE_SPEC)
SOURCE_SPEC.loader.exec_module(SOURCE_MODULE)


def _bundle(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    for relative in SOURCE_MODULE.INCLUDED_PATHS:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative}\n", encoding="utf-8")
    SOURCE_MODULE.build_source_bundle(repo_root=repo, output_dir=tmp_path / "bundle")
    return (
        tmp_path / "bundle" / SOURCE_MODULE.MANIFEST_NAME,
        tmp_path / "bundle" / SOURCE_MODULE.ARCHIVE_NAME,
    )


def test_source_bundle_provenance_requires_hash_and_isolation_contract(tmp_path: Path) -> None:
    manifest, archive = _bundle(tmp_path)
    value = MODULE.require_source_bundle(manifest_path=manifest, archive_path=archive)
    assert value["archive"]["sha256"] == MODULE.sha256_file(archive)
    assert len(value["source_tree_sha256"]) == 64

    archive.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        MODULE.require_source_bundle(manifest_path=manifest, archive_path=archive)


def test_source_bundle_rejects_promotable_or_malformed_provenance(tmp_path: Path) -> None:
    manifest, archive = _bundle(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["source_contract"]["contains_labels"] = True
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="isolation contract"):
        MODULE.require_source_bundle(manifest_path=manifest, archive_path=archive)
