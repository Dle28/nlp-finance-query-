#!/usr/bin/env python3
"""Verify completed human responses for the computational-semantics queues.

The immutable blank queue and the completed responses are intentionally
separate files.  A verified ``accept`` is only a request to amend semantic
metadata; this verifier never modifies taxonomy, table routing, source/cell
bindings, evidence, training data, execution output, or submission state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "computational_semantic_human_review_receipt_v1"
RESPONSE_PROTOCOL = "computational_semantic_human_review_response_v1"
DECISIONS = frozenset({"accept", "reject", "abstain"})
QUEUE_PROTOCOLS = {
    "source_coordinate": frozenset(
        {
            "computational_semantic_source_review_queue_v1",
            "computational_semantic_component_source_review_queue_v1",
        }
    ),
    "scope": frozenset({"computational_semantic_scope_review_queue_v1"}),
    "dimension": frozenset({"computational_semantic_dimension_review_queue_v1"}),
}
COMMON_RESPONSE_FIELDS = frozenset(
    {
        "schema_version",
        "protocol",
        "review_kind",
        "question_id",
        "immutable_review_context_sha256",
        "decision",
        "decision_provenance",
        "reviewer_id",
        "reviewed_at",
        "source_coordinates_checked",
        "notes",
        "is_blank_template",
        "materialization_allowed",
        "source_contract",
    }
)
KIND_RESPONSE_FIELDS = {
    "source_coordinate": frozenset({"period_unit_dimension_checked"}),
    "scope": frozenset({"proposed_scope", "scope_evidence_kind", "period_unit_dimension_checked"}),
    "dimension": frozenset({"proposed_dimension_contract", "dimension_evidence_kind"}),
}
COORDINATE_FIELDS = frozenset(
    {
        "node_id",
        "source_locator",
        "document_id",
        "internal_table_uid",
        "page_no",
        "section",
        "row_label",
        "row_index",
        "column_label",
        "column_index",
        "period_header",
        "unit_label",
        "dimension_selector",
    }
)
FORBIDDEN_CONTENT_KEYS = frozenset(
    {
        "answer",
        "value",
        "numeric_value",
        "raw_value",
        "formula",
        "execution",
        "execution_result",
        "result",
        "training",
        "label",
        "evidence",
        "submission",
        "promotion",
        "eligibility",
        "eligible_for_materialization",
        "materialization_allowed",
    }
)
SCOPE_EVIDENCE_KINDS = frozenset(
    {"report_scope_declaration", "report_title", "question_and_source_context"}
)
DIMENSION_EVIDENCE_KINDS = frozenset({"report_note", "schedule", "table_header", "row_label"})
DIMENSION_REPRESENTATIONS = frozenset(
    {
        "note_disclosure",
        "schedule",
        "statement_subtable",
        "segment_table",
        "counterparty_table",
        "instrument_note",
        "measurement_basis_column",
        "row_label",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def index(rows: Iterable[Mapping[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if type(value) is not int or value in output:
            raise ValueError(f"{label} has an invalid or duplicate {key}: {value!r}")
        output[value] = dict(row)
    return output


def require_hash(path: Path, expected: object, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_time(value: object, question_id: int) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"Q{question_id}: reviewed_at must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"Q{question_id}: reviewed_at is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Q{question_id}: reviewed_at lacks UTC timezone")
    return value


def walk_forbidden_content(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_CONTENT_KEYS:
                raise ValueError(f"Review response contains forbidden content key: {key}")
            walk_forbidden_content(child)
    elif isinstance(value, list):
        for child in value:
            walk_forbidden_content(child)


def require_text(value: object, label: str, question_id: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Q{question_id}: {label} must be non-empty text")
    return value


def candidate_index(packet: Mapping[str, Any], review_kind: str) -> dict[tuple[str, str], dict[str, str]]:
    context = packet.get("review_context") or {}
    output: dict[tuple[str, str], dict[str, str]] = {}
    if review_kind == "source_coordinate":
        route_groups = context.get("source_candidates") or []
    elif review_kind == "scope":
        route_groups = [route for routes in (context.get("scope_options") or {}).values() for route in routes]
    else:
        route_groups = context.get("source_route_diagnostics") or []
    for route in route_groups:
        if not isinstance(route, Mapping):
            raise ValueError(f"Q{packet.get('question_id')}: malformed immutable source route")
        node_id = str(route.get("node_id") or "")
        table_field = "candidate_tables" if review_kind != "dimension" else "candidate_table_metadata"
        for table in route.get(table_field) or []:
            if not isinstance(table, Mapping):
                raise ValueError(f"Q{packet.get('question_id')}: malformed immutable candidate table")
            uid = str(table.get("internal_table_uid") or "")
            document_id = str(table.get("document_id") or "")
            if not uid or not document_id:
                raise ValueError(f"Q{packet.get('question_id')}: immutable candidate table lacks identity")
            key = (node_id, uid)
            prior = output.get(key)
            current = {"document_id": document_id}
            if prior is not None and prior != current:
                raise ValueError(f"Q{packet.get('question_id')}: candidate table identity is ambiguous")
            output[key] = current
    return output


def scope_candidate_index(packet: Mapping[str, Any], proposed_scope: str) -> dict[tuple[str, str], dict[str, str]]:
    context = packet.get("review_context") or {}
    scope_options = context.get("scope_options") or {}
    routes = scope_options.get(proposed_scope)
    if not isinstance(routes, list) or not routes:
        raise ValueError(f"Q{packet.get('question_id')}: proposed scope is absent from immutable options")
    result: dict[tuple[str, str], dict[str, str]] = {}
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError(f"Q{packet.get('question_id')}: malformed immutable scope route")
        node_id = str(route.get("node_id") or "")
        for table in route.get("candidate_tables") or []:
            if not isinstance(table, Mapping):
                raise ValueError(f"Q{packet.get('question_id')}: malformed immutable scope candidate")
            uid = str(table.get("internal_table_uid") or "")
            document_id = str(table.get("document_id") or "")
            if not uid or not document_id:
                raise ValueError(f"Q{packet.get('question_id')}: immutable scope candidate lacks identity")
            key = (node_id, uid)
            prior = result.get(key)
            current = {"document_id": document_id}
            if prior is not None and prior != current:
                raise ValueError(f"Q{packet.get('question_id')}: scope candidate table identity is ambiguous")
            result[key] = current
    return result


def require_coordinates(
    *,
    value: object,
    question_id: int,
    required_node_ids: set[str] | None,
    allowed_candidates: Mapping[tuple[str, str], Mapping[str, str]],
    require_candidate_identity: bool,
) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Q{question_id}: completed non-abstain review needs source coordinates")
    covered_nodes: set[str] = set()
    for position, coordinate in enumerate(value):
        if not isinstance(coordinate, Mapping) or set(coordinate).difference(COORDINATE_FIELDS):
            raise ValueError(f"Q{question_id}: coordinate {position} has unsupported fields")
        walk_forbidden_content(coordinate)
        require_text(coordinate.get("source_locator"), f"coordinate {position} source_locator", question_id)
        document_id = require_text(coordinate.get("document_id"), f"coordinate {position} document_id", question_id)
        for key in ("section", "row_label", "column_label", "period_header", "unit_label", "dimension_selector"):
            if key in coordinate:
                require_text(coordinate[key], f"coordinate {position} {key}", question_id)
        for key in ("page_no", "row_index", "column_index"):
            if key in coordinate and (type(coordinate[key]) is not int or coordinate[key] < 0):
                raise ValueError(f"Q{question_id}: coordinate {position} has invalid {key}")
        uid = coordinate.get("internal_table_uid")
        node_id = coordinate.get("node_id")
        if require_candidate_identity:
            if not isinstance(uid, str) or not isinstance(node_id, str) or (node_id, uid) not in allowed_candidates:
                raise ValueError(f"Q{question_id}: coordinate {position} must name an immutable candidate table")
            expected = allowed_candidates[(node_id, uid)]
            if document_id != expected["document_id"]:
                raise ValueError(f"Q{question_id}: coordinate {position} document identity does not match candidate")
            covered_nodes.add(node_id)
        elif uid is not None:
            matches = [
                candidate
                for (candidate_node_id, candidate_uid), candidate in allowed_candidates.items()
                if isinstance(uid, str) and candidate_uid == uid
            ]
            if not matches:
                raise ValueError(f"Q{question_id}: coordinate {position} names an unknown navigation candidate")
            if all(document_id != candidate["document_id"] for candidate in matches):
                raise ValueError(f"Q{question_id}: coordinate {position} document identity does not match candidate")
    if required_node_ids is not None and covered_nodes != required_node_ids:
        raise ValueError(f"Q{question_id}: accepted review must cover every immutable source route")


def require_period_unit_dimension_checks(value: object, question_id: int, *, accepted: bool) -> None:
    expected = {"period_verified", "unit_verified", "dimension_verified"}
    if not isinstance(value, Mapping) or set(value) != expected or any(type(v) is not bool for v in value.values()):
        raise ValueError(f"Q{question_id}: period_unit_dimension_checked must contain three booleans")
    if accepted and not all(value.values()):
        raise ValueError(f"Q{question_id}: accepted review requires verified period, unit and dimension")


def require_dimension_contract(value: object, packet: Mapping[str, Any], question_id: int) -> None:
    if not isinstance(value, Mapping) or set(value) != {"dimensions", "requires_independent_taxonomy_change"}:
        raise ValueError(f"Q{question_id}: proposed_dimension_contract has unsupported fields")
    if value.get("requires_independent_taxonomy_change") is not True:
        raise ValueError(f"Q{question_id}: accepted dimension contract must require independent taxonomy change")
    dimensions = value.get("dimensions")
    expected_dimensions = set(
        ((packet.get("review_context") or {}).get("semantic_plan") or {}).get("unmodelled_detail_dimensions") or []
    )
    if not isinstance(dimensions, list) or not dimensions or not expected_dimensions:
        raise ValueError(f"Q{question_id}: dimension contract has no dimensions")
    seen: set[str] = set()
    for entry in dimensions:
        if not isinstance(entry, Mapping) or set(entry) != {"dimension_id", "selector_text", "representation"}:
            raise ValueError(f"Q{question_id}: dimension contract entry has unsupported fields")
        dimension_id = require_text(entry.get("dimension_id"), "dimension_id", question_id)
        require_text(entry.get("selector_text"), "selector_text", question_id)
        if entry.get("representation") not in DIMENSION_REPRESENTATIONS:
            raise ValueError(f"Q{question_id}: dimension contract representation is unsupported")
        seen.add(dimension_id)
    if len(seen) != len(dimensions) or seen != expected_dimensions:
        raise ValueError(f"Q{question_id}: dimension contract must cover exactly the detected dimensions")
    walk_forbidden_content(value)


def validate_response(
    *,
    response: Mapping[str, Any],
    packet: Mapping[str, Any],
    review_kind: str,
    reviewer_id: str,
) -> None:
    question_id = int(packet["question_id"])
    allowed_fields = COMMON_RESPONSE_FIELDS | KIND_RESPONSE_FIELDS[review_kind]
    if (
        set(response).difference(allowed_fields)
        or response.get("schema_version") != 1
        or response.get("protocol") != RESPONSE_PROTOCOL
        or response.get("review_kind") != review_kind
        or response.get("question_id") != question_id
        or response.get("immutable_review_context_sha256") != packet.get("immutable_review_context_sha256")
        or response.get("decision") not in DECISIONS
        or response.get("decision_provenance") != "human_verified"
        or response.get("reviewer_id") != reviewer_id
        or response.get("is_blank_template") is not False
        or response.get("materialization_allowed") is not False
        or response.get("source_contract") != packet.get("source_contract")
    ):
        raise ValueError(f"Q{question_id}: response does not bind immutable review packet")
    if canonical_sha256(packet.get("review_context")) != packet.get("immutable_review_context_sha256"):
        raise ValueError(f"Q{question_id}: immutable review context hash is invalid")
    require_time(response.get("reviewed_at"), question_id)
    require_text(response.get("notes"), "notes", question_id)
    decision = str(response["decision"])
    coordinates = response.get("source_coordinates_checked")
    if decision == "abstain":
        if coordinates != []:
            raise ValueError(f"Q{question_id}: abstain must not carry source coordinates")
    if review_kind == "source_coordinate":
        if decision != "abstain":
            candidates = candidate_index(packet, review_kind)
            nodes = {
                str(route.get("node_id") or "")
                for route in ((packet.get("review_context") or {}).get("source_candidates") or [])
            }
            require_coordinates(
                value=coordinates,
                question_id=question_id,
                required_node_ids=nodes,
                allowed_candidates=candidates,
                require_candidate_identity=True,
            )
            require_period_unit_dimension_checks(
                response.get("period_unit_dimension_checked"), question_id, accepted=decision == "accept"
            )
        elif response.get("period_unit_dimension_checked") is not None:
            raise ValueError(f"Q{question_id}: abstain must not assert period/unit/dimension checks")
    elif review_kind == "scope":
        proposed_scope = response.get("proposed_scope")
        evidence_kind = response.get("scope_evidence_kind")
        if decision == "accept":
            if not isinstance(proposed_scope, str) or evidence_kind not in SCOPE_EVIDENCE_KINDS:
                raise ValueError(f"Q{question_id}: accepted scope review needs a supported scope and evidence kind")
            candidates = scope_candidate_index(packet, proposed_scope)
            required_nodes = {node_id for node_id, _ in candidates}
            require_coordinates(
                value=coordinates,
                question_id=question_id,
                required_node_ids=required_nodes,
                allowed_candidates=candidates,
                require_candidate_identity=True,
            )
            require_period_unit_dimension_checks(response.get("period_unit_dimension_checked"), question_id, accepted=True)
        elif decision == "reject":
            if proposed_scope is not None or evidence_kind is not None:
                raise ValueError(f"Q{question_id}: rejected scope review must not propose a scope")
            require_coordinates(
                value=coordinates,
                question_id=question_id,
                required_node_ids=None,
                allowed_candidates=candidate_index(packet, review_kind),
                require_candidate_identity=True,
            )
            require_period_unit_dimension_checks(response.get("period_unit_dimension_checked"), question_id, accepted=False)
        elif proposed_scope is not None or evidence_kind is not None or response.get("period_unit_dimension_checked") is not None:
            raise ValueError(f"Q{question_id}: abstain must not propose or verify a scope")
    else:
        contract = response.get("proposed_dimension_contract")
        evidence_kind = response.get("dimension_evidence_kind")
        candidates = candidate_index(packet, review_kind)
        if decision == "accept":
            if evidence_kind not in DIMENSION_EVIDENCE_KINDS:
                raise ValueError(f"Q{question_id}: accepted dimension review needs a supported evidence kind")
            require_coordinates(
                value=coordinates,
                question_id=question_id,
                required_node_ids=None,
                allowed_candidates=candidates,
                require_candidate_identity=False,
            )
            require_dimension_contract(contract, packet, question_id)
        elif decision == "reject":
            if contract is not None or evidence_kind is not None:
                raise ValueError(f"Q{question_id}: rejected dimension review must not propose a contract")
            require_coordinates(
                value=coordinates,
                question_id=question_id,
                required_node_ids=None,
                allowed_candidates=candidates,
                require_candidate_identity=False,
            )
        elif contract is not None or evidence_kind is not None:
            raise ValueError(f"Q{question_id}: abstain must not propose a dimension contract")


def verify(
    *,
    queue: Path,
    queue_manifest: Path,
    completed_responses: Path,
    review_kind: str,
    reviewer_id: str,
    output: Path,
) -> dict[str, Any]:
    """Verify one complete response batch and write a non-materializable receipt."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite computational semantic review receipt: {output}")
    if review_kind not in QUEUE_PROTOCOLS or not reviewer_id.strip():
        raise ValueError("A supported review_kind and non-empty reviewer_id are required")
    manifest = load_json(queue_manifest)
    queue_sha = require_hash(queue, ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256"), "review queue")
    queue_protocol = manifest.get("protocol")
    if (
        queue_protocol not in QUEUE_PROTOCOLS[review_kind]
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or not isinstance(manifest.get("source_contract"), Mapping)
    ):
        raise ValueError("Review queue manifest is not a blank non-materializable queue")
    queue_rows = index(load_jsonl(queue), "question_id", "review queue")
    if (
        not queue_rows
        or manifest.get("question_count") != len(queue_rows)
        or manifest.get("question_ids") != sorted(queue_rows)
    ):
        raise ValueError("Review queue manifest does not cover queue question IDs exactly")
    for question_id, packet in queue_rows.items():
        if (
            packet.get("schema_version") != 1
            or packet.get("protocol") != queue_protocol
            or packet.get("materialization_allowed") is not False
            or packet.get("source_contract") != manifest.get("source_contract")
            or canonical_sha256(packet.get("review_context")) != packet.get("immutable_review_context_sha256")
            or (packet.get("review_decision_contract") or {}).get("decision") is not None
        ):
            raise ValueError(f"Q{question_id}: immutable review queue packet is malformed")

    responses = index(load_jsonl(completed_responses), "question_id", "completed review responses")
    if set(responses) != set(queue_rows):
        raise ValueError("Completed review responses must cover the review queue exactly")
    for question_id, response in responses.items():
        validate_response(
            response=response,
            packet=queue_rows[question_id],
            review_kind=review_kind,
            reviewer_id=reviewer_id,
        )

    decision_counts = Counter(str(response["decision"]) for response in responses.values())
    receipt = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "review_kind": review_kind,
        "reviewer_id": reviewer_id,
        "inputs": {
            "queue": {"path": str(queue), "sha256": queue_sha},
            "queue_manifest": {"path": str(queue_manifest), "sha256": sha256_file(queue_manifest)},
            "completed_responses": {"path": str(completed_responses), "sha256": sha256_file(completed_responses)},
        },
        "counts": {
            "response_count": len(responses),
            "decision_counts": dict(sorted(decision_counts.items())),
            "semantic_amendment_request_count": decision_counts["accept"],
        },
        "semantic_amendment_application_allowed": False,
        "materialization_allowed": False,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "source_contract": manifest["source_contract"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--queue-manifest", type=Path, required=True)
    parser.add_argument("--completed-responses", type=Path, required=True)
    parser.add_argument("--review-kind", choices=sorted(QUEUE_PROTOCOLS), required=True)
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(**{name: value.resolve() if isinstance(value, Path) else value for name, value in vars(args).items()})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
