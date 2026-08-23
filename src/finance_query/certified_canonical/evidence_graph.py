"""Finite, source-anchored evidence graphs for Certified Canonical packets.

The graph is deliberately a *packet graph*, not a semantic truth store.  Its
selectable nodes are immutable raw context/cell anchors and its edges are
deterministic structural relations derived from V2/V3 coordinates.  A model
may select only these IDs; it cannot mint a new cell coordinate, quote, hash,
or relation.  Semantic claims therefore remain proposals until a later
profile-specific verifier succeeds.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


EVIDENCE_GRAPH_PROTOCOL = "vifinqa_packet_evidence_graph_v1"
EVIDENCE_GRAPH_SCHEMA_VERSION = 1

RELATION_TYPES = frozenset(
    {
        "heading_scopes_table",
        "header_applies_to_column",
        "period_applies_to_column",
        "unit_applies_to_column",
        "row_label_defines_value",
    }
)

_FIELD_RELATIONS = {
    "heading_context": frozenset({"heading_scopes_table"}),
    "table_semantics": frozenset({"heading_scopes_table", "row_label_defines_value"}),
    "period_context": frozenset({"period_applies_to_column"}),
    "unit_context": frozenset({"unit_applies_to_column"}),
}


class EvidenceGraphError(ValueError):
    """A packet evidence graph or a model selection violated its contract."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _as_rows(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    return [
        [str(cell or "") for cell in row]
        for row in value
        if isinstance(row, list)
    ]


def _raw_cell_anchor(
    *,
    uid: str,
    row_index: int,
    column_index: int,
    text: object,
    role: str,
) -> dict[str, Any]:
    identity = {
        "kind": "raw_cell",
        "internal_table_uid": uid,
        "row_index": row_index,
        "column_index": column_index,
        "raw_text_sha256": _text_sha(text),
    }
    return {
        "anchor_id": _sha(identity),
        **identity,
        "role": role,
        "evidence_class": "E0",
        "selectable": True,
        # The value is packet display data only.  The hash/coordinates are
        # the identity used by the validator.
        "quoted_text": str(text or ""),
    }


def _context_anchor(*, uid: str, field: str, text: object) -> dict[str, Any]:
    identity = {
        "kind": "raw_context",
        "internal_table_uid": uid,
        "field": field,
        "raw_text_sha256": _text_sha(text),
    }
    return {
        "anchor_id": _sha(identity),
        **identity,
        "role": field,
        "evidence_class": "E0",
        "selectable": True,
        "quoted_text": str(text or ""),
    }


def _column_slot(*, uid: str, column_index: int, canonical_label: object) -> dict[str, Any]:
    identity = {
        "kind": "canonical_column_slot",
        "internal_table_uid": uid,
        "column_index": column_index,
    }
    return {
        "anchor_id": _sha(identity),
        **identity,
        "canonical_label": str(canonical_label or ""),
        "evidence_class": "E1",
        "selectable": False,
    }


def _table_slot(*, uid: str) -> dict[str, Any]:
    identity = {"kind": "table_slot", "internal_table_uid": uid}
    return {
        "anchor_id": _sha(identity),
        **identity,
        "evidence_class": "E1",
        "selectable": False,
    }


def _edge(
    *,
    uid: str,
    relation_type: str,
    source_anchor_id: str,
    target_anchor_id: str,
    derivation_rule: str,
) -> dict[str, Any]:
    if relation_type not in RELATION_TYPES:
        raise EvidenceGraphError(f"unsupported relation type: {relation_type}")
    identity = {
        "internal_table_uid": uid,
        "relation_type": relation_type,
        "source_anchor_id": source_anchor_id,
        "target_anchor_id": target_anchor_id,
        "derivation_rule": derivation_rule,
        "rule_version": "1.0.0",
    }
    return {
        "relation_id": _sha(identity),
        **identity,
        "evidence_class": "E1",
        "status": "PASS",
    }


def _first_nonempty_cell(row: list[str]) -> int | None:
    for index, value in enumerate(row):
        if value.strip():
            return index
    return None


