from __future__ import annotations

import json
from pathlib import Path

from finance_query.research.uncomputed_e2e_triage import (
    SOURCE_CONTRACT,
    _primary_blocker,
    build_uncomputed_e2e_triage,
    validate_uncomputed_e2e_triage,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _packet(question_id: int, *, route_status: str, binding_status: str, reasons: list[str]) -> dict[str, object]:
    return {
        "question_id": question_id,
        "route_status": route_status,
        "binding_packet_status": binding_status,
        "stages": [{"required_operands": [{"reason_codes": reasons}]}],
    }


def test_triage_separates_entity_question_gap_route_gap_and_period_gap(tmp_path: Path) -> None:
    plans = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans,
        [
            {
                "question_id": 1,
                "decomposition_status": "complete",
                "reason_codes": [],
                "entities": ["AAA"],
                "operands": [{"entity": "AAA"}],
            },
            {
                "question_id": 2,
                "decomposition_status": "abstain",
                "reason_codes": ["MISSING_ENTITY", "MISSING_OPERANDS"],
                "entities": [],
                "operands": [],
            },
            {
                "question_id": 3,
                "decomposition_status": "complete",
                "reason_codes": [],
                "entities": ["CCC"],
                "operands": [{"entity": "CCC"}],
            },
        ],
    )
    taxonomy = tmp_path / "taxonomy.jsonl"
    _write_jsonl(
        taxonomy,
        [
            {"question_id": 1, "entities_resolved": ["AAA"]},
            {"question_id": 2, "entities_resolved": []},
            {"question_id": 3, "entities_resolved": ["CCC"]},
        ],
    )
    routing = tmp_path / "routing.jsonl"
    _write_jsonl(
        routing,
        [
            {"question_id": 1, "routing_status": "ROUTED", "table_candidate_count": 3},
            {"question_id": 2, "routing_status": "NOT_ROUTED", "table_candidate_count": 0},
            {"question_id": 3, "routing_status": "ROUTED", "table_candidate_count": 2},
        ],
    )
    hybrid = tmp_path / "hybrid.jsonl"
    _write_jsonl(
        hybrid,
        [
            {"question_id": 1, "priority_bucket": "standard_review"},
            {"question_id": 3, "priority_bucket": "agreement_high_proxy"},
        ],
    )
    period_packets = tmp_path / "period_packets.jsonl"
    _write_jsonl(
        period_packets,
        [
            {"question_id": 1, "packet_status": "packet_blocked"},
            {"question_id": 2, "packet_status": "route_blocked"},
            {"question_id": 3, "packet_status": "ambiguous_period_columns"},
        ],
    )
    bindings = tmp_path / "bindings.jsonl"
    _write_jsonl(
        bindings,
        [
            _packet(1, route_status="route_incomplete", binding_status="route_incomplete", reasons=["ROUTE_INCOMPLETE"]),
            _packet(2, route_status="route_incomplete", binding_status="route_incomplete", reasons=["ROUTE_INCOMPLETE"]),
            _packet(3, route_status="route_complete", binding_status="binding_blocked", reasons=["PERIOD_CANDIDATE_BLOCKED"]),
        ],
    )
    execution = tmp_path / "execution.jsonl"
    _write_jsonl(
        execution,
        [
            {"question_id": 1, "execution_status": "route_incomplete"},
            {"question_id": 2, "execution_status": "route_incomplete"},
            {"question_id": 3, "execution_status": "binding_conflict"},
        ],
    )
    evidence = tmp_path / "evidence.jsonl"
    _write_jsonl(
        evidence,
        [
            {"question_id": 1, "evidence_binding": {"binding_status": "BLOCKED"}},
            {"question_id": 3, "evidence_binding": {"binding_status": "BLOCKED"}},
        ],
    )
    certificates = tmp_path / "certificates.jsonl"
    _write_jsonl(
        certificates,
        [
            {"question_id": question_id, "answer_certificate": {"status": "ABSTAIN"}}
            for question_id in range(1, 4)
        ],
    )
    output = tmp_path / "triage"

    summary = build_uncomputed_e2e_triage(
        plans_path=plans,
        taxonomy_path=taxonomy,
        routing_status_path=routing,
        hybrid_review_queue_path=hybrid,
        period_packets_path=period_packets,
        bindings_path=bindings,
        execution_path=execution,
        evidence_bindings_path=evidence,
        certificates_path=certificates,
        output_dir=output,
        expected_question_count=3,
    )

    rows = {
        row["question_id"]: row
        for row in map(json.loads, (output / "question_triage_v1.jsonl").read_text(encoding="utf-8").splitlines())
    }
    assert rows[1]["primary_blocker"] == "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"
    assert rows[1]["source_candidate_signal"] == "CANDIDATES_REQUIRE_ROW_COLUMN_CHECK"
    assert rows[2]["primary_blocker"] == "ENTITY_NOT_RESOLVED_FROM_QUESTION"
    assert rows[2]["repair_action"] == "KEEP_ABSTAIN_AND_REQUEST_OR_DEFINE_ENTITY_SCOPE"
    assert rows[3]["primary_blocker"] == "PERIOD_COLUMN_AMBIGUITY"
    assert rows[3]["repair_action"] == "REPAIR_PERIOD_HEADER_AND_COMPARATIVE_COLUMN_BINDING"
    assert all(row["source_contract"] == SOURCE_CONTRACT for row in rows.values())
    handoff = [
        json.loads(line)
        for line in (output / "route_repair_handoff_v1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {row["source_navigation"]["question_id"] for row in handoff} == {1, 3}
    assert all(row["exact_row_and_column_unresolved"] is True for row in handoff)
    assert all("raw_value" not in row for row in handoff)
    assert summary["not_computed_count"] == 3
    assert summary["route_repair_handoff_count"] == 2
    assert validate_uncomputed_e2e_triage(output, expected_question_count=3)["status"] == "PASS"


def test_packet_blocked_is_a_navigation_gap_not_a_period_ambiguity() -> None:
    blocker, layer, action = _primary_blocker(
        plan={"decomposition_status": "complete", "reason_codes": []},
        taxonomy={"entities_resolved": ["AAA"]},
        period_packet={"packet_status": "packet_blocked"},
        binding_packet={
            "route_status": "route_complete",
            "stages": [{"required_operands": [{"reason_codes": ["PERIOD_CANDIDATE_BLOCKED"]}]}],
        },
        execution={"execution_status": "binding_conflict"},
        routing={"routing_status": "ROUTED"},
    )

    assert blocker == "SOURCE_NAVIGATION_PACKET_MISSING"
    assert layer == "document_retrieval"
    assert action == "CREATE_SOURCE_NAVIGATION_PACKET_FOR_ROUTE_COMPLETE"
