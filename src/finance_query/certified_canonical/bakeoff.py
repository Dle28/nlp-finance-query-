"""Fail-closed Phase 3 bake-off job construction.

This module intentionally prepares model requests but does not execute a
model.  Execution must happen in a separately recorded GPU job, whose responses
are later checked against the request and source-anchor contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO

import yaml

from finance_query.table_structure import sha256_file

from .evidence_graph import EvidenceGraphError, validate_evidence_graph
from .pipeline import CERTIFIED_CANONICAL_PROTOCOL, CertifiedCanonicalError


BAKEOFF_PROTOCOL = "vifinqa_ccl_phase3_bakeoff_v1"
BAKEOFF_SCHEMA_VERSION = 1
_MAX_MODEL_PARAMETERS_BILLIONS = 14.7
_OUTPUT_NAMES = (
    "llm_bakeoff_requests_v1.jsonl",
    "llm_proposal_schema_v1.json",
    "bakeoff_plan_report.json",
)

# Kept in the job builder so a request records the exact allowed finite
# evidence relation types before any model executes.  The executor imports
# this constant rather than maintaining a second, drift-prone table.
PROPOSAL_FIELD_RELATION_TYPES: dict[str, frozenset[str]] = {
    "heading_context": frozenset({"heading_scopes_table"}),
    "table_semantics": frozenset({"heading_scopes_table", "row_label_defines_value"}),
    "period_context": frozenset({"period_applies_to_column"}),
    "unit_context": frozenset({"unit_applies_to_column"}),
}
_DEFAULT_TASK_FIELDS = ("table_semantics", "heading_context", "period_context", "unit_context")
_DEFAULT_TASK_INSTRUCTIONS = (
    "Propose only values supported by the supplied packet.",
    "Give source anchors and competing alternatives for every proposal.",
    "State unresolved conditions instead of guessing.",
    "Never emit certification, training eligibility, a numeric replacement, or a release decision.",
)


@dataclass(frozen=True, slots=True)
class BakeoffResult:
    output_dir: Path
    manifest_path: Path
    request_count: int
    route_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class _RequestCompaction:
    """Deterministic display budget for one model request.

    The canonical packet remains immutable.  This applies only to the
    proposal-only copy carried by a GPU request, which must still expose a
    valid finite subgraph.  Oversized source text becomes an explicitly marked
    preview, never a replacement for the hash-bound raw cell.
    """

    max_request_payload_characters: int
    max_model_input_tokens: int
    max_anchor_display_characters: int
    max_selectable_anchors: int
    max_relations: int

    def as_dict(self) -> dict[str, int]:
        return {
            "max_request_payload_characters": self.max_request_payload_characters,
            "max_model_input_tokens": self.max_model_input_tokens,
            "max_anchor_display_characters": self.max_anchor_display_characters,
            "max_selectable_anchors": self.max_selectable_anchors,
            "max_relations": self.max_relations,
        }


def _sha_json(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"{path}:{line_number} is not a JSON object")
            yield value


def _write_json_line(file: TextIO, value: Mapping[str, Any]) -> None:
    file.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _resolve(repo_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (repo_root / path).resolve()


def _require_new_output(path: Path, *, inputs: list[Path]) -> None:
    if path.resolve() in {item.resolve() for item in inputs}:
        raise CertifiedCanonicalError("output-dir cannot be a bake-off input")
    if path.exists():
        raise FileExistsError("output-dir must be new; bake-off jobs are immutable")
    path.mkdir(parents=True)


def _read_inputs(config_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    config_path = config_path.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if (
        config.get("protocol") != BAKEOFF_PROTOCOL
        or int(config.get("schema_version") or 0) != BAKEOFF_SCHEMA_VERSION
    ):
        raise CertifiedCanonicalError("unsupported CCL Phase 3 bake-off config")
    repo_root = config_path.parent.parent.resolve()
    declared = config.get("inputs") or {}
    required = ("ccl_release_manifest", "benchmark_packets")
    if any(not declared.get(key) for key in required):
        raise CertifiedCanonicalError("bake-off config must declare CCL manifest and packet input")
    paths = {key: _resolve(repo_root, declared[key]) for key in required}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    paths["config"] = config_path
    return config, paths


def _validate_routes(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    routes = config.get("routes") or []
    if not isinstance(routes, list) or not routes:
        raise CertifiedCanonicalError("bake-off config must define at least one model route")
    ids: set[str] = set()
    validated: list[dict[str, Any]] = []
    for item in routes:
        if not isinstance(item, dict):
            raise CertifiedCanonicalError("each bake-off route must be a mapping")
        route_id = str(item.get("route_id") or "")
        model_id = str(item.get("model_id") or "")
        revision = str(item.get("revision") or "")
        if not route_id or not model_id or not revision or route_id in ids:
            raise CertifiedCanonicalError("route_id, model_id and revision must be unique non-empty values")
        ids.add(route_id)
        parameter_count = float(item.get("parameter_count_billions") or 0)
        if not 0 < parameter_count < _MAX_MODEL_PARAMETERS_BILLIONS:
            raise CertifiedCanonicalError(
                f"route {route_id} violates strict <{_MAX_MODEL_PARAMETERS_BILLIONS}B parameter policy"
            )
        modality = str(item.get("modality") or "")
        if modality not in {"text", "vision"}:
            raise CertifiedCanonicalError(f"route {route_id} modality must be text or vision")
        buckets = item.get("buckets")
        packet_ids = item.get("packet_ids")
        if packet_ids is not None:
            if buckets not in (None, []):
                raise CertifiedCanonicalError(f"route {route_id} must use buckets or packet_ids, not both")
            if (
                not isinstance(packet_ids, list)
                or not packet_ids
                or not all(isinstance(packet_id, str) and packet_id for packet_id in packet_ids)
                or len(set(packet_ids)) != len(packet_ids)
            ):
                raise CertifiedCanonicalError(f"route {route_id} must declare unique non-empty packet_ids")
            normalized_buckets: list[str] = []
            normalized_packet_ids: list[str] | None = list(packet_ids)
        else:
            if not isinstance(buckets, list) or not buckets or not all(
                isinstance(bucket, str) and bucket for bucket in buckets
            ):
                raise CertifiedCanonicalError(f"route {route_id} must declare non-empty buckets")
            normalized_buckets = list(buckets)
            normalized_packet_ids = None
        max_packets = int(item.get("max_packets") or 0)
        if max_packets < 1:
            raise CertifiedCanonicalError(f"route {route_id} max_packets must be positive")
        if normalized_packet_ids is not None and max_packets != len(normalized_packet_ids):
            raise CertifiedCanonicalError(
                f"route {route_id} max_packets must equal packet_ids length to prevent partial exact selection"
            )
        enabled = bool(item.get("enabled", False))
        disabled_reason = str(item.get("disabled_reason") or "")
        if not enabled and not disabled_reason:
            raise CertifiedCanonicalError(f"disabled route {route_id} must declare disabled_reason")
        validated.append(
            {
                "route_id": route_id,
                "model_id": model_id,
                "revision": revision,
                "parameter_count_billions": parameter_count,
                "modality": modality,
                "enabled": enabled,
                "disabled_reason": disabled_reason or None,
                "buckets": normalized_buckets,
                "packet_ids": normalized_packet_ids,
                "max_packets": max_packets,
                "purpose": str(item.get("purpose") or "semantic_proposal"),
            }
        )
    if not any(route["enabled"] for route in validated):
        raise CertifiedCanonicalError("at least one model route must be enabled for a bake-off job")
    return validated


def _default_task_contract() -> dict[str, Any]:
    return {
        "name": "evidence_anchored_semantic_proposal",
        "fields": list(_DEFAULT_TASK_FIELDS),
        "instructions": list(_DEFAULT_TASK_INSTRUCTIONS),
        "field_value_constraints": {},
        "field_relation_constraints": {},
    }


def _validate_task_contract(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return an immutable request-task contract, optionally schema-constrained.

    Values and relation types are whitelisted at job-build time.  This makes a
    focused smoke verifiable by the same deterministic validator rather than
    relying on prompt wording or model self-report.
    """
    raw = config.get("task_contract")
    if raw is None:
        return _default_task_contract()
    if not isinstance(raw, Mapping):
        raise CertifiedCanonicalError("task_contract must be a mapping")
    expected = {"name", "fields", "instructions", "field_value_constraints", "field_relation_constraints"}
    if set(raw) != expected:
        raise CertifiedCanonicalError("task_contract must declare exactly: " + ", ".join(sorted(expected)))
    name = str(raw.get("name") or "")
    fields = raw.get("fields")
    instructions = raw.get("instructions")
    if not name or not isinstance(fields, list) or not fields or len(set(fields)) != len(fields):
        raise CertifiedCanonicalError("task_contract name and unique non-empty fields are required")
    if not all(isinstance(field, str) and field in PROPOSAL_FIELD_RELATION_TYPES for field in fields):
        raise CertifiedCanonicalError("task_contract contains an unsupported proposal field")
    if not isinstance(instructions, list) or not all(isinstance(item, str) and item for item in instructions):
        raise CertifiedCanonicalError("task_contract instructions must be non-empty strings")
    raw_value_constraints = raw.get("field_value_constraints")
    raw_relation_constraints = raw.get("field_relation_constraints")
    if not isinstance(raw_value_constraints, Mapping) or not isinstance(raw_relation_constraints, Mapping):
        raise CertifiedCanonicalError("task_contract field constraints must be mappings")
    value_constraints: dict[str, dict[str, list[str]]] = {}
    for field, constraint in raw_value_constraints.items():
        if field not in fields or not isinstance(constraint, Mapping) or set(constraint) != {"allowed_proposed_values"}:
            raise CertifiedCanonicalError("task_contract value constraint is malformed")
        allowed_values = constraint.get("allowed_proposed_values")
        if (
            not isinstance(allowed_values, list)
            or not allowed_values
            or not all(isinstance(value, str) and value for value in allowed_values)
            or len(set(allowed_values)) != len(allowed_values)
        ):
            raise CertifiedCanonicalError("task_contract allowed_proposed_values must be unique non-empty strings")
        value_constraints[str(field)] = {"allowed_proposed_values": list(allowed_values)}
    relation_constraints: dict[str, list[str]] = {}
    for field, relation_types in raw_relation_constraints.items():
        if field not in fields or not isinstance(relation_types, list) or not relation_types:
            raise CertifiedCanonicalError("task_contract relation constraint is malformed")
        if (
            not all(isinstance(relation_type, str) and relation_type for relation_type in relation_types)
            or len(set(relation_types)) != len(relation_types)
            or not set(relation_types).issubset(PROPOSAL_FIELD_RELATION_TYPES[str(field)])
        ):
            raise CertifiedCanonicalError("task_contract relation constraint is not allowed for its field")
        relation_constraints[str(field)] = list(relation_types)
    return {
        "name": name,
        "fields": list(fields),
        "instructions": list(instructions),
        "field_value_constraints": value_constraints,
        "field_relation_constraints": relation_constraints,
    }