def _unique(items: Iterable[Mapping[str, Any]], *, key: str) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = str(item.get(key) or "")
        if value and value not in result:
            result[value] = dict(item)
    return [result[value] for value in sorted(result)]


def build_packet_evidence_graph(
    *,
    raw: Mapping[str, Any],
    structured: Mapping[str, Any],
    normalized: Mapping[str, Any],
    preview_row_limit: int = 8,
) -> dict[str, Any]:
    """Build a bounded selectable graph from one hash-bound CCL record.

    Only the rows visible to the Phase-3 packet are included as selectable
    value/row-label anchors.  Header anchors are always included because they
    carry period/unit meaning.  The graph explicitly records this bounded
    universe so a successful proposal means only that it is anchored within
    the packet, never that it is globally unique in the full corpus.
    """
    if preview_row_limit < 1:
        raise EvidenceGraphError("preview_row_limit must be positive")
    uid = str(raw.get("internal_table_uid") or "")
    if not uid or uid != str(structured.get("internal_table_uid") or "") or uid != str(
        normalized.get("internal_table_uid") or ""
    ):
        raise EvidenceGraphError("packet graph requires one matching internal_table_uid")

    source_rows = _as_rows(structured.get("rows"))
    grid = normalized.get("canonical_grid") or {}
    canonical_rows = _as_rows(grid.get("rows"))
    columns = [item for item in grid.get("columns") or [] if isinstance(item, Mapping)]
    table_node = _table_slot(uid=uid)
    anchors: list[dict[str, Any]] = [table_node]
    edges: list[dict[str, Any]] = []
    coordinate_anchors: dict[tuple[int, int], dict[str, Any]] = {}

    def add_raw_cell(row_index: int, column_index: int, *, role: str) -> dict[str, Any] | None:
        if not (0 <= row_index < len(source_rows) and 0 <= column_index < len(source_rows[row_index])):
            return None
        key = (row_index, column_index)
        existing = coordinate_anchors.get(key)
        if existing is not None:
            return existing
        anchor = _raw_cell_anchor(
            uid=uid,
            row_index=row_index,
            column_index=column_index,
            text=source_rows[row_index][column_index],
            role=role,
        )
        coordinate_anchors[key] = anchor
        anchors.append(anchor)
        return anchor

    raw_context = str(raw.get("context_before") or "")
    outside = normalized.get("outside_table_context") or {}
    heading = str(outside.get("source_heading") or outside.get("reader_heading") or "").strip()
    if heading and heading.casefold() in raw_context.casefold():
        heading_anchor = _context_anchor(uid=uid, field="context_before", text=raw_context)
        anchors.append(heading_anchor)
        edges.append(
            _edge(
                uid=uid,
                relation_type="heading_scopes_table",
                source_anchor_id=heading_anchor["anchor_id"],
                target_anchor_id=table_node["anchor_id"],
                derivation_rule="raw_context_heading_occurrence_v1",
            )
        )

    for column in columns:
        try:
            column_index = int(column.get("column_index"))
        except (TypeError, ValueError):
            continue
        slot = _column_slot(uid=uid, column_index=column_index, canonical_label=column.get("canonical_label"))
        anchors.append(slot)
        header_anchors: list[dict[str, Any]] = []
        for coordinate in column.get("header_source_cells") or []:
            if not isinstance(coordinate, Mapping):
                continue
            row_index = coordinate.get("row_index")
            source_column = coordinate.get("column_index")
            if not isinstance(row_index, int) or not isinstance(source_column, int):
                continue
            anchor = add_raw_cell(row_index, source_column, role="header")
            if anchor is not None:
                header_anchors.append(anchor)
                edges.append(
                    _edge(
                        uid=uid,
                        relation_type="header_applies_to_column",
                        source_anchor_id=anchor["anchor_id"],
                        target_anchor_id=slot["anchor_id"],
                        derivation_rule="v3_header_source_cell_replay_v1",
                    )
                )
        for anchor in header_anchors:
            if column.get("period_labels"):
                edges.append(
                    _edge(
                        uid=uid,
                        relation_type="period_applies_to_column",
                        source_anchor_id=anchor["anchor_id"],
                        target_anchor_id=slot["anchor_id"],
                        derivation_rule="v3_period_header_replay_v1",
                    )
                )
            if column.get("unit_labels"):
                edges.append(
                    _edge(
                        uid=uid,
                        relation_type="unit_applies_to_column",
                        source_anchor_id=anchor["anchor_id"],
                        target_anchor_id=slot["anchor_id"],
                        derivation_rule="v3_unit_header_replay_v1",
                    )
                )

    # Row-label/value relations are structural, not a claim that the label is
    # the requested metric.  This distinction lets a later binding verifier
    # reject a plausible but wrong row.
    for row_index, row in enumerate(canonical_rows[:preview_row_limit]):
        label_index = _first_nonempty_cell(row)
        if label_index is None:
            continue
        label_anchor = add_raw_cell(row_index, label_index, role="row_label")
        if label_anchor is None:
            continue
        for column_index, value in enumerate(row):
            if column_index == label_index or not value.strip():
                continue
            value_anchor = add_raw_cell(row_index, column_index, role="visible_cell")
            if value_anchor is None:
                continue
            edges.append(
                _edge(
                    uid=uid,
                    relation_type="row_label_defines_value",
                    source_anchor_id=label_anchor["anchor_id"],
                    target_anchor_id=value_anchor["anchor_id"],
                    derivation_rule="same_source_row_preview_v1",
                )
            )

    temporal_columns = []
    for column in columns:
        try:
            column_index = int(column.get("column_index"))
        except (TypeError, ValueError):
            continue
        temporal_columns.append(
            {
                "column_index": column_index,
                "period_labels": [str(value) for value in column.get("period_labels") or []],
                "unit_labels": [str(value) for value in column.get("unit_labels") or []],
                # A year label alone does not prove flow versus stock, exact
                # as-of date, restatement semantics, or a fiscal calendar.
                "period_grain": "UNRESOLVED",
                "flow_or_stock": "UNRESOLVED",
                "as_of_date": None,
                "comparative_basis": "UNRESOLVED",
                "restatement_status": "UNRESOLVED",
                "status": "PARTIALLY_ANCHORED"
                if column.get("period_labels") or column.get("unit_labels")
                else "UNRESOLVED",
            }
        )

    anchors = _unique(anchors, key="anchor_id")
    edges = _unique(edges, key="relation_id")
    payload = {
        "schema_version": EVIDENCE_GRAPH_SCHEMA_VERSION,
        "protocol": EVIDENCE_GRAPH_PROTOCOL,
        "internal_table_uid": uid,
        "graph_scope": "phase_3_packet_visible_anchors",
        "candidate_universe": {
            "preview_row_limit": preview_row_limit,
            "raw_context_included": bool(raw_context),
            "full_corpus_uniqueness_proven": False,
        },
        "anchors": anchors,
        "relations": edges,
        "temporal_semantics": {
            "columns": temporal_columns,
            "scope": str(raw.get("scope") or "") or None,
            "scope_status": "SOURCE_METADATA_ONLY" if raw.get("scope") else "UNRESOLVED",
        },
        "training_eligible": False,
        "promotion_allowed": False,
    }
    return {"evidence_graph_id": _sha(payload), **payload}


