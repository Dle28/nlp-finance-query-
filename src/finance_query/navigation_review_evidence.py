"""Build literal-free numeric navigation evidence for human/ChatGPT review."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .binding_conflict_workbench import canonical_sha256, sha256_file
from .navigation_remediation import PROTOCOL as QUEUE_PROTOCOL


PROTOCOL = "vifinqa_navigation_review_evidence_v1"
_NUMERIC_CELL_RE = re.compile(r"^[\s()\-+.,%\d]+$")


class NavigationReviewEvidenceError(ValueError):
    """Raised when navigation evidence inputs are stale or incomplete."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise NavigationReviewEvidenceError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise NavigationReviewEvidenceError(f"{path} must be an object")
    return value


def _valid_queue_item(row: Mapping[str, Any]) -> bool:
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"schema_version", "protocol", "remediation_item_sha256", "source_contract"}
    }
    return (
        row.get("protocol") == QUEUE_PROTOCOL
        and row.get("remediation_item_sha256") == canonical_sha256(payload)
    )


def _text_cells(row: Sequence[object]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for index, raw in enumerate(row):
        text = str(raw or "").strip()
        if not text or _NUMERIC_CELL_RE.fullmatch(text):
            continue
        values.append({"column_index": index, "text": text})
    return values


def build_evidence_packets(
    *,
    queue: Path,
    queue_manifest: Path,
    route_packets: Path,
    route_manifest: Path,
    structured_tables: Path,
    table_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Freeze exact source rows and gate vectors without exposing numeric values."""

    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite navigation evidence: {output_dir}")
    queue_meta = _json(queue_manifest)
    route_meta = _json(route_manifest)
    table_meta = _json(table_manifest)
    queue_sha = sha256_file(queue)
    route_sha = sha256_file(route_packets)
    tables_sha = sha256_file(structured_tables)
    if queue_meta.get("protocol") != QUEUE_PROTOCOL:
        raise NavigationReviewEvidenceError("unexpected navigation queue protocol")
    if (((queue_meta.get("outputs") or {}).get("queue") or {}).get("sha256")) != queue_sha:
        raise NavigationReviewEvidenceError("navigation queue SHA-256 mismatch")
    if (((route_meta.get("outputs") or {}).get("packets") or {}).get("sha256")) != route_sha:
        raise NavigationReviewEvidenceError("route packet SHA-256 mismatch")
    if table_meta.get("sidecar_sha256") != tables_sha:
        raise NavigationReviewEvidenceError("structured table SHA-256 mismatch")

    queue_rows = _rows(queue)
    if any(not _valid_queue_item(row) for row in queue_rows):
        raise NavigationReviewEvidenceError("navigation remediation item SHA-256 mismatch")
    routes = {int(row["question_id"]): row for row in _rows(route_packets)}
    if len(routes) != 1012:
        raise NavigationReviewEvidenceError("route packet corpus coverage mismatch")
    required_uids = {
        str(candidate["internal_table_uid"])
        for item in queue_rows
        for candidate in item.get("nearby_exact_concept_candidates") or []
    }
    tables: dict[str, dict[str, Any]] = {}
    for table in _rows(structured_tables):
        uid = str(table.get("internal_table_uid") or "")
        if uid in required_uids:
            tables[uid] = table
    if set(tables) != required_uids:
        raise NavigationReviewEvidenceError("one or more navigation tables are missing")

    packets: list[dict[str, Any]] = []
    cause_counts: Counter[str] = Counter()
    candidate_count = 0
    exact_context_count = 0
    numeric_value_exposure_count = 0
    for item in sorted(queue_rows, key=lambda row: int(row["question_id"])):
        question_id = int(item["question_id"])
        route = routes.get(question_id)
        if route is None:
            raise NavigationReviewEvidenceError(f"missing route packet Q{question_id}")
        cause = str(item.get("exclusive_primary_cause") or "")
        cause_counts[cause] += 1
        candidate_evidence: list[dict[str, Any]] = []
        for candidate_index, candidate in enumerate(item.get("nearby_exact_concept_candidates") or [], start=1):
            uid = str(candidate.get("internal_table_uid") or "")
            table = tables[uid]
            row_index = int(candidate.get("row_index", -1))
            rows = table.get("rows") or []
            if row_index < 0 or row_index >= len(rows) or not isinstance(rows[row_index], list):
                raise NavigationReviewEvidenceError(f"Q{question_id} row index is invalid")
            raw_row = rows[row_index]
            text_cells = _text_cells(raw_row)
            if any(_NUMERIC_CELL_RE.fullmatch(str(cell["text"])) for cell in text_cells):
                numeric_value_exposure_count += 1
            source = table.get("source_provenance") or {}
            context = table.get("context_trace") or {}
            gate_vector = candidate.get("gate_vector") or {}
            exact_context = all(
                bool((gate_vector.get(field) or {}).get("pass"))
                for field in ("entity", "scope", "year", "table_type")
            )
            exact_context_count += int(exact_context)
            evidence_payload = {
                "candidate_index": candidate_index,
                "document_id": candidate.get("document_id"),
                "internal_table_uid": uid,
                "row_index": row_index,
                "failure_codes": candidate.get("failure_codes") or [],
                "gate_vector": gate_vector,
                "exact_identity_scope_year_table": exact_context,
                "row_text_cells": text_cells,
                "raw_row_sha256": canonical_sha256(raw_row),
                "column_labels": table.get("column_labels") or [],
                "column_labels_sha256": canonical_sha256(table.get("column_labels") or []),
                "source_title": context.get("source_title") or "",
                "source_title_sha256": canonical_sha256(context.get("source_title") or ""),
                "source_document_sha256": source.get("source_sha256"),
                "source_table_sha256": source.get("table_sha256"),
            }
            candidate_evidence.append({
                **evidence_payload,
                "candidate_evidence_sha256": canonical_sha256(evidence_payload),
            })
            candidate_count += 1
        payload = {
            "question_id": question_id,
            "question": route.get("question"),
            "question_context": route.get("question_context") or {},
            "concept_id": item.get("concept_id"),
            "exclusive_primary_cause": cause,
            "exclusive_primary_cause_blocker_set": item.get("exclusive_primary_cause_blocker_set") or [],
            "minimal_blocker_sets": item.get("minimal_blocker_sets") or [],
            "recommended_review_queue": item.get("recommended_review_queue"),
            "candidate_evidence": candidate_evidence,
            "source_remediation_item_sha256": item.get("remediation_item_sha256"),
            "source_route_packet_sha256": canonical_sha256(route),
        }
        packets.append({
            "schema_version": 1,
            "protocol": PROTOCOL,
            **payload,
            "packet_sha256": canonical_sha256(payload),
            "source_contract": {
                "review_evidence_only": True,
                "numeric_values_exposed": False,
                "evidence_eligible": False,
                "eligible_for_materialization": False,
                "may_change_route": False,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
            },
        })
    if numeric_value_exposure_count:
        raise NavigationReviewEvidenceError("numeric value leaked into review packet")

    output_dir.mkdir(parents=True, exist_ok=False)
    packets_path = output_dir / "navigation_review_evidence_v1.jsonl"
    packets_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in packets
        ),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "review_evidence_only_not_materialized",
        "inputs": {
            "queue": {"path": str(queue), "sha256": queue_sha},
            "queue_manifest": {"path": str(queue_manifest), "sha256": sha256_file(queue_manifest)},
            "route_packets": {"path": str(route_packets), "sha256": route_sha},
            "route_manifest": {"path": str(route_manifest), "sha256": sha256_file(route_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": tables_sha},
            "table_manifest": {"path": str(table_manifest), "sha256": sha256_file(table_manifest)},
        },
        "outputs": {"packets": {"path": str(packets_path), "sha256": sha256_file(packets_path)}},
        "counts": {
            "packet_count": len(packets),
            "candidate_count": candidate_count,
            "exact_identity_scope_year_table_count": exact_context_count,
            "numeric_value_exposure_count": numeric_value_exposure_count,
            "primary_cause_counts": dict(sorted(cause_counts.items())),
        },
        "source_contract": {
            "review_evidence_only": True,
            "numeric_values_exposed": False,
            "eligible_for_materialization": False,
            "may_change_route": False,
            "may_select_value": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
        },
    }
    manifest_path = output_dir / "navigation_review_evidence_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
