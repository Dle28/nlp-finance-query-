from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.research.population_feedback_breakthrough import (
    FORBIDDEN_OUTPUT_KEYS,
    _normalize_e2e_reason,
    build_population_feedback_breakthrough,
    semantic_reason_family,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _feedback_row(qid: int, packet_id: str, status: str) -> dict[str, object]:
    return {
        "question_id": qid,
        "packet_id": packet_id,
        "model_status": status,
        "prompt_profile": "en_system_vi_context_compact_v2",
        "feedback": {
            "decision": "ABSTAIN",
            "failure_class": "ABSTAIN",
            "reason_codes": ["MODEL_UNCERTAIN" if status == "MODEL_NOT_RUN" else "MODEL_OUTPUT_INVALID"],
        },
    }


def test_semantic_reason_family_is_reusable_not_question_specific() -> None:
    assert semantic_reason_family("PROVISION_ROW_CONFLICT") == "METRIC_ROW_BINDING"
    assert semantic_reason_family("COLUMN_PERIOD_YEAR_MISMATCH") == "TEMPORAL_BINDING"
    assert semantic_reason_family("TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING") == "FORMULA_TOTAL_OPERAND"
    assert semantic_reason_family("PERCENTAGE_CELL_FOR_AMOUNT_QUERY") == "UNIT_SCALE"
    assert semantic_reason_family("UNQUALIFIED_RELATED_PARTY_TABLE") == "ENTITY_SCOPE_PROVENANCE"
    assert _normalize_e2e_reason("FORMULA_CONFLICT:q1001:stage:one") == "FORMULA_CONFLICT:stage:one"
    assert _normalize_e2e_reason("FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION:q1001:stage:x:role:y") == "FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION"
    assert _normalize_e2e_reason("q1001:stage:stage_1_assets:role:assets:BINDING_FIELD_NOT_PASS:unit_status") == "BINDING_FIELD_NOT_PASS"


def test_builds_full_population_ledger_and_primary_queue(tmp_path: Path) -> None:
    feedback_dir = tmp_path / "feedback"
    prior_dir = tmp_path / "prior"
    feedback_dir.mkdir()
    prior_dir.mkdir()
    packet_ids = [f"packet-{qid}" for qid in range(1, 4)]
    packets = [
        {
            "question_id": qid,
            "packet_id": packet_ids[qid - 1],
            "typed_question": {
                "plan_shape": "multi_operand_plan" if qid == 1 else "single_cell_lookup",
                "plan_status": "CANDIDATE_GROUPS_COVERED",
                "research_route_status": (
                    "composed_execution_required" if qid == 1 else "route_incomplete"
                ),
                "reporting_scope": "separate",
                "source_scope": {"source_scope_status": "NOT_MATERIALIZED_IN_PACKET_INPUT"},
            },
            "proposal": {"answer_route": "program_aggregate_total_v1"},
            "submission_status": {"verification_class": "PARTIAL"},
            "blocked_reason_codes": ["CHECK_NOT_PASS:semantic_binding:UNRESOLVED"],
        }
        for qid in range(1, 4)
    ]
    _write_jsonl(feedback_dir / "blocked_question_context_packets_v1.jsonl", packets)
    _write_jsonl(
        feedback_dir / "blocked_question_feedback_v1.jsonl",
        [_feedback_row(qid, packet_ids[qid - 1], "MODEL_NOT_RUN") for qid in range(1, 4)],
    )
    _write_json(
        feedback_dir / "feedback_summary.json",
        {
            "status": "PREPARED_MODEL_FEEDBACK_NOT_RUN",
            "blocked_count": 3,
            "feedback_record_count": 3,
            "model_evaluated_count": 0,
            "valid_feedback_count": 0,
            "model_status_counts": {"MODEL_NOT_RUN": 3},
            "failure_class_counts": {"ABSTAIN": 3},
            "answer_authorized": False,
            "evidence_authorized": False,
            "training_eligible": False,
            "promotion_allowed": False,
            "submission_eligible": False,
            "release_authorized": False,
        },
    )
    _write_json(
        feedback_dir / "blocked_feedback_run.manifest.json",
        {"authority": {"real_model_invoked": False}},
    )
    _write_jsonl(
        prior_dir / "blocked_question_feedback_v1.jsonl",
        [_feedback_row(qid, f"old-packet-{qid}", "MODEL_OUTPUT_INVALID") for qid in range(1, 4)],
    )
    _write_json(
        prior_dir / "feedback_summary.json",
        {
            "status": "COMPLETED_MODEL_FEEDBACK_NON_AUTHORIZING",
            "blocked_count": 3,
            "feedback_record_count": 3,
            "model_evaluated_count": 0,
            "valid_feedback_count": 0,
            "model_output_invalid_count": 3,
            "model_status_counts": {"MODEL_OUTPUT_INVALID": 3},
            "failure_class_counts": {"ABSTAIN": 3},
            "answer_authorized": False,
            "evidence_authorized": False,
            "training_eligible": False,
            "promotion_allowed": False,
            "submission_eligible": False,
            "release_authorized": False,
        },
    )
    _write_json(
        prior_dir / "model_run_manifest.json",
        {"authority": {"real_model_invoked": True}, "model": {"id": "test", "revision": "r1"}},
    )
    semantic_summary = tmp_path / "semantic_summary.json"
    _write_json(
        semantic_summary,
        {
            "protocol": "vifinqa_semantic_contract_audit_v1",
            "records": [
                {
                    "question_id": 1,
                    "semantic_contract_status": "SEMANTIC_ABSTAIN",
                    "semantic_rejection_reason_codes": ["PROVISION_ROW_CONFLICT"],
                    "semantic_rejection_count": 4,
                }
            ],
            "counts": {
                "status_counts": {"SEMANTIC_ABSTAIN": 1},
                "semantic_abstain_count": 1,
                "semantic_candidate_survived_count": 0,
                "guard_rejection_event_count": 4,
                "rejection_reason_counts": {"PROVISION_ROW_CONFLICT": 4},
            },
            "policy": {"accuracy_measured": False},
        },
    )
    output_dir = tmp_path / "output"
    summary = build_population_feedback_breakthrough(
        feedback_dir=feedback_dir,
        prior_model_dir=prior_dir,
        semantic_summary_path=semantic_summary,
        output_dir=output_dir,
        expected_question_count=3,
    )
    assert summary["status"] == "COMPLETE_DIAGNOSTIC"
    assert summary["population_counts"]["processed_records"] == 3
    assert summary["population_counts"]["source_scope_not_materialized_count"] == 3
    assert summary["feedback_transport"]["current"]["valid_feedback_count"] == 0
    assert summary["feedback_transport"]["prior"]["model_output_invalid_count"] == 3
    assert summary["family_feedback_queue"][0]["failure_family"] == "SOURCE_BOUND_PLAN_COMPLETENESS"
    assert summary["scorer_gold"]["answer_accuracy"] == "NOT_MEASURED"
    ledger = (output_dir / "population_feedback_ledger_v1.jsonl").read_text(encoding="utf-8")
    assert "answer_decimal" not in ledger
    assert "numeric_cells" not in ledger
    for path in output_dir.iterdir():
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert not (set(payload) & FORBIDDEN_OUTPUT_KEYS)


def test_rejects_partial_population(tmp_path: Path) -> None:
    feedback_dir = tmp_path / "feedback"
    prior_dir = tmp_path / "prior"
    feedback_dir.mkdir()
    prior_dir.mkdir()
    _write_jsonl(feedback_dir / "blocked_question_context_packets_v1.jsonl", [{"question_id": 1, "packet_id": "p1"}])
    _write_jsonl(feedback_dir / "blocked_question_feedback_v1.jsonl", [{"question_id": 1, "packet_id": "p1"}])
    _write_json(feedback_dir / "feedback_summary.json", {})
    _write_jsonl(prior_dir / "blocked_question_feedback_v1.jsonl", [{"question_id": 1, "packet_id": "p1"}])
    _write_json(prior_dir / "feedback_summary.json", {})
    semantic = tmp_path / "semantic.json"
    _write_json(semantic, {"records": [], "counts": {}, "policy": {}})
    with pytest.raises(ValueError, match="population mismatch"):
        build_population_feedback_breakthrough(
            feedback_dir=feedback_dir,
            prior_model_dir=prior_dir,
            semantic_summary_path=semantic,
            output_dir=tmp_path / "out",
            expected_question_count=2,
        )
