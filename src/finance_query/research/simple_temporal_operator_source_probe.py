"""Value-blind source probe for plainly worded two-period changes.

The probe is deliberately prior to E2E materialisation.  A temporal phrase
may define a simple arithmetic direction while its two report rows remain
missing or ambiguous.  This artifact proves which condition is blocking each
candidate without choosing a value or making a route executable.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.questions import normalize_text
from finance_query.e2e.question_compiler import _typed_operand
from finance_query.research.full_corpus_candidate_retrieval import (
    _row_candidates,
    _search_operand_year,
    metric_core_query,
)
from finance_query.research.full_corpus_direct_lookup_adapter import _candidate
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_simple_temporal_operator_source_probe_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset({
    "answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query",
    "rows", "raw_source_row", "raw_source_cell", "source_label", "human_verified",
})
PERCENT_INCREASE_RE = re.compile(r"\btăng\s+bao\s+nhiêu\s*(?:%|phần\s+trăm)\s+so\s+với\b", re.IGNORECASE)
GREATER_RE = re.compile(r"\blớn\s+hơn\s+bao\s+nhiêu\b.+\bso\s+với\b", re.IGNORECASE)
LESSER_RE = re.compile(r"\bthấp\s+hơn\s+[^?]*\bso\s+với\b", re.IGNORECASE)
LEADING_DATE_RE = re.compile(r"^(?:đến|vào|tại)\s+(?:ngày\s+)?\d{1,2}/\d{1,2}/(?:19|20)\d{2}\s*,?\s*", re.IGNORECASE)
ISSUER_BOUNDARY_RE = re.compile(
    r"\s+của\s+(?:công\s+ty\s+mẹ|ctcp|công\s+ty\s+cổ\s+phần|ngân\s+hàng|tập\s+đoàn|tổng\s+công\s+ty)\b",
    re.IGNORECASE,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
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


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_output(manifest_path: Path, output_name: str, path: Path) -> None:
    manifest = _read_json(manifest_path)
    expected = ((manifest.get("outputs") or {}).get(output_name) or {}).get("sha256")
    if not expected or sha256_file(path) != expected:
        raise ValueError(f"{path} does not match {manifest_path} output {output_name}")


def _candidate_operation(question: str) -> tuple[str, str] | None:
    if PERCENT_INCREASE_RE.search(question):
        return "percentage_change", "NEW_MINUS_OLD_DIVIDED_BY_OLD"
    if GREATER_RE.search(question):
        return "subtract", "NEW_MINUS_OLD"
    if LESSER_RE.search(question):
        return "subtract", "OLD_MINUS_NEW"
    return None


def _metric_hint(question: str) -> str | None:
    value = LEADING_DATE_RE.sub("", normalize_text(question))
    boundary = ISSUER_BOUNDARY_RE.search(value)
    if boundary is not None:
        value = value[: boundary.start()]
    value = re.sub(r"^(?:số\s+dư|giá\s+trị)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:tăng|lớn\s+hơn|thấp\s+hơn)\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ,:;.-")
    return value if len(value) >= 4 and any(character.isalpha() for character in value) else None


def _eligible(plan: Mapping[str, Any]) -> tuple[str, str, str] | None:
    if (
        plan.get("decomposition_status") != "abstain"
        or not {"MISSING_OPERANDS", "UNKNOWN_OPERAND_STRUCTURE"}.issubset(plan.get("reason_codes") or [])
        or len(plan.get("entities") or []) != 1
        or len(plan.get("years") or []) != 2
    ):
        return None
    operation = _candidate_operation(str(plan.get("question") or ""))
    metric = _metric_hint(str(plan.get("question") or ""))
    if operation is None or metric is None:
        return None
    return operation[0], operation[1], metric


def build_simple_temporal_operator_source_probe(
    *,
    plans_path: Path,
    lexical_index_path: Path,
    assets_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence-context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    if set(plans) != set(range(1, expected_question_count + 1)):
        raise ValueError("plans must cover the expected question population")
    assets = {str(row["internal_table_uid"]): row for row in _read_jsonl(assets_path)}
    tables = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(evidence_context_path)}
    audit: list[dict[str, Any]] = []
    connection = sqlite3.connect(lexical_index_path)
    try:
        connection.execute("PRAGMA query_only=ON")
        for question_id in range(1, expected_question_count + 1):
            source_plan = plans[question_id]
            candidate = _eligible(source_plan)
            if candidate is None:
                continue
            operation, direction, metric = candidate
            entity = str(source_plan["entities"][0])
            years = [int(value) for value in source_plan["years"]]
            scope = source_plan.get("scope")
            operand_records: list[dict[str, Any]] = []
            for position, year in enumerate(years):
                operand = _typed_operand(
                    {
                        "operand_id": f"x{position}",
                        "role": "temporal_value",
                        "metric": metric,
                        "period": year,
                        "ticker": entity,
                    },
                    default_ticker=None,
                    default_scope=scope,
                    requested_unit=source_plan.get("requested_unit"),
                )
                operand_plan = {
                    "question_id": question_id,
                    "years": [year],
                    "operands": [operand],
                }
                tables_found = _search_operand_year(
                    index_path=lexical_index_path,
                    precision_query=metric_core_query(metric, ticker=entity),
                    recall_query=metric,
                    ticker=entity,
                    year=year,
                    scope=scope,
                    top_k=10,
                    match_mode="any",
                    connection=connection,
                )
                failed: Counter[str] = Counter()
                strict_count = 0
                for table_candidate in tables_found:
                    asset = assets.get(str(table_candidate["internal_table_uid"]))
                    if asset is None:
                        continue
                    for row in _row_candidates(asset, table_candidate["retrieval_query"], maximum=3):
                        review_row = {
                            "question_id": question_id,
                            "ticker": entity,
                            "report_year": year,
                            "observed_scope": table_candidate.get("scope"),
                            "internal_table_uid": table_candidate["internal_table_uid"],
                            "row_index": row["row_index"],
                            "row_rank": row["row_rank"],
                            "row_label": row["row_label"],
                            "row_token_jaccard": row["row_token_jaccard"],
                            "numeric_cell_indices": row["numeric_cell_indices"],
                            "review_packet_id": "value_blind_probe",
                        }
                        strict_candidate, checks = _candidate(
                            row=review_row,
                            plan=operand_plan,
                            table=tables.get(str(table_candidate["internal_table_uid"])),
                            context=contexts.get(str(table_candidate["internal_table_uid"])),
                            minimum_row_jaccard=minimum_row_jaccard,
                        )
                        if strict_candidate is not None:
                            strict_count += 1
                        failed.update(name for name, passed in checks.items() if passed is False)
                operand_records.append(
                    {
                        "operand_id": operand["operand_id"],
                        "year": year,
                        "table_candidate_count": len(tables_found),
                        "strict_source_candidate_count": strict_count,
                        "failed_source_gate_counts": dict(sorted(failed.items())),
                    }
                )
            all_ready = all(item["strict_source_candidate_count"] == 1 for item in operand_records)
            record = {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "proposed_operation": operation,
                "proposed_direction": direction,
                "operand_source_checks": operand_records,
                "quarantine_status": (
                    "ALL_OPERANDS_STRICT_SOURCE_READY_BUT_ROUTE_NOT_MATERIALIZED"
                    if all_ready
                    else "ONE_OR_MORE_OPERANDS_STRICT_SOURCE_GATES_INCOMPLETE"
                ),
                "raw_numeric_values_included": False,
                "source_contract": dict(CONTRACT),
            }
            if _contains_forbidden(record):
                raise ValueError("simple temporal source probe leaked an unsafe field")
            audit.append(record)
    finally:
        connection.close()
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(audit),
        "status_counts": dict(sorted(Counter(row["quarantine_status"] for row in audit).items())),
        "operation_counts": dict(sorted(Counter(row["proposed_operation"] for row in audit).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "simple_temporal_operator_source_probe_v1.jsonl"
        summary_path = temporary / "simple_temporal_operator_source_probe_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "plans": plans_path,
            "lexical_index": lexical_index_path,
            "assets": assets_path,
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


def validate_simple_temporal_operator_source_probe(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected simple-temporal source-probe protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("simple-temporal source-probe hash mismatch")
    audit = _read_jsonl(artifact_dir / "simple_temporal_operator_source_probe_v1.jsonl")
    if not audit or any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        or not isinstance(row.get("operand_source_checks"), list)
        or len(row["operand_source_checks"]) != 2
        for row in audit
    ):
        raise ValueError("simple-temporal source probe lost value-blind boundary")
    summary = _read_json(artifact_dir / "simple_temporal_operator_source_probe_summary_v1.json")
    if summary.get("target_question_count") != len(audit):
        raise ValueError("simple-temporal source-probe summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "target_question_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
