from __future__ import annotations

import json
from pathlib import Path

from finance_query.pipeline.context.compiler import compile_blocked_question_packets
from finance_query.pipeline.resolver import resolve_submission_artifacts
from finance_query.pipeline.submission_compiler import compile_submission_package

from test_context_and_feedback import _submission_fixture


def test_resolver_emits_explicit_candidate_and_authority_boundary(tmp_path: Path) -> None:
    submission_dir = _submission_fixture(tmp_path / "submission")
    result = resolve_submission_artifacts(
        submission_dir,
        output_dir=tmp_path / "resolver",
    )

    rows = [
        json.loads(line)
        for line in result.predictions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert result.question_count == 2
    # The proposal's ``answer_decimal`` (10) disagrees with the source cell
    # (1000000).  The Decimal handoff must reject that claim instead of
    # treating the legacy pandas replay as proof.
    assert result.ready_count == 0
    assert result.blocked_count == 2
    assert rows[0]["protocol"] == "vifinqa_resolved_prediction_v1"
    assert rows[0]["resolution_status"] == "BLOCKED"
    assert rows[0]["operand_receipts"][0]["binding_status"] == "BOUND_CANDIDATE"
    assert rows[0]["execution_receipt"]["canonical_decimal_reexecution"] is True
    assert rows[0]["execution_receipt"]["status"] == "REJECTED"
    assert rows[0]["execution_receipt"]["legacy_claim_used_for_execution"] is False
    assert rows[0]["authority_boundary"] == "resolved_candidate_not_authorized_until_e2e"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["migration"]["duplicate_execution_legacy"] is True
    assert manifest["migration"]["canonical_decimal_reexecution"] is True
    assert manifest["migration"]["canonical_decimal_reexecution_status"] == "PARTIAL"


def test_compiler_requires_e2e_for_strict_ready_but_keeps_best_effort_coverage(tmp_path: Path) -> None:
    submission_dir = _submission_fixture(tmp_path / "submission")
    resolved = resolve_submission_artifacts(
        submission_dir,
        output_dir=tmp_path / "resolver",
    )
    compiled = compile_submission_package(
        proposal_dir=submission_dir,
        resolved_predictions_path=resolved.predictions_path,
        e2e_receipts_path=None,
        output_dir=tmp_path / "compiled",
        policy="best_effort",
    )
    assert compiled.archive_path.is_file()
    rows = [
        json.loads(line)
        for line in compiled.ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert all(row["e2e_status"] == "NOT_RUN" for row in rows)
    assert all(row["submission_eligible"] is True for row in rows)
    packets = compile_blocked_question_packets(compiled.output_dir)
    assert packets[0]["resolved_prediction"]
    assert packets[0]["e2e_receipt"] == {}