def _validate_request_compaction(config: Mapping[str, Any]) -> _RequestCompaction | None:
    raw = config.get("request_compaction")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise CertifiedCanonicalError("request_compaction must be a mapping")
    expected = {
        "max_request_payload_characters",
        "max_model_input_tokens",
        "max_anchor_display_characters",
        "max_selectable_anchors",
        "max_relations",
    }
    if set(raw) != expected:
        raise CertifiedCanonicalError(
            "request_compaction must declare exactly: " + ", ".join(sorted(expected))
        )
    try:
        values = {key: int(raw[key]) for key in expected}
    except (TypeError, ValueError) as error:
        raise CertifiedCanonicalError("request_compaction values must be integers") from error
    if not 4_000 <= values["max_request_payload_characters"] <= 40_000:
        raise CertifiedCanonicalError("max_request_payload_characters must be between 4000 and 40000")
    if not 512 <= values["max_model_input_tokens"] <= 8_192:
        raise CertifiedCanonicalError("max_model_input_tokens must be between 512 and 8192")
    if not 64 <= values["max_anchor_display_characters"] <= 4_096:
        raise CertifiedCanonicalError("max_anchor_display_characters must be between 64 and 4096")
    if not 4 <= values["max_selectable_anchors"] <= 128:
        raise CertifiedCanonicalError("max_selectable_anchors must be between 4 and 128")
    if not 1 <= values["max_relations"] <= 128:
        raise CertifiedCanonicalError("max_relations must be between 1 and 128")
    return _RequestCompaction(**values)


