from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from finance_query.certified_canonical import (
    CertifiedCanonicalError,
    build_phase5_component_selection_smoke_job,
)
from finance_query.certified_canonical.phase5_component_selection import _task_contract


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CertifiedCanonicalPhase5ComponentSelectionSmokeTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        route_id = "route"
        packet_ids = [f"packet-{index}" for index in range(5)]
        packets = []
        for index, packet_id in enumerate(packet_ids):
            kind = "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED" if index < 3 else "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY"
            packets.append(
                {
                    "schema_version": 1,
                    "protocol": "vifinqa_ccl_phase5_component_selection_v1",
                    "component_selection_packet_id": packet_id,
                    "phase45_assertion_id": f"assertion-{index}",
                    "phase3_request_id": f"request-{index}",
                    "internal_table_uid": f"table-{index}",
                    "route_id": route_id,
                    "source_first_route_status": kind,
                    "task": _task_contract(),
                    "components": [
                        {"component_id": f"root-{index}", "role": "report_scope_or_selected_relation_context", "literal": "Phạm vi"},
                        {"component_id": f"header-{index}", "role": "column_header", "literal": "2024"},
                    ],
                    "llm_dispatch_allowed": False,
                    "campaign_candidate_allowed": False,
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
        packets_path = root / "packets.jsonl"
        packets_path.write_text("".join(json.dumps(packet, ensure_ascii=False) + "\n" for packet in packets), encoding="utf-8")
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase5_component_selection_v1",
                    "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
                    "navigation_overlay_required": True,
                    "training_eligible": False,
                    "certification_allowed": False,
                    "inputs": {
                        "report_navigation_overlay_v1.jsonl": {"sha256": "a" * 64},
                        "report_navigation_overlay_manifest.json": {"sha256": "b" * 64},
                    },
                    "outputs": {packets_path.name: {"sha256": _sha(packets_path)}},
                }
            ),
            encoding="utf-8",
        )
        config = root / "config.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "protocol": "vifinqa_ccl_phase5_component_selection_smoke_v1",
                    "schema_version": 1,
                    "inputs": {"packets": str(packets_path), "packet_manifest": str(manifest_path)},
                    "route": {
                        "route_id": route_id,
                        "model_id": "Qwen/Qwen3-8B",
                        "revision": "main",
                        "parameter_count_billions": 8.0,
                        "load_in_4bit": True,
                        "max_input_tokens": 2048,
                        "max_new_tokens": 256,
                        "packet_ids": packet_ids,
                    },
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return config

    def test_builds_exact_five_packet_smoke_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = build_phase5_component_selection_smoke_job(config_path=self._config(root), output_dir=root / "job")
            self.assertEqual(result.request_count, 5)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["model_execution_allowed"])
            self.assertTrue(manifest["navigation_overlay_required"])
            requests = [json.loads(line) for line in (root / "job/component_selection_smoke_requests_v1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["component_selection_packet_id"] for row in requests], [f"packet-{index}" for index in range(5)])
            selection_manifest = json.loads((root / "job/component_selection_smoke_packet_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(selection_manifest["selected_packet_ids"], [f"packet-{index}" for index in range(5)])
            self.assertTrue(all(row["training_eligible"] is False and row["certification_allowed"] is False for row in requests))

    def test_rejects_manifest_hash_mismatch_before_writing_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            packet_path = root / "packets.jsonl"
            packet_path.write_text("{}\n", encoding="utf-8")
            output_dir = root / "job"
            with self.assertRaisesRegex(CertifiedCanonicalError, "packets do not match"):
                build_phase5_component_selection_smoke_job(config_path=config, output_dir=output_dir)
            self.assertFalse(output_dir.exists())

    def test_rejects_an_ungated_component_packet_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("inputs")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(CertifiedCanonicalError, "required navigation overlay"):
                build_phase5_component_selection_smoke_job(config_path=config, output_dir=root / "job")
