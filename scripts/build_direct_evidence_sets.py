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
from finance_query.report_segments import validate_report_segment_sidecar
from finance_query.table_structure import sha256_file, validate_structure_sidecar

import auto_review_bundle_v4 as v4


SCHEMA_VERSION = 1
END_ROW_MARKERS = ("cuối năm", "cuối kỳ")
START_ROW_MARKERS = ("đầu năm", "đầu kỳ")
TRAILING_PERIOD_PATTERNS = (
    re.compile(
        r"\s+(?:vào|tại|đến)?\s*(?:cuối|đầu)\s+(?:năm|kỳ)(?:\s+(?:19|20)\d{2})?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s+(?:đến|tại|vào)\s+ngày\s+\d{1,2}(?:\s*[/.-]|\s+tháng\s+)\d{1,2}(?:\s*[/.-]|\s+năm)(?:\s+(?:19|20)\d{2})?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s+(?:ngày\s+)?31\s+tháng\s+12\s+năm(?:\s+(?:19|20)\d{2})?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"\s+(?:trong\s+)?năm\s+(?:19|20)\d{2}\s*$", re.IGNORECASE),
)
TRAILING_PARENT_ENTITY_RE = re.compile(r"\s+(?:tại\s+)?công\s+ty\s+mẹ\s*$", re.IGNORECASE)
LEADING_BALANCE_DESCRIPTOR_RE = re.compile(r"^số\s+dư\s+", re.IGNORECASE)
LEADING_PERIOD_DESCRIPTOR_RE = re.compile(
    r"^(?:(?:vào|tại|đến)\s+)?(?:đầu|cuối)\s+(?:năm|kỳ)(?:\s+(?:19|20)\d{2})?\s*,?\s*",
    re.IGNORECASE,
)
LEADING_PUNCTUATION_RE = re.compile(r"^[,;:]\s*")
FINANCIAL_PARENT_TOKENS = {
    "tài", "sản", "nợ", "vốn", "doanh", "thu", "chi", "phí", "vay",
    "tiền", "lợi", "nhuận", "hàng", "tồn", "kho", "đầu", "tư", "phải", "trả",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question-plan-overrides", type=Path, default=None)
    parser.add_argument(
        "--report-segments",
        type=Path,
        default=None,
        help=(
            "Optional hash-bound normalized report segments. Explicit parent-heading "
            "plus exact row recovery is enabled only when this sidecar is valid."
        ),
    )
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


def compact_space(value: object) -> str:
    return " ".join(str(value or "").split())


def context_free_metric_variants(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return exact-match metric variants with only query-context removed.

    The primary effective metric remains the first and preferred variant.  A
    second variant is permitted solely to remove an already-resolved ticker, a
    trailing date/endpoint phrase, or the leading query descriptor ``Số dư``.
    The latter describes the requested balance rather than changing the line
    item; it is retained in the primary form and only used when a raw row
    exactly matches the shorter form.  Accounting modifiers such as ``tổng``
    and ``ngắn hạn`` are deliberately untouched: removing them could turn a
    subtotal or a different balance-sheet line into a false positive.
    """
    original = compact_space(item.get("effective_metric"))
    original_tokens = metric_tokens(original)
    if not original_tokens:
        return []
    variants = [
        {
            "matched_metric": original,
            "tokens": original_tokens,
            "policy": "exact_raw_v2_metric_token_sequence_v1",
            "removed_context": [],
        }
    ]
    stripped = original
    removed: list[str] = []
    plan = item.get("question_plan") or {}
    plan_has_year = any(isinstance(value, int) for value in plan.get("years") or [])
    for pattern in TRAILING_PERIOD_PATTERNS:
        match = pattern.search(stripped)
        if match is not None:
            removed.append(compact_space(match.group(0)))
            stripped = stripped[: match.start()]
            break
    for ticker in sorted(
        {str(value).strip() for value in plan.get("tickers") or [] if str(value).strip()},
        key=len,
        reverse=True,
    ):
        pattern = re.compile(rf"(?<!\w){re.escape(ticker)}(?!\w)", re.IGNORECASE)
        match = pattern.search(stripped)
        if match is not None:
            removed.append(match.group(0))
            stripped = pattern.sub(" ", stripped)
    entity_match = TRAILING_PARENT_ENTITY_RE.search(stripped)
    if entity_match is not None:
        removed.append(compact_space(entity_match.group(0)))
        stripped = stripped[: entity_match.start()]
    stripped = LEADING_PUNCTUATION_RE.sub("", stripped)
    endpoint_match = LEADING_PERIOD_DESCRIPTOR_RE.search(stripped)
    if endpoint_match is not None and plan_has_year:
        removed.append(compact_space(endpoint_match.group(0)))
        stripped = stripped[endpoint_match.end() :]
    balance_match = LEADING_BALANCE_DESCRIPTOR_RE.search(stripped)
    if balance_match is not None:
        removed.append(compact_space(balance_match.group(0)))
        stripped = stripped[balance_match.end() :]
    stripped = LEADING_PUNCTUATION_RE.sub("", stripped)
    endpoint_match = LEADING_PERIOD_DESCRIPTOR_RE.search(stripped)
    if endpoint_match is not None and plan_has_year:
        removed.append(compact_space(endpoint_match.group(0)))
        stripped = stripped[endpoint_match.end() :]
    stripped = compact_space(stripped)
    stripped_tokens = metric_tokens(stripped)
    # A one-token fallback after context removal (for example merely ``tiền``)
    # would be too broad for automatic source discovery unless it was already
    # the original metric, which is represented above.
    if (
        stripped_tokens
        and stripped.casefold() != original.casefold()
        and len(stripped_tokens) >= 2
    ):
        variants.append(
            {
                "matched_metric": stripped,
                "tokens": stripped_tokens,
                "policy": "exact_raw_v2_metric_context_stripped_token_sequence_v1",
                "removed_context": removed,
            }
        )
    return variants


def contains_token_sequence(tokens: list[str], phrase: list[str]) -> bool:
    return bool(phrase) and any(
        tokens[index : index + len(phrase)] == phrase
        for index in range(max(0, len(tokens) - len(phrase) + 1))
    )


def context_axis_metric_variant(
    question: str,
    table: dict[str, Any],
    row: list[Any],
    *,
    original_metric: str,
) -> dict[str, Any] | None:
    """Bind a source parent heading and a child row, without inferring values.

    Some note tables encode the requested metric in an explicit parent heading
    and the requested category in the exact row (for example ``Cho vay khách
    hàng`` → ``Theo ngành nghề`` → ``Thương mại``).  The source parent and
    row must each occur as contiguous token sequences in the question.  This
    is a recovery of source hierarchy, not semantic similarity: any missing or
    non-exact axis simply returns ``None``.
    """
    segment = table.get("report_segment") or {}
    parent = compact_space(segment.get("source_parent_heading"))
    child = compact_space(segment.get("source_heading"))
    label = v4.row_label([str(value) for value in row])
    parent_tokens = metric_tokens(parent)
    row_tokens = metric_tokens(label)
    question_tokens = metric_tokens(question)
    if (
        len(parent_tokens) < 2
        or len(row_tokens) < 2
        or not (set(parent_tokens) & FINANCIAL_PARENT_TOKENS)
        or not contains_token_sequence(question_tokens, parent_tokens)
        or not contains_token_sequence(question_tokens, row_tokens)
    ):
        return None
    return {
        "original_metric": original_metric,
        "matched_metric": parent + " | " + label,
        "tokens": parent_tokens + row_tokens,
        "policy": "exact_raw_v2_source_parent_and_row_token_sequence_v1",
        "removed_context": [],
        "context_evidence": {
            "source_context_sha256": segment.get("source_context_sha256"),
            "parent_heading": parent,
            "source_heading": child,
            "row_label": label,
        },
    }


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
    *,
    uid: str,
    ticker: str,
    report_year: int | None,
    scope: str,
    row_index: int,
    row: list[Any],
    metric_variant: dict[str, Any],
    bundle_inclusion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an exact-row candidate without inheriting a stale Top-K preview.

    A retrieved table can contain a different projected value row from the raw
    row discovered here.  Its former ``one_line_summary`` must therefore never
    be carried forward: it would display a correct UID next to wrong evidence.
    Keep the source description deliberately compact and entirely derived from
    immutable metadata and the exact V2 row.
    """
    source_row = v4.v3.source_row_text(row)
    identity = " | ".join(
        value
        for value in (
            str(ticker or "").strip(),
            "" if report_year is None else str(report_year),
            str(scope or "").strip(),
        )
        if value
    )
    context_evidence = metric_variant.get("context_evidence") or {}
    source_context = compact_space(context_evidence.get("parent_heading"))
    direct_evidence = "VALUE: " + source_row
    if source_context:
        # ``||`` is the V3 evidence-field delimiter.  It lets structure
        # validation isolate the exact VALUE row while retaining the explicit
        # source parent as separate context evidence.
        direct_evidence = "CONTEXT: " + source_context + " || " + direct_evidence
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
        # This metric is either the original effective metric or a narrowly
        # audited context-free form.  V4 still proves it against the exact raw
        # row, and separately binds the stripped period through the header.
        "effective_metric": metric_variant["matched_metric"],
        "best_row_index": row_index,
        "direct_evidence": direct_evidence,
        # This intentionally replaces any retrieved preview for the same UID.
        # It is a display/audit field only, not a fact used by the reviewer.
        "one_line_summary": (
            f"Nguồn raw V2: {identity or uid}. Hàng exact: {source_row}"
        ),
        "evidence_features": {
            "row_score": 1.0,
            "metric_overlap": 1.0,
            "question_overlap": 1.0,
            "numeric": True,
        },
        "source_discovery": {
            "policy": metric_variant["policy"],
            "row_index": row_index,
            "source_row": [str(value) for value in row],
            "metric_match": {
                "original_metric": metric_variant["original_metric"],
                "matched_metric": metric_variant["matched_metric"],
                "removed_context": list(metric_variant["removed_context"]),
            },
            **(
                {"context_evidence": context_evidence}
                if context_evidence
                else {}
            ),
            **(
                {"bundle_table_origin": "direct_metadata_support_v1"}
                if (bundle_inclusion or {}).get("direct_metadata_support")
                else {}
            ),
        },
    }


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    output = args.output.resolve()
    if output.parent != bundle:
        raise ValueError("Direct EvidenceSet output must reside in the review bundle")
    context_path = (
        args.evidence_context or bundle / "tables_evidence_context_v3.jsonl"
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
    requested_segments = args.report_segments
    default_segments = bundle / "report_segments_v1.jsonl"
    segment_path = requested_segments or default_segments
    segment_manifest: dict[str, Any] | None = None
    segments: dict[str, dict[str, Any]] = {}
    if requested_segments is not None and not segment_path.is_file():
        raise FileNotFoundError(segment_path)
    if segment_path.is_file():
        segment_path = segment_path.resolve()
        segment_manifest = validate_report_segment_sidecar(bundle, segment_path)
        segments = {
            str(row.get("internal_table_uid") or ""): row
            for row in load_jsonl(segment_path)
        }
        if "" in segments or len(segments) != int(segment_manifest.get("segment_count") or -1):
            raise ValueError("Report-segment UID/count contract is invalid")
    tables = {
        uid: {**base, **structures[uid]}
        for uid, base in base_tables.items()
        if uid in structures
    }
    unknown_segments = sorted(set(segments) - set(tables))
    if unknown_segments:
        raise RuntimeError("Report-segment sidecar has an unknown table UID: " + unknown_segments[0])
    for uid, segment in segments.items():
        tables[uid]["report_segment"] = segment
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
        metric_variants = context_free_metric_variants(item)
        variants_by_tokens = {
            tuple(variant["tokens"]): {
                **variant,
                "original_metric": compact_space(item.get("effective_metric")),
            }
            for variant in metric_variants
        }
        matches_by_uid: dict[str, list[dict[str, Any]]] = defaultdict(list)
        tickers = {str(value) for value in plan.get("tickers") or [] if str(value)}
        years = {int(value) for value in plan.get("years") or [] if isinstance(value, int)}
        requested_scope = str(plan.get("scope") or "")
        indexed_target_tables = [
            (uid, table)
            for (ticker, report_year, scope), indexed_tables in table_by_metadata.items()
            if (not tickers or ticker in tickers)
            and (not years or report_year in years)
            and (not requested_scope or scope == requested_scope)
            for uid, table in indexed_tables
        ]
        if variants_by_tokens:
            for uid, table in indexed_target_tables:
                context = contexts[uid]
                if str((context.get("quality") or {}).get("status") or "") != "review_ready":
                    continue
                for row_index, row in enumerate(table.get("rows") or []):
                    label = v4.row_label([str(value) for value in row])
                    metric_variant = variants_by_tokens.get(tuple(metric_tokens(label)))
                    if (
                        metric_variant is None
                        or not row_endpoint_compatible(str(item.get("question") or ""), label)
                    ):
                        continue
                    candidate = base_candidate(
                        uid=uid,
                        ticker=str(table.get("ticker") or ""),
                        report_year=table.get("report_year"),
                        scope=str(table.get("scope") or ""),
                        row_index=row_index,
                        row=row,
                        metric_variant=metric_variant,
                        bundle_inclusion=table.get("bundle_inclusion"),
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
        # Source hierarchy recovery is deliberately a fallback.  An ordinary
        # exact row label always wins; this path is only for a table whose
        # metric is explicitly in the parent heading and category in its row.
        if not matches_by_uid and segments:
            original_metric = compact_space(item.get("effective_metric"))
            for uid, table in indexed_target_tables:
                context = contexts[uid]
                if str((context.get("quality") or {}).get("status") or "") != "review_ready":
                    continue
                for row_index, row in enumerate(table.get("rows") or []):
                    metric_variant = context_axis_metric_variant(
                        str(item.get("question") or ""),
                        table,
                        row,
                        original_metric=original_metric,
                    )
                    label = v4.row_label([str(value) for value in row])
                    if (
                        metric_variant is None
                        or not row_endpoint_compatible(str(item.get("question") or ""), label)
                    ):
                        continue
                    candidate = base_candidate(
                        uid=uid,
                        ticker=str(table.get("ticker") or ""),
                        report_year=table.get("report_year"),
                        scope=str(table.get("scope") or ""),
                        row_index=row_index,
                        row=row,
                        metric_variant=metric_variant,
                        bundle_inclusion=table.get("bundle_inclusion"),
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
                "metric_variants": [
                    {
                        key: variant[key]
                        for key in ("matched_metric", "policy", "removed_context")
                    }
                    for variant in metric_variants
                ],
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
        "report_segments_file": None if segment_manifest is None else segment_path.name,
        "report_segments_sha256": None if segment_manifest is None else sha256_file(segment_path),
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
