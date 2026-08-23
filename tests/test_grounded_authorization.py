from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest
import yaml

from finance_query.grounded_authorization import (
    GroundedAuthorizationError,
    _replace_formula_roles,
    materialize_authorization_replay,
)
from finance_query.semantic_approvals import SEMANTIC_DECISION_PROTOCOL, build_semantic_review_queue


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(value, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def _inputs(root: Path) -> dict[str, Path]:
    table = {
        "document_id": "VJC_2024_separate",
        "internal_table_uid": "table-1",
        "source_provenance": {"source_sha256": "a" * 64, "table_sha256": "b" * 64},
        "rows": [["Chỉ tiêu", "Năm 2024 (VND)"], ["Doanh thu", "1234.5"]],
    }
    tables = root / "tables.jsonl"
    _write_jsonl(tables, [table])
    evidence_context = root / "evidence_context.jsonl"
    _write_jsonl(
        evidence_context,
        [
            {
                "document_id": "VJC_2024_separate",
                "internal_table_uid": "table-1",
                "source_provenance": {"source_sha256": "a" * 64, "table_sha256": "b" * 64},
                "grid": {"rectangular": True, "provenance_complete": True},
                "quality": {"status": "review_ready"},
                "canonical_headers": {
                    "columns": [
                        {
                            "column_index": 1,
                            "source_label": "Năm 2024 (VND)",
                            "header_source_cells": [{"row_index": 0, "column_index": 1}],
                            "period_labels": ["2024"],
                            "unit_labels": ["VND"],
                        }
                    ]
                },
                "table_function": {"kind": "income_statement"},
                "context_trace": {
                    "source_title": "Báo cáo kết quả hoạt động kinh doanh riêng cho năm kết thúc ngày 31/12/2024"
                },
            }
        ],
    )
    evidence_context_manifest = root / "evidence_context.manifest.json"
    evidence_context_manifest.write_text(
        json.dumps(
            {
                "sidecar_sha256": _sha(evidence_context),
                "input_structure_sha256": _sha(tables),
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "question_id": 1,
        "question_context": {"entities": ["VJC"], "scope": "separate", "years": [2024]},
        "requested_output_unit": {"kind": "currency", "unit": "vnd", "vnd_to_output_divisor": "1"},
        "stages": [
            {
                "stage_id": "reported-value",
                "metric_id": None,
                "required_operands": [
                    {
                        "role": "reported_value",
                        "binding_status": "binding_ready",
                        "internal_table_uid": "table-1",
                        "row_index": 1,
                        "column_index": 1,
                        "source_unit": "vnd",
                        "source_to_vnd_multiplier": "1",
                        "requested_output_unit": {"kind": "currency", "unit": "vnd"},
                        "header_source_cells": [{"row_index": 0, "column_index": 1}],
                        "source_unit_anchors": [{"row_index": 0, "column_index": 1}],
                    }
                ],
            }
        ],
    }
    bindings = root / "bindings.jsonl"
    _write_jsonl(bindings, [binding])
    bindings_manifest = root / "bindings.manifest.json"
    bindings_manifest.write_text(json.dumps({"outputs": {"bindings": {"sha256": _sha(bindings)}}}), encoding="utf-8")
    execution = root / "execution.jsonl"
    _write_jsonl(
        execution,
        [{"question_id": 1, "execution_status": "execution_replay_ready", "stage_traces": [{"stage_id": "reported-value", "status": "execution_replay_ready", "converted_output_decimal": "1234.5"}]}],
    )
    execution_manifest = root / "execution.manifest.json"
    execution_manifest.write_text(json.dumps({"outputs": {"execution": {"sha256": _sha(execution)}}}), encoding="utf-8")
    registry = root / "registry.yaml"
    registry.write_text(yaml.safe_dump({"metrics": []}), encoding="utf-8")
    result = {
        "bindings": bindings,
        "bindings_manifest": bindings_manifest,
        "execution": execution,
        "execution_manifest": execution_manifest,
        "structured_tables": tables,
        "evidence_context": evidence_context,
        "evidence_context_manifest": evidence_context_manifest,
        "metric_registry": registry,
    }
    _rebuild_semantic_queue(result, root, "semantic-review")
    return result


def _rebuild_semantic_queue(inputs: dict[str, Path], root: Path, name: str) -> None:
    review = build_semantic_review_queue(
        bindings=inputs["bindings"],
        bindings_manifest=inputs["bindings_manifest"],
        structured_tables=inputs["structured_tables"],
        evidence_context=inputs["evidence_context"],
        evidence_context_manifest=inputs["evidence_context_manifest"],
        output_dir=root / name,
    )
    inputs["semantic_review_queue"] = Path(review["outputs"]["queue"]["path"])
    inputs["semantic_review_manifest"] = Path(review["manifest_path"])
    inputs["semantic_human_decisions"] = Path(review["outputs"]["blank_decisions"]["path"])


def test_full_corpus_authorization_resolves_source_backed_period_but_still_abstains() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        evidence = root / "evidence.jsonl"
        certificates = root / "certificates.jsonl"
        result = materialize_authorization_replay(
            **inputs,
            evidence_bindings_output=evidence,
            answer_certificates_output=certificates,
        )

        evidence_row = json.loads(evidence.read_text(encoding="utf-8"))
        binding = evidence_row["evidence_binding"]
        assert binding["binding_status"] == "BLOCKED"
        assert binding["field_statuses"]["source_integrity_status"] == "PASS"
        assert binding["field_statuses"]["unit_status"] == "PASS"
        assert binding["field_statuses"]["variable_status"] == "UNRESOLVED"
        assert binding["field_statuses"]["period_status"] == "PASS"
        assert binding["field_statuses"]["scope_status"] == "PASS"
        assert binding["period_binding"]["period_grain"] == "fiscal_year"
        assert binding["period_binding"]["start_date"] == "2024-01-01"
        assert binding["period_binding"]["end_date"] == "2024-12-31"
        assert binding["binding_lineage"]["document_sha256"] == "a" * 64
        assert binding["binding_lineage"]["table_sha256"] == "b" * 64

        certificate = json.loads(certificates.read_text(encoding="utf-8"))["answer_certificate"]
        assert certificate["status"] == "ABSTAIN"
        assert certificate["training_eligible"] is False
        assert certificate["promotion_allowed"] is False
        assert result["counts"]["answer_certificate_status_counts"] == {"ABSTAIN": 1}
        readiness = json.loads(Path(result["readiness_path"]).read_text(encoding="utf-8"))
        assert readiness["authorization_status"] == "blocked"
        assert readiness["release_authorized"] is False


def test_recovered_current_period_header_is_revalidated_against_source_title() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        table = json.loads(inputs["structured_tables"].read_text(encoding="utf-8"))
        table["rows"][0][1] = "Năm nay"
        _write_jsonl(inputs["structured_tables"], [table])
        context = json.loads(inputs["evidence_context"].read_text(encoding="utf-8"))
        context["canonical_headers"]["columns"][0]["source_label"] = "Năm nay"
        context["canonical_headers"]["columns"][0]["period_labels"] = ["Năm nay"]
        context["context_trace"]["source_title"] = (
            "Báo cáo kết quả hoạt động kinh doanh riêng cho năm tài chính kết thúc ngày 31/12/2024"
        )
        _write_jsonl(inputs["evidence_context"], [context])
        inputs["evidence_context_manifest"].write_text(
            json.dumps({
                "sidecar_sha256": _sha(inputs["evidence_context"]),
                "input_structure_sha256": _sha(inputs["structured_tables"]),
            }),
            encoding="utf-8",
        )
        binding = json.loads(inputs["bindings"].read_text(encoding="utf-8"))
        operand = binding["stages"][0]["required_operands"][0]
        operand["period_labels"] = ["2024"]
        operand["period_resolution_method"] = "v2_exact_source_title_current_header_v1"
        operand["period_source_title_sha256"] = hashlib.sha256(
            context["context_trace"]["source_title"].encode("utf-8")
        ).hexdigest()
        operand["period_source_date"] = "2024-12-31"
        _write_jsonl(inputs["bindings"], [binding])
        inputs["bindings_manifest"].write_text(
            json.dumps({"outputs": {"bindings": {"sha256": _sha(inputs["bindings"])}}}),
            encoding="utf-8",
        )
        _rebuild_semantic_queue(inputs, root, "semantic-review-recovered-period")
        evidence = root / "evidence-recovered.jsonl"
        materialize_authorization_replay(
            **inputs,
            evidence_bindings_output=evidence,
            answer_certificates_output=root / "certificates-recovered.jsonl",
        )
        period = json.loads(evidence.read_text(encoding="utf-8"))["evidence_binding"]["period_binding"]
        assert period["status"] == "PASS"
        assert period["recognition_method"] == "v3_exact_source_title_current_duration_header_v1"

        binding["stages"][0]["required_operands"][0]["period_source_title_sha256"] = "0" * 64
        _write_jsonl(inputs["bindings"], [binding])
        inputs["bindings_manifest"].write_text(
            json.dumps({"outputs": {"bindings": {"sha256": _sha(inputs["bindings"])}}}),
            encoding="utf-8",
        )
        _rebuild_semantic_queue(inputs, root, "semantic-review-tampered-period")
        evidence = root / "evidence-tampered.jsonl"
        materialize_authorization_replay(
            **inputs,
            evidence_bindings_output=evidence,
            answer_certificates_output=root / "certificates-tampered.jsonl",
        )
        period = json.loads(evidence.read_text(encoding="utf-8"))["evidence_binding"]["period_binding"]
        assert period["status"] == "UNRESOLVED"
        assert period["reason_codes"] == ["PERIOD_SOURCE_TITLE_HASH_MISMATCH"]


@pytest.mark.parametrize(
    ("header_year", "title", "expected_basis", "expected_method"),
    [
        (
            2017,
            "Báo cáo kết quả hoạt động kinh doanh riêng. Năm tài chính kết thúc vào ngày 31 tháng 12 năm 2017",
            "current",
            "v3_exact_header_and_source_title_duration_v1",
        ),
        (
            2019,
            "Báo cáo kết quả hoạt động kinh doanh riêng cho năm tài chính kết thúc ngày 31/12/2020",
            "prior_year",
            "v3_exact_comparative_header_and_next_report_title_duration_v1",
        ),
    ],
)
def test_exact_duration_header_supports_vietnamese_into_wording_and_one_year_comparative(
    header_year: int, title: str, expected_basis: str, expected_method: str
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        table = json.loads(inputs["structured_tables"].read_text(encoding="utf-8"))
        table["rows"][0][1] = f"Năm {header_year} (VND)"
        _write_jsonl(inputs["structured_tables"], [table])
        context = json.loads(inputs["evidence_context"].read_text(encoding="utf-8"))
        header = context["canonical_headers"]["columns"][0]
        header["source_label"] = f"Năm {header_year} (VND)"
        header["period_labels"] = [str(header_year)]
        context["context_trace"]["source_title"] = title
        _write_jsonl(inputs["evidence_context"], [context])
        inputs["evidence_context_manifest"].write_text(
            json.dumps({
                "sidecar_sha256": _sha(inputs["evidence_context"]),
                "input_structure_sha256": _sha(inputs["structured_tables"]),
            }),
            encoding="utf-8",
        )
        binding = json.loads(inputs["bindings"].read_text(encoding="utf-8"))
        binding["question_context"]["years"] = [header_year]
        _write_jsonl(inputs["bindings"], [binding])
        inputs["bindings_manifest"].write_text(
            json.dumps({"outputs": {"bindings": {"sha256": _sha(inputs["bindings"])}}}),
            encoding="utf-8",
        )
        _rebuild_semantic_queue(inputs, root, f"semantic-review-{header_year}")
        evidence = root / "evidence.jsonl"
        materialize_authorization_replay(
            **inputs,
            evidence_bindings_output=evidence,
            answer_certificates_output=root / "certificates.jsonl",
        )
        period = json.loads(evidence.read_text(encoding="utf-8"))["evidence_binding"]["period_binding"]
        assert period["status"] == "PASS"
        assert period["period_years"] == [header_year]
        assert period["comparative_basis"] == expected_basis
        assert period["recognition_method"] == expected_method
        assert period["end_date"] == f"{header_year}-12-31"


def test_human_semantic_approval_can_materialize_bound_operand() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        queue = json.loads(inputs["semantic_review_queue"].read_text(encoding="utf-8"))
        label = queue["row_label_candidates"][0]
        _write_jsonl(
            inputs["semantic_human_decisions"],
            [
                {
                    "protocol": SEMANTIC_DECISION_PROTOCOL,
                    "queue_item_sha256": queue["queue_item_sha256"],
                    "source_review_queue_sha256": _sha(inputs["semantic_review_queue"]),
                    "decision": "approve",
                    "decision_provenance": {
                        "reviewer_type": "human_verified",
                        "reviewer_id": "reviewer-1",
                    },
                    "reviewed_at": "2026-08-22T00:00:00Z",
                    "notes": "Verified exact row label and issuer scope.",
                    "source_coordinates_checked": True,
                    "approved_variable_id": "reported_value",
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
        evidence = root / "evidence.jsonl"
        result = materialize_authorization_replay(
            **inputs,
            evidence_bindings_output=evidence,
            answer_certificates_output=root / "certificates.jsonl",
        )
        binding = json.loads(evidence.read_text(encoding="utf-8"))["evidence_binding"]
        assert binding["binding_status"] == "BOUND"
        assert binding["field_statuses"]["variable_status"] == "PASS"
        assert binding["field_statuses"]["entity_status"] == "PASS"
        assert result["counts"]["human_semantic_approval_count"] == 1


def test_authorization_refuses_exact_binding_hash_mismatch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        inputs["bindings"].write_text("{}\n", encoding="utf-8")
        with pytest.raises(GroundedAuthorizationError, match="exact bindings"):
            materialize_authorization_replay(
                **inputs,
                evidence_bindings_output=root / "evidence.jsonl",
                answer_certificates_output=root / "certificates.jsonl",
            )


def test_authorization_refuses_stale_v3_lineage() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        inputs["evidence_context_manifest"].write_text(
            json.dumps(
                {
                    "sidecar_sha256": _sha(inputs["evidence_context"]),
                    "input_structure_sha256": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(GroundedAuthorizationError, match="does not derive"):
            materialize_authorization_replay(
                **inputs,
                evidence_bindings_output=root / "evidence.jsonl",
                answer_certificates_output=root / "certificates.jsonl",
            )


def test_ambiguous_v3_duration_remains_unresolved() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        context = json.loads(inputs["evidence_context"].read_text(encoding="utf-8"))
        context["context_trace"]["source_title"] = (
            "Báo cáo riêng cho năm kết thúc ngày 31/12/2024; "
            "tham chiếu kỳ kết thúc ngày 30/06/2024"
        )
        _write_jsonl(inputs["evidence_context"], [context])
        inputs["evidence_context_manifest"].write_text(
            json.dumps(
                {
                    "sidecar_sha256": _sha(inputs["evidence_context"]),
                    "input_structure_sha256": _sha(inputs["structured_tables"]),
                }
            ),
            encoding="utf-8",
        )
        _rebuild_semantic_queue(inputs, root, "semantic-review-ambiguous")
        evidence = root / "evidence.jsonl"
        materialize_authorization_replay(
            **inputs,
            evidence_bindings_output=evidence,
            answer_certificates_output=root / "certificates.jsonl",
        )
        binding = json.loads(evidence.read_text(encoding="utf-8"))["evidence_binding"]
        assert binding["field_statuses"]["period_status"] == "UNRESOLVED"
        assert binding["period_binding"]["reason_codes"] == ["DURATION_END_DATE_NOT_UNIQUE"]


def test_multi_stage_questions_materialize_operand_receipts_before_abstaining() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = _inputs(root)
        question = json.loads(inputs["bindings"].read_text(encoding="utf-8"))
        second_stage = json.loads(json.dumps(question["stages"][0]))
        second_stage["stage_id"] = "reported-value-2"
        question["stages"].append(second_stage)
        _write_jsonl(inputs["bindings"], [question])
        inputs["bindings_manifest"].write_text(
            json.dumps({"outputs": {"bindings": {"sha256": _sha(inputs["bindings"])}}}),
            encoding="utf-8",
        )
        _rebuild_semantic_queue(inputs, root, "semantic-review-multi-stage")
        evidence = root / "evidence.jsonl"
        certificates = root / "certificates.jsonl"
        result = materialize_authorization_replay(
            **inputs,
            evidence_bindings_output=evidence,
            answer_certificates_output=certificates,
        )
        assert len(evidence.read_text(encoding="utf-8").splitlines()) == 2
        certificate = json.loads(certificates.read_text(encoding="utf-8"))["answer_certificate"]
        assert certificate["status"] == "ABSTAIN"
        assert certificate["abstain_reason_codes"] == ["MULTI_STAGE_EXECUTION_GRAPH_NOT_MATERIALIZED"]
        assert result["counts"]["evidence_binding_count"] == 2


def test_formula_plan_replaces_registry_roles_with_immutable_operand_ids() -> None:
    ast = {"op": "divide", "args": ["current_assets", "current_liabilities"]}
    assert _replace_formula_roles(
        ast,
        {"current_assets": "q1:assets", "current_liabilities": "q1:liabilities"},
    ) == {"op": "divide", "args": ["q1:assets", "q1:liabilities"]}
