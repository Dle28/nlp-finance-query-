"""Fail-closed source validation for consensus route-review handoffs.

This layer reopens only the documented report identity behind a consensus
handoff.  It deliberately does not select a table, row, column or value, does
not alter an abstained route, and does not promote any label.  Its output is a
hash-bound source-locatable receipt for the next, track-specific validator.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .evidence_context import validate_evidence_context_sidecar
from .route_coverage_adjudication import canonical_sha256, sha256_file, source_contract
from .table_structure import validate_structure_sidecar


PROTOCOL = "route_coverage_source_validation_v1"
HANDOFF_PROTOCOL = "route_coverage_consensus_handoff_v1"
ALLOWED_TRACKS = frozenset(
    {
        "question_plan_context",
        "literal_concept_taxonomy",
        "typed_direct_operation",
        "typed_composed_operation",
        "route_contract",
    }
)
LOCATOR_PAGE_RE = re.compile(r"(?:#|[?&])page=(\d+)(?:\b|$)", re.IGNORECASE)
ALLOWED_COORDINATE_FIELDS = frozenset({"source_locator", "document_id", "page_no", "section", "notes"})
FORBIDDEN_PROPOSAL_KEYS = frozenset(
    {
        "answer", "answer_value", "numeric_value", "raw_value", "parsed_value", "source_value",
        "selected", "selected_value", "selected_cell", "selected_table", "candidate", "candidates",
        "table", "table_id", "table_uid", "internal_table_uid", "row", "row_index", "column",
        "column_index", "cell", "cell_id", "source_cell", "evidence", "evidence_set", "execution",
        "execution_result", "result", "output", "submission", "promotion", "eligibility",
        "materialization_allowed",
    }
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects: {path}")
    return rows


def _require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _walk_proposal(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_PROPOSAL_KEYS:
                raise ValueError(f"Route source-validation proposal contains forbidden key: {key}")
            _walk_proposal(child)
    elif isinstance(value, list):
        for child in value:
            _walk_proposal(child)


def _source_path_for_locator(locator: str) -> str:
    return locator.split("#", 1)[0].strip()


def _locator_page(locator: str) -> int | None:
    match = LOCATOR_PAGE_RE.search(locator)
    return None if match is None else int(match.group(1))


def _coordinate_records(
    *, coordinates: object, documents: Mapping[str, list[dict[str, Any]]], question_id: int
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError(f"Q{question_id}: consensus handoff has no source coordinates")
    checked: list[dict[str, Any]] = []
    all_valid = True
    for index, coordinate in enumerate(coordinates):
        if not isinstance(coordinate, dict) or set(coordinate).difference(ALLOWED_COORDINATE_FIELDS):
            raise ValueError(f"Q{question_id}: source coordinate {index} has unsupported fields")
        locator = coordinate.get("source_locator")
        if not isinstance(locator, str) or not locator.strip():
            raise ValueError(f"Q{question_id}: source coordinate {index} lacks source_locator")
        document_id = coordinate.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError(f"Q{question_id}: source coordinate {index} must name a document_id")
        document_rows = documents.get(document_id)
        if not document_rows:
            raise ValueError(f"Q{question_id}: source coordinate {index} document_id is absent from V2/V3")
        page_no = coordinate.get("page_no")
        locator_page = _locator_page(locator)
        if page_no is None:
            page_no = locator_page
        if type(page_no) is not int or page_no < 1:
            raise ValueError(f"Q{question_id}: source coordinate {index} requires a positive page number")
        if locator_page is not None and locator_page != page_no:
            raise ValueError(f"Q{question_id}: source coordinate {index} locator/page mismatch")
        locator_path = _source_path_for_locator(locator)
        candidates = [
            row
            for row in document_rows
            if int(row["page_no"]) == page_no
            and str((row.get("source_provenance") or {}).get("source_path") or "") == locator_path
        ]
        valid = bool(candidates)
        all_valid = all_valid and valid
        checked.append(
            {
                "source_locator": locator,
                "document_id": document_id,
                "page_no": page_no,
                "source_path": locator_path,
                "source_table_count_on_page": len(candidates),
                "source_sha256_values": sorted(
                    {str((row.get("source_provenance") or {}).get("source_sha256") or "") for row in candidates}
                ),
                "v2_v3_coordinate_exists": valid,
            }
        )
    return checked, all_valid


def _validate_handoff(
    *, handoff: Path, handoff_manifest: Path
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    manifest = _load_json(handoff_manifest)
    if (
        manifest.get("protocol") != HANDOFF_PROTOCOL
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != source_contract()
    ):
        raise ValueError("Route consensus handoff manifest is not non-materializable")
    handoff_sha = _require_hash(handoff, ((manifest.get("outputs") or {}).get("handoff") or {}).get("sha256"), "route consensus handoff")
    rows = _load_jsonl(handoff)
    ids = [row.get("question_id") for row in rows]
    if not rows or any(type(question_id) is not int for question_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("Route consensus handoff has missing or duplicate question IDs")
    expected_count = (manifest.get("counts") or {}).get("consensus_candidate_count")
    if expected_count != len(rows):
        raise ValueError("Route consensus handoff count mismatch")
    for row in rows:
        question_id = int(row["question_id"])
        tracks = row.get("review_tracks")
        proposal = row.get("consensus_proposal")
        coordinates = row.get("consensus_source_coordinates_checked")
        if (
            row.get("protocol") != HANDOFF_PROTOCOL
            or row.get("handoff_state") != "requires_source_bound_validation"
            or row.get("route_status") != "abstain"
            or row.get("materialization_allowed") is not False
            or row.get("source_contract") != source_contract()
            or not isinstance(tracks, list)
            or not tracks
            or set(map(str, tracks)).difference(ALLOWED_TRACKS)
            or not isinstance(proposal, dict)
            or not proposal
            or row.get("consensus_proposal_sha256") != canonical_sha256(proposal)
            or row.get("consensus_source_coordinates_sha256")
            != canonical_sha256({"source_coordinates_checked": coordinates})
        ):
            raise ValueError(f"Q{question_id}: route consensus handoff violates its immutable contract")
        _walk_proposal(proposal)
    return rows, manifest, handoff_sha


def validate_consensus_handoff_sources(
    *, bundle_dir: Path, handoff: Path, handoff_manifest: Path, output_dir: Path
) -> dict[str, Any]:
    """Reopen handoff locators against V2/V3 without materializing routes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "route_coverage_source_validation_v1.jsonl"
    manifest_path = output_dir / "route_coverage_source_validation_v1.manifest.json"
    if output_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite route source-validation output: {output_dir}")
    handoff_rows, _handoff_manifest, handoff_sha = _validate_handoff(handoff=handoff, handoff_manifest=handoff_manifest)

    v2_path = bundle_dir / "tables_structured_v2.jsonl"
    v3_path = bundle_dir / "tables_evidence_context_v3.jsonl"
    validate_structure_sidecar(bundle_dir, v2_path)
    validate_evidence_context_sidecar(bundle_dir, v2_path, v3_path)
    v2_rows = _load_jsonl(v2_path)
    v3_by_uid = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(v3_path)}
    if not v2_rows or "" in v3_by_uid or len(v3_by_uid) != len(v2_rows):
        raise ValueError("V2/V3 source sidecars have incomplete UID coverage")
    documents: dict[str, list[dict[str, Any]]] = {}
    for row in v2_rows:
        uid = str(row.get("internal_table_uid") or "")
        document_id = str(row.get("document_id") or "")
        provenance = row.get("source_provenance") or {}
        page_no = row.get("page_no")
        if (
            not uid
            or uid not in v3_by_uid
            or not document_id
            or type(page_no) is not int
            or page_no < 1
            or not isinstance(provenance.get("source_path"), str)
            or not provenance["source_path"]
            or not isinstance(provenance.get("source_sha256"), str)
            or len(provenance["source_sha256"]) != 64
            or v3_by_uid[uid].get("document_id") != document_id
            or (v3_by_uid[uid].get("source_provenance") or {}).get("source_sha256") != provenance["source_sha256"]
        ):
            raise ValueError("V2/V3 source sidecars have invalid source provenance")
        documents.setdefault(document_id, []).append(row)

    output_rows: list[dict[str, Any]] = []
    for handoff_row in sorted(handoff_rows, key=lambda row: int(row["question_id"])):
        question_id = int(handoff_row["question_id"])
        checked_coordinates, locatable = _coordinate_records(
            coordinates=handoff_row.get("consensus_source_coordinates_checked"),
            documents=documents,
            question_id=question_id,
        )
        output_rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "immutable_queue_payload_sha256": handoff_row.get("immutable_queue_payload_sha256"),
                "consensus_proposal_sha256": handoff_row.get("consensus_proposal_sha256"),
                "consensus_source_coordinates_sha256": handoff_row.get("consensus_source_coordinates_sha256"),
                "review_tracks": list(handoff_row["review_tracks"]),
                "source_coordinate_validation": checked_coordinates,
                "source_locatable": locatable,
                "validation_state": "source_locatable_non_materializable" if locatable else "source_locator_unresolved",
                "route_status": "abstain",
                "materialization_allowed": False,
                "source_contract": source_contract(),
            }
        )
    if any(not row["source_locatable"] for row in output_rows):
        # The source coordinate supplied by a consensus handoff is a hard
        # requirement.  Do not silently produce a partially trustworthy
        # sidecar when a purported source locator cannot be reopened.
        raise ValueError("Route consensus handoff includes a source locator absent from V2/V3 source provenance")
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "materialization_allowed": False,
        "inputs": {
            "handoff": {"path": str(handoff), "sha256": handoff_sha},
            "handoff_manifest": {"path": str(handoff_manifest), "sha256": sha256_file(handoff_manifest)},
            "structured_tables_v2": {"path": str(v2_path), "sha256": sha256_file(v2_path)},
            "evidence_context_v3": {"path": str(v3_path), "sha256": sha256_file(v3_path)},
        },
        "outputs": {"validation": {"path": str(output_path), "sha256": sha256_file(output_path)}},
        "counts": {
            "handoff_count": len(handoff_rows),
            "source_locatable_count": len(output_rows),
            "review_track_counts": dict(sorted(Counter(track for row in output_rows for track in row["review_tracks"]).items())),
        },
        "source_contract": source_contract(),
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
