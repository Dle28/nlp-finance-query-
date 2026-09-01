from __future__ import annotations

from finance_query.e2e.core.grounded_authorization import (
    AUTHORIZATION_CONTRACT,
    _build_binding,
    _context_is_source_aligned,
    _source_lineage_feedback,
    _strict_answer_view,
)


SOURCE_SHA256 = "a" * 64
TABLE_SHA256 = "b" * 64


def _canonical_v2_table() -> dict[str, object]:
    return {
        "document_id": "AAA_financial_statements_2023_separate",
        "internal_table_uid": "table-1",
        "source_provenance": {
            "source_sha256": SOURCE_SHA256,
            "table_sha256": TABLE_SHA256,
        },
        # The cell payload is intentionally redacted.  These tests exercise
        # only coordinates, identities and hashes, never an answer/value.
        "rows": [["Metric", "VND"], ["REDACTED_METRIC", "REDACTED_CELL"]],
    }


def _canonical_v3_context() -> dict[str, object]:
    return {
        "document_id": "AAA_financial_statements_2023_separate",
        "internal_table_uid": "table-1",
        "source_provenance": {
            "source_sha256": SOURCE_SHA256,
            "table_sha256": TABLE_SHA256,
        },
        "grid": {"rectangular": True, "provenance_complete": True},
        "quality": {"status": "review_ready"},
        "context_trace": {"source_title": "Báo cáo tài chính riêng"},
        "canonical_headers": {
            "columns": [
                {
                    "column_index": 1,
                    "source_label": "VND",
                    "header_source_cells": [{"row_index": 0, "column_index": 1}],
                    "period_labels": [],
                    "unit_labels": ["VND"],
                }
            ]
        },
    }


def _value_blind_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key
            for nested in value.values()
            for key in _value_blind_keys(nested)
        }
    if isinstance(value, list):
        return {key for nested in value for key in _value_blind_keys(nested)}
    return set()


def test_exact_v2_v3_source_link_is_reported_but_never_authorizes() -> None:
    table = _canonical_v2_table()
    context = _canonical_v3_context()

    feedback = _source_lineage_feedback(table, context)

    assert _context_is_source_aligned(table, context) is True
    assert feedback["status"] == "PASS"
    assert feedback["reason_codes"] == []
    assert feedback["may_authorize"] is False
    assert feedback["diagnostic_only"] is True
    assert feedback["raw_source_hash_recomputed"] is False
    assert feedback["external_full_table_assets_comparison"] == (
        "NOT_AVAILABLE_IN_AUTHORIZATION_INPUT"
    )
    assert {"answer", "answer_decimal", "value", "gold", "model", "research"}.isdisjoint(
        _value_blind_keys(feedback)
    )


def test_v2_v3_hash_mismatch_is_a_blocker_not_a_repair() -> None:
    table = _canonical_v2_table()
    context = _canonical_v3_context()
    context["source_provenance"] = {
        "source_sha256": SOURCE_SHA256,
        "table_sha256": "c" * 64,
    }

    feedback = _source_lineage_feedback(table, context)

    assert _context_is_source_aligned(table, context) is False
    assert feedback["status"] == "BLOCKED"
    assert "SOURCE_CLOSURE_TABLE_SHA256_MISMATCH" in feedback["reason_codes"]
    assert feedback["may_authorize"] is False


def test_full_asset_top_level_hashes_are_not_silently_treated_as_canonical_v2() -> None:
    full_asset_shape = {
        "document_id": "AAA_financial_statements_2023_separate",
        "internal_table_uid": "table-1",
        "source_sha256": SOURCE_SHA256,
        "table_sha256": TABLE_SHA256,
    }

    feedback = _source_lineage_feedback(full_asset_shape, _canonical_v3_context())

    assert feedback["status"] == "BLOCKED"
    assert "SOURCE_CLOSURE_CANONICAL_V2_PROVENANCE_SCHEMA_MISSING" in feedback[
        "reason_codes"
    ]
    assert "BINDING_LINEAGE_DOCUMENT_SHA256_INVALID" in feedback["reason_codes"]
    assert "BINDING_LINEAGE_TABLE_SHA256_INVALID" in feedback["reason_codes"]
    assert feedback["external_full_table_assets_comparison"] == (
        "NOT_AVAILABLE_IN_AUTHORIZATION_INPUT"
    )


