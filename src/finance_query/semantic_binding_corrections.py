"""Authorized semantic-row corrections with deterministic value materialization.

The reviewer sees only claim text and non-value row cells.  A correction may
select a different semantic row, but it cannot read or select the numeric
value.  ``apply_semantic_binding_corrections`` reopens the hash-bound table and
materializes the value cell after the review has been validated.

Original human decisions are never rewritten.  The effective correction keeps
their hashes as superseded-decision lineage and records separate
``chatgpt_verified`` provenance.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .exact_cell_bindings import parse_vietnamese_numeric_candidate


PACKET_PROTOCOL = "vifinqa_semantic_binding_correction_packet_v1"
DECISION_PROTOCOL = "vifinqa_semantic_binding_chatgpt_correction_v1"
MANIFEST_PROTOCOL = "vifinqa_semantic_binding_correction_review_v1"
MATERIALIZATION_PROTOCOL = "exact_cell_unit_binding_candidates_v4_semantic_correction"
AUTHORITY_SCOPE = "semantic_binding_review_gate_equivalence"
_NUMERICISH = re.compile(r"^\s*\(?[-+]?\d[\d.,\s%]*\)?\s*$")

SAFE_SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "release_authorized": False,
    "may_materialize_answer": False,
    "may_select_value": False,
    "numeric_literals_exposed_to_reviewer": False,
}


class SemanticBindingCorrectionError(ValueError):
    """Raised when correction review or deterministic rebinding loses lineage."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object, label: str = "value") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticBindingCorrectionError(f"{label} must be a mapping")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise SemanticBindingCorrectionError(f"{path}:{line_number} must be a JSON object")
        result.append(value)
    return result


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise SemanticBindingCorrectionError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _binding_operand(
    rows: Sequence[Mapping[str, Any]], *, question_id: int, stage_id: str, role: str
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    questions = [row for row in rows if row.get("question_id") == question_id]
    if len(questions) != 1:
        raise SemanticBindingCorrectionError(f"Q{question_id} binding is missing or duplicated")
    question = questions[0]
    stages = [stage for stage in question.get("stages") or [] if stage.get("stage_id") == stage_id]
    if len(stages) != 1:
        raise SemanticBindingCorrectionError(f"Q{question_id} stage binding is missing or duplicated")
    stage = stages[0]
    operands = [operand for operand in stage.get("required_operands") or [] if operand.get("role") == role]
    if len(operands) != 1:
        raise SemanticBindingCorrectionError(f"Q{question_id} operand binding is missing or duplicated")
    return question, stage, operands[0]


def _table_index(path: Path) -> dict[str, Mapping[str, Any]]:
    tables: dict[str, Mapping[str, Any]] = {}
    for table in _rows(path):
        uid = _text(table.get("internal_table_uid"))
        if not uid or uid in tables:
            raise SemanticBindingCorrectionError("structured tables require unique internal_table_uid")
        tables[uid] = table
    return tables


def _non_value_cells(row: Sequence[object], value_column_index: int) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for column_index, raw_value in enumerate(row):
        raw_text = str(raw_value)
        if (
            column_index == value_column_index
            or not raw_text.strip()
            or _NUMERICISH.fullmatch(raw_text)
        ):
            continue
        cells.append(
            {
                "column_index": column_index,
                "raw_text": raw_text,
                "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            }
        )
    return cells


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
        raise SemanticBindingCorrectionError("ChatGPT semantic correction lacks explicit authority")


def build_semantic_binding_correction_review(
    *,
    audit_decision: Path,
    audit_manifest: Path,
    base_bindings: Path,
    base_bindings_manifest: Path,
    structured_tables: Path,
    semantic_review_queue: Path,
    semantic_human_decisions: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build literal-free correction packets and authorized review decisions."""

    config = _json(config_path)
    audit_meta = _json(audit_manifest)
    base_meta = _json(base_bindings_manifest)
    expected_audit = ((_mapping(audit_meta.get("outputs"), "audit.outputs").get("decision") or {}).get("sha256"))
    if expected_audit != sha256_file(audit_decision):
        raise SemanticBindingCorrectionError("campaign audit decision SHA-256 mismatch")
    expected_bindings = ((_mapping(base_meta.get("outputs"), "bindings.outputs").get("bindings") or {}).get("sha256"))
    if expected_bindings != sha256_file(base_bindings):
        raise SemanticBindingCorrectionError("base binding SHA-256 mismatch")
    expected_tables = ((_mapping(base_meta.get("inputs"), "bindings.inputs").get("structured_tables") or {}).get("sha256"))
    if expected_tables != sha256_file(structured_tables):
        raise SemanticBindingCorrectionError("base bindings are stale for structured tables")

    provenance = _mapping(config.get("decision_provenance"), "decision_provenance")
    _validate_provenance(provenance)
    reviewed_at = _text(config.get("reviewed_at"))
    if not reviewed_at:
        raise SemanticBindingCorrectionError("correction config requires reviewed_at")

    audit_rows = _rows(audit_decision)
    audit_reviews = {
        int(review["question_id"]): review
        for row in audit_rows
        for review in row.get("candidate_reviews") or []
        if isinstance(review, Mapping)
    }
    binding_rows = _rows(base_bindings)
    tables = _table_index(structured_tables)
    queue_rows = _rows(semantic_review_queue)
    human_rows = _rows(semantic_human_decisions)
    human_by_queue_sha = {_text(row.get("queue_item_sha256")): row for row in human_rows}

    packets: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for spec in config.get("corrections") or []:
        spec = _mapping(spec, "correction spec")
        question_id = int(spec.get("question_id"))
        stage_id = _text(spec.get("stage_id"))
        role = _text(spec.get("role"))
        corrected_variable_id = _text(spec.get("corrected_variable_id"))
        target_row_index = spec.get("target_row_index")
        target_row_literal = _text(spec.get("target_row_must_contain"))
        if not stage_id or not role or not corrected_variable_id or not isinstance(target_row_index, int):
            raise SemanticBindingCorrectionError(f"Q{question_id} correction spec is incomplete")

        audit = _mapping(audit_reviews.get(question_id), f"Q{question_id} audit")
        if audit.get("review_outcome") != "semantic_mismatch":
            raise SemanticBindingCorrectionError(f"Q{question_id} is not an audited semantic mismatch")
        if _text(audit.get("reason_code")) != _text(spec.get("required_reason_code")):
            raise SemanticBindingCorrectionError(f"Q{question_id} audit reason drift")
        _, _, operand = _binding_operand(
            binding_rows, question_id=question_id, stage_id=stage_id, role=role
        )
        uid = _text(operand.get("internal_table_uid"))
        table = _mapping(tables.get(uid), f"Q{question_id} table")
        rows = table.get("rows") or []
        old_row_index = operand.get("row_index")
        value_column_index = operand.get("column_index")
        if (
            not isinstance(old_row_index, int)
            or not isinstance(value_column_index, int)
            or target_row_index < 0
            or target_row_index >= len(rows)
            or old_row_index < 0
            or old_row_index >= len(rows)
        ):
            raise SemanticBindingCorrectionError(f"Q{question_id} correction coordinates are invalid")
        old_row, target_row = rows[old_row_index], rows[target_row_index]
        if not isinstance(old_row, list) or not isinstance(target_row, list):
            raise SemanticBindingCorrectionError(f"Q{question_id} correction rows are invalid")
        if value_column_index >= len(old_row) or value_column_index >= len(target_row):
            raise SemanticBindingCorrectionError(f"Q{question_id} value column is invalid")
        target_text = " | ".join(cell["raw_text"] for cell in _non_value_cells(target_row, value_column_index))
        if not target_row_literal or target_row_literal.casefold() not in target_text.casefold():
            raise SemanticBindingCorrectionError(f"Q{question_id} target row literal is absent")
        alternative = _mapping(audit.get("alternative_semantic_row"), "alternative_semantic_row")
        if (
            alternative.get("row_index") != target_row_index
            or alternative.get("full_row_sha256") != canonical_sha256(target_row)
        ):
            raise SemanticBindingCorrectionError(f"Q{question_id} target row is not the audited alternative")

        queue_matches = [
            row for row in queue_rows
            if row.get("question_id") == question_id
            and row.get("stage_id") == stage_id
            and row.get("role") == role
        ]
        if len(queue_matches) != 1:
            raise SemanticBindingCorrectionError(f"Q{question_id} original semantic queue item is unavailable")
        queue_item = queue_matches[0]
        original_human = human_by_queue_sha.get(_text(queue_item.get("queue_item_sha256")))
        if original_human is None or original_human.get("decision") != "approve":
            raise SemanticBindingCorrectionError(f"Q{question_id} original human approval is unavailable")

        label_column_index = int(spec.get("target_label_column_index", 0))
        label_raw = str(target_row[label_column_index])
        packet_payload = {
            "schema_version": 1,
            "protocol": PACKET_PROTOCOL,
            "question_id": question_id,
            "stage_id": stage_id,
            "role": role,
            "question": audit.get("question"),
            "audited_mismatch": {
                "candidate_review_sha256": audit.get("candidate_review_sha256"),
                "reason_code": audit.get("reason_code"),
                "rationale": audit.get("rationale"),
            },
            "source_identity": {
                "document_uid": table.get("document_id"),
                "internal_table_uid": uid,
                "table_sha256": _mapping(table.get("source_provenance"), "source_provenance").get("table_sha256"),
                "source_file_sha256": _mapping(table.get("source_provenance"), "source_provenance").get("source_sha256"),
            },
            "current_semantic_row": {
                "row_index": old_row_index,
                "non_value_cells": _non_value_cells(old_row, value_column_index),
                "full_row_sha256": canonical_sha256(old_row),
                "value_cell_raw_sha256": hashlib.sha256(str(old_row[value_column_index]).encode("utf-8")).hexdigest(),
            },
            "proposed_semantic_row": {
                "row_index": target_row_index,
                "non_value_cells": _non_value_cells(target_row, value_column_index),
                "full_row_sha256": canonical_sha256(target_row),
                "label_cell": {
                    "column_index": label_column_index,
                    "raw_text": label_raw,
                    "raw_text_sha256": hashlib.sha256(label_raw.encode("utf-8")).hexdigest(),
                },
                "value_column_index": value_column_index,
                "value_cell_raw_sha256": hashlib.sha256(str(target_row[value_column_index]).encode("utf-8")).hexdigest(),
            },
            "semantic_transition": {
                "from_variable_id": operand.get("concept_id") or role,
                "to_variable_id": corrected_variable_id,
                "interpretation": spec.get("interpretation"),
            },
            "superseded_human_decision_lineage": {
                "queue_item_sha256": queue_item.get("queue_item_sha256"),
                "decision_sha256": canonical_sha256(original_human),
                "reviewer_type": _mapping(original_human.get("decision_provenance"), "human provenance").get("reviewer_type"),
            },
            "reviewer_action": "approve_semantic_row_correction | reject_correction | needs_investigation",
            "source_contract": dict(SAFE_SOURCE_CONTRACT),
        }
        packet = {**packet_payload, "correction_packet_sha256": canonical_sha256(packet_payload)}
        packets.append(packet)

        decision_payload = {
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "correction_packet_sha256": packet["correction_packet_sha256"],
            "decision": "approve_semantic_row_correction",
            "corrected_variable_id": corrected_variable_id,
            "selected_semantic_row": {
                "document_uid": table.get("document_id"),
                "internal_table_uid": uid,
                "row_index": target_row_index,
                "label_column_index": label_column_index,
                "label_raw_text_sha256": hashlib.sha256(label_raw.encode("utf-8")).hexdigest(),
                "full_row_sha256": canonical_sha256(target_row),
                "value_column_index": value_column_index,
                "value_cell_raw_sha256": hashlib.sha256(str(target_row[value_column_index]).encode("utf-8")).hexdigest(),
            },
            "decision_provenance": dict(provenance),
            "reviewed_at": reviewed_at,
            "notes": spec.get("notes") or "",
            "reviewer_authority": {
                "may_select_semantic_row": True,
                "may_select_value": False,
                "may_change_binding": False,
                "may_execute_formula": False,
                "release_authorized": False,
            },
            "deterministic_materialization_required": True,
            "source_contract": dict(SAFE_SOURCE_CONTRACT),
        }
        decisions.append({**decision_payload, "correction_decision_sha256": canonical_sha256(decision_payload)})

    if not packets:
        raise SemanticBindingCorrectionError("correction review requires at least one correction")
    output_dir.mkdir(parents=True, exist_ok=False)
    packet_path = output_dir / "semantic_binding_correction_packets_v1.jsonl"
    decision_path = output_dir / "semantic_binding_chatgpt_corrections_v1.jsonl"
    _write_jsonl(packet_path, packets)
    _write_jsonl(decision_path, decisions)
    manifest = {
        "schema_version": 1,
        "protocol": MANIFEST_PROTOCOL,
        "status": "reviewed_for_deterministic_rebinding",
        "inputs": {
            "audit_decision": {"path": str(audit_decision), "sha256": sha256_file(audit_decision)},
            "audit_manifest": {"path": str(audit_manifest), "sha256": sha256_file(audit_manifest)},
            "base_bindings": {"path": str(base_bindings), "sha256": sha256_file(base_bindings)},
            "base_bindings_manifest": {"path": str(base_bindings_manifest), "sha256": sha256_file(base_bindings_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "semantic_review_queue": {"path": str(semantic_review_queue), "sha256": sha256_file(semantic_review_queue)},
            "semantic_human_decisions": {"path": str(semantic_human_decisions), "sha256": sha256_file(semantic_human_decisions)},
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
        },
        "outputs": {
            "packets": {"path": str(packet_path), "sha256": sha256_file(packet_path)},
            "decisions": {"path": str(decision_path), "sha256": sha256_file(decision_path)},
        },
        "counts": {
            "correction_count": len(decisions),
            "numeric_literal_exposure_count": 0,
            "superseded_human_decision_count": len(decisions),
        },
        "source_contract": dict(SAFE_SOURCE_CONTRACT),
    }
    manifest_path = output_dir / "semantic_binding_correction_review_v1.manifest.json"
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def load_semantic_binding_corrections(
    *,
    packets: Path,
    decisions: Path,
    manifest_path: Path,
    base_bindings: Path,
    structured_tables: Path,
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Validate correction reviews and return deterministic rebinding instructions."""

    manifest = _json(manifest_path)
    if manifest.get("protocol") != MANIFEST_PROTOCOL:
        raise SemanticBindingCorrectionError("unexpected semantic correction manifest protocol")
    inputs = _mapping(manifest.get("inputs"), "manifest.inputs")
    outputs = _mapping(manifest.get("outputs"), "manifest.outputs")
    for name, path in {"base_bindings": base_bindings, "structured_tables": structured_tables}.items():
        if (_mapping(inputs.get(name), name).get("sha256")) != sha256_file(path):
            raise SemanticBindingCorrectionError(f"semantic correction is stale for {name}")
    if _mapping(outputs.get("packets"), "packets").get("sha256") != sha256_file(packets):
        raise SemanticBindingCorrectionError("semantic correction packet SHA-256 mismatch")
    if _mapping(outputs.get("decisions"), "decisions").get("sha256") != sha256_file(decisions):
        raise SemanticBindingCorrectionError("semantic correction decision SHA-256 mismatch")

    packet_index: dict[str, Mapping[str, Any]] = {}
    for packet in _rows(packets):
        if packet.get("protocol") != PACKET_PROTOCOL:
            raise SemanticBindingCorrectionError("unexpected semantic correction packet protocol")
        packet_sha = _text(packet.get("correction_packet_sha256"))
        payload = {key: value for key, value in packet.items() if key != "correction_packet_sha256"}
        if not packet_sha or packet_sha != canonical_sha256(payload) or packet_sha in packet_index:
            raise SemanticBindingCorrectionError("semantic correction packet identity is invalid")
        if _mapping(packet.get("source_contract"), "packet.source_contract").get("numeric_literals_exposed_to_reviewer") is not False:
            raise SemanticBindingCorrectionError("semantic correction packet may not expose numeric literals")
        packet_index[packet_sha] = packet

    tables = _table_index(structured_tables)
    base_rows = _rows(base_bindings)
    result: dict[tuple[int, str, str], dict[str, Any]] = {}
    for decision in _rows(decisions):
        if decision.get("protocol") != DECISION_PROTOCOL:
            raise SemanticBindingCorrectionError("unexpected semantic correction decision protocol")
        decision_sha = _text(decision.get("correction_decision_sha256"))
        decision_payload = {key: value for key, value in decision.items() if key != "correction_decision_sha256"}
        if not decision_sha or decision_sha != canonical_sha256(decision_payload):
            raise SemanticBindingCorrectionError("semantic correction decision identity is invalid")
        packet_sha = _text(decision.get("correction_packet_sha256"))
        packet = packet_index.get(packet_sha)
        if packet is None:
            raise SemanticBindingCorrectionError("semantic correction decision references a missing packet")
        if decision.get("decision") != "approve_semantic_row_correction":
            continue
        _validate_provenance(_mapping(decision.get("decision_provenance"), "decision_provenance"))
        authority = _mapping(decision.get("reviewer_authority"), "reviewer_authority")
        if (
            authority.get("may_select_semantic_row") is not True
            or authority.get("may_select_value") is not False
            or authority.get("may_change_binding") is not False
            or authority.get("may_execute_formula") is not False
            or authority.get("release_authorized") is not False
            or decision.get("deterministic_materialization_required") is not True
        ):
            raise SemanticBindingCorrectionError("semantic correction authority boundary is invalid")
        if not _text(decision.get("corrected_variable_id")) or not _text(decision.get("reviewed_at")):
            raise SemanticBindingCorrectionError("semantic correction decision is incomplete")

        question_id = int(packet["question_id"])
        stage_id = _text(packet.get("stage_id"))
        role = _text(packet.get("role"))
        _, _, operand = _binding_operand(base_rows, question_id=question_id, stage_id=stage_id, role=role)
        current = _mapping(packet.get("current_semantic_row"), "current_semantic_row")
        if (
            operand.get("row_index") != current.get("row_index")
            or hashlib.sha256(str(operand.get("raw_source_cell") or "").encode("utf-8")).hexdigest()
            != current.get("value_cell_raw_sha256")
        ):
            raise SemanticBindingCorrectionError(f"Q{question_id} base binding drift")
        selected = _mapping(decision.get("selected_semantic_row"), "selected_semantic_row")
        uid = _text(selected.get("internal_table_uid"))
        table = _mapping(tables.get(uid), f"Q{question_id} selected table")
        if selected.get("document_uid") != table.get("document_id") or uid != operand.get("internal_table_uid"):
            raise SemanticBindingCorrectionError(f"Q{question_id} selected source identity drift")
        row_index = selected.get("row_index")
        value_column_index = selected.get("value_column_index")
        label_column_index = selected.get("label_column_index")
        rows = table.get("rows") or []
        if (
            not isinstance(row_index, int)
            or not isinstance(value_column_index, int)
            or not isinstance(label_column_index, int)
            or row_index < 0
            or row_index >= len(rows)
            or not isinstance(rows[row_index], list)
            or max(value_column_index, label_column_index) >= len(rows[row_index])
        ):
            raise SemanticBindingCorrectionError(f"Q{question_id} selected coordinates are invalid")
        row = rows[row_index]
        if (
            canonical_sha256(row) != selected.get("full_row_sha256")
            or hashlib.sha256(str(row[label_column_index]).encode("utf-8")).hexdigest()
            != selected.get("label_raw_text_sha256")
            or hashlib.sha256(str(row[value_column_index]).encode("utf-8")).hexdigest()
            != selected.get("value_cell_raw_sha256")
        ):
            raise SemanticBindingCorrectionError(f"Q{question_id} selected row is stale")
        key = (question_id, stage_id, role)
        if key in result:
            raise SemanticBindingCorrectionError(f"duplicate semantic correction key: {key}")
        result[key] = {
            "packet": dict(packet),
            "decision": dict(decision),
            "row": list(row),
            "table": dict(table),
        }
    if len(result) != int(_mapping(manifest.get("counts"), "manifest.counts").get("correction_count") or 0):
        raise SemanticBindingCorrectionError("semantic correction count drift")
    return result


def apply_semantic_binding_corrections(
    *,
    base_bindings: Path,
    base_bindings_manifest: Path,
    structured_tables: Path,
    packets: Path,
    decisions: Path,
    correction_manifest: Path,
    output: Path,
) -> dict[str, Any]:
    """Materialize approved semantic rows from exact tables, including values."""

    base_meta = _json(base_bindings_manifest)
    if ((_mapping(base_meta.get("outputs"), "base.outputs").get("bindings") or {}).get("sha256")) != sha256_file(base_bindings):
        raise SemanticBindingCorrectionError("base binding manifest is stale")
    corrections = load_semantic_binding_corrections(
        packets=packets,
        decisions=decisions,
        manifest_path=correction_manifest,
        base_bindings=base_bindings,
        structured_tables=structured_tables,
    )
    rows = _rows(base_bindings)
    applied: list[dict[str, Any]] = []
    for key, correction in corrections.items():
        question_id, stage_id, role = key
        _, _, operand = _binding_operand(rows, question_id=question_id, stage_id=stage_id, role=role)
        if not isinstance(operand, dict):
            raise SemanticBindingCorrectionError("binding operand is not mutable")
        decision = correction["decision"]
        selected = _mapping(decision.get("selected_semantic_row"), "selected_semantic_row")
        row = correction["row"]
        table = correction["table"]
        row_index = int(selected["row_index"])
        column_index = int(selected["value_column_index"])
        raw = str(row[column_index])
        parsed_status, decimal_literal, parse_policy = parse_vietnamese_numeric_candidate(raw)
        if parsed_status != "parsed_decimal_candidate" or decimal_literal is None:
            raise SemanticBindingCorrectionError(f"Q{question_id} corrected value cell is not Decimal-parseable")
        provenance_rows = table.get("cell_provenance") or []
        if row_index >= len(provenance_rows) or column_index >= len(provenance_rows[row_index]):
            raise SemanticBindingCorrectionError(f"Q{question_id} corrected cell provenance is unavailable")
        prior_coordinate = {
            "row_index": operand.get("row_index"),
            "column_index": operand.get("column_index"),
            "raw_text_sha256": hashlib.sha256(str(operand.get("raw_source_cell") or "").encode("utf-8")).hexdigest(),
        }
        operand.update(
            {
                "concept_id": decision["corrected_variable_id"],
                "row_index": row_index,
                "column_index": column_index,
                "raw_source_row": row,
                "raw_source_cell": raw,
                "cell_provenance": provenance_rows[row_index][column_index],
                "raw_decimal_candidate": decimal_literal,
                "numeric_parse_policy": parse_policy,
                "semantic_correction": {
                    "protocol": DECISION_PROTOCOL,
                    "correction_packet_sha256": decision["correction_packet_sha256"],
                    "correction_decision_sha256": decision["correction_decision_sha256"],
                    "decision_provenance": decision["decision_provenance"],
                    "prior_coordinate": prior_coordinate,
                    "numeric_value_selected_by_reviewer": False,
                    "value_materialization_method": "deterministic_exact_table_reopen_v1",
                },
            }
        )
        applied.append(
            {
                "question_id": question_id,
                "stage_id": stage_id,
                "role": role,
                "corrected_variable_id": decision["corrected_variable_id"],
                "row_index": row_index,
                "column_index": column_index,
                "correction_decision_sha256": decision["correction_decision_sha256"],
            }
        )

    for question in rows:
        question["schema_version"] = 4
        question["protocol"] = MATERIALIZATION_PROTOCOL
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output, rows)
    status_counts = Counter(
        _text(operand.get("binding_status")) or "unknown"
        for question in rows
        for stage in question.get("stages") or []
        for operand in stage.get("required_operands") or []
        if isinstance(operand, Mapping)
    )
    packet_counts = Counter(_text(row.get("binding_packet_status")) or "unknown" for row in rows)
    manifest = {
        "schema_version": 4,
        "protocol": MATERIALIZATION_PROTOCOL,
        "inputs": {
            "base_bindings": {"path": str(base_bindings), "sha256": sha256_file(base_bindings)},
            "base_bindings_manifest": {"path": str(base_bindings_manifest), "sha256": sha256_file(base_bindings_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "correction_packets": {"path": str(packets), "sha256": sha256_file(packets)},
            "correction_decisions": {"path": str(decisions), "sha256": sha256_file(decisions)},
            "correction_manifest": {"path": str(correction_manifest), "sha256": sha256_file(correction_manifest)},
        },
        "outputs": {"bindings": {"path": str(output), "sha256": sha256_file(output)}},
        "counts": {
            "question_count": len(rows),
            "semantic_correction_count": len(applied),
            "binding_status_counts": dict(sorted(status_counts.items())),
            "binding_packet_status_counts": dict(sorted(packet_counts.items())),
        },
        "applied_corrections": applied,
        "source_contract": {
            **SAFE_SOURCE_CONTRACT,
            "executor_private_numeric_materialization": True,
        },
    }
    manifest_path = output.with_suffix(".manifest.json")
    _write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def effective_approval_from_correction(
    *, base_approval: Mapping[str, Any], correction: Mapping[str, Any]
) -> dict[str, Any]:
    """Derive an effective approval without mutating the original human record."""

    packet = _mapping(correction.get("packet"), "correction.packet")
    decision = _mapping(correction.get("decision"), "correction.decision")
    selected = _mapping(decision.get("selected_semantic_row"), "selected_semantic_row")
    row = correction.get("row") or []
    label_column_index = int(selected["label_column_index"])
    label_raw = str(row[label_column_index])
    result = dict(base_approval)
    result.update(
        {
            "variable_id": decision["corrected_variable_id"],
            "variable_approval_id": decision["correction_decision_sha256"],
            "variable_decision_provenance": dict(
                _mapping(decision.get("decision_provenance"), "decision_provenance")
            ),
            "row_label": {
                "document_uid": selected["document_uid"],
                "internal_table_uid": selected["internal_table_uid"],
                "row_index": selected["row_index"],
                "column_index": label_column_index,
                "raw_text": label_raw,
                "raw_text_sha256": selected["label_raw_text_sha256"],
            },
            "reviewed_at": decision["reviewed_at"],
            "semantic_correction_lineage": {
                "correction_packet_sha256": decision["correction_packet_sha256"],
                "correction_decision_sha256": decision["correction_decision_sha256"],
                "superseded_human_approval_id": base_approval.get("approval_id"),
                "superseded_human_decision_sha256": _mapping(
                    packet.get("superseded_human_decision_lineage"),
                    "superseded_human_decision_lineage",
                ).get("decision_sha256"),
                "numeric_value_selected_by_reviewer": False,
            },
        }
    )
    return result
