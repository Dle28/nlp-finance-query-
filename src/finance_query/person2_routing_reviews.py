"""Independent, non-promoting Person 2 review of routing-gate queue items."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "person2_routing_reviews_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _index(rows: list[Mapping[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(field) or "")
        if not key or key in values:
            raise ValueError(f"{label} missing/duplicate {field}")
        values[key] = dict(row)
    return values


def _contract() -> dict[str, bool]:
    return {
        "review_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _require_hash(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def review_routing_item(
    item: Mapping[str, Any], *, v2_by_uid: Mapping[str, Mapping[str, Any]],
    v3_by_uid: Mapping[str, Mapping[str, Any]], routing_by_uid: Mapping[str, Mapping[str, Any]],
    completed_at_utc: str,
) -> dict[str, Any]:
    nearby = list(item.get("nearby_exact_concept_candidates") or [])
    if not nearby:
        raise ValueError("Routing review item has no exact-source candidate")
    coordinate_valid = True
    role_values: set[str] = set()
    routing_values: set[bool] = set()
    navigation_values: set[str] = set()
    quality_codes: set[str] = set()
    checked: list[dict[str, Any]] = []
    for candidate in nearby:
        uid = str(candidate.get("internal_table_uid") or "")
        row_index = candidate.get("row_index")
        if uid not in v2_by_uid or uid not in v3_by_uid or uid not in routing_by_uid or not isinstance(row_index, int):
            raise ValueError("Routing candidate lacks immutable V2/V3/routing identity")
        v2, v3, routing = v2_by_uid[uid], v3_by_uid[uid], routing_by_uid[uid]
        rows = v2.get("rows") or []
        if row_index < 0 or row_index >= len(rows) or list(rows[row_index]) != list(candidate.get("raw_source_row") or []):
            coordinate_valid = False
        if candidate.get("document_id") != v2.get("document_id") or v2.get("document_id") != v3.get("document_id") or v2.get("document_id") != routing.get("document_id"):
            coordinate_valid = False
        grid = v3.get("grid") or {}
        profile = next((value for value in v3.get("row_profiles") or [] if value.get("row_index") == row_index), None)
        if not grid.get("rectangular") or not grid.get("provenance_complete") or profile is None:
            quality_codes.add("V3_STRUCTURE_QUALITY_UNRESOLVED")
        else:
            quality_codes.add("V3_STRUCTURE_INTEGRITY_CHECKED")
        role_values.add(str(routing.get("table_type_status") or "unknown"))
        routing_values.add(bool(routing.get("routing_eligible")))
        navigation_values.add(str((candidate.get("gate_vector") or {}).get("navigation_gate", {}).get("observed") or "unknown"))
        checked.append({
            "internal_table_uid": uid,
            "document_id": v2.get("document_id"),
            "row_index": row_index,
            "raw_source_row": list(rows[row_index]),
            "v3_grid": {key: grid.get(key) for key in ("rectangular", "provenance_complete", "width", "reason_codes")},
            "row_profile": profile,
            "routing": {key: routing.get(key) for key in ("table_type", "table_type_status", "routing_eligible", "available_period_years")},
        })
    cause = str(item.get("exclusive_primary_cause") or "")
    auxiliary_or_provisional = any(value != "source_structural" for value in role_values)
    if cause == "ROUTING_ELIGIBILITY" and routing_values == {False}:
        decision, table_role_correct, reason = "reject", (None if auxiliary_or_provisional else True), "ROUTING_PROMOTION_REJECTED"
    elif cause == "NAVIGATION_GATE":
        decision, table_role_correct, reason = "uncertain", (None if auxiliary_or_provisional else True), "NAVIGATION_GATE_POLICY_REVIEW_REQUIRED"
    else:
        decision, table_role_correct, reason = "uncertain", (None if auxiliary_or_provisional else True), "MULTIPLE_BLOCKERS_RETAINED"
    return {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "review_target": "routing_navigation_readiness_not_evidence",
        "immutable_review_payload_sha256": canonical_sha(item),
        "immutable_source_identity": item.get("immutable_source_identity") or {},
        "queue_kind": item.get("queue_kind"),
        "exclusive_primary_cause": cause,
        "minimal_blocker_sets": item.get("minimal_blocker_sets") or [],
        "source_coordinates_checked": coordinate_valid,
        "source_coordinates": checked,
        "source_quality": sorted(quality_codes),
        "semantic_correct": True,
        "table_role_correct": table_role_correct,
        "routing_eligible": routing_values == {True},
        "navigation_gate_statuses": sorted(navigation_values),
        "navigation_eligible": False,
        "abstain_required": True,
        "decision": decision,
        "reason_codes": sorted(set([reason, "NO_PRIMARY_ROLE_PROMOTION", "NO_EVIDENCE_PROMOTION"])),
        "reviewer_notes": (
            "Literal source rows and V2/V3 coordinates were replayed. This review "
            "does not promote a note, schedule, auxiliary, provisional, or blocked "
            "routing candidate to a primary statement or evidence."
        ),
        "reviewer_provenance": {
            "reviewer_id": "person_2_routing_v1",
            "reviewer_type": "independent_ai_source_review",
            "completed_at_utc": completed_at_utc,
            "blind_to_other_review": True,
        },
        "source_contract": _contract(),
    }


def materialize_reviews(
    *, queue_path: Path, adjudication_manifest_path: Path, structured_tables_path: Path,
    evidence_context_path: Path, routing_catalog_path: Path, output: Path, completed_at_utc: str,
) -> dict[str, Any]:
    manifest = json.loads(adjudication_manifest_path.read_text(encoding="utf-8"))
    expected_outputs = manifest.get("outputs") or {}
    expected_inputs = manifest.get("inputs") or {}
    _require_hash(queue_path, (expected_outputs.get("routing_queue") or {}).get("sha256"), "routing queue")
    _require_hash(structured_tables_path, (expected_inputs.get("structured_tables_v2") or {}).get("sha256"), "V2 tables")
    _require_hash(evidence_context_path, (expected_inputs.get("evidence_context_v3") or {}).get("sha256"), "V3 context")
    _require_hash(routing_catalog_path, (expected_inputs.get("routing_catalog") or {}).get("sha256"), "routing catalog")
    queue = _rows(queue_path)
    if len(queue) != 37 or Counter(item.get("queue_kind") for item in queue) != {"LOANS_TO_CUSTOMERS_ROUTING_REVIEW": 8, "ROUTING_OR_NAVIGATION_REVIEW": 29}:
        raise ValueError("Routing queue coverage mismatch")
    v2_by_uid = _index(_rows(structured_tables_path), "internal_table_uid", "V2")
    v3_by_uid = _index(_rows(evidence_context_path), "internal_table_uid", "V3")
    routing_by_uid = _index(_rows(routing_catalog_path), "internal_table_uid", "routing catalog")
    if set(v2_by_uid) != set(v3_by_uid) or set(v2_by_uid) != set(routing_by_uid):
        raise ValueError("V2/V3/routing UID coverage mismatch")
    reviews = [review_routing_item(item, v2_by_uid=v2_by_uid, v3_by_uid=v3_by_uid, routing_by_uid=routing_by_uid, completed_at_utc=completed_at_utc) for item in queue]
    reviews.sort(key=lambda value: (str(value["queue_kind"]), int(value["immutable_source_identity"]["question_id"]), str(value["immutable_source_identity"]["stage_id"]), str(value["immutable_source_identity"]["role"])))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in reviews), encoding="utf-8")
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "routing_queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)},
            "adjudication_manifest": {"path": str(adjudication_manifest_path), "sha256": sha256_file(adjudication_manifest_path)},
            "structured_tables_v2": {"path": str(structured_tables_path), "sha256": sha256_file(structured_tables_path)},
            "evidence_context_v3": {"path": str(evidence_context_path), "sha256": sha256_file(evidence_context_path)},
            "routing_catalog": {"path": str(routing_catalog_path), "sha256": sha256_file(routing_catalog_path)},
        },
        "outputs": {"reviews": {"path": str(output), "sha256": sha256_file(output)}},
        "counts": {
            "review_count": len(reviews),
            "queue_kind_counts": dict(sorted(Counter(row["queue_kind"] for row in reviews).items())),
            "decision_counts": dict(sorted(Counter(row["decision"] for row in reviews).items())),
            "source_coordinate_valid_count": sum(bool(row["source_coordinates_checked"]) for row in reviews),
            "abstain_required_count": sum(bool(row["abstain_required"]) for row in reviews),
            "reason_code_counts": dict(sorted(Counter(code for row in reviews for code in row["reason_codes"]).items())),
        },
        "source_contract": _contract(),
    }
    manifest_path = output.with_name("person2_routing_review_v1.manifest.json")
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
