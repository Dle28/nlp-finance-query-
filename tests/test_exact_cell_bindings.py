from __future__ import annotations

import json
from pathlib import Path

from finance_query.exact_cell_bindings import build_exact_cell_unit_bindings, parse_vietnamese_numeric_candidate


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle"
PERIOD = ROOT / "artifacts/research/period_column_candidates_v1"


def _kwargs(output_dir: Path) -> dict:
    return {"period_packets_path": PERIOD / "period_column_candidate_packets_v1.jsonl", "period_manifest_path": PERIOD / "period_column_candidate_packets_v1.manifest.json", "metric_registry_path": ROOT / "configs/financial_metric_registry_v1.yaml", "structured_tables_path": RUN / "tables_structured_v2.jsonl", "evidence_context_path": RUN / "tables_evidence_context_v3.jsonl", "output_dir": output_dir}


def test_explicit_vietnamese_decimal_policy_has_no_float_guessing() -> None:
    assert parse_vietnamese_numeric_candidate("1.234.567,89") == ("parsed_decimal_candidate", "1234567.89", "VI_GROUPED_DECIMAL_POLICY_V1")
    assert parse_vietnamese_numeric_candidate("(1.000)") == ("parsed_decimal_candidate", "-1000", "VI_GROUPED_DECIMAL_POLICY_V1")
    assert parse_vietnamese_numeric_candidate("-")[0] == "numeric_parse_failure"
    assert parse_vietnamese_numeric_candidate("1,000,000")[0] == "numeric_parse_failure"


def test_binding_output_keeps_exact_coordinates_and_nonpromotable_contract(tmp_path: Path) -> None:
    result = build_exact_cell_unit_bindings(**_kwargs(tmp_path))
    assert result["counts"]["unique_period_input_packets"] == 15
    assert result["counts"]["binding_candidate_count"] == 18
    rows = [json.loads(line) for line in Path(result["outputs"]["bindings"]["path"]).read_text(encoding="utf-8").splitlines()]
    ready = [candidate for row in rows for stage in row["stages"] for operand in stage["required_operands"] for candidate in operand["binding_candidates"]]
    assert ready and all(candidate["raw_source_cell"] == candidate["raw_source_row"][candidate["column_index"]] for candidate in ready)
    assert all(candidate["source_contract"]["evidence_eligible"] is False and candidate["source_contract"]["may_execute_formula"] is False for candidate in ready)
