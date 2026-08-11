"""Exact-source submission records for a small, allow-listed staged program.

The public submission contract accepts one runnable pandas expression per
question.  A staged financial question needs more than a final arithmetic
formula: its expression must also prove the median screen and, when relevant,
the unique ranking decision.  This module compiles only the two audited Qwen
route families below.  It has no model dependency and refuses a malformed or
incomplete set of exact cell bindings.

It deliberately does *not* decide provenance or submission eligibility.  That
is the responsibility of the production-ledger and full-corpus audit gates.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Any, Mapping

from .schemas import DirectBinding
from .submission import (
    SubmissionValidationError,
    _binding_csv_path,
    _binding_value_query,
    _write_binding_table,
)


Q368_FAMILY = "quick_ratio_median_then_net_profit_margin"
Q369_FAMILY = "quick_ratio_gpm_interest_coverage_selection"
SUPPORTED_STAGED_SUBMISSION_FAMILIES = {Q368_FAMILY, Q369_FAMILY}


def _decimal(value: Any, *, question_id: int, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise SubmissionValidationError(
            f"Q{question_id}: {label} is not a decimal"
        ) from error
    if not result.is_finite():
        raise SubmissionValidationError(f"Q{question_id}: {label} must be finite")
    return result


def _require_binding(
    bindings: Mapping[str, tuple[DirectBinding, Mapping[str, Any]]],
    operand_id: str,
    *,
    question_id: int,
) -> tuple[DirectBinding, Mapping[str, Any]]:
    try:
        binding, asset = bindings[operand_id]
    except (KeyError, TypeError, ValueError) as error:
        raise SubmissionValidationError(
            f"Q{question_id}: staged contract is missing operand {operand_id!r}"
        ) from error
    if not isinstance(binding, DirectBinding):
        raise SubmissionValidationError(
            f"Q{question_id}: staged operand {operand_id!r} lacks DirectBinding"
        )
    return binding, asset


def _materialize_operands(
    bindings: Mapping[str, tuple[DirectBinding, Mapping[str, Any]]],
    *,
    question_id: int,
    package_root: Any,
) -> tuple[dict[str, str], dict[str, Decimal], list[str], list[str], list[dict[str, str]]]:
    """Write source CSVs and return literal query/value maps for every operand."""
    if not bindings:
        raise SubmissionValidationError(f"Q{question_id}: no staged source bindings")
    prepared: list[tuple[str, DirectBinding, Mapping[str, Any], str, int, Any]] = []
    by_csv: dict[Any, list[tuple[DirectBinding, Mapping[str, Any]]]] = {}
    for operand_id, pair in bindings.items():
        if not isinstance(operand_id, str) or not operand_id:
            raise SubmissionValidationError(f"Q{question_id}: invalid staged operand id")
        try:
            binding, asset = pair
        except (TypeError, ValueError) as error:
            raise SubmissionValidationError(
                f"Q{question_id}: invalid staged binding {operand_id!r}"
            ) from error
        if not isinstance(binding, DirectBinding):
            raise SubmissionValidationError(
                f"Q{question_id}: staged binding {operand_id!r} lacks DirectBinding"
            )
        document_id, ordinal, relative_csv = _binding_csv_path(
            binding, asset, question_id=question_id
        )
        prepared.append((operand_id, binding, asset, document_id, ordinal, relative_csv))
        by_csv.setdefault(relative_csv, []).append((binding, asset))

    variable_by_csv: dict[Any, str] = {}
    evidence: list[dict[str, str]] = []
    for relative_csv, grouped in by_csv.items():
        first_binding, first_asset = grouped[0]
        table_uid = str(first_asset.get("internal_table_uid") or first_binding.internal_table_uid)
        if any(
            str(asset.get("internal_table_uid") or binding.internal_table_uid) != table_uid
            for binding, asset in grouped
        ):
            raise SubmissionValidationError(
                f"Q{question_id}: one staged evidence CSV combines source tables"
            )
        _write_binding_table(
            first_binding,
            first_asset,
            package_root,
            question_id=question_id,
            bindings=[binding for binding, _asset in grouped],
        )
        variable = f"df{len(variable_by_csv) + 1}"
        variable_by_csv[relative_csv] = variable
        evidence.append({"variable": variable, "csv_path": relative_csv.as_posix()})

    values: dict[str, Decimal] = {}
    queries: dict[str, str] = {}
    documents: list[str] = []
    tables: list[str] = []
    for operand_id, binding, _asset, document_id, ordinal, relative_csv in prepared:
        if binding.source_unit not in {None, "vnd", "thousand_vnd", "million_vnd", "billion_vnd", "trillion_vnd"}:
            raise SubmissionValidationError(
                f"Q{question_id}: unsupported staged source unit {binding.source_unit!r}"
            )
        values[operand_id] = _decimal(
            binding.parsed_value, question_id=question_id, label=f"operand {operand_id!r}"
        )
        queries[operand_id] = _binding_value_query(variable_by_csv[relative_csv], binding)
        if document_id not in documents:
            documents.append(document_id)
        table_ref = f"{document_id}|{ordinal}"
        if table_ref not in tables:
            tables.append(table_ref)
    return queries, values, documents, tables, evidence


def _quick_ratio_expression(values: Mapping[str, str], entity: str) -> str:
    prefix = f"quick_ratio_screen.{entity}."
    return (
        f"(({values[prefix + 'current_assets']} - {values[prefix + 'inventory']}) "
        f"/ {values[prefix + 'current_liabilities']})"
    )


def _quick_ratio_value(values: Mapping[str, Decimal], entity: str) -> Decimal:
    prefix = f"quick_ratio_screen.{entity}."
    try:
        denominator = values[prefix + "current_liabilities"]
        if denominator == 0:
            raise ZeroDivisionError
        return (values[prefix + "current_assets"] - values[prefix + "inventory"]) / denominator
    except KeyError as error:
        raise SubmissionValidationError(
            f"staged contract is missing a quick-ratio operand for {entity}"
        ) from error
    except ZeroDivisionError as error:
        raise SubmissionValidationError(
            f"quick ratio denominator is zero for {entity}"
        ) from error


def _median_expression(expressions: list[str]) -> str:
    # The population is explicitly fixed to four entities by the route family.
    values = ", ".join(expressions)
    return f"((sorted([{values}])[1] + sorted([{values}])[2]) / 2)"


def _median_value(values: Mapping[str, Decimal], entities: list[str]) -> Decimal:
    ordered = sorted(_quick_ratio_value(values, entity) for entity in entities)
    return (ordered[1] + ordered[2]) / Decimal("2")


def _q368_contract(
    query_values: Mapping[str, str], numeric_values: Mapping[str, Decimal], *, question_id: int
) -> tuple[str, Decimal]:
    entities = ["HPG", "HSG", "MSR", "NKG"]
    quick_expressions = {entity: _quick_ratio_expression(query_values, entity) for entity in entities}
    median_expression = _median_expression([quick_expressions[entity] for entity in entities])
    median_value = _median_value(numeric_values, entities)
    eligible = [
        entity for entity in entities if _quick_ratio_value(numeric_values, entity) < median_value
    ]
    if eligible != ["HSG", "MSR"]:
        raise SubmissionValidationError(
            f"Q{question_id}: Q368 staged screen did not select exactly HSG and MSR"
        )
    condition = " and ".join(
        [
            f"({quick_expressions['HPG']} >= {median_expression})",
            f"({quick_expressions['HSG']} < {median_expression})",
            f"({quick_expressions['MSR']} < {median_expression})",
            f"({quick_expressions['NKG']} >= {median_expression})",
        ]
    )
    margins: dict[str, str] = {}
    numeric_margins: dict[str, Decimal] = {}
    for entity in eligible:
        prefix = f"net_profit_margin_after_screen.{entity}."
        try:
            margins[entity] = f"(({query_values[prefix + 'net_income']} / {query_values[prefix + 'net_revenue']}) * 100)"
            numeric_margins[entity] = (
                numeric_values[prefix + "net_income"] / numeric_values[prefix + "net_revenue"]
            ) * Decimal("100")
        except KeyError as error:
            raise SubmissionValidationError(
                f"Q{question_id}: Q368 staged margin binding is incomplete"
            ) from error
        except ZeroDivisionError as error:
            raise SubmissionValidationError(
                f"Q{question_id}: Q368 net revenue is zero for {entity}"
            ) from error
    result = sum(numeric_margins.values(), Decimal("0")) / Decimal(len(eligible))
    expression = f"float(int({condition}) * (({margins['HSG']} + {margins['MSR']}) / 2))"
    return expression, result


def _q369_contract(
    query_values: Mapping[str, str], numeric_values: Mapping[str, Decimal], *, question_id: int
) -> tuple[str, Decimal]:
    entities = ["HPG", "HSG", "MSR", "NKG"]
    quick_expressions = {entity: _quick_ratio_expression(query_values, entity) for entity in entities}
    median_expression = _median_expression([quick_expressions[entity] for entity in entities])
    median_value = _median_value(numeric_values, entities)
    eligible = [
        entity for entity in entities if _quick_ratio_value(numeric_values, entity) < median_value
    ]
    if eligible != ["HSG", "MSR"]:
        raise SubmissionValidationError(
            f"Q{question_id}: Q369 staged quick-ratio screen did not select exactly HSG and MSR"
        )
    gpm_expression: dict[tuple[str, str], str] = {}
    gpm_value: dict[tuple[str, str], Decimal] = {}
    for stage_id, period in (("gross_profit_margin_old", "old"), ("gross_profit_margin_new", "new")):
        for entity in eligible:
            prefix = f"{stage_id}.{entity}."
            try:
                gpm_expression[(entity, period)] = (
                    f"(({query_values[prefix + 'gross_profit']} / {query_values[prefix + 'net_revenue']}) * 100)"
                )
                gpm_value[(entity, period)] = (
                    numeric_values[prefix + "gross_profit"] / numeric_values[prefix + "net_revenue"]
                ) * Decimal("100")
            except KeyError as error:
                raise SubmissionValidationError(
                    f"Q{question_id}: Q369 {stage_id} binding is incomplete"
                ) from error
            except ZeroDivisionError as error:
                raise SubmissionValidationError(
                    f"Q{question_id}: Q369 net revenue is zero for {entity}/{period}"
                ) from error
    changes = {
        entity: gpm_value[(entity, "new")] - gpm_value[(entity, "old")]
        for entity in eligible
    }
    winner_value = max(changes.values())
    winner = [entity for entity in eligible if changes[entity] == winner_value]
    if winner != ["HSG"]:
        raise SubmissionValidationError(
            f"Q{question_id}: Q369 GPM transition did not produce unique HSG winner"
        )
    prefix = "interest_coverage_lookup.HSG."
    try:
        interest_expression = f"abs({query_values[prefix + 'interest_expense']})"
        result_expression = (
            f"(({query_values[prefix + 'profit_before_tax']} + {interest_expression}) / {interest_expression})"
        )
        interest = abs(numeric_values[prefix + "interest_expense"])
        if interest == 0:
            raise ZeroDivisionError
        result = (numeric_values[prefix + "profit_before_tax"] + interest) / interest
    except KeyError as error:
        raise SubmissionValidationError(
            f"Q{question_id}: Q369 interest-coverage binding is incomplete"
        ) from error
    except ZeroDivisionError as error:
        raise SubmissionValidationError(f"Q{question_id}: Q369 interest expense is zero") from error
    screen = " and ".join(
        [
            f"({quick_expressions['HPG']} >= {median_expression})",
            f"({quick_expressions['HSG']} < {median_expression})",
            f"({quick_expressions['MSR']} < {median_expression})",
            f"({quick_expressions['NKG']} >= {median_expression})",
        ]
    )
    hsg_change = f"({gpm_expression[('HSG', 'new')]} - {gpm_expression[('HSG', 'old')]})"
    msr_change = f"({gpm_expression[('MSR', 'new')]} - {gpm_expression[('MSR', 'old')]})"
    expression = f"float(int(({screen}) and ({hsg_change} > {msr_change})) * {result_expression})"
    return expression, result


def write_staged_execution_record(
    *,
    question_id: int,
    question: str,
    route_family: str,
    operand_bindings: Mapping[str, tuple[DirectBinding, Mapping[str, Any]]],
    expected_result_value: str,
    expected_result_unit: str,
    package_root: Any,
) -> dict[str, Any]:
    """Write a re-runnable exact-source record for a supported staged route."""
    if route_family not in SUPPORTED_STAGED_SUBMISSION_FAMILIES:
        raise SubmissionValidationError(
            f"Q{question_id}: unsupported staged submission family {route_family!r}"
        )
    expected_units = {
        Q368_FAMILY: "percent",
        Q369_FAMILY: "times",
    }
    if expected_result_unit != expected_units[route_family]:
        raise SubmissionValidationError(
            f"Q{question_id}: staged result unit does not match {route_family}"
        )
    query_values, numeric_values, documents, tables, evidence = _materialize_operands(
        operand_bindings, question_id=question_id, package_root=package_root
    )
    if route_family == Q368_FAMILY:
        expression, computed = _q368_contract(query_values, numeric_values, question_id=question_id)
    else:
        expression, computed = _q369_contract(query_values, numeric_values, question_id=question_id)
    expected = _decimal(expected_result_value, question_id=question_id, label="audited staged result")
    if computed != expected:
        raise SubmissionValidationError(
            f"Q{question_id}: exact staged replay result differs from audited result"
        )
    answer = float(computed)
    if not math.isfinite(answer):
        raise SubmissionValidationError(f"Q{question_id}: staged answer is not finite")
    return {
        "id": question_id,
        "question": question,
        "answer": answer,
        "relevant_docs": documents,
        "relevant_tables": tables,
        "evidence": evidence,
        "pandas_query": expression,
    }
