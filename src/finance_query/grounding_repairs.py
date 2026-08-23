"""Materialize reviewed grounding repairs as a non-mutating overlay."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


GROUNDING_REPAIRS_PROTOCOL = "grounding_repairs_overlay_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Manifest must be a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }


def _require_hash(path: Path, expected: object, label: str) -> None:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")


def _queue_sources(manifest: Mapping[str, Any]) -> dict[str, str]:
    outputs = manifest.get("outputs") or {}
    return {
        "metadata": str((outputs.get("metadata_queue") or {}).get("sha256") or ""),
        "routing": str((outputs.get("routing_queue") or {}).get("sha256") or ""),
        "period": str((outputs.get("period_queue") or {}).get("sha256") or ""),
    }


def _approved_overlay(queue_name: str, item: Mapping[str, Any], queue_sha: str) -> dict[str, Any]:
    decision = item.get("decision_contract") or {}
    provenance = decision.get("decision_provenance")
    patch = decision.get("proposed_patch")
    required_patch = {"scope", "old_value", "proposed_value", "reason", "source_authority", "source_coordinates", "before_gate_vector", "after_gate_vector"}
    if decision.get("decision") != "accept_repair":
        raise ValueError("Internal approved overlay called for non-approved decision")
    if not isinstance(provenance, Mapping) or provenance.get("reviewer_type") not in {"human_verified", "independent_ai_source_review"}:
        raise ValueError("Approved repair requires truthful independent reviewer provenance")
    if provenance.get("reviewer_type") == "human_verified" and not provenance.get("reviewer_id"):
        raise ValueError("human_verified repair requires a reviewer identity")
    if not decision.get("reviewer_id") or not decision.get("reviewed_at") or not decision.get("source_coordinates_checked"):
        raise ValueError("Approved repair requires reviewer, timestamp, and checked coordinates")
    if not isinstance(patch, Mapping) or not required_patch.issubset(patch):
        raise ValueError("Approved repair proposed_patch is incomplete")
    if str(patch.get("scope")) not in {"metadata", "taxonomy", "routing", "period_anchor"}:
        raise ValueError("Approved repair has unsupported scope")
    identity = item.get("immutable_source_identity") or {}
    question_id = identity.get("question_id")
    if question_id in {368, 369, 551} and str(patch.get("scope")) != "metadata":
        # Any scope repair for these canaries must explicitly bind scope in the
        # reviewed patch; a routing/period patch cannot launder an absent scope.
        raise ValueError("Blocked scope canary requires an explicit metadata scope repair")
    return {
        "schema_version": 1,
        "protocol": GROUNDING_REPAIRS_PROTOCOL,
        "queue_name": queue_name,
        "source_queue_sha256": queue_sha,
        "source_queue_item_sha256": _canonical_sha(item),
        "immutable_source_identity": identity,
        "reviewer_provenance": dict(provenance),
        "reviewer_id": decision["reviewer_id"],
        "reviewed_at": decision["reviewed_at"],
        "scope": patch["scope"],
        "old_value": patch["old_value"],
        "proposed_value": patch["proposed_value"],
        "reason": patch["reason"],
        "source_authority": patch["source_authority"],
        "source_coordinates": patch["source_coordinates"],
        "before_gate_vector": patch["before_gate_vector"],
        "after_gate_vector": patch["after_gate_vector"],
        "materialization": "overlay_only",
        "source_contract": _contract(),
    }


def materialize_grounding_repairs(
    *, metadata_queue_path: Path, routing_queue_path: Path, period_queue_path: Path,
    adjudication_manifest_path: Path, output_dir: Path,
) -> dict[str, Any]:
    manifest = _read_json(adjudication_manifest_path)
    expected = _queue_sources(manifest)
    for name, path in (("metadata", metadata_queue_path), ("routing", routing_queue_path), ("period", period_queue_path)):
        _require_hash(path, expected[name], f"{name} queue")
    approved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for name, path in (("metadata", metadata_queue_path), ("routing", routing_queue_path), ("period", period_queue_path)):
        for item in _read_jsonl(path):
            decision = (item.get("decision_contract") or {}).get("decision")
            if decision == "accept_repair":
                approved.append(_approved_overlay(name, item, expected[name]))
            else:
                unresolved.append({
                    "schema_version": 1,
                    "protocol": GROUNDING_REPAIRS_PROTOCOL,
                    "queue_name": name,
                    "source_queue_sha256": expected[name],
                    "source_queue_item_sha256": _canonical_sha(item),
                    "immutable_source_identity": item.get("immutable_source_identity") or {},
                    "decision": decision,
                    "reason_code": "BLANK_DECISION" if decision is None else "REJECTED_OR_UNCERTAIN",
                    "eligible_for_materialization": False,
                    "source_contract": _contract(),
                })
    approved.sort(key=lambda row: (str(row["scope"]), str(row["queue_name"]), json.dumps(row["immutable_source_identity"], sort_keys=True)))
    unresolved.sort(key=lambda row: (str(row["queue_name"]), json.dumps(row["immutable_source_identity"], sort_keys=True)))
    output_dir.mkdir(parents=True, exist_ok=True)
    approved_path = output_dir / "approved_grounding_repairs_v1.jsonl"
    unresolved_path = output_dir / "rejected_or_unresolved_repairs_v1.jsonl"
    manifest_path = output_dir / "grounding_repairs_v1.manifest.json"
    _write_jsonl(approved_path, approved)
    _write_jsonl(unresolved_path, unresolved)
    result = {
        "schema_version": 1,
        "protocol": GROUNDING_REPAIRS_PROTOCOL,
        "inputs": {
            "adjudication_manifest": {"path": str(adjudication_manifest_path), "sha256": sha256_file(adjudication_manifest_path)},
            "metadata_queue": {"path": str(metadata_queue_path), "sha256": sha256_file(metadata_queue_path)},
            "routing_queue": {"path": str(routing_queue_path), "sha256": sha256_file(routing_queue_path)},
            "period_queue": {"path": str(period_queue_path), "sha256": sha256_file(period_queue_path)},
        },
        "outputs": {
            "approved": {"path": str(approved_path), "sha256": sha256_file(approved_path)},
            "unresolved": {"path": str(unresolved_path), "sha256": sha256_file(unresolved_path)},
        },
        "counts": {"approved": len(approved), "rejected_or_unresolved": len(unresolved)},
        "source_contract": _contract(),
    }
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
