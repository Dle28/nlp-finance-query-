"""Hash-bound, non-promotable independent reviews of grounding queues.

This module only materializes reviewer sidecars.  It does not mutate an
adjudication queue, apply a repair, select a value, or promote provenance.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


INDEPENDENT_REVIEW_PROTOCOL = "grounding_adjudication_independent_review_v1"
ALLOWED_DECISIONS = {"accept_repair", "reject_repair", "uncertain"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        rows.append(value)
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
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def review_source_contract() -> dict[str, bool]:
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
    }


def _require_hash(path: Path, expected: object, label: str) -> str:
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"Missing SHA-256 for {label}")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")
    return actual


def _metadata_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
    identity = item.get("immutable_source_identity") or {}
    return (
        int(identity.get("question_id") or 0),
        str(identity.get("stage_id") or ""),
        str(identity.get("role") or ""),
    )


def _period_key(item: Mapping[str, Any]) -> int:
    identity = item.get("immutable_source_identity") or {}
    return int(identity.get("question_id") or 0)


def _plan_key(plan: Mapping[str, Any]) -> tuple[str, int, str, str]:
    source_queue = str(plan.get("source_queue") or "")
    question_id = int(plan.get("question_id") or 0)
    if source_queue == "metadata":
        return (
            source_queue,
            question_id,
            str(plan.get("stage_id") or ""),
            str(plan.get("role") or ""),
        )
    if source_queue == "period":
        return (source_queue, question_id, "", "")
    raise ValueError(f"Unsupported source_queue: {source_queue!r}")


def _validate_file_source(source: Mapping[str, Any], *, repository_root: Path) -> dict[str, Any]:
    relative = Path(str(source.get("path") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Authoritative file source must use a repository-relative path")
    path = repository_root / relative
    expected = source.get("sha256")
    actual = sha256_file(path)
    if expected is not None and (not isinstance(expected, str) or len(expected) != 64 or actual != expected):
        raise ValueError(f"SHA-256 mismatch for authoritative source {relative}")
    line_start = source.get("line_start")
    line_end = source.get("line_end", line_start)
    if (
        isinstance(line_start, bool)
        or not isinstance(line_start, int)
        or isinstance(line_end, bool)
        or not isinstance(line_end, int)
        or line_start < 1
        or line_end < line_start
    ):
        raise ValueError(f"Invalid source line range for {relative}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if line_end > len(lines):
        raise ValueError(f"Source line range is out of bounds for {relative}")
    raw_excerpt = "\n".join(lines[line_start - 1 : line_end])
    expected_excerpt = str(source.get("expected_excerpt") or "").strip()
    if expected_excerpt and expected_excerpt not in raw_excerpt:
        raise ValueError(f"Expected excerpt not found at {relative}:{line_start}-{line_end}")
    return {
        "source_kind": str(source.get("source_kind") or "issuer_financial_statement"),
        "path": str(relative),
        "sha256": actual,
        "line_start": line_start,
        "line_end": line_end,
        "raw_excerpt": raw_excerpt,
        "retrieved_at": source.get("retrieved_at"),
    }


def _validate_artifact_source(
    source: Mapping[str, Any],
    *,
    structured_tables_path: Path,
    evidence_context_path: Path,
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    require_header_cells: bool,
) -> dict[str, Any]:
    uid = str(source.get("internal_table_uid") or "")
    prefix = str(source.get("internal_table_uid_prefix") or "")
    if not uid and prefix:
        matches = sorted(candidate for candidate in tables if candidate.startswith(prefix))
        if len(matches) != 1:
            raise ValueError(f"Table UID prefix must resolve exactly once: {prefix!r}")
        uid = matches[0]
    if uid not in tables or uid not in contexts:
        raise ValueError(f"Unknown period source table UID: {uid!r}")
    row_index = source.get("row_index")
    column_index = source.get("column_index")
    if (
        isinstance(row_index, bool)
        or not isinstance(row_index, int)
        or isinstance(column_index, bool)
        or not isinstance(column_index, int)
    ):
        raise ValueError("Period source requires integer row_index and column_index")
    rows = tables[uid].get("rows") or []
    if row_index < 0 or row_index >= len(rows) or column_index < 0 or column_index >= len(rows[row_index]):
        raise ValueError(f"Period source coordinate is out of bounds: {uid} r{row_index}c{column_index}")
    header_source_cells = list(source.get("header_source_cells") or [])
    if require_header_cells and not header_source_cells:
        raise ValueError("Accepted period source requires header_source_cells")
    for cell in header_source_cells:
        header_row = cell.get("row_index")
        header_column = cell.get("column_index")
        if (
            isinstance(header_row, bool)
            or not isinstance(header_row, int)
            or isinstance(header_column, bool)
            or not isinstance(header_column, int)
            or header_row < 0
            or header_row >= len(rows)
            or header_column < 0
            or header_column >= len(rows[header_row])
        ):
            raise ValueError(f"Invalid header source coordinate for {uid}")
        observed = str(rows[header_row][header_column])
        expected = str(cell.get("raw_text") or observed)
        if expected != observed:
            raise ValueError(f"Header source text mismatch for {uid} r{header_row}c{header_column}")
    return {
        "source_kind": "hash_bound_table_artifact",
        "structured_tables_path": str(structured_tables_path),
        "structured_tables_sha256": sha256_file(structured_tables_path),
        "evidence_context_path": str(evidence_context_path),
        "evidence_context_sha256": sha256_file(evidence_context_path),
        "internal_table_uid": uid,
        "document_id": tables[uid].get("document_id"),
        "row_index": row_index,
        "column_index": column_index,
        "raw_source_row": list(rows[row_index]),
        "header_source_cells": header_source_cells,
        "context_trace": contexts[uid].get("context_trace") or {},
    }


def _materialize_review(
    *,
    queue_name: str,
    queue_sha: str,
    item: Mapping[str, Any],
    plan: Mapping[str, Any],
    repository_root: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    decision = str(plan.get("decision") or "")
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"Unsupported review decision: {decision!r}")
    proposed_patch = plan.get("proposed_patch")
    if decision == "accept_repair" and not isinstance(proposed_patch, Mapping):
        raise ValueError("accept_repair requires proposed_patch")
    if decision != "accept_repair" and proposed_patch is not None:
        raise ValueError("reject_repair/uncertain must not carry proposed_patch")
    reviewer = plan.get("decision_provenance") or {}
    if reviewer.get("reviewer_type") not in {"human_verified", "independent_ai_source_review"}:
        raise ValueError("Review requires truthful independent provenance")
    if not str(reviewer.get("reviewer_id") or ""):
        raise ValueError("Review requires reviewer_id")
    if not str(plan.get("reviewed_at") or ""):
        raise ValueError("Review requires reviewed_at")
    raw_sources = list(plan.get("authoritative_sources") or [])
    if decision == "accept_repair" and not raw_sources:
        raise ValueError("Accepted repair requires authoritative_sources")
    sources: list[dict[str, Any]] = []
    for source in raw_sources:
        source_kind = str(source.get("source_kind") or "")
        if source_kind == "hash_bound_table_artifact":
            sources.append(
                _validate_artifact_source(
                    source,
                    structured_tables_path=structured_tables_path,
                    evidence_context_path=evidence_context_path,
                    tables=tables,
                    contexts=contexts,
                    require_header_cells=queue_name == "period" and decision == "accept_repair",
                )
            )
        else:
            sources.append(_validate_file_source(source, repository_root=repository_root))
    identity = item.get("immutable_source_identity") or {}
    operands = list(item.get("operands") or []) if queue_name == "period" else []
    return {
        "schema_version": 1,
        "protocol": INDEPENDENT_REVIEW_PROTOCOL,
        "source_queue": queue_name,
        "source_queue_sha256": queue_sha,
        "source_item_sha256": canonical_sha256(item),
        "question_id": identity.get("question_id"),
        "stage_id": identity.get("stage_id") if queue_name == "metadata" else None,
        "role": identity.get("role") if queue_name == "metadata" else None,
        "reviewed_operands": [
            {key: operand.get(key) for key in ("stage_id", "role", "concept_id", "period_type")}
            for operand in operands
        ],
        "queue_kind": item.get("queue_kind"),
        "source_diagnosis": item.get("diagnosis") or item.get("packet_status"),
        "decision": decision,
        "decision_provenance": dict(reviewer),
        "reviewed_at": plan["reviewed_at"],
        "source_coordinates_checked": bool(plan.get("source_coordinates_checked")),
        "authoritative_sources": sources,
        "reason_codes": list(plan.get("reason_codes") or []),
        "proposed_patch": dict(proposed_patch) if isinstance(proposed_patch, Mapping) else None,
        "notes": str(plan.get("notes") or ""),
        "eligible_for_materialization": False,
        "source_contract": review_source_contract(),
    }


def build_independent_adjudication_reviews(
    *,
    metadata_queue_path: Path,
    period_queue_path: Path | None,
    adjudication_manifest_path: Path,
    review_plan_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    repository_root: Path,
    output_dir: Path,
    artifact_version: str = "v1",
    evidence_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Validate frozen queues and materialize 54 metadata plus optional period reviews."""
    manifest = _read_json(adjudication_manifest_path)
    outputs = manifest.get("outputs") or {}
    metadata_sha = _require_hash(
        metadata_queue_path, (outputs.get("metadata_queue") or {}).get("sha256"), "metadata queue"
    )
    period_sha = None
    if period_queue_path is not None:
        period_sha = _require_hash(
            period_queue_path, (outputs.get("period_queue") or {}).get("sha256"), "period queue"
        )
    inputs = manifest.get("inputs") or {}
    evidence_inputs = inputs
    if not (inputs.get("structured_tables_v2") or {}).get("sha256"):
        if evidence_manifest_path is None:
            raise ValueError("V3 metadata review requires evidence_manifest_path for V2/V3 table hashes")
        evidence_inputs = _read_json(evidence_manifest_path).get("inputs") or {}
    _require_hash(
        structured_tables_path,
        (evidence_inputs.get("structured_tables_v2") or {}).get("sha256"),
        "structured tables V2",
    )
    _require_hash(
        evidence_context_path,
        (evidence_inputs.get("evidence_context_v3") or {}).get("sha256"),
        "evidence context V3",
    )
    metadata = _read_jsonl(metadata_queue_path)
    period = _read_jsonl(period_queue_path) if period_queue_path is not None else []
    if len(metadata) != 54 or len(period) not in {0, 16}:
        raise ValueError("Frozen review queue coverage changed")
    metadata_by_key = {_metadata_key(item): item for item in metadata}
    period_by_key = {_period_key(item): item for item in period}
    if len(metadata_by_key) != len(metadata) or len(period_by_key) != len(period):
        raise ValueError("Review queues contain duplicate identities")
    plan_rows = _read_jsonl(review_plan_path)
    plans: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for plan in plan_rows:
        key = _plan_key(plan)
        if key in plans:
            raise ValueError(f"Duplicate review plan key: {key}")
        plans[key] = plan
    expected_keys = {
        ("metadata", *key) for key in metadata_by_key
    } | {("period", key, "", "") for key in period_by_key}
    if set(plans) != expected_keys:
        missing = sorted(expected_keys - set(plans))
        extra = sorted(set(plans) - expected_keys)
        raise ValueError(f"Review plan coverage mismatch: missing={missing}, extra={extra}")
    table_rows = _read_jsonl(structured_tables_path)
    context_rows = _read_jsonl(evidence_context_path)
    tables = {str(row.get("internal_table_uid") or ""): row for row in table_rows}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in context_rows}
    if len(tables) != len(table_rows) or set(tables) != set(contexts):
        raise ValueError("V2/V3 table identity coverage mismatch")
    metadata_reviews = [
        _materialize_review(
            queue_name="metadata",
            queue_sha=metadata_sha,
            item=item,
            plan=plans[("metadata", *key)],
            repository_root=repository_root,
            structured_tables_path=structured_tables_path,
            evidence_context_path=evidence_context_path,
            tables=tables,
            contexts=contexts,
        )
        for key, item in sorted(metadata_by_key.items())
    ]
    period_reviews = [
        _materialize_review(
            queue_name="period",
            queue_sha=str(period_sha),
            item=item,
            plan=plans[("period", key, "", "")],
            repository_root=repository_root,
            structured_tables_path=structured_tables_path,
            evidence_context_path=evidence_context_path,
            tables=tables,
            contexts=contexts,
        )
        for key, item in sorted(period_by_key.items())
    ]
    if any(review["eligible_for_materialization"] for review in metadata_reviews + period_reviews):
        raise ValueError("Independent review sidecars must remain non-materializable")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not artifact_version or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in artifact_version):
        raise ValueError("artifact_version must be a safe non-empty label")
    metadata_path = output_dir / f"person1_metadata_reviews_{artifact_version}.jsonl"
    period_path = output_dir / f"person1_period_reviews_{artifact_version}.jsonl" if period_queue_path is not None else None
    manifest_path = output_dir / f"person1_metadata_period_review_{artifact_version}.manifest.json"
    _write_jsonl(metadata_path, metadata_reviews)
    if period_path is not None:
        _write_jsonl(period_path, period_reviews)
    all_reviews = metadata_reviews + period_reviews
    result = {
        "schema_version": 1,
        "protocol": INDEPENDENT_REVIEW_PROTOCOL,
        "inputs": {
            "adjudication_manifest": {
                "path": str(adjudication_manifest_path),
                "sha256": sha256_file(adjudication_manifest_path),
            },
            "metadata_queue": {"path": str(metadata_queue_path), "sha256": metadata_sha},
            "review_plan": {"path": str(review_plan_path), "sha256": sha256_file(review_plan_path)},
            "structured_tables_v2": {
                "path": str(structured_tables_path),
                "sha256": sha256_file(structured_tables_path),
            },
            "evidence_context_v3": {
                "path": str(evidence_context_path),
                "sha256": sha256_file(evidence_context_path),
            },
        },
        "outputs": {"metadata_reviews": {"path": str(metadata_path), "sha256": sha256_file(metadata_path)}},
        "counts": {
            "metadata_reviews": len(metadata_reviews),
            "period_reviews": len(period_reviews),
            "decision_counts": dict(sorted(Counter(row["decision"] for row in all_reviews).items())),
            "metadata_decision_counts": dict(
                sorted(Counter(row["decision"] for row in metadata_reviews).items())
            ),
            "period_decision_counts": dict(
                sorted(Counter(row["decision"] for row in period_reviews).items())
            ),
            "authoritative_source_count": sum(len(row["authoritative_sources"]) for row in all_reviews),
        },
        "status": "independent_review_sidecars_only",
        "repairs_materialized": False,
        "source_contract": review_source_contract(),
    }
    if period_queue_path is not None and period_path is not None:
        result["inputs"]["period_queue"] = {"path": str(period_queue_path), "sha256": period_sha}
        result["outputs"]["period_reviews"] = {"path": str(period_path), "sha256": sha256_file(period_path)}
    if evidence_manifest_path is not None:
        result["inputs"]["evidence_manifest"] = {"path": str(evidence_manifest_path), "sha256": sha256_file(evidence_manifest_path)}
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
