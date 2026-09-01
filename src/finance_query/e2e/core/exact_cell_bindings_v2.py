"""V2 exact bindings with source-unit provenance and output conversion."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from .exact_cell_bindings import parse_vietnamese_numeric_candidate
from .financial_taxonomy import normalize_label


PROTOCOL = "exact_cell_unit_binding_candidates_v2"
UNIT: dict[str, tuple[str, Decimal]] = {
    "nghin ty dong": ("nghin_ty_dong", Decimal("1000000000000")),
    "nghin ty vnd": ("nghin_ty_dong", Decimal("1000000000000")),
    "tram ty dong": ("tram_ty_dong", Decimal("100000000000")),
    "tram ty vnd": ("tram_ty_dong", Decimal("100000000000")),
    "ty dong": ("ty_dong", Decimal("1000000000")),
    "ty vnd": ("ty_dong", Decimal("1000000000")),
    "trieu dong": ("trieu_dong", Decimal("1000000")),
    "trieu vnd": ("trieu_dong", Decimal("1000000")),
    "nghin dong": ("nghin_dong", Decimal("1000")),
    "nghin vnd": ("nghin_dong", Decimal("1000")),
    "vnd": ("vnd", Decimal("1")),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def sha_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _require(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def resolve_source_unit(
    anchors: list[dict[str, Any]],
) -> tuple[str | None, str | None, Decimal | None]:
    """Resolve one unique unit from provenance-backed source-cell anchors."""

    found: list[tuple[str, Decimal]] = []
    for anchor in anchors:
        text = re.sub(
            r"(?<=\d)(?=[a-z])",
            " ",
            normalize_label(anchor.get("raw_source_cell") or ""),
        )
        for phrase, (unit, multiplier) in sorted(
            UNIT.items(), key=lambda item: -len(item[0])
        ):
            if phrase in text:
                found.append((unit, multiplier))
                break

    unique = list(dict.fromkeys(found))
    if len(unique) != 1:
        return None, None, None
    unit, multiplier = unique[0]
    # The second tuple item is retained for compatibility with the existing
    # V2 caller contract; the multiplier remains the third item.
    return unit, unit, multiplier


def table_header_unit_anchors(
    *,
    table: Mapping[str, Any],
    selected_anchors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add provenance-backed header cells when the selected header has no unit.

    The fallback may recover a table-wide printed unit (for example a merged
    OCR header rendered in the comparative column). It never borrows a unit
    from a data row, table title, or another table, and conflicts remain
    blocked by :func:`resolve_source_unit`.
    """

    if resolve_source_unit(selected_anchors)[2] is not None:
        return selected_anchors

    table_rows = table.get("rows") or []
    provenance = table.get("cell_provenance") or []
    selected_coordinates = {
        (int(anchor["row_index"]), int(anchor["column_index"]))
        for anchor in selected_anchors
    }
    output = list(selected_anchors)

    for row_index in table.get("header_row_indices") or []:
        if (
            not isinstance(row_index, int)
            or row_index < 0
            or row_index >= len(table_rows)
            or row_index >= len(provenance)
        ):
            continue
        for column_index, raw in enumerate(table_rows[row_index]):
            if (
                (row_index, column_index) in selected_coordinates
                or column_index >= len(provenance[row_index])
            ):
                continue
            anchor = {
                "row_index": row_index,
                "column_index": column_index,
                "raw_source_cell": str(raw),
                "cell_provenance": provenance[row_index][column_index],
            }
            if resolve_source_unit([anchor])[2] is not None:
                output.append(anchor)
    return output


