"""Read-only OCR and table-alignment diagnostics derived from V2/V3.

The profile is deliberately an observational sidecar.  It records where raw
OCR values or table layouts are risky, but never replaces a string, moves a
cell, supplies a header, changes candidate rank, or becomes numerical
evidence.  Grounding keeps using the immutable V2 grid and the canonical V3
header provenance.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .evidence_context import validate_evidence_context_sidecar
from .execution import parse_decimal
from .table_structure import sha256_file, validate_structure_sidecar


OCR_QUALITY_PROFILE_VERSION = 1
OCR_QUALITY_PROFILE_PROTOCOL = "v2_v3_observational_ocr_quality_v1"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _data_profiles(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(profile)
        for profile in context.get("row_profiles") or []
        if str(profile.get("role") or "") in {"data", "data_with_unreliable_numeric"}
    ]


def _unreliable_cell_refs(
    table: dict[str, Any],
    profiles: Iterable[dict[str, Any]],
    *,
    ignored_columns: set[int],
) -> tuple[list[dict[str, Any]], Counter[str], int, int]:
    """Return source coordinates/warnings only; raw values never leave V2."""
    rows = table.get("rows") or []
    refs: list[dict[str, Any]] = []
    warning_counts: Counter[str] = Counter()
    structural_count = 0
    reliable_count = 0
    for profile in profiles:
        row_index = profile.get("row_index")
        if not _is_int(row_index) or not 0 <= row_index < len(rows):
            continue
        row = rows[row_index]
        for column_index in profile.get("structural_numeric_columns") or []:
            if (
                not _is_int(column_index)
                or not 0 < column_index < len(row)
                or int(column_index) in ignored_columns
            ):
                continue
            structural_count += 1
            parsed = parse_decimal(row[column_index])
            unsafe_warnings = [
                warning
                for warning in parsed.warnings
                if warning != "percent_value_not_scaled"
            ]
            if parsed.value is not None and not unsafe_warnings:
                reliable_count += 1
                continue
            codes = unsafe_warnings or ["numeric_parse_failed"]
            warning_counts.update(codes)
            refs.append(
                {
                    "row_index": row_index,
                    "column_index": column_index,
                    "warning_codes": codes,
                }
            )
    return refs, warning_counts, structural_count, reliable_count


def _alignment_signal(
    profiles: Iterable[dict[str, Any]],
    *,
    ignored_columns: set[int],
) -> dict[str, Any]:
    layouts: Counter[tuple[int, ...]] = Counter()
    rows_by_layout: dict[tuple[int, ...], list[int]] = {}
    for profile in profiles:
        columns = tuple(
            sorted(
                int(column)
                for column in profile.get("structural_numeric_columns") or []
                if _is_int(column) and int(column) > 0 and int(column) not in ignored_columns
            )
        )
        if not columns:
            continue
        row_index = profile.get("row_index")
        if not _is_int(row_index):
            continue
        layouts[columns] += 1
        rows_by_layout.setdefault(columns, []).append(int(row_index))
    if not layouts:
        return {
            "observed_numeric_layout_count": 0,
            "dominant_numeric_columns": [],
            "dominant_layout_row_count": 0,
            "non_dominant_row_indices": [],
            "subset_layout_row_indices": [],
            "alignment_anomaly_row_indices": [],
            "ignored_reference_columns": sorted(ignored_columns),
        }
    # Prefer the widest observed layout: a blank/optional value is normally a
    # subset of a full financial-table row and is not a column shift.  Stable
    # ties use count then the column tuple.
    maximum_width = max(len(layout) for layout in layouts)
    widest = [layout for layout in layouts if len(layout) == maximum_width]
    dominant = min(widest, key=lambda layout: (-layouts[layout], layout))
    subset_rows = sorted(
        row_index
        for layout, indices in rows_by_layout.items()
        if layout != dominant and set(layout).issubset(dominant)
        for row_index in indices
    )
    anomaly_rows = sorted(
        row_index
        for layout, indices in rows_by_layout.items()
        if not set(layout).issubset(dominant)
        for row_index in indices
    )
    return {
        "observed_numeric_layout_count": len(layouts),
        "dominant_numeric_columns": list(dominant),
        "dominant_layout_row_count": layouts[dominant],
        "non_dominant_row_indices": sorted(
            row_index
            for layout, indices in rows_by_layout.items()
            if layout != dominant
            for row_index in indices
        ),
        "subset_layout_row_indices": subset_rows,
        "alignment_anomaly_row_indices": anomaly_rows,
        "ignored_reference_columns": sorted(ignored_columns),
    }


def profile_ocr_quality(table: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Build one source-preserving table diagnostic from matching V2/V3 rows."""
    uid = str(table.get("internal_table_uid") or "")
    if not uid or uid != str(context.get("internal_table_uid") or ""):
        raise ValueError("OCR quality profile requires matching non-empty V2/V3 table UID")
    profiles = _data_profiles(context)
    headers = (context.get("canonical_headers") or {}).get("columns") or []
    ignored_reference_columns = {
        int(column.get("column_index"))
        for column in headers
        if _is_int(column.get("column_index"))
        and str(column.get("role") or "") in {"reference", "row_label"}
    }
    unreliable_refs, warning_counts, structural_count, reliable_count = _unreliable_cell_refs(
        table, profiles, ignored_columns=ignored_reference_columns
    )
    alignment = _alignment_signal(profiles, ignored_columns=ignored_reference_columns)
    v3_quality = dict(context.get("quality") or {})
    source_status = str(v3_quality.get("status") or "unknown")
    reasons: list[str] = []
    if source_status == "blocked":
        reasons.append("v3_context_blocked")
    if structural_count and not reliable_count:
        reasons.append("no_reliable_numeric_source_cell")
    if unreliable_refs:
        reasons.append("unreliable_numeric_source_cells_observed")
    if alignment["alignment_anomaly_row_indices"]:
        reasons.append("numeric_column_alignment_non_nested")
    if float(v3_quality.get("numeric_header_coverage") or 0.0) < 1.0:
        reasons.append("numeric_column_without_canonical_source_header")
    if source_status == "blocked" or (
        structural_count > 0 and reliable_count == 0
    ):
        action = "quarantine"
    elif reasons or source_status != "review_ready":
        action = "review_required"
    else:
        action = "normal"
    return {
        "internal_table_uid": uid,
        "ocr_quality_profile_version": OCR_QUALITY_PROFILE_VERSION,
        "protocol": OCR_QUALITY_PROFILE_PROTOCOL,
        "source_contract": {
            "raw_values_preserved": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "may_repair_ocr": False,
            "may_change_candidate_rank": False,
        },
        "signals": {
            "data_row_count": len(profiles),
            "structural_numeric_cell_count": structural_count,
            "reliable_numeric_cell_count": reliable_count,
            "unreliable_numeric_cell_count": len(unreliable_refs),
            "numeric_parse_warning_counts": dict(sorted(warning_counts.items())),
            "unreliable_cell_refs": unreliable_refs,
            "numeric_column_alignment": alignment,
            "v3_context_status": source_status,
            "v3_numeric_header_coverage": v3_quality.get("numeric_header_coverage"),
        },
        "triage": {
            "action": action,
            "reason_codes": reasons,
        },
    }


