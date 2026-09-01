from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "scripts/research/run_semantic_contract_audit_v1.py"
COMPARATOR_PATH = ROOT / "scripts/research/compare_semantic_contract_audits_v1.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _load_module("feedback_audit_contract_under_test", AUDIT_PATH)
COMPARATOR = _load_module("feedback_comparator_contract_under_test", COMPARATOR_PATH)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fake_builder(path: Path) -> None:
    # This is an audit-only test double.  Its returned selection deliberately
    # contains sentinel values to prove the feedback harness does not emit
    # them.  The real answer-selection code is never modified by this test.
    path.write_text(
        """
def normalize_structured_table(raw):
    return raw


def enrich_review_items_with_registry_identity(items):
    return items, {"identity_planner_question_count": len(items)}


def rank_semantic_cells(item, *, tables_by_uid, allow_uncertain, rejection_log):
    assert allow_uncertain is True
    assert "model_answer" not in item
    assert "research_answer" not in item
    assert "gold_answer" not in item
    for candidate in item.get("candidates") or []:
        assert "candidate_answer_decimal" not in candidate
        assert "model_answer" not in candidate
        assert "research_answer" not in candidate
    if int(item["id"]) == 1:
        rejection_log.extend(
            [
                {
                    "stage": "semantic_contract",
                    "internal_table_uid": "u1",
                    "document_id": "AAA_financial_statements_2024_separate",
                    "candidate_rank": 1,
                    "row_index": 2,
                    "column_index": 1,
                    "reason_codes": ["ROW_METRIC_CONFLICT"],
                },
                {
                    "stage": "evidence_window",
                    "internal_table_uid": "u2",
                    "document_id": "AAA_financial_statements_2024_separate",
                    "candidate_rank": 2,
                    "row_index": None,
                    "column_index": None,
                    "reason_codes": ["NO_EVIDENCE_WINDOW"],
                },
            ]
        )
        return []
    return [
        {
            "internal_table_uid": "u1",
            "row_index": 3,
            "column_index": 2,
            "value": "ANSWER_SENTINEL",
            "raw_value": "RAW_VALUE_SENTINEL",
        }
    ]
""",
        encoding="utf-8",
    )


def _audit_fixture(tmp_path: Path) -> tuple[argparse.Namespace, Path]:
    builder_snapshot = tmp_path / "snapshot" / "scripts" / "e2e" / "builder.py"
    builder_snapshot.parent.mkdir(parents=True)
    _write_fake_builder(builder_snapshot)

    independent = tmp_path / "independent.jsonl"
    _write_jsonl(
        independent,
        [
            {
                "question_id": 1,
                "question": "Chỉ tiêu A năm 2024 là bao nhiêu?",
                "review_status": "MISMATCH",
                "candidate_answer_decimal": "BASELINE_SENTINEL",
                "source": {"internal_table_uid": "u-old", "row_index": 1, "column_index": 1},
            },
            {
                "question_id": 2,
                "question": "Tỷ lệ B năm 2024 là bao nhiêu?",
                "review_status": "CONSISTENT_CANDIDATE",
                "candidate_answer_decimal": "BASELINE_RATIO_SENTINEL",
                "source": {"internal_table_uid": "u1", "row_index": 3, "column_index": 2},
            },
        ],
    )

    review_items = tmp_path / "review_items.jsonl"
    _write_jsonl(
        review_items,
        [
            {
                "id": 1,
                "question": "Chỉ tiêu A năm 2024 là bao nhiêu?",
                "question_plan": {
                    "family": "direct_lookup",
                    "years": [2024],
                    "scope": "separate",
                    "requested_unit": "million_vnd",
                    "model_answer": "MODEL_SENTINEL",
                    "research_answer": "RESEARCH_SENTINEL",
                },
                "candidates": [
                    {
                        "rank": 1,
                        "internal_table_uid": "u1",
                        "document_id": "AAA_financial_statements_2024_separate",
                        "evidence_window": [{"index": 2, "row": ["A", "SOURCE_SENTINEL"]}],
                        "candidate_answer_decimal": "CANDIDATE_SENTINEL",
                        "model_answer": "MODEL_CANDIDATE_SENTINEL",
                        "research_answer": "RESEARCH_CANDIDATE_SENTINEL",
                    },
                    {
                        "rank": 2,
                        "internal_table_uid": "u2",
                        "document_id": "AAA_financial_statements_2024_separate",
                        "evidence_window": [],
                    },
                ],
            },
            {
                "id": 2,
                "question": "Tỷ lệ B năm 2024 là bao nhiêu?",
                "question_plan": {
                    "family": "ratio_or_derived",
                    "years": [2024],
                    "scope": "separate",
                },
                "candidates": [
                    {
                        "rank": 1,
                        "internal_table_uid": "u1",
                        "document_id": "AAA_financial_statements_2024_separate",
                        "evidence_window": [{"index": 3, "row": ["B", "SOURCE_SENTINEL"]}],
                    }
                ],
            },
        ],
    )

    structured_tables = tmp_path / "tables.jsonl"
    _write_jsonl(
        structured_tables,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_financial_statements_2024_separate",
                "rows": [["header", "million"], ["A", "1"]],
            }
        ],
    )
    output = tmp_path / "audit"
    args = argparse.Namespace(
        builder_path=builder_snapshot,
        independent_review=independent,
        review_items=review_items,
        structured_tables=structured_tables,
        output=output,
    )
    return args, builder_snapshot


