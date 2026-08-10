#!/usr/bin/env python3
"""Autonomous source-aware review on canonical raw-HTML table context.

V4 is intentionally a machine-silver pipeline, not a human-label generator.
It runs independent retrieval, semantic, evidence, metadata and critic views
over candidates that have survived two deterministic source contracts:

1. exact V2 raw-HTML row binding; and
2. V2 canonical header/provenance quality gate.

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

from finance_query.evidence_context import (  # noqa: E402
    AUTONOMOUS_REVIEW_PROTOCOL,
    validate_evidence_context_sidecar,
)
from finance_query.binding import row_label  # noqa: E402
from finance_query.corpus import infer_unit  # noqa: E402
from finance_query.execution import parse_decimal  # noqa: E402
from finance_query.plan_overrides import (
    apply_plan_overrides,
    canonical_sha256,
    validate_plan_overrides,
)
from finance_query.report_segments import validate_report_segment_sidecar
from finance_query.table_structure import sha256_file, validate_structure_sidecar

import auto_review_bundle_v31 as v31


v3 = v31.v3
END_RE = re.compile(r"cuối\s+năm|31\s*[/.-]\s*12|cuối\s+kỳ", re.IGNORECASE)
START_RE = re.compile(r"đầu\s+năm|0?1\s*[/.-]\s*0?1|đầu\s+kỳ", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
SELECTOR_TIE_EPSILON = 1e-12
# A note-reference cell is not part of a financial metric.  Keep this narrow:
# it accepts only a standalone Roman-numeral note prefix followed by a number,
# such as ``VI.06``.  It must not remove ordinary label text.
RAW_NOTE_REFERENCE_RE = re.compile(
    r"(?<!\w)[IVXLCDM]+\s*\.\s*\d+(?:\s*\([A-Za-z]\))?(?!\w)",
    re.IGNORECASE,
)
# Statement row codes (``XIII``, ``16.``, ``1.2.``) are not financial
# metric words. They can occur in their own source cell or before the textual
# label after V2 row serialization. Do not treat an ordinary word as a code:
# a code must have an explicit structural separator.
RAW_STRUCTURAL_ROW_CODE_RE = re.compile(
    r"^\s*(?:(?:[IVXLCDM]+|\d+(?:\.\d+)*|[A-Za-z])\s*(?:[.)]|>)\s*)+",
    re.IGNORECASE,
)
STRICT_FINANCIAL_TOKEN_EXPANSIONS = {
    # This is a fixed Vietnamese accounting abbreviation, not a learned
    # similarity rule. It permits source ``TNDN`` to match the same fully
    # spelled-out source/question metric and nothing else.
    "tndn": ("thuế", "thu", "nhập", "doanh", "nghiệp"),
}
STRICT_TIEBREAK_MIN_SEMANTIC = 0.90
STRICT_TIEBREAK_MIN_EVIDENCE = 0.85
DIRECT_EVIDENCE_SCHEMA_VERSION = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, default=None)
    parser.add_argument(
        "--report-segments",
        type=Path,
        default=None,
        help=(
            "Optional hash-bound report-segment sidecar. It can supply only "
            "source-derived heading/unit navigation metadata; raw V2 rows and "
            "headers remain the sole evidence."
        ),
    )
    parser.add_argument("--quarantine-output", type=Path, default=None)
    parser.add_argument(
        "--question-plan-overrides",
        type=Path,
        default=None,
        help="Optional hash-bound local plan-override sidecar; source bundle stays immutable.",
    )
    parser.add_argument(
        "--direct-evidence",
        type=Path,
        default=None,
        help=(
            "Optional hash-bound raw-V2 direct-source-discovery sidecar. "
            "It supplements candidate recall but does not create labels by itself."
        ),
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


def validate_direct_evidence_sidecar(
    bundle: Path,
    sidecar: Path,
    context_path: Path,
    *,
    question_plan_overrides: Path | None,
) -> list[dict[str, Any]]:
    """Accept only an immutable-input-bound V2 direct-source sidecar."""
    if sidecar.parent != bundle:
        raise ValueError("Direct evidence sidecar must reside in the review bundle")
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not sidecar.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Direct evidence sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != DIRECT_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("Unsupported direct evidence sidecar schema")
    expected_paths = {
        "bundle_review_items_sha256": bundle / "review_items.jsonl",
        "bundle_tables_sha256": bundle / "tables.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
        "evidence_context_sha256": context_path,
    }
    for key, path in expected_paths.items():
        if str(manifest.get(key) or "") != sha256_file(path):
            raise ValueError(f"Direct evidence manifest does not match {path.name}")
    if str(manifest.get("evidence_context_file") or "") != context_path.name:
        raise ValueError("Direct evidence manifest names a different canonical context")
    expected_override_file = (
        None if question_plan_overrides is None else question_plan_overrides.name
    )
    expected_override_hash = (
        None if question_plan_overrides is None else sha256_file(question_plan_overrides)
    )
    if manifest.get("question_plan_override_file") != expected_override_file:
        raise ValueError("Direct evidence sidecar was built with different plan overrides")
    if manifest.get("question_plan_overrides_sha256") != expected_override_hash:
        raise ValueError("Direct evidence plan-override hash does not match")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError("Direct evidence sidecar hash does not match its manifest")
    rows = v3.load_jsonl(sidecar)
    if int(manifest.get("question_count") or -1) != len(rows):
        raise ValueError("Direct evidence sidecar question count does not match manifest")
    return rows


def apply_direct_source_discovery(
    items: list[dict[str, Any]], sidecar_rows: list[dict[str, Any]]
) -> tuple[int, int]:
    """Merge direct raw-row candidates, retaining ordinary Top-K provenance.

    The discovery sidecar contains at most one candidate per table/question.
    It may replace a retrieved candidate's projected evidence for the same
    table UID with an exact raw V2 row, while preserving retrieval ranks for
    audit.  Multi-row same-table matches are excluded by the builder rather
    than resolved here by list order.
    """
    discoveries: dict[int, dict[str, Any]] = {}
    for row in sidecar_rows:
        qid = int(row.get("id") or -1)
        if qid in discoveries:
            raise ValueError(f"Duplicate direct evidence sidecar record for Q{qid}")
        discoveries[qid] = row
    replaced_or_added = 0
    ambiguous = 0
    for item in items:
        plan = item.get("question_plan") or {}
        if str(plan.get("family") or item.get("weak_family") or "") != "direct_lookup":
            continue
        qid = int(item["id"])
        discovered = discoveries.get(qid)
        if discovered is None:
            raise ValueError(f"Direct evidence sidecar omits direct-lookup Q{qid}")
        if int(discovered.get("schema_version") or 0) != DIRECT_EVIDENCE_SCHEMA_VERSION:
            raise ValueError(f"Q{qid}: unsupported direct evidence record schema")
        if str(discovered.get("family") or "") != "direct_lookup":
            raise ValueError(f"Q{qid}: direct evidence family differs from effective plan")
        if str(discovered.get("effective_question_plan_sha256") or "") != canonical_sha256(plan):
            raise ValueError(f"Q{qid}: direct evidence plan hash differs from effective plan")
        ambiguous += len(discovered.get("ambiguous_same_table_rows") or [])
        existing = {
            str(candidate["internal_table_uid"]): dict(candidate)
            for candidate in item.get("candidates") or []
        }
        seen: set[str] = set()
        for candidate in discovered.get("candidates") or []:
            uid = str(candidate.get("internal_table_uid") or "")
            source = candidate.get("source_discovery") or {}
            if not uid or uid in seen:
                raise ValueError(f"Q{qid}: invalid or duplicate direct source candidate UID")
            seen.add(uid)
            if str(candidate.get("candidate_source") or "") != "raw_v2_direct_source_discovery":
                raise ValueError(f"Q{qid}: direct source candidate has an invalid source marker")
            if str(source.get("policy") or "") != "exact_raw_v2_metric_token_sequence_v1":
                raise ValueError(f"Q{qid}: direct source candidate has an invalid discovery policy")
            row_index = candidate.get("best_row_index")
            if not isinstance(row_index, int) or isinstance(row_index, bool):
                raise ValueError(f"Q{qid}: direct source candidate lacks a raw row index")
            prior = existing.get(uid, {})
            # Retain only the retrieval ordering fields from Top-K.  All row
            # evidence/provenance is supplied by the source-discovery record.
            merged = {
                **prior,
                **candidate,
                "rank": prior.get("rank", candidate.get("rank")),
                "lexical_rank": prior.get("lexical_rank", candidate.get("lexical_rank")),
                "dense_rank": prior.get("dense_rank", candidate.get("dense_rank")),
            }
            existing[uid] = merged
            replaced_or_added += 1
        item["candidates"] = list(existing.values())
    extra_ids = sorted(set(discoveries) - {int(item["id"]) for item in items})
    if extra_ids:
        raise ValueError(f"Direct evidence sidecar has IDs absent from bundle: {extra_ids[:10]}")
    return replaced_or_added, ambiguous


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
    identity = raw_metric_identity(item, candidate, table, binding)
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
        "raw_metric_identity": identity,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def pick(assessments: list[dict[str, Any]], key) -> dict[str, Any] | None:
    return max(assessments, key=key) if assessments else None


def raw_metric_identity(
    item: dict[str, Any],
    candidate: dict[str, Any],
    table: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    """Compare the planned candidate metric with its raw V2 value-row label.

    V3's regular grounding intentionally uses projected evidence for broad
    retrieval review. A special autonomous tie-break needs a stricter fact:
    the non-numeric label of the exact raw V2 value row must contain the same
    significant token sequence as the candidate's planned effective metric.
    This rejects near neighbours such as ``Thặng dư vốn cổ phần`` for a
    question asking ``Vốn cổ phần``. It is deliberately too strict for normal
    recall and is never used to make a partial/complex question executable.
    """
    row_index = binding.get("row_index")
    rows = table.get("rows") or []
    if not isinstance(row_index, int) or not 0 <= row_index < len(rows):
        return {
            "exact": False,
            "reason": "value_row_unavailable",
            "metric_tokens": [],
            "row_tokens": [],
        }
    metric = v3.metric_text(item, candidate)
    def identity_tokens(value: str) -> tuple[list[str], bool]:
        output: list[str] = []
        used_financial_acronym = False
        for token in v3.token_sequence(value):
            if token.isdecimal():
                continue
            expansion = STRICT_FINANCIAL_TOKEN_EXPANSIONS.get(token)
            used_financial_acronym = used_financial_acronym or expansion is not None
            if token == "tndn" and output[-1:] == ["thuế"]:
                # ``thuế TNDN`` is the standard shortened spelling of
                # ``thuế thu nhập doanh nghiệp``; do not duplicate ``thuế``.
                output.extend(expansion[1:])
            else:
                output.extend(expansion or (token,))
        return output, used_financial_acronym

    metric_tokens, metric_uses_acronym = identity_tokens(metric)
    label = row_label([str(value) for value in rows[row_index]])
    ignored_note_references = RAW_NOTE_REFERENCE_RE.findall(label)
    without_note_references = RAW_NOTE_REFERENCE_RE.sub("", label)
    ignored_structural_row_code = RAW_STRUCTURAL_ROW_CODE_RE.findall(
        without_note_references
    )
    identity_label = RAW_STRUCTURAL_ROW_CODE_RE.sub("", without_note_references)
    row_tokens, row_uses_acronym = identity_tokens(identity_label)
    used_financial_acronym = metric_uses_acronym or row_uses_acronym
    exact = bool(metric_tokens) and metric_tokens == row_tokens
    return {
        "exact": exact,
        "metric": metric,
        "raw_row_label": label,
        "identity_row_label": identity_label,
        "ignored_note_references": ignored_note_references,
        "ignored_structural_row_code": ignored_structural_row_code,
        "expanded_financial_acronym": used_financial_acronym,
        "metric_tokens": metric_tokens,
        "row_tokens": row_tokens,
        "reason": (
            "exact_significant_token_sequence"
            if exact
            and not ignored_note_references
            and not ignored_structural_row_code
            and not used_financial_acronym
            else "exact_after_ignoring_standalone_note_reference"
            if (
                exact
                and ignored_note_references
                and not ignored_structural_row_code
                and not used_financial_acronym
            )
            else "exact_after_ignoring_structural_row_code_or_expanding_financial_acronym"
            if exact
            else "metric_row_label_differs"
        ),
    }


def selector_has_tied_best(assessments: list[dict[str, Any]], key) -> bool:
    """Return true only if a selector cannot distinguish its best candidate."""
    if len(assessments) < 2:
        return False
    scores = sorted((float(key(value)) for value in assessments), reverse=True)
    return abs(scores[0] - scores[1]) <= SELECTOR_TIE_EPSILON


def raw_source_unit(table: dict[str, Any]) -> str | None:
    """Resolve only the unit explicitly available in the raw source context."""
    hinted = table.get("unit_hint")
    if isinstance(hinted, str) and hinted:
        return hinted
    segment = table.get("report_segment") or {}
    context = " ".join(
        [
            # These labels were extracted from V2 canonical source headers and
            # are hash-bound to this exact grid.  They make unit lookup robust
            # to OCR/HTML debris in broader surrounding prose; they do not
            # supply a metric, value, or period binding.
            " ".join(str(value) for value in segment.get("unit_labels") or []),
            str(table.get("context_before") or ""),
            str((table.get("context_trace") or {}).get("source_title") or ""),
            " ".join((table.get("context_trace") or {}).get("unit_labels") or []),
        ]
    )
    rows = table.get("rows") or []
    return infer_unit(context, "\n".join(" | ".join(map(str, row)) for row in rows))


def reliable_raw_number(value: object) -> str | None:
    """Accept exactly the number form accepted later by the execution ledger."""
    parsed = parse_decimal(str(value))
    if parsed.value is None or any(
        warning != "percent_value_not_scaled" for warning in parsed.warnings
    ):
        return None
    return parsed.value


def equivalent_critic_alternatives(
    selected: dict[str, Any],
    eligible: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    selected_score: float,
) -> list[dict[str, Any]] | None:
    """Prove that every critic-near-tie has the same executable raw answer.

    A critic normally vetoes a direct candidate whose closest alternate is
    within 0.05 of its content score.  That is correct when the alternate can
    lead to a different answer.  It is not an ambiguity when *each* such
    alternate is independently cell-bound and carries the same parsed numeric
    value in the same declared source unit.  The return value is deliberately
    an audit trail rather than a boolean.
    """
    selected_binding = selected.get("value_binding") or {}
    if str(selected_binding.get("status") or "") != "cell_bound":
        return None
    selected_table = tables.get(str(selected.get("uid") or ""))
    if selected_table is None:
        return None
    selected_value = reliable_raw_number(selected_binding.get("value"))
    selected_unit = raw_source_unit(selected_table)
    if selected_value is None or not selected_unit:
        return None

    equivalents: list[dict[str, Any]] = []
    for alternative in eligible:
        if alternative.get("uid") == selected.get("uid"):
            continue
        alternative_score = (
            0.55 * float(alternative["semantic_score"])
            + 0.35 * float(alternative["evidence_score"])
            + 0.10 * float(alternative["metadata_score"])
        )
        if selected_score - alternative_score >= 0.05:
            continue
        binding = alternative.get("value_binding") or {}
        table = tables.get(str(alternative.get("uid") or ""))
        if str(binding.get("status") or "") != "cell_bound" or table is None:
            return None
        value = reliable_raw_number(binding.get("value"))
        unit = raw_source_unit(table)
        if value != selected_value or unit != selected_unit:
            return None
        equivalents.append(
            {
                "internal_table_uid": str(alternative["uid"]),
                "raw_row_label": (alternative.get("raw_metric_identity") or {}).get(
                    "raw_row_label"
                ),
                "row_index": binding.get("row_index"),
                "column_index": binding.get("column_index"),
                "column_label": binding.get("column_label"),
                "raw_value": binding.get("value"),
                "parsed_value": value,
                "source_unit": unit,
                "source_cell": binding.get("source_cell"),
            }
        )
    return equivalents or None


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
                    "protocol": AUTONOMOUS_REVIEW_PROTOCOL,
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
    ordinary_silver = (
        fully_grounded_direct
        # A high overlap score is useful for review ordering, but it is not a
        # license to turn a related row into a training label. The selected
        # raw V2 row must name the planned metric exactly; otherwise a newly
        # recovered header can make an unrelated but nearby row look highly
        # confident (for example a construction advance versus PPE cost).
        and bool(selected["raw_metric_identity"].get("exact"))
        and agreement >= min_agreement
        and critic_accepts
        and confidence >= silver_threshold
    )
    # Metadata and source quality are hard eligibility gates, but they cannot
    # select between two equally-valid candidates when their scores tie. The
    # legacy ``max`` selector still emitted an arbitrary UID in that case,
    # which could suppress an otherwise exact direct answer. Resolve only this
    # narrow failure mode: three content selectors and the critic must agree,
    # the raw V2 value-row label must be an exact metric identity, and both
    # nondiscriminative selectors must demonstrably have a tied best score.
    content_selectors = ("semantic_agent", "evidence_agent", "challenger_agent")
    strict_identity_tiebreak = (
        fully_grounded_direct
        and critic_accepts
        and bool(selected["raw_metric_identity"].get("exact"))
        and selected["semantic_score"] >= STRICT_TIEBREAK_MIN_SEMANTIC
        and selected["evidence_score"] >= STRICT_TIEBREAK_MIN_EVIDENCE
        and selected["source_score"] >= 1.0 - SELECTOR_TIE_EPSILON
        and selected["metadata_score"] >= 1.0 - SELECTOR_TIE_EPSILON
        and all(votes.get(name) == chosen_uid for name in content_selectors)
        and selector_has_tied_best(eligible, lambda value: value["source_score"])
        and selector_has_tied_best(eligible, lambda value: value["metadata_score"])
    )
    # A close critic alternative normally prevents silver.  Treat the critic
    # as resolved only if every near-tied alternative independently produces
    # the exact same parsed value in the same explicit source unit.  This is
    # stricter than metric similarity: it preserves the complete list of raw
    # rows/cells whose equality made the answer unambiguous.
    equivalent_alternatives = equivalent_critic_alternatives(
        selected, eligible, tables, selected_score
    )
    strict_equivalent_critic_answer = (
        fully_grounded_direct
        and not critic_accepts
        and bool(selected["raw_metric_identity"].get("exact"))
        and selected["semantic_score"] >= STRICT_TIEBREAK_MIN_SEMANTIC
        and selected["evidence_score"] >= STRICT_TIEBREAK_MIN_EVIDENCE
        and selected["source_score"] >= 1.0 - SELECTOR_TIE_EPSILON
        and selected["metadata_score"] >= 1.0 - SELECTOR_TIE_EPSILON
        and all(votes.get(name) == chosen_uid for name in content_selectors)
        and selector_has_tied_best(eligible, lambda value: value["source_score"])
        and selector_has_tied_best(eligible, lambda value: value["metadata_score"])
        and equivalent_alternatives is not None
    )
    if ordinary_silver or strict_identity_tiebreak or strict_equivalent_critic_answer:
        status = "machine_calibrated"
        reason = (
            "Autonomous source/semantic/evidence/critic consensus passed all raw-V2 gates."
            if ordinary_silver
            else "Strict raw-metric identity resolved nondiscriminative source/metadata selector ties."
            if strict_identity_tiebreak
            else "Every critic-near-tie has the same exact V2 parsed value and source unit."
        )
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
                "protocol": AUTONOMOUS_REVIEW_PROTOCOL,
                "training_eligible": status == "machine_calibrated",
                "critic_accepts": critic_accepts,
                "selection_policy": (
                    "ordinary_multi_view_consensus"
                    if ordinary_silver
                    else "strict_raw_metric_identity_tiebreak"
                    if strict_identity_tiebreak
                    else "strict_equivalent_critic_answer"
                    if strict_equivalent_critic_answer
                    else "provisional_or_needs_human"
                ),
                "alternative_uid": None if alternative is None else alternative["uid"],
                "equivalent_critic_alternatives": equivalent_alternatives or [],
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
    context_path = args.evidence_context or bundle / "tables_evidence_context_v3.jsonl"
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
    direct_source_candidates = 0
    direct_source_ambiguous = 0
    if args.direct_evidence is not None:
        sidecar_rows = validate_direct_evidence_sidecar(
            bundle,
            args.direct_evidence.resolve(),
            context_path.resolve(),
            question_plan_overrides=(
                None
                if args.question_plan_overrides is None
                else args.question_plan_overrides.resolve()
            ),
        )
        direct_source_candidates, direct_source_ambiguous = apply_direct_source_discovery(
            items, sidecar_rows
        )
    tables = by_uid(v3.load_jsonl(structure_path), "V2 structures")
    contexts = by_uid(v3.load_jsonl(context_path), "evidence contexts")
    requested_segments = args.report_segments
    default_segments = bundle / "report_segments_v1.jsonl"
    segment_path = requested_segments or default_segments
    segment_count = 0
    if requested_segments is not None and not segment_path.is_file():
        raise FileNotFoundError(segment_path)
    if segment_path.is_file():
        segment_path = segment_path.resolve()
        segment_manifest = validate_report_segment_sidecar(bundle, segment_path)
        segments = by_uid(v3.load_jsonl(segment_path), "report segments")
        if len(segments) != int(segment_manifest.get("segment_count") or -1):
            raise ValueError("Report-segment UID/count contract is invalid")
        unknown_segments = sorted(set(segments) - set(tables))
        if unknown_segments:
            raise RuntimeError(
                "Report-segment sidecar has an unknown table UID: "
                + unknown_segments[0]
            )
        for uid, segment in segments.items():
            tables[uid]["report_segment"] = segment
        segment_count = len(segments)
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
    if segment_count:
        print(
            "Attached normalized report segments:",
            segment_count,
            "(navigation/unit metadata only; raw V2 rows remain evidence)",
        )
    if args.question_plan_overrides is not None:
        print("Applied source-bound plan overrides:", len(overrides))
    if args.direct_evidence is not None:
        print(
            "Applied raw-V2 direct source candidates:",
            direct_source_candidates,
            "same-table ambiguities retained:",
            direct_source_ambiguous,
        )
    if args.quarantine_output:
        print("Quarantine audit:", args.quarantine_output)


if __name__ == "__main__":
    main()
