from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import verify_phase5_header_components


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifiedCanonicalPhase5HeaderComponentTests(unittest.TestCase):
    def test_splits_only_the_selected_component_without_rewriting_the_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            route_id = "route"
            requests_rows: list[dict] = []
            assertions: list[dict] = []
            for index, (field, header, reason) in enumerate(
                (
                    ("period_context", "31/12/2024 VND", "period_value_contains_unit_token"),
                    ("unit_context", "Năm nayVND", "unit_value_contains_period_token"),
                ),
                start=1,
            ):
                uid, request_id = f"table-{index}", f"request-{index}"
                source_id, target_id, relation_id = f"source-{index}", f"target-{index}", f"relation-{index}"
                relation_type = "period_applies_to_column" if field == "period_context" else "unit_applies_to_column"
                graph = {
                    "schema_version": 1,
                    "protocol": "vifinqa_packet_evidence_graph_v1",
                    "internal_table_uid": uid,
                    "graph_scope": "test",
                    "candidate_universe": {},
                    "anchors": [
                        {"anchor_id": source_id, "internal_table_uid": uid, "kind": "raw_cell", "quoted_text": header, "selectable": True},
                        {"anchor_id": target_id, "internal_table_uid": uid, "kind": "canonical_column_slot", "selectable": False},
                    ],
                    "relations": [
                        {
                            "relation_id": relation_id,
                            "internal_table_uid": uid,
                            "relation_type": relation_type,
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
                requests_rows.append(
                    {
                        "request_id": request_id,
                        "internal_table_uid": uid,
                        "route_id": route_id,
                        "training_eligible": False,
                        "packet": {"evidence_graph": graph},
                    }
                )
                assertions.append(
                    {
                        "assertion_id": f"assertion-{index}",
                        "phase3_request_id": request_id,
                        "phase3_proposal_sha256": f"proposal-{index}",
                        "internal_table_uid": uid,
                        "route_id": route_id,
                        "field": field,
                        "proposed_value": header,
                        "evidence_relation_ids": [relation_id],
                        "evidence_anchor_ids": [source_id],
                        "reason_codes": [reason],
                        "training_eligible": False,
                        "certification_allowed": False,
                    }
                )
            requests = root / "requests.jsonl"
            requests.write_text("".join(json.dumps(row) + "\n" for row in requests_rows), encoding="utf-8")
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
            assertion_path = root / "assertions.jsonl"
            assertion_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in assertions), encoding="utf-8")
            phase45 = root / "phase45.json"
            phase45.write_text(
                json.dumps(
                    {
                        "protocol": "vifinqa_ccl_phase45_semantic_verifier_v1",
                        "run_status": "phase_4_5_deterministic_verification_complete_not_certified",
                        "training_eligible": False,
                        "certification_allowed": False,
                        "outputs": {"assertions.jsonl": {"sha256": _sha_file(assertion_path)}},
                    }
                ),
                encoding="utf-8",
            )
            result = verify_phase5_header_components(
                job_manifest=job,
                requests=requests,
                phase45_assertions=assertion_path,
                phase45_manifest=phase45,
                output_dir=root / "components",
                route_id=route_id,
            )
            self.assertEqual(
                (result.component_count, result.period_component_count, result.unit_component_count, result.unresolved_count),
                (2, 1, 1, 0),
            )
            rows = [
                json.loads(line)
                for line in (root / "components/phase5_header_components_v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            by_field = {row["field"]: row for row in rows}
            self.assertEqual(by_field["period_context"]["derived_value"], "31/12/2024")
            self.assertEqual(by_field["unit_context"]["derived_value"], "VND")
            self.assertEqual(by_field["period_context"]["origin_proposed_value"], "31/12/2024 VND")
            self.assertTrue(all(not row["training_eligible"] and not row["certification_allowed"] for row in rows))
