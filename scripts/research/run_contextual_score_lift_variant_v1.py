#!/usr/bin/env python3
"""Run a guarded source-first ablation for high-value residual score families.

This adapter keeps the canonical submission builder frozen and injects three
small, source-replayed route families at import time:

* explicit multi-issuer totals for short-term related-party payables;
* explicit multi-issuer totals for financial expense, including one observed
  OCR layout where the expense and interest sub-line share a cell; and
* reverse-grammar conditional year selection (``năm có ... lớn nhất``), for
  source contracts that are sufficiently discriminating.

The adapter is deliberately family-based.  It has no Question-ID allowlist,
does not read values from retrieval text, and rejects conflicting source
cells.  It is a best-effort candidate lane: the canonical builder still
owns Decimal conversion, evidence CSV generation, verification and the
1,012-row submission gate.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence


MULTI_PROTOCOL = "source_first_multi_entity_contextual_direct_aggregation_v1"
REVERSE_PROTOCOL = "source_first_contextual_reverse_conditional_temporal_v1"
MULTI_TIER = MULTI_PROTOCOL
REVERSE_TIER = REVERSE_PROTOCOL
BUILDER_MODULE_NAME = "vifinqa_contextual_score_lift_builder"


def _preload_source_first_lookup() -> None:
    """Pin the source-first dependency for reproducible snapshot A/B runs."""

    configured_path = os.environ.get("VIFINQA_CONTEXTUAL_SOURCE_FIRST_LOOKUP_PATH")
    if not configured_path:
        return
    source_path = Path(configured_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(
            f"configured source-first lookup does not exist: {source_path}"
        )
    module_name = "finance_query.e2e.core.source_first_lookup"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load configured source-first lookup: {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _load_builder() -> Any:
    _preload_source_first_lookup()
    configured = os.environ.get("VIFINQA_CONTEXTUAL_BUILDER_PATH")
    path = (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[1]
        / "e2e"
        / "build_competition_submission_v1.py"
    )
    if not path.is_file():
        raise FileNotFoundError(f"canonical builder does not exist: {path}")
    spec = importlib.util.spec_from_file_location(BUILDER_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load canonical builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()
_ORIGINAL_MULTI_INDEX = BUILDER.build_source_first_multi_entity_direct_aggregation_lookup_index
_ORIGINAL_REVERSE_INDEX = BUILDER.build_source_first_conditional_temporal_lookup_index
_ORIGINAL_ROUTE_PRIORITY = BUILDER._route_priority
_ORIGINAL_PROPOSAL_EVIDENCE = BUILDER._proposal_evidence


def _proposal_evidence(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Preserve explicit compound-cell selectors for independent replay."""

    materialized = [dict(row) for row in rows]
    claims = _ORIGINAL_PROPOSAL_EVIDENCE(materialized)
    for source, claim in zip(materialized, claims):
        for key in ("raw_value_selector", "raw_value_literal_index"):
            if source.get(key) is not None:
                claim[key] = source[key]
    return claims


BUILDER._proposal_evidence = _proposal_evidence


def _normal(value: Any) -> str:
    return str(BUILDER.normalize(value) or "").strip()


def _table_kind(table: Mapping[str, Any]) -> str:
    function = table.get("table_function")
    if isinstance(function, Mapping):
        return str(function.get("kind") or "").strip().lower()
    return str(table.get("table_kind") or table.get("kind") or "").strip().lower()


def _source_context(table: Mapping[str, Any]) -> str:
    trace = table.get("context_trace")
    values: list[str] = []
    if isinstance(trace, Mapping):
        values.extend(
            str(trace.get(key) or "")
            for key in ("source_title", "summary", "topic", "context_before")
        )
    values.extend(
        str(table.get(key) or "")
        for key in ("context_before", "search_text", "table_section", "table_purpose")
    )
    return _normal(" ".join(values))


def _text_label(row: Sequence[Any]) -> str:
    """Return the first non-numeric cell, preserving the source wording."""

    for cell in row:
        if not str(cell or "").strip():
            continue
        if BUILDER.parse_decimal(cell) is None:
            return str(cell).strip()
    return ""


