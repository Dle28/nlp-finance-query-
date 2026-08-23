from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from finance_query.certified_canonical import build_bakeoff_job, run_certified_canonical
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


source_bundle = _load_script("build_ccl_phase3_source_bundle.py")
job_dataset = _load_script("build_ccl_phase3_kaggle_job_dataset.py")
kernel_package = _load_script("prepare_ccl_phase3_kaggle_kernel.py")


class CCLPhase3KagglePackagingTests(unittest.TestCase):
    def test_source_bundle_contains_only_hash_bound_source_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source"
            result = source_bundle.build_source_bundle(repo_root=ROOT, output_dir=output)
            archive = output / source_bundle.ARCHIVE_NAME
            manifest = json.loads((output / source_bundle.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result["outputs"]["archive"]["sha256"], source_bundle.sha256_file(archive))
            declared = [item["path"] for item in manifest["source_bundle"]["files"]]
            self.assertIn("src/finance_query/certified_canonical/model_execution.py", declared)
            self.assertNotIn("data/ViFinQA/questions/questions.jsonl", declared)
            with tarfile.open(archive, mode="r:*") as bundle:
                self.assertEqual([item.name for item in bundle.getmembers()], declared + [source_bundle.IDENTITY_NAME])

    def test_job_dataset_copies_only_declared_non_promotable_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = ccl_fixture.CertifiedCanonicalTests()
            ccl_config, _ = fixture._write_fixture(root)
            ccl_dir = root / "ccl"
            run_certified_canonical(ccl_config, ccl_dir)
            bakeoff = bakeoff_fixture.CertifiedCanonicalBakeoffTests()
            bakeoff_config = bakeoff._config(
                root,
                ccl_dir / "release_manifest.json",
                ccl_dir / "benchmark_packets_v1.jsonl",
            )
            job_dir = root / "job"
            build_bakeoff_job(bakeoff_config, job_dir)
            result = job_dataset.build_dataset(job_dir=job_dir, output_dir=root / "dataset")
            self.assertFalse(result["training_eligible"])
            self.assertFalse(result["promotion_allowed"])
            self.assertFalse(result["contains_model_output"])
            self.assertEqual(
                sorted(path.name for path in (root / "dataset").iterdir()),
                sorted([*job_dataset.REQUIRED_FILES, "DATASET_CONTENTS.json"]),
            )

    def test_kernel_package_is_private_gpu_and_binds_two_private_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kernel"
            package = kernel_package.build_kernel_package(repo_root=ROOT, output_dir=output)
            metadata = json.loads((output / "kernel-metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["id"], kernel_package.KERNEL_ID)
            self.assertEqual(metadata["is_private"], "true")
            self.assertEqual(metadata["enable_gpu"], "true")
            self.assertTrue(metadata["enable_internet"])
            self.assertEqual(metadata["dataset_sources"], list(kernel_package.DATASET_SOURCES))
            self.assertFalse(package["source_contract"]["training_eligible"])
            notebook = json.loads((output / kernel_package.NOTEBOOK_NAME).read_text(encoding="utf-8"))
            source = "\n".join("".join(cell.get("source") or []) if isinstance(cell.get("source"), list) else str(cell.get("source") or "") for cell in notebook["cells"])
            self.assertIn("torch==2.6.0", source)
            self.assertIn("torchvision==0.21.0", source)
            self.assertIn("bitsandbytes==0.45.5", source)
            self.assertIn("--no-deps", source)
            self.assertIn("P100 PyTorch runtime was overwritten", source)
            self.assertIn("'cuda_available': True", source)
            self.assertIn("'--max-new-tokens', '768'", source)

    def test_kernel_package_supports_a_separate_graph_contract_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kernel"
            package = kernel_package.build_kernel_package(
                repo_root=ROOT,
                output_dir=output,
                kernel_id="dungle2810/vifinqa-ccl-phase-3-graph-bake-off-v2",
                kernel_title="ViFinQA CCL Phase 3 Graph Bake-off V2",
                notebook_name="vifinqa_ccl_phase3_graph_bakeoff_v2.ipynb",
                dataset_sources=(
                    "dungle2810/vifinqa-ccl-phase3-source-v2",
                    "dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v2",
                ),
            )
            metadata = json.loads((output / "kernel-metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["id"], package["kernel_id"])
            self.assertEqual(metadata["code_file"], package["notebook"]["name"])
            self.assertEqual(
                metadata["dataset_sources"],
                ["dungle2810/vifinqa-ccl-phase3-source-v2", "dungle2810/vifinqa-ccl-phase3-bakeoff-graph-v2"],
            )
