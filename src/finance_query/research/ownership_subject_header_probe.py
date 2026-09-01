"""Validate a narrow ownership header after subject-row navigation.

This follows ``embedded_subject_row_probe``.  It accepts only a unique subject
row in a previously retrieved, scope-compatible table, then requires one V3
header that says ownership (not voting rights) and contains the requested
year, including a full date such as ``31/12/2016``.  The result is navigation
metadata, never an evidence binding or answer.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.machine_direct_lookup_route_materialization import _source_aligned
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_ownership_subject_header_probe_v1"
CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "human_verified",
        "rows",
        "row_label",
        "raw_source_row",
        "raw_source_cell",
        "source_label",
        "header_text",
    }
)


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain JSON objects")
            yield value


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_output(manifest_path: Path, key: str, path: Path) -> None:
    descriptor = (_json(manifest_path).get("outputs") or {}).get(key) or {}
    if sha256_file(path) != descriptor.get("sha256"):
        raise ValueError(f"SHA-256 mismatch for {key}")


def _ownership_header(header: Mapping[str, Any], *, year: int) -> bool:
    label = str(header.get("source_label") or "").casefold()
    has_ownership = "sở hữu" in label and "quyền biểu quyết" not in label
    has_year = bool(re.search(rf"(?<!\d){re.escape(str(year))}(?!\d)", label))
    return has_ownership and has_year


def _header_coordinates_valid(header: Mapping[str, Any], table: Mapping[str, Any]) -> bool:
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    cells = [cell for cell in header.get("header_source_cells") or [] if isinstance(cell, Mapping)]
    return bool(
        cells
        and all(
            isinstance(cell.get("row_index"), int)
            and isinstance(cell.get("column_index"), int)
            and 0 <= cell["row_index"] < len(rows)
            and 0 <= cell["column_index"] < len(rows[cell["row_index"]])
            and cell["row_index"] < len(provenance)
            and cell["column_index"] < len(provenance[cell["row_index"]])
            and isinstance(provenance[cell["row_index"]][cell["column_index"]], Mapping)
            for cell in cells
        )
    )


def _row_coordinate_valid(context: Mapping[str, Any], table: Mapping[str, Any], *, row_index: int, column_index: int) -> bool:
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    return bool(
        len(profiles) == 1
        and column_index in set(profiles[0].get("numeric_columns") or [])
        and column_index not in set(profiles[0].get("unreliable_numeric_columns") or [])
        and 0 <= row_index < len(rows)
        and 0 <= column_index < len(rows[row_index])
        and row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )


def build_ownership_subject_header_probe(
    *,
    subject_probe_dir: Path,
    reclassification_dir: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Check unique ownership-period headers for unique subject rows."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_output(subject_probe_dir / "manifest.json", "embedded_subject_row_probe_v1.jsonl", subject_probe_dir / "embedded_subject_row_probe_v1.jsonl")
    _require_output(reclassification_dir / "manifest.json", "plans", reclassification_dir / "typed_operand_plans.jsonl")
    context_manifest = _json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")

    subject_rows = list(_rows(subject_probe_dir / "embedded_subject_row_probe_v1.jsonl"))
    plans = {int(row["question_id"]): row for row in _rows(reclassification_dir / "typed_operand_plans.jsonl")}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(plans) != expected_ids or not subject_rows:
        raise ValueError("subject probe and plans have invalid coverage")
    requested_uids = {
        str(candidate.get("internal_table_uid") or "")
        for record in subject_rows
        for candidate in record.get("subject_row_candidates") or []
        if isinstance(candidate, Mapping)
    }
    tables = {str(row.get("internal_table_uid") or ""): row for row in _rows(structured_tables_path) if str(row.get("internal_table_uid") or "") in requested_uids}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _rows(evidence_context_path) if str(row.get("internal_table_uid") or "") in requested_uids}

    records: list[dict[str, Any]] = []
    for subject_record in subject_rows:
        question_id = int(subject_record["question_id"])
        candidate_rows = subject_record.get("subject_row_candidates") or []
        if subject_record.get("status") != "UNIQUE_EXACT_SUBJECT_ROW_NAVIGATION" or len(candidate_rows) != 1:
            status, candidates = "SUBJECT_ROW_NOT_UNIQUE", []
        else:
            row = candidate_rows[0]
            uid = str(row.get("internal_table_uid") or "")
            table, context = tables.get(uid), contexts.get(uid)
            operand = (plans[question_id].get("operands") or [None])[0]
            years = plans[question_id].get("years") or []
            if not isinstance(operand, Mapping) or len(years) != 1 or not isinstance(years[0], int):
                status, candidates = "PLAN_NOT_SINGLE_YEAR_OWNERSHIP", []
            elif table is None or context is None or not _source_aligned(table, context):
                status, candidates = "V2_V3_SOURCE_NOT_READY", []
            else:
                selected: list[dict[str, Any]] = []
                for header in ((context.get("canonical_headers") or {}).get("columns") or []):
                    if not isinstance(header, Mapping) or not _ownership_header(header, year=years[0]):
                        continue
                    column_index = header.get("column_index")
                    if not isinstance(column_index, int):
                        continue
                    if not _header_coordinates_valid(header, table) or not _row_coordinate_valid(context, table, row_index=int(row["row_index"]), column_index=column_index):
                        continue
                    selected.append(
                        {
                            "internal_table_uid": uid,
                            "document_id": str(row.get("document_id") or ""),
                            "local_ordinal": int(row.get("local_ordinal") or 0),
                            "row_index": int(row["row_index"]),
                            "column_index": column_index,
                            "header_coordinate_count": len(header.get("header_source_cells") or []),
                            "period_match": "YEAR_TOKEN_IN_OWNERSHIP_HEADER",
                            "metric_match": "OWNERSHIP_NOT_VOTING_RIGHTS",
                        }
                    )
                candidates = selected
                status = (
                    "UNIQUE_OWNERSHIP_SUBJECT_HEADER_NAVIGATION"
                    if len(candidates) == 1
                    else "MULTIPLE_OWNERSHIP_SUBJECT_HEADER_NAVIGATION"
                    if len(candidates) > 1
                    else "NO_OWNERSHIP_SUBJECT_HEADER_NAVIGATION"
                )
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "status": status,
            "header_candidate_count": len(candidates),
            "header_candidates": candidates,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("ownership subject-header probe leaked unsafe content")
        records.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(records),
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in records).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        records_path = temporary / "ownership_subject_header_probe_v1.jsonl"
        summary_path = temporary / "ownership_subject_header_probe_summary_v1.json"
        _write_jsonl(records_path, records)
        _write_json(summary_path, summary)
        inputs = {
            "subject_probe_manifest": subject_probe_dir / "manifest.json",
            "reclassification_manifest": reclassification_dir / "manifest.json",
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    records_path.name: {"path": records_path.name, "sha256": sha256_file(records_path)},
                    summary_path.name: {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"status": "BUILT", **summary, "answer_eligible": False, "submission_eligible": False}


def validate_ownership_subject_header_probe(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected ownership subject-header probe contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("ownership subject-header probe input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("ownership subject-header probe output hash mismatch")
    records = list(_rows(artifact_dir / "ownership_subject_header_probe_v1.jsonl"))
    summary = _json(artifact_dir / "ownership_subject_header_probe_summary_v1.json")
    if not records or len(records) > expected_question_count or len({int(row["question_id"]) for row in records}) != len(records):
        raise ValueError("ownership subject-header probe IDs are invalid")
    if any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT for row in records):
        raise ValueError("ownership subject-header probe lost navigation-only boundary")
    counts = dict(sorted(Counter(str(row.get("status") or "") for row in records).items()))
    if int(summary.get("target_question_count") or 0) != len(records) or summary.get("status_counts") != counts:
        raise ValueError("ownership subject-header probe summary mismatch")
    return {"status": "PASS", "target_question_count": len(records), "answer_eligible": False, "submission_eligible": False}
