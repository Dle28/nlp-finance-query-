"""Fail-closed human review of retrieval-metadata report context.

The report graph can connect documents only after a question has a trustworthy
issuer, period and accounting scope.  This module packages the unusually
small cohort where the first five retrieval candidates agree on that context.
Agreement is merely a review proposal: an independent reviewer must verify it
against the source document.  Neither a queue nor an approved response may
materialize a route, table cell, evidence row, answer, label or submission.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .corporate_report_graph import GRAPH_SOURCE_CONTRACT
from .corporate_report_routes import ROUTE_HINT_SOURCE_CONTRACT
from .table_structure import sha256_file


CORPORATE_REPORT_CONTEXT_REVIEW_VERSION = 1
CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL = "corporate_report_context_review_queue_v1"
CORPORATE_REPORT_CONTEXT_RESPONSE_PROTOCOL = "corporate_report_context_human_review_response_v1"
CORPORATE_REPORT_CONTEXT_RECEIPT_PROTOCOL = "corporate_report_context_human_review_receipt_v1"
CONTEXT_REVIEW_DECISIONS = frozenset(
    {"approve_question_context", "reject_question_context", "abstain"}
)
CONTEXT_REVIEW_SOURCE_CONTRACT = {
    **GRAPH_SOURCE_CONTRACT,
    "candidate_only": True,
    "materialization_allowed": False,
    "may_propose_question_context_for_review": True,
    "may_select_table_candidate": False,
    "may_select_value_cell": False,
    "may_compute_answer": False,
    "may_infer_scope": False,
    "may_promote_provenance": False,
}

_QUEUE_FIELDS = frozenset(
    {
        "schema_version",
        "protocol",
        "question_id",
        "immutable_review_context_sha256",
        "review_context",
        "review_instructions",
        "review_decision_contract",
        "materialization_allowed",
        "source_contract",
    }
)
_RESPONSE_FIELDS = frozenset(
    {
        "schema_version",
        "protocol",
        "question_id",
        "immutable_review_context_sha256",
        "decision",
        "decision_provenance",
        "reviewer_id",
        "reviewed_at",
        "reviewed_context",
        "source_documents_checked",
        "notes",
        "is_blank_template",
        "materialization_allowed",
        "source_contract",
    }
)
_FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "answer",
        "value",
        "numeric_value",
        "raw_value",
        "cell",
        "cell_coordinate",
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
    }
)


def canonical_sha256(value: object) -> str:
    """Hash a JSON value with a stable representation."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def require_hash(path: Path, expected: object, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _index(rows: Iterable[Mapping[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if type(value) is not int or value in output:
            raise ValueError(f"{label} has an invalid or duplicate {key}: {value!r}")
        output[value] = dict(row)
    return output


def _known_plan_strings(value: object, *, field: str, question_id: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"Q{question_id}: {field} in question plan is malformed")
    return sorted(set(value))


def _known_plan_years(value: object, *, question_id: int) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise ValueError(f"Q{question_id}: years in question plan is malformed")
    return sorted(set(value))


def _candidate_context(candidate: Mapping[str, Any], *, question_id: int) -> tuple[str, int, str]:
    ticker = candidate.get("ticker")
    year = candidate.get("report_year")
    scope = candidate.get("scope")
    if (
        not isinstance(ticker, str)
        or not ticker
        or type(year) is not int
        or not isinstance(scope, str)
        or scope in {"", "unknown"}
    ):
        raise ValueError(f"Q{question_id}: top-five retrieval candidate lacks complete report metadata")
    return ticker, year, scope