def _validate_ccl_lineage(paths: Mapping[str, Path]) -> dict[str, Any]:
    manifest = json.loads(paths["ccl_release_manifest"].read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") != CERTIFIED_CANONICAL_PROTOCOL
        or manifest.get("run_status") != "complete_phase_0_to_2_research_only"
        or manifest.get("training_eligible") is not False
        or manifest.get("promotion_allowed") is not False
    ):
        raise CertifiedCanonicalError("CCL manifest is not a non-promotable Phase 0-2 result")
    output = (manifest.get("outputs") or {}).get(paths["benchmark_packets"].name) or {}
    if output.get("sha256") != sha256_file(paths["benchmark_packets"]):
        raise CertifiedCanonicalError("benchmark packets do not match the declared CCL release manifest")
    return manifest


def _proposal_schema(task_contract: Mapping[str, Any]) -> dict[str, Any]:
    """The only admissible model response shape; certification is forbidden."""
    return {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "response_contract": {
            # The executor, not the model, owns request/model identity.  Asking
            # a model to echo four long identifiers wastes generation budget and
            # creates a second, weaker copy of an already hash-bound envelope.
            "response_contract_version": "relation_only_json_v3",
            "required_top_level_keys": ["proposals", "unresolved_conditions"],
            "allowed_top_level_keys": ["proposals", "unresolved_conditions"],
            "proposals_item_required": [
                "field",
                "proposed_value",
                "evidence_relation_ids",
                "alternative_candidates",
                "confidence_not_for_promotion",
            ],
            "derived_evidence_contract": {
                "evidence_anchor_ids": "derived deterministically from each selected relation's selectable endpoint",
                "model_must_not_emit_evidence_anchor_ids": True,
            },
            "alternative_candidate_contract": {
                "empty_list_means_no_source_bounded_alternative": True,
                "unresolved_object": {"status": "unresolved"},
                "otherwise_required": [
                    "status",
                    "proposed_value",
                    "evidence_anchor_ids",
                    "evidence_relation_ids",
                ],
            },
            "forbidden_keys": [
                "CERTIFIED",
                "training_eligible",
                "promotion_allowed",
                "raw_value_replacement",
            ],
            "evidence_selection_contract": {
                "anchor_ids": "validator derives only packet.evidence_graph selectable anchor IDs",
                "relation_ids": "must select only packet.evidence_graph relation IDs",
                "free_text_citations_allowed": False,
                "global_uniqueness_claim_allowed": False,
            },
            "policy": "proposal_only_deterministic_verifier_is_the_only_certification_authority",
            "task_contract": dict(task_contract),
        },
    }


