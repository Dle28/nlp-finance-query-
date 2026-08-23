"""Promote reviewed cross-entity operands into a deterministic V6 binding graph.

The ChatGPT-facing artifacts contain semantic labels, exact coordinates and
hashes, never financial values.  This module validates that review against the
grounded V5 tables, reopens values only inside the deterministic materializer,
and attaches a controlled multi-stage subtraction graph.  It cannot authorize
release, serving, training, submission, or answer promotion.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .cross_entity_operand_reviews import canonical_sha256, sha256_file
from .exact_cell_bindings import parse_vietnamese_numeric_candidate
from .exact_cell_bindings_v2 import resolve_source_unit


PACKET_PROTOCOL = "vifinqa_cross_entity_binding_promotion_packet_v1"
DECISION_PROTOCOL = "vifinqa_cross_entity_binding_chatgpt_promotion_v1"
MANIFEST_PROTOCOL = "vifinqa_cross_entity_binding_promotion_review_v1"
MATERIALIZATION_PROTOCOL = "exact_cell_unit_binding_candidates_v6_cross_entity_composition"
AUTHORITY_SCOPE = "cross_entity_binding_review_gate_equivalence"

SAFE_SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "release_authorized": False,
    "may_materialize_answer": False,
    "may_select_value": False,
    "numeric_value_exposed_to_reviewer": False,
}


class CrossEntityBindingPromotionError(ValueError):
    """Raised when promotion lineage or authority fails closed."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object, label: str = "value") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CrossEntityBindingPromotionError(f"{label} must be a mapping")
    return value


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise CrossEntityBindingPromotionError(f"{path} must contain an object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise CrossEntityBindingPromotionError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _output_sha(manifest: Mapping[str, Any], output_name: str) -> str:
    return _text(_mapping(_mapping(manifest.get("outputs"), "outputs").get(output_name), output_name).get("sha256"))


def _require_sha(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CrossEntityBindingPromotionError(f"{label} SHA-256 mismatch")


def _verify_record(record: Mapping[str, Any], hash_field: str, label: str) -> None:
    payload = {key: value for key, value in record.items() if key != hash_field}
    if record.get(hash_field) != canonical_sha256(payload):
        raise CrossEntityBindingPromotionError(f"{label} canonical SHA-256 mismatch")


def _verify_operand_record(record: Mapping[str, Any], hash_field: str, label: str) -> None:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"schema_version", "protocol", hash_field}
    }
    if record.get(hash_field) != canonical_sha256(payload):
        raise CrossEntityBindingPromotionError(f"{label} canonical SHA-256 mismatch")


