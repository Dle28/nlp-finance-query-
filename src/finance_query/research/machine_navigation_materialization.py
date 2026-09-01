"""Materialize narrowly rechecked machine navigation candidates for E2E replay.

This producer is intentionally more restrictive than the proposal generator.
It writes a new immutable period-packet input only when the candidate can be
reconciled with the V2 table, the V3 canonical header, the pre-authored table
type constraint, and a reliable numeric-cell coordinate.  The packets remain
candidate-only: they can make the arithmetic replayable, but cannot grant
evidence, an answer, training use, promotion, or submission.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit
from finance_query.research.machine_exact_cell_proposals import (
    PROTOCOL as PROPOSAL_PROTOCOL,
    SOURCE_CONTRACT as PROPOSAL_CONTRACT,
    sha256_file,
)


PROTOCOL = "vifinqa_machine_navigation_materialization_v1"
SCHEMA_VERSION = 1
PERIOD_PACKET_PROTOCOL = "period_column_candidate_packets_v1"
PERIOD_SOURCE_CONTRACT = {
    "candidate_only": True,
    "evidence_eligible": False,
    "may_execute_formula": False,
    "may_select_final_column": False,
    "may_select_value": False,
    "promotion_allowed": False,
    "submission_eligible": False,
    "training_eligible": False,
}
MACHINE_CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN_AUDIT_KEYS = frozenset(
    {
        "answer",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "rows",
        "raw_source_row",
        "raw_source_cell",
        "cell_provenance",
    }
)


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        values.append(value)
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not an integer") from error


def _contains_forbidden_audit_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in FORBIDDEN_AUDIT_KEYS or _contains_forbidden_audit_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_audit_key(child) for child in value)
    return False


def _require_manifest_output(manifest: Mapping[str, Any], key: str, path: Path, *, label: str) -> None:
    descriptor = (manifest.get("outputs") or {}).get(key) or {}
    if not isinstance(descriptor, Mapping) or sha256_file(path) != descriptor.get("sha256"):
        raise ValueError(f"SHA-256 mismatch for {label}")


def _source_aligned(table: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    table_provenance = table.get("source_provenance")
    context_provenance = context.get("source_provenance")
    if not isinstance(table_provenance, Mapping) or not isinstance(context_provenance, Mapping):
        return False
    return bool(
        table.get("internal_table_uid")
        and table.get("internal_table_uid") == context.get("internal_table_uid")
        and table.get("document_id") == context.get("document_id")
        and table_provenance.get("source_sha256") == context_provenance.get("source_sha256")
        and table_provenance.get("table_sha256") == context_provenance.get("table_sha256")
        and (context.get("grid") or {}).get("rectangular") is True
        and (context.get("grid") or {}).get("provenance_complete") is True
        and (context.get("quality") or {}).get("status") == "review_ready"
    )


def _header(context: Mapping[str, Any], column_index: int) -> Mapping[str, Any] | None:
    matches = [
        column
        for column in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(column, Mapping) and column.get("column_index") == column_index
    ]
    return matches[0] if len(matches) == 1 else None


def _row_profile(context: Mapping[str, Any], row_index: int) -> Mapping[str, Any] | None:
    matches = [
        profile
        for profile in context.get("row_profiles") or []
        if isinstance(profile, Mapping) and profile.get("row_index") == row_index
    ]
    return matches[0] if len(matches) == 1 else None


def _expected_types(packet: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for stage in packet.get("stages") or []:
        if isinstance(stage, Mapping):
            for operand in stage.get("required_operands") or []:
                if isinstance(operand, Mapping):
                    values.update(str(value) for value in operand.get("expected_table_types") or [] if str(value))
    return values


def _proposal_navigation(proposal: Mapping[str, Any]) -> Mapping[str, Any]:
    navigation = proposal.get("source_navigation")
    if not isinstance(navigation, Mapping):
        raise ValueError("machine proposal has no source navigation")
    return navigation


def _machine_candidate(
    *,
    proposal: Mapping[str, Any],
    packet: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, bool], list[str]]:
    """Recheck a proposal and return a period candidate only if all checks pass."""
    navigation = _proposal_navigation(proposal)
    row_index = _int(navigation.get("row_index"), label="proposal row index")
    column_index = _int(navigation.get("column_index"), label="proposal column index")
    requested_year = _int(navigation.get("requested_year"), label="proposal requested year")
    checks: dict[str, bool] = {
        "proposal_contract": proposal.get("source_contract") == PROPOSAL_CONTRACT,
        "source_context_aligned": _source_aligned(table, context),
        "expected_table_type": bool(
            set(str(value) for value in proposal.get("expected_table_types") or [])
            == _expected_types(packet)
        ),
    }
    reasons: list[str] = []
    table_function = context.get("table_function") or {}
    table_kind = str(table_function.get("kind") or "") if isinstance(table_function, Mapping) else ""
    expected = _expected_types(packet)
    compatible = {table_kind}
    if table_kind in {"financial_note", "financial_note_detail"}:
        compatible.add("notes")
    checks["source_table_type_compatible"] = bool(expected & compatible)
    header = _header(context, column_index)
    checks["canonical_header_unique"] = header is not None
    if header is None:
        reasons.append("V3_HEADER_NOT_UNIQUE")
        return None, checks, sorted(set(reasons))
    header_cells = header.get("header_source_cells") or []
    coordinates = [
        {"row_index": _int(value.get("row_index"), label="header row index"), "column_index": _int(value.get("column_index"), label="header column index")}
        for value in header_cells
        if isinstance(value, Mapping)
    ]
    checks["header_coordinates_present"] = bool(coordinates)
    rows = table.get("rows") or []
    header_texts: list[str] = []
    for coordinate in coordinates:
        header_row, header_column = coordinate["row_index"], coordinate["column_index"]
        if header_row < 0 or header_column < 0 or header_row >= len(rows) or header_column >= len(rows[header_row]):
            checks["header_coordinates_valid"] = False
            reasons.append("V2_HEADER_COORDINATE_INVALID")
            continue
        header_texts.append(str(rows[header_row][header_column]))
    checks.setdefault("header_coordinates_valid", bool(coordinates))
    checks["header_hash_matches_proposal"] = (
        bool(header_texts)
        and hashlib.sha256("\n".join(header_texts).encode("utf-8")).hexdigest()
        == str(navigation.get("period_header_sha256") or "")
    )
    # V2 can retain non-leading rows in ``header_row_indices``.  V3 records
    # those exclusions explicitly and is the authoritative canonical-header
    # view.  A mismatch is therefore repairable only when V3 keeps a strict,
    # provenance-backed subset of the original V2 header rows -- never merely
    # because it provides a different label.
    raw_v2_header_rows = {
        _int(value, label="V2 header row index") for value in table.get("header_row_indices") or []
    }
    canonical_header_rows = {coordinate["row_index"] for coordinate in coordinates}
    checks["v3_canonical_header_strictly_refines_v2_headers"] = bool(
        canonical_header_rows
        and canonical_header_rows < raw_v2_header_rows
    )
    checks["header_provenance_reconciled"] = bool(
        checks["header_hash_matches_proposal"]
        or checks["v3_canonical_header_strictly_refines_v2_headers"]
    )
    period_labels = [str(value) for value in header.get("period_labels") or [] if str(value)]
    checks["requested_year_in_canonical_header"] = period_labels == [str(requested_year)]
    profile = _row_profile(context, row_index)
    checks["row_profile_unique"] = profile is not None
    checks["selected_cell_is_reliable_numeric"] = bool(
        profile is not None
        and column_index in set(profile.get("numeric_columns") or [])
        and column_index not in set(profile.get("unreliable_numeric_columns") or [])
    )
    source_label = str(header.get("source_label") or "")
    unit, _, _ = resolve_source_unit([{"raw_source_cell": source_label}])
    checks["source_unit_matches_header"] = unit == str(navigation.get("source_unit_candidate") or "")
    for name, passed in checks.items():
        if name in {"header_hash_matches_proposal", "v3_canonical_header_strictly_refines_v2_headers"}:
            continue
        if not passed:
            reasons.append(f"CHECK_FAILED_{name.upper()}")
    if reasons:
        return None, checks, sorted(set(reasons))
    return {
        "internal_table_uid": navigation["internal_table_uid"],
        "row_index": row_index,
        "column_index": column_index,
        "header_source_cells": coordinates,
        "period_labels": period_labels,
        "source_label": source_label,
        "unit_labels": list(header.get("unit_labels") or []),
        "requested_year": requested_year,
        "reason_codes": [
            "EXACT_REQUESTED_YEAR",
            "MACHINE_SOURCE_RECHECK_CANDIDATE_ONLY",
            *(
                ["V3_CANONICAL_HEADER_RECONCILIATION"]
                if not checks["header_hash_matches_proposal"]
                else []
            ),
        ],
        "source_contract": dict(PERIOD_SOURCE_CONTRACT),
    }, checks, []


def _patch_packet(packet: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Replace exactly one simple reported-concept operand, otherwise fail closed."""
    result = json.loads(json.dumps(packet))
    operands = [
        operand
        for stage in result.get("stages") or []
        if isinstance(stage, Mapping)
        for operand in stage.get("required_operands") or []
        if isinstance(operand, dict)
    ]
    if len(operands) != 1:
        raise ValueError("machine navigation materialization supports exactly one operand")
    operand = operands[0]
    operand["period_column_candidates"] = [dict(candidate)]
    operand["period_column_candidate_count"] = 1
    operand["column_status"] = "unique_period_column_candidate"
    operand["navigation_row_count"] = 1
    operand["column_candidate_reason_counts"] = {"EXACT_REQUESTED_YEAR": 1, "MACHINE_SOURCE_RECHECK_CANDIDATE_ONLY": 1}
    operand["row_status_counts"] = {"unique_period_column_candidate": 1}
    result["input_packet_status"] = "machine_source_recheck_candidate"
    result["packet_status"] = "unique_period_column_candidate"
    return result


