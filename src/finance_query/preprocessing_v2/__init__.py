"""Fail-closed canonical preprocessing for ViFinQA table assets."""

from .pipeline import PreprocessingResult, run_preprocessing
from .text_repair import RepairRule, normalize_canonical_text

__all__ = [
    "PreprocessingResult",
    "RepairRule",
    "normalize_canonical_text",
    "run_preprocessing",
]
