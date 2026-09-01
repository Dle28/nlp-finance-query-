from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from finance_query.research.scalar_multiply_experiment import _record, validate_scalar_multiply_assignments


HYPOTHESIS = Path("configs/research/experiments/dimensionless_scalar_multiply_v1.json")


def _row(stage: str, index: int, *, metric: str = "m", composition: str = "add>multiply") -> dict:
    digest = hashlib.sha256(f"{stage}-{index}".encode()).hexdigest()
    return {
        "benchmark_stage": stage,
        "record_sha256": digest,
        "company_hash": hashlib.sha256(f"{stage}-company-{index}".encode()).hexdigest(),
        "wording_template_hash": hashlib.sha256(f"{stage}-wording-{index}".encode()).hexdigest(),
        "metric_family": metric,
        "composition_signature": composition,
        "sealed_before_evaluation": stage == "untouched_evaluation",
        "question_or_program_materialized": False,
    }


def test_assignment_validator_enforces_all_holdouts() -> None:
    hypothesis = json.loads(HYPOTHESIS.read_text(encoding="utf-8"))
    rows = [
        *[_row("discovery", index) for index in range(10)],
        *[_row("development", index) for index in range(30)],
        *[_row("untouched_evaluation", index, metric="held", composition="held>multiply") for index in range(30)],
    ]
    summary = {
        "company_overlap_counts": {"discovery_development": 0, "discovery_untouched": 0, "development_untouched": 0},
        "wording_overlap_development_untouched": 0,
        "metric_holdout": {"development_count": 0, "untouched_count": 30},
        "composition_holdout": {"development_count": 0, "untouched_count": 30},
        "selection_uses_source_record_id": False,
    }
    validate_scalar_multiply_assignments(rows, summary, hypothesis)
    bad = deepcopy(summary)
    bad["company_overlap_counts"]["development_untouched"] = 1
    with pytest.raises(ValueError, match="company"):
        validate_scalar_multiply_assignments(rows, bad, hypothesis)


def test_assignment_validator_rejects_unsealed_payload() -> None:
    hypothesis = json.loads(HYPOTHESIS.read_text(encoding="utf-8"))
    rows = [
        *[_row("discovery", index) for index in range(10)],
        *[_row("development", index) for index in range(30)],
        *[_row("untouched_evaluation", index, metric="held", composition="held>multiply") for index in range(30)],
    ]
    rows[-1]["question_or_program_materialized"] = True
    summary = {
        "company_overlap_counts": {"discovery_development": 0, "discovery_untouched": 0, "development_untouched": 0},
        "wording_overlap_development_untouched": 0,
        "metric_holdout": {"development_count": 0, "untouched_count": 30},
        "composition_holdout": {"development_count": 0, "untouched_count": 30},
        "selection_uses_source_record_id": False,
    }
    with pytest.raises(ValueError, match="materialize"):
        validate_scalar_multiply_assignments(rows, summary, hypothesis)


def test_question_id_is_tracking_only_for_record_selection() -> None:
    base = {
        "id": "AAA/2023/page_1.pdf-1",
        "filename": "AAA/2023/page_1.pdf",
        "table": [["metric", "2023"], ["revenue", "100"]],
        "qa": {"question": "What is twice revenue?", "program": "multiply(100, const_2)"},
    }
    changed_id = deepcopy(base)
    changed_id["id"] = "TRACKING-ID-CHANGED"
    first = _record(base, "train", "seed")
    second = _record(changed_id, "train", "seed")
    assert first["record_sha256"] == second["record_sha256"]
    assert first["selection_hash"] == second["selection_hash"]
    assert first["company"] == "AAA"
