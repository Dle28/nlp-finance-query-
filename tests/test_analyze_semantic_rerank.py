from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "analyze_semantic_rerank", ROOT / "scripts" / "analyze_semantic_rerank.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


class AnalyzeSemanticRerankTests(unittest.TestCase):
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