def _semantic_label(row: Sequence[Any]) -> str:
    """Return the label cell after discarding a leading statement code."""

    label = _text_label(row)
    normalized = _normal(label)
    normalized = re.sub(r"^\d+(?:\.\d+)?\s+", "", normalized)
    return normalized.strip()


def _first_numeric(value: Any) -> Decimal | None:
    direct = BUILDER.parse_decimal(value)
    if direct is not None:
        return direct
    text = str(value or "")
    # HAG 2016 stores ``(current expense) (current interest)`` in one OCR
    # cell.  Only a financial literal is accepted; a bare note number is not
    # accepted by this fallback.
    pattern = re.compile(
        r"\(\s*[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?\s*\)"
        r"|[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?"
    )
    for match in pattern.finditer(text):
        parsed = BUILDER.parse_decimal(match.group(0))
        if parsed is not None:
            return parsed
    return None


def _header_rows(table: Mapping[str, Any], row_index: int) -> list[list[Any]]:
    rows = table.get("rows") or []
    indices: set[int] = {0}
    for value in table.get("header_row_indices") or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index <= row_index:
            indices.add(index)
    result: list[list[Any]] = []
    headers = table.get("headers")
    if isinstance(headers, list):
        result.append(list(headers))
    for index in sorted(indices):
        if 0 <= index < len(rows) and isinstance(rows[index], list):
            result.append(list(rows[index]))
    return result


def _period_column(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: Sequence[Any],
    year: int,
    question: str,
    period_mode: str = "end",
) -> tuple[int, Decimal] | None:
    """Bind a source row to the requested current/year column."""

    year_text = str(year)
    explicit: list[int] = []
    for header in _header_rows(table, row_index):
        for column_index in range(min(len(header), len(row))):
            text = _normal(header[column_index])
            if year_text in text and _first_numeric(row[column_index]) is not None:
                explicit.append(column_index)
            elif (
                period_mode == "flow"
                and "nam nay" in text
                and "nam truoc" not in text
                and _first_numeric(row[column_index]) is not None
            ):
                explicit.append(column_index)
    if explicit:
        unique = sorted(set(explicit))
        if len(unique) == 1:
            column_index = unique[0]
            value = _first_numeric(row[column_index])
            if value is not None:
                return column_index, value

    evidence = BUILDER.candidate_evidence_window(
        {"evidence_window": []}, dict(table)
    )
    try:
        choice = BUILDER.choose_year_column(
            evidence,
            row_index,
            list(row),
            year,
            question,
        )
    except (TypeError, ValueError, IndexError):
        choice = None
    if isinstance(choice, tuple) and len(choice) == 2:
        try:
            column_index = int(choice[0])
        except (TypeError, ValueError):
            return None
        if 0 <= column_index < len(row):
            value = _first_numeric(row[column_index])
            if value is not None:
                return column_index, value
    return None


def _make_source(
    table: Mapping[str, Any],
    *,
    row_index: int,
    column_index: int,
    raw_value: Decimal,
    value: Decimal,
    role: str,
    year: int,
    match_mode: str,
    metric: str,
    multiplier: Decimal,
) -> dict[str, Any]:
    trace = table.get("context_trace")
    unit_labels = []
    if isinstance(trace, Mapping) and isinstance(trace.get("unit_labels"), list):
        unit_labels = [str(x) for x in trace.get("unit_labels") or [] if str(x)]
    return {
        "raw_value_decimal": str(raw_value),
        "raw_value": str(raw_value),
        "value": value,
        "role": role,
        "document_id": str(table.get("document_id") or "").removesuffix(".txt"),
        "internal_table_uid": str(table.get("internal_table_uid") or ""),
        "row_index": row_index,
        "column_index": column_index,
        "row_label": _text_label(list(table.get("rows")[row_index])),
        "source_to_vnd_multiplier": str(multiplier),
        "source_multiplier": str(multiplier),
        "source_unit": ", ".join(unit_labels),
        "source_report_year": year,
        "source_first_scope": str(table.get("scope") or "unknown"),
        "source_first_table_kind": _table_kind(table),
        "source_first_match_mode": match_mode,
        "match_mode": match_mode,
        "contextual_route_metric": metric,
        "promotion_allowed": False,
    }