def test_audit_separates_guard_from_missing_evidence_and_is_value_free(tmp_path: Path) -> None:
    args, builder_snapshot = _audit_fixture(tmp_path)
    summary = AUDIT.audit(args)

    assert summary["policy"]["value_free_output"] is True
    assert summary["policy"]["accuracy_measured"] is False
    assert summary["policy"]["question_id_exceptions"] is False
    assert summary["policy"]["research_or_model_sidecars_passed_to_selector"] is False
    assert summary["policy"]["abstain_reason_codes_complete"] is True
    assert summary["builder_snapshot"]["path"] == str(builder_snapshot.resolve())
    assert summary["builder_snapshot"]["sha256"] == _sha256(builder_snapshot)

    assert summary["counts"]["status_counts"] == {
        "SEMANTIC_ABSTAIN": 1,
        "SEMANTIC_CANDIDATE_RETAINED": 1,
    }
    assert summary["counts"]["rejection_class_counts"] == {
        "GUARD_REJECTION": 1,
        "MISSING_EVIDENCE": 1,
    }
    assert summary["family_status_counts"] == {
        "direct_lookup": {"SEMANTIC_ABSTAIN": 1},
        "ratio_or_derived": {"SEMANTIC_CANDIDATE_RETAINED": 1},
    }

    abstain = next(
        row for row in summary["records"] if row["semantic_contract_status"] == "SEMANTIC_ABSTAIN"
    )
    assert abstain["abstain_reason_codes"] == [
        "EVIDENCE_NOT_HYDRATED",
        "NO_EVIDENCE_WINDOW",
        "ROW_METRIC_CONFLICT",
    ]
    assert abstain["semantic_rejection_class_counts"] == {
        "GUARD_REJECTION": 1,
        "MISSING_EVIDENCE": 1,
    }

    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in args.output.iterdir()
        if path.is_file()
    )
    for sentinel in (
        "BASELINE_SENTINEL",
        "BASELINE_RATIO_SENTINEL",
        "ANSWER_SENTINEL",
        "RAW_VALUE_SENTINEL",
        "SOURCE_SENTINEL",
        "MODEL_SENTINEL",
        "RESEARCH_SENTINEL",
        "CANDIDATE_SENTINEL",
    ):
        assert sentinel not in rendered
    payload = json.loads((args.output / "summary.json").read_text(encoding="utf-8"))
    AUDIT._assert_value_free_output(payload)
    assert "selected" not in payload["records"][0]
    assert "baseline_candidate_answer_decimal" not in payload["records"][0]


def test_audit_requires_explicit_builder_snapshot_and_rejects_qid_exceptions() -> None:
    with pytest.raises(ValueError, match="explicit --builder-path"):
        AUDIT._require_builder_snapshot(None)
    with pytest.raises(ValueError, match="Question-ID-specific exceptions"):
        AUDIT._assert_no_question_id_exceptions(
            [{"question_id": 7, "question_id_overrides": {"7": "special"}}],
            "fixture",
        )


