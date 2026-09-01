"""Fail-closed typed operand decomposition for ViFinQA questions.

The planner describes *what must be grounded*.  It never retrieves a value,
executes arithmetic, or promotes a review status.  A plan is complete only
when every leaf of its AST has an explicit typed operand.  Unrecognised or
underspecified programs are represented as an abstention rather than an
invented formula.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping

from .decimal_executor import OPERATOR_REGISTRY
from .core.advisory_intent import is_explicit_single_entity_advisory
from .core.answer_certificates import temporal_contract_for_operand
from .core.currency_units import is_fixed_vnd_scale
from .core.financial_metrics import infer_formula_spec
from .core.questions import (
    normalize_text,
    reported_value_lookup_reason,
    reported_value_metric_hint,
)
from .core.report_entities import resolve_explicit_question_ticker, resolve_question_entity


TYPED_OPERAND_PLAN_SCHEMA_VERSION = 1
TYPED_OPERAND_PLAN_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"


# These patterns are deliberately about program *shape*, not report content.
# They enable only a leaf-wise lookup followed by one deterministic arithmetic
# operator.  A selector, threshold, population filter, or a second metric is
# a different program and must remain abstained until it has a typed stage
# contract.
COMPLEX_STAGE_RE = re.compile(
    r"\b(?:xét|nếu|giả sử|trung vị|đồng thời|thỏa mãn|trong số|"
    r"(?:doanh nghiệp|công ty|các mã)\s+có|"
    r"(?:có|với)\s+.{0,100}\b(?:lớn hơn|nhỏ hơn|cao hơn|thấp hơn|dương|âm)\b|"
    r"(?:tại|vào|ở)\s+năm\s+(?!nào\b)|năm\s+mà|"
    r"(?:cao nhất|thấp nhất|lớn nhất|nhỏ nhất)\s+(?:trong số|của các)|"
    r"năm có|tại năm|vào năm mà|sau năm|trước năm)\b",
    re.IGNORECASE,
)
COMPARISON_RE = re.compile(
    r"\b(?:chênh lệch|hiệu số|trừ đi|lớn hơn|nhỏ hơn|cao hơn|thấp hơn)\b",
    re.IGNORECASE,
)
DIRECTIONAL_COMPARISON_RE = re.compile(
    r"\b(?:lớn hơn|nhỏ hơn|cao hơn|thấp hơn|trừ đi)\b", re.IGNORECASE
)
TEMPORAL_CHANGE_RE = re.compile(
    r"\b(?:chênh lệch|hiệu số|trừ đi|tăng\s+so\s+với|giảm\s+so\s+với|"
    r"tăng trưởng|biến động)\b",
    re.IGNORECASE,
)
YEAR_RESULT_RE = re.compile(r"\b(?:năm nào|năm nào\b|mốc nào)\b", re.IGNORECASE)

# A source-title match proves at most one issuer.  It is useful only for
# question shapes that are intrinsically single-entity; never use it to
# collapse a genuine comparison, aggregation, or selection population.
SOURCE_TITLE_SINGLE_ENTITY_FAMILIES = frozenset(
    {"direct_lookup", "temporal_change", "ratio_or_derived"}
)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _plan_from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return dict(item.get("effective_question_plan") or item.get("question_plan") or {})


def _typed_operand(
    operand: Mapping[str, Any],
    *,
    default_ticker: str | None,
    default_scope: str | None,
    requested_unit: str | None,
) -> dict[str, Any]:
    years = [int(year) for year in operand.get("years") or []]
    if not years and operand.get("period") is not None:
        years = [int(operand["period"])]
    entity = str(operand.get("entity") or operand.get("ticker") or default_ticker or "").strip()
    hints = [str(value).strip() for value in operand.get("metric_hints") or [] if str(value).strip()]
    metric = str(operand.get("metric") or operand.get("label") or "").strip()
    if metric and metric not in hints:
        hints.insert(0, metric)
    return {
        "operand_id": str(operand.get("operand_id") or "").strip(),
        "role": str(operand.get("role") or operand.get("operand_id") or "value").strip(),
        "metric_hints": hints,
        "entity": entity or None,
        "ticker": entity or None,
        "years": years,
        "scope": str(operand.get("scope") or default_scope or "").strip() or None,
        "temporal_contract": temporal_contract_for_operand(
            years=years,
            scope=str(operand.get("scope") or default_scope or "").strip() or None,
            requested_unit=requested_unit,
            entity=entity or None,
        ),
        "unit_contract": {
            "requested_unit": requested_unit,
            "source_unit_required": True,
            # A source unit remains mandatory and is checked later against an
            # exact header.  Only deterministic VND scales may be converted;
            # rates, percentages and arbitrary units remain fail-closed.
            "conversion_allowed": is_fixed_vnd_scale(requested_unit),
            "conversion_policy": (
                "exact_fixed_vnd_scale_only"
                if is_fixed_vnd_scale(requested_unit)
                else "not_applicable"
            ),
        },
        "allowed_table_functions": list(operand.get("allowed_table_functions") or []),
        "stage_id": str(operand.get("stage_id") or "").strip() or None,
        "required": bool(operand.get("required", True)),
        "grounding_contract": {
            "exact_internal_table_uid": True,
            "exact_row_index": True,
            "exact_column_index": True,
            "exact_raw_cell": True,
            "canonical_header_required": True,
            "adjacent_table_inference_allowed": False,
        },
    }


def _formula_ast(formula: Mapping[str, Any]) -> dict[str, Any]:
    formula_id = str(formula.get("formula_id") or "")
    operands = [str(value.get("operand_id")) for value in formula.get("operands") or []]
    simple = {
        "current_ratio": {"op": "divide", "args": ["numerator", "denominator"]},
        "quick_ratio": {
            "op": "divide",
            "args": [{"op": "subtract", "args": ["current_assets", "inventory"]}, "current_liabilities"],
        },
        "net_service_result": {"op": "subtract", "args": ["service_income", "service_expense"]},
        "net_finance_result": {"op": "subtract", "args": ["finance_income", "finance_expense"]},
        "net_other_income": {"op": "subtract", "args": ["other_income", "other_expense"]},
        # The controlled rule explicitly names ``x_old`` and ``x_new``.  The
        # Decimal operator takes new first, then old; making that orientation
        # explicit removes a former compiler-only blocker without guessing a
        # period or an operand.
        "percentage_change": {"op": "percentage_change", "args": ["x_new", "x_old"]},
    }
    if formula_id in simple:
        return simple[formula_id]
    if len(operands) == 2 and {"numerator", "denominator"}.issubset(operands):
        return {"op": "divide", "args": ["numerator", "denominator"]}
    # Complex controlled formulas keep their explicit stage semantics.  The
    # executor must compile these stages before production; no arithmetic AST
    # is fabricated from a human-readable expression.
    if formula.get("stages"):
        return {
            "op": "staged_program",
            "formula_id": formula_id,
            "stages": deepcopy(list(formula.get("stages") or [])),
        }
    return {"op": "formula_contract", "formula_id": formula_id, "args": operands}


def _missing_contracts(operands: list[dict[str, Any]]) -> list[str]:
    missing: set[str] = set()
    if not operands:
        return ["operands"]
    for operand in operands:
        if not operand["operand_id"]:
            missing.add("operand_id")
        if not operand["metric_hints"]:
            missing.add("metric_hints")
        if not operand["entity"]:
            missing.add("entity")
        if not operand["years"]:
            missing.add("year")
    return sorted(missing)


def _simple_metric_hint(question: str) -> str | None:
    """Return a conservative lookup label for one-metric programs.

    This is not a semantic parser.  It removes only the operation phrase and
    the clearly delimited company/time clause.  When no compact metric remains
    the caller must abstain instead of using the entire question as a fuzzy
    retrieval query.
    """
    value = normalize_text(question).strip(" ?.")
    # Comparison questions identify their single source row immediately
    # before the entity separator.  This covers both ``chênh lệch X giữa A và
    # B`` and ``X của A ... lớn hơn của B`` without treating the company names
    # as row-label tokens.
    match = re.search(
        r"^(?:tính\s+)?(?:giá trị\s+)?(?:chênh lệch|hiệu số)\s+(.+?)\s+giữa\b",
        value,
        re.IGNORECASE,
    )
    if match:
        value = match.group(1)
    else:
        value = re.sub(
            r"^(?:tính\s+)?(?:giá trị\s+)?(?:chênh lệch|hiệu số)\s+",
            "",
            value,
            flags=re.IGNORECASE,
        )
        # A source label preceding ``của <entity>`` is still exact enough to
        # be an evidence hint.  Do not apply this to a generic ``của`` inside
        # a row label: it is limited to obvious company introducers.
        company_boundary = re.search(
            r"\s+của\s+(?:công ty|ctcp|ngân hàng|tập đoàn|tổng công ty)\b",
            value,
            re.IGNORECASE,
        )
        if company_boundary:
            value = value[: company_boundary.start()]

    value = re.sub(
        r"^(?:giá trị|mức|số|tổng giá trị)\s+(?:trung bình|bình quân)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    # For a one-entity extremum, the entity appears before ``có``/``ghi
    # nhận`` and the source row begins immediately afterwards.  This is not
    # used for filters (which were rejected before metric extraction).
    value = re.sub(
        r"^.+?\b(?:có|ghi\s+nhận)\s+(?:mức\s+)?",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\s+(?:trung bình|bình quân)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    # Stop at an explicitly temporal/presentation clause.  We intentionally
    # leave plain report words (including ``từ``) intact because they can be
    # part of a financial row label.
    value = re.split(
        r"\s+(?:giữa|trong|qua|tại|vào|từ)\s+(?:các\s+)?"
        r"(?:năm|mốc|giai đoạn|khoảng thời gian)\b|"
        r"\s+(?:cuối|đầu)\s+năm\b|\s+là\s+bao\s+nhiêu\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"\b(?:19|20)\d{2}\b", " ", value)
    value = re.sub(
        r"\s+(?:cao nhất|lớn nhất|thấp nhất|nhỏ nhất)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value).strip(" ,:;.-")
    # The compact fallback is useful for wording such as "X cao nhất" where
    # no entity clause occurs.  Reject it when it is merely a question shell.
    if len(value) < 4 or not any(character.isalpha() for character in value):
        return None
    return value


def _simple_operands(
    *,
    metric: str,
    tickers: list[str],
    years: list[int],
    scope: str | None,
    requested_unit: str | None,
    role: str = "value",
) -> list[dict[str, Any]]:
    """Expand a one-metric cartesian set only for an unambiguous axis.

    The caller guarantees exactly one of ``tickers`` or ``years`` has many
    values.  That prevents silently claiming a cross-product over an unclear
    entity-period pairing.
    """
    pairs: list[tuple[str, int]]
    if len(tickers) == 1 and len(years) >= 1:
        pairs = [(tickers[0], year) for year in years]
    elif len(years) == 1 and len(tickers) >= 1:
        pairs = [(ticker, years[0]) for ticker in tickers]
    else:
        return []
    return [
        _typed_operand(
            {
                "operand_id": f"x{index}",
                "role": role,
                "metric": metric,
                "period": year,
                "ticker": ticker,
            },
            default_ticker=None,
            default_scope=scope,
            requested_unit=requested_unit,
        )
        for index, (ticker, year) in enumerate(pairs)
    ]


def _repaired_explicit_operation(
    source_ast: Mapping[str, Any], question: str, operands: list[dict[str, Any]]
) -> dict[str, Any]:
    """Repair only a legacy ``plan_required`` with two explicit leaves."""
    ast = deepcopy(dict(source_ast or {}))
    if str(ast.get("op") or "") != "plan_required" or len(operands) != 2:
        return ast
    normalized = normalize_text(question).casefold()
    operand_ids = [operand["operand_id"] for operand in operands]
    if "tăng trưởng" in normalized or "phần trăm tăng" in normalized:
        return {"op": "percentage_change", "args": [operand_ids[-1], operand_ids[0]]}
    if re.search(r"\b(?:trừ đi|hiệu số|chênh lệch|tăng bao nhiêu|giảm bao nhiêu)\b", normalized):
        return {"op": "subtract", "args": [operand_ids[-1], operand_ids[0]]}
    return ast


def _simple_structure_plan(
    question: str,
    *,
    tickers: list[str],
    years: list[int],
    scope: str | None,
    requested_unit: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, list[str]] | None:
    """Recognise a single-operation, single-metric non-formula program.

    This remains intentionally small.  In particular, any filter/rank/target
    chain is rejected even if its first phrase resembles a mean, sum or max.
    """
    normalized = normalize_text(question)
    if COMPLEX_STAGE_RE.search(normalized):
        return None
    metric = _simple_metric_hint(question)
    if metric is None:
        return None

    # Exactly two resolved entities and one period makes a plain comparison
    # auditable.  An unlabeled "chênh lệch" is represented as an absolute
    # difference; use signed subtraction only where wording fixes direction.
    if len(tickers) == 2 and len(years) == 1 and COMPARISON_RE.search(normalized):
        operands = _simple_operands(
            metric=metric,
            tickers=tickers,
            years=years,
            scope=scope,
            requested_unit=requested_unit,
            role="comparison_value",
        )
        op = "subtract" if DIRECTIONAL_COMPARISON_RE.search(normalized) else "absolute_difference"
        return (
            operands,
            {"op": op, "args": [operand["operand_id"] for operand in operands]},
            "simple_cross_entity_comparison",
            ["SINGLE_METRIC_TWO_ENTITY_COMPARISON"],
        )

    # Legacy family routing can confuse a row label beginning with ``Tổng``
    # for an aggregation.  With one entity, exactly two explicit years, and a
    # comparison phrase, it is a temporal program instead.  The direction is
    # fixed by the chronology already stored in the plan; no intervening year
    # is invented.
    if len(tickers) == 1 and len(years) == 2 and TEMPORAL_CHANGE_RE.search(normalized):
        operands = _simple_operands(
            metric=metric,
            tickers=tickers,
            years=years,
            scope=scope,
            requested_unit=requested_unit,
            role="temporal_value",
        )
        lower = normalized.casefold()
        op = (
            "percentage_change"
            if "tăng trưởng" in lower or "tăng so với" in lower or "giảm so với" in lower
            else "subtract"
        )
        return (
            operands,
            {"op": op, "args": [operands[-1]["operand_id"], operands[0]["operand_id"]]},
            "simple_temporal_comparison",
            ["SINGLE_METRIC_TWO_PERIOD_COMPARISON"],
        )

    # A single entity across stated years, or several entities in one stated
    # year, is the only aggregation grid accepted here.  Do not infer a
    # multi-entity/multi-year cross-product.
    one_axis_many = (len(tickers) == 1 and len(years) >= 2) or (
        len(tickers) >= 2 and len(years) == 1
    )
    if not one_axis_many:
        return None

    lower = normalized.casefold()
    if "trung bình" in lower or "bình quân" in lower:
        op = "mean"
    elif any(token in lower for token in ("cao nhất", "lớn nhất")):
        op = "max"
    elif any(token in lower for token in ("thấp nhất", "nhỏ nhất")):
        op = "min"
    elif any(token in lower for token in ("cộng lại", "cộng dồn", "tích lũy")) or re.search(
        r"^(?:tính\s+)?tổng(?:\s+(?:giá trị|số))?\b", lower
    ):
        op = "sum"
    else:
        return None

    operands = _simple_operands(
        metric=metric,
        tickers=tickers,
        years=years,
        scope=scope,
        requested_unit=requested_unit,
        role="aggregation_value",
    )
    if not operands:
        return None
    if op in {"max", "min"} and YEAR_RESULT_RE.search(normalized):
        # The leaves are fully described, but the answer is an identity rather
        # than the numeric extremum.  Preserve that distinction for the staged
        # executor instead of returning the maximum amount as a year.
        return (
            operands,
            {"op": "arg_extreme_period", "direction": op, "args": [operand["operand_id"] for operand in operands]},
            "simple_period_extremum",
            ["SINGLE_METRIC_PERIOD_SELECTOR"],
        )
    return (
        operands,
        {"op": op, "args": [operand["operand_id"] for operand in operands]},
        "simple_aggregation",
        ["SINGLE_METRIC_PLAIN_AGGREGATION"],
    )


def build_typed_operand_plan(
    item: Mapping[str, Any],
    *,
    report_entity_aliases: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one deterministic, answer-ineligible typed plan for ``item``."""
    question = str(item.get("question") or "")
    source_plan = _plan_from_item(item)
    tickers = [str(value) for value in source_plan.get("tickers") or [] if str(value)]
    scope = str(source_plan.get("scope") or "").strip() or None
    requested_unit = str(source_plan.get("requested_unit") or "").strip() or None
    route = "abstain"
    reason_codes: list[str] = []
    effective_family = str(source_plan.get("family") or item.get("weak_family") or "unknown")
    entity_resolution: dict[str, Any] | None = None
    if (
        report_entity_aliases
        and len(tickers) != 1
        and effective_family in SOURCE_TITLE_SINGLE_ENTITY_FAMILIES
    ):
        # The alias sidecar itself is source-bound and rejects collisions.  A
        # successful result is navigation metadata for the operand plan, not
        # numeric evidence and never an implicit scope choice.
        entity_resolution = resolve_question_entity(question, report_entity_aliases)
        if entity_resolution is None:
            entity_resolution = resolve_explicit_question_ticker(question, report_entity_aliases)
        if entity_resolution is not None:
            tickers = [str(entity_resolution["ticker"])]
            reason_codes.append(
                "SOURCE_TITLE_ENTITY_RESOLVED"
                if entity_resolution["policy"] != "unique_explicit_source_ticker_token_question_match_v1"
                else "EXPLICIT_SOURCE_TICKER_RESOLVED"
            )
    default_ticker = tickers[0] if len(tickers) == 1 else None
    operands: list[dict[str, Any]] = []
    operation_ast: dict[str, Any] = {"op": "abstain"}
    formula = infer_formula_spec(question)
    formula_id = str((formula or {}).get("formula_id") or "")
    explicit_staged_formula = formula_id in {
        "quick_ratio_gpm_interest_coverage_selection",
        "cfo_positive_multiyear_max_net_margin",
        "debt_to_equity_argmax_interest_coverage",
        "positive_operating_profit_argmin_cfo_ratio_net_margin",
        "operating_cash_flow_argmax_period",
    }
    safe_formula_route = bool(
        formula
        and formula_id != "multi_stage_selection_unresolved"
        and (
            explicit_staged_formula
            or (
                len(tickers) <= 1
                and effective_family in {"ratio_or_derived", "temporal_change"}
            )
        )
    )

    source_operands = list(source_plan.get("operands") or [])
    reported_lookup_reason = reported_value_lookup_reason(question)
    if reported_lookup_reason is None and len(tickers) == 1:
        # This does not resolve an issuer from text.  It only permits a
        # reported-row classifier to ignore duplicate uppercase title/ticker
        # tokens after the incoming typed plan has already resolved one
        # issuer.  Exact V2/V3 entity, scope, row and period checks remain
        # mandatory downstream.
        reported_lookup_reason = reported_value_lookup_reason(
            question,
            allow_resolved_single_entity_ticker_noise=True,
        )
    if is_explicit_single_entity_advisory(question, entity_count=len(tickers)):
        route = "explicit_advisory_abstention"
        effective_family = "out_of_scope_advisory"
        reason_codes.append("EXPLICIT_SINGLE_ENTITY_ADVISORY_INTENT")
    elif reported_lookup_reason:
        # The original keyword router can misclassify report-row names such as
        # "Tổng cộng tài sản" as aggregation.  This narrow classifier only
        # restores the one disclosed-value semantics; evidence remains exact.
        effective_family = "direct_lookup"
        metric = str(source_plan.get("metric_hint") or "").strip()
        if not metric:
            metric = reported_value_metric_hint(question, reported_lookup_reason)
        operands = [
            _typed_operand(
                {
                    "operand_id": "x0",
                    "role": "reported_value",
                    "metric": metric,
                    "period": (source_plan.get("years") or [None])[0],
                },
                default_ticker=default_ticker,
                default_scope=scope,
                requested_unit=requested_unit,
            )
        ]
        operation_ast = {"op": "lookup", "args": ["x0"]}
        route = "reported_value_lookup"
        reason_codes.append("NARROW_REPORTED_ROW_RECLASSIFICATION")
    elif safe_formula_route:
        formula_entity = str(formula.get("entity") or default_ticker or "").strip() or None
        operands = [
            _typed_operand(
                operand,
                default_ticker=formula_entity,
                default_scope=scope,
                requested_unit=requested_unit,
            )
            for operand in formula.get("operands") or []
        ]
        operation_ast = _formula_ast(formula)
        route = "controlled_formula_template"
        reason_codes.append("CONTROLLED_FORMULA_MATCH")
    elif source_operands:
        operands = [
            _typed_operand(
                operand,
                default_ticker=default_ticker,
                default_scope=scope,
                requested_unit=requested_unit,
            )
            for operand in source_operands
        ]
        operation_ast = _repaired_explicit_operation(
            dict(source_plan.get("operation_ast") or {}), question, operands
        )
        route = "existing_typed_plan"
        reason_codes.append("SOURCE_PLAN_HAS_EXPLICIT_OPERANDS")
        if operation_ast != dict(source_plan.get("operation_ast") or {}):
            reason_codes.append("LEGACY_PLAN_REQUIRED_REPAIRED_FROM_EXPLICIT_LEAVES")
    elif (
        simple_plan := _simple_structure_plan(
            question,
            tickers=tickers,
            years=[int(value) for value in source_plan.get("years") or []],
            scope=scope,
            requested_unit=requested_unit,
        )
    ) is not None:
        operands, operation_ast, route, simple_reasons = simple_plan
        reason_codes.extend(simple_reasons)
    else:
        reason_codes.append(
            "UNRESOLVED_MULTI_STAGE_SELECTION"
            if formula and formula.get("formula_id") == "multi_stage_selection_unresolved"
            else "UNKNOWN_OPERAND_STRUCTURE"
        )

    missing = _missing_contracts(operands)
    operator = str(operation_ast.get("op") or "")
    contract = OPERATOR_REGISTRY.get(operator)
    executable_ast = bool(contract and contract.shadow_eligible)
    if missing:
        status = "abstain"
        reason_codes.extend(f"MISSING_{name.upper()}" for name in missing)
    elif not executable_ast:
        status = "typed_non_executable"
        reason_codes.append("EXECUTOR_COMPILATION_REQUIRED")
    else:
        status = "complete"

    core = {
        "schema_version": TYPED_OPERAND_PLAN_SCHEMA_VERSION,
        "protocol": TYPED_OPERAND_PLAN_PROTOCOL,
        "question_id": int(item["id"]),
        "question": question,
        "effective_family": effective_family,
        "decomposition_status": status,
        "route": route,
        "reason_codes": sorted(set(reason_codes)),
        "entities": tickers,
        "years": [int(value) for value in source_plan.get("years") or []],
        "scope": scope,
        "requested_unit": requested_unit,
        "operands": operands,
        "operation_ast": operation_ast,
        "formula_id": formula_id if route == "controlled_formula_template" else None,
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "source_title_entity_resolution": (
            {
                "policy": str(entity_resolution["policy"]),
                "ticker": str(entity_resolution["ticker"]),
                "matched_canonical_entities": list(
                    entity_resolution.get("matched_canonical_entities") or []
                ),
                "matched_document_ids": list(entity_resolution.get("matched_document_ids") or []),
                "scope_inferred": False,
            }
            if entity_resolution is not None
            else None
        ),
    }
    core["plan_fingerprint"] = _stable_hash(
        {
            "effective_family": core["effective_family"],
            "status": status,
            "route": route,
            "operands": operands,
            "operation_ast": operation_ast,
        }
    )
    return core
