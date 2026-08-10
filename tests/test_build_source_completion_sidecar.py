from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "build_source_completion_sidecar",
    ROOT / "scripts" / "build_source_completion_sidecar.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _table(uid: str, value: str, origins: list[dict] | None = None) -> dict:
    return {
        "internal_table_uid": uid,
        "rows": [["Chỉ tiêu", "Năm 2022"], ["Tiền", value]],
        "source_provenance": {"source_sha256": "raw-sha", "raw_table_sha256": "table-sha"},
        "source_completion": {
            "protocol": "raw_source_completion_v1",
            "candidate_source": "raw_source_completion_v1",
            "answer_eligible": False,
            "training_eligible": False,
            "review_status_promotion_allowed": False,
            "origins": origins or [],
        },
    }


def _context(uid: str) -> dict:
    return {"internal_table_uid": uid, "evidence_context_version": 3}


class BuildSourceCompletionSidecarTests(unittest.TestCase):
    def test_merge_retains_base_and_new_tables_in_deterministic_uid_order(self) -> None:
        tables, contexts = mod.merge_completion_rows(
            [_table("z-base", "10")],
            [_context("z-base")],
            [_table("a-new", "20")],
            [_context("a-new")],
        )
        self.assertEqual([table["internal_table_uid"] for table in tables], ["a-new", "z-base"])
        self.assertEqual([context["internal_table_uid"] for context in contexts], ["a-new", "z-base"])

    def test_merge_deduplicates_identical_raw_uid_and_unions_audit_origins(self) -> None:
        tables, _contexts = mod.merge_completion_rows(
            [_table("raw-u1", "10", [{"question_id": 1, "formula_id": "a", "operand_id": "x"}])],
            [_context("raw-u1")],
            [_table("raw-u1", "10", [
                {"question_id": 1, "formula_id": "a", "operand_id": "x"},
                {"question_id": 2, "formula_id": "b", "operand_id": "y"},
            ])],
            [_context("raw-u1")],
        )
        self.assertEqual(len(tables), 1)
        self.assertEqual(
            tables[0]["source_completion"]["origins"],
            [
                {"question_id": 1, "formula_id": "a", "operand_id": "x"},
                {"question_id": 2, "formula_id": "b", "operand_id": "y"},
            ],
        )

    def test_merge_rejects_uid_with_different_raw_grid(self) -> None:
        with self.assertRaisesRegex(ValueError, "different raw table provenance"):
            mod.merge_completion_rows(
                [_table("raw-u1", "10")],
                [_context("raw-u1")],
                [_table("raw-u1", "99")],
                [_context("raw-u1")],
            )

    def test_merge_rejects_duplicate_context_uid(self) -> None:
        with self.assertRaisesRegex(ValueError, "contexts have duplicate or empty UIDs"):
            mod.merge_completion_rows(
                [_table("raw-u1", "10")],
                [_context("raw-u1"), _context("raw-u1")],
                [],
                [],
            )


if __name__ == "__main__":
    unittest.main()
