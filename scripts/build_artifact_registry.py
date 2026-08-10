#!/usr/bin/env python3
"""Create or validate a typed, hash-bound workspace artifact registry.

The registry deliberately receives logical declarations from the caller. It
does not infer business meaning from filenames such as ``*_v4``. Existing
sidecar manifests remain validated by their current producers/consumers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.artifact_registry import (  # noqa: E402
    ARTIFACT_REGISTRY_SCHEMA_VERSION,
    ArtifactRegistry,
    ArtifactRegistryError,
    load_artifact_registry,
    write_artifact_registry,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Registry path; defaults to <workspace-root>/artifact_registry_v1.json.",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="LOGICAL:TYPE:PATH",
        help="Register one workspace-relative artifact; repeatable.",
    )
    parser.add_argument(
        "--schema-version",
        action="append",
        default=[],
        metavar="LOGICAL=VERSION",
        help="Schema version for one logical artifact; defaults to 1.",
    )
    parser.add_argument(
        "--depends-on",
        action="append",
        default=[],
        metavar="CHILD=PARENT[,PARENT...]",
        help="Logical dependency edges; parents must be declared before the child.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate an existing registry without writing it.",
    )
    return parser.parse_args()


def parse_artifact(value: str) -> tuple[str, str, str]:
    logical_name, separator, remainder = value.partition(":")
    artifact_type, separator2, relative_path = remainder.partition(":")
    if not separator or not separator2 or not logical_name or not artifact_type or not relative_path:
        raise ArtifactRegistryError("--artifact must be LOGICAL:TYPE:PATH")
    return logical_name, artifact_type, relative_path


def parse_schema_versions(values: list[str]) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        name, separator, raw_version = value.partition("=")
        if not separator or not name:
            raise ArtifactRegistryError("--schema-version must be LOGICAL=VERSION")
        try:
            version = int(raw_version)
        except ValueError as error:
            raise ArtifactRegistryError("Artifact schema version must be an integer") from error
        if version < 1 or name in output:
            raise ArtifactRegistryError("Schema version must be positive and declared once")
        output[name] = version
    return output


def parse_dependencies(values: list[str]) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for value in values:
        child, separator, raw_parents = value.partition("=")
        parents = [parent.strip() for parent in raw_parents.split(",") if parent.strip()]
        if not separator or not child or not parents or child in output:
            raise ArtifactRegistryError("--depends-on must be CHILD=PARENT[,PARENT...]")
        output[child] = parents
    return output


def main() -> None:
    args = parse_args()
    workspace = args.workspace_root.resolve()
    requested_output = args.output or workspace / "artifact_registry_v1.json"
    output = (
        requested_output.resolve()
        if requested_output.is_absolute()
        else (workspace / requested_output).resolve()
    )
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    if args.validate_only:
        if args.artifact or args.schema_version or args.depends_on:
            raise ArtifactRegistryError("--validate-only cannot be combined with registry declarations")
        registry = load_artifact_registry(output, workspace, validate=True)
        print(
            json.dumps(
                {
                    "registry": str(output),
                    "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
                    "artifact_count": len(registry.records),
                    "valid": True,
                },
                ensure_ascii=False,
            )
        )
        return
    if not args.artifact:
        raise ArtifactRegistryError("At least one --artifact declaration is required")
    versions = parse_schema_versions(args.schema_version)
    dependencies = parse_dependencies(args.depends_on)
    registry = ArtifactRegistry()
    for raw_artifact in args.artifact:
        name, artifact_type, relative_path = parse_artifact(raw_artifact)
        registry.register(
            workspace_root=workspace,
            logical_name=name,
            artifact_type=artifact_type,
            schema_version=versions.get(name, 1),
            path=workspace / relative_path,
            dependency_names=dependencies.get(name, []),
            metadata={"registration_source": "explicit_cli_declaration_v1"},
        )
    unknown_version_names = sorted(set(versions) - set(registry.records))
    unknown_dependency_children = sorted(set(dependencies) - set(registry.records))
    if unknown_version_names or unknown_dependency_children:
        raise ArtifactRegistryError(
            "Registry declarations reference unknown artifacts: "
            + ", ".join(unknown_version_names + unknown_dependency_children)
        )
    write_artifact_registry(output, workspace, registry)
    print(
        json.dumps(
            {
                "registry": str(output),
                "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
                "artifact_count": len(registry.records),
                "logical_artifacts": sorted(registry.records),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
