"""Narrowly reclassify disclosed report-row questions before source retrieval.

This is a research-only planning revision.  It only changes a question whose
own typed plan already resolves one issuer and whose wording matches a small,
explicit disclosed-row form (ownership/voting ratio, a reported total, or a
foreign-exchange row).  It never selects a table, row, column, or value.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.question_compiler import build_typed_operand_plan
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_reported_row_plan_reclassification_v1"
TARGET_BRIDGE_PROTOCOL = "vifinqa_direct_lookup_pre_materialization_target_v1"
EXPECTED_ROUTE_PROTOCOL = "route_completeness_overlay_v3"
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
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "rows",
        "raw_source_row",
        "raw_source_cell",
        "source_label",
        "human_verified",
    }
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


def _json_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_manifest_output(manifest_path: Path, key: str, path: Path) -> None:
    manifest = _read_json(manifest_path)
    expected = ((manifest.get("outputs") or {}).get(key) or {}).get("sha256")
    # The original typed-plan sidecar predates the generic output-descriptor
    # convention and records its file digest at the manifest root.
    if key == "sidecar" and not expected:
        expected = manifest.get("sidecar_sha256")
    if not expected or sha256_file(path) != expected:
        raise ValueError(f"{path} does not match {manifest_path} output {key}")


def _is_new_reported_row_direct_lookup(base: Mapping[str, Any], revised: Mapping[str, Any]) -> bool:
    operand_list = revised.get("operands") or []
    return bool(
        base.get("decomposition_status") == "abstain"
        and revised.get("decomposition_status") == "complete"
        and revised.get("effective_family") == "direct_lookup"
        and revised.get("route") == "reported_value_lookup"
        and isinstance(revised.get("operation_ast"), Mapping)
        and revised["operation_ast"] == {"op": "lookup", "args": ["x0"]}
        and len(operand_list) == 1
        and len(revised.get("entities") or []) == 1
        and len(revised.get("years") or []) == 1
    )


def _pre_materialization_route(base: Mapping[str, Any], revised_plan: Mapping[str, Any]) -> dict[str, Any]:
    operand = (revised_plan.get("operands") or [None])[0]
    if not isinstance(operand, Mapping):
        raise ValueError("reported-row plan lost its only operand")
    output = deepcopy(dict(base))
    output.update(
        {
            "protocol": EXPECTED_ROUTE_PROTOCOL,
            "route_status": "route_incomplete",
            "covered_operations": [],
            "required_operations": ["reported_value"],
            "missing_operations": ["reported_value"],
            "reason_codes": ["REPORTED_ROW_RECLASSIFICATION_REQUIRES_STRICT_SOURCE_RECHECK"],
            "question_context": {
                "entities": list(revised_plan.get("entities") or []),
                "scope": operand.get("scope"),
                "source": "typed_plan_reported_row_reclassification",
                "years": list(revised_plan.get("years") or []),
            },
            "machine_plan_fingerprint": revised_plan.get("plan_fingerprint"),
            "source_contract": dict(CONTRACT),
        }
    )
    return output


def build_reported_row_plan_reclassification(
    *,
    base_plans_path: Path,
    base_plans_manifest_path: Path,
    review_items_path: Path,
    entity_aliases_path: Path,
    base_route_overlay_path: Path,
    base_route_overlay_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Produce an immutable planning revision plus strict-source target bridge."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_manifest_output(base_plans_manifest_path, "sidecar", base_plans_path)
    _require_manifest_output(base_route_overlay_manifest_path, "overlay", base_route_overlay_path)

    base_plans = {int(row["question_id"]): row for row in _read_jsonl(base_plans_path)}
    review_items = {int(row["id"]): row for row in _read_jsonl(review_items_path)}
    aliases = _read_jsonl(entity_aliases_path)
    base_routes = {int(row["question_id"]): row for row in _read_jsonl(base_route_overlay_path)}
    expected_ids = set(range(1, expected_question_count + 1))
    if set(base_plans) != expected_ids or set(review_items) != expected_ids or set(base_routes) != expected_ids:
        raise ValueError("planning reclassification inputs must cover every question exactly once")
    if any(_contains_forbidden(row) for row in aliases):
        raise ValueError("entity alias sidecar must not contain review/value fields")

    revised_plans: dict[int, dict[str, Any]] = {}
    revised_routes: dict[int, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    bridge: list[dict[str, Any]] = []
    for question_id in range(1, expected_question_count + 1):
        base = base_plans[question_id]
        recomputed = build_typed_operand_plan(review_items[question_id], report_entity_aliases=aliases)
        eligible = _is_new_reported_row_direct_lookup(base, recomputed)
        if eligible:
            revised_plans[question_id] = recomputed
            revised_routes[question_id] = _pre_materialization_route(base_routes[question_id], recomputed)
            bridge.append(
                {
                    "schema_version": 1,
                    "protocol": TARGET_BRIDGE_PROTOCOL,
                    "question_id": question_id,
                    "primary_blocker": "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E",
                    "reason_code": "REPORTED_ROW_PLAN_RECLASSIFIED_REQUIRES_STRICT_SOURCE_RECHECK",
                    "source_contract": dict(CONTRACT),
                }
            )
        else:
            # The baseline plan snapshot is immutable.  Runtime planner code
            # may have acquired unrelated hardening since that snapshot (for
            # example a stricter unit-contract detail), but this revision must
            # not silently absorb it.  Retain the original row byte-for-byte
            # unless this exact disclosed-row reclassification applies.
            revised_plans[question_id] = base
            revised_routes[question_id] = base_routes[question_id]
        audit_row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "reclassification_status": (
                "RECLASSIFIED_REPORTED_ROW_DIRECT_LOOKUP" if eligible else "UNCHANGED"
            ),
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(audit_row):
            raise ValueError("planning reclassification audit contains an unsafe field")
        audit.append(audit_row)

    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": expected_question_count,
        "reclassified_question_count": len(bridge),
        "status_counts": dict(sorted(Counter(row["reclassification_status"] for row in audit).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        plans_path = temporary / "typed_operand_plans.jsonl"
        routes_path = temporary / "route_completeness_overlay_v3.jsonl"
        bridge_path = temporary / "direct_lookup_target_bridge_v1.jsonl"
        audit_path = temporary / "reported_row_reclassification_audit_v1.jsonl"
        summary_path = temporary / "reported_row_reclassification_summary_v1.json"
        _write_jsonl(plans_path, (revised_plans[question_id] for question_id in range(1, expected_question_count + 1)))
        _write_jsonl(routes_path, (revised_routes[question_id] for question_id in range(1, expected_question_count + 1)))
        _write_jsonl(bridge_path, bridge)
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "base_plans": base_plans_path,
            "base_plans_manifest": base_plans_manifest_path,
            "review_items": review_items_path,
            "entity_aliases": entity_aliases_path,
            "base_route_overlay": base_route_overlay_path,
            "base_route_overlay_manifest": base_route_overlay_manifest_path,
        }
        common = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
            "source_contract": dict(CONTRACT),
        }
        _write_json(
            temporary / "typed_operand_plans.manifest.json",
            {
                **common,
                "outputs": {"sidecar": {"path": plans_path.name, "sha256": sha256_file(plans_path)}},
            },
        )
        _write_json(
            temporary / "route_completeness_overlay_v3.manifest.json",
            {
                **common,
                "outputs": {"overlay": {"path": routes_path.name, "sha256": sha256_file(routes_path)}},
            },
        )
        _write_json(
            temporary / "manifest.json",
            {
                **common,
                "outputs": {
                    "plans": {"path": plans_path.name, "sha256": sha256_file(plans_path)},
                    "route_overlay": {"path": routes_path.name, "sha256": sha256_file(routes_path)},
                    "target_bridge": {"path": bridge_path.name, "sha256": sha256_file(bridge_path)},
                    "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                    "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_reported_row_plan_reclassification(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Verify full coverage and that only the bounded direct-row shape changed."""
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected reported-row reclassification protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("reported-row reclassification hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(artifact_dir / "typed_operand_plans.jsonl")}
    routes = {int(row["question_id"]): row for row in _read_jsonl(artifact_dir / "route_completeness_overlay_v3.jsonl")}
    bridge = _read_jsonl(artifact_dir / "direct_lookup_target_bridge_v1.jsonl")
    audit = _read_jsonl(artifact_dir / "reported_row_reclassification_audit_v1.jsonl")
    expected_ids = set(range(1, expected_question_count + 1))
    if set(plans) != expected_ids or set(routes) != expected_ids or {int(row["question_id"]) for row in audit} != expected_ids:
        raise ValueError("reported-row reclassification lost all-question coverage")
    target_ids = {int(row["question_id"]) for row in bridge}
    if len(target_ids) != len(bridge) or any(
        row.get("protocol") != TARGET_BRIDGE_PROTOCOL
        or row.get("primary_blocker") != "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"
        or row.get("source_contract") != CONTRACT
        or _contains_forbidden(row)
        for row in bridge
    ):
        raise ValueError("invalid direct-lookup target bridge")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        for row in audit
    ):
        raise ValueError("reported-row reclassification audit lost value-blind boundary")
    base_plans_path = Path(str(((manifest.get("inputs") or {}).get("base_plans") or {}).get("path") or ""))
    base_routes_path = Path(str(((manifest.get("inputs") or {}).get("base_route_overlay") or {}).get("path") or ""))
    base_plans = {int(row["question_id"]): row for row in _read_jsonl(base_plans_path)}
    base_routes = {int(row["question_id"]): row for row in _read_jsonl(base_routes_path)}
    for question_id in expected_ids - target_ids:
        if _json_hash(plans[question_id]) != _json_hash(base_plans[question_id]) or _json_hash(routes[question_id]) != _json_hash(base_routes[question_id]):
            raise ValueError("non-target plan or route changed")
    for question_id in target_ids:
        if not _is_new_reported_row_direct_lookup(base_plans[question_id], plans[question_id]):
            raise ValueError("target lost bounded reported-row direct-lookup shape")
        route = routes[question_id]
        if not (
            route.get("route_status") == "route_incomplete"
            and route.get("required_operations") == ["reported_value"]
            and route.get("missing_operations") == ["reported_value"]
            and route.get("source_contract") == CONTRACT
        ):
            raise ValueError("target route is not ready for strict direct-source recheck")
    summary = _read_json(artifact_dir / "reported_row_reclassification_summary_v1.json")
    if summary.get("reclassified_question_count") != len(target_ids):
        raise ValueError("reported-row reclassification summary mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "reclassified_question_count": len(target_ids),
        "answer_eligible": False,
        "submission_eligible": False,
    }
