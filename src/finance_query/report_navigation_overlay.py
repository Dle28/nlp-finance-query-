"""Fail-closed navigation-table guard derived from immutable raw table cells.

The overlay recognizes a narrow table-of-contents shape.  It does not alter a
raw table or its V2/V3 sidecars; it only prevents a detected navigation grid
from being dispatched to a financial-table semantic worker.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
import unicodedata

from .table_structure import sha256_file


REPORT_NAVIGATION_OVERLAY_PROTOCOL = "vifinqa_report_navigation_overlay_v1"
REPORT_NAVIGATION_OVERLAY_SCHEMA_VERSION = 1
OVERLAY_NAME = "report_navigation_overlay_v1.jsonl"
REPORT_NAME = "report_navigation_overlay_report.json"
MANIFEST_NAME = "report_navigation_overlay_manifest.json"
_PAGE_REFERENCE = re.compile(r"^\s*\d+\s*(?:[-\u2013]\s*\d+)?\s*$")


class ReportNavigationOverlayError(ValueError):
    """The navigation overlay is incomplete, malformed, or unsafe to consume."""


@dataclass(frozen=True, slots=True)
class ReportNavigationOverlayResult:
    output_dir: Path
    table_count: int
    contents_page_count: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(character for character in normalized if not unicodedata.combining(character))
    return " ".join(without_marks.casefold().split())


def _source_cell(row_index: int, column_index: int, literal: str) -> dict[str, Any]:
    return {
        "raw_row_index": row_index,
        "raw_column_index": column_index,
        "literal": literal,
        "literal_sha256": hashlib.sha256(literal.encode("utf-8")).hexdigest(),
    }


def detect_table_of_contents(raw_table: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return only a literal contents-page receipt, or ``None`` if it is absent.

    A match requires the first source row to be ``NỘI DUNG | TRANG`` and every
    following non-empty second-column cell to be an exact page locator.  This
    excludes normal financial schedules with a single incidental page number.
    """
    rows = raw_table.get("rows")
    if not isinstance(rows, list) or len(rows) < 3:
        return None
    first = rows[0]
    if not isinstance(first, list) or len(first) < 2:
        return None
    first_left, first_right = str(first[0] or "").strip(), str(first[1] or "").strip()
    if _fold(first_left) != "noi dung" or _fold(first_right) != "trang":
        return None

    page_cells: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows[1:], start=1):
        if not isinstance(row, list) or len(row) < 2:
            return None
        label, page_reference = str(row[0] or "").strip(), str(row[1] or "").strip()
        if not label:
            continue
        if not _PAGE_REFERENCE.fullmatch(page_reference):
            return None
        page_cells.append(_source_cell(row_index, 1, page_reference))
    if len(page_cells) < 2:
        return None
    return {
        "kind": "table_of_contents",
        "rule": "literal_contents_and_page_locator_grid_v1",
        "source_cells": [_source_cell(0, 0, first_left), _source_cell(0, 1, first_right), *page_cells],
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ReportNavigationOverlayError(f"Invalid JSONL: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise ReportNavigationOverlayError(f"Expected JSON object: {path}:{line_number}")
            rows.append(value)
    return rows


def _overlay_record(raw_table: Mapping[str, Any]) -> dict[str, Any]:
    table_uid = str(raw_table.get("internal_table_uid") or "")
    if not table_uid:
        raise ReportNavigationOverlayError("Raw table has no internal_table_uid")
    receipt = detect_table_of_contents(raw_table)
    payload: dict[str, Any] = {
        "schema_version": REPORT_NAVIGATION_OVERLAY_SCHEMA_VERSION,
        "protocol": REPORT_NAVIGATION_OVERLAY_PROTOCOL,
        "internal_table_uid": table_uid,
        "document_id": str(raw_table.get("document_id") or ""),
        "local_ordinal": raw_table.get("local_ordinal"),
        "raw_table_record_sha256": _sha_json(raw_table),
        "source_table_sha256": str(raw_table.get("table_sha256") or ""),
        "status": "NO_NAVIGATION_PATTERN_DETECTED",
        "source_shape": None,
        "table_semantic_dispatch_allowed": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    if receipt is not None:
        payload.update(
            {
                "status": "TABLE_OF_CONTENTS_DETECTED",
                "source_shape": receipt,
                "table_semantic_dispatch_allowed": False,
            }
        )
    return {"navigation_overlay_id": _sha_json(payload), **payload}


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def build_report_navigation_overlay(*, raw_tables: Path, output_dir: Path) -> ReportNavigationOverlayResult:
    """Build a new full-coverage, non-promotable navigation classification overlay."""
    raw_tables, output_dir = raw_tables.resolve(), output_dir.resolve()
    if not raw_tables.is_file():
        raise FileNotFoundError(raw_tables)
    if output_dir.exists() or output_dir == raw_tables:
        raise FileExistsError("output-dir must be new and distinct from the immutable raw tables")
    before_hash = sha256_file(raw_tables)
    raw_rows = _read_jsonl(raw_tables)
    table_uids = [str(row.get("internal_table_uid") or "") for row in raw_rows]
    if not raw_rows or "" in table_uids or len(table_uids) != len(set(table_uids)):
        raise ReportNavigationOverlayError("Raw table identities are incomplete or duplicated")
    overlays = [_overlay_record(row) for row in raw_rows]
    if sha256_file(raw_tables) != before_hash:
        raise ReportNavigationOverlayError("Overlay build changed the immutable raw tables")

    output_dir.mkdir(parents=True)
    overlay_path = output_dir / OVERLAY_NAME
    _write_jsonl(overlay_path, overlays)
    status_counts = Counter(str(row["status"]) for row in overlays)
    report = {
        "schema_version": REPORT_NAVIGATION_OVERLAY_SCHEMA_VERSION,
        "protocol": REPORT_NAVIGATION_OVERLAY_PROTOCOL,
        "run_status": "report_navigation_overlay_complete_not_promoted",
        "table_count": len(overlays),
        "status_counts": dict(sorted(status_counts.items())),
        "table_semantic_dispatch_blocked_count": status_counts["TABLE_OF_CONTENTS_DETECTED"],
        "input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "certification_allowed": False,
        "next_gate": "require_navigation_overlay_before_any_financial_table_semantic_dispatch",
    }
    report_path = output_dir / REPORT_NAME
    _write_json(report_path, report)
    manifest = {
        "schema_version": REPORT_NAVIGATION_OVERLAY_SCHEMA_VERSION,
        "protocol": REPORT_NAVIGATION_OVERLAY_PROTOCOL,
        "run_status": report["run_status"],
        "inputs": {"raw_tables": {"path": str(raw_tables), "sha256": before_hash}},
        "outputs": {
            OVERLAY_NAME: {"path": str(overlay_path), "sha256": sha256_file(overlay_path), "bytes": overlay_path.stat().st_size},
            REPORT_NAME: {"path": str(report_path), "sha256": sha256_file(report_path), "bytes": report_path.stat().st_size},
        },
        "table_count": len(overlays),
        "training_eligible": False,
        "certification_allowed": False,
    }
    _write_json(output_dir / MANIFEST_NAME, manifest)
    validate_report_navigation_overlay(output_dir)
    return ReportNavigationOverlayResult(
        output_dir=output_dir,
        table_count=len(overlays),
        contents_page_count=status_counts["TABLE_OF_CONTENTS_DETECTED"],
    )


def load_report_navigation_overlay(output_dir: Path) -> dict[str, dict[str, Any]]:
    """Load a validated overlay indexed by immutable table identity."""
    validate_report_navigation_overlay(output_dir)
    rows = _read_jsonl(output_dir.resolve() / OVERLAY_NAME)
    return {str(row["internal_table_uid"]): row for row in rows}


def require_financial_table_semantic_dispatch(
    overlay_by_table_uid: Mapping[str, Mapping[str, Any]],
    internal_table_uid: str,
) -> Mapping[str, Any]:
    """Fail closed if source layout proves the table is navigation, not finance."""
    overlay = overlay_by_table_uid.get(internal_table_uid)
    if overlay is None:
        raise ReportNavigationOverlayError("Navigation overlay has no record for the requested table")
    if overlay.get("protocol") != REPORT_NAVIGATION_OVERLAY_PROTOCOL:
        raise ReportNavigationOverlayError("Navigation overlay record uses an unsupported protocol")
    if overlay.get("table_semantic_dispatch_allowed") is not True:
        raise ReportNavigationOverlayError("Financial table semantic dispatch blocked: table-of-contents source shape")
    return overlay


def validate_report_navigation_overlay(output_dir: Path) -> dict[str, Any]:
    """Verify all records, hashes, and non-promotable source-shape receipts."""
    output_dir = output_dir.resolve()
    overlay_path, report_path, manifest_path = output_dir / OVERLAY_NAME, output_dir / REPORT_NAME, output_dir / MANIFEST_NAME
    if not all(path.is_file() for path in (overlay_path, report_path, manifest_path)):
        raise FileNotFoundError("Report navigation overlay is incomplete")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReportNavigationOverlayError("Report navigation overlay JSON is malformed") from error
    if (
        manifest.get("schema_version") != REPORT_NAVIGATION_OVERLAY_SCHEMA_VERSION
        or manifest.get("protocol") != REPORT_NAVIGATION_OVERLAY_PROTOCOL
        or manifest.get("run_status") != "report_navigation_overlay_complete_not_promoted"
        or manifest.get("training_eligible") is not False
        or manifest.get("certification_allowed") is not False
        or ((manifest.get("outputs") or {}).get(OVERLAY_NAME) or {}).get("sha256") != sha256_file(overlay_path)
        or ((manifest.get("outputs") or {}).get(REPORT_NAME) or {}).get("sha256") != sha256_file(report_path)
    ):
        raise ReportNavigationOverlayError("Report navigation overlay manifest is malformed")
    raw_input = (manifest.get("inputs") or {}).get("raw_tables") or {}
    raw_path = Path(str(raw_input.get("path") or ""))
    if not raw_path.is_file() or sha256_file(raw_path) != raw_input.get("sha256"):
        raise ReportNavigationOverlayError("Report navigation overlay input hash no longer matches the immutable raw tables")
    rows = _read_jsonl(overlay_path)
    table_uids = [str(row.get("internal_table_uid") or "") for row in rows]
    if not rows or "" in table_uids or len(table_uids) != len(set(table_uids)) or len(rows) != manifest.get("table_count"):
        raise ReportNavigationOverlayError("Report navigation overlay has incomplete table coverage")
    status_counts = Counter(str(row.get("status") or "") for row in rows)
    if report.get("status_counts") != dict(sorted(status_counts.items())) or report.get("table_count") != len(rows):
        raise ReportNavigationOverlayError("Report navigation overlay report is inconsistent")
    for row in rows:
        if (
            row.get("schema_version") != REPORT_NAVIGATION_OVERLAY_SCHEMA_VERSION
            or row.get("protocol") != REPORT_NAVIGATION_OVERLAY_PROTOCOL
            or row.get("training_eligible") is not False
            or row.get("certification_allowed") is not False
            or row.get("evidence_eligible") is not False
        ):
            raise ReportNavigationOverlayError("Report navigation overlay weakens the non-promotable contract")
        detected = row.get("status") == "TABLE_OF_CONTENTS_DETECTED"
        if detected != (row.get("table_semantic_dispatch_allowed") is False):
            raise ReportNavigationOverlayError("Report navigation overlay dispatch gate disagrees with status")
        if detected and not isinstance(row.get("source_shape"), Mapping):
            raise ReportNavigationOverlayError("Contents-page overlay record lacks source-cell evidence")
    return manifest