_RELATION_PRIORITY = {
    "heading_scopes_table": 0,
    "period_applies_to_column": 1,
    "unit_applies_to_column": 2,
    "header_applies_to_column": 3,
    "row_label_defines_value": 4,
}
_ANCHOR_PRIORITY = {
    "context_before": 0,
    "header": 1,
    "row_label": 2,
    "visible_cell": 3,
}


def _display_anchor(anchor: Mapping[str, Any], *, character_limit: int) -> dict[str, Any]:
    """Render source text within a declared GPU budget without changing identity."""
    rendered = dict(anchor)
    text = str(rendered.get("quoted_text") or "")
    if len(text) > character_limit:
        rendered["quoted_text"] = text[:character_limit]
        rendered["quoted_text_display_status"] = "PREFIX_TRUNCATED_RAW_TEXT_REMAINS_HASH_BOUND"
        rendered["quoted_text_full_character_count"] = len(text)
    return rendered


def _compact_evidence_graph(
    *,
    packet: Mapping[str, Any],
    selected_anchor_ids: set[str],
    selected_relation_ids: set[str],
    compaction: _RequestCompaction,
) -> dict[str, Any]:
    source_graph = packet["evidence_graph"]
    anchors = {
        str(anchor["anchor_id"]): anchor
        for anchor in source_graph.get("anchors") or []
        if isinstance(anchor, Mapping)
    }
    relations = {
        str(relation["relation_id"]): relation
        for relation in source_graph.get("relations") or []
        if isinstance(relation, Mapping)
    }
    graph = {
        "schema_version": source_graph["schema_version"],
        "protocol": source_graph["protocol"],
        "internal_table_uid": source_graph["internal_table_uid"],
        "graph_scope": "phase_3_request_compact_evidence_subgraph",
        "candidate_universe": {
            "parent_evidence_graph_id": source_graph["evidence_graph_id"],
            "parent_anchor_count": len(anchors),
            "parent_relation_count": len(relations),
            "selection_policy": "deterministic_relation_then_anchor_priority_v1",
            "display_contract": "omitted_nodes_are_not_selectable; truncated_text_is_a_preview_of_hash_bound_raw_text",
        },
        "anchors": [
            _display_anchor(anchors[anchor_id], character_limit=compaction.max_anchor_display_characters)
            for anchor_id in sorted(selected_anchor_ids)
        ],
        "relations": [dict(relations[relation_id]) for relation_id in sorted(selected_relation_ids)],
        "temporal_semantics": {
            "scope": (source_graph.get("temporal_semantics") or {}).get("scope"),
            "scope_status": (source_graph.get("temporal_semantics") or {}).get("scope_status"),
            "columns": [],
        },
        "training_eligible": False,
        "promotion_allowed": False,
    }
    graph["evidence_graph_id"] = _sha_json(graph)
    return graph