def resolve_source_title_unit_recheck(
    *,
    candidate: Mapping[str, Any],
    table: Mapping[str, Any],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[str | None, Decimal | None, dict[str, Any] | None, str | None]:
    """Accept a V3 source-title unit only through a hash-bound marker.

    Period headers without a printed unit are common in OCR. This narrow
    fallback is disabled unless a period candidate carries an immutable marker
    produced by the V3 recheck pipeline. It never reads a unit from another
    table or from free-form route metadata.
    """

    marker = candidate.get("unit_source_title_recheck")
    if not isinstance(marker, Mapping):
        return None, None, None, None

    uid = str(candidate.get("internal_table_uid") or "")
    context = contexts.get(uid)
    provenance = table.get("source_provenance") or {}
    context_provenance = (context or {}).get("source_provenance") or {}

    context_matches = context and all(
        (
            str(marker.get("document_id") or "")
            == str(table.get("document_id") or ""),
            str(marker.get("internal_table_uid") or "") == uid,
            str(marker.get("source_sha256") or "")
            == str(provenance.get("source_sha256") or ""),
            str(marker.get("table_sha256") or "")
            == str(provenance.get("table_sha256") or ""),
            str(context.get("document_id") or "")
            == str(table.get("document_id") or ""),
            str(context_provenance.get("source_sha256") or "")
            == str(provenance.get("source_sha256") or ""),
            str(context_provenance.get("table_sha256") or "")
            == str(provenance.get("table_sha256") or ""),
            str(marker.get("evidence_context_row_sha256") or "")
            == sha_json(context),
        )
    )
    if not context_matches:
        return None, None, None, "SOURCE_UNIT_TITLE_RECHECK_CONTEXT_MISMATCH"

    title = str(((context.get("context_trace") or {}).get("source_title")) or "")
    title_sha256 = hashlib.sha256(title.encode("utf-8")).hexdigest()
    if not title or str(marker.get("source_title_sha256") or "") != title_sha256:
        return None, None, None, "SOURCE_UNIT_TITLE_RECHECK_TITLE_MISMATCH"

    unit, _, multiplier = resolve_source_unit([{"raw_source_cell": title}])
    if unit is None or multiplier is None:
        return None, None, None, "SOURCE_UNIT_TITLE_RECHECK_UNIT_UNRESOLVED"
    if (
        unit != str(marker.get("source_unit") or "")
        or format(multiplier, "f")
        != str(marker.get("source_to_vnd_multiplier") or "")
    ):
        return None, None, None, "SOURCE_UNIT_TITLE_RECHECK_UNIT_MISMATCH"

    anchor = {
        "anchor_kind": "evidence_context_source_title",
        "document_id": str(table.get("document_id") or ""),
        "internal_table_uid": uid,
        "source_sha256": str(provenance.get("source_sha256") or ""),
        "table_sha256": str(provenance.get("table_sha256") or ""),
        "source_title_sha256": str(marker.get("source_title_sha256") or ""),
        "evidence_context_row_sha256": sha_json(context),
        "raw_unit_label": str(marker.get("raw_unit_label") or ""),
    }
    return unit, multiplier, anchor, None


def build(
    *,
    period_packets: Path,
    period_manifest: Path,
    route_overlay: Path,
    route_overlay_manifest: Path,
    structured_tables: Path,
    metric_registry: Path,
    output: Path,
    approved_repairs: Path | None = None,
    repairs_manifest: Path | None = None,
) -> dict[str, Any]:
    """Build exact-cell candidates from the locked period and route packets."""

    period_manifest_payload = json.loads(
        period_manifest.read_text(encoding="utf-8")
    )
    _require(
        period_packets,
        period_manifest_payload["outputs"]["period_packets"]["sha256"],
        "period packets",
    )

    route_manifest_payload = json.loads(
        route_overlay_manifest.read_text(encoding="utf-8")
    )
    _require(
        route_overlay,
        route_manifest_payload["outputs"]["overlay"]["sha256"],
        "route overlay",
    )
    _require(
        structured_tables,
        period_manifest_payload["inputs"]["structured_tables_v2"]["sha256"],
        "V2 tables",
    )

    if bool(approved_repairs) != bool(repairs_manifest):
        raise ValueError("approved repairs/manifest must be paired")
    if approved_repairs and approved_repairs.read_text(encoding="utf-8").strip():
        raise ValueError("NON_EMPTY_REPAIR_OVERLAY_UNSUPPORTED")

    period_rows = rows(period_packets)
    route_rows = rows(route_overlay)
    table_rows = rows(structured_tables)
    packets = {row["question_id"]: row for row in period_rows}
    routes = {row["question_id"]: row for row in route_rows}
    tables = {row["internal_table_uid"]: row for row in table_rows}

    context_descriptor = (
        period_manifest_payload.get("inputs", {}).get("evidence_context_v3") or {}
    )
    contexts: dict[str, dict[str, Any]] = {}
    if context_descriptor:
        context_path = Path(str(context_descriptor.get("path") or ""))
        expected_context_sha = context_descriptor.get("sha256")
        if (
            not context_path.is_file()
            or not isinstance(expected_context_sha, str)
            or sha(context_path) != expected_context_sha
        ):
            raise ValueError("SHA-256 mismatch for V3 evidence context")
        contexts = {
            str(row.get("internal_table_uid") or ""): row
            for row in rows(context_path)
        }

    if set(packets) != set(routes) or set(packets) != set(range(1, 1013)):
        raise ValueError("route/period ID mismatch")

    metric_payload = yaml.safe_load(metric_registry.read_text(encoding="utf-8"))
    registry = {row["metric_id"]: row for row in metric_payload["metrics"]}
    output_rows: list[dict[str, Any]] = []
    candidate_counts: Counter[str] = Counter()

    for question_id in sorted(packets):
        packet = packets[question_id]
        route = routes[question_id]
        stages: list[dict[str, Any]] = []
        question_request = route["requested_output_unit"]
        graph = route.get("controlled_operation_graph") or {}
        selector_mode = (
            isinstance(graph, Mapping)
            and graph.get("composition_mode")
            == "same_entity_multi_period_argmax_v1"
        )
        request = question_request
        if selector_mode:
            if (
                question_request.get("kind") != "period"
                or question_request.get("unit") != "year"
            ):
                raise ValueError("period selector requires a year output contract")
            request = {
                "kind": "currency",
                "unit": "vnd",
                "vnd_to_output_divisor": "1",
                "source": "controlled_period_selector_internal_v1",
            }

        for stage in packet.get("stages") or []:
            operands: list[dict[str, Any]] = []
            metric = registry.get(stage.get("metric_id"))
            output_kind = (
                "currency"
                if metric is None or metric.get("output_unit") == "source_unit"
                else "percent"
                if metric.get("output_unit") == "percent"
                else "times"
            )

            for operand in stage.get("required_operands") or []:
                period_candidates = list(
                    operand.get("period_column_candidates") or []
                )
                status = "binding_blocked"
                record: dict[str, Any] | None = None
                reasons: list[str] = []

                if route["route_status"] != "route_complete":
                    reasons = ["ROUTE_INCOMPLETE"]
                elif packet.get("packet_status") != "unique_period_column_candidate":
                    packet_status = str(packet.get("packet_status") or "")
                    reasons = [
                        {
                            "packet_blocked": "PERIOD_CANDIDATE_BLOCKED",
                            "ambiguous_period_columns": "PERIOD_AMBIGUOUS",
                            "no_period_column": "PERIOD_COLUMN_MISSING",
                            "unreliable_numeric_source": "PERIOD_SOURCE_UNRELIABLE",
                        }.get(packet_status, "PERIOD_NOT_UNIQUE")
                    ]
                elif len(period_candidates) != 1:
                    reasons = ["PERIOD_NOT_UNIQUE"]
                else:
                    candidate = period_candidates[0]
                    uid = str(candidate["internal_table_uid"])
                    row_index = int(candidate["row_index"])
                    column_index = int(candidate["column_index"])
                    table = tables[uid]
                    raw_value = str(table["rows"][row_index][column_index])
                    parse_status, decimal_value, parse_policy = (
                        parse_vietnamese_numeric_candidate(raw_value)
                    )

                    anchors: list[dict[str, Any]] = []
                    for coordinate in candidate.get("header_source_cells") or []:
                        header_row = int(coordinate["row_index"])
                        header_column = int(coordinate["column_index"])
                        anchors.append(
                            {
                                "row_index": header_row,
                                "column_index": header_column,
                                "raw_source_cell": str(
                                    table["rows"][header_row][header_column]
                                ),
                                "cell_provenance": table["cell_provenance"][
                                    header_row
                                ][header_column],
                            }
                        )
                    anchors = table_header_unit_anchors(
                        table=table,
                        selected_anchors=anchors,
                    )

                    source_unit, _, multiplier = resolve_source_unit(anchors)
                    title_unit_anchor: dict[str, Any] | None = None
                    title_unit_reason: str | None = None
                    if multiplier is None:
                        (
                            source_unit,
                            multiplier,
                            title_unit_anchor,
                            title_unit_reason,
                        ) = resolve_source_title_unit_recheck(
                            candidate=candidate,
                            table=table,
                            contexts=contexts,
                        )

                    if parse_status != "parsed_decimal_candidate":
                        status = "numeric_parse_failure"
                        reasons = [parse_policy or "NUMERIC_PARSE_FAILURE"]
                    elif output_kind == "currency" and multiplier is None:
                        status = "unit_missing"
                        reasons = [
                            title_unit_reason
                            or "SOURCE_UNIT_ANCHOR_MISSING_OR_CONFLICTING"
                        ]
                    elif output_kind == "currency" and request["kind"] != "currency":
                        status = "unit_conflict"
                        reasons = ["REQUESTED_OUTPUT_KIND_CONFLICT"]
                    elif output_kind in {"percent", "times"} and request["kind"] not in {
                        output_kind,
                        "source_unit",
                    }:
                        status = "unit_conflict"
                        reasons = ["REQUESTED_OUTPUT_KIND_CONFLICT"]
                    else:
                        status = "binding_ready"

                    record = {
                        "question_id": question_id,
                        "stage_id": stage.get("stage_id"),
                        "role": operand.get("role"),
                        "concept_id": operand.get("concept_id"),
                        "binding_status": status,
                        "formula_output_kind": output_kind,
                        "requested_output_unit": request,
                        "document_id": table.get("document_id"),
                        "internal_table_uid": uid,
                        "row_index": row_index,
                        "column_index": column_index,
                        "raw_source_row": table["rows"][row_index],
                        "raw_source_cell": raw_value,
                        "cell_provenance": table["cell_provenance"][row_index][
                            column_index
                        ],
                        "header_source_cells": candidate.get("header_source_cells")
                        or [],
                        "period_labels": candidate.get("period_labels") or [],
                        "period_resolution_method": candidate.get(
                            "period_resolution_method"
                        ),
                        "period_source_title_sha256": candidate.get(
                            "period_source_title_sha256"
                        ),
                        "period_source_date": candidate.get("period_source_date"),
                        "period_entity_corroboration": candidate.get(
                            "period_entity_corroboration"
                        ),
                        "source_unit_anchors": anchors,
                        "source_unit": source_unit,
                        "source_to_vnd_multiplier": (
                            None if multiplier is None else format(multiplier, "f")
                        ),
                        "vnd_to_output_divisor": request.get(
                            "vnd_to_output_divisor"
                        ),
                        "raw_decimal_candidate": decimal_value,
                        "numeric_parse_policy": parse_policy,
                        "reason_codes": reasons,
                        "source_contract": contract(),
                    }
                    if candidate.get("period_document_header_marker") is not None:
                        record["period_document_header_marker"] = candidate.get(
                            "period_document_header_marker"
                        )
                    if title_unit_anchor is not None:
                        record.update(
                            {
                                "unit_resolution_method": (
                                    "v3_exact_source_title_unit_v1"
                                ),
                                "source_unit_context_anchors": [title_unit_anchor],
                            }
                        )

                operands.append(
                    record
                    or {
                        "question_id": question_id,
                        "stage_id": stage.get("stage_id"),
                        "role": operand.get("role"),
                        "concept_id": operand.get("concept_id"),
                        "binding_status": status,
                        "binding_candidates": [],
                        "reason_codes": reasons,
                        "source_contract": contract(),
                    }
                )
                candidate_counts[status] += 1

            stages.append(
                {
                    "stage_id": stage.get("stage_id"),
                    "route_kind": stage.get("route_kind"),
                    "metric_id": stage.get("metric_id"),
                    "required_operands": operands,
                }
            )

        statuses = [
            operand["binding_status"]
            for stage in stages
            for operand in stage["required_operands"]
        ]
        all_reasons = [
            reason
            for stage in stages
            for operand in stage["required_operands"]
            for reason in operand.get("reason_codes", [])
        ]
        packet_status = (
            "binding_ready"
            if statuses and all(status == "binding_ready" for status in statuses)
            else "route_incomplete"
            if "ROUTE_INCOMPLETE" in all_reasons
            else "binding_blocked"
        )
        question_record: dict[str, Any] = {
            "schema_version": 2,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "route_status": route["route_status"],
            "binding_packet_status": packet_status,
            "question_context": route["question_context"],
            "requested_output_unit": question_request,
            "stages": stages,
            "source_contract": contract(),
        }
        controlled_graph = route.get("controlled_operation_graph")
        if isinstance(controlled_graph, dict):
            question_record["controlled_operation_graph"] = controlled_graph
        output_rows.append(question_record)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in output_rows
        ),
        encoding="utf-8",
    )

    result = {
        "schema_version": 2,
        "protocol": PROTOCOL,
        "inputs": {
            "period_packets": {
                "path": str(period_packets),
                "sha256": sha(period_packets),
            },
            "route_overlay": {
                "path": str(route_overlay),
                "sha256": sha(route_overlay),
            },
            "structured_tables": {
                "path": str(structured_tables),
                "sha256": sha(structured_tables),
            },
            "metric_registry": {
                "path": str(metric_registry),
                "sha256": sha(metric_registry),
            },
            "approved_repairs": (
                None
                if not approved_repairs
                else {
                    "path": str(approved_repairs),
                    "sha256": sha(approved_repairs),
                }
            ),
        },
        "outputs": {
            "bindings": {
                "path": str(output),
                "sha256": sha(output),
            }
        },
        "counts": {
            "question_count": len(output_rows),
            "binding_packet_status_counts": dict(
                Counter(row["binding_packet_status"] for row in output_rows)
            ),
            "binding_status_counts": dict(candidate_counts),
        },
        "source_contract": contract(),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}
