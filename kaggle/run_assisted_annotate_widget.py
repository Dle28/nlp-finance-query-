#!/usr/bin/env python3
"""Kaggle/Jupyter launcher for evidence-assisted annotation.

Run from the existing live Kaggle session; no rebuild/restart is required.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
KAGGLE_ROOT = REPO_ROOT / "kaggle"
TARGET = KAGGLE_ROOT / "assisted_annotate_widget.py"

for path in (str(SRC_ROOT), str(KAGGLE_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

if not TARGET.is_file():
    raise FileNotFoundError(f"Assisted annotation widget not found: {TARGET}")

sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")
