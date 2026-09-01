"""Value-blind full-role header probe for ambiguous financial-note layouts.

The probe treats an OCR table's period, balance role, and account role as one
selector.  It was added after a date-only recheck could mistake a 31/12
column for an opening balance or confuse payable with receivable.  It is
research only: no values, cell coordinates, evidence bindings, answers, or
submission records are emitted.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.explicit_ticker_semantic_source_probe import (
    CONTRACT as BASE_CONTRACT,
    PROTOCOL as BASE_PROTOCOL,
    validate_explicit_ticker_semantic_source_probe,
)
from finance_query.research.full_corpus_candidate_retrieval import validate_candidate_artifact
from finance_query.research.hydrated_source_gate_probe import _hydrate, _require_asset_manifest
from finance_query.research.machine_exact_cell_proposals import sha256_file
from finance_query.research.staged_formula_metric_phrase_probe import (
    _collapse_verified_page_continuations,
    _compact,
    _contains_forbidden,
    _role_header_status,
    _semantic_rows,
)


PROTOCOL = "vifinqa_explicit_ticker_role_header_probe_v1"
CONTRACT = {
    **BASE_CONTRACT,
    "header_role_navigation_only": True,
}


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
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _rules(path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    payload = _read_json(path)
    if payload.get("protocol") != PROTOCOL or int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unexpected role-header probe config")
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for raw in payload.get("rules") or []:
        if not isinstance(raw, Mapping):
            raise ValueError("role-header rule must be an object")
        question_id, operand_id = int(raw.get("question_id") or 0), str(raw.get("operand_id") or "")
        row_fragments = tuple(str(value) for value in raw.get("required_row_compact_fragments") or [])
        header_fragments = tuple(str(value) for value in raw.get("required_header_compact_fragments") or [])
        allowed_functions = tuple(str(value) for value in raw.get("allowed_table_functions") or [])
        period_role, rule_id = str(raw.get("period_role") or ""), str(raw.get("rule_id") or "")
        key = (question_id, operand_id)
        if (
            question_id <= 0
            or not operand_id
            or not rule_id
            or period_role not in {"opening", "closing"}
            or not row_fragments
            or not header_fragments
            or not allowed_functions
            or any(not value or _compact(value) != value for value in (*row_fragments, *header_fragments))
            or any(not value for value in allowed_functions)
            or key in result
        ):
            raise ValueError("role-header rule is incomplete or duplicated")
        result[key] = {
            "rule_id": rule_id,
            "required": row_fragments,
            "forbidden": (),
            "primary_statement_only": False,
            "required_account_codes": (),
            "allowed_table_functions": allowed_functions,
            "period_role": period_role,
            "required_header_compact_fragments": header_fragments,
        }
    if not result:
        raise ValueError("role-header probe has no rules")
    return result


def _effective_operand(operand: Mapping[str, Any], rule: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(operand))
    result["allowed_table_functions"] = list(rule["allowed_table_functions"])
    return result


def build_explicit_ticker_role_header_probe(
    *,
    config_path: Path,
    plans_path: Path,
    base_artifact_dir: Path,
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
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("source thresholds must be between zero and one")
    rules = _rules(config_path)
    base_validation = validate_explicit_ticker_semantic_source_probe(base_artifact_dir, expected_question_count=expected_question_count)
    if base_validation.get("status") != "PASS" or _read_json(base_artifact_dir / "manifest.json").get("protocol") != BASE_PROTOCOL:
        raise ValueError("role-header probe requires a validated semantic-source base artifact")
    validate_candidate_artifact(candidate_artifact_dir, expected_question_count=expected_question_count)
    _require_asset_manifest(full_assets_path, full_assets_manifest_path)
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    if set(plans) != set(range(1, expected_question_count + 1)):
        raise ValueError("plans have invalid coverage")
    for question_id, operand_id in rules:
        if not any(str(row.get("operand_id") or "") == operand_id for row in plans[question_id].get("operands") or []):
            raise ValueError("role-header rule references an absent operand")

    candidate_manifest = _read_json(candidate_artifact_dir / "manifest.json")
    candidate_path = candidate_artifact_dir / "table_candidates_v1.jsonl"
    if sha256_file(candidate_path) != ((candidate_manifest.get("outputs") or {}).get(candidate_path.name) or {}).get("sha256"):
        raise ValueError("candidate table list does not match its manifest")
    candidates_by_key: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    requested_uids: set[str] = set()
    for candidate in _read_jsonl(candidate_path):
        key = (int(candidate.get("question_id") or 0), str(candidate.get("operand_id") or ""))
        if key in rules:
            candidates_by_key[key].append(candidate)
            requested_uids.add(str(candidate.get("internal_table_uid") or ""))
    if not requested_uids or "" in requested_uids:
        raise ValueError("role-header target has no candidate tables")
    assets = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(full_assets_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    if requested_uids - set(assets):
        raise ValueError("role-header candidate table is absent from immutable assets")
    current_tables = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(structured_tables_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    current_contexts = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(evidence_context_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    hydrate_ids = requested_uids - (set(current_tables) & set(current_contexts))
    cache: dict[Path, tuple[str, str]] = {}
    hydrated_tables: dict[str, dict[str, Any]] = {}
    hydrated_contexts: dict[str, dict[str, Any]] = {}
    failures = Counter()
    for table_uid in sorted(hydrate_ids):
        try:
            table, context = _hydrate(assets[table_uid], source_cache=cache)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            failures[str(error)] += 1
            continue
        hydrated_tables[table_uid], hydrated_contexts[table_uid] = table, context
    tables, contexts = {**current_tables, **hydrated_tables}, {**current_contexts, **hydrated_contexts}

    audit: list[dict[str, Any]] = []
    for question_id, operand_id in sorted(rules):
        rule = rules[(question_id, operand_id)]
        original_operand = next(row for row in plans[question_id].get("operands") or [] if str(row.get("operand_id") or "") == operand_id)
        operand = _effective_operand(original_operand, rule)
        raw_rows = _semantic_rows(table_candidates=candidates_by_key[(question_id, operand_id)], assets=assets, rule=rule)
        semantic_rows, collapsed = _collapse_verified_page_continuations(source_rows=raw_rows, tables=tables, contexts=contexts)
        role_header = _role_header_status(
            source_rows=semantic_rows,
            operand=operand,
            tables=tables,
            contexts=contexts,
            period_role=str(rule["period_role"]),
            required_header_compact_fragments=rule["required_header_compact_fragments"],
            minimum_row_jaccard=minimum_row_jaccard,
            minimum_row_margin=minimum_row_margin,
        )
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "operand_id": operand_id,
            "rule_id": rule["rule_id"],
            "semantic_match_row_count": len(semantic_rows),
            "raw_semantic_match_row_count": len(raw_rows),
            "page_continuation_collapsed_row_count": collapsed,
            "role_header": role_header,
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("role-header probe leaked a forbidden field")
        audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_operand_count": len(audit),
        "role_header_status_counts": dict(sorted(Counter(row["role_header"]["operand_status"] for row in audit).items())),
        "semantic_rule_match_operand_count": sum(row["semantic_match_row_count"] > 0 for row in audit),
        "requested_table_count": len(requested_uids),
        "hydrated_table_count": len(hydrated_tables),
        "hydration_failure_counts": dict(sorted(failures.items())),
        "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin},
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path, summary_path = temporary / "explicit_ticker_role_header_probe_v1.jsonl", temporary / "explicit_ticker_role_header_probe_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "config": config_path,
            "plans": plans_path,
            "base_artifact_manifest": base_artifact_dir / "manifest.json",
            "candidate_manifest": candidate_artifact_dir / "manifest.json",
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
                "outputs": {"audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)}, "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)}},
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_explicit_ticker_role_header_probe(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected role-header probe contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("role-header probe hash mismatch")
    audit = _read_jsonl(artifact_dir / "explicit_ticker_role_header_probe_v1.jsonl")
    if not audit or not {int(row["question_id"]) for row in audit} <= set(range(1, expected_question_count + 1)):
        raise ValueError("role-header probe question coverage is invalid")
    if any(_contains_forbidden(row) or row.get("raw_numeric_values_included") is not False or row.get("source_contract") != CONTRACT for row in audit):
        raise ValueError("role-header probe lost its boundary")
    summary = _read_json(artifact_dir / "explicit_ticker_role_header_probe_summary_v1.json")
    counts = dict(sorted(Counter(row["role_header"]["operand_status"] for row in audit).items()))
    if summary.get("target_operand_count") != len(audit) or summary.get("role_header_status_counts") != counts:
        raise ValueError("role-header probe summary mismatch")
    return {"status": "PASS", "target_operand_count": len(audit), "answer_eligible": False, "submission_eligible": False}
