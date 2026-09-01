from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from finance_query.pipeline import e2e_observer
from finance_query.pipeline.contracts import canonical_sha256
from finance_query.pipeline.resolver import resolve_submission_artifacts
from finance_query.pipeline.submission_compiler import compile_submission_package

from test_context_and_feedback import _submission_fixture


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _canonical_submission(root: Path, *, proposal_answer: str = "10", source_value: str = "10") -> Path:
    submission = _submission_fixture(root / "submission")
    audits = _jsonl(submission / "prediction_audit_ledger_v1.jsonl")
    audits[0]["answer_decimal"] = proposal_answer
    evidence = audits[0]["evidence"]
    assert isinstance(evidence, list)
    assert isinstance(evidence[0], dict)
    evidence[0]["raw_value"] = source_value
    _write_jsonl(submission / "prediction_audit_ledger_v1.jsonl", audits)
    return submission


def _base_config(root: Path) -> Path:
    config = root / "grounded.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "protocol": "vifinqa_grounded_e2e_v1",
                "run_name": "canonical-test-grounded-e2e",
                "paths": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config


def _complete_certificate(resolved: dict[str, object]) -> dict[str, object]:
    execution = resolved["execution_receipt"]
    assert isinstance(execution, dict)
    operand = resolved["operand_receipts"]
    assert isinstance(operand, list)
    assert isinstance(operand[0], dict)
    first_operand = operand[0]
    source = first_operand["source"]
    assert isinstance(source, dict)
    operation_ast_sha = str(execution["operation_ast_sha256"])
    certificate: dict[str, object] = {
        "schema_version": 1,
        "protocol": "vifinqa_answer_certificate_v1",
        "question_id": int(resolved["question_id"]),
        "binding_plan_sha256": "b" * 64,
        "operation_ast_sha256": operation_ast_sha,
        "answer": str(resolved["answer_decimal"]),
        "answer_status": "ANSWER",
        "prediction_status": "AUTHORIZED_ANSWER",
        "answer_available": True,
        "answer_authorized": True,
        "candidate_prediction": None,
        "binding_receipts": [
            {
                "operand_id": first_operand["operand_id"],
                "binding_id": "binding-q1-x0",
                "document_uid": source["document_id"],
                "internal_table_uid": source["internal_table_uid"],
                "source_value_cell": {
                    "row_index": source["row_index"],
                    "column_index": source["column_index"],
                },
                "field_statuses": {
                    "variable_status": "PASS",
                    "period_status": "PASS",
                    "unit_status": "PASS",
                    "entity_status": "PASS",
                    "scope_status": "PASS",
                    "source_integrity_status": "PASS",
                },
            }
        ],
        "execution_receipt": {
            "status": "PASS",
            "answer_decimal": str(resolved["answer_decimal"]),
            "operation_ast_sha256": operation_ast_sha,
        },
        "counterfactual_checks": [
            {
                "dimension": dimension,
                "status": "EXHAUSTED",
                "candidate_count": 1,
                "candidate_set_sha256": "c" * 64,
                "enumeration_complete": True,
                "rejection_code": "NO_ALTERNATIVE_IN_BOUND_SET",
            }
            for dimension in ("period", "scope", "unit")
        ],
        "candidate_universe": "bounded_by_hash_bound_candidate_set",
        "global_uniqueness_proven": False,
        "status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
        "abstain_reason_codes": [],
        "training_eligible": False,
        "promotion_allowed": False,
        "serving_eligible": True,
        "next_gate": "campaign_review_and_release_policy",
    }
    certificate["answer_certificate_id"] = canonical_sha256(
        {key: value for key, value in certificate.items() if key != "answer_certificate_id"}
    )
    return certificate


def _patch_fake_engine(monkeypatch: pytest.MonkeyPatch, certificates: list[dict[str, object]]) -> None:
    def fake_load_inputs(_: Path) -> object:
        return object()

    def fake_run_replay(_: object, *, output_dir: Path) -> dict[str, object]:
        output_dir.mkdir(parents=True)
        _write_jsonl(
            output_dir / "answer_certificates_v1.jsonl",
            certificates,
        )
        (output_dir / "evidence_bindings_v1.jsonl").write_text("", encoding="utf-8")
        (output_dir / "grounded_e2e_run_v1.json").write_text(
            json.dumps({"run_status": "complete_answer_capable"}) + "\n",
            encoding="utf-8",
        )
        return {"run_status": "complete_answer_capable", "mocked": True}

    monkeypatch.setattr(e2e_observer, "load_deterministic_replay_inputs", fake_load_inputs)
    monkeypatch.setattr(e2e_observer, "run_deterministic_replay", fake_run_replay)


def test_canonical_resolver_does_not_use_legacy_answer_or_pandas_as_execution(tmp_path: Path) -> None:
    submission = _canonical_submission(
        tmp_path,
        proposal_answer="999",
        source_value="10",
    )
    result = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    row = _jsonl(result.predictions_path)[0]

    assert row["answer_decimal"] == "999"
    execution = row["execution_receipt"]
    assert isinstance(execution, dict)
    assert execution["answer_decimal"] == "10"
    assert execution["candidate_answer_decimal"] == "999"
    assert execution["legacy_claim_used_for_execution"] is False
    assert execution["status"] == "REJECTED"
    assert row["resolution_status"] == "BLOCKED"
    assert "CANONICAL_RESULT_MISMATCH_PROPOSAL" in row["resolution_failure_reason_codes"]


