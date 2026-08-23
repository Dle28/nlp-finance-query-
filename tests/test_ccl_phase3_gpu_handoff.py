from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from finance_query.certified_canonical import build_bakeoff_job, run_certified_canonical, validate_raw_responses
import tests.test_certified_canonical as ccl_fixture
import tests.test_certified_canonical_bakeoff as bakeoff_fixture


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


downloader = _load_script("download_ccl_phase3_kernel_artifacts.py")
auditor = _load_script("audit_ccl_phase3_gpu_run.py")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Output:
    def __init__(self, name: str, url: str) -> None:
        self.file_name = name
        self.url = url


class CCLPhase3GPUHandoffTests(unittest.TestCase):
    def test_downloader_requires_one_url_for_every_expected_output(self) -> None:
        files = [_Output(f"nested/{name}", f"https://example.test/{name}") for name in downloader.EXPECTED_NAMES]
        selected = downloader.select_expected_urls(files)
        self.assertEqual(set(selected), downloader.EXPECTED_NAMES)
        with self.assertRaisesRegex(ValueError, "missing expected"):
            downloader.select_expected_urls(files[:-1])

    def test_audit_accepts_route_scoped_invalid_proposals_without_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ccl = ccl_fixture.CertifiedCanonicalTests()
            ccl_config, _ = ccl._write_fixture(root)
            ccl_dir = root / "ccl"
            run_certified_canonical(ccl_config, ccl_dir)
            bakeoff = bakeoff_fixture.CertifiedCanonicalBakeoffTests()
            config = bakeoff._config(root, ccl_dir / "release_manifest.json", ccl_dir / "benchmark_packets_v1.jsonl")
            job_dir = root / "job"
            build_bakeoff_job(config, job_dir)
            job_manifest = job_dir / "bakeoff_job_manifest.json"
            requests = job_dir / "llm_bakeoff_requests_v1.jsonl"
            request = json.loads(requests.read_text(encoding="utf-8"))
            route_id = request["route_id"]

            artifacts = root / "artifacts"
            artifacts.mkdir()
            raw_path = artifacts / "llm_raw_responses_v1.jsonl"
            raw_path.write_text(
                json.dumps(
                    {
                        "request_id": request["request_id"],
                        "internal_table_uid": request["internal_table_uid"],
                        "route_id": route_id,
                        "model_id": request["model_id"],
                        "model_revision": request["model_revision"],
                        "raw_response": "not valid JSON",
                        "training_eligible": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report_path = artifacts / "model_execution_report.json"
            report_path.write_text("{}\n", encoding="utf-8")
            job = json.loads(job_manifest.read_text(encoding="utf-8"))
            route = next(item for item in job["routes"] if item["route_id"] == route_id)
            execution = {
                "protocol": "vifinqa_ccl_phase3_bakeoff_v1",
                "run_status": "model_execution_complete_responses_unvalidated",
                "inputs": {
                    "job_manifest": {"sha256": _sha(job_manifest)},
                    "requests": {"sha256": _sha(requests)},
                },
                "route": route,
                "load_in_4bit": True,
                "generation_contract": {
                    "do_sample": False,
                    "max_new_tokens": 768,
                    "qwen3_thinking_enabled": False,
                },
                "runtime": {
                    "cuda_available": True,
                    "gpu_name": "Test GPU",
                    "gpu_compute_capability": [6, 0],
                    "torch_version": "2.6.0+cu118",
                    "torch_cuda_version": "11.8",
                    "torch_arch_list": ["sm_60"],
                },
                "outputs": {
                    "llm_raw_responses_v1.jsonl": {"sha256": _sha(raw_path)},
                    "model_execution_report.json": {"sha256": _sha(report_path)},
                },
                "training_eligible": False,
                "certification_allowed": False,
            }
            execution_path = artifacts / "model_execution_manifest.json"
            execution_path.write_text(json.dumps(execution), encoding="utf-8")

            validated_dir = root / "validated"
            validate_raw_responses(
                job_manifest_path=job_manifest,
                requests_path=requests,
                raw_responses_path=raw_path,
                output_dir=validated_dir,
                route_id=route_id,
            )
            for name in (
                "llm_proposals_validated_v1.jsonl",
                "llm_response_validation_report.json",
                "proposal_validation_manifest.json",
            ):
                shutil.copyfile(validated_dir / name, artifacts / name)
            source_manifest = root / "source.json"
            source_manifest.write_text(json.dumps({"source_bundle": {"source_tree_sha256": "source-tree"}}), encoding="utf-8")
            receipt = {
                "training_eligible": False,
                "certification_allowed": False,
                "gpu": {"name": "Test GPU", "vram_gib": 16},
                "model_runtime": execution["runtime"],
                "source_tree_sha256": "source-tree",
                "job_manifest_sha256": _sha(job_manifest),
                "qwen_execution_manifest_sha256": _sha(execution_path),
                "qwen_validation_manifest_sha256": _sha(artifacts / "proposal_validation_manifest.json"),
            }
            (artifacts / "ccl_phase3_kaggle_receipt_v1.json").write_text(json.dumps(receipt), encoding="utf-8")

            result = auditor.audit_gpu_run(
                job_manifest=job_manifest,
                requests=requests,
                source_manifest=source_manifest,
                artifact_dir=artifacts,
                output=root / "audit.json",
                route_id=route_id,
            )
            self.assertTrue(result["audit_passed"])
            self.assertEqual(result["counts"]["valid_proposal_only_count"], 0)
            self.assertEqual(result["counts"]["invalid_or_missing_unresolved_count"], 1)
            self.assertFalse(result["training_eligible"])
