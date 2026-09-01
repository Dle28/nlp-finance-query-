from __future__ import annotations

import json
from pathlib import Path

import finance_query.pipeline.e2e_observer as e2e_observer
from finance_query.pipeline.flow_audit import audit_submission_flow
from finance_query.pipeline.feedback.blocked import run_blocked_feedback
from finance_query.pipeline.resolver import resolve_submission_artifacts
from finance_query.pipeline.submission_compiler import compile_submission_package

from test_context_and_feedback import _submission_fixture


def _prepare_submission(root: Path) -> Path:
    submission = _submission_fixture(root)
    report_path = submission / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.update(
        {
            "question_count": 2,
            "validation": {
                "valid": True,
                "records": 2,
                "queries_replayed": 2,
                "errors": [],
            },
        }
    )
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")
    data_dir = submission / "data"
    data_dir.mkdir()
    for question_id in (1, 2):
        (data_dir / f"q{question_id:04d}_evidence.csv").write_text(
            "variable,value\ndf1,1\n",
            encoding="utf-8",
        )
    return submission


def test_flow_audit_accepts_one_candidate_only_flow_with_explicit_skip(tmp_path: Path) -> None:
    proposal = _prepare_submission(tmp_path / "proposal")
    resolved = resolve_submission_artifacts(proposal, output_dir=tmp_path / "resolver")
    compiled = compile_submission_package(
        proposal_dir=proposal,
        resolved_predictions_path=resolved.predictions_path,
        e2e_receipts_path=None,
        output_dir=tmp_path / "compiled",
        policy="best_effort",
    )
    feedback = run_blocked_feedback(
        submission_dir=compiled.output_dir,
        output_dir=tmp_path / "feedback",
        prepare_only=True,
    )

    audit = audit_submission_flow(
        proposal_dir=proposal,
        resolver_dir=resolved.output_dir,
        observer_dir=None,
        compile_dir=compiled.output_dir,
        feedback_dir=feedback.output_dir,
        expected_question_count=2,
        require_e2e=False,
    )

    assert audit["gate_passed"] is True
    assert audit["status"] == "PASS"
    assert audit["population"]["observed_question_count"] == 2
    assert audit["checks"]["e2e_observer"]["status"] == "SKIPPED_EXPLICITLY"
    assert audit["authority"]["accuracy_measured"] is False


def test_flow_audit_rejects_missing_required_e2e_stage(tmp_path: Path) -> None:
    proposal = _prepare_submission(tmp_path / "proposal")
    resolved = resolve_submission_artifacts(proposal, output_dir=tmp_path / "resolver")
    compiled = compile_submission_package(
        proposal_dir=proposal,
        resolved_predictions_path=resolved.predictions_path,
        e2e_receipts_path=None,
        output_dir=tmp_path / "compiled",
        policy="best_effort",
    )
    feedback = run_blocked_feedback(
        submission_dir=compiled.output_dir,
        output_dir=tmp_path / "feedback",
        prepare_only=True,
    )

    audit = audit_submission_flow(
        proposal_dir=proposal,
        resolver_dir=resolved.output_dir,
        observer_dir=None,
        compile_dir=compiled.output_dir,
        feedback_dir=feedback.output_dir,
        expected_question_count=2,
        require_e2e=True,
    )

    assert audit["gate_passed"] is False
    assert any("required but no observer output" in error for error in audit["errors"])


def test_flow_audit_accepts_hash_bound_e2e_handoff(tmp_path: Path, monkeypatch) -> None:
    proposal = _prepare_submission(tmp_path / "proposal")
    resolved = resolve_submission_artifacts(proposal, output_dir=tmp_path / "resolver")

    base_config = tmp_path / "grounded.yaml"
    base_config.write_text(
        "schema_version: 1\nprotocol: vifinqa_grounded_e2e_v1\npaths: {}\n",
        encoding="utf-8",
    )

    def fake_load_inputs(_: Path) -> object:
        return object()

    def fake_run_replay(_: object, *, output_dir: Path) -> dict[str, object]:
        output_dir.mkdir(parents=True)
        (output_dir / "answer_certificates_v1.jsonl").write_text("", encoding="utf-8")
        (output_dir / "evidence_bindings_v1.jsonl").write_text("", encoding="utf-8")
        (output_dir / "grounded_e2e_run_v1.json").write_text(
            '{"run_status":"complete_answer_capable"}\n',
            encoding="utf-8",
        )
        return {"run_status": "complete_answer_capable", "mocked": True}

    monkeypatch.setattr(e2e_observer, "load_deterministic_replay_inputs", fake_load_inputs)
    monkeypatch.setattr(e2e_observer, "run_deterministic_replay", fake_run_replay)
    observed = e2e_observer.run_e2e_observer(
        resolved_predictions_path=resolved.predictions_path,
        base_config=base_config,
        output_dir=tmp_path / "observer",
    )
    compiled = compile_submission_package(
        proposal_dir=proposal,
        resolved_predictions_path=resolved.predictions_path,
        e2e_receipts_path=observed.receipts_path,
        output_dir=tmp_path / "compiled",
        policy="best_effort",
    )
    feedback = run_blocked_feedback(
        submission_dir=compiled.output_dir,
        output_dir=tmp_path / "feedback",
        prepare_only=True,
    )

    audit = audit_submission_flow(
        proposal_dir=proposal,
        resolver_dir=resolved.output_dir,
        observer_dir=observed.output_dir,
        compile_dir=compiled.output_dir,
        feedback_dir=feedback.output_dir,
        expected_question_count=2,
        require_e2e=True,
    )

    assert audit["gate_passed"] is True
    assert audit["checks"]["e2e_observer"]["question_count"] == 2
    assert audit["checks"]["submission_compiler"]["zip"]["integrity_test"] is True
