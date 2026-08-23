from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load("source_bundle_builder", "scripts/build_kaggle_grounded_critic_source_bundle.py")
VERIFIER = _load("source_bundle_verifier", "scripts/verify_kaggle_grounded_critic_source_bundle.py")


def _build(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    for name in BUILDER.INCLUDED_PATHS:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{name}\n", encoding="utf-8")
    BUILDER.build_source_bundle(repo_root=repo, output_dir=tmp_path / "bundle")
    return tmp_path / "bundle" / BUILDER.MANIFEST_NAME, tmp_path / "bundle" / BUILDER.ARCHIVE_NAME


def test_verifier_checks_identity_members_and_extracted_files(tmp_path: Path) -> None:
    manifest, archive = _build(tmp_path)
    output = tmp_path / "out"
    result = VERIFIER.verify_and_extract(manifest_path=manifest, archive_path=archive, output_dir=output)
    assert len(result["source_tree_sha256"]) == 64
    assert (output / "SOURCE_BUNDLE.json").is_file()
    assert (output / "scripts" / "verify_kaggle_grounded_critic_source_bundle.py").is_file()


def test_verifier_cli_uses_its_declared_arguments(tmp_path: Path) -> None:
    manifest, archive = _build(tmp_path)
    output = tmp_path / "cli-out"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify_kaggle_grounded_critic_source_bundle.py"),
            "--manifest",
            str(manifest),
            "--archive",
            str(archive),
            "--output-dir",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert len(json.loads(completed.stdout)["source_tree_sha256"]) == 64


def test_verifier_fails_closed_for_tampered_or_existing_output(tmp_path: Path) -> None:
    manifest, archive = _build(tmp_path)
    archive.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="archive SHA-256 mismatch"):
        VERIFIER.verify_and_extract(manifest_path=manifest, archive_path=archive, output_dir=tmp_path / "out")

    manifest, archive = _build(tmp_path / "again")
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        VERIFIER.verify_and_extract(manifest_path=manifest, archive_path=archive, output_dir=output)
