#!/usr/bin/env python3
"""Run direct replay and independent source critique for staged Qwen output.

This command accepts only a completed *production* Qwen staged-review run.
Dry-runs and test decision fixtures remain useful diagnostics, but cannot be
promoted.  The audit itself is source-only: it recreates each allowed packet
from bundle sidecars, reopens selected V2/V3 cells, then recomputes every
stage from independent source candidates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.llm_staged_audit import (  # noqa: E402
    LLM_STAGED_AUDIT_PROTOCOL,
    LLM_STAGED_AUDIT_SCHEMA_VERSION,
    direct_replay_selected_stage,
    execution_matches,
    independently_execute_packet_stage,
)
from finance_query.llm_stage_execution import execute_gross_profit_margin_change_rank  # noqa: E402
from finance_query.llm_table_reviewer import build_review_packet  # noqa: E402
from finance_query.report_normalization import route_stage_candidates  # noqa: E402
from finance_query.table_structure import sha256_file  # noqa: E402


Q368_FAMILY = "quick_ratio_median_then_net_profit_margin"
Q369_FAMILY = "quick_ratio_gpm_interest_coverage_selection"
SUPPORTED_FAMILIES = {Q368_FAMILY, Q369_FAMILY}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--qwen-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question-id", type=int, action="append", default=[])
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _abstention(question_id: int, reason_code: str, **details: Any) -> dict[str, Any]:
    return {
        "schema_version": LLM_STAGED_AUDIT_SCHEMA_VERSION,
        "protocol": LLM_STAGED_AUDIT_PROTOCOL,
        "id": question_id,
        "annotation_status": "needs_human",
        "provenance_status": "machine_abstained",
        "reason_codes": [reason_code],
        "feedback": details,
        "direct_replay_gate": {"status": "not_run"},
        "independent_critic_gate": {"status": "not_run", "reviewer_inputs_used": []},
        "human_verified": False,
        "training_eligible": False,
        "submission_eligible": False,
    }


def _stage_record_index(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        if str(row.get("record_type") or "") == "deterministic_final_result":
            continue
        stage_id = str(row.get("stage_id") or "")
        if not stage_id:
            continue
        key = (int(row["id"]), stage_id)
        if key in result:
            raise ValueError(f"Duplicate Qwen stage record: {key}")
        result[key] = dict(row)
    return result


def _final_record_index(rows: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("record_type") or "") != "deterministic_final_result":
            continue
        key = int(row["id"])
        if key in result:
            raise ValueError(f"Duplicate Qwen final record: Q{key}")
        result[key] = dict(row)
    return result


def _runtime_is_promotable(manifest: Mapping[str, Any]) -> tuple[bool, str | None]:
    if bool(manifest.get("dry_run")):
        return False, "qwen_output_is_dry_run"
    if bool(manifest.get("decision_fixture")):
        return False, "qwen_output_uses_test_decision_fixture"
    declared = set(manifest.get("supported_families") or [])
    if not declared and manifest.get("supported_family"):
        declared = {str(manifest["supported_family"])}
    if not declared or not declared.issubset(SUPPORTED_FAMILIES):
        return False, "unsupported_qwen_runner_protocol"
    return True, None


def _packet(
    *,
    question_id: int,
    question: str,
    route: Mapping[str, Any],
    stage: Mapping[str, Any],
    entities: list[str],
    catalog_rows: list[dict[str, Any]],
    catalog: Mapping[str, Mapping[str, Any]],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    scope: str | None,
) -> dict[str, Any]:
    candidates = route_stage_candidates(
        catalog_rows,
        stage,
        resolved_entities=entities,
        scope=scope,
    )
    if str(candidates.get("status") or "") != "candidate_tables_found":
        raise ValueError("independent_router_" + str(candidates.get("status") or "unknown"))
    return build_review_packet(
        question_id=question_id,
        question=question,
        stage={**stage, "entities": entities},
        candidate_result=candidates,
        catalog_by_uid=catalog,
        tables_by_uid=tables,
        contexts_by_uid=contexts,
    )


def _checked_source_stage(
    *,
    question_id: int,
    question: str,
    route: Mapping[str, Any],
    stage: Mapping[str, Any],
    entities: list[str],
    scope: str | None,
    stage_records: Mapping[tuple[int, str], Mapping[str, Any]],
    catalog_rows: list[dict[str, Any]],
    catalog: Mapping[str, Mapping[str, Any]],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Rebuild one packet and check it without trusting Qwen's packet file."""
    stage_id = str(stage.get("stage_id") or "")
    try:
        packet = _packet(
            question_id=question_id,
            question=question,
            route=route,
            stage=stage,
            entities=entities,
            catalog_rows=catalog_rows,
            catalog=catalog,
            tables=tables,
            contexts=contexts,
            scope=scope,
        )
    except ValueError as error:
        return {"ok": False, "reason": "independent_packet_build_failed", "detail": str(error)}
    review = stage_records.get((question_id, stage_id))
    if review is None:
        return {
            "ok": False,
            "reason": "qwen_stage_record_missing",
            "detail": stage_id,
            "packet": packet,
        }
    direct = direct_replay_selected_stage(packet, review, tables, contexts)
    independent = independently_execute_packet_stage(
        packet, {**stage, "entities": entities}, tables, contexts
    )
    qwen_execution = dict(review.get("deterministic_stage_execution") or {})
    independent_execution = dict(independent.get("deterministic_stage_execution") or {})
    ok = (
        direct.get("status") == "direct_replay_ready"
        and independent.get("status") == "independent_critic_ready"
        and execution_matches(qwen_execution, independent_execution)
    )
    return {
        "ok": ok,
        "reason": None if ok else "source_stage_replay_or_critic_failed",
        "packet": packet,
        "review": dict(review),
        "direct": direct,
        "independent": independent,
        "qwen_execution": qwen_execution,
        "independent_execution": independent_execution,
    }


