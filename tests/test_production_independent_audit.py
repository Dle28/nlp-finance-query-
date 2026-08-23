from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "production_independent_audit", ROOT / "scripts" / "build_production_independent_audit.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_direct_question_passes_only_with_reviewer_independent_source_critic() -> None:
    row = mod.audit_question(
        {"id": 1},
        {"effective_family": "direct_lookup", "decomposition_status": "complete"},
        None,
        {"status": "independent_ready", "reason_codes": []},
        None,
    )
    assert row["independent_audit_status"] == "passed"
    assert row["production_eligible"] is True


def test_formula_partial_stays_blocked_even_if_a_template_exists() -> None:
    row = mod.audit_question(
        {"id": 2},
        {"effective_family": "ratio_or_derived", "decomposition_status": "complete"},
        {
            "evidence_completeness": "partial",
            "reason_codes": [],
            "missing_operand_ids": ["denominator"],
        },
        None,
        None,
    )
    assert row["independent_audit_status"] == "blocked"
    assert "missing_operand:denominator" in row["reason_codes"]


def test_typed_abstain_never_becomes_production_eligible() -> None:
    row = mod.audit_question(
        {"id": 3},
        {
            "effective_family": "conditional_analytical",
            "decomposition_status": "abstain",
            "reason_codes": ["UNKNOWN_OPERAND_STRUCTURE"],
        },
        None,
        None,
        None,
    )
    assert row["production_eligible"] is False
    assert row["reason_codes"] == ["UNKNOWN_OPERAND_STRUCTURE"]
