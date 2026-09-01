from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from finance_query.research.external_financial_reference import (
    build_external_reference,
    validate_reference_rows,
    validate_source_config,
)


CONFIG = Path("configs/research/external_financial_reference_sources_v1.json")


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_builds_frozen_external_split_index(tmp_path: Path) -> None:
    finqa = tmp_path / "finqa"
    tatqa = tmp_path / "tatqa"
    finqa_item = {
        "id": "report-1",
        "qa": {
            "question": "What is the growth rate?",
            "program": "divide(subtract(120, 100), 100)",
            "exe_ans": 0.2,
            "gold_inds": {"table_1": "Revenue 120 100"},
        },
    }
    tatqa_item = {
        "questions": [
            {
                "uid": "q-1",
                "question": "What is the difference?",
                "answer": 20,
                "derivation": "120 - 100",
                "answer_type": "arithmetic",
                "answer_from": "table",
                "scale": "million",
            }
        ]
    }
    for split in ("train", "dev", "test"):
        _write_json(finqa / f"{split}.json", [finqa_item | {"id": f"{split}-report"}])
    for split in ("train", "dev", "test_gold"):
        _write_json(tatqa / f"tatqa_dataset_{split}.json", [tatqa_item])
    rows, report = build_external_reference(
        finqa_root=finqa,
        tatqa_root=tatqa,
        source_config=_config(),
    )
    assert len(rows) == 6
    assert report["stage_counts"] == {
        "development": 2,
        "discovery": 2,
        "untouched_evaluation": 2,
    }
    assert report["operator_counts"]["divide"] == 3
    assert all(row["source_contract"]["submission_eligible"] is False for row in rows)


def test_rejects_split_drift_and_vifinqa_content() -> None:
    row = {
        "reference_id": "r1",
        "source_dataset": "finqa",
        "source_split": "train",
        "benchmark_stage": "development",
        "question": "external question",
        "reasoning_target": {"expected_answer": 1},
        "source_locator": "data/ViFinQA/questions/questions.jsonl#id=1",
        "source_contract": {
            "research_only": True,
            "training_eligible": False,
            "evidence_eligible": False,
            "may_materialize_vifinqa_answer": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    with pytest.raises(ValueError, match="split mapping"):
        validate_reference_rows([row], _config())
    row["benchmark_stage"] = "discovery"
    with pytest.raises(ValueError, match="ViFinQA"):
        validate_reference_rows([row], _config())


def test_rejects_authority_or_firewall_downgrade() -> None:
    config = deepcopy(_config())
    config["source_contract"]["submission_eligible"] = True
    with pytest.raises(ValueError, match="cannot authorize"):
        validate_source_config(config)
    config = deepcopy(_config())
    config["firewall"]["leaderboard_feedback_allowed_for_development"] = True
    with pytest.raises(ValueError, match="firewall"):
        validate_source_config(config)
