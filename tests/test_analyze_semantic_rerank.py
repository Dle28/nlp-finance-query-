from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from finance_query.semantic_rerank import (
    SEMANTIC_INPUT_RENDERER_VERSION,
    semantic_candidate_input,
    semantic_input_digest,
)


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "analyze_semantic_rerank", ROOT / "scripts" / "analyze_semantic_rerank.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class AnalyzeSemanticRerankTests(unittest.TestCase):
    def test_context_filename_uses_manifest_or_legacy_v1_fallback(self) -> None:
        bundle = Path("/tmp/bundle")
        self.assertEqual(
            mod.evidence_context_path(bundle, {"evidence_context_file": "tables_evidence_context_v2.jsonl"}),
            bundle / "tables_evidence_context_v2.jsonl",
        )
        self.assertEqual(
            mod.evidence_context_path(bundle, {}),
            bundle / "tables_evidence_context_v1.jsonl",
        )
        with self.assertRaises(ValueError):
            mod.evidence_context_path(bundle, {"evidence_context_file": "../outside.jsonl"})

    def test_percentile_uses_bounded_observed_values(self) -> None:
        self.assertIsNone(mod.percentile([], 0.5))
        self.assertEqual(mod.percentile([0.1, 0.2, 0.3], 0.5), 0.2)

    def test_complete_human_audit_excludes_partial_and_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "human.jsonl"
            records = [
                {
                    "id": 1,
                    "annotation_status": "human_verified",
                    "evidence_completeness": "complete",
                    "positive_table_uids": ["u1"],
                },
                {
                    "id": 2,
                    "annotation_status": "human_verified_partial",
                    "evidence_completeness": "partial",
                    "positive_table_uids": ["u2"],
                },
            ]
            path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
            self.assertEqual(mod.complete_human_uids(path), {1: {"u1"}})

    def test_v2_audit_rejects_input_not_regenerable_from_raw_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            candidate = {
                "internal_table_uid": "u1",
                "rank": 1,
                "value_row_index": 1,
                "evidence_window": [{"index": 1, "row": ["Tiền", "100"]}],
            }
            item = {"id": 1, "question": "Tiền cuối năm là bao nhiêu?", "candidates": [candidate]}
            table = {"internal_table_uid": "u1", "rows": [["", "Cuối năm"], ["Tiền", "100"]]}
            context = {
                "internal_table_uid": "u1",
                "canonical_headers": {"columns": [{"source_label": "Chỉ tiêu"}, {"source_label": "Cuối năm"}]},
            }
            (bundle / "tables_structured_v2.jsonl").write_text(json.dumps(table) + "\n", encoding="utf-8")
            (bundle / "tables_evidence_context_v1.jsonl").write_text(json.dumps(context) + "\n", encoding="utf-8")
            source_input = semantic_candidate_input(item["question"], candidate, table, context)
            score_rows = {
                1: {
                    "candidate_scores": [
                        {
                            "internal_table_uid": "u1",
                            "rank": 1,
                            "source_input": source_input,
                            "input_sha256": semantic_input_digest(item["question"], source_input),
                        }
                    ]
                }
            }
            manifest = {"schema_version": 2, "input_renderer_version": SEMANTIC_INPUT_RENDERER_VERSION}
            mod.validate_v2_source_inputs(bundle, manifest, score_rows, {1: item})
            score_rows[1]["candidate_scores"][0]["source_input"] = "not source grounded"
            with self.assertRaises(ValueError):
                mod.validate_v2_source_inputs(bundle, manifest, score_rows, {1: item})
