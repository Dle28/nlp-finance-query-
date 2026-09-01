"""Measure sidecar-coverage effects without creating an E2E evidence path.

This probe reconstructs missing V2/V3 table structures in memory from the
immutable OCR slice named by a full-corpus asset.  It compares the current
review-bundle coverage against that reconstructed view under the *same* source
gates.  Results contain only statuses, immutable identities, and gate names.
They cannot be used as bindings, answers, evidence, training data, or a
submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.table_structure import PERIOD_RE, UNIT_LABEL_RE, parse_html_table
from finance_query.research.full_corpus_candidate_retrieval import NUMERIC_CELL_RE
from finance_query.research.full_corpus_direct_lookup_adapter import TARGET_BLOCKER, _candidate
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_hydrated_source_gate_probe_v1"
CONTRACT = {
    "research_only": True,
    "in_memory_sidecar_probe_only": True,
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
        "raw_source_cell",
        "raw_source_row",
        "rows",
        "cell_provenance",
        "context_before",
        "row_label",
    }
)


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            yield value


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
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


def _require_asset_manifest(assets_path: Path, manifest_path: Path) -> None:
    descriptor = (_json(manifest_path).get("outputs") or {}).get(assets_path.name) or {}
    if sha256_file(assets_path) != descriptor.get("sha256"):
        raise ValueError("full-corpus assets do not match their manifest")


def _unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value).split())
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def _header_value(rows: list[list[str]], provenance: list[list[Mapping[str, Any]]], row_index: int, column_index: int) -> tuple[str, tuple[int, int] | None]:
    if not (0 <= row_index < len(rows) and 0 <= column_index < len(rows[row_index])):
        return "", None
    value = str(rows[row_index][column_index]).strip()
    provenance_cell = provenance[row_index][column_index] if row_index < len(provenance) and column_index < len(provenance[row_index]) else {}
    if value:
        return value, (row_index, column_index)
    if isinstance(provenance_cell, Mapping) and provenance_cell.get("covered_by_span") is True:
        anchor_row, anchor_column = provenance_cell.get("anchor_row"), provenance_cell.get("anchor_column")
        if isinstance(anchor_row, int) and isinstance(anchor_column, int) and 0 <= anchor_row < len(rows) and 0 <= anchor_column < len(rows[anchor_row]):
            anchor_value = str(rows[anchor_row][anchor_column]).strip()
            if anchor_value:
                return anchor_value, (anchor_row, anchor_column)
    return "", None


def _header_context(parsed: Mapping[str, Any]) -> dict[str, Any]:
    rows = [[str(cell) for cell in row] for row in parsed.get("rows") or []]
    provenance = parsed.get("cell_provenance") or []
    header_rows = [int(value) for value in parsed.get("header_row_indices") or []]
    width = max((len(row) for row in rows), default=0)
    columns: list[dict[str, Any]] = []
    for column_index in range(width):
        texts: list[str] = []
        cells: list[dict[str, int]] = []
        seen_cells: set[tuple[int, int]] = set()
        for row_index in header_rows:
            value, anchor = _header_value(rows, provenance, row_index, column_index)
            if value:
                texts.append(value)
            if anchor is not None and anchor not in seen_cells:
                seen_cells.add(anchor)
                cells.append({"row_index": anchor[0], "column_index": anchor[1]})
        label = " · ".join(_unique(texts))
        periods = _unique(match.group(0) for match in PERIOD_RE.finditer(label))
        units = _unique(match.group(0) for match in UNIT_LABEL_RE.finditer(label))
        columns.append(
            {
                "column_index": column_index,
                "source_label": label,
                "header_source_cells": cells,
                "role": "row_label" if column_index == 0 else "value_or_text",
                "period_labels": periods,
                "unit_labels": units,
            }
        )
    return {
        "header_row_indices": header_rows,
        "raw_header_row_indices": header_rows,
        "excluded_header_row_indices": [],
        "columns": columns,
        "span_recoveries": 0,
        "inline_recovery": {"status": "not_attempted_research_probe", "header_row_indices": []},
    }


def _row_profiles(parsed: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [[str(cell) for cell in row] for row in parsed.get("rows") or []]
    header_rows = {int(value) for value in parsed.get("header_row_indices") or []}
    profiles: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        numeric_columns = [
            column_index
            for column_index, value in enumerate(row)
            if column_index > 0 and bool(NUMERIC_CELL_RE.search(value))
        ]
        profiles.append(
            {
                "row_index": row_index,
                "role": "header" if row_index in header_rows else "data",
                "numeric_columns": [] if row_index in header_rows else numeric_columns,
                "structural_numeric_columns": numeric_columns,
                "unreliable_numeric_columns": [],
                "non_empty_cell_count": sum(bool(value.strip()) for value in row),
            }
        )
    return profiles


def _hydrate(asset: Mapping[str, Any], *, source_cache: dict[Path, tuple[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = Path(str(asset.get("source_path") or ""))
    if not source_path.is_file():
        raise ValueError("source_file_missing")
    if source_path not in source_cache:
        raw = source_path.read_bytes()
        source_cache[source_path] = (raw.decode("utf-8", errors="strict"), hashlib.sha256(raw).hexdigest())
    text, source_sha256 = source_cache[source_path]
    if source_sha256 != str(asset.get("source_sha256") or ""):
        raise ValueError("source_hash_mismatch")
    char_start, char_end = int(asset["char_start"]), int(asset["char_end"])
    if char_start < 0 or char_end <= char_start or char_end > len(text):
        raise ValueError("source_locator_invalid")
    table_html = text[char_start:char_end]
    if hashlib.sha256(table_html.encode("utf-8")).hexdigest() != str(asset.get("table_sha256") or ""):
        raise ValueError("table_slice_hash_mismatch")
    parsed = parse_html_table(table_html, context=str(asset.get("context_before") or ""))
    if parsed.get("rows") != asset.get("rows"):
        raise ValueError("parsed_rows_drift")
    rows, provenance = parsed.get("rows") or [], parsed.get("cell_provenance") or []
    if len(rows) != len(provenance) or any(len(row) != len(prov) for row, prov in zip(rows, provenance)):
        raise ValueError("parsed_grid_invalid")
    source_provenance = {
        "source_path": str(asset["source_path"]),
        "source_sha256": str(asset["source_sha256"]),
        "char_start": int(asset["char_start"]),
        "table_sha256": str(asset["table_sha256"]),
    }
    table = {
        "internal_table_uid": str(asset["internal_table_uid"]),
        "document_id": str(asset["document_id"]),
        "local_ordinal": int(asset["local_ordinal"]),
        "source_provenance": source_provenance,
        **{key: parsed[key] for key in ("structure_version", "context_schema_version", "rows", "column_labels", "header_row_indices", "cell_provenance", "table_function", "table_section", "table_purpose", "context_trace", "structure_quality")},
    }
    context = {
        "internal_table_uid": table["internal_table_uid"],
        "document_id": table["document_id"],
        "local_ordinal": table["local_ordinal"],
        "evidence_context_version": "research_probe_v1",
        "source_provenance": source_provenance,
        "grid": {"rectangular": True, "provenance_complete": True, "width": max((len(row) for row in rows), default=0), "reason_codes": []},
        "canonical_headers": _header_context(parsed),
        "row_profiles": _row_profiles(parsed),
        # ``_candidate`` is intentionally evaluated under the same structural
        # V3 readiness check as the existing review bundle.  This declares only
        # that the reconstructed grid is review-ready at the source-structure
        # layer: the enclosing research contract remains the authority that
        # forbids using this transient sidecar as E2E evidence or an answer.
        "quality": {"status": "review_ready", "reason_codes": ["reconstructed_in_memory_research_only"]},
        "table_function": parsed["table_function"],
        "table_section": parsed["table_section"],
        "context_trace": parsed["context_trace"],
    }
    return table, context


def _unique_candidates(rows: Iterable[Mapping[str, Any]], plan: Mapping[str, Any], tables: Mapping[str, Mapping[str, Any]], contexts: Mapping[str, Mapping[str, Any]], *, minimum_row_jaccard: float) -> tuple[set[tuple[str, int, int]], Counter[str]]:
    candidates: set[tuple[str, int, int]] = set()
    failed = Counter()
    for row in rows:
        uid = str(row.get("internal_table_uid") or "")
        candidate, checks = _candidate(
            row=row,
            plan=plan,
            table=tables.get(uid),
            context=contexts.get(uid),
            minimum_row_jaccard=minimum_row_jaccard,
        )
        if candidate is None:
            failed.update(name for name, passed in checks.items() if passed is False)
            continue
        candidates.add((str(candidate["internal_table_uid"]), int(candidate["row_index"]), int(candidate["column_index"])))
    return candidates, failed


def _status(*, simple: bool, candidates: set[tuple[str, int, int]]) -> str:
    if not simple:
        return "NOT_SIMPLE_DIRECT_LOOKUP"
    if len(candidates) == 1:
        return "UNIQUE_STRICT_SOURCE_CANDIDATE"
    if len(candidates) > 1:
        return "MULTIPLE_STRICT_SOURCE_CANDIDATES"
    return "STRICT_SOURCE_GATES_INCOMPLETE"


def _direct_lookup_route_shape(plan: Mapping[str, Any], overlay: Mapping[str, Any]) -> str:
    """Classify the narrow, source-only direct-lookup shapes for this probe.

    The historical materializer's ``_simple_plan`` permits only a route whose
    ``reported_value`` operation is explicitly missing.  Some current routes
    say that this operation is covered, yet retain ``route_incomplete`` because
    no exact source has been materialized.  That is a route-state mismatch, not
    evidence.  We measure it as a separate shape, without changing the E2E
    materializer or treating the route as complete.
    """
    ast = plan.get("operation_ast") or {}
    core_direct_lookup = bool(
        plan.get("decomposition_status") == "complete"
        and plan.get("effective_family") == "direct_lookup"
        and isinstance(ast, Mapping)
        and ast.get("op") == "lookup"
        and len(plan.get("operands") or []) == 1
        and len(plan.get("entities") or []) == 1
        and len(plan.get("years") or []) == 1
        and overlay.get("route_status") == "route_incomplete"
        and overlay.get("required_operations") == ["reported_value"]
    )
    if not core_direct_lookup:
        return "NOT_ELIGIBLE"
    if overlay.get("missing_operations") == ["reported_value"]:
        return "REPORTED_VALUE_MISSING"
    if (
        overlay.get("missing_operations") == []
        and overlay.get("covered_operations") == ["reported_value"]
    ):
        return "REPORTED_VALUE_COVERED_ROUTE_STILL_INCOMPLETE"
    return "NOT_ELIGIBLE"


def build_hydrated_source_gate_probe(
    *,
    triage_path: Path,
    plans_path: Path,
    route_overlay_path: Path,
    row_review_queue_path: Path,
    full_assets_path: Path,
    full_assets_manifest_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
) -> dict[str, Any]:
    """Compare current and reconstructed-sidecar strict source readiness."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0:
        raise ValueError("minimum row jaccard must be between zero and one")
    _require_asset_manifest(full_assets_path, full_assets_manifest_path)
    triage = {int(row["question_id"]): row for row in _rows(triage_path)}
    if set(triage) != set(range(1, expected_question_count + 1)):
        raise ValueError("triage must cover the expected question population")
    target_ids = sorted(question_id for question_id, row in triage.items() if row.get("primary_blocker") == TARGET_BLOCKER)
    plans = {int(row["question_id"]): row for row in _rows(plans_path)}
    if not set(target_ids) <= set(plans):
        raise ValueError("plans do not cover direct-lookup targets")
    routes = {int(row["question_id"]): row for row in _rows(route_overlay_path)}
    if not set(target_ids) <= set(routes):
        raise ValueError("route overlay does not cover direct-lookup targets")
    review_by_question: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    requested_uids: set[str] = set()
    for row in _rows(row_review_queue_path):
        question_id = int(row.get("question_id") or 0)
        if question_id in target_ids:
            review_by_question[question_id].append(row)
            uid = str(row.get("internal_table_uid") or "")
            if uid:
                requested_uids.add(uid)
    current_tables = {str(row.get("internal_table_uid") or ""): row for row in _rows(structured_tables_path) if str(row.get("internal_table_uid") or "") in requested_uids}
    current_contexts = {str(row.get("internal_table_uid") or ""): row for row in _rows(evidence_context_path) if str(row.get("internal_table_uid") or "") in requested_uids}
    current_complete_ids = set(current_tables) & set(current_contexts)
    hydrate_ids = requested_uids - current_complete_ids
    assets: dict[str, dict[str, Any]] = {}
    for asset in _rows(full_assets_path):
        uid = str(asset.get("internal_table_uid") or "")
        if uid in hydrate_ids:
            assets[uid] = asset
    if hydrate_ids - set(assets):
        raise ValueError("review route table is missing from full assets")
    source_cache: dict[Path, tuple[str, str]] = {}
    hydrated_tables: dict[str, dict[str, Any]] = {}
    hydrated_contexts: dict[str, dict[str, Any]] = {}
    hydration_failures: Counter[str] = Counter()
    for uid in sorted(hydrate_ids):
        try:
            table, context = _hydrate(assets[uid], source_cache=source_cache)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            hydration_failures[str(error)] += 1
            continue
        hydrated_tables[uid] = table
        hydrated_contexts[uid] = context
    merged_tables = {**current_tables, **hydrated_tables}
    merged_contexts = {**current_contexts, **hydrated_contexts}
    audit_rows: list[dict[str, Any]] = []
    baseline_counts: Counter[str] = Counter()
    hydrated_counts: Counter[str] = Counter()
    improved = 0
    for question_id in target_ids:
        plan = plans[question_id]
        route_shape = _direct_lookup_route_shape(plan, routes[question_id])
        simple = route_shape != "NOT_ELIGIBLE"
        rows = review_by_question[question_id]
        baseline_candidates, baseline_failed = _unique_candidates(rows, plan, current_tables, current_contexts, minimum_row_jaccard=minimum_row_jaccard)
        hydrated_candidates, hydrated_failed = _unique_candidates(rows, plan, merged_tables, merged_contexts, minimum_row_jaccard=minimum_row_jaccard)
        baseline_status = _status(simple=simple, candidates=baseline_candidates)
        hydrated_status = _status(simple=simple, candidates=hydrated_candidates)
        baseline_counts[baseline_status] += 1
        hydrated_counts[hydrated_status] += 1
        improved += int(baseline_status != "UNIQUE_STRICT_SOURCE_CANDIDATE" and hydrated_status == "UNIQUE_STRICT_SOURCE_CANDIDATE")
        row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "direct_lookup_route_shape": route_shape,
            "baseline_status": baseline_status,
            "hydrated_status": hydrated_status,
            "baseline_candidate_count": len(baseline_candidates),
            "hydrated_candidate_count": len(hydrated_candidates),
            "baseline_failed_gate_counts": dict(sorted(baseline_failed.items())),
            "hydrated_failed_gate_counts": dict(sorted(hydrated_failed.items())),
            "current_v2_v3_table_count": sum(str(row.get("internal_table_uid") or "") in current_complete_ids for row in rows),
            "hydrated_table_count": sum(str(row.get("internal_table_uid") or "") in hydrated_tables for row in rows),
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(row):
            raise ValueError("hydrated source probe leaked unsafe content")
        audit_rows.append(row)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(target_ids),
        "target_review_table_count": len(requested_uids),
        "current_v2_v3_table_count": len(current_complete_ids),
        "hydrated_table_count": len(hydrated_tables),
        "hydration_failure_counts": dict(sorted(hydration_failures.items())),
        "minimum_row_jaccard": minimum_row_jaccard,
        "direct_lookup_route_shape_counts": dict(
            sorted(Counter(str(row["direct_lookup_route_shape"]) for row in audit_rows).items())
        ),
        "baseline_status_counts": dict(sorted(baseline_counts.items())),
        "hydrated_status_counts": dict(sorted(hydrated_counts.items())),
        "new_unique_strict_source_question_count": improved,
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "hydrated_source_gate_probe_v1.jsonl"
        summary_path = temporary / "hydrated_source_gate_probe_summary_v1.json"
        _write_jsonl(audit_path, audit_rows)
        _write_json(summary_path, summary)
        inputs = {
            "triage": triage_path,
            "plans": plans_path,
            "route_overlay": route_overlay_path,
            "row_review_queue": row_review_queue_path,
            "full_assets": full_assets_path,
            "full_assets_manifest": full_assets_manifest_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    audit_path.name: {"path": audit_path.name, "sha256": sha256_file(audit_path)},
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


def validate_hydrated_source_gate_probe(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected hydrated source probe contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("hydrated source probe input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("hydrated source probe output hash mismatch")
    rows = list(_rows(artifact_dir / "hydrated_source_gate_probe_v1.jsonl"))
    summary = _json(artifact_dir / "hydrated_source_gate_probe_summary_v1.json")
    if any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT for row in rows):
        raise ValueError("hydrated source probe leaked unsafe content")
    if len({int(row["question_id"]) for row in rows}) != len(rows):
        raise ValueError("hydrated source probe question IDs are duplicated")
    if int(summary.get("target_question_count") or 0) != len(rows) or int(summary.get("target_question_count") or 0) > expected_question_count:
        raise ValueError("hydrated source probe coverage mismatch")
    for key in ("baseline_status_counts", "hydrated_status_counts"):
        if sum(int(value) for value in (summary.get(key) or {}).values()) != len(rows):
            raise ValueError("hydrated source probe status summary mismatch")
    route_shapes = Counter(str(row.get("direct_lookup_route_shape") or "") for row in rows)
    if dict(sorted(route_shapes.items())) != summary.get("direct_lookup_route_shape_counts"):
        raise ValueError("hydrated source probe route-shape summary mismatch")
    return {
        "status": "PASS",
        "target_question_count": len(rows),
        "new_unique_strict_source_question_count": int(summary.get("new_unique_strict_source_question_count") or 0),
        "answer_eligible": False,
        "submission_eligible": False,
    }
