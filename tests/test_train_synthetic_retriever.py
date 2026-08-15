from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from finance_query.synthetic_curriculum import (
    build_synthetic_curriculum,
    manifest_payload,
    sha256_file,
)


fixture_spec = importlib.util.spec_from_file_location(
    "synthetic_curriculum_fixture",
    Path(__file__).parent / "test_synthetic_curriculum.py",
)
fixture_mod = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture_mod)
ASSETS = fixture_mod.ASSETS


spec = importlib.util.spec_from_file_location(
    "train_synthetic_retriever",
    Path(__file__).parents[1] / "scripts" / "train_synthetic_retriever_v1.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TrainSyntheticRetrieverTests(unittest.TestCase):
    def test_training_environment_disables_wandb_before_library_import(self):
        previous = {key: os.environ.get(key) for key in ("WANDB_DISABLED", "WANDB_MODE")}
        try:
            mod.configure_training_environment(device="cuda:0", gpu_id="0")
            self.assertEqual(os.environ["WANDB_DISABLED"], "true")
            self.assertEqual(os.environ["WANDB_MODE"], "disabled")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_manifest_and_rows_fail_closed_on_hash_or_replay_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tables = root / "tables.jsonl"
            tables.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ASSETS),
                encoding="utf-8",
            )
            examples, summary = build_synthetic_curriculum(
                ASSETS,
                source_tables_sha256=sha256_file(tables),
                max_examples=100,
                max_cells_per_table=4,
                hard_negatives_per_example=5,
                min_hard_negatives=2,
            )
            curriculum = root / "curriculum.jsonl"
            curriculum.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in examples),
                encoding="utf-8",
            )
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    manifest_payload(
                        tables_path=tables,
                        tables_sha256=sha256_file(tables),
                        examples_path=curriculum,
                        examples_sha256=sha256_file(curriculum),
                        generation_config={},
                        summary=summary,
                    )
                ),
                encoding="utf-8",
            )
            loaded = mod.validate_manifest(curriculum, manifest, tables)
            rows = mod.load_training_rows(
                curriculum,
                split="train",
                expected_tables_sha256=loaded["input"]["tables_sha256"],
            )
            self.assertGreater(len(rows), 0)

            examples[0]["execution_verification"]["answer_decimal"] = "999"
            curriculum.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in examples),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "hash differs"):
                mod.validate_manifest(curriculum, manifest, tables)


if __name__ == "__main__":
    unittest.main()
