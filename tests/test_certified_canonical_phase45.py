from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_query.certified_canonical import build_bakeoff_job, verify_phase45_proposals
from finance_query.certified_canonical.pipeline import CertifiedCanonicalError
import tests.test_certified_canonical_bakeoff as bakeoff_fixture


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifiedCanonicalPhase45Tests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, Path | str]:
        fixture = bakeoff_fixture.CertifiedCanonicalBakeoffTests()
        manifest, packets = fixture._inputs(root)
        config = fixture._config(root, manifest, packets)
        job_dir = root / "job"
        build_bakeoff_job(config, job_dir)
        job_manifest = job_dir / "bakeoff_job_manifest.json"
        requests = job_dir / "llm_bakeoff_requests_v1.jsonl"
        request = json.loads(requests.read_text(encoding="utf-8"))
        graph = request["packet"]["evidence_graph"]
        anchors = {item["anchor_id"]: item for item in graph["anchors"]}
        relations = {item["relation_type"]: item for item in graph["relations"]}

        def proposal(field: str, relation_type: str, value: str) -> dict:
            relation = relations[relation_type]
            return {
                "field": field,
                "proposed_value": value,
                "evidence_relation_ids": [relation["relation_id"]],
                "evidence_anchor_ids": [relation["source_anchor_id"]],
                "evidence_anchor_ids_derivation": "relation_selectable_endpoint_v1",
                "alternative_candidates": [],
                "confidence_not_for_promotion": True,
            }

        period_source = anchors[relations["period_applies_to_column"]["source_anchor_id"]]["quoted_text"]
        unit_source = anchors[relations["unit_applies_to_column"]["source_anchor_id"]]["quoted_text"]
        self.assertEqual(period_source, "2022 VND")
        self.assertEqual(unit_source, "2022 VND")
        validated = root / "validated.jsonl"
        validated.write_text(
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "internal_table_uid": request["internal_table_uid"],
                    "route_id": request["route_id"],
                    "status": "VALID_PROPOSAL_ONLY",
                    "proposal_sha256": "phase3-proposal-hash",
                    "proposals": [
                        proposal("period_context", "period_applies_to_column", "2022"),
                        proposal("unit_context", "unit_applies_to_column", "VND"),
                        proposal("table_semantics", "heading_scopes_table", "Bảng cân đối kế toán"),
                    ],
                    "unresolved_conditions": [],
                    "training_eligible": False,
                    "certification_allowed": False,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        source_manifest = root / "source.json"
        source_manifest.write_text(
            json.dumps({"source_bundle": {"source_tree_sha256": "source-tree"}}), encoding="utf-8"
        )
        validation_manifest = root / "validation.json"
        validation_manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase3_bakeoff_v1",
                    "run_status": "proposal_validation_complete_not_certified",
                    "route_id": request["route_id"],
                    "training_eligible": False,
                    "inputs": {
                        "job_manifest": {"sha256": _sha(job_manifest)},
                        "requests": {"sha256": _sha(requests)},
                    },
                    "outputs": {"validated.jsonl": {"sha256": _sha(validated)}},
                }
            ),
            encoding="utf-8",
        )
        receipt = root / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "job_manifest_sha256": _sha(job_manifest),
                    "source_tree_sha256": "source-tree",
                    "qwen_validation_manifest_sha256": _sha(validation_manifest),
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            ),
            encoding="utf-8",
        )
        audit = root / "audit.json"
        audit.write_text(
            json.dumps(
                {
                    "protocol": "ccl_phase3_gpu_run_audit_v1",
                    "audit_passed": True,
                    "kernel_route": request["route_id"],
                    "input_hashes": {
                        "job_manifest": _sha(job_manifest),
                        "requests": _sha(requests),
                        "source_manifest": _sha(source_manifest),
                    },
                    "counts": {
                        "qwen_request_count": 1,
                        "raw_response_count": 1,
                        "valid_proposal_only_count": 1,
                        "invalid_or_missing_unresolved_count": 0,
                    },
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            ),
            encoding="utf-8",
        )
        return {
            "job_manifest": job_manifest,
            "requests": requests,
            "source_manifest": source_manifest,
            "validated_proposals": validated,
            "proposal_validation_manifest": validation_manifest,
            "phase3_receipt": receipt,
            "phase3_audit": audit,
            "route_id": request["route_id"],
        }

    def test_materializes_period_and_unit_support_without_certifying_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            result = verify_phase45_proposals(**inputs, output_dir=root / "phase45")
            self.assertEqual(result.assertion_count, 3)
            self.assertEqual(result.source_bound_count, 2)
            self.assertEqual(result.unresolved_count, 1)
            assertions = [
                json.loads(line)
                for line in (root / "phase45/phase45_semantic_assertions_v1.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            statuses = {item["field"]: item["status"] for item in assertions}
            self.assertEqual(statuses["period_context"], "SOURCE_BOUND_PROPOSAL")
            self.assertEqual(statuses["unit_context"], "SOURCE_BOUND_PROPOSAL")
            self.assertEqual(statuses["table_semantics"], "UNRESOLVED")
            self.assertTrue(
                all(
                    item["training_eligible"] is False and item["certification_allowed"] is False
                    for item in assertions
                )
            )

    def test_refuses_unbound_validated_proposal_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            validated = Path(str(inputs["validated_proposals"]))
            record = json.loads(validated.read_text(encoding="utf-8"))
            record["proposals"][0]["evidence_anchor_ids"] = ["not-a-derived-anchor"]
            validated.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            output_dir = root / "must_not_exist"
            with self.assertRaisesRegex(CertifiedCanonicalError, "SHA-256 mismatch: validated proposals"):
                verify_phase45_proposals(**inputs, output_dir=output_dir)
            self.assertFalse(output_dir.exists())

