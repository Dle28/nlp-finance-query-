"""Materialize narrow, literal source-heading spans for Phase 4.

Phase 3 graph edges prove that a context anchor structurally scopes a table,
but the packet intentionally does not expose a narrow heading span.  This
module joins the audited Phase 4-5 assertion to immutable raw and normalized
records and emits a span only when the declared literal ``source_heading`` has
exactly one occurrence in the hash-bound raw context.  It is layout evidence,
not a table-topic certificate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from finance_query.table_structure import sha256_file

from .evidence_graph import EvidenceGraphError, validate_evidence_graph
from .phase45 import PHASE45_PROTOCOL
from .pipeline import CertifiedCanonicalError


PHASE4_LAYOUT_PROTOCOL = "vifinqa_ccl_phase4_heading_span_materialization_v1"
PHASE4_LAYOUT_SCHEMA_VERSION = 1
_SPANS_NAME = "phase4_heading_spans_v1.jsonl"
_REPORT_NAME = "phase4_heading_span_report.json"
_MANIFEST_NAME = "phase4_heading_span_manifest.json"
_OUTPUT_NAMES = (_SPANS_NAME, _REPORT_NAME, _MANIFEST_NAME)


@dataclass(frozen=True, slots=True)
class Phase4HeadingSpanResult:
    output_dir: Path
    span_count: int
    materialized_count: int
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


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CertifiedCanonicalError(f"SHA-256 mismatch: {label}")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise CertifiedCanonicalError(f"{label} violates the non-promotable contract")


def _require_new_output(output_dir: Path, inputs: Iterable[Path]) -> None:
    if output_dir.resolve() in {path.resolve() for path in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be a Phase 4 input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; Phase 4 layout output is immutable")


def _record_sha(record: Mapping[str, Any]) -> str:
    return _sha_json(record)


def _select_records(path: Path, requested_uids: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise CertifiedCanonicalError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(row, dict):
                raise CertifiedCanonicalError(f"expected JSONL object: {path}:{line_number}")
            uid = str(row.get("internal_table_uid") or "")
            if uid not in requested_uids:
                continue
            if uid in selected:
                raise CertifiedCanonicalError(f"duplicate source record for table UID: {uid}")
            selected[uid] = row
            if len(selected) == len(requested_uids):
                break
    missing = sorted(requested_uids - set(selected))
    if missing:
        raise CertifiedCanonicalError(f"missing source record(s) for table UIDs: {', '.join(missing[:3])}")
    return selected


def _heading_relation(
    *, assertion: Mapping[str, Any], request: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, str | None]:
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
    relations = {
        str(item.get("relation_id")): item
        for item in graph.get("relations") or []
        if isinstance(item, Mapping) and item.get("relation_id")
    }
    selected_ids = assertion.get("evidence_relation_ids")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise CertifiedCanonicalError("Phase 4-5 assertion lacks evidence relation IDs")
    selected = [relations.get(str(value)) for value in selected_ids]
    if any(value is None for value in selected):
        raise CertifiedCanonicalError("Phase 4-5 assertion references an unknown relation")
    headings = [item for item in selected if item and item.get("relation_type") == "heading_scopes_table"]
    if not headings:
        return None, None, "proposal_has_no_heading_relation"
    if len(headings) != 1:
        return None, None, "proposal_has_ambiguous_heading_relations"
    relation = headings[0]
    source_anchor = anchors.get(str(relation.get("source_anchor_id") or ""))
    target_anchor = anchors.get(str(relation.get("target_anchor_id") or ""))
    if not isinstance(source_anchor, Mapping) or not isinstance(target_anchor, Mapping):
        raise CertifiedCanonicalError("heading relation has an unknown endpoint")
    if source_anchor.get("kind") != "raw_context" or source_anchor.get("field") != "context_before":
        return None, None, "heading_relation_source_is_not_raw_context"
    if target_anchor.get("kind") != "table_slot":
        return None, None, "heading_relation_target_is_not_table_slot"
    return relation, source_anchor, None


def _span_record(
    *,
    assertion: Mapping[str, Any],
    request: Mapping[str, Any],
    raw: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> dict[str, Any]:
    uid = str(assertion["internal_table_uid"])
    if raw.get("internal_table_uid") != uid or normalized.get("internal_table_uid") != uid:
        raise CertifiedCanonicalError("raw/normalized table identity does not match the Phase 4-5 assertion")
    if normalized.get("source_record_sha256") != _record_sha(raw):
        raise CertifiedCanonicalError("normalized table does not bind the raw source record")
    relation, source_anchor, relation_reason = _heading_relation(assertion=assertion, request=request)
    payload: dict[str, Any] = {
        "schema_version": PHASE4_LAYOUT_SCHEMA_VERSION,
        "protocol": PHASE4_LAYOUT_PROTOCOL,
        "phase45_assertion_id": assertion["assertion_id"],
        "phase3_request_id": assertion["phase3_request_id"],
        "phase3_proposal_sha256": assertion.get("phase3_proposal_sha256"),
        "internal_table_uid": uid,
        "route_id": assertion["route_id"],
        "status": "UNRESOLVED",
        "evidence_class": "E3",
        "heading_relation_id": relation.get("relation_id") if relation else None,
        "source_heading": None,
        "source_heading_sha256": None,
        "source_context_sha256": None,
        "raw_context_character_span": None,
        "source_support": [],
        "reason_codes": [],
        "derivation_rule": "none_phase4_insufficient_source_heading",
        "training_eligible": False,
        "certification_allowed": False,
    }
    if relation_reason is not None:
        payload["reason_codes"] = [relation_reason]
        return {"heading_span_id": _sha_json(payload), **payload}
    raw_context = str(raw.get("context_before") or "")
    outside = normalized.get("outside_table_context") or {}
    source_heading = str(outside.get("source_heading") or "").strip()
    context_sha = str(outside.get("source_context_sha256") or "")
    if _text_sha(raw_context) != context_sha:
        raise CertifiedCanonicalError("normalized source-context hash does not match raw context")
    if source_anchor is None or source_anchor.get("raw_text_sha256") != context_sha:
        raise CertifiedCanonicalError("selected heading relation is not bound to the raw source context")
    if not source_heading:
        payload["reason_codes"] = ["source_heading_missing"]
        return {"heading_span_id": _sha_json(payload), **payload}
    context_folded = raw_context.casefold()
    heading_folded = source_heading.casefold()
    occurrences: list[int] = []
    start = context_folded.find(heading_folded)
    while start >= 0:
        occurrences.append(start)
        start = context_folded.find(heading_folded, start + len(heading_folded))
    if len(occurrences) != 1:
        payload["reason_codes"] = ["source_heading_not_unique_in_raw_context"]
        return {"heading_span_id": _sha_json(payload), **payload}
    start = occurrences[0]
    end = start + len(source_heading)
    payload.update(
        {
            "status": "SOURCE_HEADING_SPAN_MATERIALIZED",
            "evidence_class": "E0",
            "source_heading": source_heading,
            "source_heading_sha256": _text_sha(source_heading),
            "source_context_sha256": context_sha,
            "raw_context_character_span": {"start": start, "end": end},
            "source_support": [
                {
                    "anchor_id": source_anchor["anchor_id"],
                    "attribute": "raw_context_unique_literal_span",
                    "value_sha256": _text_sha(source_heading),
                }
            ],
            "derivation_rule": "phase4_unique_literal_source_heading_span_v1",
        }
    )
    return {"heading_span_id": _sha_json(payload), **payload}


def materialize_phase4_heading_spans(
    *,
    job_manifest: Path,
    requests: Path,
    phase45_assertions: Path,
    phase45_manifest: Path,
    ccl_input_inventory: Path,
    raw_tables: Path,
    normalized_tables: Path,
    output_dir: Path,
    route_id: str,
) -> Phase4HeadingSpanResult:
    """Create exact literal heading spans for audited table-semantic proposals."""
    inputs = [
        path.resolve()
        for path in (
            job_manifest,
            requests,
            phase45_assertions,
            phase45_manifest,
            ccl_input_inventory,
            raw_tables,
            normalized_tables,
        )
    ]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing Phase 4 heading-span input")
    _require_new_output(output_dir, inputs)
    before_hashes = {path.name: sha256_file(path) for path in inputs}
    job = _json(job_manifest)
    if (
        job.get("protocol") != "vifinqa_ccl_phase3_bakeoff_v1"
        or job.get("training_eligible") is not False
        or job.get("run_status") != "prepared_phase_3_inference_not_executed"
    ):
        raise CertifiedCanonicalError("job manifest is not a non-promotable prepared Phase 3 job")
    _require_hash(requests, ((job.get("outputs") or {}).get(requests.name) or {}).get("sha256"), label="requests")
    phase45 = _json(phase45_manifest)
    if (
        phase45.get("protocol") != PHASE45_PROTOCOL
        or phase45.get("run_status") != "phase_4_5_deterministic_verification_complete_not_certified"
        or phase45.get("training_eligible") is not False
        or phase45.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError("Phase 4-5 manifest is unsupported or promotable")
    phase45_inputs = phase45.get("inputs") or {}
    _require_hash(job_manifest, (phase45_inputs.get("job_manifest") or {}).get("sha256"), label="Phase 4-5 job")
    _require_hash(requests, (phase45_inputs.get("requests") or {}).get("sha256"), label="Phase 4-5 requests")
    _require_hash(
        phase45_assertions,
        ((phase45.get("outputs") or {}).get(phase45_assertions.name) or {}).get("sha256"),
        label="Phase 4-5 assertions",
    )
    inventory = _json(ccl_input_inventory)
    if inventory.get("protocol") != "vifinqa_certified_canonical_v1" or inventory.get("stage") != "phase_0_freeze_and_baseline":
        raise CertifiedCanonicalError("CCL input inventory is unsupported")
    inventory_inputs = inventory.get("inputs") or {}
    _require_hash(raw_tables, (inventory_inputs.get("raw_tables") or {}).get("sha256"), label="raw tables")
    _require_hash(
        normalized_tables,
        (inventory_inputs.get("preprocessing_normalized") or {}).get("sha256"),
        label="normalized tables",
    )
    request_rows = {
        str(row.get("request_id")): row
        for row in _jsonl(requests)
        if row.get("route_id") == route_id and row.get("request_id")
    }
    if not request_rows:
        raise CertifiedCanonicalError("requested route has no prepared requests")
    assertion_rows = [
        row
        for row in _jsonl(phase45_assertions)
        if row.get("route_id") == route_id and row.get("field") == "table_semantics"
    ]
    if not assertion_rows:
        raise CertifiedCanonicalError("Phase 4-5 assertions contain no table-semantic proposals for this route")
    seen_assertions: set[str] = set()
    target_uids: set[str] = set()
    for row in assertion_rows:
        _require_non_promotable(row, label="Phase 4-5 assertion")
        assertion_id = str(row.get("assertion_id") or "")
        request_id = str(row.get("phase3_request_id") or "")
        uid = str(row.get("internal_table_uid") or "")
        if not assertion_id or assertion_id in seen_assertions or request_id not in request_rows or not uid:
            raise CertifiedCanonicalError("Phase 4-5 table-semantic assertion identity is invalid")
        if request_rows[request_id].get("internal_table_uid") != uid:
            raise CertifiedCanonicalError("Phase 4-5 table-semantic assertion has mismatched table identity")
        seen_assertions.add(assertion_id)
        target_uids.add(uid)
    raw_by_uid = _select_records(raw_tables, target_uids)
    normalized_by_uid = _select_records(normalized_tables, target_uids)
    spans = [
        _span_record(
            assertion=assertion,
            request=request_rows[str(assertion["phase3_request_id"])],
            raw=raw_by_uid[str(assertion["internal_table_uid"])],
            normalized=normalized_by_uid[str(assertion["internal_table_uid"])],
        )
        for assertion in sorted(assertion_rows, key=lambda row: str(row["assertion_id"]))
    ]
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("Phase 4 heading-span materialization changed a hash-bound input")
    output_dir.mkdir(parents=True)
    spans_path = output_dir / _SPANS_NAME
    with spans_path.open("x", encoding="utf-8") as file:
        for span in spans:
            file.write(json.dumps(span, ensure_ascii=False, sort_keys=True) + "\n")
    status_counts = Counter(str(row["status"]) for row in spans)
    reason_counts = Counter(str(reason) for row in spans for reason in (row.get("reason_codes") or []))
    report = {
        "schema_version": PHASE4_LAYOUT_SCHEMA_VERSION,
        "protocol": PHASE4_LAYOUT_PROTOCOL,
        "run_status": "phase_4_heading_span_materialization_complete_not_certified",
        "route_id": route_id,
        "table_semantic_assertion_count": len(spans),
        "status_counts": dict(sorted(status_counts.items())),
        "unresolved_reason_counts": dict(sorted(reason_counts.items())),
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "profile_specific_table_topic_rules",
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE4_LAYOUT_SCHEMA_VERSION,
        "protocol": PHASE4_LAYOUT_PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {path.name: {"path": str(path), "sha256": digest} for path, digest in zip(inputs, before_hashes.values())},
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
    (output_dir / _MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Phase4HeadingSpanResult(
        output_dir=output_dir,
        span_count=len(spans),
        materialized_count=status_counts["SOURCE_HEADING_SPAN_MATERIALIZED"],
        unresolved_count=status_counts["UNRESOLVED"],
    )
