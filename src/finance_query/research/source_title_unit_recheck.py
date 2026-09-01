"""Recheck unit-less period headers against hash-bound V3 source titles."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit, sha_json
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_source_title_unit_recheck_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset({"answer", "raw_value", "raw_values", "cell_value", "pandas_query", "raw_source_cell", "raw_source_row", "rows"})


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values), encoding="utf-8")


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(item) for key, item in value.items())
    return any(_contains_forbidden(item) for item in value) if isinstance(value, list) else False


def _int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    return int(value)


def _aligned(table: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    source, context_source = table.get("source_provenance") or {}, context.get("source_provenance") or {}
    return bool(
        table.get("internal_table_uid") == context.get("internal_table_uid")
        and table.get("document_id") == context.get("document_id")
        and source.get("source_sha256") == context_source.get("source_sha256")
        and source.get("table_sha256") == context_source.get("table_sha256")
        and (context.get("grid") or {}).get("rectangular") is True
        and (context.get("grid") or {}).get("provenance_complete") is True
        and (context.get("quality") or {}).get("status") == "review_ready"
    )


def _candidate_audit(
    *, binding: Mapping[str, Any], table: Mapping[str, Any] | None, context: Mapping[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, bool], list[str]]:
    uid = str(binding.get("internal_table_uid") or "")
    checks = {"v2_v3_table_present": table is not None and context is not None}
    if table is None or context is None:
        return None, checks, ["V2_OR_V3_TABLE_MISSING"]
    checks["v2_v3_source_aligned"] = _aligned(table, context)
    row_index, column_index = _int(binding.get("row_index"), "binding row index"), _int(binding.get("column_index"), "binding column index")
    profiles = [row for row in context.get("row_profiles") or [] if row.get("row_index") == row_index]
    checks["selected_cell_is_reliable_numeric"] = bool(
        len(profiles) == 1
        and column_index in set(profiles[0].get("numeric_columns") or [])
        and column_index not in set(profiles[0].get("unreliable_numeric_columns") or [])
    )
    title = str(((context.get("context_trace") or {}).get("source_title")) or "")
    unit, _, multiplier = resolve_source_unit([{"raw_source_cell": title}])
    checks["unique_unit_in_v3_source_title"] = unit is not None and multiplier is not None
    if not all(checks.values()):
        return None, checks, [f"CHECK_FAILED_{name.upper()}" for name, passed in checks.items() if not passed]
    source = table.get("source_provenance") or {}
    marker = {
        "protocol": PROTOCOL,
        "document_id": table.get("document_id"),
        "internal_table_uid": uid,
        "source_sha256": source.get("source_sha256"),
        "table_sha256": source.get("table_sha256"),
        "evidence_context_row_sha256": sha_json(context),
        "source_title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest(),
        "source_unit": unit,
        "source_to_vnd_multiplier": format(multiplier, "f"),
        "raw_unit_label": unit,
    }
    return marker, checks, []


def _patch_packet(packet: Mapping[str, Any], *, binding: Mapping[str, Any], marker: Mapping[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(packet))
    matches = []
    for stage in output.get("stages") or []:
        for operand in stage.get("required_operands") or []:
            if operand.get("role") == binding.get("role"):
                matches.append(operand)
    if len(matches) != 1 or len(matches[0].get("period_column_candidates") or []) != 1:
        raise ValueError("unit title recheck requires exactly one existing period candidate")
    candidate = matches[0]["period_column_candidates"][0]
    if (
        candidate.get("internal_table_uid") != binding.get("internal_table_uid")
        or candidate.get("row_index") != binding.get("row_index")
        or candidate.get("column_index") != binding.get("column_index")
    ):
        raise ValueError("unit title recheck candidate is stale for period packet")
    candidate["unit_source_title_recheck"] = dict(marker)
    candidate["reason_codes"] = sorted(set(candidate.get("reason_codes") or []) | {"V3_SOURCE_TITLE_UNIT_RECHECK"})
    return output


def build_source_title_unit_recheck(
    *,
    base_period_packets: Path,
    base_period_manifest: Path,
    bindings: Path,
    bindings_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    base_manifest, binding_manifest, context_manifest = _read_json(base_period_manifest), _read_json(bindings_manifest), _read_json(evidence_context_manifest)
    if sha256_file(base_period_packets) != ((base_manifest.get("outputs") or {}).get("period_packets") or {}).get("sha256"):
        raise ValueError("base period packets hash mismatch")
    if sha256_file(bindings) != ((binding_manifest.get("outputs") or {}).get("bindings") or {}).get("sha256"):
        raise ValueError("bindings hash mismatch")
    if sha256_file(structured_tables) != ((base_manifest.get("inputs") or {}).get("structured_tables_v2") or {}).get("sha256"):
        raise ValueError("structured V2 table hash mismatch")
    if sha256_file(evidence_context) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    packets = {_int(row.get("question_id"), "period packet question id"): row for row in _rows(base_period_packets)}
    if set(packets) != set(range(1, 1013)):
        raise ValueError("base period packets have incomplete question coverage")
    tables = {str(row.get("internal_table_uid") or ""): row for row in _rows(structured_tables)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _rows(evidence_context)}
    missing_unit = [
        operand
        for question in _rows(bindings)
        for stage in question.get("stages") or []
        for operand in stage.get("required_operands") or []
        if operand.get("binding_status") == "unit_missing"
        and "SOURCE_UNIT_ANCHOR_MISSING_OR_CONFLICTING" in set(operand.get("reason_codes") or [])
    ]
    revised: dict[int, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for binding in missing_unit:
        question_id = _int(binding.get("question_id"), "binding question id")
        uid = str(binding.get("internal_table_uid") or "")
        marker, checks, reasons = _candidate_audit(binding=binding, table=tables.get(uid), context=contexts.get(uid))
        status = "MATERIALIZED_UNIT_TITLE_RECHECK" if marker is not None else "QUARANTINED_UNIT_TITLE_RECHECK"
        if marker is not None:
            revised[question_id] = _patch_packet(packets[question_id], binding=binding, marker=marker)
        row = {
            "protocol": PROTOCOL,
            "schema_version": 1,
            "question_id": question_id,
            "stage_id": binding.get("stage_id"),
            "role": binding.get("role"),
            "unit_recheck_status": status,
            "checks": checks,
            "reason_codes": reasons,
            "source_navigation": {key: binding.get(key) for key in ("document_id", "internal_table_uid", "row_index", "column_index")},
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(row):
            raise ValueError("unit recheck audit contains a numeric-answer field")
        audit.append(row)
    output_packets = [revised.get(question_id, packets[question_id]) for question_id in range(1, 1013)]
    summary = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "candidate_count": len(missing_unit),
        "materialized_question_count": len(revised),
        "status_counts": dict(sorted(Counter(row["unit_recheck_status"] for row in audit).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        packet_path, audit_path, summary_path = temporary / "period_column_candidate_packets_v1.jsonl", temporary / "source_title_unit_recheck_audit_v1.jsonl", temporary / "source_title_unit_recheck_summary_v1.json"
        _write_jsonl(packet_path, output_packets); _write_jsonl(audit_path, audit); _write_json(summary_path, summary)
        manifest = {
            "protocol": PROTOCOL,
            "schema_version": 1,
            "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in {"base_period_packets": base_period_packets, "base_period_manifest": base_period_manifest, "bindings": bindings, "bindings_manifest": bindings_manifest, "structured_tables_v2": structured_tables, "evidence_context_v3": evidence_context, "evidence_context_manifest_v3": evidence_context_manifest}.items()},
            "outputs": {"period_packets": {"path": packet_path.name, "sha256": sha256_file(packet_path)}, "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)}, "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)}},
            "source_contract": dict(CONTRACT),
        }
        _write_json(temporary / "period_column_candidate_packets_v1.manifest.json", manifest); temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True); raise
    return summary


def validate_source_title_unit_recheck(artifact_dir: Path) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "period_column_candidate_packets_v1.manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected source-title unit recheck protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = (artifact_dir / str(descriptor.get("path") or "")) if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("source-title unit recheck hash mismatch")
    audit = _rows(artifact_dir / "source_title_unit_recheck_audit_v1.jsonl")
    if any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT or row.get("raw_numeric_values_included") is not False or "human_verified" in row for row in audit):
        raise ValueError("source-title unit recheck audit lost its boundary")
    summary = _read_json(artifact_dir / "source_title_unit_recheck_summary_v1.json")
    materialized = sum(row.get("unit_recheck_status") == "MATERIALIZED_UNIT_TITLE_RECHECK" for row in audit)
    if summary.get("candidate_count") != len(audit) or summary.get("materialized_question_count") != materialized:
        raise ValueError("source-title unit recheck summary mismatch")
    return {"status": "PASS", "candidate_count": len(audit), "materialized_question_count": materialized, "answer_eligible": False, "submission_eligible": False}
