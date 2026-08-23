"""Tests for deterministic component-selection pilot job construction."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import yaml

from finance_query.certified_canonical.phase5_component_selection import _task_contract


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_ccl_phase5_component_selection_pilot_job.py"
SPEC = importlib.util.spec_from_file_location("component_selection_pilot", SCRIPT)
assert SPEC and SPEC.loader
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Tokenizer:
    chat_template = "test-chat-template"

    def apply_chat_template(self, messages: object, **_kwargs: object) -> str:
        assert isinstance(messages, list)
        return str(messages[0]["content"])

    def __call__(self, prompt: str, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(input_ids=list(range(2500 if "note-root-3" in prompt else 500)))


class ComponentSelectionPilotJobTests(unittest.TestCase):
    def _config(self, root: Path, *, row_count: int = 2) -> Path:
        route_id = "route"
        rows = []
        for index in range(4):
            rows.append(
                {
                    "schema_version": 1,
                    "protocol": "vifinqa_ccl_phase5_component_selection_v1",
                    "component_selection_packet_id": f"note-{3 - index}",
                    "phase45_assertion_id": f"note-assertion-{index}",
                    "phase3_request_id": f"note-request-{index}",
                    "internal_table_uid": f"note-table-{index}",
                    "route_id": route_id,
                    "source_first_route_status": "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED",
                    "task": _task_contract(),
                    "components": [
                        {"component_id": f"note-root-{index}", "role": "report_scope_or_selected_relation_context", "literal": "Nguồn"},
                        {"component_id": f"note-header-{index}", "role": "column_header", "literal": "Cột"},
                    ],
                    "llm_dispatch_allowed": False,
                    "campaign_candidate_allowed": False,
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
        for index in range(row_count):
            rows.append(
                {
                    "schema_version": 1,
                    "protocol": "vifinqa_ccl_phase5_component_selection_v1",
                    "component_selection_packet_id": f"row-{2 - index}",
                    "phase45_assertion_id": f"row-assertion-{index}",
                    "phase3_request_id": f"row-request-{index}",
                    "internal_table_uid": f"row-table-{index}",
                    "route_id": route_id,
                    "source_first_route_status": "DETERMINISTIC_ROW_LABEL_RELATION_CONTEXT_ONLY",
                    "task": _task_contract(),
                    "components": [
                        {"component_id": f"row-root-{index}", "role": "report_scope_or_selected_relation_context", "literal": "Nguồn"},
                        {"component_id": f"row-label-{index}", "role": "row_label", "literal": "Dòng"},
                    ],
                    "llm_dispatch_allowed": False,
                    "campaign_candidate_allowed": False,
                    "training_eligible": False,
                    "certification_allowed": False,
                }
            )
        packets = root / "packets.jsonl"
        packets.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "protocol": "vifinqa_ccl_phase5_component_selection_v1",
                    "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
                    "training_eligible": False,
                    "certification_allowed": False,
                    "navigation_overlay_required": True,
                    "inputs": {
                        "report_navigation_overlay_v1.jsonl": {"sha256": "a" * 64},
                        "report_navigation_overlay_manifest.json": {"sha256": "b" * 64},
                    },
                    "outputs": {packets.name: {"sha256": _sha(packets)}},
                }
            ),
            encoding="utf-8",
        )
        config = root / "config.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "protocol": pilot.PROTOCOL,
                    "schema_version": 1,
                    "inputs": {"packets": str(packets), "packet_manifest": str(manifest)},
                    "selection": {"strategy": "token_budget_then_packet_id_by_source_context", "note_context_count": 3, "row_label_context_count": 2},
                    "route": {
                        "route_id": route_id,
                        "model_id": "Qwen/Qwen3-8B",
                        "revision": "main",
                        "parameter_count_billions": 8.0,
                        "load_in_4bit": True,
                        "max_input_tokens": 2048,
                        "max_new_tokens": 384,
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return config

    def test_selects_deterministic_strata_and_keeps_them_non_promotable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(pilot, "_load_tokenizer", return_value=_Tokenizer()):
                result = pilot.build_component_selection_pilot_job(config_path=self._config(root), output_dir=root / "job")
            self.assertEqual(result["request_count"], 5)
            manifest = json.loads((root / "job/component_selection_pilot_packet_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["selected_packet_ids"], ["note-1", "note-2", "note-3", "row-1", "row-2"])
            self.assertFalse(manifest["training_eligible"])
            self.assertTrue(manifest["navigation_overlay_required"])
            preflight = json.loads((root / "job/component_selection_pilot_token_preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(preflight["eligible_candidate_counts"]["DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED"], 3)
            self.assertEqual(preflight["results"][0]["exclusion_reason"], "INPUT_TOKEN_BUDGET_EXCEEDED")

    def test_rejects_quota_that_exceeds_available_source_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, row_count=1)
            with self.assertRaisesRegex(Exception, "quotas exceed"):
                with mock.patch.object(pilot, "_load_tokenizer", return_value=_Tokenizer()):
                    pilot.build_component_selection_pilot_job(config_path=config, output_dir=root / "job")
            self.assertFalse((root / "job").exists())

    def test_rejects_source_manifest_without_navigation_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("navigation_overlay_required")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "required navigation overlay"):
                with mock.patch.object(pilot, "_load_tokenizer", return_value=_Tokenizer()):
                    pilot.build_component_selection_pilot_job(config_path=config, output_dir=root / "job")
            self.assertFalse((root / "job").exists())


if __name__ == "__main__":
    unittest.main()