def test_resolver_does_not_fall_back_to_question_answer_field(tmp_path: Path) -> None:
    submission = _canonical_submission(tmp_path)
    questions = json.loads((submission / "submission.json").read_text(encoding="utf-8"))
    assert isinstance(questions, list)
    questions[0]["answer"] = 999
    (submission / "submission.json").write_text(
        json.dumps(questions, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    audits = _jsonl(submission / "prediction_audit_ledger_v1.jsonl")
    audits[0].pop("answer_decimal")
    _write_jsonl(submission / "prediction_audit_ledger_v1.jsonl", audits)

    result = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    row = _jsonl(result.predictions_path)[0]

    assert row["answer_decimal"] == "10"
    assert row["proposal"]["answer_origin"] == "canonical_decimal_reexecution"
    assert row["answer_decimal"] != "999"  # question fixture contains a gold-like field


def test_canonical_resolver_missing_source_stays_blocked(tmp_path: Path) -> None:
    submission = _submission_fixture(tmp_path / "submission")
    result = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    rows = _jsonl(result.predictions_path)
    missing_source = rows[1]

    assert missing_source["resolution_status"] == "BLOCKED"
    execution = missing_source["execution_receipt"]
    assert isinstance(execution, dict)
    assert execution["canonical_decimal_reexecution"] is False
    assert execution["status"] == "BLOCKED"
    assert execution["answer_decimal"] is None
    assert "legacy_builder_replay_claim_adapter" not in json.dumps(missing_source)


def test_e2e_observer_missing_certificate_stays_non_authorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submission = _canonical_submission(tmp_path)
    resolved = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    _patch_fake_engine(monkeypatch, [])

    result = e2e_observer.run_e2e_observer(
        resolved_predictions_path=resolved.predictions_path,
        base_config=_base_config(tmp_path),
        output_dir=tmp_path / "observer",
    )
    rows = _jsonl(result.receipts_path)
    first = rows[0]

    assert result.verified_count == 0
    assert first["e2e_status"] == "PARTIAL"
    assert first["certificate"]["answer_authorized"] is False
    assert "E2E_CERTIFICATE_MISSING" in first["failure_reason_codes"]


def test_complete_mocked_canonical_receipt_is_strict_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submission = _canonical_submission(tmp_path)
    resolved = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    resolved_rows = _jsonl(resolved.predictions_path)
    certificate = _complete_certificate(resolved_rows[0])
    _patch_fake_engine(monkeypatch, [{"question_id": 1, "answer_certificate": certificate}])

    observed = e2e_observer.run_e2e_observer(
        resolved_predictions_path=resolved.predictions_path,
        base_config=_base_config(tmp_path),
        output_dir=tmp_path / "observer",
    )
    (submission / "data").mkdir()
    compiled = compile_submission_package(
        proposal_dir=submission,
        resolved_predictions_path=resolved.predictions_path,
        e2e_receipts_path=observed.receipts_path,
        output_dir=tmp_path / "compiled",
        policy="strict",
    )
    ledger = _jsonl(compiled.ledger_path)

    assert observed.verified_count == 1
    assert _jsonl(observed.receipts_path)[0]["e2e_status"] == "VERIFIED"
    assert ledger[0]["delivery_status"] == "STRICT_READY"
    assert ledger[0]["answer_authorized"] is True
    assert ledger[1]["delivery_status"] == "BLOCKED_STRICT_POLICY"


def test_e2e_observer_rejects_resolved_prediction_id_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submission = _canonical_submission(tmp_path)
    resolved = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    rows = _jsonl(resolved.predictions_path)
    rows[0]["resolved_prediction_id"] = "0" * 64
    tampered = tmp_path / "tampered.jsonl"
    _write_jsonl(tampered, rows)
    _patch_fake_engine(monkeypatch, [])

    with pytest.raises(ValueError, match="resolved prediction id mismatch"):
        e2e_observer.run_e2e_observer(
            resolved_predictions_path=tampered,
            base_config=_base_config(tmp_path),
            output_dir=tmp_path / "observer",
        )


def test_e2e_observer_rejects_certificate_id_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submission = _canonical_submission(tmp_path)
    resolved = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    certificate = _complete_certificate(_jsonl(resolved.predictions_path)[0])
    certificate["answer_certificate_id"] = "0" * 64
    _patch_fake_engine(monkeypatch, [{"question_id": 1, "answer_certificate": certificate}])

    result = e2e_observer.run_e2e_observer(
        resolved_predictions_path=resolved.predictions_path,
        base_config=_base_config(tmp_path),
        output_dir=tmp_path / "observer",
    )
    first = _jsonl(result.receipts_path)[0]

    assert first["e2e_status"] == "REJECTED"
    assert first["certificate"]["content_hash_valid"] is False
    assert "ANSWER_CERTIFICATE_ID_MISMATCH" in first["failure_reason_codes"]


def test_e2e_observer_rejects_certificate_operand_id_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submission = _canonical_submission(tmp_path)
    resolved = resolve_submission_artifacts(submission, output_dir=tmp_path / "resolver")
    certificate = _complete_certificate(_jsonl(resolved.predictions_path)[0])
    bindings = certificate["binding_receipts"]
    assert isinstance(bindings, list)
    assert isinstance(bindings[0], dict)
    bindings[0]["operand_id"] = "wrong-operand-id"
    certificate["answer_certificate_id"] = canonical_sha256(certificate)
    _patch_fake_engine(monkeypatch, [{"question_id": 1, "answer_certificate": certificate}])

    result = e2e_observer.run_e2e_observer(
        resolved_predictions_path=resolved.predictions_path,
        base_config=_base_config(tmp_path),
        output_dir=tmp_path / "observer",
    )
    first = _jsonl(result.receipts_path)[0]

    assert first["e2e_status"] == "REJECTED"
    assert "E2E_OPERAND_ID_MISMATCH" in first["failure_reason_codes"]
