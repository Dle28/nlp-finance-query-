"""Exact-cell and unit binding candidates for frozen period-column packets."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import yaml


EXACT_CELL_BINDING_PROTOCOL = "exact_cell_unit_binding_candidates_v1"
_VI_GROUPED = re.compile(r"^-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?$")
_DASH = {"", "-", "–", "—", "n/a", "na"}
_UNIT_MULTIPLIERS = {"vnd": Decimal("1"), "nghìn đồng": Decimal("1000"), "triệu đồng": Decimal("1000000"), "tỷ đồng": Decimal("1000000000")}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _contract() -> dict[str, bool]:
    return {"candidate_only": True, "evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False, "may_select_final_candidate": False, "may_select_value": False, "may_execute_formula": False}


def _require_hash(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def parse_vietnamese_numeric_candidate(raw: object) -> tuple[str, str | None, str | None]:
    """Return a Decimal string under one explicit VN formatting policy, never float."""
    text = str(raw or "").strip().replace("\u00a0", " ")
    if text.casefold() in _DASH:
        return "numeric_parse_failure", None, "NULL_OR_DASH_SOURCE_CELL"
    negative = text.startswith("(") and text.endswith(")")
    body = text[1:-1].strip() if negative else text
    if not _VI_GROUPED.fullmatch(body):
        return "numeric_parse_failure", None, "UNSUPPORTED_NUMERIC_FORMAT"
    normalized = body.replace(".", "").replace(",", ".")
    if negative:
        normalized = "-" + normalized
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return "numeric_parse_failure", None, "INVALID_DECIMAL"
    return "parsed_decimal_candidate", format(value, "f"), "VI_GROUPED_DECIMAL_POLICY_V1"


def _registry_output_units(path: Path) -> dict[str, str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Metric registry must be a mapping")
    values: dict[str, str] = {}
    for metric in payload.get("metrics") or []:
        if not isinstance(metric, Mapping) or not metric.get("metric_id"):
            raise ValueError("Invalid metric registry entry")
        values[str(metric["metric_id"])] = str(metric.get("output_unit") or "source_unit")
    return values


def _index(rows: Sequence[Mapping[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(field) or "")
        if not value or value in index:
            raise ValueError(f"{label} duplicate or missing {field}")
        index[value] = dict(row)
    return index


def _candidate_source_cell(
    *,
    candidate: Mapping[str, Any],
    rows: Sequence[Sequence[Any]],
    provenance: Sequence[Sequence[Any]],
    row_index: int,
    column_index: int,
) -> tuple[str, dict[str, Any]]:
    """Resolve a candidate-only coordinate against the immutable V2 table.

    Research materializers intentionally omit raw values from their packets.
    The exact binding stage may rehydrate the cell only when the packet
    explicitly carries the candidate-only contract. Legacy packets that
    already carry raw fields continue to require an exact byte-for-byte match.
    """
    if not (
        0 <= row_index < len(rows)
        and 0 <= column_index < len(rows[row_index])
        and 0 <= row_index < len(provenance)
        and 0 <= column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    ):
        raise ValueError("Period candidate source coordinates out of bounds")
    raw_source_cell = str(rows[row_index][column_index])
    cell_provenance = dict(provenance[row_index][column_index])
    raw_present = "raw_source_cell" in candidate or "cell_provenance" in candidate
    if raw_present:
        if str(candidate.get("raw_source_cell") or "") != raw_source_cell or candidate.get("cell_provenance") != cell_provenance:
            raise ValueError("Period candidate V2 cell provenance mismatch")
        return raw_source_cell, cell_provenance
    contract = candidate.get("source_contract")
    if not isinstance(contract, Mapping) or contract.get("candidate_only") is not True or contract.get("may_select_value") is not False:
        raise ValueError("Value-free period candidate lacks an explicit candidate-only contract")
    return raw_source_cell, cell_provenance


def build_exact_cell_unit_bindings(
    *, period_packets_path: Path, period_manifest_path: Path, metric_registry_path: Path,
    structured_tables_path: Path, evidence_context_path: Path, output_dir: Path,
    approved_repairs_path: Path | None = None, repairs_manifest_path: Path | None = None,
) -> dict[str, Any]:
    manifest = _read_json(period_manifest_path)
    outputs = manifest.get("outputs") or {}
    inputs = manifest.get("inputs") or {}
    _require_hash(period_packets_path, (outputs.get("period_packets") or {}).get("sha256"), "period packets")
    _require_hash(structured_tables_path, (inputs.get("structured_tables_v2") or {}).get("sha256"), "V2 tables")
    _require_hash(evidence_context_path, (inputs.get("evidence_context_v3") or {}).get("sha256"), "V3 contexts")
    if (approved_repairs_path is None) != (repairs_manifest_path is None):
        raise ValueError("Approved repairs and repairs manifest must be supplied together")
    repair_inputs: dict[str, dict[str, str]] = {}
    if approved_repairs_path and repairs_manifest_path:
        repair_manifest = _read_json(repairs_manifest_path)
        _require_hash(approved_repairs_path, ((repair_manifest.get("outputs") or {}).get("approved") or {}).get("sha256"), "approved repairs")
        repair_inputs = {"approved_repairs": {"path": str(approved_repairs_path), "sha256": sha256_file(approved_repairs_path)}, "repairs_manifest": {"path": str(repairs_manifest_path), "sha256": sha256_file(repairs_manifest_path)}}
    packets = _read_jsonl(period_packets_path)
    if len(packets) != 1012 or {int(row.get("question_id") or 0) for row in packets} != set(range(1, 1013)):
        raise ValueError("Period packet question coverage mismatch")
    v2 = _index(_read_jsonl(structured_tables_path), "internal_table_uid", "V2")
    v3 = _index(_read_jsonl(evidence_context_path), "internal_table_uid", "V3")
    if set(v2) != set(v3):
        raise ValueError("V2/V3 table UID coverage mismatch")
    output_units = _registry_output_units(metric_registry_path)
    bindings: list[dict[str, Any]] = []
    packet_rows: list[dict[str, Any]] = []
    for packet in sorted(packets, key=lambda row: int(row["question_id"])):
        ready = packet.get("packet_status") == "unique_period_column_candidate"
        stages: list[dict[str, Any]] = []
        for stage in packet.get("stages") or []:
            operands: list[dict[str, Any]] = []
            requested_unit = output_units.get(str(stage.get("metric_id") or ""), "source_unit")
            for operand in stage.get("required_operands") or []:
                candidates = list(operand.get("period_column_candidates") or []) if ready else []
                if not ready or len(candidates) != 1:
                    operands.append({"role": operand.get("role"), "concept_id": operand.get("concept_id"), "binding_status": "binding_blocked", "binding_candidates": [], "reason_codes": ["PERIOD_PACKET_NOT_UNIQUE"], "requested_output_unit": requested_unit})
                    continue
                candidate = candidates[0]
                uid, row_index, column_index = str(candidate["internal_table_uid"]), int(candidate["row_index"]), int(candidate["column_index"])
                rows = v2[uid].get("rows") or []
                provenance = v2[uid].get("cell_provenance") or []
                profiles = v3[uid].get("row_profiles") or []
                raw_source_cell, cell_provenance = _candidate_source_cell(
                    candidate=candidate,
                    rows=rows,
                    provenance=provenance,
                    row_index=row_index,
                    column_index=column_index,
                )
                profile = next((row for row in profiles if int(row.get("row_index") or -1) == row_index), None)
                if not profile or column_index in set(profile.get("unreliable_numeric_columns") or []):
                    status, parsed, reason = "source_cell_unreliable", None, "V3_UNRELIABLE_NUMERIC_VETO"
                else:
                    parse_status, parsed, reason = parse_vietnamese_numeric_candidate(raw_source_cell)
                    units = list(dict.fromkeys(str(value) for value in candidate.get("unit_labels") or [] if str(value)))
                    if parse_status != "parsed_decimal_candidate":
                        status = "numeric_parse_failure"
                    elif not units:
                        status = "unit_missing"
                    elif len(units) != 1 or units[0].casefold() not in _UNIT_MULTIPLIERS:
                        status = "ambiguous_unit"
                    else:
                        status = "binding_candidate_ready"
                units = list(dict.fromkeys(str(value) for value in candidate.get("unit_labels") or [] if str(value)))
                scale_candidates = [{"unit_label": unit, "multiplier_candidate": format(_UNIT_MULTIPLIERS[unit.casefold()], "f")} for unit in units if unit.casefold() in _UNIT_MULTIPLIERS]
                binding = {"question_id": packet["question_id"], "stage_id": stage.get("stage_id"), "role": operand.get("role"), "concept_id": operand.get("concept_id"), "period_type": operand.get("period_type"), "binding_status": status, "document_id": v2[uid].get("document_id"), "internal_table_uid": uid, "row_index": row_index, "column_index": column_index, "raw_source_row": list(rows[row_index]), "raw_source_cell": raw_source_cell, "cell_provenance": cell_provenance, "header_source_cells": list(candidate.get("header_source_cells") or []), "period_labels": list(candidate.get("period_labels") or []), "unit_labels": units, "table_unit_anchors": list(candidate.get("header_source_cells") or []), "document_unit_anchors": [], "requested_output_unit": requested_unit, "numeric_parse_candidate": parsed, "numeric_parse_policy": reason, "scale_candidates": scale_candidates, "reason_codes": [] if status == "binding_candidate_ready" else [str(reason or status).upper()], "source_contract": _contract()}
                bindings.append(binding)
                operands.append({"role": operand.get("role"), "concept_id": operand.get("concept_id"), "period_type": operand.get("period_type"), "binding_status": status, "binding_candidates": [binding], "reason_codes": binding["reason_codes"], "requested_output_unit": requested_unit})
            stages.append({"stage_id": stage.get("stage_id"), "metric_id": stage.get("metric_id"), "required_operands": operands})
        statuses = [op["binding_status"] for stage in stages for op in stage["required_operands"]]
        packet_rows.append({"schema_version": 1, "protocol": EXACT_CELL_BINDING_PROTOCOL, "question_id": packet["question_id"], "input_packet_status": packet.get("packet_status"), "binding_packet_status": "binding_candidate_ready" if statuses and all(status == "binding_candidate_ready" for status in statuses) else ("binding_blocked" if not ready else "binding_conflict"), "question_context": packet.get("question_context") or {}, "stages": stages, "source_contract": _contract()})
    bindings.sort(key=lambda row: (int(row["question_id"]), str(row["stage_id"]), str(row["role"])))
    packet_rows.sort(key=lambda row: int(row["question_id"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    binding_path, examples_path, manifest_path = output_dir / "exact_cell_unit_binding_candidates_v1.jsonl", output_dir / "exact_cell_unit_bindings_v1.examples.jsonl", output_dir / "exact_cell_unit_bindings_v1.manifest.json"
    _write_jsonl(binding_path, packet_rows)
    _write_jsonl(examples_path, [row for row in packet_rows if row["binding_packet_status"] != "binding_blocked"][:20])
    result = {"schema_version": 1, "protocol": EXACT_CELL_BINDING_PROTOCOL, "inputs": {"period_packets": {"path": str(period_packets_path), "sha256": sha256_file(period_packets_path)}, "period_manifest": {"path": str(period_manifest_path), "sha256": sha256_file(period_manifest_path)}, "metric_registry": {"path": str(metric_registry_path), "sha256": sha256_file(metric_registry_path)}, "structured_tables_v2": {"path": str(structured_tables_path), "sha256": sha256_file(structured_tables_path)}, "evidence_context_v3": {"path": str(evidence_context_path), "sha256": sha256_file(evidence_context_path)}, **repair_inputs}, "outputs": {"bindings": {"path": str(binding_path), "sha256": sha256_file(binding_path)}, "examples": {"path": str(examples_path), "sha256": sha256_file(examples_path)}}, "counts": {"question_count": len(packet_rows), "unique_period_input_packets": sum(row.get("packet_status") == "unique_period_column_candidate" for row in packets), "binding_candidate_count": len(bindings), "binding_status_counts": dict(sorted(Counter(row["binding_status"] for row in bindings).items())), "binding_packet_status_counts": dict(sorted(Counter(row["binding_packet_status"] for row in packet_rows).items()))}, "source_contract": _contract()}
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
