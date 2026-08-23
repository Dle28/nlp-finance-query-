"""Hash-bound ChatGPT navigation promotion with deterministic cell materialization.

The reviewer may certify an exact source row, its period header, statement
role, issuer/scope, and an explicit document-line entity-role assertion.  The
review packet never contains the selected numeric value.  Only
``apply_navigation_binding_promotions`` reopens the immutable table and reads
that cell after the review receipt has passed all lineage checks.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .exact_cell_bindings import parse_vietnamese_numeric_candidate
from .exact_cell_bindings_v2 import resolve_source_unit
from .semantic_binding_corrections import canonical_sha256, sha256_file


PACKET_PROTOCOL = "vifinqa_navigation_binding_promotion_packet_v1"
DECISION_PROTOCOL = "vifinqa_navigation_binding_chatgpt_promotion_v1"
MANIFEST_PROTOCOL = "vifinqa_navigation_binding_promotion_review_v1"
MATERIALIZATION_PROTOCOL = "exact_cell_unit_binding_candidates_v5_navigation_promotion"
AUTHORITY_SCOPE = "navigation_binding_review_gate_equivalence"

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


class NavigationBindingPromotionError(ValueError):
    """Raised when a navigation promotion loses evidence or authority lineage."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object, label: str = "value") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NavigationBindingPromotionError(f"{label} must be a mapping")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise NavigationBindingPromotionError(f"{path}:{line_number} must be an object")
        result.append(value)
    return result


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise NavigationBindingPromotionError(f"{path} must be an object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _index(path: Path, field: str, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in _rows(path):
        key = _text(row.get(field))
        if not key or key in result:
            raise NavigationBindingPromotionError(f"{label} requires unique {field}")
        result[key] = row
    return result


def _operand(
    rows: Sequence[Mapping[str, Any]], *, question_id: int, stage_id: str, role: str
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    questions = [row for row in rows if row.get("question_id") == question_id]
    if len(questions) != 1:
        raise NavigationBindingPromotionError(f"Q{question_id} is missing or duplicated")
    question = questions[0]
    stages = [row for row in question.get("stages") or [] if row.get("stage_id") == stage_id]
    if len(stages) != 1:
        raise NavigationBindingPromotionError(f"Q{question_id} stage is missing or duplicated")
    stage = stages[0]
    operands = [row for row in stage.get("required_operands") or [] if row.get("role") == role]
    if len(operands) != 1:
        raise NavigationBindingPromotionError(f"Q{question_id} operand is missing or duplicated")
    return question, stage, operands[0]


def _validate_provenance(provenance: Mapping[str, Any]) -> None:
    authority = _mapping(provenance.get("authority_grant"), "authority_grant")
    if (
        provenance.get("reviewer_type") != "chatgpt_verified"
        or not _text(provenance.get("reviewer_id"))
        or not _text(provenance.get("model_family"))
        or provenance.get("review_policy") != "fail_closed_evidence_bound_v1"
        or authority.get("granted_by") != "campaign_owner"
        or authority.get("grant_scope") != AUTHORITY_SCOPE
        or authority.get("grant_basis") != "explicit_user_instruction"
    ):
        raise NavigationBindingPromotionError("ChatGPT navigation promotion lacks explicit authority")


def _source_line(table: Mapping[str, Any], line_number: int) -> tuple[str, str, str]:
    provenance = _mapping(table.get("source_provenance"), "source_provenance")
    source_path = Path(_text(provenance.get("source_path")))
    expected_sha = _text(provenance.get("source_sha256"))
    if not source_path.is_file() or sha256_file(source_path) != expected_sha:
        raise NavigationBindingPromotionError("source document is unavailable or stale")
    lines = source_path.read_text(encoding="utf-8").splitlines()
    if line_number < 1 or line_number > len(lines):
        raise NavigationBindingPromotionError("entity-role source line is outside the document")
    raw = lines[line_number - 1]
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), expected_sha


def build_navigation_binding_promotion_review(
    *,
    navigation_decisions: Path,
    navigation_decisions_manifest: Path,
    navigation_evidence: Path,
    navigation_evidence_manifest: Path,
    base_bindings: Path,
    base_bindings_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create numeric-value-free promotion packets and ChatGPT decisions."""

    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite navigation promotion review: {output_dir}")
    decision_meta = _json(navigation_decisions_manifest)
    evidence_meta = _json(navigation_evidence_manifest)
    base_meta = _json(base_bindings_manifest)
    context_meta = _json(evidence_context_manifest)
    if ((_mapping(decision_meta.get("outputs"), "decision outputs").get("decisions") or {}).get("sha256")) != sha256_file(navigation_decisions):
        raise NavigationBindingPromotionError("navigation decision SHA-256 mismatch")
    if ((_mapping(evidence_meta.get("outputs"), "evidence outputs").get("packets") or {}).get("sha256")) != sha256_file(navigation_evidence):
        raise NavigationBindingPromotionError("navigation evidence SHA-256 mismatch")
    if ((_mapping(base_meta.get("outputs"), "base outputs").get("bindings") or {}).get("sha256")) != sha256_file(base_bindings):
        raise NavigationBindingPromotionError("base bindings SHA-256 mismatch")
    if ((_mapping(base_meta.get("inputs"), "base inputs").get("structured_tables") or {}).get("sha256")) != sha256_file(structured_tables):
        raise NavigationBindingPromotionError("base bindings are stale for structured tables")
    if context_meta.get("sidecar_sha256") != sha256_file(evidence_context) or context_meta.get("input_structure_sha256") != sha256_file(structured_tables):
        raise NavigationBindingPromotionError("evidence context lineage mismatch")

    config = _json(config_path)
    provenance = _mapping(config.get("decision_provenance"), "decision_provenance")
    _validate_provenance(provenance)
    reviewed_at = _text(config.get("reviewed_at"))
    if not reviewed_at:
        raise NavigationBindingPromotionError("reviewed_at is required")

    navigation_by_question = {int(row["question_id"]): row for row in _rows(navigation_decisions)}
    evidence_by_question = {int(row["question_id"]): row for row in _rows(navigation_evidence)}
    binding_rows = _rows(base_bindings)
    tables = _index(structured_tables, "internal_table_uid", "structured tables")
    contexts = _index(evidence_context, "internal_table_uid", "evidence context")
    packets: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for raw_spec in config.get("promotions") or []:
        spec = _mapping(raw_spec, "promotion spec")
        question_id = int(spec.get("question_id"))
        stage_id = _text(spec.get("stage_id"))
        role = _text(spec.get("role"))
        uid = _text(spec.get("internal_table_uid"))
        document_uid = _text(spec.get("document_uid"))
        row_index = spec.get("row_index")
        value_column_index = spec.get("value_column_index")
        label_column_index = spec.get("label_column_index", 0)
        if not stage_id or not role or not uid or not document_uid or not all(isinstance(value, int) for value in (row_index, value_column_index, label_column_index)):
            raise NavigationBindingPromotionError(f"Q{question_id} promotion spec is incomplete")

        nav = _mapping(navigation_by_question.get(question_id), f"Q{question_id} navigation decision")
        evidence = _mapping(evidence_by_question.get(question_id), f"Q{question_id} navigation evidence")
        if nav.get("decision") != "approve_navigation_candidate_semantics":
            raise NavigationBindingPromotionError(f"Q{question_id} lacks approved navigation semantics")
        selected = _mapping(nav.get("selected_candidate"), "selected_candidate")
        if nav.get("packet_sha256") != evidence.get("packet_sha256"):
            raise NavigationBindingPromotionError(f"Q{question_id} navigation packet drift")
        if (
            selected.get("candidate_evidence_sha256") != spec.get("selected_candidate_evidence_sha256")
            or selected.get("exact_identity_scope_year_table") is not True
            or selected.get("internal_table_uid") != uid
            or selected.get("document_id") != document_uid
            or selected.get("row_index") != row_index
        ):
            raise NavigationBindingPromotionError(f"Q{question_id} selected navigation candidate drift")

        question, _, base_operand = _operand(binding_rows, question_id=question_id, stage_id=stage_id, role=role)
        if base_operand.get("binding_status") != "binding_blocked" or "PERIOD_CANDIDATE_BLOCKED" not in (base_operand.get("reason_codes") or []):
            raise NavigationBindingPromotionError(f"Q{question_id} is not the expected blocked operand")
        table = _mapping(tables.get(uid), f"Q{question_id} table")
        context = _mapping(contexts.get(uid), f"Q{question_id} context")
        if table.get("document_id") != document_uid or context.get("document_id") != document_uid:
            raise NavigationBindingPromotionError(f"Q{question_id} source identity mismatch")
        source_provenance = _mapping(table.get("source_provenance"), "source_provenance")
        context_provenance = _mapping(context.get("source_provenance"), "context source_provenance")
        if any(source_provenance.get(key) != context_provenance.get(key) for key in ("source_sha256", "table_sha256")):
            raise NavigationBindingPromotionError(f"Q{question_id} source/context lineage mismatch")
        table_rows = table.get("rows") or []
        if row_index < 0 or row_index >= len(table_rows) or not isinstance(table_rows[row_index], list):
            raise NavigationBindingPromotionError(f"Q{question_id} source row is invalid")
        source_row = table_rows[row_index]
        if max(value_column_index, label_column_index) >= len(source_row):
            raise NavigationBindingPromotionError(f"Q{question_id} source column is invalid")
        label = str(source_row[label_column_index])
        if _text(spec.get("row_label_must_contain")).casefold() not in label.casefold():
            raise NavigationBindingPromotionError(f"Q{question_id} row label literal is absent")
        headers = [
            value for value in _mapping(context.get("canonical_headers"), "canonical_headers").get("columns") or []
            if isinstance(value, Mapping) and value.get("column_index") == value_column_index
        ]
        if len(headers) != 1:
            raise NavigationBindingPromotionError(f"Q{question_id} period header is not unique")
        header = headers[0]
        expected_year = int(spec.get("period_year"))
        if str(expected_year) not in (_text(header.get("source_label")) + " " + " ".join(map(str, header.get("period_labels") or []))):
            raise NavigationBindingPromotionError(f"Q{question_id} period header does not prove the requested year")
        header_coordinates = header.get("header_source_cells") or []
        if not header_coordinates:
            raise NavigationBindingPromotionError(f"Q{question_id} period header lacks exact coordinates")
        header_anchors = []
        for coordinate in header_coordinates:
            row_i, column_i = int(coordinate["row_index"]), int(coordinate["column_index"])
            header_anchors.append({
                "row_index": row_i,
                "column_index": column_i,
                "raw_source_cell": str(table_rows[row_i][column_i]),
                "cell_provenance": table["cell_provenance"][row_i][column_i],
            })
        source_unit, _, multiplier = resolve_source_unit(header_anchors)
        if source_unit != spec.get("source_unit") or multiplier is None or format(multiplier, "f") != str(spec.get("source_to_vnd_multiplier")):
            raise NavigationBindingPromotionError(f"Q{question_id} source unit mismatch")
        source_title = _text(_mapping(context.get("context_trace"), "context_trace").get("source_title"))
        for literal in spec.get("source_title_must_contain") or []:
            if _text(literal).casefold() not in source_title.casefold():
                raise NavigationBindingPromotionError(f"Q{question_id} source title proof is incomplete")
        line_number = int(_mapping(spec.get("entity_role_source"), "entity_role_source").get("line_number"))
        role_line, role_line_sha, source_file_sha = _source_line(table, line_number)
        for literal in _mapping(spec.get("entity_role_source"), "entity_role_source").get("must_contain") or []:
            if _text(literal).casefold() not in role_line.casefold():
                raise NavigationBindingPromotionError(f"Q{question_id} entity-role source line is insufficient")

        value_raw = str(source_row[value_column_index])
        packet_payload = {
            "schema_version": 1,
            "protocol": PACKET_PROTOCOL,
            "question_id": question_id,
            "stage_id": stage_id,
            "role": role,
            "question_context": question.get("question_context") or {},
            "navigation_semantic_decision_sha256": nav.get("decision_sha256"),
            "selected_candidate_evidence_sha256": selected.get("candidate_evidence_sha256"),
            "source_identity": {
                "document_uid": document_uid,
                "internal_table_uid": uid,
                "source_file_sha256": source_provenance.get("source_sha256"),
                "table_sha256": source_provenance.get("table_sha256"),
            },
            "reviewed_row": {
                "row_index": row_index,
                "label_column_index": label_column_index,
                "label_raw_text": label,
                "label_raw_text_sha256": hashlib.sha256(label.encode("utf-8")).hexdigest(),
                "full_row_sha256": canonical_sha256(source_row),
                "value_column_index": value_column_index,
                "value_cell_raw_sha256": hashlib.sha256(value_raw.encode("utf-8")).hexdigest(),
            },
            "reviewed_header": {
                "source_label": header.get("source_label"),
                "period_labels": header.get("period_labels") or [],
                "unit_labels": header.get("unit_labels") or [],
                "header_source_cells": header_coordinates,
                "source_title": source_title,
                "source_title_sha256": hashlib.sha256(source_title.encode("utf-8")).hexdigest(),
            },
            "entity_role_source": {
                "role": spec.get("entity_role"),
                "line_number": line_number,
                "raw_text": role_line,
                "raw_text_sha256": role_line_sha,
                "source_file_sha256": source_file_sha,
            },
            "review_findings": {
                "exact_row_semantics": "PASS",
                "primary_statement_role": "PASS",
                "issuer_identity": "PASS",
                "reporting_scope": "PASS",
                "period": "PASS",
                "unit": "PASS",
                "entity_role": "PASS",
                "sector": "NOT_INFERRED_EXACT_SOURCE_ROW_ONLY",
            },
            "source_contract": dict(SAFE_SOURCE_CONTRACT),
        }
        packet = {**packet_payload, "promotion_packet_sha256": canonical_sha256(packet_payload)}
        packets.append(packet)
        decision_payload = {
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "promotion_packet_sha256": packet["promotion_packet_sha256"],
            "decision": "approve_navigation_binding_promotion",
            "approved_variable_id": spec.get("variable_id"),
            "approved_entity": spec.get("entity"),
            "approved_scope": spec.get("scope"),
            "approved_entity_role": spec.get("entity_role"),
            "decision_provenance": dict(provenance),
            "reviewed_at": reviewed_at,
            "notes": spec.get("notes") or "",
            "reviewer_authority": {
                "may_approve_exact_row": True,
                "may_approve_period_header": True,
                "may_approve_table_role": True,
                "may_approve_entity_role": True,
                "may_infer_sector": False,
                "may_select_value": False,
                "may_execute_formula": False,
                "release_authorized": False,
            },
            "deterministic_materialization_required": True,
            "source_contract": dict(SAFE_SOURCE_CONTRACT),
        }
        decisions.append({**decision_payload, "promotion_decision_sha256": canonical_sha256(decision_payload)})

    if not packets:
        raise NavigationBindingPromotionError("at least one promotion is required")
    output_dir.mkdir(parents=True, exist_ok=False)
    packets_path = output_dir / "navigation_binding_promotion_packets_v1.jsonl"
    decisions_path = output_dir / "navigation_binding_chatgpt_promotions_v1.jsonl"
    _write_jsonl(packets_path, packets)
    _write_jsonl(decisions_path, decisions)
    manifest = {
        "schema_version": 1,
        "protocol": MANIFEST_PROTOCOL,
        "status": "reviewed_for_deterministic_navigation_binding",
        "inputs": {
            "navigation_decisions": {"path": str(navigation_decisions), "sha256": sha256_file(navigation_decisions)},
            "navigation_decisions_manifest": {"path": str(navigation_decisions_manifest), "sha256": sha256_file(navigation_decisions_manifest)},
            "navigation_evidence": {"path": str(navigation_evidence), "sha256": sha256_file(navigation_evidence)},
            "navigation_evidence_manifest": {"path": str(navigation_evidence_manifest), "sha256": sha256_file(navigation_evidence_manifest)},
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
        "counts": {"promotion_count": len(decisions), "numeric_value_exposure_count": 0},
        "source_contract": dict(SAFE_SOURCE_CONTRACT),
    }
    manifest_path = output_dir / "navigation_binding_promotion_review_v1.manifest.json"
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def load_navigation_binding_promotions(
    *, packets: Path, decisions: Path, manifest_path: Path, base_bindings: Path,
    structured_tables: Path, evidence_context: Path,
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Validate promotion receipts without exposing their selected value."""

    manifest = _json(manifest_path)
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise NavigationBindingPromotionError("unexpected navigation promotion manifest")
    inputs, outputs = _mapping(manifest.get("inputs"), "inputs"), _mapping(manifest.get("outputs"), "outputs")
    for name, path in {"base_bindings": base_bindings, "structured_tables": structured_tables, "evidence_context": evidence_context}.items():
        if _mapping(inputs.get(name), name).get("sha256") != sha256_file(path):
            raise NavigationBindingPromotionError(f"navigation promotion is stale for {name}")
    if _mapping(outputs.get("packets"), "packets").get("sha256") != sha256_file(packets) or _mapping(outputs.get("decisions"), "decisions").get("sha256") != sha256_file(decisions):
        raise NavigationBindingPromotionError("navigation promotion output SHA-256 mismatch")
    packet_index: dict[str, Mapping[str, Any]] = {}
    for packet in _rows(packets):
        packet_sha = _text(packet.get("promotion_packet_sha256"))
        payload = {key: value for key, value in packet.items() if key != "promotion_packet_sha256"}
        if packet.get("protocol") != PACKET_PROTOCOL or not packet_sha or packet_sha != canonical_sha256(payload) or packet_sha in packet_index:
            raise NavigationBindingPromotionError("navigation promotion packet identity is invalid")
        if _mapping(packet.get("source_contract"), "source_contract").get("numeric_value_exposed_to_reviewer") is not False:
            raise NavigationBindingPromotionError("navigation promotion packet exposes a numeric value")
        packet_index[packet_sha] = packet
    binding_rows = _rows(base_bindings)
    tables = _index(structured_tables, "internal_table_uid", "structured tables")
    contexts = _index(evidence_context, "internal_table_uid", "evidence context")
    result: dict[tuple[int, str, str], dict[str, Any]] = {}
    for decision in _rows(decisions):
        decision_sha = _text(decision.get("promotion_decision_sha256"))
        payload = {key: value for key, value in decision.items() if key != "promotion_decision_sha256"}
        if decision.get("protocol") != DECISION_PROTOCOL or not decision_sha or decision_sha != canonical_sha256(payload):
            raise NavigationBindingPromotionError("navigation promotion decision identity is invalid")
        packet = packet_index.get(_text(decision.get("promotion_packet_sha256")))
        if packet is None:
            raise NavigationBindingPromotionError("navigation promotion decision references a missing packet")
        if decision.get("decision") != "approve_navigation_binding_promotion":
            continue
        _validate_provenance(_mapping(decision.get("decision_provenance"), "decision_provenance"))
        authority = _mapping(decision.get("reviewer_authority"), "reviewer_authority")
        if (
            authority.get("may_approve_exact_row") is not True
            or authority.get("may_approve_period_header") is not True
            or authority.get("may_approve_table_role") is not True
            or authority.get("may_approve_entity_role") is not True
            or authority.get("may_infer_sector") is not False
            or authority.get("may_select_value") is not False
            or authority.get("may_execute_formula") is not False
            or authority.get("release_authorized") is not False
            or decision.get("deterministic_materialization_required") is not True
        ):
            raise NavigationBindingPromotionError("navigation promotion authority boundary is invalid")
        question_id, stage_id, role = int(packet["question_id"]), _text(packet.get("stage_id")), _text(packet.get("role"))
        _, _, operand = _operand(binding_rows, question_id=question_id, stage_id=stage_id, role=role)
        if operand.get("binding_status") != "binding_blocked":
            raise NavigationBindingPromotionError(f"Q{question_id} base operand drift")
        source = _mapping(packet.get("source_identity"), "source_identity")
        uid = _text(source.get("internal_table_uid"))
        table, context = _mapping(tables.get(uid), "table"), _mapping(contexts.get(uid), "context")
        if table.get("document_id") != source.get("document_uid") or context.get("document_id") != source.get("document_uid"):
            raise NavigationBindingPromotionError(f"Q{question_id} document identity drift")
        reviewed = _mapping(packet.get("reviewed_row"), "reviewed_row")
        row_index, column_index = reviewed.get("row_index"), reviewed.get("value_column_index")
        rows = table.get("rows") or []
        if not isinstance(row_index, int) or not isinstance(column_index, int) or row_index < 0 or row_index >= len(rows) or column_index < 0 or column_index >= len(rows[row_index]):
            raise NavigationBindingPromotionError(f"Q{question_id} reviewed coordinates are invalid")
        row = rows[row_index]
        raw = str(row[column_index])
        if canonical_sha256(row) != reviewed.get("full_row_sha256") or hashlib.sha256(raw.encode("utf-8")).hexdigest() != reviewed.get("value_cell_raw_sha256"):
            raise NavigationBindingPromotionError(f"Q{question_id} reviewed row is stale")
        role_source = _mapping(packet.get("entity_role_source"), "entity_role_source")
        source_line, source_line_sha, source_file_sha = _source_line(table, int(role_source["line_number"]))
        if source_line != role_source.get("raw_text") or source_line_sha != role_source.get("raw_text_sha256") or source_file_sha != role_source.get("source_file_sha256"):
            raise NavigationBindingPromotionError(f"Q{question_id} entity-role source drift")
        key = (question_id, stage_id, role)
        if key in result:
            raise NavigationBindingPromotionError("duplicate navigation promotion key")
        result[key] = {"packet": dict(packet), "decision": dict(decision), "table": dict(table), "context": dict(context), "row": list(row)}
    if len(result) != int(_mapping(manifest.get("counts"), "counts").get("promotion_count") or 0):
        raise NavigationBindingPromotionError("navigation promotion count drift")
    return result


def apply_navigation_binding_promotions(
    *, base_bindings: Path, base_bindings_manifest: Path, structured_tables: Path,
    evidence_context: Path, packets: Path, decisions: Path, promotion_manifest: Path,
    output: Path,
) -> dict[str, Any]:
    """Reopen exact tables and materialize only approved navigation bindings."""

    base_meta = _json(base_bindings_manifest)
    if ((_mapping(base_meta.get("outputs"), "outputs").get("bindings") or {}).get("sha256")) != sha256_file(base_bindings):
        raise NavigationBindingPromotionError("base binding manifest is stale")
    promotions = load_navigation_binding_promotions(
        packets=packets, decisions=decisions, manifest_path=promotion_manifest,
        base_bindings=base_bindings, structured_tables=structured_tables,
        evidence_context=evidence_context,
    )
    rows = _rows(base_bindings)
    applied: list[dict[str, Any]] = []
    for key, promotion in promotions.items():
        question_id, stage_id, role = key
        question, _, operand = _operand(rows, question_id=question_id, stage_id=stage_id, role=role)
        if not isinstance(question, dict) or not isinstance(operand, dict):
            raise NavigationBindingPromotionError("promotion target is not mutable")
        packet, decision, table, context, row = promotion["packet"], promotion["decision"], promotion["table"], promotion["context"], promotion["row"]
        reviewed = _mapping(packet.get("reviewed_row"), "reviewed_row")
        row_index, column_index = int(reviewed["row_index"]), int(reviewed["value_column_index"])
        raw = str(row[column_index])
        parsed_status, decimal_literal, parse_policy = parse_vietnamese_numeric_candidate(raw)
        if parsed_status != "parsed_decimal_candidate" or decimal_literal is None:
            raise NavigationBindingPromotionError(f"Q{question_id} promoted value is not Decimal-parseable")
        header = _mapping(packet.get("reviewed_header"), "reviewed_header")
        header_coordinates = list(header.get("header_source_cells") or [])
        header_anchors = []
        for coordinate in header_coordinates:
            row_i, column_i = int(coordinate["row_index"]), int(coordinate["column_index"])
            header_anchors.append({
                "row_index": row_i,
                "column_index": column_i,
                "raw_source_cell": str(table["rows"][row_i][column_i]),
                "cell_provenance": table["cell_provenance"][row_i][column_i],
            })
        source_unit, _, multiplier = resolve_source_unit(header_anchors)
        if source_unit is None or multiplier is None:
            raise NavigationBindingPromotionError(f"Q{question_id} promoted unit is unresolved")
        requested_years = list(_mapping(question.get("question_context"), "question_context").get("years") or [])
        period_labels = list(header.get("period_labels") or [])
        if len(requested_years) != 1 or str(requested_years[0]) not in " ".join(map(str, period_labels)):
            raise NavigationBindingPromotionError(f"Q{question_id} promoted period is inconsistent")
        source_title = _text(_mapping(context.get("context_trace"), "context_trace").get("source_title"))
        operand.clear()
        operand.update({
            "question_id": question_id,
            "stage_id": stage_id,
            "role": role,
            "concept_id": decision.get("approved_variable_id"),
            "binding_status": "binding_ready",
            "formula_output_kind": "currency",
            "requested_output_unit": question.get("requested_output_unit"),
            "document_id": table.get("document_id"),
            "internal_table_uid": table.get("internal_table_uid"),
            "row_index": row_index,
            "column_index": column_index,
            "raw_source_row": row,
            "raw_source_cell": raw,
            "cell_provenance": table["cell_provenance"][row_index][column_index],
            "header_source_cells": header_coordinates,
            "period_labels": period_labels,
            "period_resolution_method": None,
            "period_source_title_sha256": hashlib.sha256(source_title.encode("utf-8")).hexdigest(),
            "period_source_date": None,
            "period_entity_corroboration": {},
            "source_unit_anchors": header_anchors,
            "source_unit": source_unit,
            "source_to_vnd_multiplier": format(multiplier, "f"),
            "vnd_to_output_divisor": _mapping(question.get("requested_output_unit"), "requested_output_unit").get("vnd_to_output_divisor"),
            "raw_decimal_candidate": decimal_literal,
            "numeric_parse_policy": parse_policy,
            "reason_codes": [],
            "navigation_promotion": {
                "protocol": DECISION_PROTOCOL,
                "promotion_packet_sha256": packet.get("promotion_packet_sha256"),
                "promotion_decision_sha256": decision.get("promotion_decision_sha256"),
                "decision_provenance": decision.get("decision_provenance"),
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
        question["binding_packet_status"] = "binding_ready"
        applied.append({"question_id": question_id, "stage_id": stage_id, "role": role, "promotion_decision_sha256": decision.get("promotion_decision_sha256")})

    for question in rows:
        question["schema_version"] = 5
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
        "schema_version": 5,
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
            "navigation_promotion_count": len(applied),
            "binding_status_counts": dict(sorted(status_counts.items())),
            "binding_packet_status_counts": dict(sorted(packet_counts.items())),
        },
        "applied_promotions": applied,
        "source_contract": {**SAFE_SOURCE_CONTRACT, "executor_private_numeric_materialization": True},
    }
    manifest_path = output.with_suffix(".manifest.json")
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def effective_approval_from_navigation_promotion(promotion: Mapping[str, Any]) -> dict[str, Any]:
    """Build the exact semantic approval consumed by authorization replay."""

    packet = _mapping(promotion.get("packet"), "packet")
    decision = _mapping(promotion.get("decision"), "decision")
    reviewed = _mapping(packet.get("reviewed_row"), "reviewed_row")
    source = _mapping(packet.get("source_identity"), "source_identity")
    role_source = _mapping(packet.get("entity_role_source"), "entity_role_source")
    provenance = dict(_mapping(decision.get("decision_provenance"), "decision_provenance"))
    return {
        "approval_id": decision.get("promotion_decision_sha256"),
        "variable_approval_id": decision.get("promotion_decision_sha256"),
        "variable_id": decision.get("approved_variable_id"),
        "entity": decision.get("approved_entity"),
        "scope": decision.get("approved_scope"),
        "entity_role": decision.get("approved_entity_role"),
        "row_label": {
            "document_uid": source.get("document_uid"),
            "internal_table_uid": source.get("internal_table_uid"),
            "row_index": reviewed.get("row_index"),
            "column_index": reviewed.get("label_column_index"),
            "raw_text": reviewed.get("label_raw_text"),
            "raw_text_sha256": reviewed.get("label_raw_text_sha256"),
        },
        "decision_provenance": provenance,
        "variable_decision_provenance": provenance,
        "entity_scope_decision_provenance": provenance,
        "entity_role_decision_provenance": provenance,
        "entity_role_evidence": {
            "source_text": role_source.get("raw_text"),
            "source_text_sha256": role_source.get("raw_text_sha256"),
            "source_anchors": [{
                "kind": "document_text_line",
                "document_uid": source.get("document_uid"),
                "line_number": role_source.get("line_number"),
                "source_file_sha256": role_source.get("source_file_sha256"),
                "raw_text_sha256": role_source.get("raw_text_sha256"),
            }],
            "role_review_decision_sha256": decision.get("promotion_decision_sha256"),
        },
        "reviewed_at": decision.get("reviewed_at"),
        "navigation_promotion_lineage": {
            "promotion_packet_sha256": packet.get("promotion_packet_sha256"),
            "promotion_decision_sha256": decision.get("promotion_decision_sha256"),
            "numeric_value_selected_by_reviewer": False,
        },
    }
