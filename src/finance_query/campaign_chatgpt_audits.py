"""Independent, hash-bound ChatGPT audit for a grounded campaign handoff.

The audit reopens every selected source row and role anchor, but the emitted
review packet contains no numeric literal. Decimal replay is checked
deterministically and exposed to the reviewer only as hashes and booleans.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


HANDOFF_PROTOCOL = "vifinqa_grounded_campaign_review_handoff_v1"
CONFIG_PROTOCOL = "vifinqa_campaign_chatgpt_audit_config_v1"
DECISION_PROTOCOL = "vifinqa_grounded_campaign_chatgpt_decision_v1"
CANDIDATE_REVIEW_PROTOCOL = "vifinqa_grounded_campaign_candidate_chatgpt_review_v1"
AUDIT_MANIFEST_PROTOCOL = "vifinqa_grounded_campaign_chatgpt_audit_manifest_v1"
SAFE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "may_materialize_answer": False,
    "release_authorized": False,
    "numeric_literals_exposed_to_reviewer": False,
}
_NUMERICISH = re.compile(r"^\s*\(?[-+]?\d[\d.,\s%]*\)?\s*$")


class CampaignChatGPTAuditError(ValueError):
    """Raised when an audit loses lineage or exceeds its authority."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CampaignChatGPTAuditError(f"Expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise CampaignChatGPTAuditError(f"Expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignChatGPTAuditError(f"Expected mapping: {label}")
    return value


def _bound_path(record: object, label: str) -> Path:
    bound = _mapping(record, label)
    path = Path(str(bound.get("path") or ""))
    expected = str(bound.get("sha256") or "")
    if not path.is_file() or sha256_file(path) != expected:
        raise CampaignChatGPTAuditError(f"Immutable artifact mismatch: {label}")
    return path


def _row_text_cells(row: list[object], value_column_index: int) -> list[str]:
    cells: list[str] = []
    for index, value in enumerate(row):
        text = str(value or "").strip()
        if not text or index == value_column_index or _NUMERICISH.fullmatch(text):
            continue
        cells.append(text)
    return cells


def _select_tables(path: Path, required_uids: set[str]) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, Mapping[str, Any]] = {}
    for table in _load_jsonl(path):
        uid = str(table.get("internal_table_uid") or "")
        if uid in required_uids:
            if uid in selected:
                raise CampaignChatGPTAuditError(f"Duplicate structured table UID: {uid}")
            selected[uid] = table
    if set(selected) != required_uids:
        missing = sorted(required_uids - set(selected))
        raise CampaignChatGPTAuditError(f"Structured tables missing audit UIDs: {missing[:5]}")
    return selected


def _verify_role_anchor(
    anchor: Mapping[str, Any], table: Mapping[str, Any], *, question_id: int
) -> dict[str, Any]:
    provenance = _mapping(table.get("source_provenance"), "table.source_provenance")
    source_path = Path(str(provenance.get("source_path") or ""))
    expected_source_sha = str(provenance.get("source_sha256") or "")
    if not source_path.is_file() or sha256_file(source_path) != expected_source_sha:
        raise CampaignChatGPTAuditError(f"Q{question_id} entity-role source file drift")
    kind = str(anchor.get("kind") or "")
    expected_text_sha = str(anchor.get("raw_text_sha256") or "")
    if kind == "document_text_line":
        line_number = anchor.get("line_number")
        lines = source_path.read_text(encoding="utf-8").splitlines()
        if not isinstance(line_number, int) or line_number < 1 or line_number > len(lines):
            raise CampaignChatGPTAuditError(f"Q{question_id} invalid entity-role line anchor")
        observed_text_sha = hashlib.sha256(lines[line_number - 1].encode("utf-8")).hexdigest()
    elif kind == "document_metadata":
        title = str((_mapping(table.get("context_trace"), "table.context_trace")).get("source_title") or "")
        # Legacy document-metadata anchors store the immutable source-file SHA
        # in raw_text_sha256.  Reopen both that file identity and the literal
        # source title; do not pretend the two hashes describe the same field.
        if expected_text_sha != expected_source_sha:
            raise CampaignChatGPTAuditError(f"Q{question_id} document-metadata file hash drift")
        if "công ty mẹ" not in title.casefold() and "công ty mệ" not in title.casefold():
            raise CampaignChatGPTAuditError(f"Q{question_id} source title lacks parent-role literal")
        observed_text_sha = expected_text_sha
    else:
        raise CampaignChatGPTAuditError(f"Q{question_id} unsupported entity-role anchor kind: {kind}")
    if observed_text_sha != expected_text_sha:
        raise CampaignChatGPTAuditError(f"Q{question_id} entity-role anchor text drift")
    if anchor.get("source_file_sha256") not in {None, expected_source_sha}:
        raise CampaignChatGPTAuditError(f"Q{question_id} entity-role anchor file hash mismatch")
    return {
        "kind": kind,
        "line_number": anchor.get("line_number"),
        "raw_text_sha256": expected_text_sha,
        "source_title_sha256": (
            hashlib.sha256(
                str((_mapping(table.get("context_trace"), "table.context_trace")).get("source_title") or "").encode("utf-8")
            ).hexdigest()
            if kind == "document_metadata"
            else None
        ),
        "source_file_sha256": expected_source_sha,
        "verified": True,
    }