def ocr_quality_manifest_path(path: Path) -> Path:
    if path.name == "ocr_quality_profiles_v1.jsonl":
        return path.with_name("ocr_quality_profile_v1.manifest.json")
    return path.with_suffix(".manifest.json")


def validate_ocr_quality_sidecar(
    bundle_dir: Path,
    structure_path: Path,
    context_path: Path,
    profile_path: Path,
) -> dict[str, Any]:
    """Verify a profile is an observational, hash-bound V2/V3 derivative."""
    bundle = Path(bundle_dir).resolve()
    structure = Path(structure_path).resolve()
    context = Path(context_path).resolve()
    profile = Path(profile_path).resolve()
    validate_structure_sidecar(bundle, structure)
    validate_evidence_context_sidecar(bundle, structure, context)
    manifest_path = ocr_quality_manifest_path(profile)
    if not profile.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("OCR quality sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("ocr_quality_profile_version") or 0) != OCR_QUALITY_PROFILE_VERSION:
        raise ValueError("Unsupported OCR quality profile version")
    if str(manifest.get("protocol") or "") != OCR_QUALITY_PROFILE_PROTOCOL:
        raise ValueError("OCR quality profile protocol mismatch")
    expected = {
        "input_bundle_tables_sha256": bundle / "tables.jsonl",
        "input_structure_sha256": structure,
        "input_evidence_context_sha256": context,
        "sidecar_sha256": profile,
    }
    for key, path in expected.items():
        if str(manifest.get(key) or "") != sha256_file(path):
            raise ValueError(f"OCR quality manifest does not match {path.name}")
    contract = manifest.get("source_contract") or {}
    if not bool(contract.get("raw_values_preserved")):
        raise ValueError("OCR quality sidecar must preserve raw values")
    if any(bool(contract.get(key)) for key in ("evidence_eligible", "training_eligible", "may_repair_ocr", "may_change_candidate_rank")):
        raise ValueError("OCR quality sidecar cannot be evidence/training/ranking eligible")
    return manifest