def _packet_scope(packet: Mapping[str, Any]) -> str | None:
    contract = dict(packet.get("scope_contract") or {})
    scopes = [str(value) for value in contract.get("viable_report_scopes") or [] if str(value)]
    if str(contract.get("status") or "") != "resolved" or len(scopes) != 1:
        return None
    return scopes[0]


def _audit_q369(
    *,
    route_row: Mapping[str, Any],
    question: str,
    stage_records: Mapping[tuple[int, str], Mapping[str, Any]],
    final_records: Mapping[int, Mapping[str, Any]],
    catalog_rows: list[dict[str, Any]],
    catalog: Mapping[str, Mapping[str, Any]],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit the Q369 five-stage source-review and deterministic transition."""
    question_id = int(route_row["id"])
    route = dict(route_row.get("route") or {})
    stages = [dict(stage) for stage in route.get("stages") or []]
    expected_stage_ids = [
        "quick_ratio_screen",
        "gross_profit_margin_old",
        "gross_profit_margin_new",
        "gross_profit_margin_change_rank",
        "interest_coverage_lookup",
    ]
    if [str(stage.get("stage_id") or "") for stage in stages] != expected_stage_ids:
        return _abstention(
            question_id,
            "unsupported_q369_stage_contract",
            actual_stage_ids=[str(stage.get("stage_id") or "") for stage in stages],
        )
    quick, gpm_old, gpm_new, transition, interest = stages
    entities = [str(value) for value in quick.get("entities") or []]
    if not entities:
        return _abstention(question_id, "initial_stage_entities_missing")

    source_gates: dict[str, Any] = {}
    critic_gates: dict[str, Any] = {}

    first = _checked_source_stage(
        question_id=question_id,
        question=question,
        route=route,
        stage=quick,
        entities=entities,
        scope=route.get("scope"),
        stage_records=stage_records,
        catalog_rows=catalog_rows,
        catalog=catalog,
        tables=tables,
        contexts=contexts,
    )
    source_gates["quick_ratio_screen"] = first.get("direct", {"status": "not_run"})
    critic_gates["quick_ratio_screen"] = first.get("independent", {"status": "not_run", "reviewer_inputs_used": []})
    if not first["ok"]:
        return {
            **_abstention(question_id, str(first["reason"]), detail=first.get("detail")),
            "direct_replay_gate": source_gates,
            "independent_critic_gate": critic_gates,
        }
    scope = str(route.get("scope") or _packet_scope(first["packet"]) or "")
    if not scope:
        return {
            **_abstention(question_id, "independent_first_stage_scope_not_resolved"),
            "direct_replay_gate": source_gates,
            "independent_critic_gate": critic_gates,
        }
    eligible = list(first["independent_execution"].get("eligible_entities") or [])
    if not eligible:
        return {
            **_abstention(question_id, "independent_first_stage_no_eligible_entity"),
            "direct_replay_gate": source_gates,
            "independent_critic_gate": critic_gates,
        }

    state: dict[str, Any] = {str(quick["stage_id"]): first["independent_execution"]}
    margin_stages: list[tuple[str, Mapping[str, Any]]] = [
        ("gross_profit_margin_old", gpm_old),
        ("gross_profit_margin_new", gpm_new),
    ]
    for gate_name, stage in margin_stages:
        checked = _checked_source_stage(
            question_id=question_id,
            question=question,
            route=route,
            stage=stage,
            entities=eligible,
            scope=scope,
            stage_records=stage_records,
            catalog_rows=catalog_rows,
            catalog=catalog,
            tables=tables,
            contexts=contexts,
        )
        source_gates[gate_name] = checked.get("direct", {"status": "not_run"})
        critic_gates[gate_name] = checked.get(
            "independent", {"status": "not_run", "reviewer_inputs_used": []}
        )
        if not checked["ok"]:
            return {
                **_abstention(question_id, str(checked["reason"]), detail=checked.get("detail")),
                "direct_replay_gate": source_gates,
                "independent_critic_gate": critic_gates,
            }
        if _packet_scope(checked["packet"]) != scope:
            return {
                **_abstention(question_id, "independent_packet_scope_changed_between_stages"),
                "direct_replay_gate": source_gates,
                "independent_critic_gate": critic_gates,
            }
        state[str(stage["stage_id"])] = checked["independent_execution"]

    independent_transition = execute_gross_profit_margin_change_rank(transition, state)
    transition_record = stage_records.get((question_id, str(transition.get("stage_id") or "")))
    transition_ok = (
        str(independent_transition.get("status") or "") == "stage_complete"
        and transition_record is not None
        and execution_matches(
            dict(transition_record.get("deterministic_stage_execution") or {}),
            independent_transition,
        )
    )
    source_gates["gross_profit_margin_change_rank"] = {
        "status": "source_free_deterministic_transition",
        "reviewer_inputs_used": [],
    }
    critic_gates["gross_profit_margin_change_rank"] = {
        "status": "independent_transition_ready" if transition_ok else "independent_transition_blocked",
        "reviewer_inputs_used": [],
        "deterministic_stage_execution": independent_transition,
    }
    if not transition_ok:
        return {
            **_abstention(question_id, "gross_profit_margin_transition_critic_failed"),
            "direct_replay_gate": source_gates,
            "independent_critic_gate": critic_gates,
        }
    winner = str(independent_transition.get("winning_entity") or "")
    if not winner:
        return {
            **_abstention(question_id, "independent_transition_winner_missing"),
            "direct_replay_gate": source_gates,
            "independent_critic_gate": critic_gates,
        }

    final_stage = _checked_source_stage(
        question_id=question_id,
        question=question,
        route=route,
        stage=interest,
        entities=[winner],
        scope=scope,
        stage_records=stage_records,
        catalog_rows=catalog_rows,
        catalog=catalog,
        tables=tables,
        contexts=contexts,
    )
    source_gates["interest_coverage_lookup"] = final_stage.get("direct", {"status": "not_run"})
    critic_gates["interest_coverage_lookup"] = final_stage.get(
        "independent", {"status": "not_run", "reviewer_inputs_used": []}
    )
    if not final_stage["ok"] or _packet_scope(final_stage.get("packet") or {}) != scope:
        return {
            **_abstention(
                question_id,
                str(final_stage.get("reason") or "independent_packet_scope_changed_between_stages"),
                detail=final_stage.get("detail"),
            ),
            "direct_replay_gate": source_gates,
            "independent_critic_gate": critic_gates,
        }
    final_execution = final_stage["independent_execution"]
    final = final_records.get(question_id)
    if not (
        final
        and str(final.get("result_value") or "") == str(final_execution.get("aggregate_value") or "")
        and str(final.get("result_unit") or "") == str(final_execution.get("result_unit") or "")
    ):
        return {
            **_abstention(question_id, "deterministic_final_result_does_not_match_independent_execution"),
            "direct_replay_gate": source_gates,
            "independent_critic_gate": critic_gates,
        }
    return {
        "schema_version": LLM_STAGED_AUDIT_SCHEMA_VERSION,
        "protocol": LLM_STAGED_AUDIT_PROTOCOL,
        "id": question_id,
        "annotation_status": "machine_calibrated",
        "provenance_status": "machine_calibrated",
        "reason_codes": [
            "qwen_selected_cells_replayed_against_v2_v3",
            "independent_source_stage_execution_matches_qwen_execution",
            "gross_profit_margin_transition_independently_recomputed",
        ],
        "result_value": final_execution["aggregate_value"],
        "result_unit": final_execution["result_unit"],
        "direct_replay_gate": source_gates,
        "independent_critic_gate": critic_gates,
        "human_verified": False,
        "training_eligible": False,
        "submission_eligible": False,
    }


def audit_route(
    *,
    route_row: Mapping[str, Any],
    question: str,
    stage_records: Mapping[tuple[int, str], Mapping[str, Any]],
    final_records: Mapping[int, Mapping[str, Any]],
    catalog_rows: list[dict[str, Any]],
    catalog: Mapping[str, Mapping[str, Any]],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    runtime_promotable: bool,
    runtime_blocker: str | None,
) -> dict[str, Any]:
    question_id = int(route_row["id"])
    route = dict(route_row.get("route") or {})
    if not runtime_promotable:
        return _abstention(question_id, runtime_blocker or "qwen_runtime_not_promotable")
    if str(route.get("routing_status") or "") != "planned":
        return _abstention(question_id, "route_not_planned")
    family = str(route.get("family") or "")
    if family not in SUPPORTED_FAMILIES:
        return _abstention(
            question_id,
            "unsupported_staged_review_family",
            family=family,
            supported_families=sorted(SUPPORTED_FAMILIES),
        )
    if family == Q369_FAMILY:
        return _audit_q369(
            route_row=route_row,
            question=question,
            stage_records=stage_records,
            final_records=final_records,
            catalog_rows=catalog_rows,
            catalog=catalog,
            tables=tables,
            contexts=contexts,
        )
    stages = list(route.get("stages") or [])
    if len(stages) != 2:
        return _abstention(question_id, "unsupported_stage_count", stage_count=len(stages))
    stage_a, stage_b = (dict(stages[0]), dict(stages[1]))
    entities_a = [str(value) for value in stage_a.get("entities") or []]
    if not entities_a:
        return _abstention(question_id, "initial_stage_entities_missing")
    try:
        packet_a = _packet(
            question_id=question_id,
            question=question,
            route=route,
            stage=stage_a,
            entities=entities_a,
            catalog_rows=catalog_rows,
            catalog=catalog,
            tables=tables,
            contexts=contexts,
            scope=route.get("scope"),
        )
    except ValueError as error:
        return _abstention(question_id, "independent_packet_build_failed", detail=str(error))
    review_a = stage_records.get((question_id, str(stage_a.get("stage_id") or "")))
    if review_a is None:
        return _abstention(question_id, "qwen_first_stage_record_missing")
    direct_a = direct_replay_selected_stage(packet_a, review_a, tables, contexts)
    independent_a = independently_execute_packet_stage(
        packet_a, {**stage_a, "entities": entities_a}, tables, contexts
    )
    qwen_execution_a = dict(review_a.get("deterministic_stage_execution") or {})
    if (
        direct_a.get("status") != "direct_replay_ready"
        or independent_a.get("status") != "independent_critic_ready"
        or not execution_matches(qwen_execution_a, independent_a.get("deterministic_stage_execution") or {})
    ):
        return {
            **_abstention(question_id, "first_stage_replay_or_critic_failed"),
            "direct_replay_gate": direct_a,
            "independent_critic_gate": independent_a,
        }
    scope_a = str(route.get("scope") or _packet_scope(packet_a) or "")
    if not scope_a:
        return {
            **_abstention(question_id, "independent_first_stage_scope_not_resolved"),
            "direct_replay_gate": direct_a,
            "independent_critic_gate": independent_a,
        }
    eligible = list((independent_a.get("deterministic_stage_execution") or {}).get("eligible_entities") or [])
    if not eligible:
        return {
            **_abstention(question_id, "independent_first_stage_no_eligible_entity"),
            "direct_replay_gate": direct_a,
            "independent_critic_gate": independent_a,
        }
    try:
        packet_b = _packet(
            question_id=question_id,
            question=question,
            route=route,
            stage=stage_b,
            entities=eligible,
            catalog_rows=catalog_rows,
            catalog=catalog,
            tables=tables,
            contexts=contexts,
            scope=scope_a,
        )
    except ValueError as error:
        return {
            **_abstention(question_id, "independent_second_stage_packet_build_failed", detail=str(error)),
            "direct_replay_gate": direct_a,
            "independent_critic_gate": independent_a,
        }
    review_b = stage_records.get((question_id, str(stage_b.get("stage_id") or "")))
    if review_b is None:
        return _abstention(question_id, "qwen_second_stage_record_missing")
    direct_b = direct_replay_selected_stage(packet_b, review_b, tables, contexts)
    independent_b = independently_execute_packet_stage(
        packet_b, {**stage_b, "entities": eligible}, tables, contexts
    )
    qwen_execution_b = dict(review_b.get("deterministic_stage_execution") or {})
    final = final_records.get(question_id)
    independent_execution_b = dict(independent_b.get("deterministic_stage_execution") or {})
    final_matches = bool(
        final
        and str(final.get("result_value") or "") == str(independent_execution_b.get("aggregate_value") or "")
        and str(final.get("result_unit") or "") == str(independent_execution_b.get("result_unit") or "")
    )
    if (
        direct_b.get("status") != "direct_replay_ready"
        or independent_b.get("status") != "independent_critic_ready"
        or _packet_scope(packet_b) != scope_a
        or not execution_matches(qwen_execution_b, independent_execution_b)
        or not final_matches
    ):
        return {
            **_abstention(question_id, "second_stage_replay_or_critic_failed"),
            "direct_replay_gate": {"stage_1": direct_a, "stage_2": direct_b},
            "independent_critic_gate": {"stage_1": independent_a, "stage_2": independent_b},
        }
    return {
        "schema_version": LLM_STAGED_AUDIT_SCHEMA_VERSION,
        "protocol": LLM_STAGED_AUDIT_PROTOCOL,
        "id": question_id,
        "annotation_status": "machine_calibrated",
        "provenance_status": "machine_calibrated",
        "reason_codes": [
            "qwen_selected_cells_replayed_against_v2_v3",
            "independent_source_stage_execution_matches_qwen_execution",
        ],
        "result_value": independent_execution_b["aggregate_value"],
        "result_unit": independent_execution_b["result_unit"],
        "direct_replay_gate": {"stage_1": direct_a, "stage_2": direct_b},
        "independent_critic_gate": {"stage_1": independent_a, "stage_2": independent_b},
        "human_verified": False,
        "training_eligible": False,
        "submission_eligible": False,
    }


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    qwen_path = args.qwen_output.resolve()
    qwen_manifest_path = qwen_path.with_suffix(".manifest.json")
    if not qwen_manifest_path.is_file():
        raise FileNotFoundError(f"Missing Qwen manifest: {qwen_manifest_path}")
    qwen_manifest = json.loads(qwen_manifest_path.read_text(encoding="utf-8"))
    promotable, blocker = _runtime_is_promotable(qwen_manifest)
    catalog_rows = load_jsonl(bundle / "table_routing_catalog_v1.jsonl")
    catalog = {str(row["internal_table_uid"]): row for row in catalog_rows}
    tables = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables_structured_v2.jsonl")
    }
    contexts = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables_evidence_context_v3.jsonl")
    }
    questions = {int(row["id"]): str(row.get("question") or "") for row in load_jsonl(bundle / "review_items.jsonl")}
    routes = load_jsonl(args.routes.resolve())
    qwen_rows = load_jsonl(qwen_path)
    stage_records = _stage_record_index(qwen_rows)
    final_records = _final_record_index(qwen_rows)
    selected = set(args.question_id)
    results = [
        audit_route(
            route_row=row,
            question=questions[int(row["id"])],
            stage_records=stage_records,
            final_records=final_records,
            catalog_rows=catalog_rows,
            catalog=catalog,
            tables=tables,
            contexts=contexts,
            runtime_promotable=promotable,
            runtime_blocker=blocker,
        )
        for row in routes
        if not selected or int(row["id"]) in selected
    ]
    output = args.output.resolve()
    atomic_write_jsonl(output, results)
    manifest = {
        "schema_version": LLM_STAGED_AUDIT_SCHEMA_VERSION,
        "protocol": LLM_STAGED_AUDIT_PROTOCOL,
        "question_count": len(results),
        "status_counts": dict(sorted(Counter(str(row["annotation_status"]) for row in results).items())),
        "qwen_output_sha256": sha256_file(qwen_path),
        "qwen_manifest_sha256": sha256_file(qwen_manifest_path),
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "table_routing_catalog_sha256": sha256_file(bundle / "table_routing_catalog_v1.jsonl"),
        "routes_sha256": sha256_file(args.routes.resolve()),
        "runtime_promotable": promotable,
        "runtime_blocker": blocker,
        "training_eligible": False,
        "submission_eligible": False,
        "sidecar_sha256": sha256_file(output),
    }
    atomic_write_json(output.with_suffix(".manifest.json"), manifest)
    print(json.dumps({"output": str(output), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
