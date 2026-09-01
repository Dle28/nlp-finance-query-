from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebooks/vifinqa_section_hierarchical_rag_kaggle_v1.ipynb"


def test_section_rag_kaggle_notebook_is_clean_and_code_cells_compile() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert len(code_cells) == 6
    for index, cell in enumerate(code_cells):
        assert cell["execution_count"] is None
        assert cell["outputs"] == []
        compile("".join(cell["source"]), f"notebook-cell-{index}", "exec")


def test_section_rag_kaggle_notebook_fails_closed_on_contract_and_authorization() -> None:
    source = NOTEBOOK.read_text(encoding="utf-8")
    assert "torch.cuda.is_available()" in source
    assert "section_chunk_assets_v1.jsonl" in source
    assert "section_source_closure_v1.jsonl" in source
    assert "section_artifact_manifest_v1.json" in source
    assert "section_rag_table_baseline_manifest_v1.json" in source
    assert "BASELINE_INPUT_DIR" in source
    assert "BUNDLED_PROJECT" in source
    assert "CODE_BUNDLE is not None" in source
    assert "b39952adbe989f3490398c1878c3ad56c7425406a1695ede7a04b386936f574b" in source
    assert "receipt['count'] == 28924" in source
    assert "receipt['resolved_device'] == 'cuda'" in source
    assert "may_authorize_answer'] is False" in source
    assert "NOT_AVAILABLE_WITHOUT_INDEPENDENT_SOURCE_ADJUDICATED_GOLD" in source
    assert "GITHUB_TOKEN" not in source