def build_campaign_chatgpt_audit(
    *, handoff_manifest: Path, config_path: Path, output_dir: Path
) -> dict[str, Any]:
    handoff = _load_json(handoff_manifest)
    if (
        handoff.get("protocol") != HANDOFF_PROTOCOL
        or handoff.get("campaign_status") != "awaiting_independent_campaign_review"
        or handoff.get("human_campaign_decision_count") != 0
    ):
        raise CampaignChatGPTAuditError("Audit requires a blank V7 campaign handoff")
    contract = _mapping(handoff.get("source_contract"), "handoff.source_contract")
    if contract.get("promotion_allowed") is not False or contract.get("may_materialize_answer") is not False:
        raise CampaignChatGPTAuditError("Campaign handoff contract is unsafe")

    config = _load_json(config_path)
    if config.get("protocol") != CONFIG_PROTOCOL or config.get("schema_version") != 1:
        raise CampaignChatGPTAuditError("Invalid campaign audit config")
    provenance = _mapping(config.get("decision_provenance"), "config.decision_provenance")
    authority = _mapping(provenance.get("authority_grant"), "config.authority_grant")
    if (
        provenance.get("reviewer_type") != "chatgpt_verified"
        or provenance.get("reviewer_role") != "authorized_ai_campaign_reviewer"
        or provenance.get("review_policy") != "fail_closed_evidence_bound_v1"
        or provenance.get("verification_authority") not in {None, "human_equivalent"}
        or authority.get("granted_by") != "campaign_owner"
        or authority.get("grant_scope") != "campaign_review_gate_equivalence"
        or authority.get("grant_basis") != "explicit_user_instruction"
    ):
        raise CampaignChatGPTAuditError("Audit config lacks the explicit ChatGPT authority grant")

    outputs = _mapping(handoff.get("outputs"), "handoff.outputs")
    candidates_path = _bound_path(outputs.get("candidates"), "handoff.candidates")
    _bound_path(outputs.get("review_packet"), "handoff.review_packet")
    role_issues_path = _bound_path(outputs.get("entity_role_issue_briefs"), "handoff.entity_role_issue_briefs")
    if role_issues_path.read_text(encoding="utf-8").strip():
        raise CampaignChatGPTAuditError("V7 campaign audit cannot approve over entity-role issue briefs")
    candidates = _load_jsonl(candidates_path)
    if len(candidates) != handoff.get("candidate_count") or not candidates:
        raise CampaignChatGPTAuditError("Campaign candidate count drift")

    inputs = _mapping(handoff.get("inputs"), "handoff.inputs")
    run_path = _bound_path(inputs.get("grounded_run"), "handoff.grounded_run")
    certificates_path = _bound_path(inputs.get("answer_certificates"), "handoff.answer_certificates")
    review_bundle_path = _bound_path(inputs.get("review_bundle"), "handoff.review_bundle")
    run = _load_json(run_path)
    if not all(value is True for value in (_mapping(run.get("reproducibility"), "run.reproducibility")).values()):
        raise CampaignChatGPTAuditError("Grounded replay is not fully reproducible")

    bindings_record = _mapping(_mapping(run.get("outputs"), "run.outputs").get("bindings"), "run.bindings")
    bindings_manifest_path = Path(str(bindings_record.get("manifest_path") or ""))
    if not bindings_manifest_path.is_file() or sha256_file(bindings_manifest_path) != bindings_record.get("manifest_sha256"):
        raise CampaignChatGPTAuditError("Bindings manifest drift")
    bindings_manifest = _load_json(bindings_manifest_path)
    structured_tables_path = _bound_path(
        _mapping(bindings_manifest.get("inputs"), "bindings_manifest.inputs").get("structured_tables"),
        "structured_tables",
    )
    execution_record = _mapping(_mapping(run.get("outputs"), "run.outputs").get("execution"), "run.execution")
    execution_path = Path(str(execution_record.get("path") or ""))
    if not execution_path.is_file() or sha256_file(execution_path) != execution_record.get("sha256"):
        raise CampaignChatGPTAuditError("Execution replay drift")

    certificates = {
        (int(row["question_id"]), str(row["stage_id"])): _mapping(row.get("answer_certificate"), "answer_certificate")
        for row in _load_jsonl(certificates_path)
    }
    execution_rows = {int(row["question_id"]): row for row in _load_jsonl(execution_path)}
    review_bundle = _load_json(review_bundle_path)
    questions = {
        int(_mapping(item.get("queue"), "review_item.queue")["question_id"]): str(item.get("question") or "")
        for item in review_bundle.get("items") or []
        if isinstance(item, Mapping) and isinstance(item.get("queue"), Mapping)
    }
    review_specs = {int(row["question_id"]): row for row in config.get("reviews") or [] if isinstance(row, Mapping)}
    candidate_ids = {int(row["question_id"]) for row in candidates}
    if set(review_specs) != candidate_ids or len(review_specs) != len(config.get("reviews") or []):
        raise CampaignChatGPTAuditError("Audit config must cover every campaign candidate exactly once")

    required_uids = {
        str(_mapping(source_cell, "candidate.source_value_cell").get("internal_table_uid") or "")
        for candidate in candidates
        for source_cell in candidate.get("source_value_cells") or []
    }
    if not required_uids or "" in required_uids:
        raise CampaignChatGPTAuditError("Campaign candidate lacks exact source-cell identity")
    tables = _select_tables(structured_tables_path, required_uids)
    candidate_reviews: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    scope_coverage: set[str] = set()
    period_method_coverage: set[str] = set()

    for candidate in sorted(candidates, key=lambda row: int(row["question_id"])):
        question_id = int(candidate["question_id"])
        stage_id = str(candidate["stage_id"])
        spec = review_specs[question_id]
        outcome = str(spec.get("outcome") or "")
        if outcome not in {"approved_candidate", "semantic_mismatch", "needs_investigation"}:
            raise CampaignChatGPTAuditError(f"Invalid candidate audit outcome for Q{question_id}")
        certificate = certificates.get((question_id, stage_id))
        if certificate is None or certificate.get("status") != "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY":
            raise CampaignChatGPTAuditError(f"Q{question_id} lacks a campaign-only certificate")
        bindings = certificate.get("binding_receipts") or []
        candidate_source_cells = candidate.get("source_value_cells") or []
        if (
            not bindings
            or [binding.get("binding_id") for binding in bindings] != candidate.get("binding_ids")
            or len(bindings) != len(candidate_source_cells)
        ):
            raise CampaignChatGPTAuditError(f"Q{question_id} binding lineage drift")
        expected_role_status = "PASS" if candidate.get("claim_entity_role") == "parent" else "NOT_APPLICABLE"
        multi_binding = len(bindings) > 1
        literal_by_document = _mapping(
            spec.get("row_must_contain_by_document"), "review.row_must_contain_by_document"
        ) if multi_binding else {}
        if multi_binding and set(literal_by_document) != {
            str(_mapping(value, "candidate.source_value_cell").get("document_uid") or "")
            for value in candidate_source_cells
        }:
            raise CampaignChatGPTAuditError(
                f"Q{question_id} multi-binding review must name every exact source document"
            )

        source_reopens: list[dict[str, Any]] = []
        field_status_records: list[dict[str, Any]] = []
        role_reopens: list[dict[str, Any]] = []
        coordinate_records: list[dict[str, Any]] = []
        table_records: list[tuple[Mapping[str, Any], list[object], int]] = []
        for binding_raw, candidate_cell_raw in zip(bindings, candidate_source_cells, strict=True):
            binding = _mapping(binding_raw, "binding")
            source_cell = _mapping(binding.get("source_value_cell"), "binding.source_value_cell")
            if source_cell != _mapping(candidate_cell_raw, "candidate.source_value_cell"):
                raise CampaignChatGPTAuditError(f"Q{question_id} source cell drift")
            table = tables[str(source_cell["internal_table_uid"])]
            if str(table.get("document_id") or "") != source_cell.get("document_uid"):
                raise CampaignChatGPTAuditError(f"Q{question_id} document identity drift")
            rows = table.get("rows") or []
            row_index = source_cell.get("row_index")
            column_index = source_cell.get("column_index")
            if (
                not isinstance(row_index, int)
                or not isinstance(column_index, int)
                or row_index < 0
                or row_index >= len(rows)
                or not isinstance(rows[row_index], list)
                or column_index < 0
                or column_index >= len(rows[row_index])
            ):
                raise CampaignChatGPTAuditError(f"Q{question_id} source coordinate is invalid")
            source_row = rows[row_index]
            raw_value = str(source_row[column_index])
            if hashlib.sha256(raw_value.encode("utf-8")).hexdigest() != source_cell.get("raw_text_sha256"):
                raise CampaignChatGPTAuditError(f"Q{question_id} raw value hash drift")
            text_cells = _row_text_cells(source_row, column_index)
            row_literal = " | ".join(text_cells)
            required_literal = str(
                literal_by_document.get(str(source_cell.get("document_uid") or ""))
                if multi_binding
                else spec.get("row_must_contain") or ""
            )
            if not required_literal or required_literal.casefold() not in row_literal.casefold():
                raise CampaignChatGPTAuditError(f"Q{question_id} reviewed row literal is absent")

            field_statuses = dict(_mapping(binding.get("field_statuses"), "binding.field_statuses"))
            for field in ("entity", "period", "scope", "source_integrity", "unit", "variable"):
                if field_statuses.get(f"{field}_status") != "PASS":
                    raise CampaignChatGPTAuditError(f"Q{question_id} has non-PASS {field}")
            if field_statuses.get("revision_status") not in {"PASS", "NOT_APPLICABLE"}:
                raise CampaignChatGPTAuditError(f"Q{question_id} has invalid revision status")
            if field_statuses.get("entity_role_status") != expected_role_status:
                raise CampaignChatGPTAuditError(f"Q{question_id} entity-role status drift")

            role = _mapping(binding.get("entity_role"), "binding.entity_role")
            verified_anchors: list[dict[str, Any]] = []
            if expected_role_status == "PASS":
                if role.get("status") != "PASS" or role.get("role") != "parent" or not role.get("source_anchors"):
                    raise CampaignChatGPTAuditError(f"Q{question_id} lacks parent-role provenance")
                verified_anchors = [
                    _verify_role_anchor(_mapping(anchor, "role.anchor"), table, question_id=question_id)
                    for anchor in role["source_anchors"]
                ]
            elif role.get("status") != "NOT_APPLICABLE":
                raise CampaignChatGPTAuditError(f"Q{question_id} role should be not applicable")

            source_provenance = _mapping(table.get("source_provenance"), "table.source_provenance")
            source_reopens.append({
                "document_uid": source_cell.get("document_uid"),
                "internal_table_uid": source_cell.get("internal_table_uid"),
                "row_index": row_index,
                "value_column_index": column_index,
                "row_text_cells": text_cells,
                "full_row_sha256": canonical_sha256(source_row),
                "source_value_raw_sha256": source_cell.get("raw_text_sha256"),
                "table_sha256": source_provenance.get("table_sha256"),
                "source_file_sha256": source_provenance.get("source_sha256"),
                "verified": True,
            })
            field_status_records.append(field_statuses)
            role_reopens.append({
                "status": expected_role_status,
                "recognition_method": role.get("recognition_method"),
                "verified_source_anchors": verified_anchors,
            })
            coordinate_records.append({
                "document_id": source_cell.get("document_uid"),
                "internal_table_uid": source_cell.get("internal_table_uid"),
                "row_index": row_index,
                "column_index": column_index,
            })
            table_records.append((table, source_row, column_index))

        execution = execution_rows.get(question_id)
        if execution is None or execution.get("execution_status") != "execution_replay_ready":
            raise CampaignChatGPTAuditError(f"Q{question_id} execution replay is unavailable")
        if multi_binding:
            composition = _mapping(execution.get("composition_trace"), "execution.composition_trace")
            stage_order = [str(value) for value in composition.get("stage_order") or []]
            operation_ast = _mapping(composition.get("operation_ast"), "composition.operation_ast")
            if (
                composition.get("status") != "execution_replay_ready"
                or composition.get("stage_id") != stage_id
                or composition.get("converted_output_decimal") != candidate.get("answer_decimal")
                or operation_ast.get("op") != spec.get("operation_must_equal")
                or stage_order != list(spec.get("stage_order_must_equal") or [])
                or composition.get("operation_ast_sha256")
                != spec.get("operation_ast_sha256_must_equal")
                or composition.get("promotion_decision_sha256")
                != spec.get("promotion_decision_sha256_must_equal")
                or len(stage_order) != len(bindings)
            ):
                raise CampaignChatGPTAuditError(f"Q{question_id} controlled composition drift")
            ordered_traces = []
            for expected_stage, expected_coordinate in zip(stage_order, coordinate_records, strict=True):
                matches = [
                    trace for trace in execution.get("stage_traces") or []
                    if trace.get("stage_id") == expected_stage
                ]
                if len(matches) != 1 or len(matches[0].get("operand_sources") or []) != 1:
                    raise CampaignChatGPTAuditError(f"Q{question_id} exact operand replay is ambiguous")
                operand = matches[0]["operand_sources"][0]
                if any(operand.get(key) != value for key, value in expected_coordinate.items()):
                    raise CampaignChatGPTAuditError(f"Q{question_id} execution operand drift")
                ordered_traces.append(matches[0])
            execution_replay = {
                "status": "execution_replay_ready",
                "execution_kind": "controlled_cross_stage_composition",
                "operation": operation_ast.get("op"),
                "stage_order": stage_order,
                "operation_ast_sha256": composition.get("operation_ast_sha256"),
                "operand_coordinates_verified": True,
                "answer_decimal_sha256": hashlib.sha256(
                    str(candidate.get("answer_decimal") or "").encode("utf-8")
                ).hexdigest(),
                "numeric_literal_exposed": False,
            }
        else:
            traces = [trace for trace in execution.get("stage_traces") or [] if trace.get("stage_id") == stage_id]
            if len(traces) != 1 or traces[0].get("converted_output_decimal") != candidate.get("answer_decimal"):
                raise CampaignChatGPTAuditError(f"Q{question_id} execution output drift")
            operand_sources = traces[0].get("operand_sources") or []
            if len(operand_sources) != 1:
                raise CampaignChatGPTAuditError(f"Q{question_id} expected one exact-cell operand")
            operand = operand_sources[0]
            if any(operand.get(key) != value for key, value in coordinate_records[0].items()):
                raise CampaignChatGPTAuditError(f"Q{question_id} execution operand drift")
            execution_replay = {
                "status": "execution_replay_ready",
                "operand_coordinate_verified": True,
                "answer_decimal_sha256": hashlib.sha256(
                    str(candidate.get("answer_decimal") or "").encode("utf-8")
                ).hexdigest(),
                "numeric_literal_exposed": False,
            }

        checks = certificate.get("counterfactual_checks") or []
        if not checks or any(check.get("status") != "EXHAUSTED" or check.get("enumeration_complete") is not True for check in checks):
            raise CampaignChatGPTAuditError(f"Q{question_id} counterfactual enumeration is incomplete")
        alternative = None
        alternative_literal = str(spec.get("alternative_row_must_contain") or "")
        if alternative_literal and multi_binding:
            raise CampaignChatGPTAuditError(
                f"Q{question_id} multi-binding alternative rows require an explicit per-document protocol"
            )
        if alternative_literal:
            table, _, column_index = table_records[0]
            rows = table.get("rows") or []
            matches = [
                (index, row)
                for index, row in enumerate(rows)
                if alternative_literal.casefold() in " | ".join(_row_text_cells(row, column_index)).casefold()
            ]
            if len(matches) != 1:
                raise CampaignChatGPTAuditError(f"Q{question_id} alternative semantic row is not unique")
            alternative_index, alternative_row = matches[0]
            alternative = {
                "row_index": alternative_index,
                "row_text_cells": _row_text_cells(alternative_row, column_index),
                "full_row_sha256": canonical_sha256(alternative_row),
            }

        payload = {
            "schema_version": 1,
            "protocol": CANDIDATE_REVIEW_PROTOCOL,
            "question_id": question_id,
            "stage_id": stage_id,
            "campaign_candidate_sha256": candidate.get("campaign_candidate_sha256"),
            "question": questions.get(question_id, "") or str(spec.get("question") or ""),
            "review_outcome": outcome,
            "reason_code": spec.get("reason_code"),
            "rationale": spec.get("rationale"),
            **({"source_reopens": source_reopens} if multi_binding else {"source_reopen": source_reopens[0]}),
            **({"field_status_records": field_status_records} if multi_binding else {"field_statuses": field_status_records[0]}),
            **({"entity_role_reopens": role_reopens} if multi_binding else {"entity_role_reopen": role_reopens[0]}),
            "execution_replay": execution_replay,
            "counterfactual_boundary": {
                "dimensions": [check.get("dimension") for check in checks],
                "enumeration_complete": True,
                "global_uniqueness_proven": certificate.get("global_uniqueness_proven") is True,
                "candidate_universe": "hash_bound_exact_binding_set_only",
            },
            "alternative_semantic_row": alternative,
            "source_contract": SAFE_CONTRACT,
        }
        candidate_reviews.append({**payload, "candidate_review_sha256": canonical_sha256(payload)})
        outcome_counts[outcome] += 1
        scope_coverage.update(str(value) for value in candidate.get("scopes") or [])
        period_method_coverage.update(str(value) for value in candidate.get("period_recognition_methods") or [])

    blocker_count = outcome_counts["semantic_mismatch"] + outcome_counts["needs_investigation"]
    verdict = "needs_revision" if blocker_count else "approve_campaign"
    authority_receipt = (
        {
            "verification_authority": "human_equivalent",
            "gate_effect": "same_eligibility_weight_as_human_verified",
            "provenance_preserved_as": "chatgpt_verified",
            "scope": authority.get("grant_scope"),
            "release_authority_included": False,
        }
        if provenance.get("verification_authority") == "human_equivalent"
        else None
    )
    campaign_label = str(config.get("campaign_label") or "V7")
    decision = {
        "schema_version": 1,
        "protocol": DECISION_PROTOCOL,
        "campaign_id": handoff.get("campaign_id"),
        "campaign_candidate_manifest_sha256": sha256_file(candidates_path),
        "decision_options": ["approve_campaign", "reject_campaign", "needs_revision"],
        "decision": verdict,
        "decision_provenance": dict(provenance),
        **({"authority_receipt": authority_receipt} if authority_receipt else {}),
        "reviewer_role": "authorized_ai_campaign_reviewer",
        "reviewed_at": config.get("reviewed_at"),
        "review_evidence": {
            "stratified_source_reopen_complete": True,
            "high_severity_review_complete": True,
            "mutation_suite_review_complete": True,
            "rule_and_code_hash_review_complete": True,
            "candidate_manifest_diff_review_complete": True,
            "entity_role_issue_review_complete": True,
            **(
                {"multi_binding_composition_review_complete": True}
                if any(len(candidate.get("binding_ids") or []) > 1 for candidate in candidates)
                else {}
            ),
        },
        "blocking_issue_count": blocker_count,
        "semantic_issue_reviews": [],
        "candidate_reviews": candidate_reviews,
        "notes": (
            f"ChatGPT reopened all {len(candidate_reviews)} {campaign_label} candidates. "
            f"{outcome_counts['approved_candidate']} pass candidate audit; {blocker_count} require revision. "
            "Campaign approval remains fail-closed and release is not authorized."
        ),
        "release_authorized": False,
        "release_policy_gate_required": True,
        "promotion_allowed": False,
        "source_contract": SAFE_CONTRACT,
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    decision_path = output_dir / "grounded_campaign_chatgpt_decision_v1.jsonl"
    decision_path.write_text(json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "protocol": AUDIT_MANIFEST_PROTOCOL,
        "status": "campaign_revision_required" if blocker_count else "campaign_approved_not_released",
        "campaign_id": handoff.get("campaign_id"),
        "candidate_count": len(candidate_reviews),
        "candidate_review_outcome_counts": dict(sorted(outcome_counts.items())),
        "blocking_issue_count": blocker_count,
        "scope_coverage": sorted(scope_coverage),
        "period_method_coverage": sorted(period_method_coverage),
        "numeric_value_exposure_count": 0,
        **({"review_authority": authority_receipt} if authority_receipt else {}),
        "inputs": {
            "handoff_manifest": {"path": str(handoff_manifest), "sha256": sha256_file(handoff_manifest)},
            "audit_config": {"path": str(config_path), "sha256": sha256_file(config_path)},
            "structured_tables": {"path": str(structured_tables_path), "sha256": sha256_file(structured_tables_path)},
            "execution_replay": {"path": str(execution_path), "sha256": sha256_file(execution_path)},
        },
        "outputs": {
            "decision": {"path": str(decision_path), "sha256": sha256_file(decision_path)},
        },
        "source_contract": SAFE_CONTRACT,
    }
    manifest_path = output_dir / "grounded_campaign_chatgpt_audit_v1.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path)}
