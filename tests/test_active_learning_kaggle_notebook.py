"""Static safety checks for the portable open-source Kaggle runner."""
from __future__ import annotations

import json
from pathlib import Path
import re


NOTEBOOK = Path("notebooks/vifinqa_active_learning_open_source_cycle_v1.ipynb")


def test_kaggle_notebook_is_clean_pinned_and_fail_closed() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code = "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] == "code":
            assert cell.get("outputs") == []
            assert cell.get("execution_count") is None
            compile("".join(cell["source"]), f"{NOTEBOOK}:cell-{index}", "exec")
    source_commit = re.search(r'^SOURCE_COMMIT = "([0-9a-f]{40})"$', code, re.MULTILINE)
    assert source_commit is not None
    assert "open_source_model_proposer" in code
    assert "open_source_model_critic" in code
    assert "training_eligible_count\"] == 0" in code
    assert "release_status\"] == \"blocked\"" in code
    assert "answer_decimal" not in code
    assert "numeric_answer" not in code
    assert code.casefold().count("chatgpt") == 1
    assert 'job["chatgpt_in_model_graph"] is False' in code
