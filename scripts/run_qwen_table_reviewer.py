#!/usr/bin/env python3
"""Run Qwen2.5-14B as a bounded table-evidence reviewer.

Use ``--dry-run`` first. It writes packets without downloading or loading a
model. Transformer inference requires optional ``accelerate`` and
``bitsandbytes`` packages and is intentionally not installed as a base project
dependency.
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

from finance_query.llm_table_reviewer import (  # noqa: E402
    LLM_TABLE_REVIEW_PROTOCOL,
    LLM_TABLE_REVIEW_SCHEMA_VERSION,
    build_review_packet,
    review_prompt,
    self_critique_prompt,
    verify_llm_decision,
)
from finance_query.qwen_inference import QwenGenerator  # noqa: E402


DEFAULT_MODEL = "Qwen/Qwen2.5-14B-Instruct"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--stage-candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--question-id", type=int, action="append", default=[])
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--max-tables-per-binding", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=1536)
    parser.add_argument("--no-self-critique", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
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


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    stage_rows = load_jsonl(args.stage_candidates.resolve())
    selected_ids = set(args.question_id)
    catalog = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "table_routing_catalog_v1.jsonl")
    }
    tables = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables_structured_v2.jsonl")
    }
    contexts = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables_evidence_context_v3.jsonl")
    }
    questions = {
        int(row["id"]): str(row.get("question") or "")
        for row in load_jsonl(bundle / "review_items.jsonl")
    }
    generator = None if args.dry_run else QwenGenerator(args.model, args.max_new_tokens)
    output: list[dict[str, Any]] = []
    packets: list[dict[str, Any]] = []
    processed_questions = 0
    for route_row in stage_rows:
        qid = int(route_row["id"])
        if selected_ids and qid not in selected_ids:
            continue
        if args.max_questions is not None and processed_questions >= args.max_questions:
            break
        processed_questions += 1
        for stage_record in route_row.get("stages") or []:
            stage = stage_record["stage"]
            result = stage_record["candidate_result"]
            status = str(result.get("status") or "")
            if status != "candidate_tables_found":
                output.append(
                    {
                        "id": qid,
                        "reviewer_type": "qwen_evidence_bounded",
                        "stage_id": stage.get("stage_id"),
                        "annotation_status": "needs_human",
                        "provenance_status": "machine_abstained",
                        "reason_codes": ["router_" + status],
                        "feedback": result.get("feedback"),
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
                continue
            packet = build_review_packet(
                question_id=qid,
                question=questions[qid],
                stage=stage,
                candidate_result=result,
                catalog_by_uid=catalog,
                tables_by_uid=tables,
                contexts_by_uid=contexts,
                max_tables_per_binding=args.max_tables_per_binding,
            )
            packets.append(packet)
            missing_source_cell_bindings = list(packet.get("missing_source_cell_bindings") or [])
            if missing_source_cell_bindings:
                output.append(
                    {
                        "id": qid,
                        "reviewer_type": "qwen_evidence_bounded",
                        "stage_id": stage.get("stage_id"),
                        "annotation_status": "needs_human",
                        "provenance_status": "machine_abstained",
                        "reason_codes": ["packet_missing_explicit_target_period_cell"],
                        "feedback": {
                            "reason_code": "missing_explicit_target_period_cell",
                            "missing_bindings": missing_source_cell_bindings,
                            "next_action": (
                                "retrieve_or_materialize a source table with an explicit "
                                "target-period header; never infer a period"
                            ),
                        },
                        "packet_sha256": packet["packet_sha256"],
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
                continue
            scope_contract = dict(packet.get("scope_contract") or {})
            scope_status = str(scope_contract.get("status") or "")
            if scope_status in {"ambiguous", "unresolved"}:
                reason_code = (
                    "ambiguous_report_scope"
                    if scope_status == "ambiguous"
                    else "no_common_report_scope_across_required_bindings"
                )
                output.append(
                    {
                        "id": qid,
                        "reviewer_type": "qwen_evidence_bounded",
                        "stage_id": stage.get("stage_id"),
                        "annotation_status": "needs_human",
                        "provenance_status": "machine_abstained",
                        "reason_codes": ["packet_" + reason_code],
                        "feedback": {
                            "reason_code": reason_code,
                            "viable_report_scopes": scope_contract.get("viable_report_scopes"),
                            "next_action": (
                                "supply an explicit consolidated/separate scope or materialize "
                                "all required variables in one source-backed scope; never mix scopes"
                            ),
                        },
                        "packet_sha256": packet["packet_sha256"],
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
                continue
            document_contract = dict(packet.get("document_contract") or {})
            if str(document_contract.get("status") or "") != "resolved":
                output.append(
                    {
                        "id": qid,
                        "reviewer_type": "qwen_evidence_bounded",
                        "stage_id": stage.get("stage_id"),
                        "annotation_status": "needs_human",
                        "provenance_status": "machine_abstained",
                        "reason_codes": ["packet_source_document_not_resolved"],
                        "feedback": {
                            "reason_code": "source_document_not_resolved",
                            "viable_document_ids_by_company": document_contract.get("viable_document_ids_by_company"),
                            "next_action": "select one source report version per company; never mix report versions",
                        },
                        "packet_sha256": packet["packet_sha256"],
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
                continue
            if args.dry_run:
                output.append(
                    {
                        "id": qid,
                        "reviewer_type": "qwen_evidence_bounded",
                        "stage_id": stage.get("stage_id"),
                        "annotation_status": "not_run",
                        "provenance_status": "dry_run",
                        "packet_sha256": packet["packet_sha256"],
                        "packet_candidate_count": len(packet["candidates"]),
                        "human_verified": False,
                        "submission_eligible": False,
                    }
                )
                continue
            assert generator is not None
            try:
                decision = generator(review_prompt(packet))
                critique = None if args.no_self_critique else generator(self_critique_prompt(packet, decision))
                verified = verify_llm_decision(packet, decision, self_critique=critique)
            except (ValueError, RuntimeError, json.JSONDecodeError) as error:
                verified = {
                    "id": qid,
                    "reviewer_type": "qwen_evidence_bounded",
                    "stage_id": stage.get("stage_id"),
                    "annotation_status": "needs_human",
                    "provenance_status": "machine_abstained",
                    "reason_codes": ["llm_output_or_verifier_failure"],
                    "feedback": {"detail": str(error)},
                    "human_verified": False,
                    "submission_eligible": False,
                }
            verified["stage_id"] = stage.get("stage_id")
            verified["model"] = args.model
            output.append(verified)
    destination = args.output.resolve()
    atomic_write_jsonl(destination, output)
    packet_path = destination.with_suffix(".packets.jsonl")
    atomic_write_jsonl(packet_path, packets)
    manifest = {
        "schema_version": LLM_TABLE_REVIEW_SCHEMA_VERSION,
        "protocol": LLM_TABLE_REVIEW_PROTOCOL,
        "model": args.model,
        "dry_run": bool(args.dry_run),
        "self_critique_enabled": not args.no_self_critique,
        "record_count": len(output),
        "packet_count": len(packets),
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
    }
    destination.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(destination), "packets": str(packet_path), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
