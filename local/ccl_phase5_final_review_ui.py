#!/usr/bin/env python3
"""Serve a local-only, blind review UI for CCL Phase 5 assignments.

The server verifies the immutable calibration package before rendering it. It
never reads the model-comparison ledger, rewrites the assignment/template, or
opens a network listener beyond localhost.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_calibration_v1"
RESPONSE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_response_v1"
SCHEMA_VERSION = 1
ASSIGNMENT_NAME = "component_selection_final_review_assignment_v1.jsonl"
TEMPLATE_NAME = "component_selection_final_review_response_template_v1.jsonl"
MANIFEST_NAME = "component_selection_final_review_calibration_manifest.json"
ALLOWED_UNRESOLVED = {
    "ambiguous_source_components",
    "insufficient_source_components",
    "source_text_damage",
}
RESPONSE_KEYS = {
    "schema_version",
    "protocol",
    "calibration_item_id",
    "immutable_assignment_sha256",
    "reviewer_id",
    "reviewed_at_utc",
    "review_decision",
    "primary_component_id",
    "supporting_component_ids",
    "unresolved_conditions",
    "source_coordinates_checked",
    "training_eligible",
    "certification_allowed",
}
PAGE_REFERENCE = re.compile(r"^\s*\d+\s*(?:[-–]\s*\d+)?\s*$")


@dataclass(frozen=True)
class ReviewPackage:
    assignments: tuple[dict[str, Any], ...]
    template_by_id: dict[str, dict[str, Any]]
    table_previews: dict[str, dict[str, Any]]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected a JSON object: {path}:{line_number}")
            rows.append(value)
    return rows


def _require_non_promotable(row: Mapping[str, Any], *, label: str) -> None:
    if row.get("training_eligible") is not False or row.get("certification_allowed") is not False:
        raise ValueError(f"{label} is unexpectedly promotable")


def _id_map(rows: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    output = {str(row.get("calibration_item_id") or ""): row for row in rows}
    if not output or "" in output or len(output) != len(rows):
        raise ValueError(f"{label} identities are malformed")
    return output


def _table_ids(assignments: tuple[dict[str, Any], ...]) -> set[str]:
    table_ids = {
        str((assignment.get("review_packet") or {}).get("internal_table_uid") or "")
        for assignment in assignments
    }
    if "" in table_ids:
        raise ValueError("Review assignment lacks an internal table identity")
    return table_ids


def _source_shape_hint(grid: Mapping[str, Any]) -> dict[str, str]:
    """Recognize a contents page from literal cells, without relabeling the source."""
    rows = grid.get("rows") or []
    if not isinstance(rows, list):
        return {}
    readable_rows = [
        [str(cell or "").strip() for cell in row]
        for row in rows
        if isinstance(row, list) and len(row) >= 2
    ]
    if len(readable_rows) < 3:
        return {}
    first = readable_rows[0]
    page_references = [row[1] for row in readable_rows[1:] if row[0]]
    if (
        first[0].casefold() == "nội dung"
        and first[1].casefold() == "trang"
        and len(page_references) >= 2
        and all(PAGE_REFERENCE.fullmatch(value) for value in page_references)
    ):
        return {
            "kind": "table_of_contents",
            "evidence": f'Nội dung | Trang; {len(page_references)} dòng chỉ số trang',
        }
    return {}


def _source_context(normalized_table: Mapping[str, Any]) -> dict[str, Any]:
    """Expose literal report context without treating it as a table header."""
    outside = normalized_table.get("outside_table_context") or {}
    document = normalized_table.get("document") or {}
    quality = normalized_table.get("quality") or {}
    grid = normalized_table.get("canonical_grid") or {}
    columns = grid.get("columns") or []
    if not all(isinstance(value, Mapping) for value in (outside, document, quality, grid)):
        raise ValueError("Normalized table context is malformed")
    if not isinstance(columns, list):
        raise ValueError("Normalized table columns are malformed")
    return {
        "source_heading": str(outside.get("source_heading") or "").strip(),
        "reader_heading": str(outside.get("reader_heading") or "").strip(),
        "document": {
            "document_id": str(document.get("document_id") or outside.get("document_id") or "unknown document"),
            "ticker": str(document.get("ticker") or outside.get("ticker") or ""),
            "report_year": document.get("report_year") or outside.get("report_year"),
            "scope": str(document.get("scope") or outside.get("scope") or ""),
        },
        "quality": {
            "status": str(quality.get("status") or "unknown"),
            "reason_codes": [str(value) for value in quality.get("reason_codes") or []],
        },
        "source_shape_hint": _source_shape_hint(grid),
        # These labels describe the UI grid. A blank source_label means there
        # was no header text in the immutable source to display as semantic fact.
        "columns": [
            {
                "column_index": int(column.get("column_index") or index),
                "display_label": str(column.get("canonical_label") or ""),
                "source_label": str(column.get("source_label") or "").strip(),
                "role": str(column.get("role") or "unknown"),
            }
            for index, column in enumerate(columns)
            if isinstance(column, Mapping)
        ],
    }


def load_table_contexts(
    assignments: tuple[dict[str, Any], ...],
    normalized_tables_path: Path,
) -> dict[str, dict[str, Any]]:
    if not normalized_tables_path.is_file():
        raise FileNotFoundError(normalized_tables_path)
    table_ids = _table_ids(assignments)
    found: dict[str, dict[str, Any]] = {}
    with normalized_tables_path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            table_id = str(row.get("internal_table_uid") or "")
            if table_id in table_ids:
                found[table_id] = _source_context(row)
    missing = table_ids - set(found)
    if missing:
        raise ValueError(f"Normalized source context is missing: {sorted(missing)}")
    return found


def _table_preview(
    table: Mapping[str, Any],
    source_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = table.get("rows")
    labels = table.get("column_labels")
    if not isinstance(rows, list) or not isinstance(labels, list):
        raise ValueError("Structured table has no readable grid")
    preview = {
        "internal_table_uid": str(table["internal_table_uid"]),
        "document_id": str(table.get("document_id") or "unknown document"),
        "column_labels": [str(value or "") for value in labels],
        # The table is evidence context, not a second retrieval corpus. A small
        # bounded preview keeps the review focused on the assignment at hand.
        "rows": [[str(cell or "") for cell in row] for row in rows[:30] if isinstance(row, list)],
        "truncated": len(rows) > 30,
    }
    if source_context is not None:
        preview["source_context"] = dict(source_context)
    return preview


def load_table_previews(
    assignments: tuple[dict[str, Any], ...],
    structured_tables_path: Path,
    normalized_tables_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    if not structured_tables_path.is_file():
        raise FileNotFoundError(structured_tables_path)
    table_ids = _table_ids(assignments)
    contexts = load_table_contexts(assignments, normalized_tables_path) if normalized_tables_path else {}
    found: dict[str, dict[str, Any]] = {}
    with structured_tables_path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            table_id = str(row.get("internal_table_uid") or "")
            if table_id in table_ids:
                found[table_id] = _table_preview(row, contexts.get(table_id))
    missing = table_ids - set(found)
    if missing:
        raise ValueError(f"Structured table preview is missing: {sorted(missing)}")
    return {
        str(assignment["calibration_item_id"]): found[str((assignment.get("review_packet") or {})["internal_table_uid"])]
        for assignment in assignments
    }


def load_review_package(
    assignment_path: Path,
    template_path: Path,
    manifest_path: Path,
    structured_tables_path: Path | None = None,
    normalized_tables_path: Path | None = None,
) -> ReviewPackage:
    """Verify every UI input before exposing the blinded component menus."""
    for path in (assignment_path, template_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    manifest = load_json(manifest_path)
    outputs = manifest.get("outputs") or {}
    if (
        manifest.get("protocol") != CALIBRATION_PROTOCOL
        or manifest.get("run_status") != "final_review_calibration_assignment_ready_not_scored"
        or manifest.get("model_decisions_blinded") is not True
        or manifest.get("human_review_required") is not True
    ):
        raise ValueError("Calibration package is not a blind, unscored final-review package")
    _require_non_promotable(manifest, label="calibration manifest")
    for path, name in ((assignment_path, ASSIGNMENT_NAME), (template_path, TEMPLATE_NAME)):
        if (outputs.get(name) or {}).get("sha256") != sha256_file(path):
            raise ValueError(f"SHA-256 mismatch for {name}")

    assignment_by_id = _id_map(load_jsonl(assignment_path), label="final-review assignment")
    template_by_id = _id_map(load_jsonl(template_path), label="final-review response template")
    if set(assignment_by_id) != set(template_by_id):
        raise ValueError("Assignment and response template do not cover the same items")
    for item_id, assignment in assignment_by_id.items():
        if (
            assignment.get("protocol") != CALIBRATION_PROTOCOL
            or assignment.get("model_decisions_blinded") is not True
        ):
            raise ValueError(f"Assignment {item_id} weakens the blind review contract")
        _require_non_promotable(assignment, label=f"assignment {item_id}")
        packet = assignment.get("review_packet") or {}
        components = packet.get("components") if isinstance(packet, Mapping) else None
        if not isinstance(components, list) or not components:
            raise ValueError(f"Assignment {item_id} has no component menu")
        component_ids = [str(component.get("component_id") or "") for component in components if isinstance(component, Mapping)]
        if len(component_ids) != len(components) or "" in component_ids or len(set(component_ids)) != len(component_ids):
            raise ValueError(f"Assignment {item_id} component identifiers are malformed")
        template = template_by_id[item_id]
        if (
            set(template) != RESPONSE_KEYS
            or template.get("protocol") != RESPONSE_PROTOCOL
            or template.get("immutable_assignment_sha256") != canonical_sha(assignment)
            or any(template.get(key) is not None for key in ("reviewer_id", "reviewed_at_utc", "review_decision", "primary_component_id"))
            or template.get("supporting_component_ids") != []
            or template.get("unresolved_conditions") != []
            or template.get("source_coordinates_checked") is not None
        ):
            raise ValueError(f"Response template {item_id} is not blank or correctly bound")
        _require_non_promotable(template, label=f"response template {item_id}")
    assignments = tuple(assignment_by_id[item_id] for item_id in sorted(assignment_by_id))
    return ReviewPackage(
        assignments=assignments,
        template_by_id=template_by_id,
        table_previews=(
            load_table_previews(assignments, structured_tables_path, normalized_tables_path)
            if structured_tables_path
            else {}
        ),
    )


def _component_menu(assignment: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    packet = assignment.get("review_packet") or {}
    components = packet.get("components") if isinstance(packet, Mapping) else []
    return {str(component["component_id"]): component for component in components}


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def build_response_rows(
    package: ReviewPackage,
    payload: Mapping[str, Any],
    *,
    reviewed_at_utc: str | None = None,
) -> list[dict[str, Any]]:
    """Turn browser form data into scorer-compatible, non-promotable rows."""
    reviewer_id = _required_str(payload, "reviewer_id")
    submitted = payload.get("items")
    if not isinstance(submitted, list):
        raise ValueError("items must be a list")
    submitted_by_id = _id_map(
        [item for item in submitted if isinstance(item, dict)],
        label="submitted review",
    )
    assignment_by_id = {str(row["calibration_item_id"]): row for row in package.assignments}
    if set(submitted_by_id) != set(assignment_by_id) or len(submitted) != len(submitted_by_id):
        raise ValueError("Responses must cover every assignment exactly once")
    timestamp = reviewed_at_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []
    for item_id, assignment in assignment_by_id.items():
        submitted_item = submitted_by_id[item_id]
        if submitted_item.get("source_coordinates_checked") is not True:
            raise ValueError(f"{item_id}: source coordinates must be checked")
        decision = submitted_item.get("review_decision")
        primary = submitted_item.get("primary_component_id")
        supporting = submitted_item.get("supporting_component_ids")
        unresolved = submitted_item.get("unresolved_conditions")
        if (
            not isinstance(supporting, list)
            or len(supporting) > 4
            or len(set(supporting)) != len(supporting)
            or not all(isinstance(component_id, str) for component_id in supporting)
            or not isinstance(unresolved, list)
            or not all(isinstance(reason, str) and reason in ALLOWED_UNRESOLVED for reason in unresolved)
        ):
            raise ValueError(f"{item_id}: component selection is malformed")
        menu = _component_menu(assignment)
        if decision == "SELECT_COMPONENTS":
            if not isinstance(primary, str) or menu.get(primary, {}).get("role") != "report_scope_or_selected_relation_context":
                raise ValueError(f"{item_id}: select one permitted primary context")
            if any(menu.get(component_id, {}).get("role") not in {"column_header", "row_label"} for component_id in supporting):
                raise ValueError(f"{item_id}: supporting component is outside the permitted menu")
            unresolved = []
        elif decision == "ABSTAIN_UNRESOLVED":
            if primary is not None or supporting or not unresolved:
                raise ValueError(f"{item_id}: abstention needs a reason and no components")
        else:
            raise ValueError(f"{item_id}: choose components or abstain")
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol": RESPONSE_PROTOCOL,
                "calibration_item_id": item_id,
                "immutable_assignment_sha256": canonical_sha(assignment),
                "reviewer_id": reviewer_id,
                "reviewed_at_utc": timestamp,
                "review_decision": decision,
                "primary_component_id": primary,
                "supporting_component_ids": supporting,
                "unresolved_conditions": unresolved,
                "source_coordinates_checked": True,
                "training_eligible": False,
                "certification_allowed": False,
            }
        )
    return rows


def write_response_rows(path: Path, rows: list[Mapping[str, Any]]) -> None:
    """Create the response file once; immutable package files are never targets."""
    if path.exists():
        raise FileExistsError(f"Response output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _public_item(package: ReviewPackage, assignment: Mapping[str, Any]) -> dict[str, Any]:
    packet = assignment["review_packet"]
    return {
        "calibration_item_id": assignment["calibration_item_id"],
        "review_stratum": assignment["review_stratum"],
        "source_quality_status": packet.get("source_quality_status"),
        "source_quality_reason_codes": packet.get("source_quality_reason_codes") or [],
        "task": packet.get("task") or {},
        "components": packet.get("components") or [],
        "table_preview": package.table_previews.get(str(assignment["calibration_item_id"])),
    }


def page_html() -> str:
    return r'''<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CCL Final Review</title>
  <style>
    :root { --paper:#f6f1e6; --ink:#18332e; --muted:#66736c; --line:#cfbe9d; --accent:#b7502a; --accent-deep:#823617; --forest:#1f5b4f; --mist:#e3ebe2; --sun:#e8bb55; --error:#8c2d25; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:linear-gradient(115deg,rgba(183,80,42,.07),transparent 35%), repeating-linear-gradient(0deg,transparent 0 27px,rgba(24,51,46,.035) 28px),var(--paper); font-family:"Iowan Old Style","Palatino Linotype","Book Antiqua",serif; }
    button,input { font:inherit; }
    .masthead { padding:28px clamp(18px,5vw,72px) 22px; border-bottom:1px solid var(--line); display:flex; align-items:end; justify-content:space-between; gap:24px; background:rgba(246,241,230,.86); }
    .eyebrow { margin:0 0 7px; font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; letter-spacing:.13em; text-transform:uppercase; font-size:12px; color:var(--accent-deep); }
    h1,h2,h3,p { margin-top:0; } h1 { font-size:clamp(30px,5vw,50px); font-weight:500; line-height:1; letter-spacing:-.04em; margin-bottom:0; } h2 { font-size:23px; font-weight:500; margin-bottom:8px; } h3 { font-size:16px; font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; letter-spacing:.08em; text-transform:uppercase; color:var(--forest); margin-bottom:8px; }
    .boundary { max-width:340px; padding:10px 13px; color:#fff; background:var(--forest); font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; font-size:13px; line-height:1.35; }
    .shell { display:grid; grid-template-columns:255px minmax(0,1fr); max-width:1320px; margin:0 auto; }
    aside { border-right:1px solid var(--line); min-height:calc(100vh - 122px); padding:28px 20px; background:rgba(227,235,226,.46); }
    .reviewer label,.reviewer input { display:block; width:100%; } .reviewer label { font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; letter-spacing:.08em; text-transform:uppercase; font-size:12px; margin-bottom:7px; } .reviewer input { padding:10px; border:1px solid var(--forest); border-radius:0; background:#fffdf7; color:var(--ink); } .reviewer-help { margin:7px 0 0; color:var(--muted); font-size:13px; line-height:1.35; }
    .progress { margin:26px 0 13px; font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; font-size:13px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
    .progress strong { display:block; color:var(--ink); font-size:26px; letter-spacing:0; margin-top:2px; }
    #item-nav { display:grid; gap:7px; } .nav-item { width:100%; text-align:left; padding:10px 11px; border:1px solid var(--line); background:transparent; color:var(--ink); cursor:pointer; } .nav-item:hover,.nav-item:focus { border-color:var(--accent); background:rgba(183,80,42,.08); } .nav-item.complete { border-left:5px solid var(--forest); }
    .save { width:100%; margin-top:28px; padding:13px 12px; border:0; border-radius:0; background:var(--accent); color:#fff; font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; font-size:14px; letter-spacing:.09em; text-transform:uppercase; cursor:pointer; } .save:hover,.save:focus { background:var(--accent-deep); } .save:disabled { opacity:.56; cursor:not-allowed; }
    .message { margin-top:14px; font-size:14px; line-height:1.4; } .message.error { color:var(--error); } .message.success { color:var(--forest); }
    main { padding:clamp(22px,4vw,54px); } .intro { max-width:810px; padding-bottom:27px; border-bottom:3px solid var(--ink); } .intro p { margin-bottom:0; color:var(--muted); font-size:17px; line-height:1.5; } .how-to { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:21px; } .how-step { padding:13px; border-top:3px solid var(--accent); background:rgba(255,253,247,.8); } .how-step strong { display:block; margin-bottom:5px; font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; letter-spacing:.08em; text-transform:uppercase; font-size:12px; color:var(--accent-deep); } .how-step span { font-size:15px; line-height:1.35; }
    #review-list { display:grid; gap:30px; margin-top:30px; } .review-card { scroll-margin-top:20px; padding:23px; border:1px solid var(--line); background:rgba(255,253,247,.8); box-shadow:8px 8px 0 rgba(31,91,79,.08); } .card-top { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; } .item-label { color:var(--accent-deep); font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; letter-spacing:.08em; font-size:12px; text-transform:uppercase; } .stratum { max-width:280px; color:var(--muted); font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; font-size:12px; text-align:right; letter-spacing:.06em; }
    .notice { margin:15px 0 19px; padding:10px 12px; border-left:4px solid var(--sun); background:#fff7dc; color:#5a4824; font-size:14px; }
    .source-context { margin:17px 0; padding:16px; border:1px solid var(--forest); background:rgba(227,235,226,.42); } .source-context h3 { margin-bottom:11px; } .context-facts { display:grid; grid-template-columns:150px minmax(0,1fr); gap:7px 14px; margin:0; font-size:14px; line-height:1.4; } .context-facts dt { color:var(--muted); font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; letter-spacing:.05em; text-transform:uppercase; font-size:12px; } .context-facts dd { margin:0; } .source-quote { padding:9px 11px; border-left:3px solid var(--forest); background:#fffdf7; font-size:16px; } .semantic-warning { margin:14px 0 0; padding:11px 12px; border-left:4px solid var(--sun); background:#fff7dc; color:#5a4824; font-size:14px; line-height:1.45; } .semantic-warning strong { color:#473811; }
    .source-table { margin:17px 0; border:1px solid var(--forest); background:#fffdf7; } .source-table summary { padding:12px 14px; color:var(--forest); cursor:pointer; font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; font-size:15px; font-weight:500; letter-spacing:.04em; } .source-table-copy { padding:0 14px 12px; color:var(--muted); font-size:14px; line-height:1.4; } .table-scroll { overflow-x:auto; border-top:1px solid var(--line); } table { width:100%; border-collapse:collapse; font-size:13px; } th,td { min-width:128px; padding:9px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); text-align:left; vertical-align:top; line-height:1.35; } th { background:var(--mist); color:var(--forest); font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; font-weight:500; } th span,th small { display:block; } th small { margin-top:3px; color:var(--muted); font-family:"Iowan Old Style","Palatino Linotype","Book Antiqua",serif; font-size:11px; font-weight:400; line-height:1.25; } .table-note { margin:0; padding:10px 14px; color:var(--muted); font-size:13px; }
    .decision { display:flex; flex-wrap:wrap; gap:11px; margin:18px 0; } .choice { display:flex; align-items:center; gap:8px; padding:9px 11px; border:1px solid var(--line); background:#fffdf7; cursor:pointer; } .choice:has(input:checked) { border-color:var(--accent); box-shadow:inset 0 -3px var(--accent); }
    .selection,.abstain { display:none; margin-top:18px; } .review-card[data-decision="SELECT_COMPONENTS"] .selection,.review-card[data-decision="ABSTAIN_UNRESOLVED"] .abstain { display:block; }
    .component-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; } .component-group { padding:15px; border:1px solid var(--line); background:rgba(227,235,226,.38); } .component-group.context { grid-column:1 / -1; background:rgba(232,187,85,.14); }
    .component-option { display:block; padding:11px 0; border-top:1px solid rgba(207,190,157,.75); cursor:pointer; } .component-option:first-of-type { border-top:0; } .component-option input { margin-right:8px; accent-color:var(--accent); } .literal { display:block; margin:5px 0 0 25px; font-size:15px; line-height:1.35; } .coord { display:block; margin:5px 0 0 25px; color:var(--muted); font-family:"Avenir Next Condensed","Arial Narrow",sans-serif; font-size:12px; letter-spacing:.03em; }
    .coordinate-check { display:flex; gap:9px; margin-top:20px; padding-top:16px; border-top:1px solid var(--line); font-size:15px; } .coordinate-check input { accent-color:var(--forest); }
    .abstain-list { display:flex; flex-wrap:wrap; gap:10px; } .abstain-list label { padding:9px; border:1px solid var(--line); background:#fffdf7; cursor:pointer; }
    @media (max-width:760px) { .masthead { display:block; } .boundary { margin-top:18px; } .shell { display:block; } aside { min-height:0; border-right:0; border-bottom:1px solid var(--line); } #item-nav { grid-template-columns:repeat(5,minmax(0,1fr)); } .nav-item { min-height:46px; padding:5px; font-size:11px; } .save { margin-top:18px; } .component-grid,.how-to { grid-template-columns:1fr; } .component-group.context { grid-column:auto; } .card-top { display:block; } .stratum { margin-top:9px; text-align:left; } }
  </style>
</head>
<body>
  <header class="masthead"><div><p class="eyebrow">ViFinQA / CCL Phase 5</p><h1>Review nguồn, từng bước một.</h1></div><div class="boundary">Bạn không cần tìm đáp án hay đoán loại bảng. Chỉ quyết định: những đoạn text nào đủ để hiểu ngữ cảnh của bảng?</div></header>
  <div class="shell"><aside><div class="reviewer"><label for="reviewer-id">Tên hoặc mã người review</label><input id="reviewer-id" autocomplete="off" placeholder="ví dụ: dungle-review-01"><p class="reviewer-help">Dùng một tên/mã ổn định để lưu cùng phản hồi của bạn.</p></div><div class="progress">Đã hoàn thành<strong id="progress-count">0 / 0</strong></div><nav id="item-nav" aria-label="Các item cần review"></nav><button id="save" class="save" type="button">Hoàn tất và lưu phản hồi</button><div id="message" class="message" role="status"></div></aside><main><section class="intro"><p class="eyebrow">Hướng dẫn nhanh</p><h2>Bạn chỉ cần làm ba việc cho mỗi item.</h2><p>Đọc các đoạn text nguồn. Nếu chúng đủ rõ, chọn ngữ cảnh chính và các chi tiết hỗ trợ. Nếu còn mơ hồ, chọn “Chưa thể kết luận” - đây là phản hồi hoàn toàn hợp lệ.</p><div class="how-to"><div class="how-step"><strong>Bước 1</strong><span>Chọn một trong hai: nguồn đủ rõ, hoặc chưa thể kết luận.</span></div><div class="how-step"><strong>Bước 2</strong><span>Nếu đủ rõ: chọn 1 ngữ cảnh chính, rồi thêm header/dòng liên quan nếu cần.</span></div><div class="how-step"><strong>Bước 3</strong><span>Tick ô đã đối chiếu text nguồn. Làm đủ 5 item rồi bấm lưu.</span></div></div></section><section id="review-list" aria-live="polite"></section></main></div>
  <script>
    const reviewState = { items: [] };
    const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[character]));
    const shortId = value => String(value).slice(0, 10);
    const coordinateText = component => {
      const provenance = component.provenance || {};
      const bits = [provenance.raw_row_index, provenance.raw_column_index, provenance.canonical_column_index]
        .filter(value => Number.isInteger(value)).map(value => `coord ${value}`);
      return bits.length ? bits.join(" · ") : "literal source context";
    };
    const grouped = components => ({
      context: components.filter(component => component.role === "report_scope_or_selected_relation_context"),
      header: components.filter(component => component.role === "column_header"),
      row: components.filter(component => component.role === "row_label"),
    });
    const unresolvedLabels = { ambiguous_source_components:"Các đoạn nguồn có thể hiểu theo nhiều cách", insufficient_source_components:"Thiếu đoạn nguồn cần thiết", source_text_damage:"Text nguồn bị lỗi hoặc khó đọc" };
    const qualityLabels = { generic_column_header:"cột giá trị không có tiêu đề trong nguồn", generic_table_semantics:"ý nghĩa bảng chưa được xác nhận", ambiguous_heading:"tiêu đề nguồn còn mơ hồ" };
    const componentGroup = (title, name, type, components, cardId, extraClass = "") => {
      if (!components.length) return "";
      return `<section class="component-group ${extraClass}"><h3>${title}</h3>${components.map(component => `<label class="component-option"><input type="${type}" name="${name}" value="${escapeHtml(component.component_id)}"><span>${escapeHtml(shortId(component.component_id))}</span><span class="literal">${escapeHtml(component.literal)}</span><span class="coord">${escapeHtml(coordinateText(component))}</span></label>`).join("")}</section>`;
    };
    const sourceContext = preview => {
      const context = preview.source_context;
      if (!context) return `<section class="source-context"><h3>Ngữ cảnh báo cáo</h3><p class="semantic-warning"><strong>Thiếu bản ghi ngữ cảnh đã chuẩn hóa.</strong> Không dùng các nhãn lưới để đoán ý nghĩa; nếu component menu không đủ rõ, chọn “Chưa thể kết luận”.</p></section>`;
      const document = context.document || {};
      const quality = context.quality || {};
      const facts = [document.ticker, document.report_year ? `năm ${document.report_year}` : "", document.scope].filter(Boolean).join(" · ");
      const sourceHeading = context.source_heading || "Không trích được tiêu đề nguồn cho bảng này.";
      const reasons = (quality.reason_codes || []).map(reason => qualityLabels[reason] || reason).join("; ");
      const shapeHint = context.source_shape_hint || {};
      const shapeWarning = shapeHint.kind === "table_of_contents"
        ? `<p class="semantic-warning"><strong>Dạng nguồn: mục lục / chỉ mục trang.</strong> ${escapeHtml(shapeHint.evidence || "")} là vị trí trang, không phải giá trị tài chính. Giữ phản hồi “Chưa thể kết luận” nếu menu không có ngữ cảnh phù hợp.</p>`
        : "";
      const status = quality.status === "needs_review"
        ? `Bảng này mới chuẩn hóa cấu trúc, chưa chuẩn hóa ý nghĩa: ${reasons || "cần kiểm tra thêm"}.`
        : `Trạng thái chuẩn hóa: ${quality.status || "không rõ"}.`;
      return `<section class="source-context"><h3>Ngữ cảnh đã liên kết từ báo cáo</h3><dl class="context-facts"><dt>Tài liệu</dt><dd>${escapeHtml(document.document_id || preview.document_id)}</dd><dt>Phạm vi</dt><dd>${escapeHtml(facts || "không có metadata bổ sung")}</dd><dt>Tiêu đề nguồn</dt><dd class="source-quote">${escapeHtml(sourceHeading)}</dd></dl>${shapeWarning}<p class="semantic-warning"><strong>${escapeHtml(status)}</strong> Tiêu đề này chỉ là ngữ cảnh của bảng, không phải tiêu đề cho các cột giá trị. Không suy ra năm, đơn vị hoặc tên chỉ số khi nguồn không ghi.</p></section>`;
    };
    const sourceTable = preview => {
      if (!preview) return `<div class="notice">Không tìm thấy bảng nguồn tương ứng. Hãy chọn “Chưa thể kết luận” và ghi lý do thiếu đoạn nguồn.</div>`;
      const labels = preview.column_labels.length ? preview.column_labels : (preview.rows[0] || []);
      const columns = (preview.source_context || {}).columns || [];
      const header = labels.map((label, index) => {
        const column = columns.find(value => value.column_index === index);
        if (!column) return `<th><span>${escapeHtml(label)}</span></th>`;
        if (column.source_label) return `<th><span>${escapeHtml(column.source_label)}</span><small>Tiêu đề có trong nguồn</small></th>`;
        const roleNote = column.role === "row_label" ? "Nhãn dòng là vai trò lưới, không phải tiêu đề nguồn" : "Không có tiêu đề trong nguồn";
        return `<th><span>Cột ${index + 1}</span><small>${escapeHtml(roleNote)}</small></th>`;
      }).join("");
      const body = preview.rows.map(row => `<tr>${labels.map((label, index) => `<td>${escapeHtml(row[index] || "")}</td>`).join("")}</tr>`).join("");
      const note = preview.truncated ? "Bảng dài hơn 30 dòng; UI chỉ hiển thị 30 dòng đầu để review tập trung." : "Đây là toàn bộ phần bảng có trong source preview.";
      return `<details class="source-table" open><summary>Bảng nguồn đang được nói tới - bấm để thu gọn</summary><p class="source-table-copy">Hãy đọc bảng này trước. Câu hỏi duy nhất là: các đoạn bạn chọn có thật sự mô tả bảng này không? Nếu không thể thấy liên hệ trực tiếp, hãy chọn “Chưa thể kết luận”.</p><div class="table-scroll"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div><p class="table-note">${escapeHtml(preview.document_id)} · ${escapeHtml(note)}</p></details>`;
    };
    const card = (item, index) => {
      const groups = grouped(item.components);
      return `<article class="review-card" id="item-${index}" data-item-id="${escapeHtml(item.calibration_item_id)}" data-decision=""><div class="card-top"><div><p class="item-label">Item ${index + 1} · ${escapeHtml(shortId(item.calibration_item_id))}</p><h2>Những đoạn nào giải thích đúng ngữ cảnh?</h2></div><div class="stratum">${escapeHtml(item.review_stratum)}</div></div><div class="notice">Đọc khung ngữ cảnh và bảng trước. Chỉ chọn literal có trong menu; không tự đặt tên cho cột hoặc suy ra năm, đơn vị, đáp án.</div>${sourceContext(item.table_preview || {})}${sourceTable(item.table_preview)}<div class="decision"><label class="choice"><input type="radio" name="decision-${index}" value="SELECT_COMPONENTS"> Nguồn đủ rõ - tôi sẽ chọn các đoạn phù hợp</label><label class="choice"><input type="radio" name="decision-${index}" value="ABSTAIN_UNRESOLVED"> Chưa thể kết luận từ nguồn hiện có</label></div><div class="selection"><div class="component-grid">${componentGroup("Bước 2a - ngữ cảnh chính (chọn đúng 1)", `primary-${index}`, "radio", groups.context, index, "context")}${componentGroup("Bước 2b - tiêu đề cột (không bắt buộc, tối đa 4 mục tổng cộng)", `support-${index}`, "checkbox", groups.header, index)}${componentGroup("Bước 2c - nhãn dòng (không bắt buộc, tối đa 4 mục tổng cộng)", `support-${index}`, "checkbox", groups.row, index)}</div></div><div class="abstain"><h3>Vì sao chưa thể kết luận? (chọn ít nhất 1)</h3><div class="abstain-list">${Object.entries(unresolvedLabels).map(([reason, label]) => `<label><input type="checkbox" name="unresolved-${index}" value="${reason}"> ${label}</label>`).join("")}</div></div><label class="coordinate-check"><input type="checkbox" name="coordinates-${index}"> Bước cuối: tôi đã đối chiếu các đoạn text nguồn của item này.</label></article>`;
    };
    const updateProgress = () => {
      const cards = [...document.querySelectorAll(".review-card")];
      const complete = cards.filter(card => card.dataset.decision && card.querySelector("input[name^='coordinates-']").checked).length;
      document.getElementById("progress-count").textContent = `${complete} / ${cards.length}`;
      cards.forEach((card, index) => document.querySelector(`[data-nav-index="${index}"]`).classList.toggle("complete", Boolean(card.dataset.decision && card.querySelector("input[name^='coordinates-']").checked)));
    };
    const bindCard = (element, index) => {
      element.querySelectorAll(`input[name="decision-${index}"]`).forEach(input => input.addEventListener("change", event => { element.dataset.decision = event.target.value; updateProgress(); }));
      element.querySelectorAll("input").forEach(input => input.addEventListener("change", () => {
        const supports = [...element.querySelectorAll(`input[name="support-${index}"]:checked`)];
        if (supports.length > 4) { input.checked = false; showMessage("Bạn chỉ có thể chọn tối đa 4 chi tiết hỗ trợ cho mỗi item.", true); }
        updateProgress();
      }));
    };
    const showMessage = (text, error = false) => { const message = document.getElementById("message"); message.textContent = text; message.className = `message ${error ? "error" : "success"}`; };
    const collect = () => ({
      reviewer_id: document.getElementById("reviewer-id").value,
      items: reviewState.items.map((item, index) => {
        const card = document.getElementById(`item-${index}`);
        const decision = card.querySelector(`input[name="decision-${index}"]:checked`);
        return { calibration_item_id: item.calibration_item_id, review_decision: decision ? decision.value : null, primary_component_id: card.querySelector(`input[name="primary-${index}"]:checked`)?.value || null, supporting_component_ids: [...card.querySelectorAll(`input[name="support-${index}"]:checked`)].map(input => input.value), unresolved_conditions: [...card.querySelectorAll(`input[name="unresolved-${index}"]:checked`)].map(input => input.value), source_coordinates_checked: card.querySelector(`input[name="coordinates-${index}"]`).checked };
      }),
    });
    const save = async () => {
      const button = document.getElementById("save"); button.disabled = true; showMessage("Đang kiểm tra 5 phản hồi của bạn...");
      try { const response = await fetch("/api/save", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(collect()) }); const body = await response.json(); if (!response.ok) throw new Error(body.error || "Chưa thể lưu phản hồi"); showMessage(`Đã lưu ${body.response_count} phản hồi. Bạn có thể báo lại để chạy bước chấm điểm.`); } catch (error) { showMessage(error.message, true); button.disabled = false; }
    };
    const render = data => { reviewState.items = data.items; document.getElementById("review-list").innerHTML = data.items.map(card).join(""); document.getElementById("item-nav").innerHTML = data.items.map((item, index) => `<button class="nav-item" type="button" data-nav-index="${index}">Item ${index + 1}</button>`).join(""); document.querySelectorAll(".nav-item").forEach((button, index) => button.addEventListener("click", () => document.getElementById(`item-${index}`).scrollIntoView({behavior:"smooth", block:"start"}))); data.items.forEach((item, index) => bindCard(document.getElementById(`item-${index}`), index)); document.getElementById("save").addEventListener("click", save); updateProgress(); };
    fetch("/api/review").then(response => response.ok ? response.json() : Promise.reject(new Error("Không thể tải review package"))).then(render).catch(error => showMessage(error.message, true));
  </script>
</body>
</html>'''


class ReviewHandler(BaseHTTPRequestHandler):
    package: ReviewPackage
    output_path: Path

    def log_message(self, format: str, *args: object) -> None:  # pragma: no cover - quiet local server
        return

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            encoded = page_html().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if path == "/api/review":
            self._send_json(HTTPStatus.OK, {"items": [_public_item(self.package, item) for item in self.package.assignments]})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/save":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
            if not 0 < content_length <= 1_000_000:
                raise ValueError("Invalid response payload size")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("Response payload must be a JSON object")
            rows = build_response_rows(self.package, payload)
            write_response_rows(self.output_path, rows)
        except (FileExistsError, ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.CONFLICT if isinstance(error, FileExistsError) else HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.CREATED, {"output": str(self.output_path), "response_count": len(rows)})


def parse_args() -> argparse.Namespace:
    base = ROOT / "artifacts/research/ccl_phase5_component_selection_final_review_calibration_v1_001"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignment", type=Path, default=base / ASSIGNMENT_NAME)
    parser.add_argument("--response-template", type=Path, default=base / TEMPLATE_NAME)
    parser.add_argument("--calibration-manifest", type=Path, default=base / MANIFEST_NAME)
    parser.add_argument(
        "--structured-tables",
        type=Path,
        default=ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl",
        help="Immutable V2 grid used for read-only source-table previews.",
    )
    parser.add_argument(
        "--normalized-tables",
        type=Path,
        default=ROOT / "artifacts/research/preprocessing_v2_run_003/normalized_tables_v2.jsonl",
        help="Hash-bound preprocessing record used for source context and header-status disclosure.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/research/ccl_phase5_component_selection_final_review_responses_v1_001.jsonl",
        help="New response JSONL path; the UI refuses to overwrite it.",
    )
    parser.add_argument("--port", type=int, default=8785)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package = load_review_package(
        args.assignment.resolve(),
        args.response_template.resolve(),
        args.calibration_manifest.resolve(),
        args.structured_tables.resolve(),
        args.normalized_tables.resolve(),
    )
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    ReviewHandler.package = package
    ReviewHandler.output_path = args.output.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    print(f"Open http://127.0.0.1:{args.port} to review {len(package.assignments)} assignments.")
    print(f"Responses will be created once at {ReviewHandler.output_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLocal review UI stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
