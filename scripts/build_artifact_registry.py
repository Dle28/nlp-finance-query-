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
        help=(
            "Logical dependency edges; parents must be declared before the child. "
            "With --refresh, replaces dependencies for a selected artifact."
        ),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate an existing registry without writing it.",
    )
    parser.add_argument(
        "--refresh",
        action="append",
        default=[],
        metavar="LOGICAL_NAME",
        help=(
            "Refresh one existing artifact while preserving its declared type, "
            "schema, and metadata. Repeatable; --depends-on may replace the "
            "dependencies of a selected artifact."
        ),
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append new --artifact declarations to a validated existing registry.",
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
        if args.artifact or args.schema_version or args.depends_on or args.refresh or args.append:
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
    if args.refresh:
        if args.artifact or args.schema_version or args.append:
            raise ArtifactRegistryError(
                "--refresh cannot be combined with --artifact, --schema-version or --append"
            )
        # A dashboard/materialized diagnostic can legitimately be regenerated
        # from unchanged hash-bound inputs. Do not permit a parent refresh here:
        # children would retain a stale dependency hash and write() would reject it.
        registry = load_artifact_registry(output, workspace, validate=False)
        refresh_names = [str(value or "").strip() for value in args.refresh]
        if any(not name for name in refresh_names):
            raise ArtifactRegistryError("--refresh requires a non-empty logical_name")
        if len(set(refresh_names)) != len(refresh_names):
            raise ArtifactRegistryError("Each --refresh logical_name may be declared once")
        refresh_set = set(refresh_names)
        dependency_overrides = parse_dependencies(args.depends_on)
        unknown_override_names = sorted(set(dependency_overrides) - refresh_set)
        if unknown_override_names:
            raise ArtifactRegistryError(
                "--depends-on with --refresh may name only selected artifacts: "
                + ", ".join(unknown_override_names)
            )
        for name in refresh_names:
            if name not in registry.records:
                raise ArtifactRegistryError(f"Artifact is not registered: {name}")
        effective_dependencies = {
            name: dependency_overrides.get(name, list(record.dependencies))
            for name, record in registry.records.items()
        }
        for name, parents in dependency_overrides.items():
            unknown_parents = sorted(set(parents) - set(registry.records))
            if unknown_parents:
                raise ArtifactRegistryError(
                    f"Refreshed artifact {name} has unregistered dependencies: "
                    + ", ".join(unknown_parents)
                )
            if name in parents:
                raise ArtifactRegistryError("An artifact cannot depend on itself")
        for name in refresh_names:
            stale_children = sorted(
                child_name
                for child_name, parents in effective_dependencies.items()
                if name in parents and child_name not in refresh_set
            )
            if stale_children:
                raise ArtifactRegistryError(
                    f"Cannot refresh {name}; also refresh dependent artifacts: "
                    + ", ".join(stale_children)
                )

        # Refresh parents before their selected children so a child's renewed
        # dependency hash captures the refreshed parent, never a stale digest.
        pending = set(refresh_names)
        while pending:
            ready = sorted(
                name
                for name in pending
                if not (set(effective_dependencies[name]) & pending)
            )
            if not ready:
                raise ArtifactRegistryError("Selected refresh artifacts contain a dependency cycle")
            for name in ready:
                record = registry.records[name]
                registry.register(
                    workspace_root=workspace,
                    logical_name=record.logical_name,
                    artifact_type=record.artifact_type,
                    schema_version=record.schema_version,
                    path=workspace / record.relative_path,
                    dependency_names=effective_dependencies[name],
                    metadata=record.metadata,
                    replace=True,
                )
                pending.remove(name)
        write_artifact_registry(output, workspace, registry)
        print(
            json.dumps(
                {
                    "registry": str(output),
                    "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
                    "artifact_count": len(registry.records),
                    "refreshed": sorted(refresh_names),
                },
                ensure_ascii=False,
            )
        )
        return
    if not args.artifact:
        raise ArtifactRegistryError("At least one --artifact declaration is required")
    versions = parse_schema_versions(args.schema_version)
    dependencies = parse_dependencies(args.depends_on)
    if args.append:
        registry = load_artifact_registry(output, workspace, validate=True)
    else:
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