def _top_five_metadata_consensus(
    item: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], str] | None:
    """Return the proposal only when exactly the first five candidates agree."""
    question_id = item.get("id")
    if type(question_id) is not int:
        raise ValueError("Review item has invalid ID")
    candidates = item.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"Q{question_id}: candidates must be a list")
    ranked = sorted(
        (candidate for candidate in candidates if isinstance(candidate, Mapping)),
        key=lambda candidate: candidate.get("rank") if type(candidate.get("rank")) is int else 10**9,
    )
    if len(ranked) < 5:
        return None
    top_five = ranked[:5]
    try:
        parsed = [_candidate_context(candidate, question_id=question_id) for candidate in top_five]
    except ValueError as error:
        # Sparse retrieval metadata is precisely what this queue is not allowed
        # to repair.  Exclude the question rather than aborting a valid cohort.
        if "top-five retrieval candidate lacks complete report metadata" in str(error):
            return None
        raise
    tuple_values = set(parsed)
    if len(tuple_values) != 1:
        return None
    ticker, year, scope = next(iter(tuple_values))

    plan = item.get("question_plan") or {}
    if not isinstance(plan, Mapping):
        raise ValueError(f"Q{question_id}: question_plan must be an object")
    planned_tickers = _known_plan_strings(plan.get("tickers"), field="tickers", question_id=question_id)
    planned_years = _known_plan_years(plan.get("years"), question_id=question_id)
    planned_scope = plan.get("scope")
    if planned_scope is not None and (not isinstance(planned_scope, str) or not planned_scope):
        raise ValueError(f"Q{question_id}: scope in question plan is malformed")
    machine_plan_relation = (
        "conflicting_or_ambiguous_machine_question_plan"
        if (
        len(planned_tickers) > 1
        or len(planned_years) > 1
        or (planned_tickers and planned_tickers != [ticker])
        or (planned_years and planned_years != [year])
        or (planned_scope not in {None, "", "unknown", scope})
        )
        else "consistent_or_missing_machine_question_plan_fields"
    )
    exposed_candidates = [
        {
            "rank": candidate["rank"],
            "ticker": candidate_ticker,
            "report_year": candidate_year,
            "report_scope": candidate_scope,
        }
        for candidate, (candidate_ticker, candidate_year, candidate_scope) in zip(top_five, parsed)
    ]
    return (
        {"ticker": ticker, "report_year": year, "report_scope": scope},
        exposed_candidates,
        machine_plan_relation,
    )


