#!/usr/bin/env python3
"""Kaggle/Jupyter launcher for ``annotate_widget.py``.

Kaggle's notebook kernel can differ from the interpreter used by ``!python`` or
``pip install -e .``.  In that case ``%run kaggle/annotate_widget.py`` may fail
with ``ModuleNotFoundError: finance_query`` even though CLI scripts work.

This launcher makes the repository's ``src`` layout importable in the current
notebook kernel, then executes the real widget script with the same CLI args.

Example::

    %run kaggle/run_annotate_widget.py \
        --questions data/labels/annotation_questions_60.jsonl \
        --config configs/annotation_baseline.yaml \
        --output data/labels/retriever_verified_60.jsonl \
        --top-k 10 \
        --preview-rows 14
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
TARGET = REPO_ROOT / "kaggle" / "annotate_widget.py"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

if not TARGET.is_file():
    raise FileNotFoundError(f"Annotation widget not found: {TARGET}")

# Preserve the arguments supplied by IPython `%run`; only argv[0] is changed so
# argparse help/errors point at the real application name.
sys.argv[0] = str(TARGET)
runpy.run_path(str(TARGET), run_name="__main__")
