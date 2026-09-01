"""Create value-blind exact-cell candidates from explicit-ticker source probes.

This is a narrow bridge for direct lookups whose company and scope are already
explicit in the question.  It replays the semantic rule, V2/V3 provenance,
explicit year-end header, unit and row-margin checks before emitting a
candidate coordinate.  The result is a machine research artifact only; it is
not evidence, an answer, or a submission record.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit
from finance_query.research.explicit_ticker_semantic_source_probe import _effective_operand, _rules
from finance_query.research.full_corpus_candidate_retrieval import validate_candidate_artifact
from finance_query.research.hydrated_source_gate_probe import _require_asset_manifest
from finance_query.research.machine_exact_cell_proposals import sha256_file
from finance_query.research.staged_formula_current_period_probe import _current_header_row_margin_candidate
from finance_query.research.staged_formula_metric_phrase_probe import (
    _explicit_year_end_header_candidate,
    _semantic_rows,
)
from finance_query.research.composition_operand_source_gap_audit import _row_candidates


PROTOCOL = "vifinqa_explicit_ticker_exact_cell_adapter_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "exact_coordinate_candidate_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset({"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "raw_source_cell", "raw_source_row", "rows", "cell_provenance", "human_verified"})


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values), encoding="utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _targets(path: Path) -> set[int]:
    rows = _read_jsonl(path)
    target_ids = {int(row.get("question_id") or 0) for row in rows}
    if not target_ids or 0 in target_ids:
        raise ValueError("exact-cell target list is empty or invalid")
    return target_ids


def build_explicit_ticker_exact_cell_adapter(
    *,
    targets_path: Path,
    semantic_config_path: Path,
    plans_path: Path,
    candidate_artifact_dir: Path,
    full_assets_path: Path,
    full_assets_manifest_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.8,
    minimum_row_margin: float = 0.1,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    target_ids, rules = _targets(targets_path), _rules(semantic_config_path)
    target_keys = {(question_id, "x0") for question_id in target_ids}
    if target_keys != set(rules) & target_keys:
        raise ValueError("every exact-cell target needs one semantic source rule")
    validate_candidate_artifact(candidate_artifact_dir, expected_question_count=expected_question_count)
    _require_asset_manifest(full_assets_path, full_assets_manifest_path)
    evidence_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != evidence_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    if set(plans) != set(range(1, expected_question_count + 1)):
        raise ValueError("plans have invalid coverage")
    for question_id in target_ids:
        plan = plans[question_id]
        if len(plan.get("operands") or []) != 1 or plan.get("effective_family") != "direct_lookup":
            raise ValueError("exact-cell target is not a simple direct lookup")
        if plan["operands"][0].get("scope") not in {"separate", "consolidated"}:
            raise ValueError("exact-cell target lacks an explicit scope")

    candidate_path = candidate_artifact_dir / "table_candidates_v1.jsonl"
    candidate_manifest = _read_json(candidate_artifact_dir / "manifest.json")
    if sha256_file(candidate_path) != ((candidate_manifest.get("outputs") or {}).get(candidate_path.name) or {}).get("sha256"):
        raise ValueError("candidate artifact table list hash mismatch")
    candidates_by_question: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    requested_uids: set[str] = set()
    for candidate in _read_jsonl(candidate_path):
        question_id = int(candidate.get("question_id") or 0)
        if question_id in target_ids and str(candidate.get("operand_id") or "") == "x0":
            candidates_by_question[question_id].append(candidate)
            requested_uids.add(str(candidate.get("internal_table_uid") or ""))
    if not requested_uids or "" in requested_uids:
        raise ValueError("exact-cell target has no table candidates")
    assets = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(full_assets_path) if str(row.get("internal_table_uid") or "") in requested_uids}
    tables = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(structured_tables_path) if str(row.get("internal_table_uid") or "") in requested_uids}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(evidence_context_path) if str(row.get("internal_table_uid") or "") in requested_uids}
    # Candidate retrieval can return more tables than the immutable V2/V3
    # sidecars cover.  Those tables are not silently hydrated here: an exact
    # binding needs the already hash-checked sidecars.  Keep only covered
    # candidates and report the omitted navigation set in the summary.
    available_uids = set(assets) & set(tables) & set(contexts)
    unavailable_uids = requested_uids - available_uids
    candidates_by_question = defaultdict(
        list,
        {
            question_id: [candidate for candidate in candidates if str(candidate.get("internal_table_uid") or "") in available_uids]
            for question_id, candidates in candidates_by_question.items()
        },
    )
    if any(not candidates_by_question[question_id] for question_id in target_ids):
        raise ValueError("exact-cell target has no candidate covered by immutable V2/V3/full assets")

    diagnostics: list[dict[str, Any]] = []
    row_candidates: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for question_id in sorted(target_ids):
        plan, rule = plans[question_id], rules[(question_id, "x0")]
        operand = _effective_operand(plan["operands"][0], rule)
        semantic_rows = _semantic_rows(table_candidates=candidates_by_question[question_id], assets=assets, rule=rule)
        rows_by_uid: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in semantic_rows:
            rows_by_uid[str(row["internal_table_uid"])].append(row)
        accepted: list[tuple[dict[str, Any], Mapping[str, Any], tuple[str, int, int]]] = []
        failed = Counter()
        for row in semantic_rows:
            uid = str(row["internal_table_uid"])
            candidate, checks = _explicit_year_end_header_candidate(row=row, operand=operand, table=tables[uid], context=contexts[uid], minimum_row_jaccard=minimum_row_jaccard)
            if candidate is None:
                failed.update(name for name, passed in checks.items() if passed is False)
                continue
            final, margin_checks = _current_header_row_margin_candidate(candidate=candidate, rows=_row_candidates(rows_by_uid[uid], table_uid=uid), table=tables[uid], context=contexts[uid], minimum_row_jaccard=minimum_row_jaccard, minimum_row_margin=minimum_row_margin)
            if final is None:
                failed.update(name for name, passed in margin_checks.items() if passed is False)
                continue
            accepted.append((candidate, row, final))
        unique = len({final for _, _, final in accepted}) == 1
        if unique:
            candidate, source_row, final = accepted[0]
            uid, row_index, column_index = final
            table, context = tables[uid], contexts[uid]
            header = next(header for header in ((context.get("canonical_headers") or {}).get("columns") or []) if isinstance(header, Mapping) and int(header.get("column_index") or -1) == column_index)
            header_cells = list(header.get("header_source_cells") or [])
            header_texts = [str(table["rows"][int(cell["row_index"])][int(cell["column_index"])]) for cell in header_cells if isinstance(cell, Mapping)]
            unit, _, multiplier = resolve_source_unit([{"raw_source_cell": str(header.get("source_label") or "")}])
            if unit is None or multiplier is None or not header_texts:
                raise ValueError("accepted exact-cell candidate lost header unit or provenance")
            locator = candidate["exact_table_locator"]
            diagnostic_id = _sha_json({"question_id": question_id, "internal_table_uid": uid, "row_index": row_index, "column_index": column_index, "locator": locator})
            diagnostics.append({
                "protocol": PROTOCOL, "diagnostic_id": diagnostic_id, "question_id": question_id,
                "document_id": table["document_id"], "internal_table_uid": uid,
                "requested_year": candidate["requested_year"], "exact_table_locator": locator,
                "exact_table_locator_sha256": _sha_json(locator), "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
                "period_resolution": "explicit_year_end_header", "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
                "scope_status": "SCOPE_MATCH", "source_unit_candidate": unit,
                "matching_year_column_indices": [column_index],
                "column_headers": [{"column_index": column_index, "header_sha256": hashlib.sha256("\n".join(header_texts).encode("utf-8")).hexdigest()}],
                "source_contract": dict(CONTRACT),
            })
            for row in rows_by_uid[uid]:
                row_candidates.append({
                    "protocol": PROTOCOL, "diagnostic_id": diagnostic_id, "row_index": int(row["row_index"]),
                    "row_rank": int(row["row_rank"]), "row_label": str(row["row_label"]),
                    "row_label_sha256": hashlib.sha256(str(row["row_label"]).encode("utf-8")).hexdigest(),
                    "row_label_token_jaccard": float(row["row_token_jaccard"]),
                    "numeric_column_indices": list(row["numeric_cell_indices"]), "source_contract": dict(CONTRACT),
                })
        audit_row = {"schema_version": 1, "protocol": PROTOCOL, "question_id": question_id,
            "adapter_status": "UNIQUE_EXACT_CELL_CANDIDATE" if unique else "QUARANTINED_EXACT_CELL_CANDIDATE",
            "accepted_coordinate_count": len({final for _, _, final in accepted}), "failed_gate_counts": dict(sorted(failed.items())),
            "raw_numeric_values_included": False, "source_contract": dict(CONTRACT)}
        if _contains_forbidden(audit_row):
            raise ValueError("exact-cell adapter leaked forbidden content")
        audit.append(audit_row)
    summary = {"schema_version": 1, "protocol": PROTOCOL, "target_question_count": len(target_ids), "diagnostic_count": len(diagnostics), "row_candidate_count": len(row_candidates), "status_counts": dict(sorted(Counter(row["adapter_status"] for row in audit).items())), "requested_table_count": len(requested_uids), "available_sidecar_table_count": len(available_uids), "unavailable_sidecar_table_count": len(unavailable_uids), "source_contract": dict(CONTRACT)}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        paths = {"table_diagnostics": temporary / "table_diagnostics_v1.jsonl", "row_candidates": temporary / "row_candidates_v1.jsonl", "audit": temporary / "explicit_ticker_exact_cell_adapter_audit_v1.jsonl", "summary": temporary / "explicit_ticker_exact_cell_adapter_summary_v1.json"}
        _write_jsonl(paths["table_diagnostics"], diagnostics); _write_jsonl(paths["row_candidates"], row_candidates); _write_jsonl(paths["audit"], audit); _write_json(paths["summary"], summary)
        inputs = {"targets": targets_path, "semantic_config": semantic_config_path, "plans": plans_path, "candidate_manifest": candidate_artifact_dir / "manifest.json", "full_assets": full_assets_path, "full_assets_manifest": full_assets_manifest_path, "structured_tables_v2": structured_tables_path, "evidence_context_v3": evidence_context_path, "evidence_context_manifest_v3": evidence_context_manifest_path}
        _write_json(temporary / "manifest.json", {"schema_version": 1, "protocol": PROTOCOL, "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()}, "outputs": {name: {"path": path.name, "sha256": sha256_file(path)} for name, path in paths.items()}, "source_contract": dict(CONTRACT)})
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_explicit_ticker_exact_cell_adapter(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected exact-cell adapter contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("exact-cell adapter hash mismatch")
    audit = _read_jsonl(artifact_dir / "explicit_ticker_exact_cell_adapter_audit_v1.jsonl")
    diagnostics = _read_jsonl(artifact_dir / "table_diagnostics_v1.jsonl")
    rows = _read_jsonl(artifact_dir / "row_candidates_v1.jsonl")
    unique_ids = {int(row["question_id"]) for row in audit if row.get("adapter_status") == "UNIQUE_EXACT_CELL_CANDIDATE"}
    if any(_contains_forbidden(row) or row.get("raw_numeric_values_included") is not False or row.get("source_contract") != CONTRACT for row in audit):
        raise ValueError("exact-cell adapter lost its non-authorizing boundary")
    if {int(row["question_id"]) for row in diagnostics} != unique_ids or len(diagnostics) != len(unique_ids):
        raise ValueError("exact-cell diagnostics are not one-to-one with unique candidates")
    diagnostic_ids = {str(row["diagnostic_id"]) for row in diagnostics}
    if any(str(row.get("diagnostic_id") or "") not in diagnostic_ids or row.get("source_contract") != CONTRACT for row in rows):
        raise ValueError("exact-cell adapter rows have invalid diagnostic lineage")
    summary = _read_json(artifact_dir / "explicit_ticker_exact_cell_adapter_summary_v1.json")
    if summary.get("diagnostic_count") != len(diagnostics) or summary.get("target_question_count") != len(audit):
        raise ValueError("exact-cell adapter summary mismatch")
    return {"status": "PASS", "target_question_count": len(audit), "diagnostic_count": len(diagnostics), "answer_eligible": False, "submission_eligible": False}
