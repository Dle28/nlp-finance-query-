from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.staged_formula_materialization_audit import (
    CONTRACT,
    build_staged_formula_materialization_audit,
    validate_staged_formula_materialization_audit,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_staged_formula_audit_is_value_blind_and_not_answer_eligible(tmp_path: Path) -> None:
    base = tmp_path / "base.jsonl"
    _write_jsonl(
        base,
        [
            {
                "question_id": 1,
                "decomposition_status": "abstain",
                "operands": [],
                "operation_ast": {"op": "abstain"},
            },
            {
                "question_id": 2,
                "decomposition_status": "abstain",
                "operands": [],
                "operation_ast": {"op": "abstain"},
            },
        ],
    )
    base_manifest = tmp_path / "base.manifest.json"
    _write_json(base_manifest, {"sidecar_sha256": _sha(base)})
    review_items = tmp_path / "review_items.jsonl"
    _write_jsonl(
        review_items,
        [
            {
                "id": 1,
                "question": "Năm 2019, trong nhóm BSR, PLX và PVT, công ty có hệ số nợ phải trả trên vốn chủ sở hữu cao nhất có hệ số khả năng thanh toán lãi vay là bao nhiêu lần?",
                "question_plan": {
                    "family": "multi_entity_or_period_aggregation",
                    "tickers": ["BSR", "PLX", "PVT"],
                    "years": [2019],
                    "operands": [],
                },
            },
            {
                "id": 2,
                "question": "Doanh thu của AAA năm 2024 là bao nhiêu?",
                "question_plan": {
                    "family": "direct_lookup",
                    "tickers": ["AAA"],
                    "years": [2024],
                    "operands": [],
                },
            },
        ],
    )
    aliases = tmp_path / "aliases.jsonl"
    _write_jsonl(aliases, [])

    artifact = tmp_path / "audit"
    summary = build_staged_formula_materialization_audit(
        base_plans_path=base,
        base_plans_manifest_path=base_manifest,
        review_items_path=review_items,
        entity_aliases_path=aliases,
        output_dir=artifact,
        expected_question_count=2,
    )
    rendered = (artifact / "staged_formula_materialization_audit_v1.jsonl").read_text(encoding="utf-8")
    assert summary["new_staged_plan_count"] == 1
    assert "khả năng thanh toán" not in rendered
    assert "human_verified" not in rendered
    target = json.loads((artifact / "staged_formula_materialization_targets_v1.jsonl").read_text(encoding="utf-8"))
    assert target["source_contract"] == CONTRACT
    validated = validate_staged_formula_materialization_audit(artifact, expected_question_count=2)
    assert validated == {
        "status": "PASS",
        "question_count": 2,
        "new_staged_plan_count": 1,
        "answer_eligible": False,
        "submission_eligible": False,
    }
