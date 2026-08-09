"""Read-only raw-source audit helpers for missing formula operands.

These helpers identify source tables which exist in a report but were not
included in an immutable review bundle.  They intentionally do *not* append
tables to the bundle, repair OCR, bind an operand, calculate a formula, or
change a review/training status.  A later source-completion step must validate
the candidate again against its raw table hash and the entity/scope contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .corpus import extract_assets_from_report, infer_document_id, infer_path_metadata
from .financial_metrics import fold_text


def source_report_index(reports_root: Path) -> dict[tuple[str, int], list[Path]]:
    """Index canonical raw reports by the ticker/year encoded in their path."""
    index: dict[tuple[str, int], list[Path]] = {}
    for path in sorted(reports_root.rglob("*_extracted.txt")):
        if not path.is_file():
            continue
        ticker, year = infer_path_metadata(path, reports_root)
        if ticker and year is not None:
            index.setdefault((ticker.casefold(), int(year)), []).append(path)
    return index


def operand_is_missing(evidence_set: Mapping[str, Any], operand_id: str) -> bool:
    """Whether a required operand has no exact V2 candidate at all."""
    return not bool((evidence_set.get("operand_matches") or {}).get(operand_id))


def _row_matches_metric(row: Iterable[Any], metric_hints: Iterable[str]) -> bool:
    folded_row = fold_text(" ".join(str(cell) for cell in row))
    return any(fold_text(hint) in folded_row for hint in metric_hints if fold_text(hint))


def raw_source_candidates(
    operand: Mapping[str, Any],
    *,
    reports_root: Path,
    reports_by_ticker_year: Mapping[tuple[str, int], list[Path]],
    bundled_uids: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Return exact raw-table candidates for a missing source operand.

    The first return value is a fail-closed audit finding, not a validation
    result.  Only raw tables whose structural `table_function` matches the
    operand allow-list and whose *row* contains an exact metric hint are
    returned.  No fuzzy recovery or label rewriting is performed.
    """
    entity = str(operand.get("entity") or "").strip()
    years = [int(year) for year in operand.get("years") or [] if str(year).isdigit()]
    metric_hints = [str(value) for value in operand.get("metric_hints") or [] if str(value).strip()]
    allowed_functions = {
        str(value) for value in operand.get("allowed_table_functions") or [] if str(value)
    }
    if not entity or len(years) != 1 or not metric_hints or not allowed_functions:
        return "operand_not_source_auditable", []

    paths = list(reports_by_ticker_year.get((entity.casefold(), years[0]), []))
    if not paths:
        return "raw_report_missing", []

    candidates: list[dict[str, Any]] = []
    for path in paths:
        for asset in extract_assets_from_report(path, reports_root):
            function = dict(asset.table_function)
            # Source completion may expose a raw table, but a title-derived
            # classifier is not enough to treat a note as a primary statement.
            # Require the existing independent row-signature classification.
            if (
                str(function.get("kind") or "") not in allowed_functions
                or str(function.get("specificity") or "") != "structural"
            ):
                continue
            matching_rows = [
                {"row_index": index, "row": list(row)}
                for index, row in enumerate(asset.rows)
                if _row_matches_metric(row, metric_hints)
            ]
            if not matching_rows:
                continue
            candidates.append(
                {
                    "raw_document_id": infer_document_id(path),
                    "ticker": asset.ticker,
                    "scope": asset.scope,
                    "report_year": asset.report_year,
                    "raw_table_uid": asset.internal_table_uid,
                    "raw_table_ordinal": asset.local_ordinal,
                    "raw_table_sha256": asset.table_sha256,
                    "raw_report_sha256": asset.source_sha256,
                    "raw_report_path": str(path.relative_to(reports_root)),
                    "page_no": asset.page_no,
                    "table_function": function,
                    "column_labels": list(asset.headers),
                    "matching_rows": matching_rows,
                    "already_in_immutable_bundle": asset.internal_table_uid in bundled_uids,
                }
            )

    if not candidates:
        return "raw_metric_or_statement_not_found", []
    if any(candidate["already_in_immutable_bundle"] for candidate in candidates):
        return "raw_table_already_in_bundle_but_unbound", candidates
    return "raw_source_present_not_in_bundle", candidates
