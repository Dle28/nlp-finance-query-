"""Build a source-bound candidate handoff from reconciled route reviews.

The handoff preserves only identical two-reviewer accepted proposals.  It is
not a route overlay and cannot update question plans, taxonomy, operations,
evidence, labels, or eligibility.  A later source-bound validator must reopen
the referenced documents and decide whether a proposed upstream change is
valid.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .route_coverage_adjudication import canonical_sha256, sha256_file, source_contract


RECONCILIATION_PROTOCOL = "route_coverage_independent_review_reconciliation_v1"
PROTOCOL = "route_coverage_consensus_handoff_v1"
ALLOWED_TRACKS = frozenset(
    {
        "question_plan_context",
        "literal_concept_taxonomy",
        "typed_direct_operation",
        "typed_composed_operation",
        "route_contract",
    }
)
PROPOSAL_FIELDS = (
    "proposed_question_plan",
    "proposed_taxonomy_alias",
    "proposed_operation_contract",
)
FORBIDDEN_KEYS = frozenset(
    {
        "answer", "value", "numeric_value", "raw_value", "selected",
        "selected_value", "table", "table_uid", "internal_table_uid",
        "row", "row_index", "column", "column_index", "cell",
        "source_cell", "evidence", "formula", "execution", "submission",
        "promotion", "eligible_for_materialization",
    }
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def _require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _walk_proposal(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_KEYS:
                raise ValueError(f"Route consensus proposal contains forbidden key: {key}")
            _walk_proposal(child)
    elif isinstance(value, list):
        for child in value:
            _walk_proposal(child)


def _require_proposal(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} must be a non-empty object")
    _walk_proposal(value)
    return dict(value)


def _required_proposal_fields(tracks: Sequence[object]) -> set[str]:
    values = {str(track) for track in tracks}
    if not values or values.difference(ALLOWED_TRACKS):
        raise ValueError("Route consensus row has unsupported review tracks")
    required: set[str] = set()
    if "question_plan_context" in values:
        required.add("proposed_question_plan")
    if "literal_concept_taxonomy" in values:
        required.add("proposed_taxonomy_alias")
    if values.intersection({"typed_direct_operation", "typed_composed_operation", "route_contract"}):
        required.add("proposed_operation_contract")
    return required


def build_consensus_handoff(
    *, reconciliation: Path, reconciliation_manifest: Path, output_dir: Path
) -> dict[str, Any]:
    """Emit only validated consensus candidates; never materialize route changes."""
    manifest = _load_json(reconciliation_manifest)
    if (
        manifest.get("protocol") != RECONCILIATION_PROTOCOL
        or manifest.get("reconciliation_complete") is not True
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != source_contract()
    ):
        raise ValueError("Route-review reconciliation is not a complete non-materializable input")
    reconciliation_sha = _require_hash(
        reconciliation,
        ((manifest.get("outputs") or {}).get("reconciled") or {}).get("sha256"),
        "route-review reconciliation",
    )
    rows = _load_jsonl(reconciliation)
    ids = [row.get("question_id") for row in rows]
    if not rows or any(type(question_id) is not int for question_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("Route-review reconciliation has missing or duplicate question IDs")
    counts = manifest.get("counts") or {}
    if counts.get("review_count") != len(rows):
        raise ValueError("Route-review reconciliation count does not match rows")

    handoff: list[dict[str, Any]] = []
    state_counts: Counter[str] = Counter()
    for row in sorted(rows, key=lambda item: int(item["question_id"])):
        question_id = int(row["question_id"])
        if (
            row.get("protocol") != RECONCILIATION_PROTOCOL
            or row.get("route_status_after_reconciliation") != "abstain"
            or row.get("materialization_allowed") is not False
            or row.get("source_contract") != source_contract()
        ):
            raise ValueError(f"Q{question_id}: reconciliation row violates abstain-only contract")
        state = row.get("reconciliation_state")
        state_counts[str(state)] += 1
        if state != "agreed_accept_non_materializable":
            continue
        proposal = row.get("proposed_route_change")
        if not isinstance(proposal, dict) or set(proposal).difference(PROPOSAL_FIELDS):
            raise ValueError(f"Q{question_id}: consensus proposal shape is invalid")
        tracks = list(row.get("review_tracks") or [])
        required = _required_proposal_fields(tracks)
        normalized_proposal: dict[str, dict[str, Any] | None] = {}
        for key in PROPOSAL_FIELDS:
            value = proposal.get(key)
            if key in required:
                normalized_proposal[key] = _require_proposal(value, label=f"Q{question_id} {key}")
            elif value is not None:
                normalized_proposal[key] = _require_proposal(value, label=f"Q{question_id} optional {key}")
            else:
                normalized_proposal[key] = None
        proposal_sha = canonical_sha256(normalized_proposal)
        recorded_hashes = row.get("reviewer_proposal_sha256") or {}
        if recorded_hashes.get("reviewer_a") != proposal_sha or recorded_hashes.get("reviewer_b") != proposal_sha:
            raise ValueError(f"Q{question_id}: consensus proposal hash differs from reviewer records")
        source_coordinates = row.get("consensus_source_coordinates_checked")
        if not isinstance(source_coordinates, list) or not source_coordinates:
            raise ValueError(f"Q{question_id}: consensus handoff lacks agreed source coordinates")
        source_coordinates_sha = canonical_sha256(
            {"source_coordinates_checked": source_coordinates}
        )
        recorded_source_hashes = row.get("reviewer_source_coordinates_sha256") or {}
        if (
            recorded_source_hashes.get("reviewer_a") != source_coordinates_sha
            or recorded_source_hashes.get("reviewer_b") != source_coordinates_sha
        ):
            raise ValueError(f"Q{question_id}: consensus source-coordinate hash differs from reviewer records")
        immutable_queue = row.get("immutable_queue_payload_sha256")
        if not isinstance(immutable_queue, str) or len(immutable_queue) != 64:
            raise ValueError(f"Q{question_id}: immutable queue payload hash is invalid")
        handoff.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "immutable_queue_payload_sha256": immutable_queue,
                "review_tracks": tracks,
                "consensus_proposal": normalized_proposal,
                "consensus_proposal_sha256": proposal_sha,
                "reviewer_proposal_sha256": dict(recorded_hashes),
                "consensus_source_coordinates_checked": source_coordinates,
                "consensus_source_coordinates_sha256": source_coordinates_sha,
                "reviewer_source_coordinates_sha256": dict(recorded_source_hashes),
                "handoff_state": "requires_source_bound_validation",
                "route_status": "abstain",
                "materialization_allowed": False,
                "source_contract": source_contract(),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = output_dir / "route_coverage_consensus_handoff_v1.jsonl"
    manifest_path = output_dir / "route_coverage_consensus_handoff_v1.manifest.json"
    if handoff_path.exists() or manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite route consensus handoff: {output_dir}")
    handoff_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in handoff),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "reconciliation": {"path": str(reconciliation), "sha256": reconciliation_sha},
            "reconciliation_manifest": {"path": str(reconciliation_manifest), "sha256": sha256_file(reconciliation_manifest)},
        },
        "outputs": {"handoff": {"path": str(handoff_path), "sha256": sha256_file(handoff_path)}},
        "counts": {
            "reconciliation_count": len(rows),
            "consensus_candidate_count": len(handoff),
            "reconciliation_state_counts": dict(sorted(state_counts.items())),
        },
        "materialization_allowed": False,
        "source_contract": source_contract(),
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