def test_missing_canonical_v2_table_emits_required_data_receipt_and_stays_blocked() -> None:
    binding = _build_binding(
        question={"question_id": 12, "question_context": {}},
        stage={"stage_id": "stage-1"},
        operand={"role": "reported", "binding_status": "binding_blocked"},
        tables={},
        evidence_contexts={},
    )

    feedback = binding["binding_lineage"]["source_lineage_feedback"]

    assert binding["binding_status"] == "BLOCKED"
    assert binding["operand_eligible"] is False
    assert feedback["status"] == "BLOCKED"
    assert "SOURCE_CLOSURE_CANONICAL_V2_TABLE_MISSING" in feedback["reason_codes"]
    assert "canonical_v2.internal_table_uid" in feedback["required_fields"]
    assert "canonical_v3.source_provenance.table_sha256" in feedback["required_fields"]
    assert feedback["may_authorize"] is False
    assert "BINDING_LINEAGE_DOCUMENT_SHA256_INVALID" in binding["reason_codes"]
    assert "BINDING_LINEAGE_RAW_CELL_SHA256_INVALID" in binding["reason_codes"]
    assert "BINDING_LINEAGE_TABLE_SHA256_INVALID" in binding["reason_codes"]


def test_exact_source_link_does_not_clear_unresolved_variable_or_entity_fields() -> None:
    table = _canonical_v2_table()
    context = _canonical_v3_context()
    binding = _build_binding(
        question={
            "question_id": 13,
            "question_context": {"entities": ["AAA"], "scope": "separate"},
        },
        stage={"stage_id": "stage-1"},
        operand={
            "role": "reported",
            "internal_table_uid": "table-1",
            "binding_status": "binding_ready",
            "row_index": 1,
            "column_index": 1,
            "header_source_cells": [{"row_index": 0, "column_index": 1}],
            "source_unit": "VND",
            "source_to_vnd_multiplier": "1",
        },
        tables={"table-1": table},
        evidence_contexts={"table-1": context},
    )

    feedback = binding["binding_lineage"]["source_lineage_feedback"]

    assert feedback["status"] == "PASS"
    assert binding["field_statuses"]["source_integrity_status"] == "PASS"
    assert binding["field_statuses"]["entity_status"] == "UNRESOLVED"
    assert binding["field_statuses"]["variable_status"] == "UNRESOLVED"
    assert binding["binding_status"] == "BLOCKED"
    assert binding["operand_eligible"] is False


def test_abstain_certificate_keeps_prediction_out_of_legacy_answer_field() -> None:
    certificate = {
        "answer_certificate_id": "ignored-in-view",
        "status": "ABSTAIN",
        "answer_authorized": False,
        "answer": "123.45",
        "answer_status": "PREDICTED_CANDIDATE",
        "candidate_prediction": {
            "candidate_id": "candidate-1",
            "answer_decimal": "123.45",
            "model_verified": True,
            "reviewer_approved": True,
            "gold_answer": "should-never-be-authority",
            "research_answer": "should-never-be-authority",
        },
        "execution_receipt": {
            "answer_decimal": "123.45",
            "candidate_answer_decimal": "123.45",
        },
        "abstain_reason_codes": ["BINDING_NOT_FULLY_ELIGIBLE"],
    }

    view = _strict_answer_view(certificate)

    assert view["answer"] is None
    assert view["answer_decimal"] is None
    assert view["strict_answer_decimal"] is None
    assert view["best_effort_candidate_decimal"] == "123.45"
    assert view["candidate_prediction"] is None
    assert view["execution_receipt"]["answer_decimal"] is None
    assert view["execution_receipt"]["candidate_answer_decimal"] is None
    assert view["answer_authorized"] is False
    assert view["strict_answer_authorized"] is False
    assert view["strict_answer_available"] is False
    assert view["release_authorized"] is False
    assert view["submission_eligible"] is False
    assert view["training_eligible"] is False
    assert view["promotion_allowed"] is False
    assert view["best_effort_candidate_authority"] is False
    assert view["strict_answer_status"] == "ABSTAIN"
    assert view["answer_channel"] == "BEST_EFFORT_CANDIDATE"


