"""Centralized repository paths for all data-processing scripts.

Edit only this file when the local data layout changes.
"""

from __future__ import annotations

from pathlib import Path


# Repository root: <repo>/data/process/project_paths.py -> <repo>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Canonical project directories used by all new downloads and generated files.
DATA_DIR = PROJECT_ROOT / "data"
VIFINQA_DIR = DATA_DIR / "ViFinQA"
FINANCIAL_STATEMENTS_DIR = VIFINQA_DIR / "financial_statements"
QUESTIONS_PATH = VIFINQA_DIR / "questions" / "questions.jsonl"
AUDIT_OUTPUT_DIR = DATA_DIR / "process" / "audit_output"

# Compatibility with the old downloader, which created data/data/ViFinQA.
LEGACY_VIFINQA_DIR = DATA_DIR / "data" / "ViFinQA"


def dataset_is_complete(dataset_dir: Path) -> bool:
    """Return True when both questions and financial reports are present."""
    return (
        (dataset_dir / "questions" / "questions.jsonl").is_file()
        and (dataset_dir / "financial_statements").is_dir()
    )


def resolve_vifinqa_dir() -> Path:
    """Resolve the best existing dataset directory.

    Priority:
    1. complete canonical dataset at ``data/ViFinQA``;
    2. complete legacy dataset at ``data/data/ViFinQA``;
    3. existing canonical directory;
    4. existing legacy directory;
    5. canonical path for a future download.
    """
    if dataset_is_complete(VIFINQA_DIR):
        return VIFINQA_DIR
    if dataset_is_complete(LEGACY_VIFINQA_DIR):
        return LEGACY_VIFINQA_DIR
    if VIFINQA_DIR.exists():
        return VIFINQA_DIR
    if LEGACY_VIFINQA_DIR.exists():
        return LEGACY_VIFINQA_DIR
    return VIFINQA_DIR


def resolve_financial_statements_dir() -> Path:
    return resolve_vifinqa_dir() / "financial_statements"


def resolve_questions_path() -> Path:
    return resolve_vifinqa_dir() / "questions" / "questions.jsonl"
