#!/usr/bin/env python3
"""Autonomous source-aware review on canonical raw-HTML table context.

V4 is intentionally a machine-silver pipeline, not a human-label generator.
It runs independent retrieval, semantic, evidence, metadata and critic views
over candidates that have survived two deterministic source contracts:

1. exact V2 raw-HTML row binding; and
2. V1 canonical header/provenance quality gate.

``machine_calibrated`` here means a conservative autonomous silver label.  It
never becomes ``human_verified``.  Candidates with unclear OCR structure are
quarantined rather than guessed or used for self-training.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from finance_query.evidence_context import validate_evidence_context_sidecar
from finance_query.plan_overrides import apply_plan_overrides, validate_plan_overrides
from finance_query.table_structure import validate_structure_sidecar

import auto_review_bundle_v31 as v31


v3 = v31.v3
END_RE = re.compile(r"cuối\s+năm|31\s*[/.-]\s*12|cuối\s+kỳ", re.IGNORECASE)
START_RE = re.compile(r"đầu\s+năm|0?1\s*[/.-]\s*0?1|đầu\s+kỳ", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, default=None)
    parser.add_argument("--quarantine-output", type=Path, default=None)
    parser.add_argument(
        "--question-plan-overrides",
        type=Path,
        default=None,
        help="Optional hash-bound local plan-override sidecar; source bundle stays immutable.",
    )
    parser.add_argument("--min-agreement", type=float, default=0.67)
    parser.add_argument("--silver-threshold", type=float, default=0.84)
    parser.add_argument("--adjacent-min-token-coverage", type=float, default=0.85)
    parser.add_argument("--adjacent-min-bigram-ratio", type=float, default=0.45)
    return parser.parse_args()


def by_uid(rows: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        uid = str(row.get("internal_table_uid") or "")
        if not uid:
            raise ValueError(f"{name} row lacks internal_table_uid")
        if uid in output:
            raise ValueError(f"Duplicate UID in {name}: {uid}")
        output[uid] = row
    return output


def period_requirement(question: str) -> str:
    if END_RE.search(question):
        return "end"
    if START_RE.search(question):
        return "start"
    return "unspecified"


def matching_period_columns(context: dict[str, Any], requirement: str) -> set[int]:
    if requirement == "unspecified":
        return set()
    expected = ("số cuối", "cuối năm", "cuối kỳ", "31/12") if requirement == "end" else (
        "số đầu",
        "đầu năm",
        "đầu kỳ",
        "1/1",
        "01/01",
    )
    return {
        int(column["column_index"])
        for column in (context.get("canonical_headers") or {}).get("columns") or []
        if any(marker in str(column.get("source_label") or "").casefold() for marker in expected)
    }


def question_years(question: str) -> list[int]:
    """Return distinct explicitly mentioned calendar years, in source order."""
    output: list[int] = []
    for value in YEAR_RE.findall(question):
        year = int(value)
        if year not in output:
            output.append(year)
    return output


def matching_year_columns(context: dict[str, Any], year: int) -> set[int]:
    """Find source-header columns explicitly labelled with one requested year.

    A report's metadata year is deliberately *not* sufficient to bind a
    comparison column: the raw table header itself has to name the requested
    year.  This prevents an adjacent/current-period value being treated as a
    historical value merely because it came from the right annual report.
    """
    token = str(year)
    return {
        int(column["column_index"])
        for column in (context.get("canonical_headers") or {}).get("columns") or []
        if token in str(column.get("source_label") or "")
    }


def matching_current_report_columns(context: dict[str, Any]) -> set[int]:
    """Find an unambiguously labelled current-period column.

    This fallback is only used when the question year equals the candidate
    report year and no header names that year.  Labels such as ``Năm nay`` are
    still raw source evidence; unlabeled numeric columns are never guessed.
    """
    markers = ("năm nay", "kỳ này", "hiện tại", "current year", "current period")
    return {
        int(column["column_index"])
        for column in (context.get("canonical_headers") or {}).get("columns") or []
        if any(marker in str(column.get("source_label") or "").casefold() for marker in markers)
    }


def bind_value_row(
    item: dict[str, Any], candidate: dict[str, Any], table: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Bind a projected V3 value row to V2 row and, when possible, its period columns."""
    validation = candidate.get("structure_validation") or {}
    row_index = validation.get("row_index")
    rows = table.get("rows") or []
    profiles = {
        int(profile["row_index"]): profile
        for profile in context.get("row_profiles") or []
        if isinstance(profile.get("row_index"), int)
    }
    if not isinstance(row_index, int) or not 0 <= row_index < len(rows):
        return {"status": "unbound", "reason": "exact V2 evidence row is unavailable"}
    profile = profiles.get(row_index) or {}
    if profile.get("role") != "data":
        return {
            "status": "unbound",
            "row_index": row_index,
            "reason": "projected evidence row is not a canonical data row",
        }
    numeric_columns = [int(column) for column in profile.get("numeric_columns") or []]
    question = str(item.get("question") or "")
    requirement = period_requirement(question)
    period_columns = matching_period_columns(context, requirement)
    years = question_years(question)
    year_columns: set[int] = set()
    if len(years) == 1:
        year_columns = matching_year_columns(context, years[0])

    # Prefer an explicitly named year.  If the wording also asks for an
    # opening/closing date, both source-header constraints must identify the
    # same unique cell.
    selected = sorted(set(numeric_columns) & year_columns)
    selection_reason = "explicit_year_header" if selected else ""
    if requirement != "unspecified" and selected:
        endpoint_selected = sorted(set(selected) & period_columns)
        if len(endpoint_selected) == 1:
            selected = endpoint_selected
            selection_reason = "explicit_year_and_endpoint_header"
        elif endpoint_selected:
            selected = endpoint_selected
        elif period_columns:
            return {
                "status": "ambiguous_period_column",
                "row_index": row_index,
                "numeric_columns": numeric_columns,
                "matching_year_columns": sorted(year_columns),
                "matching_period_columns": sorted(period_columns),
                "reason": "year and opening/closing raw headers do not identify one common cell",
            }

    if not selected and requirement != "unspecified":
        selected = sorted(set(numeric_columns) & period_columns)
        selection_reason = "opening_or_closing_header" if selected else ""

    # A source table can use ``Năm nay`` / ``Kỳ này`` rather than an absolute
    # year.  Use it only if the candidate's report year exactly agrees with
    # the sole question year, retaining a concrete raw-header binding.
    if (
        not selected
        and len(years) == 1
        and int(candidate.get("report_year") or 0) == years[0]
    ):
        selected = sorted(set(numeric_columns) & matching_current_report_columns(context))
        selection_reason = "current_period_header_matches_report_year" if selected else ""

    if len(selected) == 1:
        column_index = selected[0]
        headers = (context.get("canonical_headers") or {}).get("columns") or []
        header = next(
            (column for column in headers if int(column.get("column_index") or -1) == column_index),
            {},
        )
        provenance = ((table.get("cell_provenance") or [])[row_index] or [])[column_index]
        return {
            "status": "cell_bound",
            "row_index": row_index,
            "column_index": column_index,
            "column_label": header.get("source_label"),
            "value": str(rows[row_index][column_index]),
            "source_cell": provenance,
            "binding_reason": selection_reason,
        }

    # One numeric source cell needs no period inference; binding it is safer
    # than retaining a row-only answer and is independently reproducible from
    # its exact raw coordinate.
    if requirement == "unspecified" and not years and len(numeric_columns) == 1:
        column_index = numeric_columns[0]
        headers = (context.get("canonical_headers") or {}).get("columns") or []
        header = next(
            (column for column in headers if int(column.get("column_index") or -1) == column_index),
            {},
        )
        provenance = ((table.get("cell_provenance") or [])[row_index] or [])[column_index]
        return {
            "status": "cell_bound",
            "row_index": row_index,
            "column_index": column_index,
            "column_label": header.get("source_label"),
            "value": str(rows[row_index][column_index]),
            "source_cell": provenance,
            "binding_reason": "only_numeric_source_cell",
        }
    if requirement == "unspecified" and not years:
        return {
            "status": "row_bound",
            "row_index": row_index,
            "numeric_columns": numeric_columns,
            "reason": "question does not request a uniquely bindable source period column",
        }
    return {
        "status": "ambiguous_period_column",
        "row_index": row_index,
        "numeric_columns": numeric_columns,
        "matching_year_columns": sorted(year_columns),
        "matching_period_columns": sorted(period_columns),
        "reason": "no unique raw-header year or period column can be bound",
    }


