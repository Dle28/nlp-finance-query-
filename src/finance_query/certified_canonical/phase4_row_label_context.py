"""Materialize exact source-cell fallback context for heading-less Phase 4 claims.

Some Phase 3 table-semantic proposals have no ``heading_scopes_table`` edge,
but do have one source-bound ``row_label_defines_value`` relation.  Its source
cell is useful report context (25 source cells have the ``row_label`` role and
eight retain the source graph's ``header`` role). It is not a heading and cannot by itself
establish the semantic type of the whole table.
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


PHASE4_ROW_LABEL_CONTEXT_PROTOCOL = "vifinqa_ccl_phase4_row_label_context_v1"
PHASE4_ROW_LABEL_CONTEXT_SCHEMA_VERSION = 1
_CONTEXT_NAME = "phase4_row_label_context_v1.jsonl"
_REPORT_NAME = "phase4_row_label_context_report.json"
_MANIFEST_NAME = "phase4_row_label_context_manifest.json"
_OUTPUT_NAMES = (_CONTEXT_NAME, _REPORT_NAME, _MANIFEST_NAME)


@dataclass(frozen=True, slots=True)
class Phase4RowLabelContextResult:
    output_dir: Path
    context_count: int
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
        raise CertifiedCanonicalError("output-dir cannot be a Phase 4 row-label input")
    if output_dir.exists():
        raise FileExistsError("output-dir must be new; Phase 4 row-label output is immutable")


def _row_label_relation(
    *, assertion: Mapping[str, Any], request: Mapping[str, Any]
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, str | None]:
    graph = (request.get("packet") or {}).get("evidence_graph")
    if not isinstance(graph, Mapping):
        raise CertifiedCanonicalError("prepared request has no evidence graph")
    try:
        validate_evidence_graph(graph)
    except EvidenceGraphError as error:
        raise CertifiedCanonicalError(f"invalid prepared evidence graph: {error}") from error
    relation_ids = assertion.get("evidence_relation_ids")
    if not isinstance(relation_ids, list) or not relation_ids:
        raise CertifiedCanonicalError("Phase 4-5 assertion lacks evidence relation IDs")
    relations = {
        str(value.get("relation_id")): value
        for value in graph.get("relations") or []
        if isinstance(value, Mapping) and value.get("relation_id")
    }
    selected = [relations.get(str(relation_id)) for relation_id in relation_ids]
    if any(relation is None for relation in selected):
        raise CertifiedCanonicalError("Phase 4-5 assertion references an unknown relation")
    if any(relation.get("relation_type") == "heading_scopes_table" for relation in selected if relation):
        return None, None, "proposal_has_heading_relation"
    row_relations = [relation for relation in selected if relation and relation.get("relation_type") == "row_label_defines_value"]
    if not row_relations:
        return None, None, "proposal_has_no_row_label_relation"
    if len(row_relations) != 1 or len(selected) != 1:
        return None, None, "proposal_has_ambiguous_row_label_relations"
    relation = row_relations[0]
    anchors = {
        str(value.get("anchor_id")): value
        for value in graph.get("anchors") or []
        if isinstance(value, Mapping) and value.get("anchor_id")
    }
    source = anchors.get(str(relation.get("source_anchor_id") or ""))
    if not isinstance(source, Mapping):
        raise CertifiedCanonicalError("row-label relation has an unknown source anchor")
    if (
        source.get("kind") != "raw_cell"
        or source.get("role") not in {"row_label", "header"}
        or source.get("selectable") is not True
    ):
        return None, None, "row_label_relation_source_is_not_selectable_raw_context_cell"
    return relation, source, None


def _context_record(*, assertion: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    relation, source_anchor, reason = _row_label_relation(assertion=assertion, request=request)
    payload: dict[str, Any] = {
        "schema_version": PHASE4_ROW_LABEL_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE4_ROW_LABEL_CONTEXT_PROTOCOL,
        "phase45_assertion_id": assertion["assertion_id"],
        "phase3_request_id": assertion["phase3_request_id"],
        "phase3_proposal_sha256": assertion.get("phase3_proposal_sha256"),
        "internal_table_uid": assertion["internal_table_uid"],
        "route_id": assertion["route_id"],
        "row_label_relation_id": relation.get("relation_id") if relation else None,
        "source_anchor_id": source_anchor.get("anchor_id") if source_anchor else None,
        "source_cell_role": source_anchor.get("role") if source_anchor else None,
        "source_cell_text": None,
        "source_cell_text_sha256": None,
        "raw_row_index": source_anchor.get("row_index") if source_anchor else None,
        "raw_column_index": source_anchor.get("column_index") if source_anchor else None,
        "status": "UNRESOLVED",
        "evidence_class": "E3",
        "reason_codes": [],
        "derivation_rule": "none_phase4_insufficient_source_row_label",
        "training_eligible": False,
        "certification_allowed": False,
    }
    if reason is not None:
        payload["reason_codes"] = [reason]
        return {"row_label_context_id": _sha_json(payload), **payload}
    source_cell_text = str(source_anchor.get("quoted_text") or "")
    if not source_cell_text:
        payload["reason_codes"] = ["raw_context_cell_is_empty"]
        return {"row_label_context_id": _sha_json(payload), **payload}
    if source_anchor.get("raw_text_sha256") != _text_sha(source_cell_text):
        raise CertifiedCanonicalError("row-label relation source anchor does not bind its raw text")
    payload.update(
        {
            "source_cell_text": source_cell_text,
            "source_cell_text_sha256": _text_sha(source_cell_text),
            "status": "SOURCE_ROW_LABEL_RELATION_CONTEXT_ONLY",
            "evidence_class": "E0",
            "derivation_rule": "phase4_selected_raw_context_cell_row_label_relation_v1",
        }
    )
    return {"row_label_context_id": _sha_json(payload), **payload}


def materialize_phase4_row_label_context(
    *,
    job_manifest: Path,
    requests: Path,
    phase45_assertions: Path,
    phase45_manifest: Path,
    output_dir: Path,
    route_id: str,
) -> Phase4RowLabelContextResult:
    """Create source-row-label context only for heading-less table-semantic assertions."""
    inputs = [path.resolve() for path in (job_manifest, requests, phase45_assertions, phase45_manifest)]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing Phase 4 row-label context input")
    _require_new_output(output_dir, inputs)
    before_hashes = {path.name: sha256_file(path) for path in inputs}
    job = _json(job_manifest)
    if (
        job.get("protocol") != "vifinqa_ccl_phase3_bakeoff_v1"
        or job.get("run_status") != "prepared_phase_3_inference_not_executed"
        or job.get("training_eligible") is not False
        or job.get("model_execution_recorded") is not False
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
    request_rows = {
        str(row.get("request_id")): row
        for row in _jsonl(requests)
        if row.get("route_id") == route_id and row.get("request_id")
    }
    assertions = [
        row
        for row in _jsonl(phase45_assertions)
        if row.get("route_id") == route_id and row.get("field") == "table_semantics"
    ]
    if not assertions:
        raise CertifiedCanonicalError("requested route has no table-semantic assertions")
    seen: set[str] = set()
    selected: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for assertion in assertions:
        _require_non_promotable(assertion, label="Phase 4-5 assertion")
        assertion_id = str(assertion.get("assertion_id") or "")
        request_id = str(assertion.get("phase3_request_id") or "")
        request = request_rows.get(request_id)
        if not assertion_id or assertion_id in seen or request is None:
            raise CertifiedCanonicalError("Phase 4-5 row-label assertion identity is invalid")
        if request.get("internal_table_uid") != assertion.get("internal_table_uid"):
            raise CertifiedCanonicalError("Phase 4-5 row-label assertion has mismatched table identity")
        seen.add(assertion_id)
        relation, _, reason = _row_label_relation(assertion=assertion, request=request)
        if relation is not None or reason not in {"proposal_has_heading_relation"}:
            if relation is not None:
                selected.append((assertion, request))
            elif reason != "proposal_has_heading_relation":
                raise CertifiedCanonicalError("heading-less assertion lacks an exact row-label fallback relation")
    if not selected:
        raise CertifiedCanonicalError("requested route has no heading-less row-label fallback assertions")
    records = [_context_record(assertion=assertion, request=request) for assertion, request in sorted(selected, key=lambda value: str(value[0]["assertion_id"]))]
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if before_hashes != after_hashes:
        raise CertifiedCanonicalError("Phase 4 row-label materialization changed a hash-bound input")
    output_dir.mkdir(parents=True)
    context_path = output_dir / _CONTEXT_NAME
    with context_path.open("x", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    status_counts = Counter(str(record["status"]) for record in records)
    reason_counts = Counter(str(reason) for record in records for reason in (record.get("reason_codes") or []))
    report = {
        "schema_version": PHASE4_ROW_LABEL_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE4_ROW_LABEL_CONTEXT_PROTOCOL,
        "run_status": "phase_4_row_label_context_materialization_complete_not_certified",
        "route_id": route_id,
        "context_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "combine_row_label_context_with_table_structure_before_any_semantic_inference",
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE4_ROW_LABEL_CONTEXT_SCHEMA_VERSION,
        "protocol": PHASE4_ROW_LABEL_CONTEXT_PROTOCOL,
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
    return Phase4RowLabelContextResult(
        output_dir=output_dir,
        context_count=len(records),
        materialized_count=status_counts["SOURCE_ROW_LABEL_RELATION_CONTEXT_ONLY"],
        unresolved_count=status_counts["UNRESOLVED"],
    )