def _compact_packet_shell(
    *,
    packet: Mapping[str, Any],
    graph: Mapping[str, Any],
    compaction: _RequestCompaction,
) -> dict[str, Any]:
    return {
        "benchmark_packet_id": packet["benchmark_packet_id"],
        "internal_table_uid": packet["internal_table_uid"],
        "candidate_bucket": packet["candidate_bucket"],
        "source_identity": dict(packet.get("source_identity") or {}),
        "outside_table_context": {},
        "canonical_columns": [],
        "row_preview": [],
        "row_preview_cell_provenance": [],
        "evidence_graph": dict(graph),
        "packet_compaction": {
            "source_packet_sha256": _sha_json(packet),
            "source_evidence_graph_id": packet["evidence_graph"]["evidence_graph_id"],
            "policy": compaction.as_dict(),
        },
    }


def _request_payload_character_count(
    packet: Mapping[str, Any], route: Mapping[str, Any], task_contract: Mapping[str, Any] | None = None
) -> int:
    return len(json.dumps(_request_payload(packet, route, task_contract), ensure_ascii=False, sort_keys=True))


def _compact_packet_for_route(
    packet: Mapping[str, Any],
    route: Mapping[str, Any],
    compaction: _RequestCompaction | None,
    task_contract: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an original or compact, validator-safe packet for one GPU route."""
    source_payload_characters = _request_payload_character_count(packet, route, task_contract)
    source_graph = packet["evidence_graph"]
    if compaction is None:
        return dict(packet), {
            "status": "unbounded_by_config",
            "source_payload_characters": source_payload_characters,
            "request_payload_characters": source_payload_characters,
        }
    if source_payload_characters <= compaction.max_request_payload_characters:
        return dict(packet), {
            "status": "original_packet_within_budget",
            "source_payload_characters": source_payload_characters,
            "request_payload_characters": source_payload_characters,
        }

    anchors = {
        str(anchor["anchor_id"]): anchor
        for anchor in source_graph.get("anchors") or []
        if isinstance(anchor, Mapping)
    }
    relations = {
        str(relation["relation_id"]): relation
        for relation in source_graph.get("relations") or []
        if isinstance(relation, Mapping)
    }
    selected_anchor_ids: set[str] = set()
    selected_relation_ids: set[str] = set()

    def selectable_count(anchor_ids: set[str]) -> int:
        return sum(bool(anchors[anchor_id].get("selectable")) for anchor_id in anchor_ids)

    def candidate_packet(anchor_ids: set[str], relation_ids: set[str]) -> dict[str, Any]:
        graph = _compact_evidence_graph(
            packet=packet,
            selected_anchor_ids=anchor_ids,
            selected_relation_ids=relation_ids,
            compaction=compaction,
        )
        return _compact_packet_shell(packet=packet, graph=graph, compaction=compaction)

    base = candidate_packet(selected_anchor_ids, selected_relation_ids)
    if _request_payload_character_count(base, route, task_contract) > compaction.max_request_payload_characters:
        raise CertifiedCanonicalError("request compaction budget cannot encode the mandatory packet identity")

    sorted_relations = sorted(
        relations.values(),
        key=lambda relation: (
            _RELATION_PRIORITY.get(str(relation.get("relation_type")), len(_RELATION_PRIORITY)),
            str(relation["relation_id"]),
        ),
    )
    for relation in sorted_relations:
        if len(selected_relation_ids) >= compaction.max_relations:
            break
        relation_id = str(relation["relation_id"])
        candidate_anchor_ids = selected_anchor_ids | {
            str(relation["source_anchor_id"]),
            str(relation["target_anchor_id"]),
        }
        candidate_relation_ids = selected_relation_ids | {relation_id}
        if selectable_count(candidate_anchor_ids) > compaction.max_selectable_anchors:
            continue
        candidate = candidate_packet(candidate_anchor_ids, candidate_relation_ids)
        if _request_payload_character_count(candidate, route, task_contract) <= compaction.max_request_payload_characters:
            selected_anchor_ids = candidate_anchor_ids
            selected_relation_ids = candidate_relation_ids

    sorted_anchors = sorted(
        anchors.values(),
        key=lambda anchor: (
            _ANCHOR_PRIORITY.get(str(anchor.get("role")), len(_ANCHOR_PRIORITY)),
            str(anchor["anchor_id"]),
        ),
    )
    for anchor in sorted_anchors:
        anchor_id = str(anchor["anchor_id"])
        candidate_anchor_ids = selected_anchor_ids | {anchor_id}
        if selectable_count(candidate_anchor_ids) > compaction.max_selectable_anchors:
            continue
        candidate = candidate_packet(candidate_anchor_ids, selected_relation_ids)
        if _request_payload_character_count(candidate, route, task_contract) <= compaction.max_request_payload_characters:
            selected_anchor_ids = candidate_anchor_ids

    compact_packet = candidate_packet(selected_anchor_ids, selected_relation_ids)
    compact_graph = compact_packet["evidence_graph"]
    try:
        validate_evidence_graph(compact_graph)
    except EvidenceGraphError as error:  # pragma: no cover - checked above, defensive at boundary
        raise CertifiedCanonicalError(f"compacted packet graph is invalid: {error}") from error
    if source_graph.get("relations") and not selected_relation_ids:
        raise CertifiedCanonicalError("request compaction removed every evidence relation")
    if any(bool(anchor.get("selectable")) for anchor in anchors.values()) and not selectable_count(selected_anchor_ids):
        raise CertifiedCanonicalError("request compaction removed every selectable evidence anchor")
    request_payload_characters = _request_payload_character_count(compact_packet, route, task_contract)
    if request_payload_characters > compaction.max_request_payload_characters:
        raise CertifiedCanonicalError("request compaction exceeded its declared payload budget")
    return compact_packet, {
        "status": "compacted_finite_subgraph",
        "source_payload_characters": source_payload_characters,
        "request_payload_characters": request_payload_characters,
        "source_anchor_count": len(anchors),
        "selected_anchor_count": len(selected_anchor_ids),
        "source_relation_count": len(relations),
        "selected_relation_count": len(selected_relation_ids),
    }


def _request_payload(
    packet: Mapping[str, Any], route: Mapping[str, Any], task_contract: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    task_contract = dict(task_contract or _default_task_contract())
    packet_sha = _sha_json(packet)
    request_core = {
        "protocol": BAKEOFF_PROTOCOL,
        "internal_table_uid": packet["internal_table_uid"],
        "packet_sha256": packet_sha,
        "route_id": route["route_id"],
        "model_id": route["model_id"],
        "model_revision": route["revision"],
        "parameter_count_billions": route["parameter_count_billions"],
        "modality": route["modality"],
        "purpose": route["purpose"],
        "max_input_tokens": ((packet.get("packet_compaction") or {}).get("policy") or {}).get(
            "max_model_input_tokens"
        ),
        "task": {
            "name": task_contract["name"],
            "fields": task_contract["fields"],
            "instructions": task_contract["instructions"],
            "field_value_constraints": task_contract["field_value_constraints"],
            "field_relation_constraints": task_contract["field_relation_constraints"],
        },
        "packet": {
            key: packet.get(key)
            for key in (
                "benchmark_packet_id",
                "internal_table_uid",
                "candidate_bucket",
                "source_identity",
                "outside_table_context",
                "canonical_columns",
                "row_preview",
                "row_preview_cell_provenance",
                "evidence_graph",
                "packet_compaction",
            )
        },
        "training_eligible": False,
    }
    return {"request_id": _sha_json(request_core), **request_core}


def build_bakeoff_job(config_path: Path, output_dir: Path) -> BakeoffResult:
    """Produce immutable LLM request packets, never an LLM completion."""
    config, paths = _read_inputs(config_path)
    routes = _validate_routes(config)
    task_contract = _validate_task_contract(config)
    request_compaction = _validate_request_compaction(config)
    ccl_manifest = _validate_ccl_lineage(paths)
    _require_new_output(output_dir, inputs=list(paths.values()))
    before_hashes = {name: sha256_file(path) for name, path in paths.items()}
    packets = list(_json_lines(paths["benchmark_packets"]))
    if not packets:
        raise CertifiedCanonicalError("CCL benchmark packet input is empty")
    packet_ids: set[str] = set()
    for packet in packets:
        packet_id = str(packet.get("benchmark_packet_id") or "")
        if not packet_id or packet_id in packet_ids or packet.get("training_eligible") is not False:
            raise CertifiedCanonicalError("bake-off packets must be unique and non-promotable")
        try:
            validate_evidence_graph(packet.get("evidence_graph") or {})
        except EvidenceGraphError as error:
            raise CertifiedCanonicalError(f"bake-off packet lacks a valid finite evidence graph: {error}") from error
        packet_ids.add(packet_id)

    requests_path = output_dir / "llm_bakeoff_requests_v1.jsonl"
    route_counts: dict[str, int] = {}
    compaction_stats: dict[str, list[dict[str, Any]]] = {}
    with requests_path.open("x", encoding="utf-8") as file:
        for route in routes:
            if not route["enabled"]:
                route_counts[route["route_id"]] = 0
                continue
            if route["packet_ids"] is None:
                selected = [
                    packet for packet in packets if str(packet.get("candidate_bucket")) in route["buckets"]
                ][: route["max_packets"]]
            else:
                selected = [
                    packet for packet in packets if str(packet.get("benchmark_packet_id")) in route["packet_ids"]
                ]
                selected_ids = {str(packet.get("benchmark_packet_id")) for packet in selected}
                missing = sorted(set(route["packet_ids"]) - selected_ids)
                if missing:
                    raise CertifiedCanonicalError(
                        f"route {route['route_id']} packet_ids are absent from the immutable packet input: {', '.join(missing[:3])}"
                    )
            for packet in selected:
                request_packet, compacted = _compact_packet_for_route(
                    packet, route, request_compaction, task_contract
                )
                _write_json_line(file, _request_payload(request_packet, route, task_contract))
                compaction_stats.setdefault(route["route_id"], []).append(compacted)
            route_counts[route["route_id"]] = len(selected)
    if not any(route_counts.values()):
        raise CertifiedCanonicalError("enabled bake-off routes selected no packets")

    schema_path = output_dir / "llm_proposal_schema_v1.json"
    schema_path.write_text(
        json.dumps(_proposal_schema(task_contract), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "run_status": "prepared_phase_3_inference_not_executed",
        "input_packet_count": len(packets),
        "request_count": sum(route_counts.values()),
        "route_request_counts": route_counts,
        "route_status": {
            route["route_id"]: "prepared" if route["enabled"] else "disabled"
            for route in routes
        },
        "disabled_route_reasons": {
            route["route_id"]: route["disabled_reason"]
            for route in routes
            if not route["enabled"]
        },
        "source_manifest_sha256": sha256_file(paths["ccl_release_manifest"]),
        "benchmark_packets_sha256": sha256_file(paths["benchmark_packets"]),
        "request_compaction": request_compaction.as_dict() if request_compaction else None,
        "task_contract": task_contract,
        "request_compaction_summary": {
            route_id: {
                "status_counts": {
                    status: sum(1 for item in values if item["status"] == status)
                    for status in sorted({str(item["status"]) for item in values})
                },
                "max_source_payload_characters": max(
                    int(item["source_payload_characters"]) for item in values
                ),
                "max_request_payload_characters": max(
                    int(item["request_payload_characters"]) for item in values
                ),
                "min_request_payload_characters": min(
                    int(item["request_payload_characters"]) for item in values
                ),
            }
            for route_id, values in sorted(compaction_stats.items())
            if values
        },
        "training_eligible_output_count": 0,
        "next_gate": "run_models_then_validate_finite_evidence_selections_and_abstentions",
    }
    report_path = output_dir / "bakeoff_plan_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    after_hashes = {name: sha256_file(path) for name, path in paths.items()}
    if before_hashes != after_hashes:
        raise CertifiedCanonicalError("bake-off job construction changed an immutable input")
    manifest = {
        "schema_version": BAKEOFF_SCHEMA_VERSION,
        "protocol": BAKEOFF_PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {
            name: {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for name, path in paths.items()
        },
        "ccl_release_manifest_sha256": sha256_file(paths["ccl_release_manifest"]),
        "ccl_table_count": ccl_manifest.get("table_count"),
        "task_contract": task_contract,
        "routes": routes,
        "outputs": {
            name: {
                "path": str(output_dir / name),
                "sha256": sha256_file(output_dir / name),
                "bytes": (output_dir / name).stat().st_size,
            }
            for name in _OUTPUT_NAMES
        },
        "training_eligible": False,
        "model_execution_recorded": False,
    }
    manifest_path = output_dir / "bakeoff_job_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return BakeoffResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        request_count=sum(route_counts.values()),
        route_counts=route_counts,
    )
