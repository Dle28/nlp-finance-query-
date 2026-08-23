"""Exact-source review and deterministic replay for two-entity subtraction.

The model-facing packet contains row/header/role evidence and hashes, never a
financial value.  An explicitly authorized ChatGPT decision may approve those
semantics with provenance distinct from ``human_verified``.  Only the
deterministic materializer reopens numeric cells and executes the subtraction.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .exact_cell_bindings import parse_vietnamese_numeric_candidate
from .execution_sandbox import execute_decimal_ast


PACKET_PROTOCOL = "vifinqa_cross_entity_operand_review_packet_v1"
DECISION_PROTOCOL = "vifinqa_cross_entity_operand_chatgpt_decision_v1"
MATERIALIZATION_PROTOCOL = "vifinqa_cross_entity_subtract_materialization_v1"
ADJUDICATION_PROTOCOL = "vifinqa_cross_entity_operand_dual_chatgpt_adjudication_v1"
AUTHORITY_SCOPE = "exact_source_operand_review_gate_equivalence"

_OUTPUT_UNITS: dict[str, Decimal] = {
    "million_vnd": Decimal("1000000"),
    "billion_vnd": Decimal("1000000000"),
}


class CrossEntityOperandReviewError(ValueError):
    """Raised when exact-source lineage or authority boundaries fail closed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _verify_record_hash(record: Mapping[str, Any], field: str, label: str) -> None:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"schema_version", "protocol", field}
    }
    if record.get(field) != canonical_sha256(payload):
        raise CrossEntityOperandReviewError(f"{label} canonical SHA-256 mismatch")


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise CrossEntityOperandReviewError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise CrossEntityOperandReviewError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


