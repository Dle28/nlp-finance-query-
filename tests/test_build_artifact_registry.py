from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from finance_query.artifact_registry import load_artifact_registry


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "build_artifact_registry.py"


class BuildArtifactRegistryTests(unittest.TestCase):
    def test_append_preserves_existing_records_and_allows_existing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for name in ("raw.jsonl", "derived.jsonl"):
                (workspace / name).write_text(name, encoding="utf-8")
            initial = [
                sys.executable,
                str(SCRIPT),
                "--workspace-root",
                str(workspace),
                "--artifact",
                "raw:raw:raw.jsonl",
            ]
            subprocess.run(initial, check=True, capture_output=True, text=True)
            append = [
                sys.executable,
                str(SCRIPT),
                "--workspace-root",
                str(workspace),
                "--append",
                "--artifact",
                "derived:derived:derived.jsonl",
                "--depends-on",
                "derived=raw",
            ]
            subprocess.run(append, check=True, capture_output=True, text=True)
            registry = load_artifact_registry(workspace / "artifact_registry_v1.json", workspace)
            self.assertEqual(set(registry.records), {"raw", "derived"})
            self.assertEqual(set(registry.records["derived"].dependencies), {"raw"})

    def test_refresh_can_replace_a_selected_artifacts_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for name in ("raw_a.jsonl", "raw_b.jsonl", "child.jsonl"):
                (workspace / name).write_text(name, encoding="utf-8")
            initial = [
                sys.executable,
                str(SCRIPT),
                "--workspace-root",
                str(workspace),
                "--artifact",
                "raw_a:raw:raw_a.jsonl",
                "--artifact",
                "raw_b:raw:raw_b.jsonl",
                "--artifact",
                "child:derived:child.jsonl",
                "--depends-on",
                "child=raw_a",
            ]
            subprocess.run(initial, check=True, capture_output=True, text=True)
            refresh = [
                sys.executable,
                str(SCRIPT),
                "--workspace-root",
                str(workspace),
                "--refresh",
                "raw_b",
                "--refresh",
                "child",
                "--depends-on",
                "child=raw_b",
            ]
            subprocess.run(refresh, check=True, capture_output=True, text=True)
            registry = load_artifact_registry(workspace / "artifact_registry_v1.json", workspace)
            self.assertEqual(set(registry.records["child"].dependencies), {"raw_b"})


if __name__ == "__main__":
    unittest.main()
