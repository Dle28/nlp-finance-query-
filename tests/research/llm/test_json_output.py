from __future__ import annotations

import pytest

from finance_query.research.llm.json_output import parse_one_json_object
from scripts.research.run_blocked_feedback_kaggle_v1 import extract_json


def test_parser_accepts_one_object_with_harmless_prose() -> None:
    assert parse_one_json_object('result: {"decision": "ABSTAIN"}') == {
        "decision": "ABSTAIN"
    }


def test_parser_accepts_complete_json_fence() -> None:
    assert parse_one_json_object('```json\n{"nested": {"ok": true}}\n```') == {
        "nested": {"ok": True}
    }


@pytest.mark.parametrize(
    "text, message",
    [
        ('{"first": 1} {"second": 2}', "exactly one JSON object"),
        ('noise [1, 2, 3]', "did not return a JSON object"),
        ('```python\n{"value": 1}\n```', "unsupported markdown code fence"),
        ('```json\n{"value": 1}', "unclosed markdown code fence"),
        ('{"value": NaN}', "did not return a JSON object"),
        ('{"value": 1} trailing {broken}', "extra structured delimiters"),
    ],
)
def test_parser_rejects_ambiguous_or_non_standard_output(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_one_json_object(text)


def test_kaggle_worker_uses_the_same_strict_parser() -> None:
    with pytest.raises(ValueError, match="exactly one JSON object"):
        extract_json('{"first": 1} {"second": 2}')
