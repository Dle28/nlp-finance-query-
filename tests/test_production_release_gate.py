from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "artifacts" / "kaggle_runs" / "notebook5554bd790d_v10_20260811" / "vifinqa_review_bundle"
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"

spec = importlib.util.spec_from_file_location(
    "production_release_gate", ROOT / "scripts" / "build_production_release_gate.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "bundle_dir": BUNDLE,
        "production_audit": BUNDLE / "production_independent_audit_candidate_v1.jsonl",
        "typed_plans": BUNDLE / "typed_operand_plans_v1.jsonl",
        "formula_evidence": BUNDLE / "formula_evidence_sets_typed_v1.jsonl",
        "query_program": BUNDLE / "query_program_ast_shadow_v1.jsonl",
        "fingerprint_canaries": BUNDLE / "fingerprint_canaries_v1.jsonl",
        "v5_readiness": V5 / "production_coverage_v5_readiness_v4.json",
        "output": tmp_path / "release_gate.json",
    }


def test_release_gate_hash_binds_full_corpus_and_is_explicitly_blocked(tmp_path: Path) -> None:
    result = mod.build(**_kwargs(tmp_path))

    assert result["release_status"] == "blocked"
    assert result["production_eligible"] is False
    assert result["submission_compilation_allowed"] is False
    assert result["submission_eligible"] is False
    assert result["counts"] == {
        "public_question_count": 1012,
        "independent_audit_passed_count": 55,
        "independent_audit_blocked_count": 957,
        "typed_plan_incomplete_count": 413,
        "formula_evidence_partial_count": 73,
        "query_program_shadow_incomplete_count": 77,
        "fingerprint_canary_blocked_count": 45,
        "production_execution_ledger_count": None,
        "v5_component_blocking": True,
    }
    assert [blocker["code"] for blocker in result["blockers"]] == [
        "COMPONENT_V5_READINESS_BLOCKED",
        "FULL_CORPUS_INDEPENDENT_AUDIT_INCOMPLETE",
        "TYPED_PLAN_COVERAGE_INCOMPLETE",
        "FORMULA_EVIDENCE_PARTIAL",
        "QUERY_PROGRAM_SHADOW_INCOMPLETE",
        "FINGERPRINT_CANARY_EXACT_BINDING_INCOMPLETE",
        "PRODUCTION_EXECUTION_LEDGER_MISSING",
    ]
    assert result["source_contract"] == {
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }


def test_release_gate_rejects_partial_ledger_pair(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    kwargs["execution_ledger"] = tmp_path / "ledger.jsonl"
    with pytest.raises(ValueError, match="supplied together"):
        mod.build(**kwargs)


def test_release_gate_refuses_to_overwrite(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    kwargs["output"].write_text("pre-existing", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        mod.build(**kwargs)


def test_release_gate_can_be_ready_only_after_full_audit_and_eligible_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A V5 research checkpoint cannot permanently deadlock a stronger release."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in (
        "review_items.jsonl",
        "tables.jsonl",
        "tables_structured_v2.jsonl",
        "tables_evidence_context_v3.jsonl",
    ):
        (bundle / name).write_text('{"id":1}\n', encoding="utf-8")
    artifact = tmp_path / "artifact.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "ledger.manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "ready-gate.json"

    monkeypatch.setattr(
        mod,
        "validate_typed_plans",
        lambda *_args: {"artifact": {"sha256": "a" * 64}, "manifest": {"sha256": "b" * 64}, "incomplete_count": 0, "incomplete_counts": {"abstain": 0, "typed_non_executable": 0}, "status_counts": {"complete": 1}},
    )
    monkeypatch.setattr(
        mod,
        "validate_formula_evidence",
        lambda *_args: {"artifact": {"sha256": "c" * 64}, "manifest": {"sha256": "d" * 64}, "partial_count": 0, "completeness_counts": {"complete": 1}},
    )
    monkeypatch.setattr(
        mod,
        "validate_query_program",
        lambda *_args: {"artifact": {"sha256": "e" * 64}, "manifest": {"sha256": "f" * 64}, "incomplete_count": 0, "shadow_execution_counts": {"shadow_complete": 1}},
    )
    monkeypatch.setattr(
        mod,
        "validate_canaries",
        lambda *_args: {"artifact": {"sha256": "1" * 64}, "manifest": {"sha256": "2" * 64}, "blocked_count": 0, "verdict_counts": {"exact_bound": 1}},
    )
    monkeypatch.setattr(
        mod,
        "validate_audit",
        lambda *_args: {"artifact": {"sha256": "3" * 64}, "manifest": {"sha256": "4" * 64}, "blocked_count": 0, "approved": True, "status_counts": {"passed": 1}},
    )
    monkeypatch.setattr(
        mod,
        "validate_v5_readiness",
        lambda _path: {"path": "v5-research-checkpoint.json", "sha256": "5" * 64, "blocker_codes": ["CRITIC_INDEPENDENT_REVIEW_PENDING"], "counts": {}},
    )
    monkeypatch.setattr(
        mod,
        "validate_ledger",
        lambda *_args: {"artifact": {"sha256": "6" * 64}, "manifest": {"sha256": "7" * 64}, "record_count": 1},
    )

    result = mod.build(
        bundle_dir=bundle,
        production_audit=artifact,
        typed_plans=artifact,
        formula_evidence=artifact,
        query_program=artifact,
        fingerprint_canaries=artifact,
        v5_readiness=artifact,
        execution_ledger=artifact,
        execution_ledger_manifest=manifest,
        output=output,
    )

    assert result["release_status"] == "ready_for_submission_compiler"
    assert result["production_eligible"] is True
    assert result["blockers"] == []
    assert result["counts"]["v5_component_blocking"] is False
    assert result["inputs"]["v5_readiness"]["blocker_codes"] == ["CRITIC_INDEPENDENT_REVIEW_PENDING"]
