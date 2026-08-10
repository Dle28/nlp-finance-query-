import json
import tempfile
import unittest
from pathlib import Path

from finance_query.artifact_registry import (
    ArtifactRegistry,
    ArtifactRegistryError,
    load_artifact_registry,
    write_artifact_registry,
)


class ArtifactRegistryTests(unittest.TestCase):
    def test_registry_resolves_hash_bound_logical_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            raw = workspace / "raw.jsonl"
            structured = workspace / "structured.jsonl"
            raw.write_text("raw\n", encoding="utf-8")
            structured.write_text("structured\n", encoding="utf-8")
            registry = ArtifactRegistry()
            registry.register(
                workspace_root=workspace,
                logical_name="raw_table",
                artifact_type="raw_table",
                schema_version=1,
                path=raw,
            )
            registry.register(
                workspace_root=workspace,
                logical_name="structured_table",
                artifact_type="structured_table",
                schema_version=2,
                path=structured,
                dependency_names=["raw_table"],
            )
            output = workspace / "artifact_registry_v1.json"
            write_artifact_registry(output, workspace, registry)
            loaded = load_artifact_registry(output, workspace)
            self.assertEqual(
                loaded.resolve(workspace, "structured_table", expected_type="structured_table"),
                structured,
            )
            self.assertEqual(
                loaded.records["structured_table"].dependencies.keys(), {"raw_table"}
            )

    def test_registry_rejects_tampered_artifact_and_dependency_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            raw = workspace / "raw.jsonl"
            child = workspace / "child.jsonl"
            raw.write_text("raw\n", encoding="utf-8")
            child.write_text("child\n", encoding="utf-8")
            registry = ArtifactRegistry()
            registry.register(
                workspace_root=workspace,
                logical_name="raw_table",
                artifact_type="raw_table",
                schema_version=1,
                path=raw,
            )
            registry.register(
                workspace_root=workspace,
                logical_name="evidence_context",
                artifact_type="evidence_context",
                schema_version=3,
                path=child,
                dependency_names=["raw_table"],
            )
            output = workspace / "artifact_registry_v1.json"
            write_artifact_registry(output, workspace, registry)
            raw.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ArtifactRegistryError, "content hash differs"):
                load_artifact_registry(output, workspace)

    def test_registry_rejects_path_escape_and_unknown_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            path = workspace / "one.jsonl"
            path.write_text("one\n", encoding="utf-8")
            registry = ArtifactRegistry()
            with self.assertRaisesRegex(ArtifactRegistryError, "unregistered dependency"):
                registry.register(
                    workspace_root=workspace,
                    logical_name="direct_evidence",
                    artifact_type="direct_evidence",
                    schema_version=1,
                    path=path,
                    dependency_names=["missing"],
                )
            payload = {
                "schema_version": 1,
                "records": [
                    {
                        "logical_name": "raw_table",
                        "artifact_type": "raw_table",
                        "schema_version": 1,
                        "relative_path": "../outside.jsonl",
                        "sha256": "0" * 64,
                    }
                ],
            }
            output = workspace / "artifact_registry_v1.json"
            output.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ArtifactRegistryError, "workspace-relative"):
                load_artifact_registry(output, workspace)
