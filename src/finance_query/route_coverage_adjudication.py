"""Hash-bound review handoff for question routes that correctly abstained.

This module measures and queues missing route contracts.  It deliberately does
not infer a ticker, year, scope, taxonomy alias, operation, source row, table,
column, value, or answer.  A route remains abstained until a separately
reviewed and materialized upstream plan/taxonomy change passes its own gates.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PROTOCOL = "route_coverage_adjudication_v1"
TEMPLATE_PROTOCOL = "route_coverage_adjudication_label_template_v1"
MISSING_CONTEXT_CODES = frozenset(
    {"MISSING_ENTITY_CONTEXT", "MISSING_YEAR_CONTEXT", "MISSING_SCOPE_CONTEXT"}
)
TRACK_ORDER = (
    "question_plan_context",
    "literal_concept_taxonomy",
    "typed_direct_operation",
    "typed_composed_operation",
    "route_contract",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "eligible_for_materialization": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
        "may_infer_missing_context": False,
        "may_add_taxonomy_alias": False,
    }


def _assert_non_promotable(contract: Mapping[str, Any], *, label: str) -> None:
    for key in ("evidence_eligible", "training_eligible", "submission_eligible", "promotion_allowed"):
        if bool(contract.get(key, False)):
            raise ValueError(f"{label} improperly enables {key}")


def _manifest_route_hash(manifest: Mapping[str, Any]) -> str:
    output = manifest.get("output") or {}
    value = output.get("sha256") if isinstance(output, Mapping) else None
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("Question-route manifest lacks output SHA-256")
    return value


def _question_id(row: Mapping[str, Any]) -> int:
    value = row.get("question_id")
    if value is None or isinstance(value, bool):
        raise ValueError("Question route has invalid question_id")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Question route has invalid question_id: {value!r}") from exc


def _validate_routes(rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    expected_count = manifest.get("question_count")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count <= 0:
        raise ValueError("Question-route manifest has invalid question_count")
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = _question_id(row)
        if question_id in by_id:
            raise ValueError(f"Question routes have duplicate question_id: {question_id}")
        if str(row.get("route_status") or "") not in {
            "abstain", "concept_lookup_candidate", "metric_candidate", "staged_candidate"
        }:
            raise ValueError(f"Q{question_id}: unsupported route_status")
        _assert_non_promotable(row.get("source_contract") or {}, label=f"Question route Q{question_id}")
        by_id[question_id] = dict(row)
    if len(by_id) != expected_count:
        raise ValueError("Question-route manifest question_count does not match route rows")
    return by_id


def review_tracks(reason_codes: Sequence[object]) -> list[str]:
    """Return deterministic remediation tracks without claiming a solution."""
    reasons = {str(value) for value in reason_codes}
    tracks: list[str] = []
    if reasons.intersection(MISSING_CONTEXT_CODES):
        tracks.append("question_plan_context")
    if "NO_LITERAL_METRIC_OR_CONCEPT_MATCH" in reasons:
        tracks.append("literal_concept_taxonomy")
    if "DIRECT_CONCEPT_OPERATION_UNSUPPORTED" in reasons:
        tracks.append("typed_direct_operation")
    if "COMPOSED_EXECUTION_REQUIRED" in reasons:
        tracks.append("typed_composed_operation")
    if not tracks:
        tracks.append("route_contract")
    return [track for track in TRACK_ORDER if track in tracks]


def _missing_context(reason_codes: Sequence[object]) -> list[str]:
    reasons = {str(value) for value in reason_codes}
    return [
        name
        for name, code in (
            ("entity", "MISSING_ENTITY_CONTEXT"),
            ("year", "MISSING_YEAR_CONTEXT"),
            ("scope", "MISSING_SCOPE_CONTEXT"),
        )
        if code in reasons
    ]


def _compact_stage(stage: Mapping[str, Any]) -> dict[str, Any]:
    """Keep route metadata only; no source/retrieval candidate is introduced."""
    operands = []
    for operand in stage.get("required_operands") or []:
        operands.append(
            {
                key: operand.get(key)
                for key in ("role", "concept_id", "concept_path", "period_type", "statement_types", "period_policy")
                if key in operand
            }
        )
    return {
        key: stage.get(key)
        for key in (
            "stage_id",
            "route_kind",
            "metric_id",
            "concept_id",
            "matched_label",
            "match_span",
            "definition_status",
            "formula_ast",
            "formula_ast_metadata_only",
            "output_unit",
            "retrieval_filters",
        )
        if key in stage
    } | {"required_operands": operands}


def queue_item(route: Mapping[str, Any]) -> dict[str, Any]:
    question_id = _question_id(route)
    reason_codes = sorted(set(str(value) for value in route.get("reason_codes") or []))
    if str(route.get("route_status") or "") != "abstain":
        raise ValueError(f"Q{question_id}: only abstained routes may enter route-coverage queue")
    tracks = review_tracks(reason_codes)
    payload = {
        "question_id": question_id,
        "question": str(route.get("question") or ""),
        "normalized_question": str(route.get("normalized_question") or ""),
        "route_status": "abstain",
        "route_reason_codes": reason_codes,
        "question_context": dict(route.get("question_context") or {}),
        "route_stages": [_compact_stage(stage) for stage in route.get("stages") or []],
        "review_tracks": tracks,
        "missing_context": _missing_context(reason_codes),
    }
    return {
        "schema_version": 1,
        "protocol": PROTOCOL,
        **payload,
        "immutable_route_sha256": canonical_sha256(route),
        "immutable_queue_payload_sha256": canonical_sha256(payload),
        "review_decision_contract": {
            "decision": None,
            "decision_provenance": None,
            "reviewer_id": None,
            "reviewed_at": None,
            "source_coordinates_checked": None,
            "proposed_question_plan": None,
            "proposed_taxonomy_alias": None,
            "proposed_operation_contract": None,
            "eligible_for_materialization": False,
        },
        "source_contract": source_contract(),
    }


def label_template(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol": TEMPLATE_PROTOCOL,
        "question_id": int(item["question_id"]),
        "immutable_queue_payload_sha256": item["immutable_queue_payload_sha256"],
        "review_tracks": list(item["review_tracks"]),
        "decision": None,
        "decision_provenance": None,
        "reviewer_id": None,
        "reviewed_at": None,
        "source_coordinates_checked": None,
        "proposed_question_plan": None,
        "proposed_taxonomy_alias": None,
        "proposed_operation_contract": None,
        "notes": "",
        "is_blank_template": True,
        "source_contract": source_contract(),
    }


def build_route_coverage_adjudication(
    *, routes: Path, routes_manifest: Path, output_dir: Path
) -> dict[str, Any]:
    """Build one non-materializable review row for each abstained question route."""
    manifest = _load_json(routes_manifest)
    expected_hash = _manifest_route_hash(manifest)
    if sha256_file(routes) != expected_hash:
        raise ValueError("SHA-256 mismatch for question routes")
    _assert_non_promotable(manifest.get("source_contract") or {}, label="Question-route manifest")
    by_id = _validate_routes(_load_jsonl(routes), manifest)
    queue = [queue_item(by_id[question_id]) for question_id in sorted(by_id) if by_id[question_id].get("route_status") == "abstain"]
    templates = [label_template(item) for item in queue]
    if len({int(item["question_id"]) for item in queue}) != len(queue):
        raise ValueError("Route-coverage queue has duplicate question IDs")
    if any(item["source_contract"] != source_contract() for item in queue):
        raise ValueError("Route-coverage queue source-contract mismatch")

    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "route_coverage_adjudication_queue_v1.jsonl"
    template_path = output_dir / "route_coverage_adjudication_label_template_v1.jsonl"
    _write_jsonl(queue_path, queue)
    _write_jsonl(template_path, templates)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "routes": {"path": str(routes), "sha256": sha256_file(routes)},
            "routes_manifest": {"path": str(routes_manifest), "sha256": sha256_file(routes_manifest)},
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)},
            "label_template": {"path": str(template_path), "sha256": sha256_file(template_path)},
        },
        "counts": {
            "route_count": len(by_id),
            "abstain_count": len(queue),
            "review_track_counts": dict(
                sorted(Counter(track for item in queue for track in item["review_tracks"]).items())
            ),
            "missing_context_counts": dict(
                sorted(Counter(field for item in queue for field in item["missing_context"]).items())
            ),
            "reason_code_counts": dict(
                sorted(Counter(code for item in queue for code in item["route_reason_codes"]).items())
            ),
            "prepopulated_label_count": 0,
        },
        "repairs_materialized": False,
        "source_contract": source_contract(),
    }
    manifest_path = output_dir / "route_coverage_adjudication_v1.manifest.json"
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
