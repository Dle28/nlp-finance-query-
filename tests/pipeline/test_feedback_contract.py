from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.pipeline.context.prompt_profiles import (
    COMPACT_PROMPT_PROFILE,
    DEFAULT_PROMPT_PROFILE,
    ONE_JSON_OBJECT_DIRECTIVE,
    render_prompt,
)
from finance_query.pipeline.feedback.blocked import (
    parse_feedback_response,
    run_blocked_feedback,
    validate_feedback_response,
)


TABLE_UID = "a" * 64
OTHER_TABLE_UID = "b" * 64


def _packet() -> dict[str, object]:
    return {"question_id": 1, "packet_id": "packet-1", "allowed_source_refs": [TABLE_UID]}


def _valid_response() -> dict[str, object]:
    return {
        "decision": "FEEDBACK_ONLY",
        "failure_class": "CONTEXT_INCOMPLETE",
        "reason_codes": ["BLOCKED_BY_SEMANTIC_BINDING"],
        "observations": ["The packet does not independently bind the requested period."],
        "missing_context_fields": ["period"],
        "recommended_actions": [
            {
                "action": "HYDRATE_CONTEXT_FIELD",
                "target": "context.period",
                "rationale": "Hydrate the exact period label before the next proposal run.",
            }
        ],
        "confidence": "MEDIUM",
        "source_ref_sha256": [TABLE_UID],
    }


@pytest.mark.parametrize(
    "serialized",
    [
        lambda value: json.dumps(value, ensure_ascii=False),
        lambda value: f"```json\n{json.dumps(value, ensure_ascii=False)}\n```",
        lambda value: (
            "The contract response is below.\n"
            + json.dumps(value, ensure_ascii=False)
            + "\nNo further action is authorized."
        ),
    ],
    ids=["plain-json", "json-code-fence", "harmless-leading-trailing-text"],
)
def test_parse_feedback_response_accepts_one_safe_json_object(serialized) -> None:
    response = _valid_response()

    parsed = parse_feedback_response(serialized(response))

    assert parsed == response
    assert validate_feedback_response(parsed, packet=_packet())["decision"] == "FEEDBACK_ONLY"


@pytest.mark.parametrize(
    "raw_response",
    [
        '{"decision":"ABSTAIN",',
        "prefix {\"broken\":} suffix",
        json.dumps(_valid_response(), ensure_ascii=False)
        + "\n"
        + json.dumps(_valid_response(), ensure_ascii=False),
    ],
    ids=["malformed-json", "malformed-object-with-valid-looking-braces", "multiple-json-objects"],
)
def test_parse_feedback_response_rejects_malformed_or_multiple_objects(raw_response: str) -> None:
    with pytest.raises(ValueError, match="JSON object|structured delimiters"):
        parse_feedback_response(raw_response)


def test_parse_feedback_response_rejects_unclosed_or_non_json_fence() -> None:
    raw_response = "```json\n" + json.dumps(_valid_response()) + "\n"

    with pytest.raises(ValueError, match="code fence"):
        parse_feedback_response(raw_response)


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "answer_decimal",
        "source_value",
        "numeric_value",
        "value",
        "authority",
        "promotion_allowed",
    ],
)
def test_feedback_contract_rejects_numeric_answer_source_and_authority_keys(
    forbidden_key: str,
) -> None:
    response = _valid_response() | {forbidden_key: "must not cross the boundary"}

    with pytest.raises(ValueError, match="authority boundary"):
        validate_feedback_response(response, packet=_packet())
    with pytest.raises(ValueError, match="authority boundary"):
        parse_feedback_response(json.dumps(response, ensure_ascii=False))


def test_feedback_contract_rejects_unknown_keys() -> None:
    response = _valid_response() | {"unexpected_model_field": "not in contract"}

    with pytest.raises(ValueError, match="keys differ from contract"):
        validate_feedback_response(response, packet=_packet())

    with pytest.raises(ValueError, match="keys differ from contract"):
        parse_feedback_response(json.dumps(response, ensure_ascii=False))


@pytest.mark.parametrize("source_ref", [OTHER_TABLE_UID, "not-a-sha256", "A" * 64])
def test_feedback_contract_rejects_invalid_source_hash(source_ref: str) -> None:
    response = _valid_response() | {"source_ref_sha256": [source_ref]}

    with pytest.raises(ValueError, match="source reference"):
        validate_feedback_response(response, packet=_packet())


def test_prompt_uses_compact_v2_and_requires_one_json_object() -> None:
    packet = {
        "schema_version": 1,
        "protocol": "vifinqa_context_packet_v1",
        "question_id": 1,
        "question_raw_vi": "Doanh thu năm 2022 là bao nhiêu?",
        "allowed_source_refs": [TABLE_UID],
        "authority_boundary": "feedback_only_non_authorizing",
    }

    assert DEFAULT_PROMPT_PROFILE == COMPACT_PROMPT_PROFILE == "en_system_vi_context_compact_v2"
    prompt = render_prompt(packet, profile_name=DEFAULT_PROMPT_PROFILE)
    assert ONE_JSON_OBJECT_DIRECTIVE in prompt
    assert "chỉ một JSON object" in prompt
    assert "Do not add keys" in prompt


def test_runner_accepts_wrapped_raw_model_output_without_authority_upgrade(
    tmp_path: Path,
) -> None:
    # Reuse the complete fixture so this test exercises the same runner path as
    # the existing feedback integration test, including artifact emission.
    from test_context_and_feedback import _submission_fixture

    submission_dir = _submission_fixture(tmp_path / "submission")
    response = _valid_response()

    def fake_model(_: str) -> str:
        return "```json\n" + json.dumps(response, ensure_ascii=False) + "\n```"

    result = run_blocked_feedback(
        submission_dir=submission_dir,
        output_dir=tmp_path / "feedback",
        model=fake_model,
        model_id="test/wrapped-feedback-model",
        prompt_profile=DEFAULT_PROMPT_PROFILE,
    )

    assert result.valid_count == 1
    record = json.loads(
        (result.output_dir / "blocked_question_feedback_v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert record["model_status"] == "VALID_FEEDBACK"
    assert record["prompt_profile"] == DEFAULT_PROMPT_PROFILE
    assert record["answer_authorized"] is False
    assert record["evidence_authorized"] is False
    assert record["training_eligible"] is False
    assert record["promotion_allowed"] is False
