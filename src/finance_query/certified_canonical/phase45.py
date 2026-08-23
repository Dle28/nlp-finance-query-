"""Fail-closed Phase 4-5 verifier for Phase 3 semantic proposals.

The GPU route proves only that an LLM selected finite IDs from a packet graph.
This module never turns that selection into a certificate.  It materializes a
new, hash-bound assertion record only where a period or unit value is an exact
normalized substring of a selected relation endpoint.  Heading and table
semantic claims remain unresolved until profile-specific source rules exist.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
import unicodedata

from finance_query.table_structure import sha256_file

from .evidence_graph import EvidenceGraphError, validate_evidence_graph
from .pipeline import CertifiedCanonicalError


PHASE45_PROTOCOL = "vifinqa_ccl_phase45_semantic_verifier_v1"
PHASE45_SCHEMA_VERSION = 1
_ASSERTIONS_NAME = "phase45_semantic_assertions_v1.jsonl"
_REPORT_NAME = "phase45_verification_report.json"
_MANIFEST_NAME = "phase45_verification_manifest.json"
_OUTPUT_NAMES = (_ASSERTIONS_NAME, _REPORT_NAME, _MANIFEST_NAME)
_FIELD_RELATIONS = {
    "heading_context": frozenset({"heading_scopes_table"}),
    "table_semantics": frozenset({"heading_scopes_table", "row_label_defines_value"}),
    "period_context": frozenset({"period_applies_to_column"}),
    "unit_context": frozenset({"unit_applies_to_column"}),
}
_PERIOD_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?!\d)|năm|nam|nay|trước|truoc|đầu|dau|cuối|cuoi|tháng|thang|ngày|ngay",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(r"triệu|trieu|tỷ|ty|nghìn|nghin|vnd|vnđ|đồng|dong|usd|eur", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Phase45VerificationResult:
    """Non-promotable output of deterministic Phase 4-5 verification."""

    output_dir: Path
    assertion_count: int
    source_bound_count: int
    unresolved_count: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertifiedCanonicalError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise CertifiedCanonicalError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise CertifiedCanonicalError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"expected JSONL object: {path}:{line_number}")
            rows.append(value)
    return rows


def _normalized_text(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CertifiedCanonicalError(f"SHA-256 mismatch: {label}")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise CertifiedCanonicalError(f"{label} violates the non-promotable contract")


def _require_new_output(output_dir: Path, inputs: Iterable[Path]) -> None:
    resolved_inputs = {path.resolve() for path in inputs}
    if output_dir.resolve() in resolved_inputs:
        raise CertifiedCanonicalError("output-dir cannot be a verifier input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; Phase 4-5 verification is immutable")


def _request_index(requests: list[dict[str, Any]], route_id: str) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for request in requests:
        if request.get("route_id") != route_id:
            continue
        request_id = str(request.get("request_id") or "")
        if not request_id or request_id in selected:
            raise CertifiedCanonicalError("prepared request identity is missing or duplicated")
        if request.get("training_eligible") is not False:
            raise CertifiedCanonicalError("prepared request is unexpectedly training eligible")
        selected[request_id] = request
    if not selected:
        raise CertifiedCanonicalError("no prepared requests for the requested route")
    return selected


def _derived_anchor_ids(relations: list[Mapping[str, Any]], anchors: Mapping[str, Mapping[str, Any]]) -> list[str]:
    selected: list[str] = []
    for relation in relations:
        source_id = str(relation.get("source_anchor_id") or "")
        target_id = str(relation.get("target_anchor_id") or "")
        if bool((anchors.get(source_id) or {}).get("selectable")):
            selected.append(source_id)
        elif bool((anchors.get(target_id) or {}).get("selectable")):
            selected.append(target_id)
        else:
            raise CertifiedCanonicalError("selected relation has no selectable endpoint")
    return list(dict.fromkeys(selected))


def _relation_endpoints(
    relations: Iterable[Mapping[str, Any]], anchors: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for relation in relations:
        for key in ("source_anchor_id", "target_anchor_id"):
            anchor_id = str(relation.get(key) or "")
            anchor = anchors.get(anchor_id) or {}
            for attribute in ("quoted_text", "canonical_label"):
                value = str(anchor.get(attribute) or "")
                if value:
                    values.append(
                        {
                            "anchor_id": anchor_id,
                            "attribute": attribute,
                            "value_sha256": _text_sha(value),
                            "normalized_value": _normalized_text(value),
                        }
                    )
    return values


def _value_shape_reason(field: str, proposed_value: object) -> str | None:
    normalized = _normalized_text(proposed_value)
    if len(normalized) < 3:
        return "proposed_value_too_short_for_source_assertion"
    if field == "period_context":
        if _UNIT_RE.search(normalized):
            return "period_value_contains_unit_token"
        if not _PERIOD_RE.search(normalized):
            return "period_value_has_no_period_marker"
    if field == "unit_context":
        if _PERIOD_RE.search(normalized):
            return "unit_value_contains_period_token"
        if not _UNIT_RE.search(normalized):
            return "unit_value_has_no_unit_marker"
    return None


def _source_support(
    *, field: str, proposed_value: object, endpoints: list[dict[str, str]]
) -> tuple[list[dict[str, str]], str | None]:
    """Return provenance only for a field-specific exact source match.

    A table-topic or heading relation contains structural evidence but no
    materialized, independently selected heading span.  Treating arbitrary
    text inside its context window as a table meaning would be an unsupported
    semantic leap, so those two fields remain unresolved here.
    """
    if field == "table_semantics":
        return [], "table_semantics_requires_profile_specific_source_rule"
    if field == "heading_context":
        return [], "heading_context_requires_materialized_heading_span"
    shape_reason = _value_shape_reason(field, proposed_value)
    if shape_reason is not None:
        return [], shape_reason
    needle = _normalized_text(proposed_value)
    support = [
        {
            "anchor_id": endpoint["anchor_id"],
            "attribute": endpoint["attribute"],
            "value_sha256": endpoint["value_sha256"],
        }
        for endpoint in endpoints
        if needle in endpoint["normalized_value"]
    ]
    if not support:
        return [], "proposed_value_not_exact_in_selected_relation_endpoints"
    return support, None


def _assertion(
    *,
    record: Mapping[str, Any],
    proposal: Mapping[str, Any],
    proposal_index: int,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    field = str(proposal.get("field") or "")
    allowed_relation_types = _FIELD_RELATIONS.get(field)
    if not allowed_relation_types:
        raise CertifiedCanonicalError(f"unsupported proposal field: {field!r}")
    graph = (request.get("packet") or {}).get("evidence_graph")
    if not isinstance(graph, Mapping):
        raise CertifiedCanonicalError("prepared request has no evidence graph")
    try:
        validate_evidence_graph(graph)
    except EvidenceGraphError as error:
        raise CertifiedCanonicalError(f"invalid prepared evidence graph: {error}") from error
    anchors = {
        str(item.get("anchor_id")): item
        for item in graph.get("anchors") or []
        if isinstance(item, Mapping) and item.get("anchor_id")
    }
    relation_ids = proposal.get("evidence_relation_ids")
    if (
        not isinstance(relation_ids, list)
        or not relation_ids
        or not all(isinstance(item, str) and item for item in relation_ids)
        or len(relation_ids) != len(set(relation_ids))
    ):
        raise CertifiedCanonicalError("proposal has missing or duplicated evidence relation IDs")
    relations_by_id = {
        str(item.get("relation_id")): item
        for item in graph.get("relations") or []
        if isinstance(item, Mapping) and item.get("relation_id")
    }
    relations: list[Mapping[str, Any]] = []
    for relation_id in relation_ids:
        relation = relations_by_id.get(str(relation_id))
        if relation is None:
            raise CertifiedCanonicalError("proposal references an unknown evidence relation")
        if relation.get("status") != "PASS" or relation.get("relation_type") not in allowed_relation_types:
            raise CertifiedCanonicalError("proposal relation does not satisfy its field contract")
        relations.append(relation)
    expected_anchors = _derived_anchor_ids(relations, anchors)
    supplied_anchors = proposal.get("evidence_anchor_ids")
    if (
        not isinstance(supplied_anchors, list)
        or not all(isinstance(item, str) and item for item in supplied_anchors)
        or supplied_anchors != expected_anchors
    ):
        raise CertifiedCanonicalError("proposal anchor IDs are not the deterministic relation-derived endpoints")
    if proposal.get("evidence_anchor_ids_derivation") != "relation_selectable_endpoint_v1":
        raise CertifiedCanonicalError("proposal anchor derivation is not recorded")
    endpoints = _relation_endpoints(relations, anchors)
    support, reason = _source_support(field=field, proposed_value=proposal.get("proposed_value"), endpoints=endpoints)
    status = "SOURCE_BOUND_PROPOSAL" if support else "UNRESOLVED"
    payload = {
        "schema_version": PHASE45_SCHEMA_VERSION,
        "protocol": PHASE45_PROTOCOL,
        "phase3_request_id": record["request_id"],
        "phase3_proposal_sha256": record.get("proposal_sha256"),
        "proposal_index": proposal_index,
        "internal_table_uid": record["internal_table_uid"],
        "route_id": record["route_id"],
        "field": field,
        "proposed_value": proposal.get("proposed_value"),
        "status": status,
        "evidence_class": "E1" if support else "E3",
        "evidence_relation_ids": [str(item) for item in relation_ids],
        "evidence_anchor_ids": list(expected_anchors),
        "evidence_anchor_ids_derivation": "relation_selectable_endpoint_v1",
        "source_support": support,
        "reason_codes": [] if reason is None else [reason],
        "derivation_rule": (
            "phase45_exact_header_endpoint_match_v1"
            if support
            else "none_phase45_insufficient_source_rule"
        ),
        "training_eligible": False,
        "certification_allowed": False,
    }
    return {"assertion_id": _sha_json(payload), **payload}


def verify_phase45_proposals(
    *,
    job_manifest: Path,
    requests: Path,
    source_manifest: Path,
    validated_proposals: Path,
    proposal_validation_manifest: Path,
    phase3_receipt: Path,
    phase3_audit: Path,
    output_dir: Path,
    route_id: str,
) -> Phase45VerificationResult:
    """Materialize source-bound Phase 4-5 assertions without certification."""
    inputs = [
        path.resolve()
        for path in (
            job_manifest,
            requests,
            source_manifest,
            validated_proposals,
            proposal_validation_manifest,
            phase3_receipt,
            phase3_audit,
        )
    ]
    if any(not path.is_file() for path in inputs):
        missing = [str(path) for path in inputs if not path.is_file()]
        raise FileNotFoundError(f"missing Phase 4-5 input(s): {missing}")
    _require_new_output(output_dir, inputs)
    before_hashes = {
        "job_manifest": sha256_file(job_manifest),
        "requests": sha256_file(requests),
        "source_manifest": sha256_file(source_manifest),
        "validated_proposals": sha256_file(validated_proposals),
        "proposal_validation_manifest": sha256_file(proposal_validation_manifest),
        "phase3_receipt": sha256_file(phase3_receipt),
        "phase3_audit": sha256_file(phase3_audit),
    }
    job = _json(job_manifest)
    if (
        job.get("protocol") != "vifinqa_ccl_phase3_bakeoff_v1"
        or job.get("run_status") != "prepared_phase_3_inference_not_executed"
        or job.get("training_eligible") is not False
    ):
        raise CertifiedCanonicalError("job manifest is not a non-promotable prepared Phase 3 route")
    _require_hash(requests, ((job.get("outputs") or {}).get(requests.name) or {}).get("sha256"), label="requests")
    routes = {
        str(item.get("route_id")): item
        for item in job.get("routes") or []
        if isinstance(item, Mapping) and item.get("route_id")
    }
    if not bool((routes.get(route_id) or {}).get("enabled")):
        raise CertifiedCanonicalError("requested route is absent or disabled")
    source = _json(source_manifest)
    source_tree_sha256 = (source.get("source_bundle") or {}).get("source_tree_sha256")
    if not isinstance(source_tree_sha256, str):
        raise CertifiedCanonicalError("source manifest has no source-tree hash")
    validation = _json(proposal_validation_manifest)
    if (
        validation.get("protocol") != "vifinqa_ccl_phase3_bakeoff_v1"
        or validation.get("run_status") != "proposal_validation_complete_not_certified"
        or validation.get("route_id") != route_id
        or validation.get("training_eligible") is not False
    ):
        raise CertifiedCanonicalError("proposal validation manifest is not a non-promotable Phase 3 result")
    validation_inputs = validation.get("inputs") or {}
    _require_hash(job_manifest, (validation_inputs.get("job_manifest") or {}).get("sha256"), label="validation job manifest")
    _require_hash(requests, (validation_inputs.get("requests") or {}).get("sha256"), label="validation requests")
    _require_hash(
        validated_proposals,
        ((validation.get("outputs") or {}).get(validated_proposals.name) or {}).get("sha256"),
        label="validated proposals",
    )
    receipt = _json(phase3_receipt)
    _require_non_promotable(receipt, label="Phase 3 Kaggle receipt")
    if (
        receipt.get("job_manifest_sha256") != before_hashes["job_manifest"]
        or receipt.get("source_tree_sha256") != source_tree_sha256
        or receipt.get("qwen_validation_manifest_sha256") != before_hashes["proposal_validation_manifest"]
    ):
        raise CertifiedCanonicalError("Kaggle receipt does not bind the Phase 3 validation result")
    audit = _json(phase3_audit)
    if audit.get("protocol") != "ccl_phase3_gpu_run_audit_v1" or audit.get("audit_passed") is not True:
        raise CertifiedCanonicalError("Phase 3 audit did not pass")
    _require_non_promotable(audit, label="Phase 3 audit")
    if audit.get("kernel_route") != route_id:
        raise CertifiedCanonicalError("Phase 3 audit is scoped to a different route")
    audit_hashes = audit.get("input_hashes") or {}
    for label, path in (("job_manifest", job_manifest), ("requests", requests), ("source_manifest", source_manifest)):
        _require_hash(path, audit_hashes.get(label), label=f"Phase 3 audit {label}")
    route_requests = _request_index(_jsonl(requests), route_id)
    audit_counts = audit.get("counts") or {}
    if (
        audit_counts.get("qwen_request_count") != len(route_requests)
        or audit_counts.get("raw_response_count") != len(route_requests)
    ):
        raise CertifiedCanonicalError("Phase 3 audit request coverage is inconsistent")
    rows = _jsonl(validated_proposals)
    rows_by_id: dict[str, dict[str, Any]] = {}
    statuses = Counter()
    for row in rows:
        request_id = str(row.get("request_id") or "")
        if not request_id or request_id in rows_by_id:
            raise CertifiedCanonicalError("validated proposal identity is missing or duplicated")
        if row.get("route_id") != route_id or request_id not in route_requests:
            raise CertifiedCanonicalError("validated proposal is outside the audited route")
        if row.get("internal_table_uid") != route_requests[request_id].get("internal_table_uid"):
            raise CertifiedCanonicalError("validated proposal table identity does not match the prepared request")
        _require_non_promotable(row, label="validated proposal")
        status = str(row.get("status") or "")
        if status not in {"VALID_PROPOSAL_ONLY", "INVALID_UNRESOLVED"}:
            raise CertifiedCanonicalError("validated proposal has unsupported status")
        statuses[status] += 1
        rows_by_id[request_id] = row
    if set(rows_by_id) != set(route_requests):
        raise CertifiedCanonicalError("validated proposal coverage does not match prepared route requests")
    if (
        statuses["VALID_PROPOSAL_ONLY"] != audit_counts.get("valid_proposal_only_count")
        or statuses["INVALID_UNRESOLVED"] != audit_counts.get("invalid_or_missing_unresolved_count")
    ):
        raise CertifiedCanonicalError("validated proposal counts do not match the audited Phase 3 result")
    assertions: list[dict[str, Any]] = []
    for request_id in sorted(rows_by_id):
        row = rows_by_id[request_id]
        if row["status"] != "VALID_PROPOSAL_ONLY":
            continue
        if not isinstance(row.get("proposals"), list) or not isinstance(row.get("unresolved_conditions"), list):
            raise CertifiedCanonicalError("valid proposal row lacks its validated proposal payload")
        if not isinstance(row.get("proposal_sha256"), str):
            raise CertifiedCanonicalError("valid proposal row lacks its Phase 3 proposal hash")
        for proposal_index, proposal in enumerate(row["proposals"]):
            if not isinstance(proposal, Mapping):
                raise CertifiedCanonicalError("valid proposal row contains a non-object proposal")
            assertions.append(
                _assertion(
                    record=row,
                    proposal=proposal,
                    proposal_index=proposal_index,
                    request=route_requests[request_id],
                )
            )
    # Do not create a partial artifact directory until every input and every
    # proposal has passed its structural fail-closed checks.
    output_dir.mkdir(parents=True)
    assertion_path = output_dir / _ASSERTIONS_NAME
    with assertion_path.open("x", encoding="utf-8") as file:
        for assertion in assertions:
            file.write(json.dumps(assertion, ensure_ascii=False, sort_keys=True) + "\n")
    status_counts = Counter(str(item["status"]) for item in assertions)
    field_counts = Counter(str(item["field"]) for item in assertions)
    reason_counts = Counter(
        str(reason) for item in assertions for reason in (item.get("reason_codes") or [])
    )
    after_hashes = {
        "job_manifest": sha256_file(job_manifest),
        "requests": sha256_file(requests),
        "source_manifest": sha256_file(source_manifest),
        "validated_proposals": sha256_file(validated_proposals),
        "proposal_validation_manifest": sha256_file(proposal_validation_manifest),
        "phase3_receipt": sha256_file(phase3_receipt),
        "phase3_audit": sha256_file(phase3_audit),
    }
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("Phase 4-5 verification changed a hash-bound input")
    report = {
        "schema_version": PHASE45_SCHEMA_VERSION,
        "protocol": PHASE45_PROTOCOL,
        "run_status": "phase_4_5_deterministic_verification_complete_not_certified",
        "route_id": route_id,
        "phase3": {
            "audit_passed": True,
            "request_count": len(route_requests),
            "valid_proposal_only_count": statuses["VALID_PROPOSAL_ONLY"],
            "invalid_unresolved_count": statuses["INVALID_UNRESOLVED"],
        },
        "assertion_count": len(assertions),
        "assertion_status_counts": dict(sorted(status_counts.items())),
        "assertion_field_counts": dict(sorted(field_counts.items())),
        "unresolved_reason_counts": dict(sorted(reason_counts.items())),
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "profile_specific_source_rules_then_campaign_certification",
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE45_SCHEMA_VERSION,
        "protocol": PHASE45_PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {name: {"path": str(path), "sha256": digest} for name, path, digest in (
            ("job_manifest", job_manifest, before_hashes["job_manifest"]),
            ("requests", requests, before_hashes["requests"]),
            ("source_manifest", source_manifest, before_hashes["source_manifest"]),
            ("validated_proposals", validated_proposals, before_hashes["validated_proposals"]),
            (
                "proposal_validation_manifest",
                proposal_validation_manifest,
                before_hashes["proposal_validation_manifest"],
            ),
            ("phase3_receipt", phase3_receipt, before_hashes["phase3_receipt"]),
            ("phase3_audit", phase3_audit, before_hashes["phase3_audit"]),
        )},
        "outputs": {
            name: {
                "path": str(output_dir / name),
                "sha256": sha256_file(output_dir / name),
                "bytes": (output_dir / name).stat().st_size,
            }
            for name in _OUTPUT_NAMES[:-1]
        },
        "rule_version": "1.0.0",
        "training_eligible": False,
        "certification_allowed": False,
    }
    manifest_path = output_dir / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Phase45VerificationResult(
        output_dir=output_dir,
        assertion_count=len(assertions),
        source_bound_count=status_counts["SOURCE_BOUND_PROPOSAL"],
        unresolved_count=status_counts["UNRESOLVED"],
    )
