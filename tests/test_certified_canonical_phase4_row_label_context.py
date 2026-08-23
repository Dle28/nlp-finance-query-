from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import CertifiedCanonicalError, materialize_phase4_row_label_context


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class CertifiedCanonicalPhase4RowLabelContextTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, Path | str]:
        route_id, uid, request_id = "route", "table", "request"
        source_id, target_id, relation_id = "row-label", "value", "relation"
        row_label = "Số lượng cổ phiếu đăng ký"
        graph = {
            "schema_version": 1,
            "protocol": "vifinqa_packet_evidence_graph_v1",
            "internal_table_uid": uid,
            "graph_scope": "test",
            "candidate_universe": {},
            "anchors": [
                {
                    "anchor_id": source_id,
                    "internal_table_uid": uid,
                    "kind": "raw_cell",
                    "role": "row_label",
                    "quoted_text": row_label,
                    "raw_text_sha256": hashlib.sha256(row_label.encode()).hexdigest(),
                    "row_index": 2,
                    "column_index": 0,
                    "selectable": True,
                },
                {
                    "anchor_id": target_id,
                    "internal_table_uid": uid,
                    "kind": "raw_cell",
                    "role": "visible_cell",
                    "quoted_text": "263,277,806",
                    "raw_text_sha256": hashlib.sha256(b"263,277,806").hexdigest(),
                    "row_index": 2,
                    "column_index": 1,
                    "selectable": True,
                },
            ],
            "relations": [
                {
                    "relation_id": relation_id,
                    "internal_table_uid": uid,
                    "relation_type": "row_label_defines_value",
                    "source_anchor_id": source_id,
                    "target_anchor_id": target_id,
                    "status": "PASS",
                }
            ],
            "temporal_semantics": {},
            "training_eligible": False,
            "promotion_allowed": False,
        }
        graph["evidence_graph_id"] = _sha_json(graph)
        requests = root / "requests.jsonl"
        request = {
            "request_id": request_id,
            "internal_table_uid": uid,
            "route_id": route_id,
            "model_id": "Qwen/Qwen3-8B",
            "model_revision": "test",
            "training_eligible": False,
            "packet": {"evidence_graph": graph},
        }
        requests.write_text(json.dumps(request, ensure_ascii=False) + "\n", encoding="utf-8")
        job = root / "job.json"
        job.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase3_bakeoff_v1",
                    "run_status": "prepared_phase_3_inference_not_executed",
                    "training_eligible": False,
                    "model_execution_recorded": False,
                    "outputs": {requests.name: {"sha256": _sha(requests)}},
                }
            ),
            encoding="utf-8",
        )
        assertions = root / "assertions.jsonl"
        assertions.write_text(
            json.dumps(
                {
                    "assertion_id": "assertion",
                    "phase3_request_id": request_id,
                    "phase3_proposal_sha256": "proposal",
                    "internal_table_uid": uid,
                    "route_id": route_id,
                    "field": "table_semantics",
                    "proposed_value": "stock_count",
                    "evidence_relation_ids": [relation_id],
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        phase45 = root / "phase45.json"
        phase45.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase45_semantic_verifier_v1",
                    "run_status": "phase_4_5_deterministic_verification_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "inputs": {
                        "job_manifest": {"sha256": _sha(job)},
                        "requests": {"sha256": _sha(requests)},
                    },
                    "outputs": {assertions.name: {"sha256": _sha(assertions)}},
                }
            ),
            encoding="utf-8",
        )
        return {
            "job_manifest": job,
            "requests": requests,
            "phase45_assertions": assertions,
            "phase45_manifest": phase45,
            "route_id": route_id,
        }

    def test_materializes_exact_source_row_label_without_claiming_table_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = materialize_phase4_row_label_context(**self._inputs(root), output_dir=root / "contexts")
            self.assertEqual((result.context_count, result.materialized_count, result.unresolved_count), (1, 1, 0))
            row = json.loads((root / "contexts/phase4_row_label_context_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["status"], "SOURCE_ROW_LABEL_RELATION_CONTEXT_ONLY")
            self.assertEqual(row["source_cell_role"], "row_label")
            self.assertEqual(row["source_cell_text"], "Số lượng cổ phiếu đăng ký")
            self.assertEqual(row["raw_row_index"], 2)
            self.assertFalse(row["training_eligible"])
            self.assertFalse(row["certification_allowed"])

    def test_rejects_tampered_assertions_before_writing_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            Path(inputs["phase45_assertions"]).write_text("{}\n", encoding="utf-8")
            output_dir = root / "contexts"
            with self.assertRaisesRegex(CertifiedCanonicalError, "SHA-256 mismatch: Phase 4-5 assertions"):
                materialize_phase4_row_label_context(**inputs, output_dir=output_dir)
            self.assertFalse(output_dir.exists())