def build_corporate_report_context_review_packets(
    review_items: Iterable[Mapping[str, Any]],
    report_families: Iterable[Mapping[str, Any]],
    route_hints: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Build blank packets for source-family-confirmed Top-5 consensus only."""
    families = {
        (str(row.get("issuer_ticker") or ""), row.get("report_year"), str(row.get("report_type") or "")): row
        for row in report_families
    }
    hints_by_question = _index(route_hints, "question_id", "route hints")
    packets: list[dict[str, Any]] = []
    exclusions: Counter[str] = Counter()
    for item in review_items:
        question_id = item.get("id")
        if type(question_id) is not int:
            raise ValueError("Review item has invalid ID")
        hint = hints_by_question.get(question_id)
        if hint is None or hint.get("route_hint_status") != "question_context_incomplete_or_ambiguous":
            exclusions["not_context_blocked_route_hint"] += 1
            continue
        consensus = _top_five_metadata_consensus(item)
        if consensus is None:
            exclusions["top_five_metadata_not_single_source_context"] += 1
            continue
        proposed_context, candidate_tables, machine_plan_relation = consensus
        family = families.get(
            (proposed_context["ticker"], proposed_context["report_year"], "financial_statements")
        )
        if family is None or family.get("family_status") != "source_derived_unique_scope_members":
            exclusions["source_report_family_not_unique"] += 1
            continue
        members = family.get("members")
        if not isinstance(members, list):
            raise ValueError(f"Q{question_id}: source report family members are malformed")
        source_members = [
            member
            for member in members
            if isinstance(member, Mapping)
            and member.get("report_scope") == proposed_context["report_scope"]
        ]
        if len(source_members) != 1:
            exclusions["source_scope_document_not_unique"] += 1
            continue
        source_document_id = source_members[0].get("document_id")
        if not isinstance(source_document_id, str) or not source_document_id:
            raise ValueError(f"Q{question_id}: source report family member lacks document ID")
        proposed_context = {**proposed_context, "source_document_id": source_document_id}
        other_scope_document_ids = sorted(
            str(member.get("document_id"))
            for member in members
            if isinstance(member, Mapping)
            and str(member.get("document_id") or "")
            and str(member.get("document_id")) != proposed_context["source_document_id"]
        )
        review_context = {
            "question": item.get("question"),
            "question_plan": item.get("question_plan"),
            "retrieval_metadata_consensus": {
                "top_k": 5,
                "proposed_question_context": proposed_context,
                "candidate_tables": candidate_tables,
            },
            "source_report_family": {
                "report_family_id": family.get("report_family_id"),
                "family_status": family.get("family_status"),
                "source_document_id": proposed_context["source_document_id"],
                "parallel_scope_document_ids": other_scope_document_ids,
            },
            "machine_question_plan_metadata_relation": machine_plan_relation,
            "route_hint_status": hint.get("route_hint_status"),
        }
        packets.append(
            {
                "schema_version": CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
                "protocol": CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL,
                "question_id": question_id,
                "immutable_review_context_sha256": canonical_sha256(review_context),
                "review_context": review_context,
                "review_instructions": [
                    "Open the proposed source document independently and determine the issuer, report period and accounting scope from source title and question wording.",
                    "Top-five metadata agreement is a proposal only; do not approve solely because retrieval candidates agree.",
                    "When machine QuestionPlan conflicts with or is ambiguous against the proposal, inspect question wording and source title independently; approval does not change that plan.",
                    "Approve only the exact proposed context. Reject or abstain when the question/source does not support it; do not substitute another issuer, year, scope or document.",
                    "Record no numeric value, table cell, answer, formula, execution result, label or evidence payload.",
                    "An approved context is non-materializable and must pass independent source/table, period, unit and provenance gates later.",
                ],
                "review_decision_contract": {
                    "decision": None,
                    "reviewed_context": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "source_documents_checked": None,
                    "notes": "",
                    "materialization_allowed": False,
                },
                "materialization_allowed": False,
                "source_contract": dict(CONTEXT_REVIEW_SOURCE_CONTRACT),
            }
        )
    if len({packet["question_id"] for packet in packets}) != len(packets):
        raise ValueError("Context-review queue contains duplicate question IDs")
    return sorted(packets, key=lambda packet: packet["question_id"]), exclusions


def validate_corporate_report_context_review_queue(output_dir: Path) -> dict[str, Any]:
    """Validate an immutable blank queue and its SHA-256 manifest binding."""
    output_dir = output_dir.resolve()
    queue_path = output_dir / "corporate_report_context_review_queue_v1.jsonl"
    manifest_path = output_dir / "corporate_report_context_review_queue_v1.manifest.json"
    if not queue_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Corporate report context-review queue is missing")
    manifest = load_json(manifest_path)
    if (
        manifest.get("schema_version") != CORPORATE_REPORT_CONTEXT_REVIEW_VERSION
        or manifest.get("protocol") != CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL
        or manifest.get("queue_status") != "blank_human_question_context_review"
        or manifest.get("labels_prepopulated") is not False
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != CONTEXT_REVIEW_SOURCE_CONTRACT
    ):
        raise ValueError("Context-review queue manifest has an invalid fail-closed contract")
    queue_sha = require_hash(queue_path, ((manifest.get("outputs") or {}).get("queue") or {}).get("sha256"), "queue")
    packets = _index(load_jsonl(queue_path), "question_id", "context-review queue")
    if manifest.get("question_count") != len(packets) or manifest.get("question_ids") != sorted(packets):
        raise ValueError("Context-review queue manifest does not cover question IDs exactly")
    for question_id, packet in packets.items():
        if (
            frozenset(packet) != _QUEUE_FIELDS
            or packet.get("schema_version") != CORPORATE_REPORT_CONTEXT_REVIEW_VERSION
            or packet.get("protocol") != CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL
            or packet.get("materialization_allowed") is not False
            or packet.get("source_contract") != CONTEXT_REVIEW_SOURCE_CONTRACT
            or canonical_sha256(packet.get("review_context")) != packet.get("immutable_review_context_sha256")
            or (packet.get("review_decision_contract") or {}).get("decision") is not None
        ):
            raise ValueError(f"Q{question_id}: malformed immutable context-review packet")
    return {**manifest, "queue_sha256": queue_sha}


def response_template(packet: Mapping[str, Any], *, reviewer_id: str) -> dict[str, Any]:
    """Create a separate blank response; never mutate the immutable queue."""
    if not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    return {
        "schema_version": CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
        "protocol": CORPORATE_REPORT_CONTEXT_RESPONSE_PROTOCOL,
        "question_id": packet["question_id"],
        "immutable_review_context_sha256": packet["immutable_review_context_sha256"],
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": reviewer_id,
        "reviewed_at": None,
        "reviewed_context": None,
        "source_documents_checked": [],
        "notes": "",
        "is_blank_template": True,
        "materialization_allowed": False,
        "source_contract": packet["source_contract"],
    }


def _require_utc(value: object, *, question_id: int) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"Q{question_id}: reviewed_at must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ValueError(f"Q{question_id}: reviewed_at is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"Q{question_id}: reviewed_at lacks UTC timezone")
    return value


def _walk_forbidden_response_content(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _FORBIDDEN_RESPONSE_KEYS:
                raise ValueError(f"Review response contains forbidden content key: {key}")
            _walk_forbidden_response_content(child)
    elif isinstance(value, list):
        for child in value:
            _walk_forbidden_response_content(child)


def verify_corporate_report_context_human_reviews(
    *,
    queue: Path,
    queue_manifest: Path,
    completed_responses: Path,
    reviewer_id: str,
    output: Path,
) -> dict[str, Any]:
    """Verify completed human responses and emit a non-applicable receipt."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite context-review receipt: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    queue_dir = queue.resolve().parent
    expected_queue = queue_dir / "corporate_report_context_review_queue_v1.jsonl"
    expected_manifest = queue_dir / "corporate_report_context_review_queue_v1.manifest.json"
    if queue.resolve() != expected_queue or queue_manifest.resolve() != expected_manifest:
        raise ValueError("Queue and manifest must remain together under the immutable queue path")
    manifest = validate_corporate_report_context_review_queue(queue_dir)
    packets = _index(load_jsonl(queue), "question_id", "context-review queue")
    responses = _index(load_jsonl(completed_responses), "question_id", "completed responses")
    if set(packets) != set(responses):
        raise ValueError("Completed responses must cover immutable queue question IDs exactly")
    if not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    decisions: Counter[str] = Counter()
    for question_id, packet in packets.items():
        response = responses[question_id]
        if frozenset(response) != _RESPONSE_FIELDS:
            raise ValueError(f"Q{question_id}: response fields do not match the strict contract")
        _walk_forbidden_response_content(response.get("reviewed_context"))
        if (
            response.get("schema_version") != CORPORATE_REPORT_CONTEXT_REVIEW_VERSION
            or response.get("protocol") != CORPORATE_REPORT_CONTEXT_RESPONSE_PROTOCOL
            or response.get("immutable_review_context_sha256") != packet.get("immutable_review_context_sha256")
            or response.get("source_contract") != CONTEXT_REVIEW_SOURCE_CONTRACT
            or response.get("materialization_allowed") is not False
            or response.get("is_blank_template") is not False
            or response.get("reviewer_id") != reviewer_id
            or response.get("decision_provenance") != "human_verified"
        ):
            raise ValueError(f"Q{question_id}: response does not bind the immutable human-review contract")
        _require_utc(response.get("reviewed_at"), question_id=question_id)
        decision = response.get("decision")
        if decision not in CONTEXT_REVIEW_DECISIONS:
            raise ValueError(f"Q{question_id}: unsupported context-review decision")
        if not isinstance(response.get("notes"), str):
            raise ValueError(f"Q{question_id}: notes must be text")
        source_document_id = (
            ((packet.get("review_context") or {}).get("retrieval_metadata_consensus") or {})
            .get("proposed_question_context", {})
            .get("source_document_id")
        )
        checked = response.get("source_documents_checked")
        if not isinstance(checked, list) or any(not isinstance(value, str) for value in checked):
            raise ValueError(f"Q{question_id}: source_documents_checked must be a string list")
        if decision == "approve_question_context":
            proposed = (
                ((packet.get("review_context") or {}).get("retrieval_metadata_consensus") or {})
                .get("proposed_question_context")
            )
            if response.get("reviewed_context") != proposed or checked != [source_document_id]:
                raise ValueError(f"Q{question_id}: approval must verify exactly the proposed source context")
        elif response.get("reviewed_context") is not None:
            raise ValueError(f"Q{question_id}: non-approval must not propose replacement context")
        elif any(value != source_document_id for value in checked):
            raise ValueError(f"Q{question_id}: checked document is outside the immutable proposal")
        decisions[str(decision)] += 1
    receipt = {
        "schema_version": CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
        "protocol": CORPORATE_REPORT_CONTEXT_RECEIPT_PROTOCOL,
        "reviewer_id": reviewer_id,
        "review_status": "human_context_review_verified_non_materializable",
        "question_count": len(packets),
        "decision_counts": dict(sorted(decisions.items())),
        "context_amendment_application_allowed": False,
        "materialization_allowed": False,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "inputs": {
            "queue": {"path": str(queue.resolve()), "sha256": manifest["queue_sha256"]},
            "queue_manifest": {"path": str(queue_manifest.resolve()), "sha256": sha256_file(queue_manifest)},
            "completed_responses": {"path": str(completed_responses.resolve()), "sha256": sha256_file(completed_responses)},
        },
        "source_contract": dict(CONTEXT_REVIEW_SOURCE_CONTRACT),
    }
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
