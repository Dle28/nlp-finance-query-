"""Tests for the non-promotable CCL Phase 5 GPU smoke auditor."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_ccl_phase5_component_selection_smoke.py"
SPEC = importlib.util.spec_from_file_location("phase5_smoke_auditor", SCRIPT)
assert SPEC and SPEC.loader
auditor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auditor)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


class Phase5ComponentSelectionSmokeAuditTests(unittest.TestCase):
    def _build_fixture(self, root: Path) -> dict[str, Path]:
        selected_ids = [f"packet-{index}" for index in range(5)]
        route = {
            "route_id": "qwen3_8b_primary_graph_v33_full",
            "model_id": "Qwen/Qwen3-8B",
            "parameter_count_billions": 8.0,
            "load_in_4bit": True,
            "packet_ids": selected_ids,
        }
        source_packets = root / "source_packets.jsonl"
        source_rows = []
        for index, packet_id in enumerate(selected_ids):
            source_rows.append(
                {
                    "component_selection_packet_id": packet_id,
                    "phase45_assertion_id": f"assertion-{index}",
                    "internal_table_uid": f"table-{index}",
                    "route_id": route["route_id"],
                    "component_menu_stats": {
                        "header_candidate_count": 1,
                        "numeric_value_header_exclusion_count": 0,
                    },
                    "components": [
                        {
                            "component_id": f"context-{index}",
                            "role": "report_scope_or_selected_relation_context",
                            "literal": f"Nguồn {index}",
                        },
                        {"component_id": f"header-{index}", "role": "column_header", "literal": f"Cột {index}"},
                        {"component_id": f"row-{index}", "role": "row_label", "literal": f"Dòng {index}"},
                    ],
                }
            )
        _write_jsonl(source_packets, source_rows)
        source_manifest = root / "source_manifest.json"
        _write_json(
            source_manifest,
            {
                "protocol": auditor.SOURCE_PROTOCOL,
                "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
                "training_eligible": False,
                "certification_allowed": False,
                "outputs": {source_packets.name: {"sha256": auditor.sha256_file(source_packets)}},
            },
        )
        smoke_packets = root / "smoke_packets.jsonl"
        _write_jsonl(smoke_packets, source_rows)
        smoke_packet_manifest = root / "smoke_packet_manifest.json"
        _write_json(
            smoke_packet_manifest,
            {
                "protocol": auditor.SOURCE_PROTOCOL,
                "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
                "source_packet_manifest_sha256": auditor.sha256_file(source_manifest),
                "selected_packet_ids": selected_ids,
                "training_eligible": False,
                "certification_allowed": False,
            },
        )
        requests = root / "requests.jsonl"
        _write_jsonl(requests, [{"component_selection_packet_id": packet_id} for packet_id in selected_ids])
        job_manifest = root / "job_manifest.json"
        _write_json(
            job_manifest,
            {
                "protocol": auditor.SMOKE_PROTOCOL,
                "run_status": "prepared_component_selection_smoke_not_executed",
                "training_eligible": False,
                "certification_allowed": False,
                "route": route,
                "outputs": {
                    smoke_packets.name: {"sha256": auditor.sha256_file(smoke_packets)},
                    smoke_packet_manifest.name: {"sha256": auditor.sha256_file(smoke_packet_manifest)},
                    requests.name: {"sha256": auditor.sha256_file(requests)},
                },
            },
        )
        source_bundle_manifest = root / "source_bundle_manifest.json"
        _write_json(source_bundle_manifest, {"source_bundle": {"source_tree_sha256": "source-tree-sha"}})

        artifact_dir = root / "artifacts"
        raw_dir = artifact_dir / "ccl_phase5_component_selection_smoke_raw"
        validated_dir = artifact_dir / "ccl_phase5_component_selection_smoke_validated"
        raw_responses = raw_dir / "component_selection_raw_responses_v1.jsonl"
        _write_jsonl(raw_responses, [{"component_selection_packet_id": packet_id, "response": "{}"} for packet_id in selected_ids])
        execution_report = raw_dir / "component_selection_model_execution_report.json"
        _write_json(execution_report, {"complete": True})
        execution_manifest = raw_dir / "component_selection_model_execution_manifest.json"
        _write_json(
            execution_manifest,
            {
                "protocol": auditor.SMOKE_PROTOCOL,
                "run_status": "component_selection_model_execution_complete_responses_unvalidated",
                "training_eligible": False,
                "certification_allowed": False,
                "route": route,
                "inputs": {
                    "job_manifest": {"sha256": auditor.sha256_file(job_manifest)},
                    "requests": {"sha256": auditor.sha256_file(requests)},
                },
                "outputs": {raw_responses.name: {"sha256": auditor.sha256_file(raw_responses)}},
            },
        )
        results = validated_dir / "phase5_component_selection_results_v1.jsonl"
        _write_jsonl(
            results,
            [
                {
                    "component_selection_packet_id": packet_id,
                    "internal_table_uid": f"table-{index}",
                    "status": "VALID_COMPONENT_SELECTION_ONLY",
                    "selection": {
                        "primary_component_id": f"context-{index}",
                        "supporting_component_ids": [f"header-{index}", f"row-{index}"],
                        "unresolved_conditions": [],
                    },
                    "training_eligible": False,
                    "certification_allowed": False,
                }
                for index, packet_id in enumerate(selected_ids)
            ],
        )
        validation_manifest = validated_dir / "phase5_component_selection_validation_manifest.json"
        _write_json(
            validation_manifest,
            {
                "protocol": auditor.SOURCE_PROTOCOL,
                "run_status": "phase_5_component_selection_validation_complete_not_certified",
                "route_id": route["route_id"],
                "input_hashes_unchanged": True,
                "training_eligible": False,
                "certification_allowed": False,
                "inputs": {
                    smoke_packets.name: {"sha256": auditor.sha256_file(smoke_packets)},
                    smoke_packet_manifest.name: {"sha256": auditor.sha256_file(smoke_packet_manifest)},
                    raw_responses.name: {"sha256": auditor.sha256_file(raw_responses)},
                },
                "outputs": {results.name: {"sha256": auditor.sha256_file(results)}},
            },
        )
        receipt = artifact_dir / "ccl_phase5_component_selection_kaggle_receipt_v1.json"
        _write_json(
            receipt,
            {
                "training_eligible": False,
                "certification_allowed": False,
                "job_manifest_sha256": auditor.sha256_file(job_manifest),
                "source_tree_sha256": "source-tree-sha",
                "execution_report_sha256": auditor.sha256_file(execution_report),
                "validation_manifest_sha256": auditor.sha256_file(validation_manifest),
                "validation_status_counts": {"VALID_COMPONENT_SELECTION_ONLY": 5},
                "gpu": {"name": "Tesla P100", "vram_gib": 15.89},
                "model_runtime": {
                    "cuda_available": True,
                    "gpu_compute_capability": [6, 0],
                    "torch_arch_list": ["sm_60"],
                },
            },
        )
        return {
            "source_packets": source_packets,
            "source_manifest": source_manifest,
            "smoke_job_manifest": job_manifest,
            "smoke_packets": smoke_packets,
            "smoke_packet_manifest": smoke_packet_manifest,
            "requests": requests,
            "source_bundle_manifest": source_bundle_manifest,
            "artifact_dir": artifact_dir,
        }

    def test_audits_hash_bound_literal_component_selection_without_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._build_fixture(Path(temporary))
            output = Path(temporary) / "audit.json"
            audit = auditor.audit_component_selection_smoke(**paths, output=output)
            self.assertTrue(audit["audit_passed"])
            self.assertEqual(audit["counts"]["validated_status_counts"], {"VALID_COMPONENT_SELECTION_ONLY": 5})
            self.assertEqual(audit["numeric_guard"], {
                "applied": True,
                "source_numeric_header_violation_count": 0,
                "source_numeric_value_header_exclusion_count": 0,
            })
            self.assertFalse(audit["training_eligible"])
            self.assertEqual(audit["source_literal_selection_audit"][0]["primary"]["literal"], "Nguồn 0")
            self.assertEqual(audit["source_literal_selection_audit"][0]["supporting"][1]["role"], "row_label")

    def test_rejects_tampered_raw_response_before_audit_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._build_fixture(Path(temporary))
            raw_responses = paths["artifact_dir"] / "ccl_phase5_component_selection_smoke_raw" / "component_selection_raw_responses_v1.jsonl"
            raw_responses.write_text('{"component_selection_packet_id":"packet-0","response":"tampered"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch: raw responses"):
                auditor.audit_component_selection_smoke(**paths, output=Path(temporary) / "audit.json")


if __name__ == "__main__":
    unittest.main()