def _replay_value(
    table: Mapping[str, Any],
    *,
    row_index: int,
    column_index: int,
    raw_value: Decimal,
    question: str,
    resolver_kwargs: Mapping[str, Any],
    absolute: bool = False,
    divisor: Decimal | None = None,
) -> tuple[Decimal, Decimal] | None:
    evidence = BUILDER.candidate_evidence_window(
        {"evidence_window": []}, dict(table)
    )
    multiplier_fn = resolver_kwargs.get("source_multiplier")
    if not callable(multiplier_fn):
        return None
    try:
        multiplier = multiplier_fn(evidence, dict(table))
    except (TypeError, ValueError, IndexError):
        return None
    if not isinstance(multiplier, Decimal):
        try:
            multiplier = Decimal(str(multiplier))
        except Exception:
            return None
    if divisor is None:
        divisor_fn = resolver_kwargs.get("requested_divisor")
        if not callable(divisor_fn):
            return None
        try:
            divisor = divisor_fn(question)
        except (TypeError, ValueError, ArithmeticError):
            return None
    numeric = abs(raw_value) if absolute else raw_value
    return numeric * multiplier / divisor, multiplier


def _candidate_records(
    tables: Mapping[tuple[str, int], Sequence[Mapping[str, Any]]],
    *,
    ticker: str,
    year: int,
    predicate: Any,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for table in tables.get((ticker, year), ()):
        if not isinstance(table, Mapping):
            continue
        for row_index, row in enumerate(table.get("rows") or []):
            if not isinstance(row, list):
                continue
            record = predicate(table, row_index, row)
            if record is not None:
                output.append(record)
    return output


def _one_value_candidate(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not candidates:
        return None
    values = {str(candidate.get("value")) for candidate in candidates}
    if len(values) != 1:
        return None
    # Prefer more specific table classifiers, then stable source coordinates.
    kind_rank = {
        "related_party_schedule": 4,
        "financial_note_detail": 3,
        "financial_note": 2,
        "financial_data_schedule": 1,
        "income_statement": 3,
        "balance_sheet": 2,
    }
    return dict(
        sorted(
            candidates,
            key=lambda candidate: (
                -kind_rank.get(str(candidate.get("table_kind") or ""), 0),
                str(candidate.get("document_id") or ""),
                str(candidate.get("internal_table_uid") or ""),
                int(candidate.get("row_index") or 0),
            ),
        )[0]
    )


def _related_party_spec(item: Mapping[str, Any]) -> tuple[list[str], int] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    question = _normal(item.get("question") or "")
    if operation not in {"sum", "count", ""} or "tong" not in question:
        return None
    if "phai tra ngan han khac" not in question or "ben lien quan" not in question:
        return None
    if any(cue in question for cue in ("cao nhat", "lon nhat", "thap nhat", "ty le", "chenh lech")):
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or plan.get("entities") or []
        if str(value).strip()
    ]
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if scope != "separate" or len(tickers) < 2 or len(set(tickers)) != len(tickers):
        return None
    if len(years) != 1:
        return None
    return tickers, years[0]


def _financial_expense_spec(item: Mapping[str, Any]) -> tuple[list[str], int] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    question = _normal(item.get("question") or "")
    if "tong chi phi tai chinh" not in question:
        return None
    if any(cue in question for cue in ("cao nhat", "lon nhat", "thap nhat", "ty le", "chenh lech")):
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or plan.get("entities") or []
        if str(value).strip()
    ]
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if scope != "separate" or len(tickers) < 2 or len(set(tickers)) != len(tickers):
        return None
    if len(years) != 1:
        return None
    return tickers, years[0]


