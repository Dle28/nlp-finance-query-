from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = (
    ROOT / "notebooks" / "vifinqa_qwen_grounded_critic_v2_run.ipynb",
    ROOT
    / "artifacts"
    / "kaggle_control"
    / "production_coverage_qwen_kernel"
    / "vifinqa-qwen-grounded-critic-v2-run.ipynb",
)


def _source(path: Path, cell_index: int) -> str:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    return "".join(notebook["cells"][cell_index]["source"])


def test_qwen_kernel_uses_only_hash_bound_minimal_source_bundle() -> None:
    for path in NOTEBOOKS:
        hydrate = _source(path, 1)
        compile(hydrate, str(path), "exec")
        assert "ai_guru_grounded_critic_source_v2.bundle" in hydrate
        assert "ai_guru_grounded_critic_source_v2.manifest.json" in hydrate
        assert "sha256" in hydrate
        assert "SOURCE_BUNDLE.json" in hydrate
        assert "destination.write_bytes(source.read())" in hydrate
        assert "Extracted critic source bundle member SHA-256 mismatch" in hydrate
        assert "Path(entry['path']).as_posix() != entry['path']" in hydrate
        assert "SOURCE_DIRS" not in hydrate
        assert "ai_guru_source_7fab0ca.tar.gz" not in hydrate
        assert "GITHUB_TOKEN" not in hydrate


def test_qwen_kernel_binds_source_bundle_and_packet_before_inference() -> None:
    for path in NOTEBOOKS:
        run = _source(path, 3)
        compile(run, str(path), "exec")
        assert "Critic packet SHA-256 differs" in run
        assert "--source-bundle-manifest" in run
        assert "--source-bundle-archive" in run
        assert "result_manifest['inputs']['source_bundle']" in run
        assert "WANDB_DISABLED" in run
