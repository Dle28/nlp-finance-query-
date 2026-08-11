#!/usr/bin/env python3
"""Run Qwen evidence review with deterministic multi-stage retrieval hand-off.

Supported full routes are ``quick_ratio_median_then_net_profit_margin`` and
``quick_ratio_gpm_interest_coverage_selection``. Qwen selects literal source
cells for a stage; deterministic code computes every filter, cross-year rank,
and final metric. Nothing written by the model is treated as an answer.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.llm_stage_execution import (  # noqa: E402
    execute_gross_profit_margin_change_rank,
    execute_reviewed_stage,
)
from finance_query.llm_table_reviewer import (  # noqa: E402
    LLM_TABLE_REVIEW_PROTOCOL,
    LLM_TABLE_REVIEW_SCHEMA_VERSION,
    build_review_packet,
    review_prompt,
    self_critique_prompt,
    verify_llm_decision,
)
from finance_query.qwen_inference import QwenGenerator  # noqa: E402
from finance_query.report_normalization import route_stage_candidates  # noqa: E402


DEFAULT_MODEL = "Qwen/Qwen2.5-14B-Instruct"
SUPPORTED_FAMILIES = {
    "quick_ratio_median_then_net_profit_margin",
    "quick_ratio_gpm_interest_coverage_selection",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--question-id", type=int, action="append", default=[])
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--max-tables-per-binding", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=1536)
    parser.add_argument("--no-self-critique", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--trace-output",
        type=Path,
        default=None,
        help=(
            "Optional non-promotable JSONL trace of parsed model decisions, self-critique, "
            "and verifier outcomes for debugging/feedback."
        ),
    )
    parser.add_argument(
        "--decision-fixture",
        type=Path,
        default=None,
        help="Test-only JSONL literal decisions keyed by id/stage_id; never use for production review.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_decision_fixture(path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    records: dict[tuple[int, str], dict[str, Any]] = {}
    for row in load_jsonl(path.resolve()):
        key = (int(row["id"]), str(row["stage_id"]))
        if key in records or not isinstance(row.get("decision"), dict):
            raise ValueError("Fixture records need unique id/stage_id and an object decision")
        records[key] = row
    return records


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def abstention(
    question_id: int,
    stage: dict[str, Any] | None,
    reason_code: str,
    feedback: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": question_id,
        "reviewer_type": "qwen_evidence_bounded_staged",
        "stage_id": None if stage is None else stage.get("stage_id"),
        "annotation_status": "needs_human",
        "provenance_status": "machine_abstained",
        "reason_codes": [reason_code],
        "feedback": feedback,
        "human_verified": False,
        "submission_eligible": False,
    }


def stage_entities(stage: dict[str, Any], state: dict[str, Any]) -> list[str] | None:
    source = stage.get("entity_source")
    if not source:
        return [str(value) for value in stage.get("entities") or []]
    name = str(source).removeprefix("@")
    value = state.get(name)
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return None


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    routes = load_jsonl(args.routes.resolve())
    selected_ids = set(args.question_id)
    catalog_rows = load_jsonl(bundle / "table_routing_catalog_v1.jsonl")
    catalog = {str(row["internal_table_uid"]): row for row in catalog_rows}
    tables = {str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables_structured_v2.jsonl")}
    contexts = {str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables_evidence_context_v3.jsonl")}
    questions = {int(row["id"]): str(row.get("question") or "") for row in load_jsonl(bundle / "review_items.jsonl")}
    fixtures = {} if args.decision_fixture is None else load_decision_fixture(args.decision_fixture)
    generator = None if args.dry_run or fixtures else QwenGenerator(args.model, args.max_new_tokens)
    output: list[dict[str, Any]] = []
    packets: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    processed_questions = 0
    for route_row in routes:
        question_id = int(route_row["id"])
        if selected_ids and question_id not in selected_ids:
            continue
        route = dict(route_row.get("route") or {})
        if str(route.get("routing_status") or "") != "planned":
            continue
        if args.max_questions is not None and processed_questions >= args.max_questions:
            break
        processed_questions += 1
        if str(route.get("family") or "") not in SUPPORTED_FAMILIES:
            output.append(
                abstention(
                    question_id,
                    None,
                    "unsupported_staged_review_family",
                    {"family": route.get("family"), "supported_families": sorted(SUPPORTED_FAMILIES)},
                )
            )
            continue
        state: dict[str, Any] = {}
        for stage in route.get("stages") or []:
            resolved_entities = stage_entities(stage, state)
            if resolved_entities is None:
                output.append(
                    abstention(
                        question_id,
                        stage,
                        "prior_stage_entity_selection_not_available",
                        {"entity_source": stage.get("entity_source")},
                    )
                )
                break
            if not bool(stage.get("requires_llm_review", True)):
                try:
                    execution = execute_gross_profit_margin_change_rank(stage, state)
                except (TypeError, ValueError) as error:
                    output.append(
                        abstention(
                            question_id,
                            stage,
                            "deterministic_transition_failure",
                            {"detail": str(error)},
                        )
                    )
                    break
                output.append(
                    {
                        "id": question_id,
                        "reviewer_type": "deterministic_staged_transition",
                        "stage_id": stage.get("stage_id"),
                        "annotation_status": "machine_provisional"
                        if str(execution.get("status") or "") == "stage_complete"
                        else "needs_human",
                        "provenance_status": "machine_provisional"
                        if str(execution.get("status") or "") == "stage_complete"
                        else "machine_abstained",
                        "deterministic_stage_execution": execution,
                        "reason_codes": list(execution.get("reason_codes") or []),
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
                if str(execution.get("status") or "") != "stage_complete":
                    break
                state[str(stage.get("stage_id") or "")] = execution
                if execution.get("winning_entity"):
                    state["winning_entity"] = str(execution["winning_entity"])
                continue
            candidate_result = route_stage_candidates(
                catalog_rows,
                stage,
                resolved_entities=resolved_entities,
                # An omitted scope may become resolved from Stage 1. Keep it
                # for every later retrieval instead of letting another stage
                # reopen a separate/consolidated ambiguity.
                scope=route.get("scope") or state.get("report_scope"),
            )
            if str(candidate_result.get("status") or "") != "candidate_tables_found":
                output.append(
                    abstention(
                        question_id,
                        stage,
                        "router_" + str(candidate_result.get("status") or "unknown"),
                        dict(candidate_result.get("feedback") or {}),
                    )
                )
                break
            packet = build_review_packet(
                question_id=question_id,
                question=questions[question_id],
                stage={**stage, "entities": resolved_entities},
                candidate_result=candidate_result,
                catalog_by_uid=catalog,
                tables_by_uid=tables,
                contexts_by_uid=contexts,
                max_tables_per_binding=args.max_tables_per_binding,
            )
            packets.append(packet)
            missing = list(packet.get("missing_source_cell_bindings") or [])
            if missing:
                output.append(
                    abstention(
                        question_id,
                        stage,
                        "packet_missing_explicit_target_period_cell",
                        {"missing_bindings": missing},
                    )
                )
                break
            scope_contract = dict(packet.get("scope_contract") or {})
            if str(scope_contract.get("status") or "") != "resolved":
                scope_reason = (
                    "ambiguous_report_scope"
                    if str(scope_contract.get("status") or "") == "ambiguous"
                    else "no_common_report_scope_across_required_bindings"
                )
                output.append(
                    abstention(
                        question_id,
                        stage,
                        "packet_" + scope_reason,
                        {"viable_report_scopes": scope_contract.get("viable_report_scopes")},
                    )
                )
                break
            resolved_scope = str((scope_contract.get("viable_report_scopes") or [""])[0])
            prior_scope = str(state.get("report_scope") or "")
            if prior_scope and prior_scope != resolved_scope:
                output.append(
                    abstention(
                        question_id,
                        stage,
                        "packet_scope_changed_between_stages",
                        {"prior_scope": prior_scope, "resolved_scope": resolved_scope},
                    )
                )
                break
            state["report_scope"] = resolved_scope
            document_contract = dict(packet.get("document_contract") or {})
            if str(document_contract.get("status") or "") != "resolved":
                output.append(
                    abstention(
                        question_id,
                        stage,
                        "packet_source_document_not_resolved",
                        {"viable_document_ids_by_company": document_contract.get("viable_document_ids_by_company")},
                    )
                )
                break
            if args.dry_run:
                output.append(
                    {
                        "id": question_id,
                        "reviewer_type": "qwen_evidence_bounded_staged",
                        "stage_id": stage.get("stage_id"),
                        "annotation_status": "not_run",
                        "provenance_status": "dry_run",
                        "packet_sha256": packet["packet_sha256"],
                        "packet_candidate_count": len(packet["candidates"]),
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
                break
            assert generator is not None or fixtures
            decision: dict[str, Any] | None = None
            critique: dict[str, Any] | None = None
            try:
                fixture = fixtures.get((question_id, str(stage.get("stage_id") or "")))
                if fixtures and fixture is None:
                    raise ValueError("Fixture has no decision for this stage")
                decision = fixture["decision"] if fixture is not None else generator(review_prompt(packet))
                critique = (
                    None
                    if args.no_self_critique
                    else fixture.get("self_critique", {"verdict": "approve"})
                    if fixture is not None
                    else generator(self_critique_prompt(packet, decision))
                )
                verified = verify_llm_decision(packet, decision, self_critique=critique)
            except (RuntimeError, ValueError, json.JSONDecodeError) as error:
                traces.append(
                    {
                        "id": question_id,
                        "stage_id": stage.get("stage_id"),
                        "packet_sha256": packet["packet_sha256"],
                        "parsed_model_decision": decision,
                        "self_critique": critique,
                        "verifier_error": str(error),
                        "trace_status": "generator_or_verifier_error",
                        "training_eligible": False,
                        "submission_eligible": False,
                    }
                )
                output.append(
                    abstention(
                        question_id,
                        stage,
                        "llm_output_or_verifier_failure",
                        {"detail": str(error)},
                    )
                )
                break
            verified["stage_id"] = stage.get("stage_id")
            verified["model"] = args.model
            traces.append(
                {
                    "id": question_id,
                    "stage_id": stage.get("stage_id"),
                    "packet_sha256": packet["packet_sha256"],
                    "parsed_model_decision": decision,
                    "self_critique": critique,
                    "verifier_annotation_status": verified.get("annotation_status"),
                    "verifier_reason_codes": list(verified.get("reason_codes") or []),
                    "trace_status": "verified",
                    "training_eligible": False,
                    "submission_eligible": False,
                }
            )
            if str(verified.get("annotation_status") or "") != "machine_provisional":
                output.append(verified)
                break
            execution = execute_reviewed_stage({**stage, "entities": resolved_entities}, verified)
            verified["deterministic_stage_execution"] = execution
            output.append(verified)
            if str(execution.get("status") or "") != "stage_complete":
                break
            if "eligible_entities" in execution:
                state["eligible_entities"] = list(execution["eligible_entities"])
            state[str(stage.get("stage_id") or "")] = execution
            if execution.get("winning_entity"):
                state["winning_entity"] = str(execution["winning_entity"])
            if bool(execution.get("final_stage")):
                output.append(
                    {
                        "id": question_id,
                        "record_type": "deterministic_final_result",
                        "annotation_status": "machine_provisional",
                        "provenance_status": "machine_provisional",
                        "result_value": execution["aggregate_value"],
                        "result_unit": execution["result_unit"],
                        "source_stage_id": stage.get("stage_id"),
                        "requires_independent_replay": True,
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
    destination = args.output.resolve()
    atomic_write_jsonl(destination, output)
    packet_path = destination.with_suffix(".packets.jsonl")
    atomic_write_jsonl(packet_path, packets)
    trace_path = None if args.trace_output is None else args.trace_output.resolve()
    if trace_path is not None:
        atomic_write_jsonl(trace_path, traces)
    manifest = {
        "schema_version": LLM_TABLE_REVIEW_SCHEMA_VERSION,
        "protocol": LLM_TABLE_REVIEW_PROTOCOL + "_staged_execution_v1",
        "model": args.model,
        "dry_run": bool(args.dry_run),
        "decision_fixture": args.decision_fixture is not None,
        "self_critique_enabled": not args.no_self_critique,
        "supported_families": sorted(SUPPORTED_FAMILIES),
        "record_count": len(output),
        "packet_count": len(packets),
        "trace_file": None if trace_path is None else str(trace_path),
        "trace_record_count": len(traces),
        "trace_training_eligible": False,
        "trace_submission_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
    }
    destination.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(destination), "packets": str(packet_path), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
