from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_query.certified_canonical import build_bakeoff_job, validate_raw_responses
from finance_query.certified_canonical.model_execution import (
    _chat_template_kwargs,
    _cuda_runtime_contract,
    _extract_json,
    _input_ids_for_request,
    _model_packet_view,
)
from finance_query.certified_canonical.pipeline import CertifiedCanonicalError
import tests.test_certified_canonical_bakeoff as bakeoff_fixture


class CertifiedCanonicalModelExecutionTests(unittest.TestCase):
    def test_qwen3_template_disables_thinking_but_other_models_are_untouched(self) -> None:
        qwen = _chat_template_kwargs({"model_id": "Qwen/Qwen3-8B"})
        self.assertFalse(qwen["enable_thinking"])
        self.assertEqual(qwen["return_tensors"], "pt")
        gemma = _chat_template_kwargs({"model_id": "google/gemma-3-12b-it"})
        self.assertNotIn("enable_thinking", gemma)

    def test_chat_template_accepts_a_batch_encoding_response(self) -> None:
        sentinel = object()

        class _Tokenizer:
            chat_template = "template"

            @staticmethod
            def apply_chat_template(*_args, **_kwargs):
                return {"input_ids": sentinel}

        input_ids = _input_ids_for_request(
            _Tokenizer(),
            {"task": {}, "packet": {}},
            {
                "response_contract": {
                    "response_contract_version": "relation_only_json_v3",
                    "required_top_level_keys": ["proposals", "unresolved_conditions"],
                    "allowed_top_level_keys": ["proposals", "unresolved_conditions"],
                }
            },
            {"model_id": "Qwen/Qwen3-8B"},
        )
        self.assertIs(input_ids, sentinel)

    def test_parser_only_repairs_one_terminal_extra_data_character(self) -> None:
        parsed, error, repair = _extract_json('{"proposals":[],"unresolved_conditions":[]}]')
        self.assertEqual(parsed, {"proposals": [], "unresolved_conditions": []})
        self.assertIsNone(error)
        self.assertEqual(repair, "dropped_single_terminal_extra_data_character")
        parsed, error, repair = _extract_json('{"proposals":[]')
        self.assertIsNone(parsed)
        self.assertEqual(error, "response_not_a_single_json_object")
        self.assertIsNone(repair)

    def test_model_packet_view_hides_nonselectable_nodes_and_scopes_relations_by_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, requests, _ = self._job(root)
            request = json.loads(requests.read_text(encoding="utf-8"))
            view = _model_packet_view(request)
            menu = view["evidence_selection_menu"]
            graph = request["packet"]["evidence_graph"]
            selectable = {
                item["anchor_id"] for item in graph["anchors"] if item.get("selectable")
            }
            self.assertEqual(
                {item["anchor_id"] for item in menu["selectable_anchors"]}, selectable
            )
            for field, options in menu["relation_options_by_field"].items():
                for option in options:
                    self.assertTrue(set(option["selectable_endpoint_anchor_ids"]).issubset(selectable))
                    self.assertIn(field, request["task"]["fields"])

    def test_cuda_preflight_rejects_a_wheel_without_the_scheduled_gpu_architecture(self) -> None:
        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def get_device_capability(_index: int) -> tuple[int, int]:
                return (6, 0)

            @staticmethod
            def get_device_name(_index: int) -> str:
                return "Tesla P100"

            @staticmethod
            def get_arch_list() -> list[str]:
                return ["sm_70", "sm_75"]

        class _Torch:
            __version__ = "2.9.0+cu126"
            cuda = _Cuda()

            class version:
                cuda = "12.6"

        with self.assertRaisesRegex(CertifiedCanonicalError, "sm_60"):
            _cuda_runtime_contract(_Torch(), require_4bit=True)

    def test_cuda_preflight_records_a_supported_p100_nf4_runtime(self) -> None:
        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def get_device_capability(_index: int) -> tuple[int, int]:
                return (6, 0)

            @staticmethod
            def get_device_name(_index: int) -> str:
                return "Tesla P100"

            @staticmethod
            def get_arch_list() -> list[str]:
                return ["sm_50", "sm_60", "sm_70"]

        class _Torch:
            __version__ = "2.6.0+cu118"
            cuda = _Cuda()

            class version:
                cuda = "11.8"

        runtime = _cuda_runtime_contract(_Torch(), require_4bit=True)
        self.assertEqual(runtime["gpu_compute_capability"], [6, 0])
        self.assertIn("sm_60", runtime["torch_arch_list"])

    def _job(self, root: Path) -> tuple[Path, Path, Path]:
        fixture = bakeoff_fixture.CertifiedCanonicalBakeoffTests()
        manifest, packets = fixture._inputs(root)
        config = fixture._config(root, manifest, packets)
        job = root / "job"
        build_bakeoff_job(config, job)
        return (
            job / "bakeoff_job_manifest.json",
            job / "llm_bakeoff_requests_v1.jsonl",
            job / "llm_proposal_schema_v1.json",
        )

    @staticmethod
    def _raw_response(request: dict, *, forbidden: bool = False) -> dict:
        graph = request["packet"]["evidence_graph"]
        relation = next(
            item
            for item in graph["relations"]
            if item["relation_type"] == "heading_scopes_table"
        )
        response = {
            "proposals": [
                {
                    "field": "table_semantics",
                    "proposed_value": "balance_sheet",
                    "evidence_anchor_ids": [relation["source_anchor_id"]],
                    "evidence_relation_ids": [relation["relation_id"]],
                    "alternative_candidates": [],
                    "confidence_not_for_promotion": 0.4,
                }
            ],
            "unresolved_conditions": [],
        }
        if forbidden:
            response["training_eligible"] = True
        return {
            "request_id": request["request_id"],
            "internal_table_uid": request["internal_table_uid"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "raw_response": json.dumps(response, ensure_ascii=False),
        }

    def test_validates_source_anchored_proposal_without_certifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_manifest, requests, _ = self._job(root)
            request = json.loads(requests.read_text(encoding="utf-8"))
            raw = root / "raw.jsonl"
            raw.write_text(json.dumps(self._raw_response(request), ensure_ascii=False) + "\n", encoding="utf-8")
            result = validate_raw_responses(
                job_manifest_path=job_manifest,
                requests_path=requests,
                raw_responses_path=raw,
                output_dir=root / "validated",
            )
            self.assertEqual(result.valid_response_count, 1)
            self.assertEqual(result.invalid_response_count, 0)
            record = json.loads((root / "validated/llm_proposals_validated_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "VALID_PROPOSAL_ONLY")
            self.assertFalse(record["training_eligible"])
            self.assertFalse(record["certification_allowed"])
            self.assertEqual(record["evidence_selection_contract"], "finite_packet_ids_only_v1")

    def test_records_terminal_json_repair_without_changing_proposal_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_manifest, requests, _ = self._job(root)
            request = json.loads(requests.read_text(encoding="utf-8"))
            raw_response = self._raw_response(request)
            raw_response["raw_response"] += "]"
            raw = root / "raw.jsonl"
            raw.write_text(json.dumps(raw_response, ensure_ascii=False) + "\n", encoding="utf-8")
            validate_raw_responses(
                job_manifest_path=job_manifest,
                requests_path=requests,
                raw_responses_path=raw,
                output_dir=root / "validated",
            )
            record = json.loads((root / "validated/llm_proposals_validated_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "VALID_PROPOSAL_ONLY")
            self.assertEqual(
                record["response_format_normalization"]["method"],
                "dropped_single_terminal_extra_data_character",
            )

    def test_relation_only_proposal_derives_its_selectable_source_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_manifest, requests, _ = self._job(root)
            request = json.loads(requests.read_text(encoding="utf-8"))
            raw_response = self._raw_response(request)
            proposal = json.loads(raw_response["raw_response"])
            relation_id = proposal["proposals"][0]["evidence_relation_ids"][0]
            proposal["proposals"][0].pop("evidence_anchor_ids")
            raw_response["raw_response"] = json.dumps(proposal, ensure_ascii=False)
            raw = root / "raw.jsonl"
            raw.write_text(json.dumps(raw_response, ensure_ascii=False) + "\n", encoding="utf-8")
            validate_raw_responses(
                job_manifest_path=job_manifest,
                requests_path=requests,
                raw_responses_path=raw,
                output_dir=root / "validated",
            )
            record = json.loads((root / "validated/llm_proposals_validated_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "VALID_PROPOSAL_ONLY")
            self.assertEqual(record["proposals"][0]["evidence_anchor_ids_derivation"], "relation_selectable_endpoint_v1")
            self.assertEqual(record["proposals"][0]["evidence_relation_ids"], [relation_id])

    def test_rejects_model_echo_of_envelope_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_manifest, requests, _ = self._job(root)
            request = json.loads(requests.read_text(encoding="utf-8"))
            raw_response = self._raw_response(request)
            proposal = json.loads(raw_response["raw_response"])
            proposal["request_id"] = request["request_id"]
            raw_response["raw_response"] = json.dumps(proposal, ensure_ascii=False)
            raw = root / "raw.jsonl"
            raw.write_text(json.dumps(raw_response, ensure_ascii=False) + "\n", encoding="utf-8")
            validate_raw_responses(
                job_manifest_path=job_manifest,
                requests_path=requests,
                raw_responses_path=raw,
                output_dir=root / "validated",
            )
            record = json.loads((root / "validated/llm_proposals_validated_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "INVALID_UNRESOLVED")
            self.assertIn("unexpected_response_top_level_key:request_id", record["validation_errors"])

    def test_forbidden_promotion_key_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_manifest, requests, _ = self._job(root)
            request = json.loads(requests.read_text(encoding="utf-8"))
            raw = root / "raw.jsonl"
            raw.write_text(json.dumps(self._raw_response(request, forbidden=True), ensure_ascii=False) + "\n", encoding="utf-8")
            result = validate_raw_responses(
                job_manifest_path=job_manifest,
                requests_path=requests,
                raw_responses_path=raw,
                output_dir=root / "validated",
            )
            self.assertEqual(result.valid_response_count, 0)
            self.assertEqual(result.invalid_response_count, 1)
            record = json.loads((root / "validated/llm_proposals_validated_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "INVALID_UNRESOLVED")
            self.assertIn("forbidden_response_key:training_eligible", record["validation_errors"])

    def test_rejects_legacy_free_text_citation_even_when_the_quote_is_plausible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_manifest, requests, _ = self._job(root)
            request = json.loads(requests.read_text(encoding="utf-8"))
            raw_response = self._raw_response(request)
            proposal = json.loads(raw_response["raw_response"])
            proposal["proposals"][0].pop("evidence_anchor_ids")
            proposal["proposals"][0].pop("evidence_relation_ids")
            proposal["proposals"][0]["evidence_anchors"] = [
                {"quoted_text": "Bảng cân đối kế toán"}
            ]
            raw_response["raw_response"] = json.dumps(proposal, ensure_ascii=False)
            raw = root / "raw.jsonl"
            raw.write_text(json.dumps(raw_response, ensure_ascii=False) + "\n", encoding="utf-8")
            validate_raw_responses(
                job_manifest_path=job_manifest,
                requests_path=requests,
                raw_responses_path=raw,
                output_dir=root / "validated",
            )
            record = json.loads((root / "validated/llm_proposals_validated_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "INVALID_UNRESOLVED")
            self.assertIn("proposal_0_legacy_lexical_anchor_only", record["validation_errors"])

    def test_rejects_value_outside_a_hash_bound_statement_enum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = bakeoff_fixture.CertifiedCanonicalBakeoffTests()
            manifest, packets = fixture._inputs(root)
            packet_id = json.loads(packets.read_text(encoding="utf-8").splitlines()[0])["benchmark_packet_id"]
            task_contract = {
                "name": "statement_profile_smoke",
                "fields": ["table_semantics"],
                "instructions": ["Classify only a controlled statement profile, or abstain."],
                "field_value_constraints": {
                    "table_semantics": {
                        "allowed_proposed_values": ["balance_sheet", "income_statement", "cash_flow_statement"]
                    }
                },
                "field_relation_constraints": {"table_semantics": ["heading_scopes_table"]},
            }
            config = fixture._config(
                root,
                manifest,
                packets,
                task_contract=task_contract,
                packet_ids=[packet_id],
            )
            job_dir = root / "job"
            build_bakeoff_job(config, job_dir)
            request = json.loads((job_dir / "llm_bakeoff_requests_v1.jsonl").read_text(encoding="utf-8"))
            raw_response = self._raw_response(request)
            proposal = json.loads(raw_response["raw_response"])
            proposal["proposals"][0]["proposed_value"] = "heading_scopes_table"
            raw_response["raw_response"] = json.dumps(proposal, ensure_ascii=False)
            raw = root / "raw.jsonl"
            raw.write_text(json.dumps(raw_response, ensure_ascii=False) + "\n", encoding="utf-8")
            validate_raw_responses(
                job_manifest_path=job_dir / "bakeoff_job_manifest.json",
                requests_path=job_dir / "llm_bakeoff_requests_v1.jsonl",
                raw_responses_path=raw,
                output_dir=root / "validated",
            )
            record = json.loads((root / "validated/llm_proposals_validated_v1.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "INVALID_UNRESOLVED")
            self.assertIn(
                "proposal_0_proposed_value_not_allowed_for_field:table_semantics",
                record["validation_errors"],
            )
