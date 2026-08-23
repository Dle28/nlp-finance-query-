from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kaggle_grounded_critic_source_bundle",
    ROOT / "scripts" / "build_kaggle_grounded_critic_source_bundle.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _fixture_repo(root: Path) -> Path:
    for relative in MODULE.INCLUDED_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative}\n", encoding="utf-8")
    return root


def test_bundle_contains_only_declared_regular_sources_with_hashes(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path / "repo")
    result = MODULE.build_source_bundle(repo_root=repo, output_dir=tmp_path / "bundle")
    archive_path = tmp_path / "bundle" / MODULE.ARCHIVE_NAME
    manifest_path = tmp_path / "bundle" / MODULE.MANIFEST_NAME
    persisted = json.loads(manifest_path.read_text())
    assert {key: value for key, value in result.items() if key != "manifest_path"} == persisted
    assert persisted["outputs"]["archive"]["sha256"] == MODULE.sha256_file(archive_path)
    assert persisted["source_contract"]["contains_credentials"] is False
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
        assert names == [*MODULE.INCLUDED_PATHS, MODULE.BUNDLE_IDENTITY_NAME]
        identity = json.loads(archive.extractfile(MODULE.BUNDLE_IDENTITY_NAME).read())
        assert identity == persisted["source_bundle"]
        for entry in identity["files"]:
            payload = archive.extractfile(entry["path"]).read()
            assert MODULE.sha256_bytes(payload) == entry["sha256"]


def test_bundle_refuses_missing_or_existing_output(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path / "repo")
    (repo / MODULE.INCLUDED_PATHS[-1]).unlink()
    with pytest.raises(ValueError, match="missing"):
        MODULE.build_source_bundle(repo_root=repo, output_dir=tmp_path / "bundle")

    repo = _fixture_repo(tmp_path / "repo-2")
    MODULE.build_source_bundle(repo_root=repo, output_dir=tmp_path / "bundle-2")
    with pytest.raises(FileExistsError, match="overwrite"):
        MODULE.build_source_bundle(repo_root=repo, output_dir=tmp_path / "bundle-2")


def test_actual_bundle_is_importable_as_a_standalone_source_tree(tmp_path: Path) -> None:
    """Catch package-level imports that the minimal bundle forgot to include."""
    result = MODULE.build_source_bundle(repo_root=ROOT, output_dir=tmp_path / "bundle")
    archive = tmp_path / "bundle" / MODULE.ARCHIVE_NAME
    target = tmp_path / "source"
    with tarfile.open(archive, "r:*") as handle:
        handle.extractall(target, filter="data")
    environment = {"PYTHONPATH": str(target / "src")}
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from finance_query.grounded_critic_protocol import validate_critic_response; "
            "from finance_query.grounded_critic_qwen_v2 import critic_prompt; "
            "from finance_query.qwen_inference import QwenGenerator",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0
    assert (target / "src" / "finance_query" / "schemas.py").is_file()
    assert result["source_bundle"]["files"][-1]["path"] == "src/finance_query/qwen_inference.py"