def build_machine_navigation_materialization(
    *,
    base_period_packets_path: Path,
    base_period_manifest_path: Path,
    proposals_path: Path,
    proposals_manifest_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Write a new candidate-only period-packet artifact after source recheck."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    base_manifest = _load_json(base_period_manifest_path)
    _require_manifest_output(base_manifest, "period_packets", base_period_packets_path, label="base period packets")
    structured_hash = ((base_manifest.get("inputs") or {}).get("structured_tables_v2") or {}).get("sha256")
    if sha256_file(structured_tables_path) != structured_hash:
        raise ValueError("SHA-256 mismatch for structured V2 tables")
    evidence_manifest = _load_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != evidence_manifest.get("sidecar_sha256"):
        raise ValueError("SHA-256 mismatch for V3 evidence context")
    proposal_manifest = _load_json(proposals_manifest_path)
    if proposal_manifest.get("protocol") != PROPOSAL_PROTOCOL:
        raise ValueError("unexpected machine proposal protocol")
    _require_manifest_output(proposal_manifest, "proposals", proposals_path, label="machine proposals")

    packets = _load_jsonl(base_period_packets_path)
    packet_by_id = {_int(packet.get("question_id"), label="base packet question_id"): packet for packet in packets}
    if set(packet_by_id) != set(range(1, expected_question_count + 1)):
        raise ValueError("base period packets have incomplete question coverage")
    proposals = _load_jsonl(proposals_path)
    proposal_by_id = {_int(proposal.get("question_id"), label="proposal question_id"): proposal for proposal in proposals}
    if len(proposal_by_id) != len(proposals):
        raise ValueError("machine proposals are not unique by question")
    tables = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(evidence_context_path)}

    materialized: dict[int, dict[str, Any]] = {}
    audits: list[dict[str, Any]] = []
    for question_id, proposal in sorted(proposal_by_id.items()):
        navigation = _proposal_navigation(proposal)
        table_uid = str(navigation.get("internal_table_uid") or "")
        table, context = tables.get(table_uid), contexts.get(table_uid)
        if table is None or context is None:
            candidate, checks, reasons = None, {"v2_v3_table_present": False}, ["V2_OR_V3_TABLE_MISSING"]
        else:
            candidate, checks, reasons = _machine_candidate(
                proposal=proposal,
                packet=packet_by_id[question_id],
                table=table,
                context=context,
            )
        status = "MATERIALIZED_EXECUTION_CANDIDATE" if candidate is not None else "QUARANTINED_SOURCE_RECHECK"
        if candidate is not None:
            materialized[question_id] = _patch_packet(packet_by_id[question_id], candidate)
        audit = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "proposal_id": proposal.get("proposal_id"),
            "machine_materialization_status": status,
            "header_reconciliation": (
                "NONE"
                if checks.get("header_hash_matches_proposal") is True
                else "V3_CANONICAL_HEADER_RECONCILES_V2_SUPERSET"
                if checks.get("header_provenance_reconciled") is True
                else "CONFLICT_UNRESOLVED"
            ),
            "checks": checks,
            "reason_codes": reasons,
            "source_navigation": {
                key: navigation[key]
                for key in (
                    "document_id",
                    "internal_table_uid",
                    "exact_table_locator_sha256",
                    "row_index",
                    "column_index",
                    "requested_year",
                    "period_header_sha256",
                    "source_unit_candidate",
                    "row_label",
                    "row_label_sha256",
                    "source_cell_provenance_sha256",
                )
            },
            "raw_numeric_values_included": False,
            "source_contract": dict(MACHINE_CONTRACT),
        }
        if _contains_forbidden_audit_key(audit):
            raise ValueError("machine navigation audit leaked a forbidden value field")
        audits.append(audit)
    output_packets = [
        materialized.get(question_id, packet_by_id[question_id])
        for question_id in range(1, expected_question_count + 1)
    ]
    status_counts = Counter(audit["machine_materialization_status"] for audit in audits)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "proposal_count": len(proposals),
        "materialized_question_count": len(materialized),
        "materialization_status_counts": dict(sorted(status_counts.items())),
        "base_packet_status_counts": dict(sorted(Counter(str(row.get("packet_status") or "") for row in packets).items())),
        "output_packet_status_counts": dict(sorted(Counter(str(row.get("packet_status") or "") for row in output_packets).items())),
        "raw_numeric_values_included_in_audit": False,
        "source_contract": dict(MACHINE_CONTRACT),
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        packets_path = temporary_dir / "period_column_candidate_packets_v1.jsonl"
        audit_path = temporary_dir / "machine_navigation_recheck_audit_v1.jsonl"
        summary_path = temporary_dir / "machine_navigation_materialization_summary_v1.json"
        _write_jsonl(packets_path, output_packets)
        _write_jsonl(audit_path, audits)
        _write_json(summary_path, summary)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "inputs": {
                "base_period_packets": {"path": str(base_period_packets_path), "sha256": sha256_file(base_period_packets_path)},
                "base_period_manifest": {"path": str(base_period_manifest_path), "sha256": sha256_file(base_period_manifest_path)},
                "machine_proposals": {"path": str(proposals_path), "sha256": sha256_file(proposals_path)},
                "machine_proposals_manifest": {"path": str(proposals_manifest_path), "sha256": sha256_file(proposals_manifest_path)},
                "structured_tables_v2": {"path": str(structured_tables_path), "sha256": sha256_file(structured_tables_path)},
                "evidence_context_v3": {"path": str(evidence_context_path), "sha256": sha256_file(evidence_context_path)},
                "evidence_context_manifest_v3": {"path": str(evidence_context_manifest_path), "sha256": sha256_file(evidence_context_manifest_path)},
            },
            "outputs": {
                "period_packets": {"path": packets_path.name, "sha256": sha256_file(packets_path)},
                "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
            },
            "source_contract": dict(MACHINE_CONTRACT),
        }
        _write_json(temporary_dir / "period_column_candidate_packets_v1.manifest.json", manifest)
        temporary_dir.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return summary


