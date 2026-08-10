"""Typed, hash-bound registry for ViFinQA pipeline artifacts.

The existing sidecar manifests remain the source-specific contracts.  This
registry is a workspace-level index above them: it gives consumers logical
names (``structured_table``, ``evidence_context``, ...) instead of making
business orchestration depend on versioned filenames.  It never changes an
artifact and fails closed when a file or dependency hash differs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


ARTIFACT_REGISTRY_SCHEMA_VERSION = 1
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


class ArtifactRegistryError(ValueError):
    """Raised when a registry cannot prove its workspace artifact lineage."""


def sha256_file(path: Path) -> str:
    """Return the complete SHA-256 of one regular artifact file."""
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identifier(value: object, field_name: str) -> str:
    output = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(output):
        raise ArtifactRegistryError(
            f"{field_name} must match {_IDENTIFIER_RE.pattern}: {output!r}"
        )
    return output


def _relative_artifact_path(workspace_root: Path, path: Path) -> str:
    root = workspace_root.resolve()
    candidate = path.resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError as error:
        raise ArtifactRegistryError("Artifact must reside below the workspace root") from error


def _resolve_artifact_path(workspace_root: Path, relative_path: object) -> Path:
    root = workspace_root.resolve()
    relative = Path(str(relative_path or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ArtifactRegistryError("Artifact path must be a workspace-relative file path")
    candidate = (root / relative).resolve()
    if root not in candidate.parents:
        raise ArtifactRegistryError("Artifact path escapes the workspace root")
    return candidate


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One immutable artifact reference resolved by a logical pipeline name."""

    logical_name: str
    artifact_type: str
    schema_version: int
    relative_path: str
    sha256: str
    dependencies: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dependencies"] = dict(sorted(self.dependencies.items()))
        payload["metadata"] = dict(sorted(self.metadata.items()))
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactRecord":
        try:
            schema_version = int(value["schema_version"])
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactRegistryError("Artifact schema_version must be an integer") from error
        if schema_version < 1:
            raise ArtifactRegistryError("Artifact schema_version must be positive")
        logical_name = _identifier(value.get("logical_name"), "logical_name")
        artifact_type = _identifier(value.get("artifact_type"), "artifact_type")
        sha256 = str(value.get("sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ArtifactRegistryError("Artifact SHA-256 must be a 64-character lowercase digest")
        relative_path = str(value.get("relative_path") or "")
        dependencies = {
            _identifier(name, "dependency logical_name"): str(digest or "").strip().lower()
            for name, digest in dict(value.get("dependencies") or {}).items()
        }
        for digest in dependencies.values():
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ArtifactRegistryError("Dependency SHA-256 must be a 64-character lowercase digest")
        metadata = dict(value.get("metadata") or {})
        return cls(
            logical_name=logical_name,
            artifact_type=artifact_type,
            schema_version=schema_version,
            relative_path=relative_path,
            sha256=sha256,
            dependencies=dependencies,
            metadata=metadata,
        )


@dataclass(slots=True)
class ArtifactRegistry:
    """Validated records for one workspace, without filename-based routing."""

    records: dict[str, ArtifactRecord] = field(default_factory=dict)
    schema_version: int = ARTIFACT_REGISTRY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "records": [self.records[name].to_dict() for name in sorted(self.records)],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactRegistry":
        try:
            version = int(value.get("schema_version") or 0)
        except (TypeError, ValueError) as error:
            raise ArtifactRegistryError("Registry schema_version must be an integer") from error
        if version != ARTIFACT_REGISTRY_SCHEMA_VERSION:
            raise ArtifactRegistryError("Unsupported artifact registry schema version")
        records: dict[str, ArtifactRecord] = {}
        for raw_record in value.get("records") or []:
            record = ArtifactRecord.from_dict(raw_record)
            if record.logical_name in records:
                raise ArtifactRegistryError(f"Duplicate logical artifact: {record.logical_name}")
            records[record.logical_name] = record
        return cls(records=records, schema_version=version)

    def register(
        self,
        *,
        workspace_root: Path,
        logical_name: str,
        artifact_type: str,
        schema_version: int,
        path: Path,
        dependency_names: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
        replace: bool = False,
    ) -> ArtifactRecord:
        """Register an existing file and capture hashes of named dependencies."""
        name = _identifier(logical_name, "logical_name")
        type_name = _identifier(artifact_type, "artifact_type")
        if int(schema_version) < 1:
            raise ArtifactRegistryError("Artifact schema_version must be positive")
        if name in self.records and not replace:
            raise ArtifactRegistryError(f"Artifact already registered: {name}")
        dependencies: dict[str, str] = {}
        for dependency_name in dependency_names:
            dependency = _identifier(dependency_name, "dependency logical_name")
            if dependency == name:
                raise ArtifactRegistryError("Artifact cannot depend on itself")
            parent = self.records.get(dependency)
            if parent is None:
                raise ArtifactRegistryError(
                    f"Artifact {name} references an unregistered dependency: {dependency}"
                )
            dependencies[dependency] = parent.sha256
        record = ArtifactRecord(
            logical_name=name,
            artifact_type=type_name,
            schema_version=int(schema_version),
            relative_path=_relative_artifact_path(workspace_root, path),
            sha256=sha256_file(path),
            dependencies=dependencies,
            metadata=dict(metadata or {}),
        )
        self.records[name] = record
        return record

    def resolve(
        self,
        workspace_root: Path,
        logical_name: str,
        *,
        expected_type: str | None = None,
    ) -> Path:
        """Resolve one logical artifact after checking its type and file hash."""
        name = _identifier(logical_name, "logical_name")
        record = self.records.get(name)
        if record is None:
            raise ArtifactRegistryError(f"Artifact is not registered: {name}")
        if expected_type is not None and record.artifact_type != _identifier(
            expected_type, "expected artifact_type"
        ):
            raise ArtifactRegistryError(
                f"Artifact {name} has type {record.artifact_type}, expected {expected_type}"
            )
        path = _resolve_artifact_path(workspace_root, record.relative_path)
        if sha256_file(path) != record.sha256:
            raise ArtifactRegistryError(f"Artifact content hash differs: {name}")
        return path

    def validate(self, workspace_root: Path) -> None:
        """Validate every file, dependency hash and dependency graph."""
        for name, record in self.records.items():
            self.resolve(workspace_root, name, expected_type=record.artifact_type)
            for dependency_name, expected_sha in record.dependencies.items():
                dependency = self.records.get(dependency_name)
                if dependency is None:
                    raise ArtifactRegistryError(
                        f"Artifact {name} has missing dependency: {dependency_name}"
                    )
                if dependency.sha256 != expected_sha:
                    raise ArtifactRegistryError(
                        f"Artifact {name} dependency hash differs: {dependency_name}"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ArtifactRegistryError("Artifact registry dependency graph contains a cycle")
            if name in visited:
                return
            visiting.add(name)
            for dependency in self.records[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in self.records:
            visit(name)


def load_artifact_registry(path: Path, workspace_root: Path, *, validate: bool = True) -> ArtifactRegistry:
    """Load a registry and, by default, prove it still matches the workspace."""
    if not path.is_file():
        raise FileNotFoundError(path)
    registry = ArtifactRegistry.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if validate:
        registry.validate(workspace_root)
    return registry


def write_artifact_registry(path: Path, workspace_root: Path, registry: ArtifactRegistry) -> None:
    """Atomically persist a validated registry below its workspace root."""
    root = workspace_root.resolve()
    output = path.resolve()
    if output.parent != root:
        raise ArtifactRegistryError("Artifact registry must reside directly in workspace root")
    registry.validate(root)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(registry.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(output)
