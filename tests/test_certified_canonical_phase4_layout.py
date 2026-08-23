from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import materialize_phase4_heading_spans


def _sha_bytes(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifiedCanonicalPhase4LayoutTests(unittest.TestCase):
    def _inputs(self, root: Path, *, context: str) -> dict[str, Path | str]:
        uid, route_id, request_id = "table-1", "qwen-route", "request-1"
        context_anchor, table_anchor, relation_id = "context-anchor", "table-anchor", "heading-relation"
        graph = {
            "schema_version": 1,
            "protocol": "vifinqa_packet_evidence_graph_v1",
            "internal_table_uid": uid,
            "graph_scope": "test",
            "candidate_universe": {},
            "anchors": [
                {
                    "anchor_id": context_anchor,
                    "internal_table_uid": uid,
                    "kind": "raw_context",
                    "field": "context_before",
                    "raw_text_sha256": _sha_bytes(context),
                    "quoted_text": context,
                    "selectable": True,
                },
                {
                    "anchor_id": table_anchor,
                    "internal_table_uid": uid,
                    "kind": "table_slot",
                    "selectable": False,
                },
            ],
            "relations": [
                {
                    "relation_id": relation_id,
                    "internal_table_uid": uid,
                    "relation_type": "heading_scopes_table",
                    "source_anchor_id": context_anchor,
                    "target_anchor_id": table_anchor,
                    "status": "PASS",
                }
            ],
            "temporal_semantics": {},
            "training_eligible": False,
            "promotion_allowed": False,
        }
        graph["evidence_graph_id"] = _sha_json(graph)
        requests = root / "requests.jsonl"
        requests.write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "internal_table_uid": uid,
                    "route_id": route_id,
                    "training_eligible": False,
                    "packet": {"evidence_graph": graph},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        job = root / "job.json"
        job.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase3_bakeoff_v1",
                    "run_status": "prepared_phase_3_inference_not_executed",
                    "training_eligible": False,
                    "outputs": {"requests.jsonl": {"sha256": _sha_file(requests)}},
                }
            ),
            encoding="utf-8",
        )
        assertions = root / "phase45.jsonl"
        assertions.write_text(
            json.dumps(
                {
                    "assertion_id": "phase45-assertion",
                    "phase3_request_id": request_id,
                    "phase3_proposal_sha256": "phase3-proposal",
                    "internal_table_uid": uid,
                    "route_id": route_id,
                    "field": "table_semantics",
                    "status": "UNRESOLVED",
                    "evidence_relation_ids": [relation_id],
                    "evidence_anchor_ids": [context_anchor],
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        phase45_manifest = root / "phase45.json"
        phase45_manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase45_semantic_verifier_v1",
                    "run_status": "phase_4_5_deterministic_verification_complete_not_certified",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "inputs": {
                        "job_manifest": {"sha256": _sha_file(job)},
                        "requests": {"sha256": _sha_file(requests)},
                    },
                    "outputs": {"phase45.jsonl": {"sha256": _sha_file(assertions)}},
                }
            ),
            encoding="utf-8",
        )
        raw = {"internal_table_uid": uid, "context_before": context}
        raw_tables = root / "raw.jsonl"
        raw_tables.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")
        normalized_tables = root / "normalized.jsonl"
        normalized_tables.write_text(
            json.dumps(
                {
                    "internal_table_uid": uid,
                    "source_record_sha256": _sha_json(raw),
                    "outside_table_context": {
                        "source_heading": "Balance Sheet",
                        "source_context_sha256": _sha_bytes(context),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        inventory = root / "inventory.json"
        inventory.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_certified_canonical_v1",
                    "stage": "phase_0_freeze_and_baseline",
                    "inputs": {
                        "raw_tables": {"sha256": _sha_file(raw_tables)},
                        "preprocessing_normalized": {"sha256": _sha_file(normalized_tables)},
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "job_manifest": job,
            "requests": requests,
            "phase45_assertions": assertions,
            "phase45_manifest": phase45_manifest,
            "ccl_input_inventory": inventory,
            "raw_tables": raw_tables,
            "normalized_tables": normalized_tables,
            "route_id": route_id,
        }

    def test_materializes_one_unique_literal_heading_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = materialize_phase4_heading_spans(
                **self._inputs(root, context="Report title. Balance Sheet. Unit: VND."),
                output_dir=root / "phase4",
            )
            self.assertEqual((result.span_count, result.materialized_count, result.unresolved_count), (1, 1, 0))
            row = json.loads((root / "phase4/phase4_heading_spans_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["status"], "SOURCE_HEADING_SPAN_MATERIALIZED")
            self.assertEqual(row["raw_context_character_span"], {"start": 14, "end": 27})
            self.assertFalse(row["training_eligible"])
            self.assertFalse(row["certification_allowed"])

    def test_keeps_repeated_literal_heading_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = materialize_phase4_heading_spans(
                **self._inputs(root, context="Balance Sheet. More text. Balance Sheet."),
                output_dir=root / "phase4",
            )
            self.assertEqual((result.materialized_count, result.unresolved_count), (0, 1))
            row = json.loads((root / "phase4/phase4_heading_spans_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["reason_codes"], ["source_heading_not_unique_in_raw_context"])

