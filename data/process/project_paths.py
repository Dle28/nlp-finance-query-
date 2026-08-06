"""Centralized repository paths for all data-processing scripts.

Edit only this file when the local data layout changes.
"""

from __future__ import annotations

from pathlib import Path


# Repository root: <repo>/data/process/project_paths.py -> <repo>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Canonical project directories.
DATA_DIR = PROJECT_ROOT / "data"
VIFINQA_DIR = DATA_DIR / "ViFinQA"
FINANCIAL_STATEMENTS_DIR = VIFINQA_DIR / "financial_statements"
QUESTIONS_PATH = VIFINQA_DIR / "questions" / "questions.jsonl"
AUDIT_OUTPUT_DIR = DATA_DIR / "process" / "audit_output"

# Temporary compatibility with the old downloader, which created data/data/ViFinQA.
LEGACY_VIFINQA_DIR = DATA_DIR / "data" / "ViFinQA"


def resolve_vifinqa_dir() -> Path:
    """Return the canonical dataset directory, or the legacy one if needed.

    New downloads always use ``data/ViFinQA``. The fallback prevents an
    existing local corpus under ``data/data/ViFinQA`` from breaking abruptly.
    """
    if VIFINQA_DIR.exists() or not LEGACY_VIFINQA_DIR.exists():
        return VIFINQA_DIR
    return LEGACY_VIFINQA_DIR


def resolve_financial_statements_dir() -> Path:
    return resolve_vifinqa_dir() / "financial_statements"


def resolve_questions_path() -> Path:
    return resolve_vifinqa_dir() / "questions" / "questions.jsonl"
