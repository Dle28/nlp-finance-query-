"""Materialize only source-backed explicit-ticker direct lookup plans.

The runtime compiler may see a ticker such as ``PC1`` or ``HT1`` directly in
the question while an older plan snapshot has no entity.  This overlay keeps
the snapshot immutable except for that narrow, fully typed shape.  It does not
retrieve a table, choose a cell, calculate an answer, or change E2E release
eligibility.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.question_compiler import build_typed_operand_plan
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_explicit_ticker_plan_recheck_v1"
TARGET_PROTOCOL = "vifinqa_explicit_ticker_direct_lookup_target_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset(
    {
        "answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query",
        "rows", "raw_source_row", "raw_source_cell", "source_label", "human_verified",
    }
)


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


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_sidecar(path: Path, manifest_path: Path) -> None:
    manifest = _read_json(manifest_path)
    expected = ((manifest.get("outputs") or {}).get("sidecar") or {}).get("sha256") or manifest.get("sidecar_sha256")
    if not expected or sha256_file(path) != expected:
        raise ValueError("base typed plans do not match their manifest")


def _is_target(base: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    operands = current.get("operands") or []
    return bool(
        base.get("decomposition_status") == "abstain"
        and current.get("decomposition_status") == "complete"
        and current.get("effective_family") == "direct_lookup"
        and current.get("route") == "existing_typed_plan"
        and "EXPLICIT_SOURCE_TICKER_RESOLVED" in set(current.get("reason_codes") or [])
        and isinstance(current.get("operation_ast"), Mapping)
        and current.get("operation_ast") == {"op": "lookup", "args": ["x0"]}
        and len(operands) == 1
        and len(current.get("entities") or []) == 1
        and len(current.get("years") or []) == 1
    )


def build_explicit_ticker_plan_recheck(
    *,
    base_plans_path: Path,
    base_plans_manifest_path: Path,
    review_items_path: Path,
    entity_aliases_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_sidecar(base_plans_path, base_plans_manifest_path)
    base = {int(row["question_id"]): row for row in _read_jsonl(base_plans_path)}
    items = {int(row["id"]): row for row in _read_jsonl(review_items_path)}
    aliases = _read_jsonl(entity_aliases_path)
    expected_ids = set(range(1, expected_question_count + 1))
    if set(base) != expected_ids or set(items) != expected_ids:
        raise ValueError("plan recheck inputs must cover every question")
    if any(_contains_forbidden(row) for row in aliases):
        raise ValueError("entity aliases must remain non-numeric metadata")

    revised: dict[int, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for question_id in range(1, expected_question_count + 1):
        current = build_typed_operand_plan(items[question_id], report_entity_aliases=aliases)
        targeted = _is_target(base[question_id], current)
        revised[question_id] = current if targeted else base[question_id]
        audit_row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "recheck_status": "EXPLICIT_TICKER_DIRECT_LOOKUP_MATERIALIZED" if targeted else "UNCHANGED",
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(audit_row):
            raise ValueError("ticker plan recheck audit leaked a forbidden field")
        audit.append(audit_row)
        if targeted:
            targets.append(
                {
                    "schema_version": 1,
                    "protocol": TARGET_PROTOCOL,
                    "question_id": question_id,
                    "primary_blocker": "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E",
                    "reason_code": "EXPLICIT_SOURCE_TICKER_PLAN_REQUIRES_STRICT_SOURCE_RECHECK",
                    "source_contract": dict(CONTRACT),
                }
            )
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": expected_question_count,
        "materialized_question_count": len(targets),
        "status_counts": dict(sorted(Counter(row["recheck_status"] for row in audit).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        plans_path = temporary / "typed_operand_plans.jsonl"
        audit_path = temporary / "explicit_ticker_plan_recheck_audit_v1.jsonl"
        targets_path = temporary / "explicit_ticker_direct_lookup_targets_v1.jsonl"
        summary_path = temporary / "explicit_ticker_plan_recheck_summary_v1.json"
        _write_jsonl(plans_path, (revised[question_id] for question_id in range(1, expected_question_count + 1)))
        _write_jsonl(audit_path, audit)
        _write_jsonl(targets_path, targets)
        _write_json(summary_path, summary)
        inputs = {
            "base_plans": base_plans_path,
            "base_plans_manifest": base_plans_manifest_path,
            "review_items": review_items_path,
            "entity_aliases": entity_aliases_path,
            "question_compiler": Path(__file__).resolve().parents[1] / "e2e/question_compiler.py",
        }
        common = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
            "source_contract": dict(CONTRACT),
        }
        _write_json(
            temporary / "manifest.json",
            {
                **common,
                "outputs": {
                    "plans": {"path": plans_path.name, "sha256": sha256_file(plans_path)},
                    "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                    "targets": {"path": targets_path.name, "sha256": sha256_file(targets_path)},
                    "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
            },
        )
        _write_json(
            temporary / "typed_operand_plans.manifest.json",
            {**common, "outputs": {"sidecar": {"path": plans_path.name, "sha256": sha256_file(plans_path)}}},
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_explicit_ticker_plan_recheck(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected explicit ticker plan recheck contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("explicit ticker plan recheck hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(artifact_dir / "typed_operand_plans.jsonl")}
    audit = _read_jsonl(artifact_dir / "explicit_ticker_plan_recheck_audit_v1.jsonl")
    targets = _read_jsonl(artifact_dir / "explicit_ticker_direct_lookup_targets_v1.jsonl")
    expected_ids = set(range(1, expected_question_count + 1))
    if set(plans) != expected_ids or {int(row["question_id"]) for row in audit} != expected_ids:
        raise ValueError("explicit ticker plan recheck lost all-question coverage")
    target_ids = {int(row["question_id"]) for row in targets}
    if len(target_ids) != len(targets) or any(
        row.get("protocol") != TARGET_PROTOCOL
        or row.get("primary_blocker") != "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"
        or row.get("source_contract") != CONTRACT
        or _contains_forbidden(row)
        for row in targets
    ):
        raise ValueError("explicit ticker targets are invalid")
    base_path = Path(str(((manifest.get("inputs") or {}).get("base_plans") or {}).get("path") or ""))
    base = {int(row["question_id"]): row for row in _read_jsonl(base_path)}
    for question_id in expected_ids - target_ids:
        if _hash(plans[question_id]) != _hash(base[question_id]):
            raise ValueError("a non-target plan changed")
    for question_id in target_ids:
        if not _is_target(base[question_id], plans[question_id]):
            raise ValueError("a target lost its explicit ticker direct-lookup shape")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        for row in audit
    ):
        raise ValueError("explicit ticker plan audit lost its boundary")
    summary = _read_json(artifact_dir / "explicit_ticker_plan_recheck_summary_v1.json")
    if summary.get("materialized_question_count") != len(target_ids):
        raise ValueError("explicit ticker plan summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "materialized_question_count": len(target_ids),
        "answer_eligible": False,
        "submission_eligible": False,
    }
