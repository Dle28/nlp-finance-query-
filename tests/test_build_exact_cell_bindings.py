from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_builder_help_lists_overlay_as_opt_in() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "scripts/build_exact_cell_unit_bindings.py", "--help"], cwd=root, check=True, capture_output=True, text=True)
    assert "--approved-repairs" in result.stdout
    assert "--repairs-manifest" in result.stdout