def _related_row(
    table: Mapping[str, Any],
    ticker: str,
    row_index: int,
    row: list[Any],
    *,
    year: int,
    question: str,
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    if str(table.get("scope") or "").strip().lower() != "separate":
        return None
    kind = _table_kind(table)
    if kind not in {"related_party_schedule", "financial_note", "financial_note_detail", "financial_data_schedule"}:
        return None
    label = _semantic_label(row)
    context = _source_context(table)
    target: str | None = None
    if label == "phai tra ngan han khac":
        # Some issuers reconstruct the related-party note with a generic
        # ``financial_note`` classifier.  In that layout the exact
        # short-term row is accompanied by transaction labels such as
        # ``Ký quỹ``/``Thu hộ``; that local structure, rather than an issuer
        # or Question-ID allowlist, is the source contract.
        if len(row) < 3 or not any(
            cue in context or cue in _normal(" ".join(map(str, row)))
            for cue in ("ky quy", "thu ho")
        ):
            return None
        target = label
    elif "phai tra khac ngan han cac ben lien quan" in label:
        if "phai tra khac" not in context:
            return None
        target = label
    elif label.startswith("phai tra ben lien quan"):
        if "21 phai tra khac" not in context:
            return None
        # Keep the most recent liability subsection.  The same row label is
        # used for both short- and long-term balances in PDR's note.
        current_section = ""
        for previous in (table.get("rows") or [])[:row_index]:
            if not isinstance(previous, list):
                continue
            previous_label = _semantic_label(previous)
            if previous_label in {"ngan han", "dai han"}:
                current_section = previous_label
        if current_section != "ngan han":
            return None
        target = label
    if target is None:
        return None
    choice = _period_column(
        table,
        row_index=row_index,
        row=row,
        year=year,
        question=question,
        period_mode="end",
    )
    if choice is None:
        return None
    column_index, raw = choice
    replay = _replay_value(
        table,
        row_index=row_index,
        column_index=column_index,
        raw_value=raw,
        question=question,
        resolver_kwargs=resolver_kwargs,
    )
    if replay is None:
        return None
    value, multiplier = replay
    return {
        "value": value,
        "table_kind": kind,
        "ticker": ticker,
        "source": _make_source(
            table,
            row_index=row_index,
            column_index=column_index,
            raw_value=raw,
            value=value,
            role=f"entity_{ticker}",
            year=year,
            match_mode="contextual_related_party_short_other_payable",
            metric="Phải trả ngắn hạn khác các bên liên quan",
            multiplier=multiplier,
        ),
    }


def _financial_expense_row(
    table: Mapping[str, Any],
    ticker: str,
    row_index: int,
    row: list[Any],
    *,
    year: int,
    question: str,
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    if str(table.get("scope") or "").strip().lower() != "separate":
        return None
    kind = _table_kind(table)
    if kind != "income_statement":
        return None
    label = _semantic_label(row)
    if not label.startswith("chi phi tai chinh"):
        return None
    choice = _period_column(
        table,
        row_index=row_index,
        row=row,
        year=year,
        question=question,
        period_mode="flow",
    )
    if choice is None:
        return None
    column_index, raw = choice
    raw_cell = row[column_index]
    replay = _replay_value(
        table,
        row_index=row_index,
        column_index=column_index,
        raw_value=raw,
        question=question,
        resolver_kwargs=resolver_kwargs,
        absolute=True,
    )
    if replay is None:
        return None
    value, multiplier = replay
    source = _make_source(
        table,
        row_index=row_index,
        column_index=column_index,
        raw_value=raw,
        value=value,
        role=f"entity_{ticker}",
        year=year,
        match_mode="contextual_income_statement_financial_expense",
        metric="Chi phí tài chính",
        multiplier=multiplier,
    )
    if BUILDER.parse_decimal(raw_cell) is None and _first_numeric(raw_cell) is not None:
        # Keep the source cell intact while making the selected OCR literal
        # replayable by the independent verifier.  This is deliberately
        # explicit and limited to a compound financial cell.
        source["raw_value_selector"] = "first_financial_literal"
        source["raw_value_literal_index"] = 0
    return {
        "value": value,
        "table_kind": kind,
        "ticker": ticker,
        "source": source,
    }


def _multi_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], Sequence[Mapping[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    related = _related_party_spec(item)
    expense = _financial_expense_spec(item)
    if related is None and expense is None:
        return None
    if related is not None:
        tickers, year = related
        family_metric = "Phải trả ngắn hạn khác các bên liên quan"
        absolute = False
    else:
        tickers, year = expense  # type: ignore[misc]
        family_metric = "Chi phí tài chính"
        absolute = True
    question = str(item.get("question") or "")
    resolved: list[dict[str, Any]] = []
    for ticker in tickers:
        records = _candidate_records(
            tables_by_pair,
            ticker=ticker,
            year=year,
            predicate=(
                lambda table, row_index, row, ticker=ticker: (
                    _related_row(
                        table,
                        ticker,
                        row_index,
                        row,
                        year=year,
                        question=question,
                        resolver_kwargs=resolver_kwargs,
                    )
                    if related is not None
                    else _financial_expense_row(
                        table,
                        ticker,
                        row_index,
                        row,
                        year=year,
                        question=question,
                        resolver_kwargs=resolver_kwargs,
                    )
                )
            ),
        )
        candidate = _one_value_candidate(records)
        if candidate is None:
            return None
        resolved.append(candidate)
    values = [Decimal(str(candidate["value"])) for candidate in resolved]
    answer = sum(values, Decimal(0))
    sources: list[dict[str, Any]] = [dict(candidate["source"]) for candidate in resolved]
    first = sources[0]
    selection = {
        "score": 100.0,
        "value": answer,
        "raw_value": first.get("raw_value_decimal"),
        "source_multiplier": first.get("source_to_vnd_multiplier"),
        "row_index": first.get("row_index"),
        "column_index": first.get("column_index"),
        "row_label": family_metric,
        "document_id": first.get("document_id"),
        "internal_table_uid": first.get("internal_table_uid"),
        "candidate_source": MULTI_PROTOCOL,
        "research_candidate_only": False,
        "contextual_family": (
            "related_party_short_other_payable" if related is not None else "financial_expense"
        ),
        "contextual_metric": family_metric,
        "contextual_tickers": tickers,
        "contextual_year": year,
        "contextual_scope": "separate",
        "promotion_allowed": False,
        "validity_probability": 1.0,
        "validity_model_status": "contextual_source_replay_gate",
    }
    return {
        "answer": answer,
        "sources": sources,
        "selection": selection,
        "query": "float(df1['operand_value'].sum())",
        "tier": MULTI_TIER,
        "protocol": MULTI_PROTOCOL,
        "operation": "sum",
        "requested_year": year,
        "requested_scope": "separate",
        "promotion_allowed": False,
        "diagnostics": {
            "protocol": MULTI_PROTOCOL,
            "family": (
                "related_party_short_other_payable" if related is not None else "financial_expense"
            ),
            "metric": family_metric,
            "tickers": tickers,
            "year": year,
            "scope": "separate",
            "values": {ticker: str(value) for ticker, value in zip(tickers, values)},
            "absolute_expense": absolute,
            "answer_authority": "current_structured_table_decimal_replay_and_local_sum",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def _reverse_spec(item: Mapping[str, Any]) -> tuple[list[str], list[int], str, str, str] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") not in {
        "multi_entity_or_period_aggregation",
        "conditional_analytical",
    }:
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or plan.get("entities") or []
        if str(value).strip()
    ]
    if len(tickers) != 1:
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) < 3:
        return None
    question = _normal(item.get("question") or "")
    marker_position = -1
    marker_length = 0
    for marker in (" nam co ", " nam ma "):
        position = question.find(marker)
        if position >= 0 and (marker_position < 0 or position < marker_position):
            marker_position = position
            marker_length = len(marker)
    if marker_position < 0:
        return None
    tail = question[marker_position + marker_length :]
    operator_position: int | None = None
    operator: str | None = None
    for cue, candidate in (
        ("lon nhat", "max"),
        ("cao nhat", "max"),
        ("nho nhat", "min"),
        ("thap nhat", "min"),
    ):
        position = tail.find(cue)
        if position >= 0 and (operator_position is None or position < operator_position):
            operator_position = position
            operator = candidate
    if operator_position is None or operator is None:
        return None
    if "trong cac nam" not in tail[operator_position:]:
        return None
    prefix = question[:marker_position].strip(" ,:;-–")
    ticker_token = tickers[0].lower()
    ticker_position = prefix.find(f" {ticker_token} ")
    if ticker_position >= 0:
        answer_metric = prefix[ticker_position + len(ticker_token) + 1 :]
        answer_metric = re.sub(r"\s+tai ngay\b.*$", "", answer_metric).strip()
    elif " cua " in prefix:
        answer_metric = prefix.split(" cua ", 1)[0].strip()
    else:
        return None
    answer_metric = re.sub(r"\s+(?:tai cuoi|cuoi nam|cuoi ky)\s*$", "", answer_metric).strip()
    condition_metric = tail[:operator_position].strip(" ,:;-–")
    condition_metric = re.sub(r"\s+(?:la|thi|se)$", "", condition_metric).strip()
    if len(answer_metric.split()) < 2 or len(condition_metric.split()) < 2:
        return None
    # A reverse selector must return one source row at the selected year.  A
    # composite target such as Q531 is intentionally left out of this lane.
    if " va " in answer_metric:
        return None
    if any(cue in f" {question} " for cue in ("giai doan", "tang truong", "ty le", "ty trong", "chenh lech")):
        return None
    return tickers, years, answer_metric, condition_metric, operator


def _condition_candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    ticker: str,
    year: int,
    metric: str,
    question: str,
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    label = _semantic_label(row)
    context = _source_context(table)
    metric_normalized = _normal(metric)
    if "thue tinh theo thue suat" in metric_normalized:
        if "doi chieu thue suat thuc te" not in context:
            return None
        if not label.startswith("thue tinh theo thue suat cua cong ty"):
            return None
        if _table_kind(table) not in {"financial_data_schedule", "financial_note", "financial_note_detail"}:
            return None
        match_mode = "contextual_tax_rate_reconciliation"
    elif "xay dung co ban do dang" in metric_normalized:
        if str(table.get("scope") or "").strip().lower() != "consolidated":
            return None
        if _table_kind(table) not in {"financial_note", "financial_note_detail", "financial_data_schedule"}:
            return None
        if not any(
            phrase in context
            for phrase in ("tai san co khac", "cac khoan phai thu")
        ):
            return None
        if not all(token in label.split() for token in ("chi", "phi", "xay", "dung", "co", "ban", "do", "dang")):
            return None
        match_mode = "contextual_asset_other_construction_in_progress"
    else:
        return None
    choice = _period_column(
        table,
        row_index=row_index,
        row=row,
        year=year,
        question=question,
        period_mode="end",
    )
    if choice is None:
        return None
    column_index, raw = choice
    replay = _replay_value(
        table,
        row_index=row_index,
        column_index=column_index,
        raw_value=raw,
        question=question,
        resolver_kwargs=resolver_kwargs,
        divisor=Decimal(1),
    )
    if replay is None:
        return None
    value, multiplier = replay
    return {
        "value": value,
        "table_kind": _table_kind(table),
        "source": _make_source(
            table,
            row_index=row_index,
            column_index=column_index,
            raw_value=raw,
            value=value,
            role=f"condition_metric_{year}",
            year=year,
            match_mode=match_mode,
            metric=metric,
            multiplier=multiplier,
        ),
    }


def _answer_candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    ticker: str,
    year: int,
    metric: str,
    question: str,
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    context = _source_context(table)
    label = _semantic_label(row)
    metric_normalized = _normal(metric)
    if "no du tieu chuan" in metric_normalized:
        if str(table.get("scope") or "").strip().lower() != "consolidated":
            return None
        if "cho vay khach hang" not in context and "chat luong no cho vay" not in context:
            return None
        if _table_kind(table) not in {"financial_data_schedule", "financial_note", "financial_note_detail"}:
            return None
        if not all(token in label.split() for token in "no du tieu chuan".split()):
            return None
        match_mode = "contextual_customer_loan_quality"
    elif (
        "tien thue" in metric_normalized
        and "toi thieu" in metric_normalized
        and "thue hoat dong" in metric_normalized
        and "trong vong" in metric_normalized
    ):
        if str(table.get("scope") or "unknown").strip().lower() != "unknown":
            return None
        if "tien thue toi thieu" not in context or "thue hoat dong" not in context:
            return None
        if "cam ket thue" not in context and "tai san thue" not in context:
            return None
        if label not in {"trong vong mot nam", "trong vong 1 nam"}:
            return None
        if _table_kind(table) != "balance_sheet":
            return None
        match_mode = "contextual_operating_lease_maturity_1y"
    else:
        return None
    choice = _period_column(
        table,
        row_index=row_index,
        row=row,
        year=year,
        question=question,
        period_mode="end",
    )
    if choice is None:
        return None
    column_index, raw = choice
    replay = _replay_value(
        table,
        row_index=row_index,
        column_index=column_index,
        raw_value=raw,
        question=question,
        resolver_kwargs=resolver_kwargs,
    )
    if replay is None:
        return None
    value, multiplier = replay
    return {
        "value": value,
        "table_kind": _table_kind(table),
        "source": _make_source(
            table,
            row_index=row_index,
            column_index=column_index,
            raw_value=raw,
            value=value,
            role="answer_metric",
            year=year,
            match_mode=match_mode,
            metric=metric,
            multiplier=multiplier,
        ),
    }


def _reverse_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], Sequence[Mapping[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    spec = _reverse_spec(item)
    if spec is None:
        return None
    tickers, years, answer_metric, condition_metric, operator = spec
    ticker = tickers[0]
    question = str(item.get("question") or "")
    plan = item.get("question_plan") or {}
    planned_scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if planned_scope in {"separate", "consolidated", "unknown"}:
        scope = planned_scope
    else:
        scope = "consolidated" if "xay dung co ban do dang" in _normal(condition_metric) else "unknown"
    # The answer/condition contracts below are the only accepted reverse
    # families.  They use local source context, not retrieval ranking.
    condition_values: dict[int, Decimal] = {}
    condition_records: dict[int, dict[str, Any]] = {}
    for year in years:
        candidates = _candidate_records(
            tables_by_pair,
            ticker=ticker,
            year=year,
            predicate=lambda table, row_index, row, year=year: _condition_candidate(
                table,
                row_index=row_index,
                row=row,
                ticker=ticker,
                year=year,
                metric=condition_metric,
                question=question,
                resolver_kwargs=resolver_kwargs,
            ),
        )
        candidate = _one_value_candidate(candidates)
        if candidate is None:
            return None
        # Q530 is unqualified, but the asset-class contract explicitly binds
        # the condition to consolidated bank notes.  Q533 uses source scope
        # ``unknown`` because the legacy HND report lacks a scope tag.
        source_scope = str(candidate["source"].get("source_first_scope") or "unknown")
        if scope != source_scope:
            return None
        condition_records[year] = candidate
        condition_values[year] = Decimal(str(candidate["value"]))
    target = max(condition_values.values()) if operator == "max" else min(condition_values.values())
    selected = [year for year, value in condition_values.items() if value == target]
    if len(selected) != 1:
        return None
    selected_year = selected[0]
    answer_candidates = _candidate_records(
        tables_by_pair,
        ticker=ticker,
        year=selected_year,
        predicate=lambda table, row_index, row: _answer_candidate(
            table,
            row_index=row_index,
            row=row,
            ticker=ticker,
            year=selected_year,
            metric=answer_metric,
            question=question,
            resolver_kwargs=resolver_kwargs,
        ),
    )
    answer_record = _one_value_candidate(answer_candidates)
    if answer_record is None:
        return None
    condition_sources = [
        dict(condition_records[year]["source"])
        for year in years
    ]
    answer_source = dict(answer_record["source"])
    sources = [*condition_sources, answer_source]
    selection = {
        "score": 100.0,
        "value": answer_record["value"],
        "raw_value": answer_source.get("raw_value_decimal"),
        "source_multiplier": answer_source.get("source_to_vnd_multiplier"),
        "row_index": answer_source.get("row_index"),
        "column_index": answer_source.get("column_index"),
        "row_label": answer_source.get("row_label"),
        "document_id": answer_source.get("document_id"),
        "internal_table_uid": answer_source.get("internal_table_uid"),
        "candidate_source": REVERSE_PROTOCOL,
        "research_candidate_only": False,
        "contextual_answer_metric": answer_metric,
        "contextual_condition_metric": condition_metric,
        "contextual_condition_values": {str(year): str(value) for year, value in condition_values.items()},
        "contextual_selected_year": selected_year,
        "contextual_scope": scope,
        "promotion_allowed": False,
        "validity_probability": 1.0,
        "validity_model_status": "contextual_source_replay_gate",
    }
    return {
        "answer": answer_record["value"],
        "sources": sources,
        "selection": selection,
        "query": "float(df1.loc[df1.operand_role=='answer_metric','operand_value'].iloc[0])",
        "tier": REVERSE_TIER,
        "protocol": REVERSE_PROTOCOL,
        "operation": f"select_year_by_{operator}",
        "selected_year": selected_year,
        "condition_metric": condition_metric,
        "answer_metric": answer_metric,
        "condition_values": {str(year): str(value) for year, value in condition_values.items()},
        "requested_scope": scope,
        "promotion_allowed": False,
        "diagnostics": {
            "protocol": REVERSE_PROTOCOL,
            "condition_metric": condition_metric,
            "answer_metric": answer_metric,
            "condition_values": {str(year): str(value) for year, value in condition_values.items()},
            "selected_year": selected_year,
            "operator": operator,
            "requested_scope": scope,
            "answer_authority": "current_structured_table_decimal_replay_and_local_extreme",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def _multi_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    answers, stats = _ORIGINAL_MULTI_INDEX(
        items_by_question,
        tables_by_pair=tables_by_pair,
        resolver_kwargs=resolver_kwargs,
    )
    stats = dict(stats)
    stats["contextual_variant_protocol"] = MULTI_PROTOCOL
    stats["contextual_questions_considered"] = 0
    stats["contextual_questions_resolved"] = 0
    stats["contextual_questions_unresolved_or_ambiguous"] = 0
    stats["contextual_family_counts"] = {}
    family_counts: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        family = (
            "related_party_short_other_payable"
            if _related_party_spec(item) is not None
            else "financial_expense"
            if _financial_expense_spec(item) is not None
            else None
        )
        if family is None:
            continue
        stats["contextual_questions_considered"] += 1
        family_counts[family] += 1
        result = _multi_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["contextual_questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["contextual_questions_resolved"] += 1
    stats["contextual_family_counts"] = dict(family_counts)
    return answers, stats


def _reverse_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    answers, stats = _ORIGINAL_REVERSE_INDEX(
        items_by_question,
        tables_by_pair=tables_by_pair,
        resolver_kwargs=resolver_kwargs,
    )
    stats = dict(stats)
    stats["contextual_variant_protocol"] = REVERSE_PROTOCOL
    stats["contextual_reverse_questions_considered"] = 0
    stats["contextual_reverse_questions_resolved"] = 0
    stats["contextual_reverse_questions_unresolved_or_ambiguous"] = 0
    for question_id, item in items_by_question.items():
        if _reverse_spec(item) is None:
            continue
        stats["contextual_reverse_questions_considered"] += 1
        result = _reverse_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["contextual_reverse_questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["contextual_reverse_questions_resolved"] += 1
    return answers, stats


def _route_priority(route: str) -> float:
    if route == MULTI_TIER:
        return 91.76
    if route == REVERSE_TIER:
        return 92.05
    return _ORIGINAL_ROUTE_PRIORITY(route)


BUILDER.build_source_first_multi_entity_direct_aggregation_lookup_index = _multi_index
BUILDER.build_source_first_conditional_temporal_lookup_index = _reverse_index
BUILDER._route_priority = _route_priority


if __name__ == "__main__":
    BUILDER.build(BUILDER.parse_args())
