"""V3 exact bindings: add hash-bound source-title unit anchors to immutable V2."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .binding_conflict_workbench import context_unit_anchor_candidate


PROTOCOL = "exact_cell_unit_binding_candidates_v3"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _uid_index(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _rows(path):
        uid = str(row.get("internal_table_uid") or "")
        if not uid or uid in result:
            raise ValueError(f"{path} requires unique internal_table_uid")
        result[uid] = row
    return result


def upgrade_context_unit_bindings(
    *,
    bindings_v2: Path,
    bindings_v2_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    output: Path,
) -> dict[str, Any]:
    v2_manifest = json.loads(bindings_v2_manifest.read_text(encoding="utf-8"))
    context_manifest = json.loads(evidence_context_manifest.read_text(encoding="utf-8"))
    if ((v2_manifest.get("outputs") or {}).get("bindings") or {}).get("sha256") != sha256_file(bindings_v2):
        raise ValueError("SHA-256 mismatch for V2 bindings")
    if ((v2_manifest.get("inputs") or {}).get("structured_tables") or {}).get("sha256") != sha256_file(structured_tables):
        raise ValueError("V2 bindings are stale for structured tables")
    if context_manifest.get("sidecar_sha256") != sha256_file(evidence_context):
        raise ValueError("SHA-256 mismatch for evidence context")
    if context_manifest.get("input_structure_sha256") != sha256_file(structured_tables):
        raise ValueError("evidence context is stale for structured tables")
    tables, contexts = _uid_index(structured_tables), _uid_index(evidence_context)
    rows = _rows(bindings_v2)
    upgraded_operands = 0
    status_counts = Counter()
    packet_counts = Counter()
    for question in rows:
        question["schema_version"] = 3
        question["protocol"] = PROTOCOL
        for stage in question.get("stages") or []:
            for operand in stage.get("required_operands") or []:
                if not isinstance(operand, dict):
                    continue
                if (
                    operand.get("binding_status") == "unit_missing"
                    and operand.get("reason_codes") == ["SOURCE_UNIT_ANCHOR_MISSING_OR_CONFLICTING"]
                ):
                    uid = str(operand.get("internal_table_uid") or "")
                    candidate = (
                        context_unit_anchor_candidate(tables[uid], contexts[uid])
                        if uid in tables and uid in contexts
                        else None
                    )
                    if candidate is not None:
                        operand["binding_status"] = "binding_ready"
                        operand["source_unit"] = candidate["source_unit"]
                        operand["source_to_vnd_multiplier"] = candidate["source_to_vnd_multiplier"]
                        operand["source_unit_context_anchors"] = [candidate]
                        operand["unit_resolution_method"] = "v3_exact_source_title_unit_v1"
                        operand["reason_codes"] = []
                        upgraded_operands += 1
                status_counts[str(operand.get("binding_status") or "unknown")] += 1
        statuses = [
            str(operand.get("binding_status") or "")
            for stage in question.get("stages") or []
            for operand in stage.get("required_operands") or []
            if isinstance(operand, Mapping)
        ]
        if statuses and all(status == "binding_ready" for status in statuses):
            question["binding_packet_status"] = "binding_ready"
        packet_counts[str(question.get("binding_packet_status") or "unknown")] += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    result = {
        "schema_version": 3,
        "protocol": PROTOCOL,
        "inputs": {
            "bindings_v2": {"path": str(bindings_v2), "sha256": sha256_file(bindings_v2)},
            "bindings_v2_manifest": {"path": str(bindings_v2_manifest), "sha256": sha256_file(bindings_v2_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "evidence_context": {"path": str(evidence_context), "sha256": sha256_file(evidence_context)},
            "evidence_context_manifest": {"path": str(evidence_context_manifest), "sha256": sha256_file(evidence_context_manifest)},
        },
        "outputs": {"bindings": {"path": str(output), "sha256": sha256_file(output)}},
        "counts": {
            "question_count": len(rows),
            "context_unit_upgraded_operand_count": upgraded_operands,
            "binding_status_counts": dict(sorted(status_counts.items())),
            "binding_packet_status_counts": dict(sorted(packet_counts.items())),
        },
        "source_contract": {
            "candidate_only": True,
            "evidence_eligible": False,
            "may_execute_formula": False,
            "may_select_value": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {**result, "manifest_path": str(manifest_path)}
