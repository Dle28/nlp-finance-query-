#!/usr/bin/env python3
"""Build an auditable review ledger for iterative Codex + human review.

The ledger always contains every bundle question. Codex-assisted reviews remain
machine-side recommendations and never become ``human_verified`` without a
human annotation produced by the widget.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from finance_query.table_structure import validate_structure_sidecar


MACHINE_STATUSES = {
    "machine_calibrated",
    "machine_high_confidence",
    "machine_provisional",
    "needs_human",
    "retrieval_failure",
}
HUMAN_STATUSES = {
    "human_verified",
    "human_verified_partial",
    "verified_no_candidate",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument("--assistant-reviews", type=Path, default=None)
    parser.add_argument("--human-labels", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--human-check-queue", type=Path, default=None)
    parser.add_argument("--human-check-size", type=int, default=6)
    return parser.parse_args()


def load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL: {path}:{line_number}") from exc
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def by_id(rows: list[dict[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        qid = int(row["id"])
        if qid in output:
            raise ValueError(f"Duplicate ID {qid} in {name}")
        output[qid] = row
    return output


def validate_assistant_review(
    item: dict[str, Any],
    review: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> None:
    if str(review.get("reviewer_type") or "") != "codex_assisted":
        raise ValueError(f"Q{item['id']}: assistant reviewer_type must be codex_assisted")
    if bool(review.get("human_verified")):
        raise ValueError(f"Q{item['id']}: Codex review cannot claim human_verified")

    candidates = {
        str(candidate["internal_table_uid"]): candidate
        for candidate in item.get("candidates") or []
    }
    proposed = {str(uid) for uid in review.get("proposed_positive_table_uids") or []}
    missing = sorted(proposed - set(candidates))
    if missing:
        raise ValueError(f"Q{item['id']}: proposed UIDs missing from bundle: {missing}")

    for ref in review.get("evidence_refs") or []:
        uid = str(ref.get("internal_table_uid") or "")
        candidate = candidates.get(uid)
        if candidate is None:
            raise ValueError(f"Q{item['id']}: evidence ref UID missing from candidates: {uid}")
        if int(ref.get("rank") or 0) != int(candidate.get("rank") or 0):
            raise ValueError(f"Q{item['id']}: evidence ref rank does not match candidate {uid}")
        if str(ref.get("direct_evidence") or "") != str(candidate.get("direct_evidence") or ""):
            raise ValueError(f"Q{item['id']}: evidence ref differs from immutable bundle {uid}")
        table = tables.get(uid)
        if table is None:
            raise ValueError(f"Q{item['id']}: table payload missing for {uid}")
        row_count = len(table.get("rows") or [])
        for row_index in ref.get("row_indices") or []:
            if not isinstance(row_index, int) or not 0 <= row_index < row_count:
                raise ValueError(
                    f"Q{item['id']}: row index {row_index} out of bounds for {uid}"
                )
        for source_row in ref.get("source_rows") or []:
            row_index = source_row.get("row_index")
            if not isinstance(row_index, int) or not 0 <= row_index < row_count:
                raise ValueError(
                    f"Q{item['id']}: source row index {row_index} out of bounds for {uid}"
                )
            if source_row.get("row") != table["rows"][row_index]:
                raise ValueError(
                    f"Q{item['id']}: source row {row_index} differs from immutable table {uid}"
                )


def validate_human_review(
    item: dict[str, Any],
    review: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> None:
    """Validate human-selected UIDs and formula row bindings against source data."""
    status = str(review.get("annotation_status") or "")
    if status not in HUMAN_STATUSES:
        raise ValueError(f"Q{item['id']}: unsupported human status: {status}")

    candidates = {
        str(candidate["internal_table_uid"]): candidate
        for candidate in item.get("candidates") or []
    }
    selected = {str(uid) for uid in review.get("positive_table_uids") or []}
    missing = sorted(selected - set(candidates))
    if missing:
        raise ValueError(f"Q{item['id']}: human UIDs missing from bundle: {missing}")
    if status == "verified_no_candidate" and selected:
        raise ValueError(f"Q{item['id']}: no-candidate review cannot select tables")

    formula = review.get("formula_spec")
    if not formula:
        return

    required = {
        str(operand["operand_id"])
        for operand in formula.get("operands") or []
        if operand.get("required", True)
    }
    coverage = review.get("operand_coverage") or {}
    valid_operands: set[str] = set()
    for operand_id, matches in coverage.items():
        operand_id = str(operand_id)
        if operand_id not in required:
            raise ValueError(f"Q{item['id']}: unknown formula operand {operand_id}")
        for match in matches or []:
            uid = str(match.get("uid") or "")
            candidate = candidates.get(uid)
            if candidate is None or uid not in selected:
                raise ValueError(
                    f"Q{item['id']}: operand {operand_id} references an unselected candidate {uid}"
                )
            if int(match.get("rank") or 0) != int(candidate.get("rank") or 0):
                raise ValueError(
                    f"Q{item['id']}: operand rank does not match candidate {uid}"
                )
            table = tables.get(uid)
            if table is None:
                raise ValueError(f"Q{item['id']}: table payload missing for {uid}")
            if str((table.get("structure_quality") or {}).get("status") or "") != "reconstructed_from_raw_html":
                continue
            source_rows = match.get("source_rows") or []
            for source_row in source_rows:
                row_index = source_row.get("row_index")
                rows = table.get("rows") or []
                if not isinstance(row_index, int) or not 0 <= row_index < len(rows):
                    raise ValueError(
                        f"Q{item['id']}: operand row {row_index} out of bounds for {uid}"
                    )
                if source_row.get("row") != rows[row_index]:
                    raise ValueError(
                        f"Q{item['id']}: operand row {row_index} differs from V2 source {uid}"
                    )
                if source_row.get("column_labels") != (table.get("column_labels") or []):
                    raise ValueError(
                        f"Q{item['id']}: operand column labels differ from V2 source {uid}"
                    )
            if source_rows:
                valid_operands.add(operand_id)

    if status == "human_verified":
        if not bool(review.get("formula_confirmed")):
            raise ValueError(f"Q{item['id']}: complete formula review lacks human confirmation")
        if str(formula.get("definition_status") or "") == "ambiguous":
            raise ValueError(f"Q{item['id']}: ambiguous formula cannot be complete")
        missing_operands = sorted(required - valid_operands)
        if missing_operands:
            raise ValueError(
                f"Q{item['id']}: complete formula review lacks V2 rows for {missing_operands}"
            )


def load_review_tables(bundle: Path) -> dict[str, dict[str, Any]]:
    tables = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables.jsonl")
    }
    sidecar_path = bundle / "tables_structured_v2.jsonl"
    if not sidecar_path.is_file():
        return tables
    validate_structure_sidecar(bundle, sidecar_path)
    sidecar = {
        str(row["internal_table_uid"]): row for row in load_jsonl(sidecar_path)
    }
    unknown = sorted(set(sidecar) - set(tables))
    if unknown:
        raise ValueError(f"V2 sidecar contains UIDs outside bundle: {unknown[:3]}")
    for uid, structure in sidecar.items():
        tables[uid] = {**tables[uid], **structure}
    return tables


def machine_assistant_disagree(
    machine: dict[str, Any] | None,
    assistant: dict[str, Any] | None,
) -> bool:
    if not machine or not assistant:
        return False
    machine_uid = machine.get("machine_candidate_uid")
    proposed = {str(uid) for uid in assistant.get("proposed_positive_table_uids") or []}
    if assistant.get("proposed_no_candidate"):
        return bool(machine_uid)
    return bool(machine_uid and proposed and str(machine_uid) not in proposed)


def ledger_status(
    machine: dict[str, Any] | None,
    assistant: dict[str, Any] | None,
    human: dict[str, Any] | None,
) -> tuple[str, str]:
    if human is not None:
        status = str(human.get("annotation_status") or "")
        if status not in HUMAN_STATUSES:
            raise ValueError(f"Unsupported human status: {status}")
        return status, "human"

    machine_status = str((machine or {}).get("consensus_status") or "needs_human")
    if machine_status not in MACHINE_STATUSES:
        machine_status = "needs_human"
    assistant_status = str((assistant or {}).get("annotation_status") or "")
    disagreement = machine_assistant_disagree(machine, assistant)
    if disagreement or assistant_status in {"needs_human", "retrieval_failure"}:
        return "needs_human", "machine"
    return machine_status, "machine"


def queue_priority(row: dict[str, Any]) -> tuple[int, int, int, int, int, float, int]:
    status = str(row.get("annotation_status") or "")
    assistant = row.get("assistant_review") or {}
    return (
        0 if assistant else 1,
        0 if status in {"needs_human", "retrieval_failure"} else 1,
        0 if row.get("machine_assistant_disagreement") else 1,
        0 if assistant.get("requires_human_confirmation") else 1,
        0 if assistant.get("evidence_completeness") != "complete" else 1,
        float(assistant.get("review_confidence") or row.get("machine_confidence") or 0.0),
        int(row["id"]),
    )


def make_human_check_queue(
    ledger: list[dict[str, Any]],
    size: int,
) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in ledger
        if row.get("needs_review_refresh")
        or (
            not row.get("human_verified")
            and row.get("annotation_status")
            in {"needs_human", "retrieval_failure", "machine_provisional"}
        )
    ]
    if size <= 0 or not eligible:
        return []

    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_family[str(row.get("family") or "unknown")].append(row)

    chosen: list[dict[str, Any]] = []
    seen: set[int] = set()
    for family in sorted(by_family):
        row = sorted(by_family[family], key=queue_priority)[0]
        chosen.append(row)
        seen.add(int(row["id"]))
    for row in sorted(eligible, key=queue_priority):
        if len(chosen) >= size:
            break
        if int(row["id"]) not in seen:
            chosen.append(row)
            seen.add(int(row["id"]))

    return [
        {
            "id": int(row["id"]),
            "family": row.get("family"),
            "annotation_status": row.get("annotation_status"),
            "machine_assistant_disagreement": row.get("machine_assistant_disagreement"),
            "assistant_confidence": (row.get("assistant_review") or {}).get(
                "review_confidence"
            ),
            "queue_reason": row.get("human_review_reasons"),
        }
        for row in chosen[:size]
    ]


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    items = load_jsonl(bundle / "review_items.jsonl")
    bundle_tables = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle / "tables.jsonl")
    }
    review_tables = load_review_tables(bundle)
    machine = by_id(load_jsonl(args.machine_reviews), "machine reviews")
    assistant = by_id(load_jsonl(args.assistant_reviews), "assistant reviews")
    human = by_id(load_jsonl(args.human_labels), "human labels")

    item_ids = {int(item["id"]) for item in items}
    for name, mapping in (("machine", machine), ("assistant", assistant), ("human", human)):
        extra = sorted(set(mapping) - item_ids)
        if extra:
            raise ValueError(f"{name} reviews contain IDs outside bundle: {extra}")

    ledger: list[dict[str, Any]] = []
    for item in items:
        qid = int(item["id"])
        machine_row = machine.get(qid)
        assistant_row = assistant.get(qid)
        human_row = human.get(qid)
        if assistant_row is not None:
            # Legacy assistant rows were materialized from the immutable V3
            # payload. New rounds may explicitly declare a checksum-validated
            # V2 sidecar and must then be checked against that exact raw-HTML
            # reconstruction instead of the legacy grid.
            assistant_uses_v2 = bool(
                (assistant_row.get("structure_validation") or {}).get("complete")
            )
            validate_assistant_review(
                item,
                assistant_row,
                review_tables if assistant_uses_v2 else bundle_tables,
            )
        if human_row is not None:
            # New formula coverage is created by the widget from the local V2
            # grid and is therefore checked against the verified sidecar.
            validate_human_review(item, human_row, review_tables)

        status, source = ledger_status(machine_row, assistant_row, human_row)
        disagreement = machine_assistant_disagree(machine_row, assistant_row)
        family = str(
            (item.get("question_plan") or {}).get("family")
            or item.get("weak_family")
            or "unknown"
        )
        reasons: list[str] = []
        if status in {"needs_human", "retrieval_failure"}:
            reasons.append(status)
        if disagreement:
            reasons.append("machine_assistant_disagreement")
        if assistant_row and assistant_row.get("requires_human_confirmation"):
            reasons.append("assistant_requested_confirmation")
        if assistant_row and assistant_row.get("evidence_completeness") != "complete":
            reasons.append("incomplete_evidence_set")
        if status == "machine_provisional":
            reasons.append("provisional_not_training_eligible")

        human_verified = bool(human_row and human_row.get("human_verified"))
        human_needs_refresh = bool(
            human_row
            and human_row.get("positive_table_uids")
            and not (human_row.get("structure_validation") or {}).get("complete")
        )
        machine_needs_refresh = bool(
            human_row is None
            and status in {"machine_calibrated", "machine_high_confidence"}
            and not ((machine_row or {}).get("structure_validation") or {}).get(
                "validated"
            )
        )
        needs_review_refresh = human_needs_refresh or machine_needs_refresh
        if human_needs_refresh:
            reasons.append("legacy_human_label_requires_v2_revalidation")
        if machine_needs_refresh:
            reasons.append("machine_label_requires_v2_revalidation")
        selected_uids = (
            list(human_row.get("positive_table_uids") or [])
            if human_row
            else [machine_row.get("machine_candidate_uid")]
            if machine_row and machine_row.get("machine_candidate_uid")
            else []
        )
        ledger.append(
            {
                "id": qid,
                "question": item.get("question"),
                "family": family,
                "weak_family": item.get("weak_family"),
                "annotation_status": status,
                "label_source": source,
                "human_verified": human_verified,
                "needs_review_refresh": needs_review_refresh,
                "selected_table_uids": selected_uids,
                "training_eligible": (
                    (
                        status in {"machine_calibrated", "machine_high_confidence"}
                        and bool(
                            ((machine_row or {}).get("structure_validation") or {}).get(
                                "validated"
                            )
                        )
                    )
                    or (status == "human_verified" and not human_needs_refresh)
                ),
                "machine_confidence": (machine_row or {}).get("machine_confidence"),
                "machine_assistant_disagreement": disagreement,
                "human_review_reasons": reasons,
                "machine_review": machine_row,
                "assistant_review": assistant_row,
                "human_review": human_row,
            }
        )

    write_jsonl(args.output, ledger)
    print("Review ledger:", args.output, "count=", len(ledger))
    print("Status counts:", {
        status: sum(row["annotation_status"] == status for row in ledger)
        for status in sorted({row["annotation_status"] for row in ledger})
    })

    if args.human_check_queue:
        queue = make_human_check_queue(ledger, args.human_check_size)
        write_jsonl(args.human_check_queue, queue)
        print("Human check queue:", args.human_check_queue, "count=", len(queue))


if __name__ == "__main__":
    main()