def validate_evidence_graph(graph: Mapping[str, Any]) -> None:
    """Validate graph identity, finite IDs and source-selection boundary."""
    if (
        graph.get("protocol") != EVIDENCE_GRAPH_PROTOCOL
        or int(graph.get("schema_version") or 0) != EVIDENCE_GRAPH_SCHEMA_VERSION
        or graph.get("training_eligible") is not False
        or graph.get("promotion_allowed") is not False
    ):
        raise EvidenceGraphError("unsupported or promotable packet evidence graph")
    uid = str(graph.get("internal_table_uid") or "")
    if not uid:
        raise EvidenceGraphError("packet evidence graph has no table UID")
    anchors = graph.get("anchors")
    relations = graph.get("relations")
    if not isinstance(anchors, list) or not isinstance(relations, list):
        raise EvidenceGraphError("packet evidence graph must contain anchors and relations")
    anchor_ids: set[str] = set()
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            raise EvidenceGraphError("packet evidence graph anchor is not an object")
        anchor_id = str(anchor.get("anchor_id") or "")
        if not anchor_id or anchor_id in anchor_ids:
            raise EvidenceGraphError("packet evidence graph has missing or duplicate anchor ID")
        if str(anchor.get("internal_table_uid") or "") != uid:
            raise EvidenceGraphError("packet evidence graph anchor UID mismatch")
        anchor_ids.add(anchor_id)
    relation_ids: set[str] = set()
    for relation in relations:
        if not isinstance(relation, Mapping):
            raise EvidenceGraphError("packet evidence graph relation is not an object")
        relation_id = str(relation.get("relation_id") or "")
        relation_type = str(relation.get("relation_type") or "")
        if not relation_id or relation_id in relation_ids or relation_type not in RELATION_TYPES:
            raise EvidenceGraphError("packet evidence graph has invalid relation ID or type")
        if (
            str(relation.get("internal_table_uid") or "") != uid
            or str(relation.get("source_anchor_id") or "") not in anchor_ids
            or str(relation.get("target_anchor_id") or "") not in anchor_ids
        ):
            raise EvidenceGraphError("packet evidence graph relation endpoint mismatch")
        relation_ids.add(relation_id)
    expected = dict(graph)
    graph_id = str(expected.pop("evidence_graph_id", ""))
    if graph_id != _sha(expected):
        raise EvidenceGraphError("packet evidence graph hash mismatch")


