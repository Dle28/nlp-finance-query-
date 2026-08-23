from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_builder_exposes_all_required_immutable_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/build_period_column_candidate_packets.py", "--help"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    for option in (
        "--route-packets",
        "--question-routes",
        "--evidence-context",
        "--no-candidate-audit-output",
    ):
        assert option in result.stdout
