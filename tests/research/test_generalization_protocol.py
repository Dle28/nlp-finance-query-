from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from finance_query.research.generalization_protocol import validate_generalization_protocol


CONFIG = Path("configs/research/generalization_experiment_protocol_v1.json")


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_repository_protocol_passes_fail_closed_validator() -> None:
    result = validate_generalization_protocol(_config())
    assert result["status"] == "VALIDATION_PASSED"
    assert result["missing_evaluator_action"] == "STOP_BEFORE_MODEL_CHANGE"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("taxonomy_policy", "question_id_role"), "routing_feature", "tracking-only"),
        (("subset_policy", "full_dataset_requires_unseen_signal"), False, "subset invariant"),
        (("human_policy", "per_question_answer_review_forbidden"), False, "human review"),
        (("evaluator_gate", "independent_gold_or_official_evaluator_required"), False, "evaluator gate"),
        (("source_contract", "promotion_allowed"), True, "cannot authorize"),
    ],
)
def test_protocol_rejects_leakage_or_authority_downgrades(path: tuple[str, str], value: object, message: str) -> None:
    config = deepcopy(_config())
    config[path[0]][path[1]] = value
    with pytest.raises(ValueError, match=message):
        validate_generalization_protocol(config)