def validate_proposal_selection(
    *,
    field: object,
    evidence_anchor_ids: object,
    evidence_relation_ids: object,
    graph: Mapping[str, Any],
) -> list[str]:
    """Return deterministic errors for one finite-ID model evidence selection."""
    try:
        validate_evidence_graph(graph)
    except EvidenceGraphError as error:
        return [f"invalid_packet_evidence_graph:{error}"]
    if not isinstance(evidence_anchor_ids, list) or not evidence_anchor_ids:
        return ["evidence_anchor_ids_missing"]
    if not isinstance(evidence_relation_ids, list) or not evidence_relation_ids:
        return ["evidence_relation_ids_missing"]
    anchors = {
        str(item["anchor_id"]): item
        for item in graph.get("anchors") or []
        if isinstance(item, Mapping)
    }
    relations = {
        str(item["relation_id"]): item
        for item in graph.get("relations") or []
        if isinstance(item, Mapping)
    }
    errors: list[str] = []
    selected_anchors = [str(value) for value in evidence_anchor_ids]
    selected_relations = [str(value) for value in evidence_relation_ids]
    if len(selected_anchors) != len(set(selected_anchors)):
        errors.append("duplicate_evidence_anchor_id")
    if len(selected_relations) != len(set(selected_relations)):
        errors.append("duplicate_evidence_relation_id")
    if any(value not in anchors for value in selected_anchors):
        errors.append("unknown_evidence_anchor_id")
    if any(
        value not in anchors or not bool(anchors[value].get("selectable"))
        for value in selected_anchors
    ):
        errors.append("nonselectable_evidence_anchor_id")
    if any(value not in relations for value in selected_relations):
        errors.append("unknown_evidence_relation_id")
    selected_anchor_set = set(selected_anchors)
    selected_relation_values = [relations[value] for value in selected_relations if value in relations]
    if any(
        str(relation.get("source_anchor_id")) not in selected_anchor_set
        and str(relation.get("target_anchor_id")) not in selected_anchor_set
        for relation in selected_relation_values
    ):
        errors.append("relation_not_connected_to_selected_anchor")
    required = _FIELD_RELATIONS.get(str(field), frozenset())
    if required and not required.intersection(
        str(relation.get("relation_type")) for relation in selected_relation_values
    ):
        errors.append("field_relation_type_missing")
    return sorted(set(errors))