def _write_comparison_fixture(tmp_path: Path) -> tuple[argparse.Namespace, Path]:
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    _write_jsonl(
        before,
        [
            {
                "question_id": 1,
                "question": "A",
                "family": "direct_lookup",
                "review_status": "MISMATCH",
                "baseline_locator": {"internal_table_uid": "old", "row_index": 1, "column_index": 1},
            },
            {
                "question_id": 2,
                "question": "B",
                "family": "ratio_or_derived",
                "review_status": "CONSISTENT_CANDIDATE",
                "baseline_locator": {"internal_table_uid": "old2", "row_index": 2, "column_index": 1},
            },
        ],
    )
    _write_jsonl(
        after,
        [
            {
                "question_id": 1,
                "question": "A",
                "family": "direct_lookup",
                "semantic_contract_status": "SEMANTIC_ABSTAIN",
                "new_locator": {"internal_table_uid": None, "row_index": None, "column_index": None},
                "abstain_reason_codes": ["ROW_METRIC_CONFLICT", "EVIDENCE_NOT_HYDRATED"],
                "semantic_rejection_reason_codes": ["ROW_METRIC_CONFLICT", "EVIDENCE_NOT_HYDRATED"],
                "semantic_rejection_class_counts": {"GUARD_REJECTION": 2, "MISSING_EVIDENCE": 1},
                "semantic_rejection_reason_counts_by_class": {
                    "ROW_METRIC_CONFLICT": {"GUARD_REJECTION": 2},
                    "EVIDENCE_NOT_HYDRATED": {"MISSING_EVIDENCE": 1},
                },
            },
            {
                "question_id": 2,
                "question": "B",
                "family": "ratio_or_derived",
                "semantic_contract_status": "SEMANTIC_CANDIDATE_CHANGED",
                "new_locator": {"internal_table_uid": "new2", "row_index": 3, "column_index": 2},
                "semantic_rejection_reason_codes": ["PERCENTAGE_CELL_FOR_AMOUNT_QUERY"],
                "semantic_rejection_class_counts": {"GUARD_REJECTION": 1},
                "semantic_rejection_reason_counts_by_class": {
                    "PERCENTAGE_CELL_FOR_AMOUNT_QUERY": {"GUARD_REJECTION": 1},
                },
            },
        ],
    )
    contract = tmp_path / "contract.py"
    contract.write_text(
        'reason_codes.append("ROW_METRIC_CONFLICT")\n'
        'reason_codes.append("EVIDENCE_NOT_HYDRATED")\n'
        'reason_codes.append("PERCENTAGE_CELL_FOR_AMOUNT_QUERY")\n',
        encoding="utf-8",
    )
    return (
        argparse.Namespace(before=before, after=after, contract=contract, output=tmp_path / "comparison"),
        after,
    )


def test_comparator_emits_family_status_class_counts_without_accuracy(tmp_path: Path) -> None:
    args, after = _write_comparison_fixture(tmp_path)
    summary = COMPARATOR.compare(args)
    COMPARATOR._write_outputs(summary, args.output)

    assert summary["policy"]["value_free_output"] is True
    assert summary["policy"]["accuracy_measured"] is False
    assert summary["policy"]["question_id_used_for_tracking_only"] is True
    assert summary["diagnostics"]["guard_rejection_and_missing_evidence_are_separate"] is True
    assert summary["counts"]["after_rejection_class_counts"] == {
        "GUARD_REJECTION": 3,
        "MISSING_EVIDENCE": 1,
    }
    assert summary["family_level"]["direct_lookup"]["after_status_counts"] == {
        "SEMANTIC_ABSTAIN": 1
    }
    assert summary["family_level"]["direct_lookup"]["after_rejection_class_counts"] == {
        "GUARD_REJECTION": 2,
        "MISSING_EVIDENCE": 1,
    }
    assert summary["counts"]["abstain_without_explicit_reason_code_count"] == 0
    assert summary["records"][0]["after_abstain_reason_codes"] == [
        "EVIDENCE_NOT_HYDRATED",
        "ROW_METRIC_CONFLICT",
    ]
    assert summary["inputs"]["after"] == str(after)

    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in args.output.iterdir()
        if path.is_file()
    )
    assert "MODEL_SECRET" not in rendered
    assert "RESEARCH_SECRET" not in rendered
    assert "GOLD_SECRET" not in rendered
    assert "ANSWER_SECRET" not in rendered
    assert "RAW_VALUE_SECRET" not in rendered
    assert "candidate_answer_decimal" not in rendered.casefold()
    assert "selected_cell" not in rendered.casefold()
    COMPARATOR._assert_value_free_output(summary)


def test_comparator_rejects_value_bearing_output_and_qid_exception(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="value-bearing output key"):
        COMPARATOR._assert_value_free_output({"records": [{"value": "123"}]})

    malformed = tmp_path / "malformed.jsonl"
    _write_jsonl(
        malformed,
        [{"question_id": 1, "question_id_overrides": {"1": "special"}}],
    )
    with pytest.raises(ValueError, match="Question-ID-specific exceptions"):
        COMPARATOR._load_record_map(malformed, "before")