def _index(path: Path, field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _rows(path):
        key = _text(row.get(field))
        if not key or key in result:
            raise CrossEntityBindingPromotionError(f"{label} requires unique {field}")
        result[key] = row
    return result


def _question(rows: Sequence[Mapping[str, Any]], question_id: int) -> Mapping[str, Any]:
    matches = [row for row in rows if int(row.get("question_id") or -1) == question_id]
    if len(matches) != 1:
        raise CrossEntityBindingPromotionError(f"Q{question_id} is missing or duplicated")
    return matches[0]


def _stage_operand(question: Mapping[str, Any], stage_id: str, role: str) -> Mapping[str, Any]:
    stages = [stage for stage in question.get("stages") or [] if _text(stage.get("stage_id")) == stage_id]
    if len(stages) != 1:
        raise CrossEntityBindingPromotionError(f"stage {stage_id} is missing or duplicated")
    operands = [operand for operand in stages[0].get("required_operands") or [] if _text(operand.get("role")) == role]
    if len(operands) != 1:
        raise CrossEntityBindingPromotionError(f"stage {stage_id} role {role} is missing or duplicated")
    return operands[0]


def _table_cell(table: Mapping[str, Any], row_index: int, column_index: int) -> str:
    rows = table.get("rows") or []
    if row_index < 0 or row_index >= len(rows) or not isinstance(rows[row_index], list) or column_index < 0 or column_index >= len(rows[row_index]):
        raise CrossEntityBindingPromotionError("exact source coordinate is invalid")
    return str(rows[row_index][column_index])


def _context_header(context: Mapping[str, Any], column_index: int) -> Mapping[str, Any]:
    matches = [
        value for value in _mapping(context.get("canonical_headers"), "canonical_headers").get("columns") or []
        if isinstance(value, Mapping) and value.get("column_index") == column_index
    ]
    if len(matches) != 1:
        raise CrossEntityBindingPromotionError("period header is not unique")
    return matches[0]


def _validate_provenance(provenance: Mapping[str, Any]) -> None:
    authority = _mapping(provenance.get("authority_grant"), "authority_grant")
    if (
        provenance.get("reviewer_type") != "chatgpt_verified"
        or not _text(provenance.get("reviewer_id"))
        or not _text(provenance.get("model_family"))
        or provenance.get("review_policy") != "fail_closed_exact_cross_entity_binding_v1"
        or authority.get("granted_by") != "campaign_owner"
        or authority.get("grant_scope") != AUTHORITY_SCOPE
        or authority.get("grant_basis") != "explicit_user_instruction"
    ):
        raise CrossEntityBindingPromotionError("cross-entity promotion lacks explicit ChatGPT authority")


def _source_role_line(evidence: Mapping[str, Any], repository_root: Path) -> tuple[str, str]:
    relative = Path(_text(evidence.get("source_path")))
    path = (repository_root / relative).resolve()
    try:
        path.relative_to(repository_root.resolve())
    except ValueError as exc:
        raise CrossEntityBindingPromotionError("entity-role source escapes repository root") from exc
    if not path.is_file() or sha256_file(path) != evidence.get("source_sha256"):
        raise CrossEntityBindingPromotionError("entity-role source document is unavailable or stale")
    line_number = int(evidence.get("line_number") or 0)
    lines = path.read_text(encoding="utf-8").splitlines()
    if line_number < 1 or line_number > len(lines):
        raise CrossEntityBindingPromotionError("entity-role source line is outside the document")
    raw = lines[line_number - 1]
    if raw != evidence.get("line_text") or hashlib.sha256(raw.encode("utf-8")).hexdigest() != evidence.get("line_sha256"):
        raise CrossEntityBindingPromotionError("entity-role source line drift")
    return raw, str(relative)


def _validate_operand_evidence(
    *,
    evidence: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
    repository_root: Path,
) -> None:
    coordinate = _mapping(evidence.get("coordinate"), "coordinate")
    lineage = _mapping(evidence.get("lineage"), "lineage")
    provenance = _mapping(table.get("source_provenance"), "source_provenance")
    if (
        table.get("document_id") != coordinate.get("document_id")
        or table.get("internal_table_uid") != coordinate.get("internal_table_uid")
        or context.get("document_id") != coordinate.get("document_id")
        or context.get("internal_table_uid") != coordinate.get("internal_table_uid")
        or provenance.get("source_sha256") != lineage.get("document_sha256")
        or provenance.get("table_sha256") != lineage.get("table_sha256")
    ):
        raise CrossEntityBindingPromotionError("operand source identity or lineage mismatch")
    row_index = int(coordinate.get("row_index"))
    label_column = int(coordinate.get("row_label_column_index"))
    value_column = int(coordinate.get("value_column_index"))
    header_row = int(coordinate.get("header_row_index"))
    label = _table_cell(table, row_index, label_column)
    header_label = _table_cell(table, header_row, value_column)
    raw_value = _table_cell(table, row_index, value_column)
    if (
        label != _mapping(evidence.get("variable"), "variable").get("source_row_label")
        or header_label != _mapping(evidence.get("period"), "period").get("source_header")
        or hashlib.sha256(label.encode("utf-8")).hexdigest() != lineage.get("row_label_sha256")
        or hashlib.sha256(header_label.encode("utf-8")).hexdigest() != lineage.get("header_label_sha256")
        or hashlib.sha256(raw_value.encode("utf-8")).hexdigest() != lineage.get("value_cell_sha256")
    ):
        raise CrossEntityBindingPromotionError("operand row, header, or value-cell hash drift")
    header = _context_header(context, value_column)
    if header.get("header_source_cells") != [{"row_index": header_row, "column_index": value_column}]:
        raise CrossEntityBindingPromotionError("operand header coordinates drift")
    if _mapping(context.get("table_function"), "table_function").get("kind") != "income_statement":
        raise CrossEntityBindingPromotionError("operand is not from an income statement")
    quality = _mapping(context.get("quality"), "quality")
    grid = _mapping(context.get("grid"), "grid")
    if quality.get("status") != "review_ready" or grid.get("rectangular") is not True or grid.get("provenance_complete") is not True:
        raise CrossEntityBindingPromotionError("operand evidence context is not review-ready")
    role = _mapping(evidence.get("entity_role"), "entity_role")
    if role.get("claim") != "parent" or role.get("status") != "candidate":
        raise CrossEntityBindingPromotionError("operand parent role is not reviewable")
    _source_role_line(_mapping(role.get("evidence"), "entity_role.evidence"), repository_root)


def build_cross_entity_binding_promotion_review(
    *,
    operand_packets: Path,
    operand_packet_manifest: Path,
    operand_decisions: Path,
    operand_decision_manifest: Path,
    base_bindings: Path,
    base_bindings_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    repository_root: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a value-free promotion packet and explicitly scoped decision."""

    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite cross-entity promotion review: {output_dir}")
    packet_meta, decision_meta = _json(operand_packet_manifest), _json(operand_decision_manifest)
    base_meta, context_meta = _json(base_bindings_manifest), _json(evidence_context_manifest)
    _require_sha(operand_packets, _output_sha(packet_meta, "packets"), "operand packets")
    _require_sha(operand_decisions, _output_sha(decision_meta, "decisions"), "operand decisions")
    _require_sha(base_bindings, _output_sha(base_meta, "bindings"), "base bindings")
    if context_meta.get("sidecar_sha256") != sha256_file(evidence_context) or context_meta.get("input_structure_sha256") != sha256_file(structured_tables):
        raise CrossEntityBindingPromotionError("evidence-context lineage mismatch")
    base_inputs = _mapping(base_meta.get("inputs"), "base inputs")
    structured_record = _mapping(base_inputs.get("structured_tables"), "base structured_tables")
    if structured_record.get("sha256") != sha256_file(structured_tables):
        raise CrossEntityBindingPromotionError("base bindings are stale for structured tables")

    config = _json(config_path)
    if config.get("protocol") != "vifinqa_cross_entity_binding_promotion_config_v1":
        raise CrossEntityBindingPromotionError("unexpected cross-entity promotion config")
    provenance = _mapping(config.get("decision_provenance"), "decision_provenance")
    _validate_provenance(provenance)
    reviewed_at = _text(config.get("reviewed_at"))
    if not reviewed_at:
        raise CrossEntityBindingPromotionError("reviewed_at is required")
    source_packets = {int(row["question_id"]): row for row in _rows(operand_packets)}
    source_decisions = {int(row["question_id"]): row for row in _rows(operand_decisions)}
    base_rows = _rows(base_bindings)
    tables = _index(structured_tables, "internal_table_uid", "structured tables")
    contexts = _index(evidence_context, "internal_table_uid", "evidence context")

    packets: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for raw_spec in config.get("promotions") or []:
        spec = _mapping(raw_spec, "promotion spec")
        question_id = int(spec.get("question_id") or -1)
        source_packet = _mapping(source_packets.get(question_id), f"Q{question_id} operand packet")
        source_decision = _mapping(source_decisions.get(question_id), f"Q{question_id} operand decision")
        _verify_operand_record(source_packet, "packet_sha256", f"Q{question_id} operand packet")
        _verify_operand_record(source_decision, "decision_sha256", f"Q{question_id} operand decision")
        if (
            source_packet.get("packet_status") != "reviewable_exact_operand_set"
            or source_decision.get("decision") != "approve_exact_operand_set"
            or source_decision.get("packet_sha256") != source_packet.get("packet_sha256")
            or any(value is not True for value in _mapping(source_decision.get("semantic_checks"), "semantic_checks").values())
        ):
            raise CrossEntityBindingPromotionError(f"Q{question_id} lacks a complete operand review")
        question = _question(base_rows, question_id)
        if question.get("route_status") != "composed_execution_required" or question.get("binding_packet_status") != "route_incomplete":
            raise CrossEntityBindingPromotionError(f"Q{question_id} base question is not the expected composed blocker")
        stage_order = [_text(value) for value in spec.get("stage_order") or []]
        if len(stage_order) != 2 or len(set(stage_order)) != 2 or source_packet.get("operation") != "subtract":
            raise CrossEntityBindingPromotionError(f"Q{question_id} promotion requires ordered binary subtraction")
        evidence_by_stage = {_text(value.get("stage_id")): value for value in source_packet.get("operand_evidence") or []}
        if set(evidence_by_stage) != set(stage_order):
            raise CrossEntityBindingPromotionError(f"Q{question_id} stage order does not match operand evidence")
        reviewed_operands: list[dict[str, Any]] = []
        for stage_id in stage_order:
            evidence = _mapping(evidence_by_stage[stage_id], "operand evidence")
            role = _text(spec.get("stage_roles", {}).get(stage_id))
            operand = _stage_operand(question, stage_id, role)
            if operand.get("binding_status") != "binding_blocked" or operand.get("reason_codes") != ["ROUTE_INCOMPLETE"]:
                raise CrossEntityBindingPromotionError(f"Q{question_id} stage {stage_id} base operand drift")
            uid = _text(_mapping(evidence.get("coordinate"), "coordinate").get("internal_table_uid"))
            table, context = _mapping(tables.get(uid), "table"), _mapping(contexts.get(uid), "context")
            _validate_operand_evidence(evidence=evidence, table=table, context=context, repository_root=repository_root)
            reviewed_operands.append({**dict(evidence), "binding_role": role})
        payload = {
            "schema_version": 1,
            "protocol": PACKET_PROTOCOL,
            "question_id": question_id,
            "question": source_packet.get("question"),
            "source_operand_packet_sha256": source_packet.get("packet_sha256"),
            "source_operand_decision_sha256": source_decision.get("decision_sha256"),
            "operation_graph": {
                "protocol": "vifinqa_controlled_composition_graph_v1",
                "operation_ast": {"op": "subtract", "args": stage_order},
                "stage_order": stage_order,
                "final_node_id": "op:final:subtract",
                "output_unit": source_packet.get("requested_unit"),
            },
            "reviewed_operands": reviewed_operands,
            "source_contract": dict(SAFE_SOURCE_CONTRACT),
        }
        packet = {**payload, "promotion_packet_sha256": canonical_sha256(payload)}
        packets.append(packet)
        decision_payload = {
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "question_id": question_id,
            "promotion_packet_sha256": packet["promotion_packet_sha256"],
            "decision": "approve_cross_entity_binding_promotion",
            "decision_provenance": dict(provenance),
            "reviewed_at": reviewed_at,
            "reviewer_authority": {
                "may_approve_exact_operands": True,
                "may_approve_composition_graph": True,
                "may_select_value": False,
                "may_execute_formula": False,
                "release_authorized": False,
            },
            "deterministic_materialization_required": True,
            "notes": spec.get("notes") or "",
            "source_contract": dict(SAFE_SOURCE_CONTRACT),
        }
        decisions.append({**decision_payload, "promotion_decision_sha256": canonical_sha256(decision_payload)})
    if not packets:
        raise CrossEntityBindingPromotionError("at least one cross-entity promotion is required")
    output_dir.mkdir(parents=True, exist_ok=False)
    packets_path = output_dir / "cross_entity_binding_promotion_packets_v1.jsonl"
    decisions_path = output_dir / "cross_entity_binding_chatgpt_promotions_v1.jsonl"
    _write_jsonl(packets_path, packets)
    _write_jsonl(decisions_path, decisions)
    manifest = {
        "schema_version": 1,
        "protocol": MANIFEST_PROTOCOL,
        "status": "reviewed_for_deterministic_cross_entity_binding",
        "inputs": {
            "operand_packets": {"path": str(operand_packets), "sha256": sha256_file(operand_packets)},
            "operand_packet_manifest": {"path": str(operand_packet_manifest), "sha256": sha256_file(operand_packet_manifest)},
            "operand_decisions": {"path": str(operand_decisions), "sha256": sha256_file(operand_decisions)},
            "operand_decision_manifest": {"path": str(operand_decision_manifest), "sha256": sha256_file(operand_decision_manifest)},
            "base_bindings": {"path": str(base_bindings), "sha256": sha256_file(base_bindings)},
            "base_bindings_manifest": {"path": str(base_bindings_manifest), "sha256": sha256_file(base_bindings_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "evidence_context": {"path": str(evidence_context), "sha256": sha256_file(evidence_context)},
            "evidence_context_manifest": {"path": str(evidence_context_manifest), "sha256": sha256_file(evidence_context_manifest)},
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
        },
        "outputs": {
            "packets": {"path": str(packets_path), "sha256": sha256_file(packets_path)},
            "decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)},
        },
        "counts": {"promotion_count": len(decisions), "operand_count": sum(len(row["reviewed_operands"]) for row in packets), "numeric_value_exposure_count": 0},
        "source_contract": dict(SAFE_SOURCE_CONTRACT),
    }
    manifest_path = output_dir / "cross_entity_binding_promotion_review_v1.manifest.json"
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def load_cross_entity_binding_promotions(
    *,
    packets: Path,
    decisions: Path,
    manifest_path: Path,
    base_bindings: Path,
    structured_tables: Path,
    evidence_context: Path,
    repository_root: Path,
) -> dict[int, dict[str, Any]]:
    """Validate approved promotion records without exposing financial values."""

    manifest = _json(manifest_path)
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise CrossEntityBindingPromotionError("unexpected cross-entity promotion manifest")
    inputs, outputs = _mapping(manifest.get("inputs"), "inputs"), _mapping(manifest.get("outputs"), "outputs")
    for name, path in {"base_bindings": base_bindings, "structured_tables": structured_tables, "evidence_context": evidence_context}.items():
        _require_sha(path, _mapping(inputs.get(name), name).get("sha256"), name)
    _require_sha(packets, _mapping(outputs.get("packets"), "packets").get("sha256"), "promotion packets")
    _require_sha(decisions, _mapping(outputs.get("decisions"), "decisions").get("sha256"), "promotion decisions")
    packet_index: dict[str, Mapping[str, Any]] = {}
    for packet in _rows(packets):
        _verify_record(packet, "promotion_packet_sha256", "cross-entity promotion packet")
        packet_sha = _text(packet.get("promotion_packet_sha256"))
        if packet.get("protocol") != PACKET_PROTOCOL or packet_sha in packet_index:
            raise CrossEntityBindingPromotionError("cross-entity promotion packet identity is invalid")
        packet_index[packet_sha] = packet
    base_rows = _rows(base_bindings)
    tables = _index(structured_tables, "internal_table_uid", "structured tables")
    contexts = _index(evidence_context, "internal_table_uid", "evidence context")
    result: dict[int, dict[str, Any]] = {}
    for decision in _rows(decisions):
        _verify_record(decision, "promotion_decision_sha256", "cross-entity promotion decision")
        packet = packet_index.get(_text(decision.get("promotion_packet_sha256")))
        if packet is None:
            raise CrossEntityBindingPromotionError("promotion decision references a missing packet")
        if decision.get("decision") != "approve_cross_entity_binding_promotion":
            continue
        _validate_provenance(_mapping(decision.get("decision_provenance"), "decision_provenance"))
        authority = _mapping(decision.get("reviewer_authority"), "reviewer_authority")
        if (
            authority.get("may_approve_exact_operands") is not True
            or authority.get("may_approve_composition_graph") is not True
            or authority.get("may_select_value") is not False
            or authority.get("may_execute_formula") is not False
            or authority.get("release_authorized") is not False
            or decision.get("deterministic_materialization_required") is not True
        ):
            raise CrossEntityBindingPromotionError("cross-entity promotion authority boundary is invalid")
        question_id = int(packet.get("question_id") or -1)
        question = _question(base_rows, question_id)
        graph = _mapping(packet.get("operation_graph"), "operation_graph")
        ast = _mapping(graph.get("operation_ast"), "operation_ast")
        stage_order = list(graph.get("stage_order") or [])
        if graph.get("protocol") != "vifinqa_controlled_composition_graph_v1" or ast != {"op": "subtract", "args": stage_order} or len(stage_order) != 2:
            raise CrossEntityBindingPromotionError("controlled composition graph is invalid")
        promotion_operands: list[dict[str, Any]] = []
        for evidence in packet.get("reviewed_operands") or []:
            item = _mapping(evidence, "reviewed operand")
            stage_id, role = _text(item.get("stage_id")), _text(item.get("binding_role"))
            operand = _stage_operand(question, stage_id, role)
            if operand.get("binding_status") != "binding_blocked":
                raise CrossEntityBindingPromotionError(f"Q{question_id} base operand drift")
            uid = _text(_mapping(item.get("coordinate"), "coordinate").get("internal_table_uid"))
            table, context = _mapping(tables.get(uid), "table"), _mapping(contexts.get(uid), "context")
            _validate_operand_evidence(evidence=item, table=table, context=context, repository_root=repository_root)
            promotion_operands.append({"evidence": dict(item), "table": table, "context": context})
        if {value["evidence"]["stage_id"] for value in promotion_operands} != set(stage_order):
            raise CrossEntityBindingPromotionError("promotion operand coverage mismatch")
        if question_id in result:
            raise CrossEntityBindingPromotionError("duplicate cross-entity promotion question")
        result[question_id] = {"packet": dict(packet), "decision": dict(decision), "operands": promotion_operands}
    if len(result) != int(_mapping(manifest.get("counts"), "counts").get("promotion_count") or 0):
        raise CrossEntityBindingPromotionError("cross-entity promotion count drift")
    return result


def merge_cross_entity_binding_promotion_reviews(
    *,
    source_manifests: Sequence[Path],
    base_bindings: Path,
    structured_tables: Path,
    evidence_context: Path,
    repository_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Merge independently reviewed promotions and revalidate a common base."""

    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite merged promotion review: {output_dir}")
    if len(source_manifests) < 2:
        raise CrossEntityBindingPromotionError("promotion merge requires at least two source manifests")
    packets: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    source_inputs: list[dict[str, str]] = []
    question_ids: set[int] = set()
    for manifest_path in source_manifests:
        manifest = _json(manifest_path)
        if manifest.get("protocol") != MANIFEST_PROTOCOL:
            raise CrossEntityBindingPromotionError("promotion merge source protocol is invalid")
        outputs = _mapping(manifest.get("outputs"), "source outputs")
        packet_record = _mapping(outputs.get("packets"), "source packets")
        decision_record = _mapping(outputs.get("decisions"), "source decisions")
        packet_path = Path(_text(packet_record.get("path")))
        decision_path = Path(_text(decision_record.get("path")))
        _require_sha(packet_path, packet_record.get("sha256"), "source promotion packets")
        _require_sha(decision_path, decision_record.get("sha256"), "source promotion decisions")
        source_packets = _rows(packet_path)
        source_decisions = _rows(decision_path)
        if len(source_packets) != len(source_decisions):
            raise CrossEntityBindingPromotionError("source promotion packet/decision count mismatch")
        for packet in source_packets:
            _verify_record(packet, "promotion_packet_sha256", "source promotion packet")
            question_id = int(packet.get("question_id") or -1)
            if question_id in question_ids:
                raise CrossEntityBindingPromotionError("duplicate question across promotion sources")
            question_ids.add(question_id)
        for decision in source_decisions:
            _verify_record(decision, "promotion_decision_sha256", "source promotion decision")
        packets.extend(source_packets)
        decisions.extend(source_decisions)
        source_inputs.append({"path": str(manifest_path), "sha256": sha256_file(manifest_path)})
    packets.sort(key=lambda row: int(row["question_id"]))
    decision_by_packet = {_text(row.get("promotion_packet_sha256")): row for row in decisions}
    decisions = [decision_by_packet[_text(row.get("promotion_packet_sha256"))] for row in packets]
    output_dir.mkdir(parents=True, exist_ok=False)
    packet_path = output_dir / "cross_entity_binding_promotion_packets_v1.jsonl"
    decision_path = output_dir / "cross_entity_binding_chatgpt_promotions_v1.jsonl"
    _write_jsonl(packet_path, packets)
    _write_jsonl(decision_path, decisions)
    manifest = {
        "schema_version": 1,
        "protocol": MANIFEST_PROTOCOL,
        "status": "merged_reviewed_for_deterministic_cross_entity_binding",
        "inputs": {
            "source_manifests": source_inputs,
            "base_bindings": {"path": str(base_bindings), "sha256": sha256_file(base_bindings)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "evidence_context": {"path": str(evidence_context), "sha256": sha256_file(evidence_context)},
        },
        "outputs": {
            "packets": {"path": str(packet_path), "sha256": sha256_file(packet_path)},
            "decisions": {"path": str(decision_path), "sha256": sha256_file(decision_path)},
        },
        "counts": {
            "promotion_count": len(decisions),
            "operand_count": sum(len(row.get("reviewed_operands") or []) for row in packets),
            "numeric_value_exposure_count": 0,
        },
        "source_contract": dict(SAFE_SOURCE_CONTRACT),
    }
    manifest_path = output_dir / "cross_entity_binding_promotion_review_v1.manifest.json"
    _write_json(manifest_path, manifest)
    load_cross_entity_binding_promotions(
        packets=packet_path,
        decisions=decision_path,
        manifest_path=manifest_path,
        base_bindings=base_bindings,
        structured_tables=structured_tables,
        evidence_context=evidence_context,
        repository_root=repository_root,
    )
    return {**manifest, "manifest_path": str(manifest_path)}


_VI_DATE_RE = re.compile(r"(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(20\d{2})", re.IGNORECASE)


def _exact_report_end_date(source_title: str, requested_year: int) -> str:
    dates: list[date] = []
    for day, month, year in _VI_DATE_RE.findall(source_title):
        try:
            value = date(int(year), int(month), int(day))
        except ValueError:
            continue
        if value.year == requested_year and value not in dates:
            dates.append(value)
    if len(dates) != 1:
        raise CrossEntityBindingPromotionError("source title does not prove a unique report end date")
    return dates[0].isoformat()


def apply_cross_entity_binding_promotions(
    *,
    base_bindings: Path,
    base_bindings_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    packets: Path,
    decisions: Path,
    promotion_manifest: Path,
    repository_root: Path,
    output: Path,
) -> dict[str, Any]:
    """Materialize reviewed operands and a controlled composition graph."""

    base_meta = _json(base_bindings_manifest)
    _require_sha(base_bindings, _output_sha(base_meta, "bindings"), "base bindings")
    promotions = load_cross_entity_binding_promotions(
        packets=packets,
        decisions=decisions,
        manifest_path=promotion_manifest,
        base_bindings=base_bindings,
        structured_tables=structured_tables,
        evidence_context=evidence_context,
        repository_root=repository_root,
    )
    rows = _rows(base_bindings)
    applied: list[dict[str, Any]] = []
    for question_id, promotion in promotions.items():
        question = _question(rows, question_id)
        if not isinstance(question, dict):
            raise CrossEntityBindingPromotionError("promotion target is not mutable")
        packet, decision = promotion["packet"], promotion["decision"]
        context_question = _mapping(question.get("question_context"), "question_context")
        requested_years = list(context_question.get("years") or [])
        if len(requested_years) != 1:
            raise CrossEntityBindingPromotionError(f"Q{question_id} requires one exact year")
        requested_year = int(requested_years[0])
        for promoted in promotion["operands"]:
            evidence, table, context = promoted["evidence"], promoted["table"], promoted["context"]
            stage_id, role = _text(evidence.get("stage_id")), _text(evidence.get("binding_role"))
            operand = _stage_operand(question, stage_id, role)
            if not isinstance(operand, dict):
                raise CrossEntityBindingPromotionError("promotion operand is not mutable")
            coordinate = _mapping(evidence.get("coordinate"), "coordinate")
            row_index, column_index = int(coordinate["row_index"]), int(coordinate["value_column_index"])
            raw = _table_cell(table, row_index, column_index)
            parse_status, decimal_literal, parse_policy = parse_vietnamese_numeric_candidate(raw)
            if parse_status != "parsed_decimal_candidate" or decimal_literal is None:
                raise CrossEntityBindingPromotionError(f"Q{question_id} promoted value is not Decimal-parseable")
            header = _context_header(context, column_index)
            header_coordinates = list(header.get("header_source_cells") or [])
            header_anchors = [
                {
                    "row_index": int(value["row_index"]),
                    "column_index": int(value["column_index"]),
                    "raw_source_cell": _table_cell(table, int(value["row_index"]), int(value["column_index"])),
                    "cell_provenance": table["cell_provenance"][int(value["row_index"])][int(value["column_index"])],
                }
                for value in header_coordinates
            ]
            source_unit, _, multiplier = resolve_source_unit(header_anchors)
            source_title = _text(_mapping(context.get("context_trace"), "context_trace").get("source_title"))
            unit_resolution_method = None
            source_unit_context_anchors: list[dict[str, Any]] = []
            if source_unit is None or multiplier is None:
                source_unit, _, multiplier = resolve_source_unit([{"raw_source_cell": source_title}])
                if source_unit is None or multiplier is None:
                    raise CrossEntityBindingPromotionError(f"Q{question_id} promoted source unit is unresolved")
                unit_resolution_method = "v3_exact_source_title_unit_v1"
                provenance = _mapping(table.get("source_provenance"), "source_provenance")
                source_unit_context_anchors = [{
                    "anchor_kind": "evidence_context_source_title",
                    "document_id": table.get("document_id"),
                    "internal_table_uid": table.get("internal_table_uid"),
                    "source_sha256": provenance.get("source_sha256"),
                    "table_sha256": provenance.get("table_sha256"),
                    "source_title_sha256": hashlib.sha256(source_title.encode("utf-8")).hexdigest(),
                    "evidence_context_row_sha256": canonical_sha256(context),
                    "raw_unit_label": source_unit,
                }]
            period_labels = list(header.get("period_labels") or [])
            period_resolution_method = None
            period_source_date = None
            if not any(str(requested_year) in _text(value) for value in period_labels):
                period_labels = [*period_labels, str(requested_year)]
                period_resolution_method = "v2_exact_source_title_current_header_v1"
                period_source_date = _exact_report_end_date(source_title, requested_year)
            role_evidence = _mapping(_mapping(evidence.get("entity_role"), "entity_role").get("evidence"), "entity_role.evidence")
            operand.clear()
            operand.update({
                "question_id": question_id,
                "stage_id": stage_id,
                "role": role,
                "concept_id": evidence.get("variable_id"),
                "binding_status": "binding_ready",
                "formula_output_kind": "currency",
                "requested_output_unit": question.get("requested_output_unit"),
                "document_id": table.get("document_id"),
                "internal_table_uid": table.get("internal_table_uid"),
                "row_index": row_index,
                "column_index": column_index,
                "raw_source_row": table["rows"][row_index],
                "raw_source_cell": raw,
                "cell_provenance": table["cell_provenance"][row_index][column_index],
                "header_source_cells": header_coordinates,
                "period_labels": period_labels,
                "period_resolution_method": period_resolution_method,
                "period_source_title_sha256": hashlib.sha256(source_title.encode("utf-8")).hexdigest(),
                "period_source_date": period_source_date,
                "period_entity_corroboration": {},
                "source_unit_anchors": header_anchors,
                "source_unit": source_unit,
                "source_to_vnd_multiplier": format(multiplier, "f"),
                "unit_resolution_method": unit_resolution_method,
                "source_unit_context_anchors": source_unit_context_anchors,
                "vnd_to_output_divisor": _mapping(question.get("requested_output_unit"), "requested_output_unit").get("vnd_to_output_divisor"),
                "raw_decimal_candidate": decimal_literal,
                "numeric_parse_policy": parse_policy,
                "reason_codes": [],
                "cross_entity_promotion": {
                    "protocol": DECISION_PROTOCOL,
                    "promotion_packet_sha256": packet.get("promotion_packet_sha256"),
                    "promotion_decision_sha256": decision.get("promotion_decision_sha256"),
                    "source_operand_decision_sha256": packet.get("source_operand_decision_sha256"),
                    "entity": evidence.get("entity"),
                    "entity_role_source_line_sha256": role_evidence.get("line_sha256"),
                    "numeric_value_selected_by_reviewer": False,
                    "value_materialization_method": "deterministic_exact_table_reopen_v1",
                },
                "source_contract": {
                    "candidate_only": True,
                    "evidence_eligible": False,
                    "may_execute_formula": False,
                    "may_select_value": False,
                    "promotion_allowed": False,
                    "submission_eligible": False,
                    "training_eligible": False,
                },
            })
        graph = _mapping(packet.get("operation_graph"), "operation_graph")
        stage_order = [_text(value) for value in graph.get("stage_order") or []]
        binding_operand_ids: list[str] = []
        for stage_id in stage_order:
            stage_matches = [stage for stage in question.get("stages") or [] if _text(stage.get("stage_id")) == stage_id]
            if len(stage_matches) != 1 or len(stage_matches[0].get("required_operands") or []) != 1:
                raise CrossEntityBindingPromotionError("controlled graph stage binding is ambiguous")
            role = _text(stage_matches[0]["required_operands"][0].get("role"))
            binding_operand_ids.append(f"q{question_id}:stage:{stage_id}:role:{role}")
        question["controlled_operation_graph"] = {
            **dict(graph),
            "binding_operation_ast": {"op": "subtract", "args": binding_operand_ids},
            "promotion_packet_sha256": packet.get("promotion_packet_sha256"),
            "promotion_decision_sha256": decision.get("promotion_decision_sha256"),
            "numeric_values_selected_by_reviewer": False,
        }
        question["route_status"] = "route_complete"
        question["binding_packet_status"] = "binding_ready"
        applied.append({
            "question_id": question_id,
            "stage_order": graph.get("stage_order"),
            "promotion_decision_sha256": decision.get("promotion_decision_sha256"),
        })
    for question in rows:
        question["schema_version"] = 6
        question["protocol"] = MATERIALIZATION_PROTOCOL
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output, rows)
    status_counts = Counter(
        _text(operand.get("binding_status")) or "unknown"
        for question in rows for stage in question.get("stages") or []
        for operand in stage.get("required_operands") or [] if isinstance(operand, Mapping)
    )
    packet_counts = Counter(_text(row.get("binding_packet_status")) or "unknown" for row in rows)
    manifest = {
        "schema_version": 6,
        "protocol": MATERIALIZATION_PROTOCOL,
        "inputs": {
            "base_bindings": {"path": str(base_bindings), "sha256": sha256_file(base_bindings)},
            "base_bindings_manifest": {"path": str(base_bindings_manifest), "sha256": sha256_file(base_bindings_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "evidence_context": {"path": str(evidence_context), "sha256": sha256_file(evidence_context)},
            "promotion_packets": {"path": str(packets), "sha256": sha256_file(packets)},
            "promotion_decisions": {"path": str(decisions), "sha256": sha256_file(decisions)},
            "promotion_manifest": {"path": str(promotion_manifest), "sha256": sha256_file(promotion_manifest)},
        },
        "outputs": {"bindings": {"path": str(output), "sha256": sha256_file(output)}},
        "counts": {
            "question_count": len(rows),
            "cross_entity_promotion_count": len(applied),
            "binding_status_counts": dict(sorted(status_counts.items())),
            "binding_packet_status_counts": dict(sorted(packet_counts.items())),
        },
        "applied_promotions": applied,
        "source_contract": {**SAFE_SOURCE_CONTRACT, "executor_private_numeric_materialization": True},
    }
    manifest_path = output.with_suffix(".manifest.json")
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def effective_approvals_from_cross_entity_promotion(
    promotion: Mapping[str, Any],
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Return per-operand semantic approvals with distinct ChatGPT provenance."""

    packet = _mapping(promotion.get("packet"), "packet")
    decision = _mapping(promotion.get("decision"), "decision")
    provenance = dict(_mapping(decision.get("decision_provenance"), "decision_provenance"))
    question_id = int(packet.get("question_id") or -1)
    approvals: dict[tuple[int, str, str], dict[str, Any]] = {}
    for raw in packet.get("reviewed_operands") or []:
        evidence = _mapping(raw, "reviewed operand")
        stage_id, role = _text(evidence.get("stage_id")), _text(evidence.get("binding_role"))
        coordinate = _mapping(evidence.get("coordinate"), "coordinate")
        variable = _mapping(evidence.get("variable"), "variable")
        role_source = _mapping(_mapping(evidence.get("entity_role"), "entity_role").get("evidence"), "entity_role.evidence")
        approval_id = canonical_sha256({
            "promotion_decision_sha256": decision.get("promotion_decision_sha256"),
            "question_id": question_id,
            "stage_id": stage_id,
            "role": role,
        })
        key = (question_id, stage_id, role)
        approvals[key] = {
            "approval_id": approval_id,
            "variable_approval_id": approval_id,
            "variable_id": evidence.get("variable_id"),
            "entity": evidence.get("entity"),
            "scope": _mapping(evidence.get("reporting_scope"), "reporting_scope").get("source"),
            "entity_role": "parent",
            "row_label": {
                "document_uid": coordinate.get("document_id"),
                "internal_table_uid": coordinate.get("internal_table_uid"),
                "row_index": coordinate.get("row_index"),
                "column_index": coordinate.get("row_label_column_index"),
                "raw_text": variable.get("source_row_label"),
                "raw_text_sha256": _mapping(evidence.get("lineage"), "lineage").get("row_label_sha256"),
            },
            "decision_provenance": provenance,
            "variable_decision_provenance": provenance,
            "entity_scope_decision_provenance": provenance,
            "entity_role_decision_provenance": provenance,
            "entity_role_evidence": {
                "source_text": role_source.get("line_text"),
                "source_text_sha256": role_source.get("line_sha256"),
                "source_anchors": [{
                    "kind": "document_text_line",
                    "document_uid": coordinate.get("document_id"),
                    "line_number": role_source.get("line_number"),
                    "source_file_sha256": role_source.get("source_sha256"),
                    "raw_text_sha256": role_source.get("line_sha256"),
                }],
                "role_review_decision_sha256": decision.get("promotion_decision_sha256"),
            },
            "reviewed_at": decision.get("reviewed_at"),
            "cross_entity_promotion_lineage": {
                "promotion_packet_sha256": packet.get("promotion_packet_sha256"),
                "promotion_decision_sha256": decision.get("promotion_decision_sha256"),
                "source_operand_packet_sha256": packet.get("source_operand_packet_sha256"),
                "source_operand_decision_sha256": packet.get("source_operand_decision_sha256"),
                "numeric_value_selected_by_reviewer": False,
            },
        }
    return approvals
