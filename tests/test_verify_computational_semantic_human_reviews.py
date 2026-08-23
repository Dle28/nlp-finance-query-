from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC = ROOT / "artifacts" / "research" / "computational_semantics_v1"
SPEC = importlib.util.spec_from_file_location(
    "verify_computational_semantic_human_reviews",
    ROOT / "scripts" / "verify_computational_semantic_human_reviews_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


QUEUE_INFO = {
    "source_coordinate": (
        SEMANTIC / "source_review_v2" / "computational_semantic_source_review_queue_v1.jsonl",
        SEMANTIC / "source_review_v2" / "computational_semantic_source_review_queue_v1.manifest.json",
    ),
    "scope": (
        SEMANTIC / "scope_review_v2" / "computational_semantic_scope_review_queue_v1.jsonl",
        SEMANTIC / "scope_review_v2" / "computational_semantic_scope_review_queue_v1.manifest.json",
    ),
    "dimension": (
        SEMANTIC / "dimension_review_v1" / "computational_semantic_dimension_review_queue_v1.jsonl",
        SEMANTIC / "dimension_review_v1" / "computational_semantic_dimension_review_queue_v1.manifest.json",
    ),
}
COMPONENT_QUEUE = (
    SEMANTIC.parent / "computational_semantics_v2" / "component_source_review_v1" / "computational_semantic_component_source_review_queue_v1.jsonl",
    SEMANTIC.parent / "computational_semantics_v2" / "component_source_review_v1" / "computational_semantic_component_source_review_queue_v1.manifest.json",
)


def rows(kind: str) -> list[dict]:
    queue, _ = QUEUE_INFO[kind]
    return [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines() if line]


def base(packet: dict, kind: str, decision: str) -> dict:
    return {
        "schema_version": 1,
        "protocol": MODULE.RESPONSE_PROTOCOL,
        "review_kind": kind,
        "question_id": packet["question_id"],
        "immutable_review_context_sha256": packet["immutable_review_context_sha256"],
        "decision": decision,
        "decision_provenance": "human_verified",
        "reviewer_id": "human-1",
        "reviewed_at": "2026-08-15T00:00:00Z",
        "source_coordinates_checked": [],
        "notes": "Reviewed against the immutable packet and source-document metadata.",
        "is_blank_template": False,
        "materialization_allowed": False,
        "source_contract": packet["source_contract"],
    }


def coordinate(*, node_id: str, table: dict) -> dict:
    return {
        "node_id": node_id,
        "source_locator": f"{table['document_id']}#page=1",
        "document_id": table["document_id"],
        "internal_table_uid": table["internal_table_uid"],
        "page_no": 1,
        "row_label": "Verified source row label",
        "period_header": "Verified period header",
        "unit_label": "Verified unit label",
    }


def source_accept(packet: dict) -> dict:
    response = base(packet, "source_coordinate", "accept")
    candidates = packet["review_context"]["source_candidates"]
    response["source_coordinates_checked"] = [
        coordinate(node_id=route["node_id"], table=route["candidate_tables"][0]) for route in candidates
    ]
    response["period_unit_dimension_checked"] = {
        "period_verified": True,
        "unit_verified": True,
        "dimension_verified": True,
    }
    return response


def scope_accept(packet: dict) -> dict:
    response = base(packet, "scope", "accept")
    scope = sorted(packet["review_context"]["scope_options"])[0]
    response["proposed_scope"] = scope
    response["scope_evidence_kind"] = "report_scope_declaration"
    response["source_coordinates_checked"] = [
        coordinate(node_id=route["node_id"], table=route["candidate_tables"][0])
        for route in packet["review_context"]["scope_options"][scope]
    ]
    response["period_unit_dimension_checked"] = {
        "period_verified": True,
        "unit_verified": True,
        "dimension_verified": True,
    }
    return response


def dimension_accept(packet: dict) -> dict:
    response = base(packet, "dimension", "accept")
    diagnostics = packet["review_context"]["source_route_diagnostics"]
    table = next(
        (
            candidate
            for diagnostic in diagnostics
            for candidate in diagnostic["candidate_table_metadata"]
        ),
        None,
    )
    if table is None:
        response["source_coordinates_checked"] = [{
            "source_locator": "reviewed-report.pdf#page=1",
            "document_id": "reviewed-report",
            "page_no": 1,
            "section": "Relevant note disclosure",
        }]
    else:
        response["source_coordinates_checked"] = [{
            "source_locator": f"{table['document_id']}#page=1",
            "document_id": table["document_id"],
            "internal_table_uid": table["internal_table_uid"],
            "page_no": 1,
            "section": "Relevant note disclosure",
        }]
    dimensions = packet["review_context"]["semantic_plan"]["unmodelled_detail_dimensions"]
    response["dimension_evidence_kind"] = "report_note"
    response["proposed_dimension_contract"] = {
        "dimensions": [
            {
                "dimension_id": dimension,
                "selector_text": f"Verified selector for {dimension}",
                "representation": "note_disclosure",
            }
            for dimension in dimensions
        ],
        "requires_independent_taxonomy_change": True,
    }
    return response


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def verify_batch(tmp_path: Path, kind: str, responses: list[dict]) -> dict:
    queue, manifest = QUEUE_INFO[kind]
    response_path = tmp_path / f"{kind}.responses.jsonl"
    write_jsonl(response_path, responses)
    return MODULE.verify(
        queue=queue,
        queue_manifest=manifest,
        completed_responses=response_path,
        review_kind=kind,
        reviewer_id="human-1",
        output=tmp_path / f"{kind}.receipt.json",
    )


def test_verifier_accepts_complete_source_coordinate_batch_without_materializing(tmp_path: Path) -> None:
    result = verify_batch(tmp_path, "source_coordinate", [source_accept(packet) for packet in rows("source_coordinate")])
    assert result["counts"] == {
        "response_count": 8,
        "decision_counts": {"accept": 8},
        "semantic_amendment_request_count": 8,
    }
    assert result["semantic_amendment_application_allowed"] is False
    assert result["submission_eligible"] is False


def test_verifier_accepts_scope_and_dimension_contracts_but_rejects_unsafe_content(tmp_path: Path) -> None:
    scope_result = verify_batch(tmp_path / "scope", "scope", [scope_accept(packet) for packet in rows("scope")])
    assert scope_result["counts"]["response_count"] == 7
    dimension_responses = [dimension_accept(packet) for packet in rows("dimension")]
    dimension_result = verify_batch(tmp_path / "dimension", "dimension", dimension_responses)
    assert dimension_result["counts"]["response_count"] == 31
    assert dimension_result["semantic_amendment_application_allowed"] is False

    unsafe_responses = [dimension_accept(packet) for packet in rows("dimension")]
    unsafe_responses[0]["proposed_dimension_contract"]["answer"] = "forbidden"
    with pytest.raises(ValueError, match="does not bind immutable|unsupported fields"):
        verify_batch(tmp_path / "unsafe", "dimension", unsafe_responses)


def test_verifier_rejects_tampered_immutable_context_hash(tmp_path: Path) -> None:
    responses = [source_accept(packet) for packet in rows("source_coordinate")]
    responses[0]["immutable_review_context_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="does not bind immutable review packet"):
        verify_batch(tmp_path, "source_coordinate", responses)


def test_verifier_accepts_component_source_protocol_but_keeps_composition_non_materializable(tmp_path: Path) -> None:
    queue, manifest = COMPONENT_QUEUE
    packets = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
    response_path = tmp_path / "component.responses.jsonl"
    write_jsonl(response_path, [source_accept(packet) for packet in packets])
    result = MODULE.verify(
        queue=queue,
        queue_manifest=manifest,
        completed_responses=response_path,
        review_kind="source_coordinate",
        reviewer_id="human-1",
        output=tmp_path / "component.receipt.json",
    )
    assert result["counts"]["response_count"] == 3
    assert result["semantic_amendment_application_allowed"] is False
