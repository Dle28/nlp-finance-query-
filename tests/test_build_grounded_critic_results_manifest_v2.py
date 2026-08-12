from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_results_manifest_builder_exposes_closed_world_runtime_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/build_grounded_critic_results_manifest_v2.py", "--help"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    for option in ("--packets", "--results", "--run-mode", "--model-revision"):
        assert option in result.stdout
