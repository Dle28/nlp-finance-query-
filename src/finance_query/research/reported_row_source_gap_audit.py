"""Record source-backed quarantine reasons for reported-row reclassifications."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.research.full_corpus_direct_lookup_adapter import _candidate
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_reported_row_source_gap_audit_v1"
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


def _quarantine(failed: Counter[str], accepted_count: int) -> tuple[str, str]:
    if accepted_count:
        return "STRICT_SOURCE_CANDIDATE_REQUIRES_SECOND_STAGE_RECHECK", "SOURCE_CANDIDATE_NOT_YET_MATERIALIZED"
    if failed.get("one_exact_v3_year_header", 0):
        return "STRICT_PERIOD_COLUMN_NOT_UNIQUE", "V3_HEADER_DOES_NOT_SELECT_ONE_REQUESTED_YEAR_COLUMN"
    if failed.get("row_score_threshold", 0):
        return "STRICT_ROW_OR_EMBEDDED_SUBJECT_UNRESOLVED", "NO_UNIQUE_ROW_MATCH_FOR_REPORTED_CONCEPT_AND_SUBJECT"
    return "STRICT_SOURCE_GATES_INCOMPLETE", "NO_CANDIDATE_PASSED_ALL_V2_V3_SOURCE_GATES"


def build_reported_row_source_gap_audit(
    *,
    reclassification_dir: Path,
    full_corpus_dir: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
) -> dict[str, Any]:
    """Audit each reclassified candidate without exposing a financial value."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_output(reclassification_dir / "manifest.json", "plans", reclassification_dir / "typed_operand_plans.jsonl")
    _require_output(reclassification_dir / "manifest.json", "target_bridge", reclassification_dir / "direct_lookup_target_bridge_v1.jsonl")
    _require_output(full_corpus_dir / "manifest.json", "row_review_queue_v1.jsonl", full_corpus_dir / "row_review_queue_v1.jsonl")
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence-context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(reclassification_dir / "typed_operand_plans.jsonl")}
    target_ids = [int(row["question_id"]) for row in _read_jsonl(reclassification_dir / "direct_lookup_target_bridge_v1.jsonl")]
    if len(plans) != expected_question_count or len(target_ids) != len(set(target_ids)):
        raise ValueError("invalid reclassification coverage")
    tables = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _read_jsonl(evidence_context_path)}
    review_rows: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(full_corpus_dir / "row_review_queue_v1.jsonl"):
        question_id = int(row["question_id"])
        if question_id in target_ids:
            review_rows[question_id].append(row)

    audit: list[dict[str, Any]] = []
    for question_id in sorted(target_ids):
        failed: Counter[str] = Counter()
        accepted_count = 0
        for row in review_rows[question_id]:
            candidate, checks = _candidate(
                row=row,
                plan=plans[question_id],
                table=tables.get(str(row.get("internal_table_uid") or "")),
                context=contexts.get(str(row.get("internal_table_uid") or "")),
                minimum_row_jaccard=minimum_row_jaccard,
            )
            if candidate is not None:
                accepted_count += 1
            failed.update(name for name, passed in checks.items() if passed is False)
        status, reason = _quarantine(failed, accepted_count)
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "quarantine_status": status,
            "reason_code": reason,
            "review_row_candidate_count": len(review_rows[question_id]),
            "strict_first_stage_candidate_count": accepted_count,
            "failed_source_gate_counts": dict(sorted(failed.items())),
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("reported-row source audit leaked an unsafe field")
        audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(audit),
        "status_counts": dict(sorted(Counter(row["quarantine_status"] for row in audit).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "reported_row_source_gap_audit_v1.jsonl"
        summary_path = temporary / "reported_row_source_gap_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "reclassification_manifest": reclassification_dir / "manifest.json",
            "full_corpus_manifest": full_corpus_dir / "manifest.json",
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


def validate_reported_row_source_gap_audit(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected reported-row source-audit protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("reported-row source-audit hash mismatch")
    audit = _read_jsonl(artifact_dir / "reported_row_source_gap_audit_v1.jsonl")
    if not audit or any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        or int(row.get("strict_first_stage_candidate_count") or 0) != 0
        for row in audit
    ):
        raise ValueError("reported-row source audit lost quarantine boundary")
    summary = _read_json(artifact_dir / "reported_row_source_gap_summary_v1.json")
    if summary.get("target_question_count") != len(audit):
        raise ValueError("reported-row source-audit summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "target_question_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
