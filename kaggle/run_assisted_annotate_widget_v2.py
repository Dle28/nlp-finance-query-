#!/usr/bin/env python3
"""Kaggle/Jupyter launcher for assisted_annotate_widget_v2.py."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
KAGGLE_ROOT = REPO_ROOT / "kaggle"
TARGET = KAGGLE_ROOT / "assisted_annotate_widget_v2.py"

for path in (SRC_ROOT, KAGGLE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

if not TARGET.is_file():
    raise FileNotFoundError(f"Annotation widget not found: {TARGET}")

sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")