def candidate_assessment(
    item: dict[str, Any],
    candidate: dict[str, Any],
    table: dict[str, Any],
    context: dict[str, Any],
    token_gate: float,
    bigram_gate: float,
) -> dict[str, Any]:
    quality = context.get("quality") or {}
    grounding = v3.grounding(item, candidate, token_gate, bigram_gate)
    binding = bind_value_row(item, candidate, table, context)
    evidence = candidate.get("evidence_features") or {}
    source_ready = str(quality.get("status") or "") == "review_ready"
    exact_row = bool((candidate.get("structure_validation") or {}).get("validated"))
    row_bound = binding.get("status") in {"row_bound", "cell_bound"}
    semantic_score = float(grounding.get("quality") or 0.0)
    evidence_score = (
        0.55 * semantic_score
        + 0.30 * float(evidence.get("row_score") or 0.0)
        + 0.15 * float(row_bound)
    )
    source_score = float(quality.get("score") or 0.0)
    metadata_score = float(candidate.get("metadata_score") or 0.0)
    retrieval_score = max(
        v3.reciprocal(candidate.get("lexical_rank")),
        v3.reciprocal(candidate.get("dense_rank")),
    )
    reason_codes: list[str] = []
    if not source_ready:
        reason_codes.extend(str(code) for code in quality.get("reason_codes") or [])
    if not exact_row:
        reason_codes.append("exact_v2_row_unvalidated")
    if not grounding.get("guard_pass"):
        reason_codes.append("grounding_guard_failed")
    if not row_bound:
        reason_codes.append(str(binding.get("status") or "value_row_unbound"))
    return {
        "uid": str(candidate["internal_table_uid"]),
        "source_ready": source_ready,
        "exact_row": exact_row,
        "row_bound": row_bound,
        "source_score": source_score,
        "semantic_score": semantic_score,
        "evidence_score": evidence_score,
        "metadata_score": metadata_score,
        "retrieval_score": retrieval_score,
        "grounding": grounding,
        "value_binding": binding,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def pick(assessments: list[dict[str, Any]], key) -> dict[str, Any] | None:
    return max(assessments, key=key) if assessments else None


def autonomous_review_item(
    item: dict[str, Any],
    tables: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    token_gate: float,
    bigram_gate: float,
    min_agreement: float,
    silver_threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = list(item.get("candidates") or [])
    family = str((item.get("question_plan") or {}).get("family") or item.get("weak_family") or "")
    quarantine: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    candidate_by_uid = {str(candidate["internal_table_uid"]): candidate for candidate in candidates}
    for candidate in candidates:
        uid = str(candidate["internal_table_uid"])
        table = tables.get(uid)
        context = contexts.get(uid)
        if table is None or context is None:
            quarantine.append(
                {"id": int(item["id"]), "internal_table_uid": uid, "reason_codes": ["missing_v2_or_context"]}
            )
            continue
        assessment = candidate_assessment(
            item, candidate, table, context, token_gate, bigram_gate
        )
        assessments.append(assessment)
        if not assessment["source_ready"]:
            quarantine.append(
                {
                    "id": int(item["id"]),
                    "internal_table_uid": uid,
                    "reason_codes": assessment["reason_codes"],
                    "quality": context.get("quality"),
                }
            )

    eligible = [
        assessment
        for assessment in assessments
        if assessment["source_ready"]
        and assessment["exact_row"]
        and assessment["row_bound"]
        and assessment["grounding"].get("guard_pass")
    ]
    if not eligible:
        return (
            {
                "id": int(item["id"]),
                "question": item["question"],
                "family": family,
                "machine_candidate_uid": None,
                "machine_candidate_rank": None,
                "agent_votes": {},
                "agreement": 0.0,
                "machine_confidence": 0.0,
                "consensus_status": "needs_human",
                "review_reason": "No candidate survived raw-source, canonical-header, exact-row and grounding gates.",
                "machine_self_review": {
                    "protocol": "raw_v2_canonical_context_v1",
                    "training_eligible": False,
                    "candidate_assessments": assessments,
                },
            },
            quarantine,
        )

    retrieval = pick(eligible, lambda value: value["retrieval_score"])
    semantic = pick(eligible, lambda value: value["semantic_score"])
    evidence = pick(eligible, lambda value: value["evidence_score"])
    metadata = pick(eligible, lambda value: value["metadata_score"])
    source = pick(eligible, lambda value: value["source_score"])
    challenger = pick(
        eligible,
        lambda value: 0.60 * value["semantic_score"]
        + 0.30 * value["evidence_score"]
        + 0.10 * value["metadata_score"],
    )

    def uid(value: dict[str, Any] | None) -> str | None:
        return None if value is None else str(value["uid"])

    votes = {
        "retrieval_agent": uid(retrieval),
        "semantic_agent": uid(semantic),
        "evidence_agent": uid(evidence),
        "metadata_agent": uid(metadata),
        "source_agent": uid(source),
        "challenger_agent": uid(challenger),
    }
    vote_counts = Counter(value for value in votes.values() if value)
    chosen_uid = max(
        vote_counts,
        key=lambda value: (
            vote_counts[value],
            next(assessment["evidence_score"] for assessment in eligible if assessment["uid"] == value),
        ),
    )
    selected = next(assessment for assessment in eligible if assessment["uid"] == chosen_uid)
    ordered = sorted(
        eligible,
        key=lambda value: (
            0.55 * value["semantic_score"]
            + 0.35 * value["evidence_score"]
            + 0.10 * value["metadata_score"]
        ),
        reverse=True,
    )
    alternative = next((value for value in ordered if value["uid"] != chosen_uid), None)
    selected_score = (
        0.55 * selected["semantic_score"]
        + 0.35 * selected["evidence_score"]
        + 0.10 * selected["metadata_score"]
    )
    alternative_score = (
        0.55 * alternative["semantic_score"]
        + 0.35 * alternative["evidence_score"]
        + 0.10 * alternative["metadata_score"]
        if alternative
        else None
    )
    critic_accepts = alternative_score is None or selected_score - alternative_score >= 0.05
    votes["critic_agent"] = chosen_uid if critic_accepts else str(alternative["uid"])
    vote_counts = Counter(value for value in votes.values() if value)
    agreement = vote_counts[chosen_uid] / len(votes)
    confidence = min(
        1.0,
        0.28 * agreement
        + 0.25 * selected["semantic_score"]
        + 0.22 * selected["evidence_score"]
        + 0.15 * selected["source_score"]
        + 0.10 * selected["metadata_score"],
    )

    requested_period = period_requirement(str(item.get("question") or ""))
    period_complete = requested_period == "unspecified" or selected["value_binding"].get("status") == "cell_bound"
    fully_grounded_direct = (
        family == "direct_lookup"
        and selected["grounding"].get("token_coverage", 0.0) >= 0.75
        and bool((candidate_by_uid[chosen_uid].get("evidence_features") or {}).get("numeric"))
        and period_complete
    )
    if (
        fully_grounded_direct
        and agreement >= min_agreement
        and critic_accepts
        and confidence >= silver_threshold
    ):
        status = "machine_calibrated"
        reason = "Autonomous source/semantic/evidence/critic consensus passed all raw-V2 gates."
    elif selected["semantic_score"] >= 0.45 and selected["evidence_score"] >= 0.45:
        status = "machine_provisional"
        reason = "Candidate is source-grounded but does not meet conservative autonomous-silver gates."
    else:
        status = "needs_human"
        reason = "Autonomous agents could not establish sufficiently grounded evidence."

    candidate = candidate_by_uid[chosen_uid]
    return (
        {
            "id": int(item["id"]),
            "question": item["question"],
            "family": family,
            "machine_candidate_uid": chosen_uid,
            "machine_candidate_rank": int(candidate.get("rank") or 0),
            "machine_candidate_summary": candidate.get("one_line_summary"),
            "machine_candidate_direct_evidence": candidate.get("direct_evidence"),
            "structure_validation": candidate.get("structure_validation"),
            "machine_candidate_source": candidate.get("candidate_source"),
            "agent_votes": votes,
            "vote_counts": dict(vote_counts),
            "agreement": agreement,
            "machine_confidence": confidence,
            "consensus_status": status,
            "review_reason": reason,
            "machine_self_review": {
                "protocol": "raw_v2_canonical_context_v1",
                "training_eligible": status == "machine_calibrated",
                "critic_accepts": critic_accepts,
                "alternative_uid": None if alternative is None else alternative["uid"],
                "selected_value_binding": selected["value_binding"],
                "selected_assessment": selected,
                "candidate_assessments": assessments,
            },
        },
        quarantine,
    )


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest.get("error_count") or 0) != 0:
        raise RuntimeError("Refuse autonomous review: bundle contains retrieval errors.")
    structure_path = bundle / "tables_structured_v2.jsonl"
    context_path = args.evidence_context or bundle / "tables_evidence_context_v1.jsonl"
    validate_structure_sidecar(bundle, structure_path)
    validate_evidence_context_sidecar(bundle, structure_path, context_path)
    source_items = v3.load_jsonl(bundle / "review_items.jsonl")
    overrides = {}
    if args.question_plan_overrides is not None:
        overrides = validate_plan_overrides(
            source_items,
            v3.load_jsonl(args.question_plan_overrides.resolve()),
        )
    items = apply_plan_overrides(source_items, overrides)
    tables = by_uid(v3.load_jsonl(structure_path), "V2 structures")
    contexts = by_uid(v3.load_jsonl(context_path), "evidence contexts")
    validated, candidate_total = v3.attach_structure_validation(items, bundle)

    reviews: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    for item in items:
        review, rejected = autonomous_review_item(
            item,
            tables,
            contexts,
            args.adjacent_min_token_coverage,
            args.adjacent_min_bigram_ratio,
            args.min_agreement,
            args.silver_threshold,
        )
        review["effective_question_plan"] = item.get("question_plan") or {}
        review["question_plan_provenance"] = item.get("_question_plan_provenance") or {}
        reviews.append(review)
        quarantine.extend(rejected)
    v3.write_jsonl(args.output, reviews)
    if args.quarantine_output:
        v3.write_jsonl(args.quarantine_output, quarantine)
    print("Autonomous machine reviews:", args.output)
    print("Status counts:", dict(Counter(str(row["consensus_status"]) for row in reviews)))
    print("Exact V2 candidate rows:", f"{validated}/{candidate_total}")
    print("Quarantined candidates:", len(quarantine))
    if args.question_plan_overrides is not None:
        print("Applied source-bound plan overrides:", len(overrides))
    if args.quarantine_output:
        print("Quarantine audit:", args.quarantine_output)


if __name__ == "__main__":
    main()