def validate_machine_navigation_materialization(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Verify source hashes, bounded edits, and the non-authorizing audit."""
    manifest_path = artifact_dir / "period_column_candidate_packets_v1.manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != MACHINE_CONTRACT:
        raise ValueError("unexpected machine navigation materialization protocol")
    for descriptor in (manifest.get("inputs") or {}).values():
        if not isinstance(descriptor, Mapping):
            raise ValueError("invalid materialization input descriptor")
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("materialization input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        if not isinstance(descriptor, Mapping):
            raise ValueError("invalid materialization output descriptor")
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("materialization output hash mismatch")
    output_packets = _load_jsonl(artifact_dir / "period_column_candidate_packets_v1.jsonl")
    audits = _load_jsonl(artifact_dir / "machine_navigation_recheck_audit_v1.jsonl")
    summary = _load_json(artifact_dir / "machine_navigation_materialization_summary_v1.json")
    if len(output_packets) != expected_question_count or {
        _int(row.get("question_id"), label="output packet question_id") for row in output_packets
    } != set(range(1, expected_question_count + 1)):
        raise ValueError("output period packets have incomplete question coverage")
    if any(
        _contains_forbidden_audit_key(audit)
        or audit.get("raw_numeric_values_included") is not False
        or audit.get("source_contract") != MACHINE_CONTRACT
        or "human_verified" in audit
        for audit in audits
    ):
        raise ValueError("materialization audit violated the non-authorizing boundary")
    materialized_ids = {
        _int(audit.get("question_id"), label="audit question id")
        for audit in audits
        if audit.get("machine_materialization_status") == "MATERIALIZED_EXECUTION_CANDIDATE"
    }
    base_path = Path(str(((manifest.get("inputs") or {}).get("base_period_packets") or {}).get("path") or ""))
    base_packets = {
        _int(row.get("question_id"), label="base packet question_id"): row for row in _load_jsonl(base_path)
    }
    output_by_id = {
        _int(row.get("question_id"), label="output packet question_id"): row for row in output_packets
    }
    for question_id in set(range(1, expected_question_count + 1)) - materialized_ids:
        if _canonical_sha(base_packets[question_id]) != _canonical_sha(output_by_id[question_id]):
            raise ValueError("non-materialized period packet changed")
    if summary.get("materialized_question_count") != len(materialized_ids):
        raise ValueError("materialization summary count mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "proposal_count": len(audits),
        "materialized_question_count": len(materialized_ids),
        "answer_eligible": False,
        "submission_eligible": False,
    }
