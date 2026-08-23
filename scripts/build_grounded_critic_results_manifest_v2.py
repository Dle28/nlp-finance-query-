#!/usr/bin/env python3
"""Hash-bind validated closed-world critic dry-run/model results V2."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import tarfile
from typing import Any, Mapping

from finance_query.grounded_critic_protocol import source_contract, validate_critic_response


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


SOURCE_BUNDLE_PROTOCOL = "kaggle_grounded_critic_source_bundle_v1"
SOURCE_BUNDLE_IDENTITY_NAME = "SOURCE_BUNDLE.json"
SOURCE_BUNDLE_CONTRACT_KEYS = (
    "contains_raw_reports",
    "contains_labels",
    "contains_research_artifacts",
    "contains_credentials",
    "eligible_for_evidence",
    "eligible_for_training",
    "eligible_for_submission",
    "eligible_for_promotion",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def require_source_identity(value: object) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Return a strict source identity and its ordered file hashes."""
    if not isinstance(value, dict):
        raise ValueError("Grounded critic source bundle lacks source identity")
    identity = value
    if identity.get("protocol") != SOURCE_BUNDLE_PROTOCOL:
        raise ValueError("Grounded critic source bundle identity has unsupported protocol")
    tree_sha = identity.get("source_tree_sha256")
    identity_base = {key: item for key, item in identity.items() if key != "source_tree_sha256"}
    if not isinstance(tree_sha, str) or len(tree_sha) != 64 or canonical_sha256(identity_base) != tree_sha:
        raise ValueError("Grounded critic source bundle source tree hash is invalid")
    raw_files = identity.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("Grounded critic source bundle has no declared source files")
    files: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for entry in raw_files:
        if not isinstance(entry, dict):
            raise ValueError("Grounded critic source bundle has malformed source entry")
        path = entry.get("path")
        digest = entry.get("sha256")
        relative = Path(path) if isinstance(path, str) else None
        if (
            relative is None
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != path
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or path in seen_paths
        ):
            raise ValueError("Grounded critic source bundle has invalid source path or SHA-256")
        seen_paths.add(path)
        files.append((path, digest))
    return identity, files


def require_source_bundle(
    *, manifest_path: Path, archive_path: Path
) -> dict[str, object]:
    """Validate optional code provenance without confusing it with evidence.

    The source archive is operational provenance only.  It never grants an
    evidence or promotion permission and remains separate from packet lineage.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("protocol") != SOURCE_BUNDLE_PROTOCOL:
        raise ValueError("Grounded critic source bundle has unsupported protocol")
    archive = ((manifest.get("outputs") or {}).get("archive") or {})
    if not isinstance(archive, dict) or archive.get("file_name") != archive_path.name:
        raise ValueError("Grounded critic source bundle archive name is invalid")
    if sha256_file(archive_path) != archive.get("sha256"):
        raise ValueError("SHA-256 mismatch for grounded critic source bundle archive")
    identity, files = require_source_identity(manifest.get("source_bundle"))
    contract = manifest.get("source_contract") or {}
    for key in SOURCE_BUNDLE_CONTRACT_KEYS:
        if contract.get(key) is not False:
            raise ValueError("Grounded critic source bundle violates isolation contract")
    expected_names = [path for path, _ in files] + [SOURCE_BUNDLE_IDENTITY_NAME]
    with tarfile.open(archive_path, mode="r:*") as bundle:
        members = bundle.getmembers()
        if [member.name for member in members] != expected_names or not all(member.isfile() for member in members):
            raise ValueError("Grounded critic source bundle archive members differ from its identity")
        identity_handle = bundle.extractfile(SOURCE_BUNDLE_IDENTITY_NAME)
        if identity_handle is None or json.loads(identity_handle.read()) != identity:
            raise ValueError("Grounded critic source bundle embedded identity differs from manifest")
        for path, digest in files:
            handle = bundle.extractfile(path)
            if handle is None or sha256_bytes(handle.read()) != digest:
                raise ValueError("Grounded critic source bundle member SHA-256 mismatch")
    return {
        "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "archive": {"path": str(archive_path), "sha256": sha256_file(archive_path)},
        "source_tree_sha256": identity["source_tree_sha256"],
        "git_revision": identity.get("git_revision"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packets-manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--run-mode", choices=("dry_run", "qwen14b_4bit"), required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--gpu", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--source-bundle-manifest", type=Path)
    parser.add_argument("--source-bundle-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.source_bundle_manifest) != bool(args.source_bundle_archive):
        raise ValueError("Grounded critic source bundle manifest and archive must be supplied together")
    packet_manifest = json.loads(args.packets_manifest.read_text(encoding="utf-8"))
    if sha256_file(args.packets) != packet_manifest["outputs"]["packets"]["sha256"]:
        raise ValueError("SHA-256 mismatch for critic packets")
    packets = {row["question_id"]: row for row in map(json.loads, args.packets.read_text(encoding="utf-8").splitlines()) if row}
    results = [row for row in map(json.loads, args.results.read_text(encoding="utf-8").splitlines()) if row]
    if {row.get("question_id") for row in results} != set(packets):
        raise ValueError("Critic result question ID coverage mismatch")
    # The runner writes its validator-added source contract.  Revalidate the
    # original model-response fields rather than treating that fixed wrapper as
    # a model-supplied schema field.
    validated = [
        validate_critic_response(
            packets[row["question_id"]],
            {key: value for key, value in row.items() if key != "source_contract"},
        )
        for row in results
    ]
    if any(row.get("provenance") != "machine_provisional" for row in validated):
        raise ValueError("Critic result provenance must remain machine_provisional")
    source_bundle = (
        require_source_bundle(
            manifest_path=args.source_bundle_manifest,
            archive_path=args.source_bundle_archive,
        )
        if args.source_bundle_manifest is not None
        else None
    )
    result = {
        "schema_version": 2,
        "protocol": "grounded_critic_results_v2",
        "run_mode": args.run_mode,
        "runtime": args.runtime,
        "gpu": args.gpu,
        "model_revision": args.model_revision,
        "inputs": {
            "packets": {"path": str(args.packets), "sha256": sha256_file(args.packets)},
            "packets_manifest": {"path": str(args.packets_manifest), "sha256": sha256_file(args.packets_manifest)},
            "source_bundle": source_bundle,
        },
        "outputs": {"results": {"path": str(args.results), "sha256": sha256_file(args.results)}},
        "counts": {
            "packet_count": len(packets),
            "result_count": len(validated),
            "status_counts": dict(sorted(Counter(row["status"] for row in validated).items())),
            "schema_valid_count": len(validated),
            "invalid_schema_count": 0,
            "external_evidence_reference_count": 0,
            "numeric_invention_count": 0,
            "candidate_selection_count": 0,
            "machine_provisional_count": len(validated),
            "reason_code_counts": dict(sorted(Counter(code for row in validated for code in (row.get("feedback") or {}).get("reason_codes") or []).items())),
        },
        "source_contract": source_contract(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
