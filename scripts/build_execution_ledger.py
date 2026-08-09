#!/usr/bin/env python3
"""Materialize direct-answer execution evidence from V4 autonomous reviews.

This is the first answer-generation stage, not a fallback answer guesser.  It
only emits an executable record when V4's independent agents selected a
``machine_calibrated`` direct-lookup candidate and its exact V2 row/cell still
matches the immutable raw-HTML sidecar.  Every other question is retained in
the ledger with an explicit non-executable reason; it cannot enter a final
submission through ``compile_vifinqa_submission.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.binding import row_label  # noqa: E402
from finance_query.corpus import infer_unit  # noqa: E402
from finance_query.execution import convert_unit, parse_decimal  # noqa: E402
from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.schemas import DirectBinding  # noqa: E402
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def index_by_id(rows: Iterable[dict[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        qid = int(row["id"])
        if qid in output:
            raise ValueError(f"Duplicate Q{qid} in {name}")
        output[qid] = row
    return output


def load_v2_tables(bundle: Path) -> dict[str, dict[str, Any]]:
    base = {
        str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables.jsonl")
    }
    sidecar = bundle / "tables_structured_v2.jsonl"
    validate_structure_sidecar(bundle, sidecar)
    tables: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(sidecar):
        uid = str(row.get("internal_table_uid") or "")
        if uid not in base:
            raise ValueError(f"V2 sidecar UID absent from bundle: {uid}")
        if str((row.get("structure_quality") or {}).get("status") or "") != "reconstructed_from_raw_html":
            continue
        tables[uid] = {**base[uid], **row}
    return tables


def load_evidence_contexts(bundle: Path) -> dict[str, dict[str, Any]]:
    structured = bundle / "tables_structured_v2.jsonl"
    context_path = bundle / "tables_evidence_context_v1.jsonl"
    validate_evidence_context_sidecar(bundle, structured, context_path)
    contexts = {
        str(row["internal_table_uid"]): row for row in load_jsonl(context_path)
    }
    if not contexts:
        raise ValueError("Canonical evidence-context sidecar is empty")
    return contexts


def canonical_unit(table: dict[str, Any]) -> str | None:
    unit = table.get("unit_hint")
    if isinstance(unit, str) and unit:
        return unit
    context = " ".join(
        [
            str(table.get("context_before") or ""),
            str((table.get("context_trace") or {}).get("source_title") or ""),
            " ".join((table.get("context_trace") or {}).get("unit_labels") or []),
        ]
    )
    rows = table.get("rows") or []
    return infer_unit(context, "\n".join(" | ".join(map(str, row)) for row in rows))


def _non_executable(item: dict[str, Any], review: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    return {
        "id": int(item["id"]),
        "provenance_status": str((review or {}).get("consensus_status") or "needs_human"),
        "execution_status": "not_executable",
        "grounding_status": "not_bound",
        "execution_mode": None,
        "reason": reason,
        "operation_ast": (item.get("question_plan") or {}).get("operation_ast") or {},
        "operand_bindings": [],
    }


def direct_execution_row(
    item: dict[str, Any],
    review: dict[str, Any] | None,
    tables: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if review is None:
        return _non_executable(item, review, "no_autonomous_review")
    if str(review.get("consensus_status") or "") != "machine_calibrated":
        return _non_executable(item, review, "review_not_machine_calibrated")
    self_review = review.get("machine_self_review") or {}
    if not bool(self_review.get("training_eligible")) or not bool(self_review.get("critic_accepts")):
        return _non_executable(item, review, "self_review_or_critic_not_accepted")
    # V4 may carry a hash-bound local plan override for a disclosed source
    # row.  The immutable bundle plan remains available in the review record's
    # provenance; execution must use the plan that V4 actually reviewed.
    plan = review.get("effective_question_plan") or item.get("question_plan") or {}
    if str(plan.get("family") or item.get("weak_family") or "") != "direct_lookup":
        return _non_executable(item, review, "family_requires_composed_executor")
    uid = str(review.get("machine_candidate_uid") or "")
    table = tables.get(uid)
    if table is None:
        return _non_executable(item, review, "selected_table_not_in_valid_v2_sidecar")
    context = (contexts or {}).get(uid)
    if contexts is not None and context is None:
        return _non_executable(item, review, "selected_table_not_in_canonical_context_sidecar")
    selected = self_review.get("selected_value_binding") or {}
    if str(selected.get("status") or "") != "cell_bound":
        return _non_executable(item, review, "selected_value_cell_not_bound")
    row_index = selected.get("row_index")
    column_index = selected.get("column_index")
    rows = table.get("rows") or []
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        return _non_executable(item, review, "selected_value_coordinates_invalid")
    if not 0 <= row_index < len(rows) or not 0 <= column_index < len(rows[row_index]):
        return _non_executable(item, review, "selected_value_coordinates_out_of_bounds")
    raw_value = str(rows[row_index][column_index])
    if raw_value != str(selected.get("value") or ""):
        return _non_executable(item, review, "selected_value_differs_from_v2_source")
    column_label = str(selected.get("column_label") or "")
    if context is not None:
        headers = (context.get("canonical_headers") or {}).get("columns") or []
        expected_label = str(
            next(
                (
                    column.get("source_label")
                    for column in headers
                    if int(column.get("column_index") or -1) == column_index
                ),
                "",
            )
            or ""
        )
        if not expected_label or column_label != expected_label:
            return _non_executable(item, review, "selected_column_label_differs_from_canonical_source")
    else:
        # Compatibility path for a small unit-level caller.  Production always
        # supplies the hash-validated canonical context above.
        column_labels = table.get("column_labels") or []
        if column_index >= len(column_labels) or column_label != str(column_labels[column_index]):
            return _non_executable(item, review, "selected_column_label_differs_from_v2_source")
    parsed = parse_decimal(raw_value)
    if parsed.value is None or any(
        warning != "percent_value_not_scaled" for warning in parsed.warnings
    ):
        return _non_executable(item, review, "selected_value_is_not_a_reliable_number")
    source_unit = canonical_unit(table)
    requested_unit = plan.get("requested_unit")
    monetary_targets = {"vnd", "thousand_vnd", "million_vnd", "billion_vnd", "trillion_vnd"}
    if requested_unit in monetary_targets and source_unit is None:
        return _non_executable(item, review, "source_monetary_unit_unresolved")
    try:
        converted_value = convert_unit(
            Decimal(parsed.value), source_unit, str(requested_unit) if requested_unit else None
        )
    except ValueError:
        return _non_executable(item, review, "source_and_requested_units_are_incompatible")
    operands = plan.get("operands") or []
    operand_id = str(operands[0].get("operand_id") or "x0") if operands else "x0"
    binding = DirectBinding(
        internal_table_uid=uid,
        document_id=str(table["document_id"]),
        row_index=row_index,
        column_index=column_index,
        row_text=row_label(rows[row_index]),
        column_text=column_label,
        raw_value=raw_value,
        parsed_value=parsed.value,
        source_unit=source_unit,
        target_unit=str(requested_unit) if requested_unit else None,
        converted_value=format(converted_value, "f"),
        binding_score=float(review.get("machine_confidence") or 0.0),
        warnings=list(parsed.warnings),
    )
    return {
        "id": int(item["id"]),
        "provenance_status": "machine_calibrated",
        "execution_status": "grounded",
        "grounding_status": "exact_rows_validated",
        "execution_mode": "direct_lookup",
        "formula_definition_status": "confirmed",
        "operation_ast": plan.get("operation_ast") or {"op": "lookup", "args": [operand_id]},
        "operand_bindings": [
            {
                "operand_id": operand_id,
                "internal_table_uid": uid,
                "binding": binding.to_dict(),
            }
        ],
        "review_provenance": {
            "review_protocol": self_review.get("protocol"),
            "machine_confidence": review.get("machine_confidence"),
            "agreement": review.get("agreement"),
            "critic_accepts": True,
        },
    }


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    items = load_jsonl(bundle / "review_items.jsonl")
    reviews = index_by_id(load_jsonl(args.machine_reviews.resolve()), "machine reviews")
    item_ids = {int(item["id"]) for item in items}
    unexpected = sorted(set(reviews) - item_ids)
    if unexpected:
        raise ValueError(f"Machine reviews contain IDs outside bundle: {unexpected[:10]}")
    tables = load_v2_tables(bundle)
    contexts = load_evidence_contexts(bundle)
    rows = [
        direct_execution_row(item, reviews.get(int(item["id"])), tables, contexts)
        for item in items
    ]
    write_jsonl(args.output.resolve(), rows)
    counts = Counter(str(row["execution_status"]) for row in rows)
    reasons = Counter(str(row.get("reason") or "") for row in rows if row.get("reason"))
    print("Execution ledger:", args.output.resolve())
    print("Status counts:", dict(counts))
    print("Top non-executable reasons:", dict(reasons.most_common(12)))


if __name__ == "__main__":
    main()
