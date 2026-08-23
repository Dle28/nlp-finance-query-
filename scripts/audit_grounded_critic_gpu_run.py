#!/usr/bin/env python3
"""Fail-closed audit of a downloaded Qwen grounded-critic GPU cohort.

This operational audit verifies the three downloaded Kaggle artifacts against
the immutable packet lineage and, when declared, a locally downloaded minimal
source bundle.  It creates a hash-bound, non-promotable audit manifest; it
never changes a critic result, evidence, route, label, or provenance state.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
RESULT_MANIFEST_SCRIPT = ROOT / "scripts" / "build_grounded_critic_results_manifest_v2.py"
PROTOCOL = "grounded_critic_gpu_run_audit_v2"
REQUIRED_COUNTERS = (
    "invalid_schema_count",
    "external_evidence_reference_count",
    "numeric_invention_count",
    "candidate_selection_count",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def _load_result_manifest_module() -> Any:
    spec = importlib.util.spec_from_file_location("grounded_critic_results_manifest_v2", RESULT_MANIFEST_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load grounded-critic result manifest verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _require_non_promotable_contract(value: object) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise ValueError("Critic GPU result has no source contract")
    expected = {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }
    if any(value.get(key) is not required for key, required in expected.items()):
        raise ValueError("Critic GPU result violates non-promotable source contract")
    return expected


def audit_gpu_run(
    *,
    packets: Path,
    packets_manifest: Path,
    results: Path,
    results_manifest: Path,
    runtime: Path,
    source_bundle_manifest: Path | None = None,
    source_bundle_archive: Path | None = None,
    output: Path,
) -> dict[str, Any]:
    """Verify a downloaded GPU critic cohort and write its audit manifest."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite GPU critic audit: {output}")
    if bool(source_bundle_manifest) != bool(source_bundle_archive):
        raise ValueError("Source bundle manifest and archive must be supplied together")
    packet_manifest = _load_json(packets_manifest)
    packet_sha = sha256_file(packets)
    if packet_sha != ((packet_manifest.get("outputs") or {}).get("packets") or {}).get("sha256"):
        raise ValueError("SHA-256 mismatch for immutable critic packets")
    packets_rows = _load_jsonl(packets)
    packets_by_id = {row.get("question_id"): row for row in packets_rows}
    if len(packets_by_id) != len(packets_rows) or not packets_by_id:
        raise ValueError("Critic packets must have non-empty unique question IDs")

    manifest = _load_json(results_manifest)
    if manifest.get("protocol") != "grounded_critic_results_v2" or manifest.get("schema_version") != 2:
        raise ValueError("Grounded critic result manifest protocol/schema mismatch")
    manifest_inputs = manifest.get("inputs") or {}
    if (
        ((manifest_inputs.get("packets") or {}).get("sha256") != packet_sha)
        or ((manifest_inputs.get("packets_manifest") or {}).get("sha256") != sha256_file(packets_manifest))
    ):
        raise ValueError("Grounded critic result manifest packet lineage mismatch")
    if sha256_file(results) != ((manifest.get("outputs") or {}).get("results") or {}).get("sha256"):
        raise ValueError("SHA-256 mismatch for downloaded critic results")
    _require_non_promotable_contract(manifest.get("source_contract"))

    results_rows = _load_jsonl(results)
    results_by_id = {row.get("question_id"): row for row in results_rows}
    if len(results_by_id) != len(results_rows) or set(results_by_id) != set(packets_by_id):
        raise ValueError("Downloaded critic result ID coverage mismatch")
    for question_id in sorted(packets_by_id):
        response = {key: value for key, value in results_by_id[question_id].items() if key != "source_contract"}
        verifier = _load_result_manifest_module()
        validated = verifier.validate_critic_response(packets_by_id[question_id], response)
        if validated != results_by_id[question_id]:
            raise ValueError(f"Q{question_id}: downloaded critic result fails closed-world validation")

    counts = manifest.get("counts") or {}
    required_count = len(packets_by_id)
    if any(counts.get(key) != required_count for key in ("packet_count", "result_count", "schema_valid_count", "machine_provisional_count")):
        raise ValueError("Grounded critic result counts do not cover every packet")
    if any(counts.get(key) != 0 for key in REQUIRED_COUNTERS):
        raise ValueError("Grounded critic result manifest records a closed-world violation")
    actual_status_counts = dict(sorted(Counter(str(row.get("status")) for row in results_rows).items()))
    if counts.get("status_counts") != actual_status_counts:
        raise ValueError("Grounded critic result status counts differ from downloaded rows")

    runtime_record = _load_json(runtime)
    if runtime_record.get("run_mode") != manifest.get("run_mode"):
        raise ValueError("Critic runtime run_mode does not match result manifest")
    for key in ("runtime", "gpu", "model_revision"):
        if runtime_record.get(key) != manifest.get(key):
            raise ValueError(f"Critic runtime {key} does not match result manifest")
    if runtime_record.get("packets_sha256") != packet_sha or runtime_record.get("results_sha256") != sha256_file(results):
        raise ValueError("Critic runtime hash lineage mismatch")
    if not isinstance(runtime_record.get("gpu_memory_gib"), (int, float)) or runtime_record["gpu_memory_gib"] < 14:
        raise ValueError("Critic GPU runtime has insufficient declared VRAM")

    source_bundle: dict[str, object] | None = None
    declared_source_bundle = manifest_inputs.get("source_bundle")
    if declared_source_bundle is not None:
        if source_bundle_manifest is None or source_bundle_archive is None:
            raise ValueError("Declared source bundle requires locally downloaded manifest and archive")
        verifier = _load_result_manifest_module()
        verified_source_bundle = verifier.require_source_bundle(
            manifest_path=source_bundle_manifest,
            archive_path=source_bundle_archive,
        )
        for key in ("source_tree_sha256", "git_revision"):
            if declared_source_bundle.get(key) != verified_source_bundle.get(key):
                raise ValueError(f"Downloaded critic source bundle {key} mismatch")
        for key in ("manifest", "archive"):
            if (
                (declared_source_bundle.get(key) or {}).get("sha256")
                != (verified_source_bundle.get(key) or {}).get("sha256")
            ):
                raise ValueError(f"Downloaded critic source bundle {key} SHA-256 mismatch")
        # Keep the immutable Kaggle input identity in the audit.  The locally
        # downloaded copy is verification material, not a durable dependency
        # of this artifact (and may live in a temporary directory).
        source_bundle = {
            "declared_input": {
                "manifest": dict(declared_source_bundle["manifest"]),
                "archive": dict(declared_source_bundle["archive"]),
            },
            "verified": {
                "manifest_sha256": verified_source_bundle["manifest"]["sha256"],
                "archive_sha256": verified_source_bundle["archive"]["sha256"],
                "source_tree_sha256": verified_source_bundle["source_tree_sha256"],
                "git_revision": verified_source_bundle.get("git_revision"),
            },
        }
    elif source_bundle_manifest is not None:
        raise ValueError("Local source bundle supplied but critic result declares none")

    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "audit_passed": True,
        "inputs": {
            "packets": {"path": str(packets), "sha256": packet_sha},
            "packets_manifest": {"path": str(packets_manifest), "sha256": sha256_file(packets_manifest)},
            "results": {"path": str(results), "sha256": sha256_file(results)},
            "results_manifest": {"path": str(results_manifest), "sha256": sha256_file(results_manifest)},
            "runtime": {"path": str(runtime), "sha256": sha256_file(runtime)},
            "source_bundle": source_bundle,
        },
        "runtime": {
            key: runtime_record[key]
            for key in ("run_mode", "runtime", "gpu", "gpu_memory_gib", "model", "model_revision")
        },
        "counts": {
            "packet_count": required_count,
            "result_count": len(results_rows),
            "status_counts": actual_status_counts,
            **{key: counts[key] for key in REQUIRED_COUNTERS},
        },
        "source_contract": _require_non_promotable_contract(manifest.get("source_contract")),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packets-manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--results-manifest", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--source-bundle-manifest", type=Path)
    parser.add_argument("--source-bundle-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(audit_gpu_run(**vars(args))["audit_passed"])


if __name__ == "__main__":
    main()
