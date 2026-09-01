from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from finance_query.pipeline.submission_flow import run_submission_flow


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values = {
        "output": tmp_path / "flow",
        "verification_config": None,
        "flow_release_policy": "best_effort",
        "feedback_model_name": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_strict_flow_requires_independent_e2e_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="strict canonical flow requires"):
        run_submission_flow(
            _args(tmp_path, flow_release_policy="strict")
        )


def test_model_feedback_requires_e2e_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model feedback requires"):
        run_submission_flow(
            _args(tmp_path, feedback_model_name="test/critic")
        )


def test_best_effort_flow_requires_e2e_or_explicit_skip(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires --verification-config"):
        run_submission_flow(_args(tmp_path))
