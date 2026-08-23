from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_builder_exposes_immutable_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/build_grounding_adjudication_queues.py", "--help"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    for option in ("--period-packets", "--no-candidate-audit", "--structured-tables", "--output-dir"):
        assert option in result.stdout
