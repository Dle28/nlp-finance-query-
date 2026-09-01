from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebooks/vifinqa_full_dense_index_kaggle_v1.ipynb"


def test_kaggle_notebook_is_clean_and_code_cells_compile() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert len(code_cells) == 5
    for index, cell in enumerate(code_cells):
        assert cell["execution_count"] is None
        assert cell["outputs"] == []
        compile("".join(cell["source"]), f"notebook-cell-{index}", "exec")


def test_kaggle_notebook_fails_closed_on_population_and_cuda() -> None:
    source = NOTEBOOK.read_text(encoding="utf-8")
    assert "torch.cuda.is_available()" in source
    assert "full_table_assets_v1.jsonl" in source
    assert "source_closure_v1.jsonl" in source
    assert "617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7" in source
    assert "receipt['count'] == 146246" in source
    assert "source_report_count'] == 1973" in source
    assert "zero_table_report_count'] == 8" in source
    assert "may_authorize_answer'] is False" in source
    assert "GITHUB_TOKEN" not in source
