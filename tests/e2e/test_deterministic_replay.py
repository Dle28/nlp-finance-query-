from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
import pytest

from finance_query.e2e.pipeline import (
    GROUNDED_E2E_PROTOCOL,
    GroundedE2EError,
    load_inputs,
    run_grounded_e2e,
)


@pytest.mark.e2e
class GroundedE2ETests(unittest.TestCase):
    def _config(self, root: Path, *, references: bool = False) -> Path:
        paths: dict[str, str] = {}
        for name in (
            "period_packets",
            "period_manifest",
            "route_overlay",
            "route_overlay_manifest",
            "structured_tables",
            "evidence_context",
            "evidence_context_manifest",
            "metric_registry",
        ):
            path = root / f"{name}.json"
            path.write_text(name, encoding="utf-8")
            paths[name] = path.name
        if references:
            for name, contents in (
                ("expected_bindings", "bindings"),
                ("expected_execution", "execution"),
                ("expected_evidence_bindings", "evidence bindings"),
                ("expected_answer_certificates", "answer certificates"),
                ("expected_authorization_readiness", "authorization readiness"),
            ):
                path = root / f"{name}.jsonl"
                path.write_text(contents, encoding="utf-8")
                paths[name] = path.name
        config = root / "grounded.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "protocol": GROUNDED_E2E_PROTOCOL,
                    "run_name": "vifinqa-grounded-e2e-replay-v1",
                    "paths": paths,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return config

    @staticmethod
    def _fake_bindings(**kwargs: object) -> dict:
        output = Path(kwargs["output"])
        output.write_text("bindings", encoding="utf-8")
        manifest = output.with_suffix(".manifest.json")
        manifest.write_text("bindings manifest", encoding="utf-8")
        return {"counts": {"binding_packet_status_counts": {"binding_ready": 1}}}

    @staticmethod
    def _fake_execution(**kwargs: object) -> dict:
        output = Path(kwargs["output"])
        output.write_text("execution", encoding="utf-8")
        manifest = output.with_suffix(".manifest.json")
        manifest.write_text("execution manifest", encoding="utf-8")
        telemetry = Path(kwargs["telemetry_output"])
        telemetry.write_text("telemetry", encoding="utf-8")
        telemetry.with_suffix(".manifest.json").write_text(
            "telemetry manifest", encoding="utf-8"
        )
        return {"counts": {"execution_status_counts": {"execution_replay_ready": 1}}}

    @staticmethod
    def _fake_cell_tokens(**kwargs: object) -> dict:
        registry = Path(kwargs["registry_output"])
        public = Path(kwargs["public_view_output"])
        registry.write_text("cell token registry", encoding="utf-8")
        public.write_text("cell token public view", encoding="utf-8")
        registry.with_suffix(".manifest.json").write_text(
            "cell token manifest", encoding="utf-8"
        )
        return {"counts": {"token_count": 1}}

    @staticmethod
    def _fake_authorization(**kwargs: object) -> dict:
        evidence_bindings = Path(kwargs["evidence_bindings_output"])
        answer_certificates = Path(kwargs["answer_certificates_output"])
        evidence_bindings.write_text("evidence bindings", encoding="utf-8")
        answer_certificates.write_text("answer certificates", encoding="utf-8")
        manifest = answer_certificates.with_suffix(".manifest.json")
        manifest.write_text("authorization manifest", encoding="utf-8")
        readiness = answer_certificates.with_name("authorization_readiness_v1.json")
        readiness.write_text("authorization readiness", encoding="utf-8")
        return {
            "manifest_path": str(manifest),
            "readiness_path": str(readiness),
            "counts": {
                "evidence_binding_status_counts": {"BLOCKED": 1},
                "answer_certificate_status_counts": {"ABSTAIN": 1},
            },
        }

    def test_run_creates_new_hash_bound_answer_capable_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = load_inputs(self._config(root, references=True))
            before = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in inputs.required_paths().values()
            }
            with patch("finance_query.e2e.pipeline.build_exact_cell_bindings", self._fake_bindings), patch(
                "finance_query.e2e.pipeline.materialize_numeric_cell_tokens", self._fake_cell_tokens
            ), patch(
                "finance_query.e2e.pipeline.run_grounded_execution", self._fake_execution
            ), patch(
                "finance_query.e2e.pipeline.materialize_authorization_replay", self._fake_authorization
            ):
                result = run_grounded_e2e(inputs, output_dir=root / "run")

            self.assertEqual(result["run_status"], "complete_answer_capable")
            self.assertEqual(result["run_name"], "vifinqa-grounded-e2e-replay-v1")
            self.assertEqual(len(result["run_id"]), 64)
            self.assertEqual(
                result["reproducibility"],
                {
                    "bindings_match": True,
                    "execution_match": True,
                    "evidence_bindings_match": True,
                    "answer_certificates_match": True,
                    "authorization_readiness_match": True,
                },
            )
            receipt = json.loads((root / "run" / "grounded_e2e_run_v1.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["source_contract"]["submission_eligible"], False)
            self.assertNotIn("research_only", receipt["source_contract"])
            self.assertTrue(receipt["source_contract"]["answer_output_allowed"])
            self.assertEqual(receipt["technical_readiness"]["execution_replay_ready_count"], 1)
            self.assertEqual(receipt["technical_readiness"]["answer_authority"], False)
            self.assertEqual(receipt["technical_readiness"]["strict_answer_authority"], False)
            self.assertEqual(receipt["technical_readiness"]["strict_answer_authorized_count"], 0)
            self.assertEqual(
                receipt["technical_readiness"]["best_effort_candidate_authority"],
                False,
            )
            self.assertEqual(receipt["technical_readiness"]["answer_output_allowed"], True)
            self.assertEqual(receipt["technical_readiness"]["answer_count"], 0)
            self.assertEqual(receipt["technical_readiness"]["abstain_count"], 1)
            self.assertEqual(receipt["outputs"]["authorization"]["counts"]["answer_certificate_status_counts"], {"ABSTAIN": 1})
            after = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in inputs.required_paths().values()
            }
            self.assertEqual(before, after)

    def test_config_rejects_an_untrackable_run_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root)
            payload = yaml.safe_load(config.read_text(encoding="utf-8"))
            payload["run_name"] = "Not trackable"
            config.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(GroundedE2EError, "run_name"):
                load_inputs(config)

    def test_replay_rejects_authorization_reference_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = load_inputs(self._config(root, references=True))
            assert inputs.expected_answer_certificates is not None
            inputs.expected_answer_certificates.write_text("stale certificates", encoding="utf-8")
            with patch("finance_query.e2e.pipeline.build_exact_cell_bindings", self._fake_bindings), patch(
                "finance_query.e2e.pipeline.materialize_numeric_cell_tokens", self._fake_cell_tokens
            ), patch(
                "finance_query.e2e.pipeline.run_grounded_execution", self._fake_execution
            ), patch(
                "finance_query.e2e.pipeline.materialize_authorization_replay", self._fake_authorization
            ), self.assertRaisesRegex(GroundedE2EError, "diverged"):
                run_grounded_e2e(inputs, output_dir=root / "run")

    def test_replay_refuses_existing_output_or_changed_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = load_inputs(self._config(root))
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(FileExistsError):
                run_grounded_e2e(inputs, output_dir=existing)

            def mutating_bindings(**kwargs: object) -> dict:
                inputs.period_packets.write_text("mutated", encoding="utf-8")
                return self._fake_bindings(**kwargs)

            with patch("finance_query.e2e.pipeline.build_exact_cell_bindings", mutating_bindings), patch(
                "finance_query.e2e.pipeline.materialize_numeric_cell_tokens", self._fake_cell_tokens
            ), patch(
                "finance_query.e2e.pipeline.run_grounded_execution", self._fake_execution
            ), patch(
                "finance_query.e2e.pipeline.materialize_authorization_replay", self._fake_authorization
            ), self.assertRaisesRegex(GroundedE2EError, "changed an input artifact"):
                run_grounded_e2e(inputs, output_dir=root / "run")
