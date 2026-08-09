#!/usr/bin/env python3
"""Discover exact raw-V2 candidates for direct lookups without rebuilding retrieval.

The review bundle's Top-K is immutable retrieval evidence.  It can nonetheless
miss an exact source row that is already present in the local raw V2 corpus.
This script creates a hash-bound *sidecar* of such rows.  The sidecar is not a
label and does not execute an answer: V4 must still validate the projected row,
canonical header, period cell, unit, multi-view votes and critic gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import validate_evidence_context_sidecar
from finance_query.plan_overrides import (
    apply_plan_overrides,
    canonical_sha256,
    validate_plan_overrides,
)
from finance_query.table_structure import sha256_file, validate_structure_sidecar

import auto_review_bundle_v4 as v4


SCHEMA_VERSION = 1
END_ROW_MARKERS = ("cuối năm", "cuối kỳ")
START_ROW_MARKERS = ("đầu năm", "đầu kỳ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question-plan-overrides", type=Path, default=None)
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


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def metric_tokens(value: object) -> list[str]:
    return [
        token
        for token in v4.v3.token_sequence(str(value or ""))
        if not token.isdecimal()
    ]


def row_endpoint_compatible(question: str, label: str) -> bool:
    """Reject a row that explicitly names the endpoint opposite the question."""
    requirement = v4.period_requirement(question)
    folded = label.casefold()
    if requirement == "end":
        return not any(marker in folded for marker in START_ROW_MARKERS)
    if requirement == "start":
        return not any(marker in folded for marker in END_ROW_MARKERS)
    return True


def base_candidate(
    *, uid: str, report_year: int | None, row_index: int, row: list[Any]
) -> dict[str, Any]:
    return {
        "internal_table_uid": uid,
        # Source-discovery is a deterministic recall supplement, not a
        # retrieval-rank claim.  Keep it last for the retrieval voter.
        "rank": 1_000_000,
        "lexical_rank": 1_000_000,
        "dense_rank": 1_000_000,
        "candidate_source": "raw_v2_direct_source_discovery",
        "metadata_score": 1.0,
        "ticker_match": True,
        "scope_match": True,
        "year_match": True,
        "report_year": report_year,
        "best_row_index": row_index,
        "direct_evidence": "VALUE: " + v4.v3.source_row_text(row),
        "evidence_features": {
            "row_score": 1.0,
            "metric_overlap": 1.0,
            "question_overlap": 1.0,
            "numeric": True,
        },
        "source_discovery": {
            "policy": "exact_raw_v2_metric_token_sequence_v1",
            "row_index": row_index,
            "source_row": [str(value) for value in row],
        },
    }


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    output = args.output.resolve()
    if output.parent != bundle:
        raise ValueError("Direct EvidenceSet output must reside in the review bundle")
    context_path = (
        args.evidence_context or bundle / "tables_evidence_context_v2.jsonl"
    ).resolve()
    if context_path.parent != bundle:
        raise ValueError("Direct EvidenceSet context must reside in the review bundle")
    structured_path = bundle / "tables_structured_v2.jsonl"
    validate_structure_sidecar(bundle, structured_path)
    validate_evidence_context_sidecar(bundle, structured_path, context_path)

    source_items = load_jsonl(bundle / "review_items.jsonl")
    overrides: dict[int, dict[str, Any]] = {}
    override_path: Path | None = None
    if args.question_plan_overrides is not None:
        override_path = args.question_plan_overrides.resolve()
        overrides = validate_plan_overrides(source_items, load_jsonl(override_path))
    items = apply_plan_overrides(source_items, overrides)
    base_tables = {
        str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables.jsonl")
    }
    structures = {
        str(row["internal_table_uid"]): row for row in load_jsonl(structured_path)
    }
    contexts = {
        str(row["internal_table_uid"]): row for row in load_jsonl(context_path)
    }
    tables = {
        uid: {**base, **structures[uid]}
        for uid, base in base_tables.items()
        if uid in structures
    }
    table_by_metadata: dict[tuple[str, int | None, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for uid, table in tables.items():
        table_by_metadata[
            (
                str(table.get("ticker") or ""),
                table.get("report_year"),
                str(table.get("scope") or ""),
            )
        ].append((uid, table))

    output_rows: list[dict[str, Any]] = []
    candidate_count = 0
    ambiguous_table_count = 0
    family_counts: Counter[str] = Counter()
    for item in items:
        plan = item.get("question_plan") or {}
        family = str(plan.get("family") or item.get("weak_family") or "")
        if family != "direct_lookup":
            continue
        family_counts[family] += 1
        planned_tokens = metric_tokens(item.get("effective_metric"))
        matches_by_uid: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if planned_tokens:
            tickers = {str(value) for value in plan.get("tickers") or [] if str(value)}
            years = {int(value) for value in plan.get("years") or [] if isinstance(value, int)}
            requested_scope = str(plan.get("scope") or "")
            for (ticker, report_year, scope), indexed_tables in table_by_metadata.items():
                if tickers and ticker not in tickers:
                    continue
                if years and report_year not in years:
                    continue
                if requested_scope and scope != requested_scope:
                    continue
                for uid, table in indexed_tables:
                    context = contexts[uid]
                    if str((context.get("quality") or {}).get("status") or "") != "review_ready":
                        continue
                    for row_index, row in enumerate(table.get("rows") or []):
                        label = v4.row_label([str(value) for value in row])
                        if (
                            metric_tokens(label) != planned_tokens
                            or not row_endpoint_compatible(str(item.get("question") or ""), label)
                        ):
                            continue
                        candidate = base_candidate(
                            uid=uid,
                            report_year=table.get("report_year"),
                            row_index=row_index,
                            row=row,
                        )
                        provisional = {
                            **candidate,
                            "structure_validation": {
                                "validated": True,
                                "row_index": row_index,
                            },
                        }
                        assessment = v4.candidate_assessment(
                            item,
                            provisional,
                            table,
                            context,
                            token_gate=0.85,
                            bigram_gate=0.45,
                        )
                        binding = assessment.get("value_binding") or {}
                        identity = assessment.get("raw_metric_identity") or {}
                        if (
                            binding.get("status") != "cell_bound"
                            or not bool((assessment.get("grounding") or {}).get("guard_pass"))
                            or not bool(identity.get("exact"))
                        ):
                            continue
                        candidate["source_discovery"].update(
                            {
                                "raw_row_label": identity.get("raw_row_label"),
                                "value_binding": binding,
                            }
                        )
                        matches_by_uid[uid].append(candidate)
        candidates: list[dict[str, Any]] = []
        ambiguous_same_table_rows: list[dict[str, Any]] = []
        for uid, matches in sorted(matches_by_uid.items()):
            if len(matches) == 1:
                candidates.append(matches[0])
            else:
                ambiguous_table_count += 1
                ambiguous_same_table_rows.append(
                    {
                        "internal_table_uid": uid,
                        "row_indices": [match["best_row_index"] for match in matches],
                        "reason": "multiple_exact_metric_rows_in_one_table",
                    }
                )
        candidate_count += len(candidates)
        output_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "id": int(item["id"]),
                "family": family,
                "effective_question_plan_sha256": canonical_sha256(plan),
                "effective_metric": item.get("effective_metric"),
                "candidates": candidates,
                "ambiguous_same_table_rows": ambiguous_same_table_rows,
            }
        )

    write_jsonl(output, output_rows)
    manifest_path = output.with_suffix(".manifest.json")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(structured_path),
        "evidence_context_file": context_path.name,
        "evidence_context_sha256": sha256_file(context_path),
        "question_plan_override_file": None if override_path is None else override_path.name,
        "question_plan_overrides_sha256": None if override_path is None else sha256_file(override_path),
        "question_count": len(output_rows),
        "candidate_count": candidate_count,
        "ambiguous_same_table_count": ambiguous_table_count,
        "family_counts": dict(family_counts),
        "sidecar_sha256": sha256_file(output),
    }
    write_json(manifest_path, manifest)
    print(json.dumps({"output": str(output), "manifest": str(manifest_path), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
