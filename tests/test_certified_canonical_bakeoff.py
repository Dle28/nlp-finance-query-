from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from finance_query.certified_canonical import CertifiedCanonicalError, build_bakeoff_job, run_certified_canonical
from finance_query.certified_canonical import bakeoff as bakeoff_module
from finance_query.certified_canonical.evidence_graph import validate_evidence_graph
import tests.test_certified_canonical as ccl_fixture


class CertifiedCanonicalBakeoffTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path]:
        fixture = ccl_fixture.CertifiedCanonicalTests()
        ccl_config, _ = fixture._write_fixture(root)
        ccl_output = root / "ccl"
        run_certified_canonical(ccl_config, ccl_output)
        return ccl_output / "release_manifest.json", ccl_output / "benchmark_packets_v1.jsonl"

    def _config(
        self,
        root: Path,
        manifest: Path,
        packets: Path,
        *,
        parameter_count: float = 8.2,
        task_contract: dict | None = None,
        packet_ids: list[str] | None = None,
    ) -> Path:
        config = root / "configs/bakeoff.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            yaml.safe_dump(
                {
                    "protocol": "vifinqa_ccl_phase3_bakeoff_v1",
                    "schema_version": 1,
                    "inputs": {
                        "ccl_release_manifest": str(manifest),
                        "benchmark_packets": str(packets),
                    },
                    **({"task_contract": task_contract} if task_contract is not None else {}),
                    "routes": [
                        {
                            "route_id": "qwen-primary",
                            "model_id": "Qwen/Qwen3-8B",
                            "revision": "test-revision",
                            "parameter_count_billions": parameter_count,
                            "modality": "text",
                            "enabled": True,
                            **({"packet_ids": packet_ids} if packet_ids is not None else {"buckets": ["review_ready"]}),
                            "max_packets": len(packet_ids) if packet_ids is not None else 1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return config

    def test_prepares_requests_without_model_execution_or_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, packets = self._inputs(root)
            result = build_bakeoff_job(self._config(root, manifest, packets), root / "bakeoff")

            self.assertEqual(result.request_count, 1)
            self.assertEqual(result.route_counts, {"qwen-primary": 1})
            request = json.loads((root / "bakeoff/llm_bakeoff_requests_v1.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(request["training_eligible"])
            self.assertIn("Never emit certification", request["task"]["instructions"][-1])
            self.assertIn("evidence_graph", request["packet"])
            schema = json.loads((root / "bakeoff/llm_proposal_schema_v1.json").read_text(encoding="utf-8"))
            self.assertIn("evidence_relation_ids", schema["response_contract"]["proposals_item_required"])
            self.assertNotIn("evidence_anchor_ids", schema["response_contract"]["proposals_item_required"])
            self.assertEqual(schema["response_contract"]["response_contract_version"], "relation_only_json_v3")
            self.assertEqual(schema["response_contract"]["required_top_level_keys"], ["proposals", "unresolved_conditions"])
            self.assertFalse(schema["response_contract"]["evidence_selection_contract"]["free_text_citations_allowed"])
            job_manifest = json.loads((root / "bakeoff/bakeoff_job_manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(job_manifest["training_eligible"])
            self.assertFalse(job_manifest["model_execution_recorded"])

    def test_prepares_an_exact_packet_smoke_with_enum_and_relation_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, packets = self._inputs(root)
            packet_id = json.loads(packets.read_text(encoding="utf-8").splitlines()[0])["benchmark_packet_id"]
            task_contract = {
                "name": "statement_profile_smoke",
                "fields": ["table_semantics"],
                "instructions": ["Classify only the source-backed statement profile, or abstain."],
                "field_value_constraints": {
                    "table_semantics": {
                        "allowed_proposed_values": [
                            "balance_sheet",
                            "income_statement",
                            "cash_flow_statement",
                        ]
                    }
                },
                "field_relation_constraints": {"table_semantics": ["heading_scopes_table"]},
            }
            build_bakeoff_job(
                self._config(
                    root,
                    manifest,
                    packets,
                    task_contract=task_contract,
                    packet_ids=[packet_id],
                ),
                root / "bakeoff",
            )
            request = json.loads((root / "bakeoff/llm_bakeoff_requests_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(request["task"], task_contract)
            self.assertEqual(request["packet"]["benchmark_packet_id"], packet_id)
            schema = json.loads((root / "bakeoff/llm_proposal_schema_v1.json").read_text(encoding="utf-8"))
            self.assertEqual(schema["response_contract"]["task_contract"], task_contract)

    def test_rejects_model_at_the_strict_parameter_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, packets = self._inputs(root)
            config = self._config(root, manifest, packets, parameter_count=14.7)
            with self.assertRaisesRegex(CertifiedCanonicalError, "strict <14.7B"):
                build_bakeoff_job(config, root / "bakeoff")

    def test_compacts_oversized_packet_to_a_valid_finite_subgraph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, packets = self._inputs(root)
            packet = json.loads(packets.read_text(encoding="utf-8").splitlines()[0])
            for anchor in packet["evidence_graph"]["anchors"]:
                if anchor.get("selectable"):
                    anchor["quoted_text"] = "x" * 20_000
            graph_without_id = dict(packet["evidence_graph"])
            graph_without_id.pop("evidence_graph_id")
            packet["evidence_graph"]["evidence_graph_id"] = bakeoff_module._sha_json(graph_without_id)
            route = {
                "route_id": "qwen-primary",
                "model_id": "Qwen/Qwen3-8B",
                "revision": "test-revision",
                "parameter_count_billions": 8.2,
                "modality": "text",
                "purpose": "semantic_proposal",
            }
            policy = bakeoff_module._RequestCompaction(
                max_request_payload_characters=7_000,
                max_model_input_tokens=4_096,
                max_anchor_display_characters=384,
                max_selectable_anchors=36,
                max_relations=48,
            )
            compacted, summary = bakeoff_module._compact_packet_for_route(packet, route, policy)
            self.assertEqual(summary["status"], "compacted_finite_subgraph")
            self.assertLessEqual(
                bakeoff_module._request_payload_character_count(compacted, route),
                policy.max_request_payload_characters,
            )
            validate_evidence_graph(compacted["evidence_graph"])
            self.assertTrue(compacted["packet_compaction"]["source_packet_sha256"])
            self.assertEqual(
                bakeoff_module._request_payload(compacted, route)["max_input_tokens"],
                policy.max_model_input_tokens,
            )
