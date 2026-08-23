"""Fail-closed split of merged period/unit header proposals.

The original raw header is immutable.  This stage emits a separate component
assertion only when the selected finite relation endpoint contains a recognized
unit suffix and the remaining prefix has a period marker.  It never rewrites
the Phase 3 or Phase 4-5 proposal.
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
from .phase45 import PHASE45_PROTOCOL
from .pipeline import CertifiedCanonicalError


PHASE5_HEADER_COMPONENT_PROTOCOL = "vifinqa_ccl_phase5_header_component_verifier_v1"
PHASE5_HEADER_COMPONENT_SCHEMA_VERSION = 1
_COMPONENTS_NAME = "phase5_header_components_v1.jsonl"
_REPORT_NAME = "phase5_header_component_report.json"
_MANIFEST_NAME = "phase5_header_component_manifest.json"
_OUTPUT_NAMES = (_COMPONENTS_NAME, _REPORT_NAME, _MANIFEST_NAME)
_PERIOD_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?!\d)|năm|nam|nay|trước|truoc|đầu|dau|cuối|cuoi|tháng|thang|ngày|ngay",
    re.IGNORECASE,
)
_UNIT_SUFFIX_RE = re.compile(
    r"^(?P<period>.+?)(?P<unit>triệu\s*(?:vnd|vnđ|đồng)|tỷ\s*(?:vnd|vnđ|đồng)|nghìn\s*(?:vnd|vnđ|đồng)|vnd|vnđ|đồng)\s*$",
    re.IGNORECASE,
)
_MERGED_REASONS = frozenset({"period_value_contains_unit_token", "unit_value_contains_period_token"})


@dataclass(frozen=True, slots=True)
class Phase5HeaderComponentResult:
    output_dir: Path
    component_count: int
    period_component_count: int
    unit_component_count: int
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


def _normalize(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CertifiedCanonicalError(f"SHA-256 mismatch: {label}")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise CertifiedCanonicalError(f"{label} violates the non-promotable contract")


def _require_new_output(output_dir: Path, inputs: Iterable[Path]) -> None:
    if output_dir.resolve() in {path.resolve() for path in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be a header-component input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; header-component output is immutable")


def _selected_source_anchor(assertion: Mapping[str, Any], request: Mapping[str, Any]) -> Mapping[str, Any]:
    graph = (request.get("packet") or {}).get("evidence_graph")
    if not isinstance(graph, Mapping):
        raise CertifiedCanonicalError("prepared request has no evidence graph")
    try:
        validate_evidence_graph(graph)
    except EvidenceGraphError as error:
        raise CertifiedCanonicalError(f"invalid prepared evidence graph: {error}") from error
    field = str(assertion.get("field") or "")
    expected_type = "period_applies_to_column" if field == "period_context" else "unit_applies_to_column"
    relation_ids = assertion.get("evidence_relation_ids")
    if not isinstance(relation_ids, list) or len(relation_ids) != 1 or not isinstance(relation_ids[0], str):
        raise CertifiedCanonicalError("merged header assertion must select exactly one relation")
    relations = {
        str(item.get("relation_id")): item
        for item in graph.get("relations") or []
        if isinstance(item, Mapping) and item.get("relation_id")
    }
    relation = relations.get(relation_ids[0])
    if relation is None or relation.get("relation_type") != expected_type or relation.get("status") != "PASS":
        raise CertifiedCanonicalError("merged header assertion relation does not satisfy the field contract")
    anchors = {
        str(item.get("anchor_id")): item
        for item in graph.get("anchors") or []
        if isinstance(item, Mapping) and item.get("anchor_id")
    }
    source_id = str(relation.get("source_anchor_id") or "")
    source = anchors.get(source_id)
    if not isinstance(source, Mapping) or not bool(source.get("selectable")):
        raise CertifiedCanonicalError("merged header relation lacks a selectable source anchor")
    if assertion.get("evidence_anchor_ids") != [source_id]:
        raise CertifiedCanonicalError("merged header assertion has a non-derived evidence anchor")
    return source


def _component(assertion: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    field = str(assertion.get("field") or "")
    source = _selected_source_anchor(assertion, request)
    original = str(assertion.get("proposed_value") or "")
    source_text = str(source.get("quoted_text") or "")
    payload = {
        "schema_version": PHASE5_HEADER_COMPONENT_SCHEMA_VERSION,
        "protocol": PHASE5_HEADER_COMPONENT_PROTOCOL,
        "phase45_assertion_id": assertion["assertion_id"],
        "phase3_request_id": assertion["phase3_request_id"],
        "phase3_proposal_sha256": assertion.get("phase3_proposal_sha256"),
        "internal_table_uid": assertion["internal_table_uid"],
        "route_id": assertion["route_id"],
        "field": field,
        "status": "UNRESOLVED",
        "evidence_class": "E3",
        "origin_proposed_value": original,
        "derived_value": None,
        "evidence_relation_ids": list(assertion["evidence_relation_ids"]),
        "evidence_anchor_ids": list(assertion["evidence_anchor_ids"]),
        "source_support": [],
        "reason_codes": [],
        "derivation_rule": "none_phase5_insufficient_header_component_evidence",
        "training_eligible": False,
        "certification_allowed": False,
    }
    if _normalize(original) != _normalize(source_text):
        payload["reason_codes"] = ["origin_proposed_value_not_exact_in_selected_source_endpoint"]
        return {"header_component_id": _sha_json(payload), **payload}
    match = _UNIT_SUFFIX_RE.match(original)
    if match is None:
        payload["reason_codes"] = ["origin_proposed_value_has_no_allowlisted_unit_suffix"]
        return {"header_component_id": _sha_json(payload), **payload}
    period = match.group("period").strip()
    unit = match.group("unit").strip()
    if not _PERIOD_RE.search(period):
        payload["reason_codes"] = ["merged_header_prefix_has_no_period_marker"]
        return {"header_component_id": _sha_json(payload), **payload}
    derived_value = period if field == "period_context" else unit
    component_start = original.find(derived_value)
    if component_start < 0:
        payload["reason_codes"] = ["derived_component_not_literal_in_origin_header"]
        return {"header_component_id": _sha_json(payload), **payload}
    payload.update(
        {
            "status": "SOURCE_SPLIT_HEADER_COMPONENT",
            "evidence_class": "E0",
            "derived_value": derived_value,
            "source_support": [
            {
                "anchor_id": source["anchor_id"],
                "attribute": "quoted_text_literal_component",
                "value_sha256": _text_sha(source_text),
                "character_span": {"start": component_start, "end": component_start + len(derived_value)},
            }
            ],
            "derivation_rule": "phase5_allowlisted_period_unit_suffix_split_v1",
        }
    )
    return {"header_component_id": _sha_json(payload), **payload}


def verify_phase5_header_components(
    *,
    job_manifest: Path,
    requests: Path,
    phase45_assertions: Path,
    phase45_manifest: Path,
    output_dir: Path,
    route_id: str,
) -> Phase5HeaderComponentResult:
    """Derive individually anchored period or unit components from merged headers."""
    inputs = [path.resolve() for path in (job_manifest, requests, phase45_assertions, phase45_manifest)]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing Phase 5 header-component input")
    _require_new_output(output_dir, inputs)
    before_hashes = {path.name: sha256_file(path) for path in inputs}
    job = _json(job_manifest)
    if (
        job.get("protocol") != "vifinqa_ccl_phase3_bakeoff_v1"
        or job.get("run_status") != "prepared_phase_3_inference_not_executed"
        or job.get("training_eligible") is not False
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
    _require_hash(
        phase45_assertions,
        ((phase45.get("outputs") or {}).get(phase45_assertions.name) or {}).get("sha256"),
        label="Phase 4-5 assertions",
    )
    request_by_id = {
        str(row.get("request_id")): row
        for row in _jsonl(requests)
        if row.get("route_id") == route_id and row.get("request_id")
    }
    selected = [
        row
        for row in _jsonl(phase45_assertions)
        if row.get("route_id") == route_id
        and row.get("field") in {"period_context", "unit_context"}
        and _MERGED_REASONS.intersection(str(reason) for reason in (row.get("reason_codes") or []))
    ]
    if not selected:
        raise CertifiedCanonicalError("no merged period/unit assertions for the requested route")
    seen: set[str] = set()
    for row in selected:
        _require_non_promotable(row, label="Phase 4-5 assertion")
        assertion_id = str(row.get("assertion_id") or "")
        request_id = str(row.get("phase3_request_id") or "")
        if not assertion_id or assertion_id in seen or request_id not in request_by_id:
            raise CertifiedCanonicalError("merged header assertion identity is invalid")
        if row.get("internal_table_uid") != request_by_id[request_id].get("internal_table_uid"):
            raise CertifiedCanonicalError("merged header assertion table identity does not match request")
        seen.add(assertion_id)
    components = [
        _component(row, request_by_id[str(row["phase3_request_id"])])
        for row in sorted(selected, key=lambda row: str(row["assertion_id"]))
    ]
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("Phase 5 header-component verification changed a hash-bound input")
    output_dir.mkdir(parents=True)
    components_path = output_dir / _COMPONENTS_NAME
    with components_path.open("x", encoding="utf-8") as file:
        for component in components:
            file.write(json.dumps(component, ensure_ascii=False, sort_keys=True) + "\n")
    field_counts = Counter(
        str(row["field"]) for row in components if row["status"] == "SOURCE_SPLIT_HEADER_COMPONENT"
    )
    status_counts = Counter(str(row["status"]) for row in components)
    reason_counts = Counter(str(reason) for row in components for reason in (row.get("reason_codes") or []))
    report = {
        "schema_version": PHASE5_HEADER_COMPONENT_SCHEMA_VERSION,
        "protocol": PHASE5_HEADER_COMPONENT_PROTOCOL,
        "run_status": "phase_5_header_component_verification_complete_not_certified",
        "route_id": route_id,
        "component_count": len(components),
        "status_counts": dict(sorted(status_counts.items())),
        "field_counts": dict(sorted(field_counts.items())),
        "unresolved_reason_counts": dict(sorted(reason_counts.items())),
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "profile_to_table_semantic_assertion_compatibility",
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE5_HEADER_COMPONENT_SCHEMA_VERSION,
        "protocol": PHASE5_HEADER_COMPONENT_PROTOCOL,
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
    return Phase5HeaderComponentResult(
        output_dir=output_dir,
        component_count=len(components),
        period_component_count=field_counts["period_context"],
        unit_component_count=field_counts["unit_context"],
        unresolved_count=status_counts["UNRESOLVED"],
    )
