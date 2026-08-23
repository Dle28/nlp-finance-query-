"""Template-contract tests for the component-selection GPU runner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_ccl_phase5_component_selection_model.py"
SPEC = importlib.util.spec_from_file_location("component_selection_runner", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class _Tokenizer:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def apply_chat_template(self, _messages: object, **kwargs: object) -> str:
        self.kwargs = kwargs
        return "rendered"


class ComponentSelectionModelTemplateTests(unittest.TestCase):
    def test_disables_thinking_only_for_qwen3(self) -> None:
        tokenizer = _Tokenizer()
        self.assertEqual(runner._chat_text(tokenizer, "prompt", model_id="Qwen/Qwen3-8B"), "rendered")
        self.assertEqual(tokenizer.kwargs, {"tokenize": False, "add_generation_prompt": True, "enable_thinking": False})

    def test_uses_standard_chat_template_for_challenger(self) -> None:
        tokenizer = _Tokenizer()
        self.assertEqual(runner._chat_text(tokenizer, "prompt", model_id="mistralai/Mistral-Nemo-Instruct-2407"), "rendered")
        self.assertEqual(tokenizer.kwargs, {"tokenize": False, "add_generation_prompt": True})

    def test_accepts_a_bounded_pilot_job_without_relabeling_its_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requests = root / "requests.jsonl"
            route = {"route_id": "source-route", "packet_ids": ["packet-1"]}
            request = {
                "component_selection_request_id": "request-1",
                "component_selection_packet_id": "packet-1",
                "protocol": "vifinqa_ccl_phase5_component_selection_pilot_v1",
                "route": route,
                "packet": {"component_selection_packet_id": "packet-1"},
                "training_eligible": False,
                "certification_allowed": False,
            }
            requests.write_text(json.dumps(request) + "\n", encoding="utf-8")
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "protocol": "vifinqa_ccl_phase5_component_selection_pilot_v1",
                        "schema_version": 1,
                        "run_status": "prepared_component_selection_pilot_not_executed",
                        "model_execution_allowed": True,
                        "training_eligible": False,
                        "certification_allowed": False,
                        "route": route,
                        "outputs": {requests.name: {"sha256": runner.sha256_file(requests)}},
                    }
                ),
                encoding="utf-8",
            )
            original = runner.render_phase5_component_selection_prompt
            try:
                runner.render_phase5_component_selection_prompt = lambda packet: str(packet["component_selection_packet_id"])
                loaded_job, loaded_requests = runner._load_job(job, requests)
            finally:
                runner.render_phase5_component_selection_prompt = original
            self.assertEqual(loaded_job["protocol"], request["protocol"])
            self.assertEqual(loaded_requests, [request])


if __name__ == "__main__":
    unittest.main()
