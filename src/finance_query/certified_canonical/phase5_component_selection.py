"""Bounded source-component selection packets for unresolved CCL table context.

The task deliberately asks a model to select literal source components, not to
write a table-semantic label.  A later renderer can show the chosen components
as context, while certification still requires an independent campaign gate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from finance_query.report_navigation_overlay import (
    MANIFEST_NAME as NAVIGATION_MANIFEST_NAME,
    OVERLAY_NAME as NAVIGATION_OVERLAY_NAME,
    ReportNavigationOverlayError,
    load_report_navigation_overlay,
    require_financial_table_semantic_dispatch,
)
from finance_query.table_structure import sha256_file

from .phase5_table_structure_context import PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL
from .pipeline import CertifiedCanonicalError


PHASE5_COMPONENT_SELECTION_PROTOCOL = "vifinqa_ccl_phase5_component_selection_v1"
PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION = 1
_PACKETS_NAME = "phase5_component_selection_packets_v1.jsonl"
_SCHEMA_NAME = "phase5_component_selection_schema_v1.json"
_REPORT_NAME = "phase5_component_selection_report.json"
_MANIFEST_NAME = "phase5_component_selection_manifest.json"
_RESULTS_NAME = "phase5_component_selection_results_v1.jsonl"
_VALIDATION_REPORT_NAME = "phase5_component_selection_validation_report.json"
_VALIDATION_MANIFEST_NAME = "phase5_component_selection_validation_manifest.json"
_MAX_COMPONENTS = 64
_MAX_LITERAL_CHARACTERS = 2048
_ALLOWED_UNRESOLVED_CONDITIONS = frozenset(
    {"ambiguous_source_components", "insufficient_source_components", "source_text_damage"}
)
_MODEL_ROUTE_STATUSES = frozenset(
    {"DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED", "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY"}
)
_NUMERIC_VALUE_LITERAL = re.compile(
    r"^\s*[\(\[]?[-+−]?\s*(?:\d{1,3}(?:[.,\s]\d{3})+|\d+(?:[.,]\d+)?)\s*%?\s*(?:VND|VNĐ|USD|EUR|đồng|triệu|tỷ|nghìn)?\s*[\)\]]?\s*$",
    re.IGNORECASE,
)
_YEAR_OR_YEAR_UNIT_HEADER = re.compile(
    r"^\s*(?:19|20)\d{2}\s*(?:VND|VNĐ|USD|EUR)?(?:['’]\s*0{3})?\s*$",
    re.IGNORECASE,
)


def _is_nonselectable_numeric_value(literal: str) -> bool:
    """Reject a data value that was misidentified as a selectable header.

    A four-digit year (with an optional currency/unit suffix) is a valid
    period header. Dates retain their separators and do not match the numeric
    value grammar, so they remain selectable source headers as well.
    """
    normalized = literal.strip()
    return bool(_NUMERIC_VALUE_LITERAL.fullmatch(normalized)) and not bool(
        _YEAR_OR_YEAR_UNIT_HEADER.fullmatch(normalized)
    )


@dataclass(frozen=True, slots=True)
class Phase5ComponentSelectionPacketResult:
    output_dir: Path
    packet_count: int
    deterministic_bypass_count: int
    navigation_blocked_count: int


@dataclass(frozen=True, slots=True)
class Phase5ComponentSelectionValidationResult:
    output_dir: Path
    valid_selection_count: int
    abstention_count: int
    invalid_count: int


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


def _require_new_output(output_dir: Path, inputs: Iterable[Path], *, label: str) -> None:
    if output_dir.resolve() in {path.resolve() for path in inputs}:
        raise CertifiedCanonicalError(f"output-dir cannot be a {label} input")
    if output_dir.exists():
        raise FileExistsError(f"output-dir must be new; {label} output is immutable")


def _require_context_manifest(manifest: Mapping[str, Any]) -> None:
    if (
        manifest.get("protocol") != PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL
        or manifest.get("run_status") != "phase_5_table_structure_context_complete_not_certified"
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError("table-structure context manifest is unsupported or promotable")


def _component(*, packet_identity: Mapping[str, Any], role: str, literal: object, provenance: Mapping[str, Any]) -> dict[str, Any]:
    text = str(literal or "").strip()
    if not text:
        raise CertifiedCanonicalError("source component has no literal")
    payload = {
        "packet_identity": dict(packet_identity),
        "role": role,
        "literal": text,
        "literal_sha256": _text_sha(text),
        "provenance": dict(provenance),
    }
    return {"component_id": _sha_json(payload), **payload}


def _task_contract() -> dict[str, Any]:
    return {
        "name": "bounded_literal_table_context_component_selection",
        "instructions": [
            "Select only listed source component IDs.",
            "Do not create a table-type label, paraphrase, numeric value, repair, certificate or training decision.",
            "Use explicit abstention when literal components are insufficient, ambiguous or text-damaged.",
        ],
        "response_contract": {
            "required_top_level_keys": [
                "primary_component_id",
                "supporting_component_ids",
                "unresolved_conditions",
            ],
            "allowed_top_level_keys": [
                "primary_component_id",
                "supporting_component_ids",
                "unresolved_conditions",
            ],
            "primary_component_role": "report_scope_or_selected_relation_context",
            "supporting_component_roles": ["column_header", "row_label"],
            "maximum_supporting_component_ids": 4,
            "allowed_unresolved_conditions": sorted(_ALLOWED_UNRESOLVED_CONDITIONS),
        },
        "proposal_only": True,
        "training_eligible": False,
        "certification_allowed": False,
    }


def _context_identity(context: Mapping[str, Any]) -> dict[str, str]:
    identity = {
        field: str(context.get(field) or "")
        for field in (
            "table_structure_context_id",
            "source_first_semantic_route_id",
            "phase45_assertion_id",
            "phase3_request_id",
            "internal_table_uid",
            "route_id",
        )
    }
    if any(not value for value in identity.values()):
        raise CertifiedCanonicalError("source-structure context has incomplete identity")
    return identity


def _components(context: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    identity = _context_identity(context)
    route_context = context.get("route_context")
    structure = context.get("table_structure")
    if not isinstance(route_context, Mapping) or not isinstance(structure, Mapping):
        raise CertifiedCanonicalError("source-structure context has no route or table structure")
    route_kind = str(route_context.get("kind") or "")
    if route_kind == "literal_numbered_note_heading":
        topic = str(route_context.get("note_topic_literal") or "").strip()
        if not topic or route_context.get("note_topic_sha256") != _text_sha(topic):
            raise CertifiedCanonicalError("numbered-note route context topic is not hash-bound")
        components = [
            _component(
                packet_identity=identity,
                role="report_scope_or_selected_relation_context",
                literal=topic,
                provenance={
                    "kind": "literal_numbered_note_heading",
                    "source_heading_sha256": route_context.get("source_heading_sha256"),
                    "note_topic_sha256": route_context.get("note_topic_sha256"),
                },
            )
        ]
    elif route_kind == "selected_source_cell_relation":
        selected_literal = str(route_context.get("literal") or "").strip()
        if not selected_literal or route_context.get("literal_sha256") != _text_sha(selected_literal):
            raise CertifiedCanonicalError("selected-relation route context literal is not hash-bound")
        components = [
            _component(
                packet_identity=identity,
                role="report_scope_or_selected_relation_context",
                literal=selected_literal,
                provenance={
                    "kind": "selected_source_cell_relation",
                    "raw_row_index": route_context.get("raw_row_index"),
                    "raw_column_index": route_context.get("raw_column_index"),
                    "row_label_relation_id": route_context.get("row_label_relation_id"),
                },
            )
        ]
    else:
        raise CertifiedCanonicalError("only note and row-label source contexts are model-selection eligible")
    columns = structure.get("canonical_columns")
    labels = structure.get("source_row_labels")
    if not isinstance(columns, list) or not isinstance(labels, list):
        raise CertifiedCanonicalError("source-structure context has invalid columns or row labels")
    header_candidate_count = 0
    numeric_value_header_exclusion_count = 0
    for column in columns:
        if not isinstance(column, Mapping):
            raise CertifiedCanonicalError("source-structure context has invalid canonical column")
        for header in column.get("source_header_cells") or []:
            if not isinstance(header, Mapping):
                raise CertifiedCanonicalError("source-structure context has invalid source header cell")
            header_literal = str(header.get("literal") or "").strip()
            if not header_literal or header.get("literal_sha256") != _text_sha(header_literal):
                raise CertifiedCanonicalError("source header component is not hash-bound")
            header_candidate_count += 1
            if _is_nonselectable_numeric_value(header_literal):
                numeric_value_header_exclusion_count += 1
                continue
            components.append(
                _component(
                    packet_identity=identity,
                    role="column_header",
                    literal=header_literal,
                    provenance={
                        "source_table_uid": header.get("source_table_uid"),
                        "raw_row_index": header.get("raw_row_index"),
                        "raw_column_index": header.get("raw_column_index"),
                        "canonical_column_index": column.get("column_index"),
                        "canonical_column_role": column.get("role"),
                    },
                )
            )
    for label in labels:
        if not isinstance(label, Mapping):
            raise CertifiedCanonicalError("source-structure context has invalid source row label")
        label_literal = str(label.get("literal") or "").strip()
        if not label_literal or label.get("literal_sha256") != _text_sha(label_literal):
            raise CertifiedCanonicalError("source row-label component is not hash-bound")
        components.append(
            _component(
                packet_identity=identity,
                role="row_label",
                literal=label_literal,
                provenance={
                    "raw_row_index": label.get("raw_row_index"),
                    "raw_column_index": label.get("raw_column_index"),
                },
            )
        )
    ids = [component["component_id"] for component in components]
    if len(ids) != len(set(ids)):
        raise CertifiedCanonicalError("source-structure context produces duplicate selection components")
    literal_characters = sum(len(str(component["literal"])) for component in components)
    if len(components) > _MAX_COMPONENTS or literal_characters > _MAX_LITERAL_CHARACTERS:
        raise CertifiedCanonicalError("source-structure context exceeds the complete bounded component budget")
    return components, {
        "header_candidate_count": header_candidate_count,
        "numeric_value_header_exclusion_count": numeric_value_header_exclusion_count,
    }


def _packet(context: Mapping[str, Any]) -> dict[str, Any]:
    _require_non_promotable(context, label="source-structure context")
    if context.get("status") != "SOURCE_STRUCTURE_CONTEXT_ONLY" or context.get("llm_dispatch_allowed") is not False:
        raise CertifiedCanonicalError("source-structure context is unsupported or model-dispatchable")
    if context.get("source_first_route_status") not in _MODEL_ROUTE_STATUSES:
        raise CertifiedCanonicalError("only unresolved note/row source routes can build component-selection packets")
    identity = _context_identity(context)
    components, component_menu_stats = _components(context)
    payload = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        **identity,
        "source_first_route_status": context["source_first_route_status"],
        "task": _task_contract(),
        "components": components,
        "component_menu_stats": component_menu_stats,
        "source_quality_status": (context.get("table_structure") or {}).get("source_quality_status"),
        "source_quality_reason_codes": (context.get("table_structure") or {}).get("source_quality_reason_codes"),
        "llm_dispatch_allowed": False,
        "campaign_candidate_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    return {"component_selection_packet_id": _sha_json(payload), **payload}


def render_phase5_component_selection_prompt(packet: Mapping[str, Any]) -> str:
    """Render the finite selection menu without exposing a semantic-label field."""
    if (
        packet.get("protocol") != PHASE5_COMPONENT_SELECTION_PROTOCOL
        or int(packet.get("schema_version") or 0) != PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION
        or packet.get("llm_dispatch_allowed") is not False
        or packet.get("training_eligible") is not False
        or packet.get("certification_allowed") is not False
        or packet.get("task") != _task_contract()
    ):
        raise CertifiedCanonicalError("component-selection packet is unsupported or promotable")
    components = packet.get("components")
    if not isinstance(components, list) or not components:
        raise CertifiedCanonicalError("component-selection packet has no finite source menu")
    menu = [
        {
            "component_id": component.get("component_id"),
            "role": component.get("role"),
            "literal": component.get("literal"),
        }
        for component in components
        if isinstance(component, Mapping)
    ]
    if len(menu) != len(components) or any(not item["component_id"] or not item["literal"] for item in menu):
        raise CertifiedCanonicalError("component-selection packet has malformed source components")
    if any(item["role"] == "column_header" and _is_nonselectable_numeric_value(str(item["literal"])) for item in menu):
        raise CertifiedCanonicalError("component-selection packet exposes a numeric data value as a column header")
    return "\n".join(
        (
            "You select literal source components for a Vietnamese financial-report table.",
            "Return exactly one JSON object. Do not use Markdown or prose.",
            "Do not infer or write a semantic table label, paraphrase any source, repair text, state a number, certify, or make a training decision.",
            "primary_component_id must be the single listed report_scope_or_selected_relation_context component, or null only when abstaining.",
            "supporting_component_ids may contain at most 4 listed column_header or row_label components; do not repeat an ID.",
            "If abstaining, select no IDs and use one or more unresolved_conditions from: ambiguous_source_components, insufficient_source_components, source_text_damage.",
            "The only top-level keys are primary_component_id, supporting_component_ids, unresolved_conditions.",
            "Always include all three keys, including unresolved_conditions: [] when none applies.",
            "Required JSON shape (replace placeholders; do not add keys):",
            '{"primary_component_id":"<listed root ID or null>","supporting_component_ids":["<up to four listed header/row IDs>"],"unresolved_conditions":[]}',
            "Source component menu:",
            json.dumps(menu, ensure_ascii=False, sort_keys=True),
        )
    )


def build_phase5_component_selection_packets(
    *,
    table_structure_contexts: Path,
    table_structure_context_manifest: Path,
    navigation_overlay_dir: Path,
    output_dir: Path,
    route_id: str,
) -> Phase5ComponentSelectionPacketResult:
    """Build complete, finite component menus without invoking a model."""
    navigation_overlay_dir = navigation_overlay_dir.resolve()
    inputs = [
        table_structure_contexts.resolve(),
        table_structure_context_manifest.resolve(),
        navigation_overlay_dir / NAVIGATION_OVERLAY_NAME,
        navigation_overlay_dir / NAVIGATION_MANIFEST_NAME,
    ]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing source-structure context or navigation-overlay input")
    _require_new_output(output_dir, inputs, label="component-selection packet")
    before_hashes = {path.name: sha256_file(path) for path in inputs}
    manifest = _json(table_structure_context_manifest)
    _require_context_manifest(manifest)
    _require_hash(
        table_structure_contexts,
        ((manifest.get("outputs") or {}).get(table_structure_contexts.name) or {}).get("sha256"),
        label="table-structure contexts",
    )
    contexts = [row for row in _jsonl(table_structure_contexts) if row.get("route_id") == route_id]
    if not contexts:
        raise CertifiedCanonicalError("requested route has no source-structure contexts")
    context_ids = [str(row.get("phase45_assertion_id") or "") for row in contexts]
    if not all(context_ids) or len(context_ids) != len(set(context_ids)):
        raise CertifiedCanonicalError("source-structure contexts have invalid assertion identities")
    navigation_overlay = load_report_navigation_overlay(navigation_overlay_dir)
    dispatchable_contexts: list[dict[str, Any]] = []
    navigation_blocked_contexts: list[dict[str, Any]] = []
    for context in contexts:
        table_uid = str(context.get("internal_table_uid") or "")
        overlay_record = navigation_overlay.get(table_uid)
        if overlay_record is None:
            raise CertifiedCanonicalError("navigation overlay lacks a source-structure context table")
        if overlay_record.get("status") == "TABLE_OF_CONTENTS_DETECTED":
            navigation_blocked_contexts.append(context)
            continue
        try:
            require_financial_table_semantic_dispatch(navigation_overlay, table_uid)
        except ReportNavigationOverlayError as error:
            raise CertifiedCanonicalError("navigation overlay rejected a non-contents source-structure context") from error
        dispatchable_contexts.append(context)
    packets = [
        _packet(context)
        for context in sorted(
            (row for row in dispatchable_contexts if row.get("source_first_route_status") in _MODEL_ROUTE_STATUSES),
            key=lambda row: str(row["phase45_assertion_id"]),
        )
    ]
    deterministic_bypass_count = sum(
        1
        for context in dispatchable_contexts
        if context.get("source_first_route_status") == "DETERMINISTIC_SOURCE_PROFILE_CONTEXT_ONLY"
    )
    if len(packets) + deterministic_bypass_count + len(navigation_blocked_contexts) != len(contexts):
        raise CertifiedCanonicalError("source-structure contexts contain an unsupported source-first route")
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("component-selection packet construction changed a hash-bound input")
    output_dir.mkdir(parents=True)
    packets_path = output_dir / _PACKETS_NAME
    with packets_path.open("x", encoding="utf-8") as file:
        for packet in packets:
            file.write(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n")
    schema = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        "task": _task_contract(),
        "model_execution_allowed": False,
        "navigation_overlay_required": True,
        "training_eligible": False,
        "certification_allowed": False,
    }
    schema_path = output_dir / _SCHEMA_NAME
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    component_counts = [len(packet["components"]) for packet in packets]
    header_candidate_count = sum(packet["component_menu_stats"]["header_candidate_count"] for packet in packets)
    numeric_value_header_exclusion_count = sum(
        packet["component_menu_stats"]["numeric_value_header_exclusion_count"] for packet in packets
    )
    report = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        "route_id": route_id,
        "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
        "source_context_count": len(contexts),
        "packet_count": len(packets),
        "deterministic_bypass_count": deterministic_bypass_count,
        "navigation_blocked_count": len(navigation_blocked_contexts),
        "navigation_blocked_table_uids": sorted(
            str(context["internal_table_uid"]) for context in navigation_blocked_contexts
        ),
        "component_count_minimum": min(component_counts) if component_counts else 0,
        "component_count_maximum": max(component_counts) if component_counts else 0,
        "header_candidate_count": header_candidate_count,
        "numeric_value_header_exclusion_count": numeric_value_header_exclusion_count,
        "input_hashes_unchanged": True,
        "next_gate": "validate_small_closed_world_component_selection_smoke_before_any_gpu_dispatch",
        "llm_dispatch_allowed": False,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
    }
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    output_manifest = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        "route_id": route_id,
        "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
        "inputs": {path.name: {"sha256": before_hashes[path.name]} for path in inputs},
        "outputs": {name: {"sha256": sha256_file(output_dir / name)} for name in (_PACKETS_NAME, _SCHEMA_NAME, _REPORT_NAME)},
        "input_hashes_unchanged": True,
        "navigation_overlay_required": True,
        "llm_dispatch_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / _MANIFEST_NAME).write_text(
        json.dumps(output_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return Phase5ComponentSelectionPacketResult(
        output_dir=output_dir,
        packet_count=len(packets),
        deterministic_bypass_count=deterministic_bypass_count,
        navigation_blocked_count=len(navigation_blocked_contexts),
    )


def _parse_response(value: object) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise CertifiedCanonicalError("response is not one JSON object") from error
    if not isinstance(value, Mapping):
        raise CertifiedCanonicalError("response is not one JSON object")
    return value


def _validate_selection(packet: Mapping[str, Any], response: object) -> dict[str, Any]:
    parsed = _parse_response(response)
    contract = ((packet.get("task") or {}).get("response_contract") or {})
    required = contract.get("required_top_level_keys")
    if not isinstance(required, list) or set(parsed) != set(required):
        raise CertifiedCanonicalError("response has keys outside the closed-world component-selection contract")
    primary = parsed.get("primary_component_id")
    supporting = parsed.get("supporting_component_ids")
    unresolved = parsed.get("unresolved_conditions")
    if primary is not None and not isinstance(primary, str):
        raise CertifiedCanonicalError("primary_component_id must be a string or null")
    if not isinstance(supporting, list) or not all(isinstance(value, str) for value in supporting):
        raise CertifiedCanonicalError("supporting_component_ids must be a string list")
    if not isinstance(unresolved, list) or not all(isinstance(value, str) for value in unresolved):
        raise CertifiedCanonicalError("unresolved_conditions must be a string list")
    if len(supporting) > int(contract.get("maximum_supporting_component_ids") or 0):
        raise CertifiedCanonicalError("response exceeds the supporting-component limit")
    if len(supporting) != len(set(supporting)) or primary in set(supporting):
        raise CertifiedCanonicalError("response repeats a selected component")
    if not set(unresolved).issubset(_ALLOWED_UNRESOLVED_CONDITIONS) or len(unresolved) != len(set(unresolved)):
        raise CertifiedCanonicalError("response contains an unsupported unresolved condition")
    components = {str(component.get("component_id")): component for component in packet.get("components") or []}
    if not components or len(components) != len(packet.get("components") or []):
        raise CertifiedCanonicalError("packet component menu is invalid")
    if primary is None:
        if supporting or not unresolved:
            raise CertifiedCanonicalError("abstention must select no components and state a closed-world condition")
        return {"status": "VALID_ABSTENTION_ONLY", "primary_component_id": None, "supporting_component_ids": [], "unresolved_conditions": unresolved}
    primary_component = components.get(primary)
    if primary_component is None or primary_component.get("role") != "report_scope_or_selected_relation_context":
        raise CertifiedCanonicalError("primary component is not a permitted source context")
    for value in supporting:
        component = components.get(value, {})
        if component.get("role") not in {"column_header", "row_label"}:
            raise CertifiedCanonicalError("supporting component is not a permitted header or row label")
        if component.get("role") == "column_header" and _is_nonselectable_numeric_value(str(component.get("literal") or "")):
            raise CertifiedCanonicalError("supporting component is a nonselectable numeric data value")
    return {
        "status": "VALID_COMPONENT_SELECTION_ONLY",
        "primary_component_id": primary,
        "supporting_component_ids": supporting,
        "unresolved_conditions": unresolved,
    }


def validate_phase5_component_selection_responses(
    *, packets: Path, packet_manifest: Path, raw_responses: Path, output_dir: Path, route_id: str
) -> Phase5ComponentSelectionValidationResult:
    """Validate only closed-world component selections from a declared packet set."""
    inputs = [packets.resolve(), packet_manifest.resolve(), raw_responses.resolve()]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("missing component-selection response validation input")
    _require_new_output(output_dir, inputs, label="component-selection validation")
    before_hashes = {path.name: sha256_file(path) for path in inputs}
    manifest = _json(packet_manifest)
    if (
        manifest.get("protocol") != PHASE5_COMPONENT_SELECTION_PROTOCOL
        or manifest.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
    ):
        raise CertifiedCanonicalError("component-selection packet manifest is unsupported or promotable")
    _require_hash(
        packets,
        ((manifest.get("outputs") or {}).get(packets.name) or {}).get("sha256"),
        label="component-selection packets",
    )
    packet_rows = [row for row in _jsonl(packets) if row.get("route_id") == route_id]
    packet_by_id = {str(row.get("component_selection_packet_id") or ""): row for row in packet_rows}
    if not packet_by_id or len(packet_by_id) != len(packet_rows):
        raise CertifiedCanonicalError("component-selection packet identities are invalid")
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for row in _jsonl(raw_responses):
        packet_id = str(row.get("component_selection_packet_id") or "")
        if set(row) != {"component_selection_packet_id", "response"} or not packet_id or packet_id in raw_by_id:
            raise CertifiedCanonicalError("raw response envelope is invalid")
        raw_by_id[packet_id] = row
    if set(raw_by_id) != set(packet_by_id):
        raise CertifiedCanonicalError("raw responses do not exactly cover the immutable packet set")
    results: list[dict[str, Any]] = []
    for packet_id in sorted(packet_by_id):
        packet = packet_by_id[packet_id]
        payload = {
            "schema_version": PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION,
            "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
            "component_selection_packet_id": packet_id,
            "phase45_assertion_id": packet["phase45_assertion_id"],
            "internal_table_uid": packet["internal_table_uid"],
            "route_id": route_id,
            "status": "INVALID_UNRESOLVED",
            "selection": None,
            "reason_codes": [],
            "training_eligible": False,
            "certification_allowed": False,
        }
        try:
            selection = _validate_selection(packet, raw_by_id[packet_id]["response"])
            payload.update({"status": selection["status"], "selection": selection})
        except CertifiedCanonicalError as error:
            payload["reason_codes"] = [str(error)]
        payload["component_selection_result_id"] = _sha_json(payload)
        results.append(payload)
    after_hashes = {path.name: sha256_file(path) for path in inputs}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("component-selection response validation changed a hash-bound input")
    output_dir.mkdir(parents=True)
    results_path = output_dir / _RESULTS_NAME
    with results_path.open("x", encoding="utf-8") as file:
        for result in results:
            file.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    counts = Counter(result["status"] for result in results)
    report = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        "route_id": route_id,
        "run_status": "phase_5_component_selection_validation_complete_not_certified",
        "status_counts": dict(sorted(counts.items())),
        "input_hashes_unchanged": True,
        "next_gate": "calibrate_component_selection_against_independent_human_campaign_samples",
        "training_eligible_output_count": 0,
        "certification_allowed": False,
    }
    report_path = output_dir / _VALIDATION_REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    output_manifest = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SCHEMA_VERSION,
        "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
        "route_id": route_id,
        "run_status": "phase_5_component_selection_validation_complete_not_certified",
        "inputs": {path.name: {"sha256": before_hashes[path.name]} for path in inputs},
        "outputs": {name: {"sha256": sha256_file(output_dir / name)} for name in (_RESULTS_NAME, _VALIDATION_REPORT_NAME)},
        "input_hashes_unchanged": True,
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / _VALIDATION_MANIFEST_NAME).write_text(
        json.dumps(output_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return Phase5ComponentSelectionValidationResult(
        output_dir=output_dir,
        valid_selection_count=counts["VALID_COMPONENT_SELECTION_ONLY"],
        abstention_count=counts["VALID_ABSTENTION_ONLY"],
        invalid_count=counts["INVALID_UNRESOLVED"],
    )
