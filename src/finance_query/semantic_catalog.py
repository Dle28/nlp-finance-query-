"""Read-only semantic catalog for table navigation, filtering and evaluation.

This module turns *existing*, hash-bound V2/V3/report-segment metadata into a
compact catalog.  It is not an evidence layer: the catalog never exposes raw
numeric values, does not repair OCR, and cannot select a row/cell or promote a
review status.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .evidence_context import validate_evidence_context_sidecar
from .report_segments import validate_report_segment_sidecar
from .table_structure import sha256_file, validate_structure_sidecar


SEMANTIC_CATALOG_VERSION = 1
SEMANTIC_CATALOG_PROTOCOL = "v2_v3_report_segment_metadata_catalog_v1"

PRIMARY_STATEMENT_FUNCTIONS = {
    "balance_sheet",
    "income_statement",
    "cash_flow_statement",
    "equity_change_statement",
}
SUPPORTING_SCHEDULE_FUNCTIONS = {
    "related_party_schedule",
    "debt_schedule",
    "investment_schedule",
    "segment_reporting",
    "project_schedule",
}
NON_FINANCIAL_FUNCTIONS = {"governance_roster", "source_information_table"}
NOTE_NUMBER_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})\s*[.)]?\s+")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _document_role(function_kind: str, source_heading_kind: str) -> tuple[str, list[str]]:
    """Return a deliberately coarse navigation class and its observed basis."""
    basis = [f"table_function={function_kind}"]
    if source_heading_kind:
        basis.append(f"source_heading_kind={source_heading_kind}")
    if function_kind in PRIMARY_STATEMENT_FUNCTIONS:
        return "primary_financial_statement", basis
    if function_kind in SUPPORTING_SCHEDULE_FUNCTIONS:
        return "supporting_schedule", basis
    if function_kind in NON_FINANCIAL_FUNCTIONS:
        return "governance_or_source_table", basis
    if function_kind in {"financial_note", "financial_note_detail"}:
        return "financial_note", basis
    if function_kind == "financial_data_schedule" and source_heading_kind == "numbered_heading":
        return "financial_note", basis
    return "unclassified", basis


def _note_hierarchy(segment: dict[str, Any]) -> dict[str, Any]:
    heading = str(segment.get("source_heading") or "")
    if str(segment.get("source_heading_kind") or "") != "numbered_heading":
        return {"path": [], "source_heading": "", "source": "none"}
    match = NOTE_NUMBER_RE.match(heading)
    if match is None:
        return {"path": [], "source_heading": heading, "source": "numbered_heading_without_numeric_path"}
    token = match.group(1)
    components = token.split(".")
    path = [".".join(components[:index]) for index in range(1, len(components) + 1)]
    return {"path": path, "source_heading": heading, "source": "source_numbered_heading"}


def _layout(context: dict[str, Any]) -> dict[str, Any]:
    headers = (context.get("canonical_headers") or {}).get("columns") or []
    profiles = context.get("row_profiles") or []
    data = [profile for profile in profiles if str(profile.get("role") or "") == "data"]
    numeric_columns = sorted(
        {
            int(column)
            for profile in data
            for column in profile.get("numeric_columns") or []
            if _is_int(column) and int(column) > 0
        }
    )
    if len(numeric_columns) >= 2:
        kind = "multi_column_numeric_table"
    elif len(numeric_columns) == 1:
        kind = "single_column_numeric_table"
    elif data:
        kind = "data_without_bindable_numeric_column"
    else:
        kind = "text_or_empty_table"
    return {
        "kind": kind,
        "header_depth": len((context.get("canonical_headers") or {}).get("header_row_indices") or []),
        "column_count": len(headers),
        "data_row_count": len(data),
        "numeric_column_count": len(numeric_columns),
        "period_bound_numeric_column_count": sum(
            bool(headers[column].get("period_labels"))
            for column in numeric_columns
            if column < len(headers)
        ),
    }


def build_semantic_catalog_entry(
    structure: dict[str, Any], context: dict[str, Any], segment: dict[str, Any]
) -> dict[str, Any]:
    """Derive one navigation record from matching V2/V3/segment UIDs."""
    uid = str(structure.get("internal_table_uid") or "")
    if not uid or uid != str(context.get("internal_table_uid") or "") or uid != str(segment.get("internal_table_uid") or ""):
        raise ValueError("Semantic catalog requires matching non-empty V2/V3/segment UIDs")
    function = dict(context.get("table_function") or structure.get("table_function") or {})
    section = dict(context.get("table_section") or structure.get("table_section") or {})
    function_kind = str(function.get("kind") or "unknown")
    heading_kind = str(segment.get("source_heading_kind") or "none")
    document_role, basis = _document_role(function_kind, heading_kind)
    return {
        "internal_table_uid": uid,
        "semantic_catalog_version": SEMANTIC_CATALOG_VERSION,
        "protocol": SEMANTIC_CATALOG_PROTOCOL,
        "source_contract": {
            "metadata_only": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "may_repair_ocr": False,
            "may_change_candidate_rank": False,
        },
        "document_role": document_role,
        "statement_family": function_kind if function_kind in PRIMARY_STATEMENT_FUNCTIONS else "",
        "table_function": {
            "kind": function_kind,
            "specificity": str(function.get("specificity") or "unknown"),
        },
        "table_section": {"kind": str(section.get("kind") or "unknown")},
        "note_hierarchy": _note_hierarchy(segment),
        "layout": _layout(context),
        "derivation_basis": basis,
    }


def semantic_catalog_manifest_path(path: Path) -> Path:
    if path.name == "semantic_catalog_v1.jsonl":
        return path.with_name("semantic_catalog_v1.manifest.json")
    return path.with_suffix(".manifest.json")


def validate_semantic_catalog_sidecar(
    bundle_dir: Path,
    structure_path: Path,
    context_path: Path,
    segment_path: Path,
    catalog_path: Path,
) -> dict[str, Any]:
    """Ensure a catalog is a hash-bound metadata derivative only."""
    bundle = Path(bundle_dir).resolve()
    structure = Path(structure_path).resolve()
    context = Path(context_path).resolve()
    segments = Path(segment_path).resolve()
    catalog = Path(catalog_path).resolve()
    validate_structure_sidecar(bundle, structure)
    validate_evidence_context_sidecar(bundle, structure, context)
    validate_report_segment_sidecar(bundle, segments)
    manifest_path = semantic_catalog_manifest_path(catalog)
    if not catalog.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Semantic catalog sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("semantic_catalog_version") or 0) != SEMANTIC_CATALOG_VERSION:
        raise ValueError("Unsupported semantic catalog version")
    if str(manifest.get("protocol") or "") != SEMANTIC_CATALOG_PROTOCOL:
        raise ValueError("Semantic catalog protocol mismatch")
    expected = {
        "input_bundle_tables_sha256": bundle / "tables.jsonl",
        "input_structure_sha256": structure,
        "input_evidence_context_sha256": context,
        "input_report_segments_sha256": segments,
        "sidecar_sha256": catalog,
    }
    for key, path in expected.items():
        if str(manifest.get(key) or "") != sha256_file(path):
            raise ValueError(f"Semantic catalog manifest does not match {path.name}")
    contract = manifest.get("source_contract") or {}
    if not bool(contract.get("metadata_only")) or any(
        bool(contract.get(key))
        for key in ("evidence_eligible", "training_eligible", "may_repair_ocr", "may_change_candidate_rank")
    ):
        raise ValueError("Semantic catalog must remain metadata-only")
    return manifest


def catalog_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Small deterministic summaries for manifest/audit, never quality labels."""
    return {
        "document_role_counts": dict(sorted(Counter(str(row["document_role"]) for row in rows).items())),
        "statement_family_counts": dict(sorted(Counter(str(row["statement_family"] or "none") for row in rows).items())),
        "layout_kind_counts": dict(sorted(Counter(str((row["layout"] or {}).get("kind") or "unknown") for row in rows).items())),
    }