def test_no_candidate_abstain_has_null_answers_and_no_authority_flags() -> None:
    view = _strict_answer_view(
        {
            "status": "ABSTAIN",
            "answer_authorized": False,
            "answer": "123.45",
            "answer_status": "ABSTAIN",
            "candidate_prediction": None,
            "execution_receipt": {
                "answer_decimal": "123.45",
                "candidate_answer_decimal": "123.45",
            },
            "abstain_reason_codes": ["SOURCE_CLOSURE_CANONICAL_V2_TABLE_MISSING"],
        }
    )

    assert view["answer"] is None
    assert view["answer_decimal"] is None
    assert view["strict_answer_decimal"] is None
    assert view["best_effort_candidate_decimal"] is None
    assert view["answer_channel"] == "ABSTAIN"
    assert view["strict_answer_status"] == "ABSTAIN"
    assert view["strict_answer_authorized"] is False
    assert view["release_authorized"] is False
    assert view["submission_eligible"] is False
    assert view["training_eligible"] is False
    assert view["promotion_allowed"] is False
    assert view["execution_receipt"]["answer_decimal"] is None
    assert view["execution_receipt"]["candidate_answer_decimal"] is None


def test_complete_certificate_preserves_only_source_authorized_strict_answer() -> None:
    view = _strict_answer_view(
        {
            "status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
            "answer_authorized": True,
            "answer": "42.00",
            "answer_status": "ANSWER",
            "candidate_prediction": None,
            "execution_receipt": {"answer_decimal": "42.00"},
        }
    )

    assert view["answer"] == "42.00"
    assert view["answer_decimal"] == "42.00"
    assert view["strict_answer_decimal"] == "42.00"
    assert view["best_effort_candidate_decimal"] is None
    assert view["strict_answer_status"] == "AUTHORIZED"
    assert view["strict_answer_authorized"] is True
    assert view["strict_answer_available"] is True
    assert view["answer_channel"] == "STRICT_AUTHORIZED"
    assert view["release_authorized"] is False
    assert view["submission_eligible"] is False
    assert view["training_eligible"] is False
    assert view["promotion_allowed"] is False


def test_candidate_metadata_cannot_change_authority_contract() -> None:
    assert AUTHORIZATION_CONTRACT["evidence_eligible"] is False
    assert AUTHORIZATION_CONTRACT["evidence_binding_authority"] is False
    assert AUTHORIZATION_CONTRACT["best_effort_candidate_authority"] is False
    assert AUTHORIZATION_CONTRACT["strict_answer_authorized_by_candidate"] is False
    assert AUTHORIZATION_CONTRACT["model_metadata_authority"] is False
    assert AUTHORIZATION_CONTRACT["reviewer_metadata_authority"] is False
    assert AUTHORIZATION_CONTRACT["research_value_authority"] is False
    assert AUTHORIZATION_CONTRACT["gold_data_used"] is False
    assert AUTHORIZATION_CONTRACT["release_authorized"] is False
    assert AUTHORIZATION_CONTRACT["submission_eligible"] is False
    assert AUTHORIZATION_CONTRACT["training_eligible"] is False
    assert AUTHORIZATION_CONTRACT["promotion_allowed"] is False