def _selected_tables(path: Path, required_uids: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise CrossEntityOperandReviewError(f"{path}:{line_number} must contain an object")
            uid = str(value.get("internal_table_uid") or "")
            if uid in required_uids:
                if uid in selected:
                    raise CrossEntityOperandReviewError(f"duplicate normalized table UID: {uid}")
                selected[uid] = value
    missing = required_uids - set(selected)
    if missing:
        raise CrossEntityOperandReviewError(f"missing normalized table UIDs: {sorted(missing)}")
    return selected


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _manifest_output_sha(manifest: Mapping[str, Any], name: str) -> str | None:
    outputs = manifest.get("outputs") or {}
    record = outputs.get(name) or outputs.get(Path(name).name) or {}
    return record.get("sha256") if isinstance(record, Mapping) else None


def _require_sha(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise CrossEntityOperandReviewError(f"{label} SHA-256 mismatch")


def _question_config(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if config.get("protocol") != "vifinqa_cross_entity_operand_review_config_v1":
        raise CrossEntityOperandReviewError("unexpected operand review config protocol")
    questions = config.get("questions")
    if not isinstance(questions, list) or not questions:
        raise CrossEntityOperandReviewError("operand review config requires questions")
    ids = [int(value.get("question_id", -1)) for value in questions if isinstance(value, Mapping)]
    if len(ids) != len(questions) or len(ids) != len(set(ids)):
        raise CrossEntityOperandReviewError("operand review config question IDs are invalid")
    return questions


def _line_at(path: Path, line_number: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if line_number < 1 or line_number > len(lines):
        raise CrossEntityOperandReviewError(f"source line is out of range: {path}:{line_number}")
    return lines[line_number - 1]


def _table_cell(table: Mapping[str, Any], row_index: int, column_index: int) -> str:
    rows = (table.get("canonical_grid") or {}).get("rows") or []
    if (
        row_index < 0
        or column_index < 0
        or row_index >= len(rows)
        or not isinstance(rows[row_index], list)
        or column_index >= len(rows[row_index])
    ):
        raise CrossEntityOperandReviewError("exact source coordinate is invalid")
    return str(rows[row_index][column_index])


def _validate_graph_and_plan(
    *, question: Mapping[str, Any], graph: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    question_id = int(question["question_id"])
    if graph.get("decision") != "approve_graph_semantics":
        raise CrossEntityOperandReviewError(f"Q{question_id} graph semantics are not approved")
    graph_contract = graph.get("source_contract") or {}
    if (
        graph_contract.get("graph_review_gate_authorized") is not True
        or graph_contract.get("may_execute_formula") is not False
    ):
        raise CrossEntityOperandReviewError(f"Q{question_id} graph authority is invalid")
    reviewed = graph.get("reviewed_graph") or {}
    nodes = reviewed.get("nodes") or []
    final = next((node for node in nodes if node.get("node_id") == reviewed.get("final_node_id")), None)
    if not isinstance(final, Mapping) or final.get("op") != "subtract" or len(final.get("inputs") or []) != 2:
        raise CrossEntityOperandReviewError(f"Q{question_id} is not an approved binary subtraction")
    if plan.get("decomposition_status") != "complete" or (plan.get("operation_ast") or {}).get("op") != "subtract":
        raise CrossEntityOperandReviewError(f"Q{question_id} typed operand plan is incomplete")
    configured = question.get("operands") or []
    planned = plan.get("operands") or []
    if len(configured) != 2 or len(planned) != 2:
        raise CrossEntityOperandReviewError(f"Q{question_id} requires exactly two operands")
    expected = [(str(value.get("operand_id")), str(value.get("entity"))) for value in configured]
    actual = [(str(value.get("operand_id")), str(value.get("entity"))) for value in planned]
    if expected != actual:
        raise CrossEntityOperandReviewError(f"Q{question_id} operand order/entity mismatch")
    requested_unit = str(question.get("requested_unit") or "")
    if requested_unit not in _OUTPUT_UNITS or plan.get("requested_unit") != requested_unit:
        raise CrossEntityOperandReviewError(f"Q{question_id} requested output unit is unsupported or stale")


def build_review_packets(
    *,
    config_path: Path,
    normalized_tables: Path,
    preprocessing_manifest: Path,
    graph_decisions: Path,
    graph_manifest: Path,
    typed_plans: Path,
    typed_plan_manifest: Path,
    repository_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build numeric-value-free exact operand packets for ChatGPT review."""

    config = _json(config_path)
    questions = _question_config(config)
    preprocessing = _json(preprocessing_manifest)
    expected_tables = ((preprocessing.get("outputs") or {}).get(normalized_tables.name) or {}).get("sha256")
    _require_sha(normalized_tables, expected_tables, "normalized tables")
    graph_meta = _json(graph_manifest)
    _require_sha(graph_decisions, _manifest_output_sha(graph_meta, "decisions"), "graph decisions")
    plan_meta = _json(typed_plan_manifest)
    _require_sha(typed_plans, plan_meta.get("sidecar_sha256"), "typed operand plans")

    graph_by_id = {int(row["question_id"]): row for row in _rows(graph_decisions)}
    plan_by_id = {int(row["question_id"]): row for row in _rows(typed_plans)}
    required_uids = {
        str(operand.get("internal_table_uid") or "")
        for question in questions
        for operand in question.get("operands") or []
    }
    if "" in required_uids:
        raise CrossEntityOperandReviewError("operand config contains a blank table UID")
    tables = _selected_tables(normalized_tables, required_uids)

    packets: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for question in questions:
        question_id = int(question["question_id"])
        graph, plan = graph_by_id.get(question_id), plan_by_id.get(question_id)
        if graph is None or plan is None:
            raise CrossEntityOperandReviewError(f"Q{question_id} graph or typed plan is missing")
        _validate_graph_and_plan(question=question, graph=graph, plan=plan)
        evidence_rows: list[dict[str, Any]] = []
        blockers: list[str] = []
        for operand in question.get("operands") or []:
            uid = str(operand["internal_table_uid"])
            table = tables[uid]
            document = table.get("document") or {}
            expected_identity = {
                "ticker": str(operand["entity"]),
                "scope": str(operand.get("scope") or "separate"),
                "report_year": int(operand["year"]),
            }
            actual_identity = {
                "ticker": str(document.get("ticker") or ""),
                "scope": str(document.get("scope") or ""),
                "report_year": int(document.get("report_year") or 0),
            }
            if expected_identity != actual_identity:
                raise CrossEntityOperandReviewError(f"Q{question_id} source identity mismatch")
            if ((table.get("inside_table_context") or {}).get("table_function") or {}).get("kind") != "income_statement":
                raise CrossEntityOperandReviewError(f"Q{question_id} source table is not an income statement")
            row_index = int(operand["row_index"])
            label_column = int(operand["row_label_column_index"])
            value_column = int(operand["value_column_index"])
            header_row = int(operand["header_row_index"])
            row_label = _table_cell(table, row_index, label_column)
            header_label = _table_cell(table, header_row, value_column)
            if row_label != operand.get("expected_row_label") or header_label != operand.get("expected_header_label"):
                raise CrossEntityOperandReviewError(f"Q{question_id} row/header literal mismatch")
            if str(operand.get("expected_unit")) not in ((table.get("inside_table_context") or {}).get("unit_labels") or []):
                raise CrossEntityOperandReviewError(f"Q{question_id} source unit mismatch")
            raw_value = _table_cell(table, row_index, value_column)
            parse_status, _decimal, _policy = parse_vietnamese_numeric_candidate(raw_value)
            if parse_status != "parsed_decimal_candidate":
                raise CrossEntityOperandReviewError(f"Q{question_id} value cell is not strict Decimal-compatible")

            role_spec = operand.get("entity_role_evidence")
            role_evidence: dict[str, Any] | None = None
            if isinstance(role_spec, Mapping):
                source_path = (repository_root / str(role_spec.get("source_path") or "")).resolve()
                try:
                    source_path.relative_to(repository_root.resolve())
                except ValueError as exc:
                    raise CrossEntityOperandReviewError("role evidence escapes repository root") from exc
                line_number = int(role_spec.get("line_number") or 0)
                line_text = _line_at(source_path, line_number)
                role_evidence = {
                    "source_path": str(source_path.relative_to(repository_root.resolve())),
                    "source_sha256": sha256_file(source_path),
                    "line_number": line_number,
                    "line_text": line_text,
                    "line_sha256": hashlib.sha256(line_text.encode("utf-8")).hexdigest(),
                }
            else:
                blockers.append(f"{operand['entity']}:ENTITY_ROLE_PARENT_UNPROVEN")

            provenance = table.get("source_provenance") or {}
            evidence_rows.append(
                {
                    "operand_id": str(operand["operand_id"]),
                    "stage_id": str(operand["stage_id"]),
                    "entity": str(operand["entity"]),
                    "variable_id": str(operand["variable_id"]),
                    "entity_identity": {
                        "claim": str(operand["entity"]),
                        "source_document_id": str(document.get("document_id") or ""),
                        "status": "candidate_exact_match",
                    },
                    "entity_role": {
                        "claim": "parent",
                        "evidence": role_evidence,
                        "status": "candidate" if role_evidence else "unproven",
                    },
                    "reporting_scope": {"claim": "separate", "source": actual_identity["scope"]},
                    "period": {
                        "requested_year": int(operand["year"]),
                        "source_header": header_label,
                        "resolution_method": str(operand["period_resolution_method"]),
                    },
                    "unit": {"source_unit": str(operand["expected_unit"]), "scale_to_vnd": "1"},
                    "variable": {
                        "claim": str(operand["variable_id"]),
                        "source_row_label": row_label,
                    },
                    "coordinate": {
                        "document_id": str(document.get("document_id") or ""),
                        "internal_table_uid": uid,
                        "row_index": row_index,
                        "row_label_column_index": label_column,
                        "value_column_index": value_column,
                        "header_row_index": header_row,
                    },
                    "lineage": {
                        "source_record_sha256": str(table.get("source_record_sha256") or ""),
                        "document_sha256": str(provenance.get("source_sha256") or ""),
                        "table_sha256": str(provenance.get("table_sha256") or ""),
                        "row_label_sha256": hashlib.sha256(row_label.encode("utf-8")).hexdigest(),
                        "header_label_sha256": hashlib.sha256(header_label.encode("utf-8")).hexdigest(),
                        "value_cell_sha256": hashlib.sha256(raw_value.encode("utf-8")).hexdigest(),
                    },
                }
            )
        status = "reviewable_exact_operand_set" if not blockers else "blocked_missing_entity_role"
        status_counts[status] += 1
        payload = {
            "question_id": question_id,
            "question": question.get("question"),
            "operation": "subtract",
            "requested_unit": str(question["requested_unit"]),
            "operand_order": [str(value["operand_id"]) for value in question.get("operands") or []],
            "packet_status": status,
            "reason_codes": blockers,
            "operand_evidence": evidence_rows,
            "graph_decision_sha256": graph.get("decision_sha256"),
            "typed_plan_fingerprint": plan.get("plan_fingerprint"),
            "source_contract": {
                "review_packet_only": True,
                "numeric_values_exposed_to_reviewer": False,
                "may_select_semantic_row": True,
                "may_select_period_header": True,
                "may_verify_entity_role": True,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        packets.append(
            {
                "schema_version": 1,
                "protocol": PACKET_PROTOCOL,
                **payload,
                "packet_sha256": canonical_sha256(payload),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    packet_path = output_dir / "cross_entity_operand_review_packets_v1.jsonl"
    _write_jsonl(packet_path, packets)
    result = {
        "schema_version": 1,
        "protocol": PACKET_PROTOCOL,
        "status": "review_packets_built_not_materialized",
        "inputs": {
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
            "normalized_tables": {"path": str(normalized_tables), "sha256": sha256_file(normalized_tables)},
            "preprocessing_manifest": {"path": str(preprocessing_manifest), "sha256": sha256_file(preprocessing_manifest)},
            "graph_decisions": {"path": str(graph_decisions), "sha256": sha256_file(graph_decisions)},
            "graph_manifest": {"path": str(graph_manifest), "sha256": sha256_file(graph_manifest)},
            "typed_plans": {"path": str(typed_plans), "sha256": sha256_file(typed_plans)},
            "typed_plan_manifest": {"path": str(typed_plan_manifest), "sha256": sha256_file(typed_plan_manifest)},
        },
        "outputs": {"packets": {"path": str(packet_path), "sha256": sha256_file(packet_path)}},
        "counts": {
            "question_count": len(packets),
            "operand_count": sum(len(row["operand_evidence"]) for row in packets),
            "packet_status_counts": dict(sorted(status_counts.items())),
            "numeric_value_exposure_count": 0,
        },
        "source_contract": {
            "numeric_values_exposed_to_reviewer": False,
            "eligible_for_materialization": False,
            "may_select_value": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "cross_entity_operand_review_packets_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def build_chatgpt_decisions(
    *, packets: Path, packet_manifest: Path, review_spec: Path, output_dir: Path
) -> dict[str, Any]:
    """Apply complete, explicit ChatGPT decisions without numeric authority."""

    manifest = _json(packet_manifest)
    _require_sha(packets, _manifest_output_sha(manifest, "packets"), "operand review packets")
    packet_rows = _rows(packets)
    spec = _json(review_spec)
    if spec.get("protocol") != "vifinqa_cross_entity_operand_chatgpt_review_spec_v1":
        raise CrossEntityOperandReviewError("unexpected ChatGPT review spec protocol")
    reviews = spec.get("reviews") or []
    configured_provenance = spec.get("decision_provenance")
    configured_receipt = spec.get("authority_receipt")
    if configured_provenance is None:
        decision_provenance = {
            "reviewer_type": "chatgpt_verified",
            "reviewer_id": "chatgpt-gpt5-cross-entity-operand-reviewer-v1",
            "model_family": "GPT-5",
            "review_policy": "fail_closed_exact_operand_semantics_v1",
            "authority_grant": {
                "granted_by": "campaign_owner",
                "grant_scope": AUTHORITY_SCOPE,
                "grant_basis": "explicit_user_instruction",
            },
        }
        authority_receipt = None
    else:
        if not isinstance(configured_provenance, Mapping):
            raise CrossEntityOperandReviewError("ChatGPT reviewer provenance must be a mapping")
        decision_provenance = dict(configured_provenance)
        grant = decision_provenance.get("authority_grant") or {}
        if (
            decision_provenance.get("reviewer_type") != "chatgpt_verified"
            or not str(decision_provenance.get("reviewer_id") or "").strip()
            or decision_provenance.get("reviewer_role") not in {
                "authorized_ai_operand_evidence_proposer",
                "authorized_ai_operand_evidence_critic",
            }
            or not str(decision_provenance.get("model_family") or "").strip()
            or decision_provenance.get("review_policy") != "fail_closed_exact_operand_semantics_v2"
            or decision_provenance.get("verification_authority") != "human_equivalent"
            or not isinstance(grant, Mapping)
            or grant.get("granted_by") != "campaign_owner"
            or grant.get("grant_scope") != AUTHORITY_SCOPE
            or grant.get("grant_basis") != "explicit_user_instruction"
        ):
            raise CrossEntityOperandReviewError("ChatGPT operand reviewer authority is invalid")
        if not isinstance(configured_receipt, Mapping) or (
            configured_receipt.get("verification_authority") != "human_equivalent"
            or configured_receipt.get("gate_effect") != "same_eligibility_weight_as_human_verified"
            or configured_receipt.get("provenance_preserved_as") != "chatgpt_verified"
            or configured_receipt.get("scope") != AUTHORITY_SCOPE
            or configured_receipt.get("release_authority_included") is not False
            or configured_receipt.get("training_authority_included") is not False
            or configured_receipt.get("submission_authority_included") is not False
            or configured_receipt.get("value_selection_authority_included") is not False
            or configured_receipt.get("formula_execution_authority_included") is not False
        ):
            raise CrossEntityOperandReviewError("ChatGPT operand authority receipt is invalid")
        authority_receipt = dict(configured_receipt)
    by_id = {int(row["question_id"]): row for row in reviews if isinstance(row, Mapping)}
    if set(by_id) != {int(row["question_id"]) for row in packet_rows} or len(by_id) != len(reviews):
        raise CrossEntityOperandReviewError("ChatGPT review coverage mismatch")
    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for packet in packet_rows:
        question_id = int(packet["question_id"])
        _verify_record_hash(packet, "packet_sha256", f"Q{question_id} packet")
        review = by_id[question_id]
        decision = str(review.get("decision") or "")
        if packet.get("packet_status") == "reviewable_exact_operand_set":
            if decision not in {"approve_exact_operand_set", "reject_exact_operand_set"}:
                raise CrossEntityOperandReviewError(f"Q{question_id} reviewable decision is invalid")
        elif decision != "confirm_source_blocker":
            raise CrossEntityOperandReviewError(f"Q{question_id} blocked packet cannot be approved")
        reason_codes = [str(value) for value in review.get("reason_codes") or []]
        rationale = str(review.get("rationale") or "").strip()
        checks = review.get("semantic_checks")
        expected_checks = {
            "operand_order_matches_question",
            "entity_identity_matches",
            "entity_role_parent_proven",
            "reporting_scope_matches",
            "period_header_matches",
            "unit_matches",
            "variable_row_matches",
        }
        if not rationale or not isinstance(checks, Mapping) or set(checks) != expected_checks:
            raise CrossEntityOperandReviewError(f"Q{question_id} review is incomplete")
        if decision == "approve_exact_operand_set" and any(value is not True for value in checks.values()):
            raise CrossEntityOperandReviewError(f"Q{question_id} approval contains a failed check")
        if decision != "approve_exact_operand_set" and not reason_codes:
            raise CrossEntityOperandReviewError(f"Q{question_id} non-approval requires reason codes")
        counts[decision] += 1
        payload = {
            "question_id": question_id,
            "packet_sha256": packet.get("packet_sha256"),
            "decision": decision,
            "reason_codes": reason_codes,
            "rationale": rationale,
            "semantic_checks": dict(checks),
            "decision_provenance": decision_provenance,
            "authority_receipt": authority_receipt,
            "source_contract": {
                "operand_review_gate_authorized": decision == "approve_exact_operand_set",
                "numeric_values_exposed_to_reviewer": False,
                "eligible_for_deterministic_materialization": decision == "approve_exact_operand_set",
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        decisions.append(
            {
                "schema_version": 1,
                "protocol": DECISION_PROTOCOL,
                **payload,
                "decision_sha256": canonical_sha256(payload),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=False)
    decision_path = output_dir / "cross_entity_operand_chatgpt_decisions_v1.jsonl"
    _write_jsonl(decision_path, decisions)
    result = {
        "schema_version": 1,
        "protocol": DECISION_PROTOCOL,
        "status": "reviewed_not_executed",
        "inputs": {
            "packets": {"path": str(packets), "sha256": sha256_file(packets)},
            "packet_manifest": {"path": str(packet_manifest), "sha256": sha256_file(packet_manifest)},
            "review_spec": {"path": str(review_spec), "sha256": sha256_file(review_spec)},
        },
        "outputs": {"decisions": {"path": str(decision_path), "sha256": sha256_file(decision_path)}},
        "counts": {"decision_count": len(decisions), **dict(sorted(counts.items()))},
        "source_contract": {
            "chatgpt_authority_grant_present": True,
            "numeric_values_exposed_to_reviewer": False,
            "may_select_value": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "cross_entity_operand_chatgpt_decisions_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def reconcile_chatgpt_decisions(
    *,
    packets: Path,
    packet_manifest: Path,
    proposal_decisions: Path,
    proposal_manifest: Path,
    critic_decisions: Path,
    critic_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Reconcile distinct proposer/critic decisions without inventing consensus."""

    packet_meta = _json(packet_manifest)
    proposal_meta = _json(proposal_manifest)
    critic_meta = _json(critic_manifest)
    _require_sha(packets, _manifest_output_sha(packet_meta, "packets"), "operand packets")
    _require_sha(
        proposal_decisions,
        _manifest_output_sha(proposal_meta, "decisions"),
        "proposal decisions",
    )
    _require_sha(
        critic_decisions,
        _manifest_output_sha(critic_meta, "decisions"),
        "critic decisions",
    )
    packet_rows = {int(row["question_id"]): row for row in _rows(packets)}
    proposals = {int(row["question_id"]): row for row in _rows(proposal_decisions)}
    critics = {int(row["question_id"]): row for row in _rows(critic_decisions)}
    if set(packet_rows) != set(proposals) or set(packet_rows) != set(critics):
        raise CrossEntityOperandReviewError("dual ChatGPT review coverage mismatch")

    reconciled: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for question_id, packet in packet_rows.items():
        _verify_record_hash(packet, "packet_sha256", f"Q{question_id} packet")
        proposal, critic = proposals[question_id], critics[question_id]
        _verify_record_hash(proposal, "decision_sha256", f"Q{question_id} proposal")
        _verify_record_hash(critic, "decision_sha256", f"Q{question_id} critic")
        proposal_provenance = proposal.get("decision_provenance") or {}
        critic_provenance = critic.get("decision_provenance") or {}
        if (
            proposal.get("packet_sha256") != packet.get("packet_sha256")
            or critic.get("packet_sha256") != packet.get("packet_sha256")
            or proposal_provenance.get("reviewer_role") != "authorized_ai_operand_evidence_proposer"
            or critic_provenance.get("reviewer_role") != "authorized_ai_operand_evidence_critic"
            or proposal_provenance.get("reviewer_id") == critic_provenance.get("reviewer_id")
        ):
            raise CrossEntityOperandReviewError("dual ChatGPT reviewer independence is invalid")
        agreed = (
            proposal.get("decision") == critic.get("decision")
            and proposal.get("semantic_checks") == critic.get("semantic_checks")
            and proposal.get("decision") == "approve_exact_operand_set"
            and all(value is True for value in (proposal.get("semantic_checks") or {}).values())
        )
        decision = "approve_exact_operand_set" if agreed else "confirm_review_disagreement"
        reason_codes = [] if agreed else ["CHATGPT_PROPOSER_CRITIC_DISAGREEMENT"]
        payload = {
            "question_id": question_id,
            "packet_sha256": packet.get("packet_sha256"),
            "decision": decision,
            "reason_codes": reason_codes,
            "rationale": (
                "Proposer và critic độc lập đồng thuận toàn bộ semantic checks."
                if agreed
                else "Proposer và critic không đồng thuận; giữ fail-closed."
            ),
            "semantic_checks": dict(proposal.get("semantic_checks") or {}) if agreed else {},
            "proposal_decision_sha256": proposal.get("decision_sha256"),
            "critic_decision_sha256": critic.get("decision_sha256"),
            "decision_provenance": {
                "reviewer_type": "chatgpt_verified",
                "reviewer_id": "chatgpt-dual-review-reconciler-v1",
                "reviewer_role": "deterministic_dual_review_reconciler",
                "model_family": "deterministic",
                "review_policy": "require_exact_proposer_critic_consensus_v1",
                "verification_authority": "human_equivalent",
                "authority_grant": {
                    "granted_by": "campaign_owner",
                    "grant_scope": AUTHORITY_SCOPE,
                    "grant_basis": "explicit_user_instruction",
                },
            },
            "authority_receipt": proposal.get("authority_receipt"),
            "source_contract": {
                "operand_review_gate_authorized": agreed,
                "numeric_values_exposed_to_reviewer": False,
                "eligible_for_deterministic_materialization": agreed,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        reconciled.append({
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            **payload,
            "decision_sha256": canonical_sha256(payload),
        })
        counts[decision] += 1
    output_dir.mkdir(parents=True, exist_ok=False)
    output_path = output_dir / "cross_entity_operand_chatgpt_decisions_v1.jsonl"
    _write_jsonl(output_path, reconciled)
    result = {
        "schema_version": 1,
        "protocol": ADJUDICATION_PROTOCOL,
        "status": "dual_chatgpt_reviews_reconciled_not_executed",
        "inputs": {
            "packets": {"path": str(packets), "sha256": sha256_file(packets)},
            "proposal_decisions": {
                "path": str(proposal_decisions), "sha256": sha256_file(proposal_decisions)
            },
            "critic_decisions": {
                "path": str(critic_decisions), "sha256": sha256_file(critic_decisions)
            },
        },
        "outputs": {"decisions": {"path": str(output_path), "sha256": sha256_file(output_path)}},
        "counts": {"decision_count": len(reconciled), **dict(sorted(counts.items()))},
        "source_contract": {
            "human_equivalent_gate_weight": True,
            "provenance_preserved_as": "chatgpt_verified",
            "numeric_values_exposed_to_reviewer": False,
            "may_select_value": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "cross_entity_operand_dual_chatgpt_adjudication_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}


def materialize_and_execute(
    *,
    packets: Path,
    packet_manifest: Path,
    decisions: Path,
    decision_manifest: Path,
    normalized_tables: Path,
    preprocessing_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Reopen approved cells privately and run a sandboxed Decimal subtraction."""

    packet_meta = _json(packet_manifest)
    decision_meta = _json(decision_manifest)
    preprocessing = _json(preprocessing_manifest)
    _require_sha(packets, _manifest_output_sha(packet_meta, "packets"), "operand packets")
    _require_sha(decisions, _manifest_output_sha(decision_meta, "decisions"), "operand decisions")
    expected_tables = ((preprocessing.get("outputs") or {}).get(normalized_tables.name) or {}).get("sha256")
    _require_sha(normalized_tables, expected_tables, "normalized tables")
    packet_rows = _rows(packets)
    decision_by_id = {int(row["question_id"]): row for row in _rows(decisions)}
    required_uids = {
        str(evidence["coordinate"]["internal_table_uid"])
        for packet in packet_rows
        for evidence in packet.get("operand_evidence") or []
        if decision_by_id[int(packet["question_id"])].get("decision") == "approve_exact_operand_set"
    }
    tables = _selected_tables(normalized_tables, required_uids)
    private_registry: list[dict[str, Any]] = []
    public_tokens: list[dict[str, Any]] = []
    private_execution: list[dict[str, Any]] = []
    public_receipts: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    for packet in packet_rows:
        question_id = int(packet["question_id"])
        _verify_record_hash(packet, "packet_sha256", f"Q{question_id} packet")
        decision = decision_by_id.get(question_id)
        if decision is None or decision.get("packet_sha256") != packet.get("packet_sha256"):
            raise CrossEntityOperandReviewError(f"Q{question_id} decision is stale")
        _verify_record_hash(decision, "decision_sha256", f"Q{question_id} decision")
        if decision.get("decision") != "approve_exact_operand_set":
            status = "dependency_blocked"
            statuses[status] += 1
            public_receipts.append(
                {
                    "schema_version": 1,
                    "protocol": MATERIALIZATION_PROTOCOL,
                    "question_id": question_id,
                    "execution_status": status,
                    "reason_codes": list(decision.get("reason_codes") or packet.get("reason_codes") or []),
                    "decision_sha256": decision.get("decision_sha256"),
                    "source_contract": {
                        "research_only": True,
                        "numeric_values_exposed": False,
                        "answer_materialization_allowed": False,
                        "promotion_allowed": False,
                        "release_authorized": False,
                    },
                }
            )
            continue
        contract = decision.get("source_contract") or {}
        if (
            contract.get("operand_review_gate_authorized") is not True
            or contract.get("numeric_values_exposed_to_reviewer") is not False
            or contract.get("may_select_value") is not False
            or contract.get("may_execute_formula") is not False
        ):
            raise CrossEntityOperandReviewError(f"Q{question_id} decision authority is invalid")
        operands: dict[str, Decimal] = {}
        token_ids: list[str] = []
        for evidence in packet.get("operand_evidence") or []:
            coordinate = evidence.get("coordinate") or {}
            lineage = evidence.get("lineage") or {}
            uid = str(coordinate["internal_table_uid"])
            table = tables[uid]
            raw_value = _table_cell(
                table, int(coordinate["row_index"]), int(coordinate["value_column_index"])
            )
            if hashlib.sha256(raw_value.encode("utf-8")).hexdigest() != lineage.get("value_cell_sha256"):
                raise CrossEntityOperandReviewError(f"Q{question_id} value cell hash drift")
            parse_status, decimal_text, policy = parse_vietnamese_numeric_candidate(raw_value)
            if parse_status != "parsed_decimal_candidate" or decimal_text is None:
                raise CrossEntityOperandReviewError(f"Q{question_id} numeric replay failed")
            operand_id = str(evidence["operand_id"])
            operands[operand_id] = Decimal(decimal_text)
            token_payload = {
                "question_id": question_id,
                "operand_id": operand_id,
                "stage_id": evidence.get("stage_id"),
                "coordinate": coordinate,
                "value_cell_sha256": lineage.get("value_cell_sha256"),
            }
            token_id = "CELL_" + canonical_sha256(token_payload)[:32]
            token_ids.append(token_id)
            public_tokens.append(
                {
                    "schema_version": 1,
                    "protocol": MATERIALIZATION_PROTOCOL,
                    "token_id": token_id,
                    **token_payload,
                    "source_contract": {
                        "executor_private": False,
                        "model_prompt_eligible": True,
                        "numeric_value_exposed": False,
                        "may_select_value": False,
                    },
                }
            )
            private_registry.append(
                {
                    "schema_version": 1,
                    "protocol": MATERIALIZATION_PROTOCOL,
                    "token_id": token_id,
                    **token_payload,
                    "raw_decimal_candidate": decimal_text,
                    "numeric_parse_policy": policy,
                    "source_contract": {
                        "executor_private": True,
                        "model_prompt_eligible": False,
                        "numeric_value_exposed": True,
                        "may_select_value": False,
                    },
                }
            )
        if set(operands) != set(packet.get("operand_order") or []):
            raise CrossEntityOperandReviewError(f"Q{question_id} materialized operand set is incomplete")
        requested_unit = str(packet.get("requested_unit") or "")
        output_divisor = _OUTPUT_UNITS.get(requested_unit)
        if output_divisor is None:
            raise CrossEntityOperandReviewError(f"Q{question_id} requested output unit is unsupported")
        sandbox_result = execute_decimal_ast(
            {
                "op": "divide",
                "args": [
                    {"op": "subtract", "args": list(packet["operand_order"])},
                    "output_divisor",
                ],
            },
            {**operands, "output_divisor": output_divisor},
        )
        result_text = format(sandbox_result.value, "f")
        result_sha = hashlib.sha256(result_text.encode("utf-8")).hexdigest()
        status = "execution_replay_ready_research_only"
        statuses[status] += 1
        private_execution.append(
            {
                "schema_version": 1,
                "protocol": MATERIALIZATION_PROTOCOL,
                "question_id": question_id,
                "execution_status": status,
                "operand_token_ids": token_ids,
                "operation": "subtract",
                "output_unit": requested_unit,
                "execution_value_decimal": result_text,
                "telemetry": sandbox_result.telemetry(),
                "source_contract": {
                    "executor_private": True,
                    "research_only": True,
                    "answer_materialization_allowed": False,
                    "promotion_allowed": False,
                    "release_authorized": False,
                },
            }
        )
        public_receipts.append(
            {
                "schema_version": 1,
                "protocol": MATERIALIZATION_PROTOCOL,
                "question_id": question_id,
                "execution_status": status,
                "operand_token_ids": token_ids,
                "operation": "subtract",
                "output_unit": requested_unit,
                "execution_result_sha256": result_sha,
                "telemetry": sandbox_result.telemetry(),
                "decision_sha256": decision.get("decision_sha256"),
                "source_contract": {
                    "research_only": True,
                    "numeric_values_exposed": False,
                    "answer_materialization_allowed": False,
                    "promotion_allowed": False,
                    "release_authorized": False,
                },
            }
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    paths = {
        "executor_registry": output_dir / "cross_entity_executor_registry_v1.jsonl",
        "public_tokens": output_dir / "cross_entity_public_tokens_v1.jsonl",
        "private_execution": output_dir / "cross_entity_private_execution_v1.jsonl",
        "public_receipts": output_dir / "cross_entity_public_execution_receipts_v1.jsonl",
    }
    _write_jsonl(paths["executor_registry"], private_registry)
    _write_jsonl(paths["public_tokens"], public_tokens)
    _write_jsonl(paths["private_execution"], private_execution)
    _write_jsonl(paths["public_receipts"], public_receipts)
    result = {
        "schema_version": 1,
        "protocol": MATERIALIZATION_PROTOCOL,
        "status": "deterministic_replay_completed_research_only",
        "inputs": {
            "packets": {"path": str(packets), "sha256": sha256_file(packets)},
            "packet_manifest": {"path": str(packet_manifest), "sha256": sha256_file(packet_manifest)},
            "decisions": {"path": str(decisions), "sha256": sha256_file(decisions)},
            "decision_manifest": {"path": str(decision_manifest), "sha256": sha256_file(decision_manifest)},
            "normalized_tables": {"path": str(normalized_tables), "sha256": sha256_file(normalized_tables)},
            "preprocessing_manifest": {"path": str(preprocessing_manifest), "sha256": sha256_file(preprocessing_manifest)},
        },
        "outputs": {
            name: {"path": str(path), "sha256": sha256_file(path)} for name, path in paths.items()
        },
        "counts": {
            "question_count": len(packet_rows),
            "token_count": len(public_tokens),
            "private_execution_count": len(private_execution),
            "execution_status_counts": dict(sorted(statuses.items())),
        },
        "source_contract": {
            "research_only": True,
            "reviewer_numeric_value_exposure": False,
            "deterministic_executor_reads_values": True,
            "answer_materialization_allowed": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "cross_entity_subtract_materialization_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
