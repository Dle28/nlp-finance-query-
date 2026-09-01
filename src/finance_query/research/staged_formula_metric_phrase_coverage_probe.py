"""Coverage-first phrase recheck for the remaining staged-formula blockers.

The first metric-phrase probe only searched tables already returned by the
normal retrieval lane.  This companion probe tests a narrow alternative:
within the requested ticker, report year, and declared statement type, scan
the immutable corpus for the same explicit metric phrase.  It is therefore a
retrieval-coverage diagnostic, not a relaxation of provenance, period, unit,
scope, or row-uniqueness checks.

Results remain value-blind navigation metadata.  They cannot become evidence,
answers, training data, promotion decisions, or a competition submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.financial_taxonomy import normalize_label
from finance_query.research.composition_operand_source_gap_audit import _subplan
from finance_query.research.full_corpus_candidate_retrieval import validate_candidate_artifact
from finance_query.research.hydrated_source_gate_probe import _hydrate, _require_asset_manifest
from finance_query.research.machine_exact_cell_proposals import sha256_file
from finance_query.research.staged_formula_current_period_probe import _current_period_status
from finance_query.research.staged_formula_hydrated_source_probe import _strict_status
from finance_query.research.staged_formula_metric_phrase_probe import (
    CONTRACT,
    _combined_status,
    _contains_forbidden,
    _read_json,
    _read_jsonl,
    _rules,
    _semantic_rows,
    validate_staged_formula_metric_phrase_probe,
)


PROTOCOL = "vifinqa_staged_formula_metric_phrase_coverage_probe_v1"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _normalised_table_function(value: object) -> str:
    observed = str(value or "")
    return {"financial_note": "financial_note_detail"}.get(observed, observed)


def _asset_matches_operand(asset: Mapping[str, Any], operand: Mapping[str, Any]) -> bool:
    """Apply typed ticker/year/table-function constraints before phrase scan."""
    if str(asset.get("ticker") or "").upper() != str(operand.get("ticker") or "").upper():
        return False
    try:
        report_year = int(asset.get("report_year") or 0)
        requested_year = int((operand.get("years") or [0])[0])
    except (TypeError, ValueError):
        return False
    if report_year != requested_year:
        return False
    expected = {str(item) for item in operand.get("allowed_table_functions") or []}
    observed = _normalised_table_function((asset.get("table_function") or {}).get("kind"))
    return not expected or observed in expected


def _asset_candidate(*, question_id: int, operand_id: str, operand: Mapping[str, Any], asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "operand_id": operand_id,
        "ticker": str(operand.get("ticker") or ""),
        "report_year": int((operand.get("years") or [0])[0]),
        "observed_scope": asset.get("scope"),
        "internal_table_uid": str(asset.get("internal_table_uid") or ""),
    }


def _metric_rule(operand: Mapping[str, Any], rules: Mapping[str, Mapping[str, Any]]) -> tuple[str, Mapping[str, Any] | None]:
    hints = operand.get("metric_hints") or []
    metric = normalize_label(hints[1] if len(hints) > 1 else hints[0] if hints else "")
    return metric, rules.get(metric)


def build_staged_formula_metric_phrase_coverage_probe(
    *,
    config_path: Path,
    plans_path: Path,
    candidate_artifact_dir: Path,
    baseline_phrase_artifact_dir: Path,
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
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("source thresholds must be between zero and one")
    rules = _rules(config_path)
    validate_candidate_artifact(candidate_artifact_dir, expected_question_count=expected_question_count)
    validate_staged_formula_metric_phrase_probe(
        baseline_phrase_artifact_dir, expected_question_count=expected_question_count
    )
    _require_asset_manifest(full_assets_path, full_assets_manifest_path)
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    if set(plans) != set(range(1, expected_question_count + 1)):
        raise ValueError("plans do not cover the expected population")

    phrase_rows = _read_jsonl(baseline_phrase_artifact_dir / "staged_formula_metric_phrase_probe_v1.jsonl")
    target_keys = {
        (int(row["question_id"]), str(row["operand_id"]))
        for row in phrase_rows
        if row.get("combined_status") == "SEMANTIC_SOURCE_GATES_INCOMPLETE"
    }
    if not target_keys:
        raise ValueError("coverage probe has no phrase-incomplete operands")
    if any(plans[question_id].get("decomposition_status") != "typed_non_executable" for question_id, _ in target_keys):
        raise ValueError("coverage probe targets must be typed staged plans")

    candidate_manifest = _read_json(candidate_artifact_dir / "manifest.json")
    table_candidate_path = candidate_artifact_dir / "table_candidates_v1.jsonl"
    candidate_descriptor = (candidate_manifest.get("outputs") or {}).get(table_candidate_path.name) or {}
    if sha256_file(table_candidate_path) != candidate_descriptor.get("sha256"):
        raise ValueError("candidate table list does not match its manifest")
    original_by_key: defaultdict[tuple[int, str], set[str]] = defaultdict(set)
    for candidate in _read_jsonl(table_candidate_path):
        key = (int(candidate.get("question_id") or 0), str(candidate.get("operand_id") or ""))
        if key in target_keys:
            original_by_key[key].add(str(candidate.get("internal_table_uid") or ""))

    all_assets = _read_jsonl(full_assets_path)
    assets = {str(row.get("internal_table_uid") or ""): row for row in all_assets}
    if "" in assets:
        raise ValueError("immutable asset corpus contains an empty table identity")
    current_tables = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(structured_tables_path)}
    current_contexts = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(evidence_context_path)}

    expanded_by_key: dict[tuple[int, str], list[dict[str, Any]]] = {}
    requested_uids: set[str] = set()
    for question_id, operand_id in sorted(target_keys):
        operand = next(
            (row for row in plans[question_id].get("operands") or [] if str(row.get("operand_id") or "") == operand_id),
            None,
        )
        if not isinstance(operand, Mapping):
            raise ValueError("coverage target is absent from typed plan")
        expanded = [
            _asset_candidate(question_id=question_id, operand_id=operand_id, operand=operand, asset=asset)
            for asset in all_assets
            if _asset_matches_operand(asset, operand)
        ]
        if not expanded:
            raise ValueError("coverage probe found no ticker/year/statement-type tables")
        expanded_by_key[(question_id, operand_id)] = expanded
        requested_uids.update(str(candidate["internal_table_uid"]) for candidate in expanded)
    if requested_uids - set(assets):
        raise ValueError("coverage table is absent from immutable assets")

    complete_ids = set(current_tables) & set(current_contexts) & requested_uids
    hydrate_ids = requested_uids - complete_ids
    source_cache: dict[Path, tuple[str, str]] = {}
    hydrated_tables: dict[str, dict[str, Any]] = {}
    hydrated_contexts: dict[str, dict[str, Any]] = {}
    failures = Counter()
    for table_uid in sorted(hydrate_ids):
        try:
            table, context = _hydrate(assets[table_uid], source_cache=source_cache)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            failures[str(error)] += 1
            continue
        hydrated_tables[table_uid] = table
        hydrated_contexts[table_uid] = context
    tables = {**current_tables, **hydrated_tables}
    contexts = {**current_contexts, **hydrated_contexts}

    audit: list[dict[str, Any]] = []
    for question_id, operand_id in sorted(target_keys):
        operand = next(row for row in plans[question_id].get("operands") or [] if str(row.get("operand_id") or "") == operand_id)
        _, rule = _metric_rule(operand, rules)
        expanded = expanded_by_key[(question_id, operand_id)]
        semantic_rows = _semantic_rows(table_candidates=expanded, assets=assets, rule=rule)
        semantic_uids = {str(row.get("internal_table_uid") or "") for row in semantic_rows}
        original_uids = original_by_key[(question_id, operand_id)] - {""}
        subplan = _subplan(question_id=question_id, operand=operand)
        literal = (
            {
                "status": "OPERAND_PLAN_NOT_SINGLE_ENTITY_AND_YEAR",
                "strict_source_candidate_count": 0,
                "navigation_candidates": [],
                "first_stage_failed_gate_counts": {},
                "second_stage_failed_gate_counts": {},
            }
            if subplan is None
            else _strict_status(
                source_rows=semantic_rows,
                plan=subplan,
                tables=tables,
                contexts=contexts,
                minimum_row_jaccard=minimum_row_jaccard,
                minimum_row_margin=minimum_row_margin,
            )
        )
        current = _current_period_status(
            source_rows=semantic_rows,
            operand=operand,
            tables=tables,
            contexts=contexts,
            minimum_row_jaccard=minimum_row_jaccard,
            minimum_row_margin=minimum_row_margin,
        )
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "operand_id": operand_id,
            "rule_id": None if rule is None else rule["rule_id"],
            "original_candidate_table_count": len(original_uids),
            "expanded_table_count": len(expanded),
            "expanded_new_table_count": len({str(row["internal_table_uid"]) for row in expanded} - original_uids),
            "semantic_match_table_count": len(semantic_uids),
            "semantic_new_table_match_count": len(semantic_uids - original_uids),
            "semantic_match_row_count": len(semantic_rows),
            "literal": literal,
            "current": current,
            "combined_status": _combined_status(literal, current),
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("coverage probe leaked a forbidden field")
        audit.append(record)

    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "baseline_phrase_incomplete_operand_count": len(target_keys),
        "combined_status_counts": dict(sorted(Counter(row["combined_status"] for row in audit).items())),
        "semantic_rule_match_operand_count": sum(row["semantic_match_row_count"] > 0 for row in audit),
        "expanded_new_table_count": sum(row["expanded_new_table_count"] for row in audit),
        "semantic_new_table_match_count": sum(row["semantic_new_table_match_count"] for row in audit),
        "requested_table_count": len(requested_uids),
        "hydrated_table_count": len(hydrated_tables),
        "hydration_failure_counts": dict(sorted(failures.items())),
        "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin},
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "staged_formula_metric_phrase_coverage_probe_v1.jsonl"
        summary_path = temporary / "staged_formula_metric_phrase_coverage_probe_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "config": config_path,
            "plans": plans_path,
            "candidate_manifest": candidate_artifact_dir / "manifest.json",
            "baseline_phrase_manifest": baseline_phrase_artifact_dir / "manifest.json",
            "full_assets": full_assets_path,
            "full_assets_manifest": full_assets_manifest_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                    "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_staged_formula_metric_phrase_coverage_probe(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected metric phrase coverage probe contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("metric phrase coverage probe hash mismatch")
    audit = _read_jsonl(artifact_dir / "staged_formula_metric_phrase_coverage_probe_v1.jsonl")
    if not audit or not {int(row["question_id"]) for row in audit} <= set(range(1, expected_question_count + 1)):
        raise ValueError("metric phrase coverage probe question coverage is invalid")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        for row in audit
    ):
        raise ValueError("metric phrase coverage probe lost its non-authorizing boundary")
    summary = _read_json(artifact_dir / "staged_formula_metric_phrase_coverage_probe_summary_v1.json")
    counts = dict(sorted(Counter(row["combined_status"] for row in audit).items()))
    if summary.get("baseline_phrase_incomplete_operand_count") != len(audit) or summary.get("combined_status_counts") != counts:
        raise ValueError("metric phrase coverage probe summary mismatch")
    return {
        "status": "PASS",
        "baseline_phrase_incomplete_operand_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
