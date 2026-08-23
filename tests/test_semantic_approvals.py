from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.semantic_approvals import (
    SEMANTIC_CHATGPT_DECISION_PROTOCOL,
    SEMANTIC_DECISION_PROTOCOL,
    SemanticApprovalError,
    build_semantic_review_queue,
    load_human_semantic_approvals,
    load_semantic_approvals,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _inputs(root: Path) -> dict[str, Path]:
    table = {
        "document_id": "VJC_2024_separate",
        "internal_table_uid": "table-1",
        "source_provenance": {"source_sha256": "a" * 64, "table_sha256": "b" * 64},
        "rows": [["Chỉ tiêu", "Năm 2024 VND"], ["Doanh thu", "123"]],
    }
    tables = root / "tables.jsonl"
    _jsonl(tables, [table])
    context = root / "context.jsonl"
    _jsonl(
        context,
        [
            {
                "document_id": table["document_id"],
                "internal_table_uid": table["internal_table_uid"],
                "source_provenance": table["source_provenance"],
                "context_trace": {"source_title": "Công ty VJC - Báo cáo tài chính riêng"},
            }
        ],
    )
    context_manifest = root / "context.manifest.json"
    context_manifest.write_text(
        json.dumps({"sidecar_sha256": _sha(context), "input_structure_sha256": _sha(tables)}),
        encoding="utf-8",
    )
    bindings = root / "bindings.jsonl"
    _jsonl(
        bindings,
        [
            {
                "question_id": 1,
                "question_context": {"entities": ["VJC"], "scope": "separate"},
                "stages": [
                    {
                        "stage_id": "stage-1",
                        "required_operands": [
                            {
                                "role": "gross_revenue",
                                "concept_id": "gross_revenue",
                                "binding_status": "binding_ready",
                                "internal_table_uid": "table-1",
                                "row_index": 1,
                                "column_index": 1,
                            }
                        ],
                    }
                ],
            }
        ],
    )
    binding_manifest = root / "bindings.manifest.json"
    binding_manifest.write_text(
        json.dumps({"outputs": {"bindings": {"sha256": _sha(bindings)}}}), encoding="utf-8"
    )
    return {
        "bindings": bindings,
        "bindings_manifest": binding_manifest,
        "structured_tables": tables,
        "evidence_context": context,
        "evidence_context_manifest": context_manifest,
    }


def test_queue_is_blank_and_human_approval_is_hash_bound(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = build_semantic_review_queue(**inputs, output_dir=tmp_path / "review")
    queue = Path(result["outputs"]["queue"]["path"])
    decisions = Path(result["outputs"]["blank_decisions"]["path"])
    manifest = Path(result["manifest_path"])
    assert result["counts"]["review_item_count"] == 1
    assert result["counts"]["human_decision_count"] == 0
    assert result["counts"]["operand_blocker_count"] == 0
    assert result["counts"]["question_stage_blocker_count"] == 0
    assert load_human_semantic_approvals(
        queue=queue,
        queue_manifest=manifest,
        decisions=decisions,
        bindings=inputs["bindings"],
        structured_tables=inputs["structured_tables"],
        evidence_context=inputs["evidence_context"],
    ) == {}

    item = json.loads(queue.read_text(encoding="utf-8"))
    label = item["row_label_candidates"][0]
    _jsonl(
        decisions,
        [
            {
                "protocol": SEMANTIC_DECISION_PROTOCOL,
                "queue_item_sha256": item["queue_item_sha256"],
                "source_review_queue_sha256": _sha(queue),
                "decision": "approve",
                "decision_provenance": {"reviewer_type": "human_verified", "reviewer_id": "reviewer-1"},
                "reviewed_at": "2026-08-22T00:00:00Z",
                "notes": "Checked issuer, scope and exact row label.",
                "source_coordinates_checked": True,
                "approved_variable_id": "gross_revenue",
                "approved_entity": "VJC",
                "approved_scope": "separate",
                "selected_row_label": {
                    "row_index": label["row_index"],
                    "column_index": label["column_index"],
                    "raw_text_sha256": label["raw_text_sha256"],
                },
            }
        ],
    )
    approvals = load_human_semantic_approvals(
        queue=queue,
        queue_manifest=manifest,
        decisions=decisions,
        bindings=inputs["bindings"],
        structured_tables=inputs["structured_tables"],
        evidence_context=inputs["evidence_context"],
    )
    assert approvals[(1, "stage-1", "gross_revenue")]["variable_id"] == "gross_revenue"
    assert approvals[(1, "stage-1", "gross_revenue")]["entity"] == "VJC"


def test_queue_rejects_machine_provenance_and_stale_inputs(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = build_semantic_review_queue(**inputs, output_dir=tmp_path / "review")
    queue = Path(result["outputs"]["queue"]["path"])
    decisions = Path(result["outputs"]["blank_decisions"]["path"])
    manifest = Path(result["manifest_path"])
    item = json.loads(queue.read_text(encoding="utf-8"))
    label = item["row_label_candidates"][0]
    _jsonl(
        decisions,
        [
            {
                "protocol": SEMANTIC_DECISION_PROTOCOL,
                "queue_item_sha256": item["queue_item_sha256"],
                "source_review_queue_sha256": _sha(queue),
                "decision": "approve",
                "decision_provenance": {"reviewer_type": "machine_provisional", "reviewer_id": "model"},
                "reviewed_at": "2026-08-22T00:00:00Z",
                "notes": "",
                "source_coordinates_checked": True,
                "approved_variable_id": "gross_revenue",
                "approved_entity": "VJC",
                "approved_scope": "separate",
                "selected_row_label": label,
            }
        ],
    )
    with pytest.raises(SemanticApprovalError, match="not authorized"):
        load_human_semantic_approvals(
            queue=queue,
            queue_manifest=manifest,
            decisions=decisions,
            bindings=inputs["bindings"],
            structured_tables=inputs["structured_tables"],
            evidence_context=inputs["evidence_context"],
        )

    inputs["structured_tables"].write_text("{}\n", encoding="utf-8")
    with pytest.raises(SemanticApprovalError, match="stale"):
        load_human_semantic_approvals(
            queue=queue,
            queue_manifest=manifest,
            decisions=decisions,
            bindings=inputs["bindings"],
            structured_tables=inputs["structured_tables"],
            evidence_context=inputs["evidence_context"],
        )


def test_authorized_chatgpt_semantic_approval_has_human_equivalent_gate_weight(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path)
    result = build_semantic_review_queue(**inputs, output_dir=tmp_path / "chatgpt-review")
    queue = Path(result["outputs"]["queue"]["path"])
    decisions = Path(result["outputs"]["blank_decisions"]["path"])
    item = json.loads(queue.read_text(encoding="utf-8"))
    label = item["row_label_candidates"][0]
    provenance = {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": "chatgpt-gpt5-semantic-reviewer-v1",
        "reviewer_role": "authorized_ai_semantic_reviewer",
        "model_family": "GPT-5",
        "review_policy": "fail_closed_evidence_bound_v1",
        "verification_authority": "human_equivalent",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "semantic_binding_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }
    receipt = {
        "verification_authority": "human_equivalent",
        "gate_effect": "same_eligibility_weight_as_human_verified",
        "provenance_preserved_as": "chatgpt_verified",
        "scope": "semantic_binding_review_gate_equivalence",
        "release_authority_included": False,
        "training_authority_included": False,
        "submission_authority_included": False,
        "value_selection_authority_included": False,
        "formula_execution_authority_included": False,
    }
    decision = {
        "protocol": SEMANTIC_CHATGPT_DECISION_PROTOCOL,
        "queue_item_sha256": item["queue_item_sha256"],
        "source_review_queue_sha256": _sha(queue),
        "decision": "approve",
        "decision_provenance": provenance,
        "authority_receipt": receipt,
        "reviewed_at": "2026-08-23T00:00:00Z",
        "notes": "Evidence-only semantic review; numeric value remained hidden.",
        "source_coordinates_checked": True,
        "approved_variable_id": "gross_revenue",
        "approved_entity": "VJC",
        "approved_scope": "separate",
        "selected_row_label": {
            "row_index": label["row_index"],
            "column_index": label["column_index"],
            "raw_text_sha256": label["raw_text_sha256"],
        },
    }
    _jsonl(decisions, [decision])

    approvals = load_semantic_approvals(
        queue=queue,
        queue_manifest=Path(result["manifest_path"]),
        decisions=decisions,
        bindings=inputs["bindings"],
        structured_tables=inputs["structured_tables"],
        evidence_context=inputs["evidence_context"],
    )
    approval = approvals[(1, "stage-1", "gross_revenue")]
    assert approval["decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert approval["verification_authority"] == "human_equivalent"

    decision["authority_receipt"]["value_selection_authority_included"] = True
    _jsonl(decisions, [decision])
    with pytest.raises(SemanticApprovalError, match="authority receipt is invalid"):
        load_semantic_approvals(
            queue=queue,
            queue_manifest=Path(result["manifest_path"]),
            decisions=decisions,
            bindings=inputs["bindings"],
            structured_tables=inputs["structured_tables"],
            evidence_context=inputs["evidence_context"],
        )

def test_chatgpt_role_review_can_bind_exact_document_line(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    source = tmp_path / "source.txt"
    source_text = "Công ty là công ty mẹ có các công ty con."
    source.write_text("Mở đầu\n" + source_text + "\nKết thúc\n", encoding="utf-8")
    source_sha = _sha(source)
    table = json.loads(inputs["structured_tables"].read_text(encoding="utf-8"))
    table["source_provenance"].update({"source_path": str(source), "source_sha256": source_sha})
    _jsonl(inputs["structured_tables"], [table])
    inputs["evidence_context_manifest"].write_text(
        json.dumps(
            {
                "sidecar_sha256": _sha(inputs["evidence_context"]),
                "input_structure_sha256": _sha(inputs["structured_tables"]),
            }
        ),
        encoding="utf-8",
    )
    binding = json.loads(inputs["bindings"].read_text(encoding="utf-8"))
    binding["question_context"]["entity_role"] = "parent"
    _jsonl(inputs["bindings"], [binding])
    inputs["bindings_manifest"].write_text(
        json.dumps({"outputs": {"bindings": {"sha256": _sha(inputs["bindings"])}}}),
        encoding="utf-8",
    )
    result = build_semantic_review_queue(**inputs, output_dir=tmp_path / "line-review")
    queue = Path(result["outputs"]["queue"]["path"])
    item = json.loads(queue.read_text(encoding="utf-8"))
    label = item["row_label_candidates"][0]
    anchor_payload = {
        "document_uid": item["document_uid"],
        "source_file_sha256": source_sha,
        "source_path": "source.txt",
        "line_number": 2,
        "raw_text": source_text,
        "raw_text_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "context_before": "Mở đầu",
        "context_after": "Kết thúc",
    }
    from finance_query.semantic_approvals import canonical_sha256

    anchor = {**anchor_payload, "candidate_sha256": canonical_sha256(anchor_payload)}
    decisions = Path(result["outputs"]["blank_decisions"]["path"])
    decision = {
        "protocol": SEMANTIC_DECISION_PROTOCOL,
        "queue_item_sha256": item["queue_item_sha256"],
        "source_review_queue_sha256": _sha(queue),
        "decision": "approve",
        "decision_provenance": {"reviewer_type": "human_verified", "reviewer_id": "reviewer-1"},
        "reviewed_at": "2026-08-23T00:00:00Z",
        "notes": "Human cell review; ChatGPT exact-line role review.",
        "source_coordinates_checked": True,
        "approved_variable_id": "gross_revenue",
        "approved_entity": "VJC",
        "approved_scope": "separate",
        "approved_entity_role": "parent",
        "entity_role_evidence_checked": True,
        "entity_role_source_anchor": anchor,
        "entity_role_provenance_decision_sha256": "d" * 64,
        "entity_role_decision_provenance": {
            "reviewer_type": "chatgpt_verified",
            "reviewer_id": "chatgpt-gpt5-role-provenance-reviewer-v1",
            "model_family": "GPT-5",
            "review_policy": "fail_closed_evidence_bound_v1",
            "authority_grant": {
                "granted_by": "campaign_owner",
                "grant_scope": "entity_role_review_gate_equivalence",
                "grant_basis": "explicit_user_instruction",
            },
        },
        "selected_row_label": {
            "row_index": label["row_index"],
            "column_index": label["column_index"],
            "raw_text_sha256": label["raw_text_sha256"],
        },
    }
    _jsonl(decisions, [decision])

    approvals = load_human_semantic_approvals(
        queue=queue,
        queue_manifest=Path(result["manifest_path"]),
        decisions=decisions,
        bindings=inputs["bindings"],
        structured_tables=inputs["structured_tables"],
        evidence_context=inputs["evidence_context"],
    )
    evidence = approvals[(1, "stage-1", "gross_revenue")]["entity_role_evidence"]
    assert evidence["source_anchors"] == [
        {
            "kind": "document_text_line",
            "document_uid": "VJC_2024_separate",
            "line_number": 2,
            "source_file_sha256": source_sha,
            "raw_text_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        }
    ]

    source.write_text("Mở đầu\nĐã thay đổi\nKết thúc\n", encoding="utf-8")
    with pytest.raises(SemanticApprovalError, match="unavailable or stale"):
        load_human_semantic_approvals(
            queue=queue,
            queue_manifest=Path(result["manifest_path"]),
            decisions=decisions,
            bindings=inputs["bindings"],
            structured_tables=inputs["structured_tables"],
            evidence_context=inputs["evidence_context"],
        )

def test_human_cell_decision_can_carry_separate_authorized_chatgpt_role_review(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    binding = json.loads(inputs["bindings"].read_text(encoding="utf-8"))
    binding["question_context"]["entity_role"] = "parent"
    _jsonl(inputs["bindings"], [binding])
    inputs["bindings_manifest"].write_text(
        json.dumps({"outputs": {"bindings": {"sha256": _sha(inputs["bindings"])}}}), encoding="utf-8"
    )
    context = json.loads(inputs["evidence_context"].read_text(encoding="utf-8"))
    context["context_trace"]["source_title"] = "CÔNG TY MẸ VJC - BÁO CÁO TÀI CHÍNH RIÊNG"
    _jsonl(inputs["evidence_context"], [context])
    inputs["evidence_context_manifest"].write_text(
        json.dumps(
            {
                "sidecar_sha256": _sha(inputs["evidence_context"]),
                "input_structure_sha256": _sha(inputs["structured_tables"]),
            }
        ),
        encoding="utf-8",
    )
    result = build_semantic_review_queue(**inputs, output_dir=tmp_path / "role-review")
    queue = Path(result["outputs"]["queue"]["path"])
    manifest = Path(result["manifest_path"])
    decisions = Path(result["outputs"]["blank_decisions"]["path"])
    item = json.loads(queue.read_text(encoding="utf-8"))
    label = item["row_label_candidates"][0]
    role_provenance = {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": "chatgpt-gpt5-role-reviewer-v1",
        "model_family": "GPT-5",
        "review_policy": "fail_closed_evidence_bound_v1",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "entity_role_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }
    decision = {
        "protocol": SEMANTIC_DECISION_PROTOCOL,
        "queue_item_sha256": item["queue_item_sha256"],
        "source_review_queue_sha256": _sha(queue),
        "decision": "approve",
        "decision_provenance": {"reviewer_type": "human_verified", "reviewer_id": "reviewer-1"},
        "reviewed_at": "2026-08-23T00:00:00Z",
        "notes": "Human row/cell decision plus separately authorized ChatGPT role review.",
        "source_coordinates_checked": True,
        "approved_variable_id": "gross_revenue",
        "approved_entity": "VJC",
        "approved_scope": "separate",
        "approved_entity_role": "parent",
        "entity_role_evidence_checked": True,
        "entity_role_source_title_sha256": hashlib.sha256(item["source_title"].encode()).hexdigest(),
        "entity_role_decision_provenance": role_provenance,
        "selected_row_label": {
            "row_index": label["row_index"],
            "column_index": label["column_index"],
            "raw_text_sha256": label["raw_text_sha256"],
        },
    }
    _jsonl(decisions, [decision])
    approvals = load_human_semantic_approvals(
        queue=queue,
        queue_manifest=manifest,
        decisions=decisions,
        bindings=inputs["bindings"],
        structured_tables=inputs["structured_tables"],
        evidence_context=inputs["evidence_context"],
    )
    approval = approvals[(1, "stage-1", "gross_revenue")]
    assert approval["entity_role"] == "parent"
    assert approval["entity_role_decision_provenance"]["reviewer_type"] == "chatgpt_verified"

    decision["entity_role_decision_provenance"].pop("authority_grant")
    _jsonl(decisions, [decision])
    with pytest.raises(SemanticApprovalError, match="authority grant"):
        load_human_semantic_approvals(
            queue=queue,
            queue_manifest=manifest,
            decisions=decisions,
            bindings=inputs["bindings"],
            structured_tables=inputs["structured_tables"],
            evidence_context=inputs["evidence_context"],
        )
