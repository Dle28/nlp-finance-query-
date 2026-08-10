"""Constrained shadow QueryProgram for explicitly modelled multi-stage QA.

The program layer is deliberately downstream of grounding. It compiles only a
controlled Formula EvidenceSet template and evaluates only caller-supplied,
already coherent operand values. It cannot retrieve a table, repair OCR,
choose a reporting scope, promote a review label, or create a submission
answer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Mapping


QUERY_PROGRAM_SCHEMA_VERSION = 1
QUERY_PROGRAM_PROTOCOL = "query_program_shadow_v1"
QUICK_GPM_ICR_FORMULA_ID = "quick_ratio_gpm_interest_coverage_selection"


class QueryProgramError(ValueError):
    """Raised when a controlled program cannot be compiled safely."""


@dataclass(frozen=True, slots=True)
class QueryProgramStage:
    stage_id: str
    operator: str
    input_operand_ids: list[str]
    output_name: str
    policy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QueryProgram:
    program_id: str
    source_formula_id: str
    entities: list[str]
    old_year: int
    new_year: int
    required_operand_ids: list[str]
    # Key format is ``entity|role|year``.  Keeping this explicit means the
    # evaluator never reconstructs a source operand identifier from a ticker
    # or relies on one particular Formula EvidenceSet naming convention.
    operand_bindings: dict[str, str]
    stages: list[QueryProgramStage]
    execution_mode: str = "shadow_only"
    submission_eligible: bool = False
    review_status_promotion_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = QUERY_PROGRAM_SCHEMA_VERSION
        payload["protocol"] = QUERY_PROGRAM_PROTOCOL
        payload["stages"] = [stage.to_dict() for stage in self.stages]
        return payload


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise QueryProgramError(f"Operand value is not a decimal: {value!r}") from error


def _operands_by_entity_role(
    formula: Mapping[str, Any],
) -> tuple[list[str], int, int, dict[tuple[str, str, int], str]]:
    entities = [str(value).strip() for value in formula.get("entities") or [] if str(value).strip()]
    if len(entities) < 2 or len(set(entities)) != len(entities):
        raise QueryProgramError("Controlled multi-stage program requires unique explicit entities")
    operands = [dict(value) for value in formula.get("operands") or [] if value.get("required", True)]
    by_key: dict[tuple[str, str, int], str] = {}
    gross_years: set[int] = set()
    for operand in operands:
        entity = str(operand.get("entity") or "").strip()
        role = str(operand.get("role") or "").strip()
        years = [int(value) for value in operand.get("years") or [] if str(value).isdigit()]
        operand_id = str(operand.get("operand_id") or "").strip()
        if entity not in entities or not role or len(years) != 1 or not operand_id:
            continue
        year = years[0]
        key = (entity, role, year)
        if key in by_key:
            raise QueryProgramError(f"Duplicate controlled operand: {key}")
        by_key[key] = operand_id
        if role in {"gross_margin_numerator", "gross_margin_denominator"}:
            gross_years.add(year)
    if len(gross_years) != 2:
        raise QueryProgramError("Gross-margin stage must have exactly two explicit years")
    old_year, new_year = sorted(gross_years)
    requirements: list[tuple[str, int]] = [
        ("quick_ratio_numerator_base", old_year),
        ("quick_ratio_subtract", old_year),
        ("quick_ratio_denominator", old_year),
        ("gross_margin_numerator", old_year),
        ("gross_margin_denominator", old_year),
        ("gross_margin_numerator", new_year),
        ("gross_margin_denominator", new_year),
        ("interest_coverage_pbt_component", new_year),
        ("interest_coverage_denominator", new_year),
    ]
    missing = [f"{entity}:{role}:{year}" for entity in entities for role, year in requirements if (entity, role, year) not in by_key]
    if missing:
        raise QueryProgramError("Controlled program lacks operands: " + ", ".join(missing))
    return entities, old_year, new_year, by_key


def _binding_key(entity: str, role: str, year: int) -> str:
    return f"{entity}|{role}|{year}"


def compile_query_program(formula: Mapping[str, Any]) -> QueryProgram | None:
    """Compile one allow-listed Formula EvidenceSet template to a QueryProgram."""
    if str(formula.get("formula_id") or "") != QUICK_GPM_ICR_FORMULA_ID:
        return None
    if str(formula.get("execution_status") or "") != "stage_binding_required":
        raise QueryProgramError("Controlled QueryProgram requires stage_binding_required source formula")
    entities, old_year, new_year, by_key = _operands_by_entity_role(formula)
    quick_inputs = [
        by_key[(entity, role, old_year)]
        for entity in entities
        for role in (
            "quick_ratio_numerator_base",
            "quick_ratio_subtract",
            "quick_ratio_denominator",
        )
    ]
    margin_inputs = [
        by_key[(entity, role, year)]
        for entity in entities
        for year in (old_year, new_year)
        for role in ("gross_margin_numerator", "gross_margin_denominator")
    ]
    coverage_inputs = [
        by_key[(entity, role, new_year)]
        for entity in entities
        for role in ("interest_coverage_pbt_component", "interest_coverage_denominator")
    ]
    required = quick_inputs + margin_inputs + coverage_inputs
    return QueryProgram(
        program_id="quick_ratio_gpm_interest_coverage_selection_v1",
        source_formula_id=QUICK_GPM_ICR_FORMULA_ID,
        entities=entities,
        old_year=old_year,
        new_year=new_year,
        required_operand_ids=required,
        operand_bindings={
            _binding_key(entity, role, year): operand_id
            for (entity, role, year), operand_id in by_key.items()
        },
        stages=[
            QueryProgramStage(
                stage_id="quick_ratio_filter",
                operator="strict_less_than_group_median",
                input_operand_ids=quick_inputs,
                output_name="eligible_entities",
                policy="quick_ratio=(current_assets-inventory)/current_liabilities; entity qualifies only when ratio < median",
            ),
            QueryProgramStage(
                stage_id="gross_margin_rank",
                operator="argmax_signed_margin_change",
                input_operand_ids=margin_inputs,
                output_name="winning_entity",
                policy="gross_margin=gross_profit/net_revenue; rank signed new-old change; ties block",
            ),
            QueryProgramStage(
                stage_id="interest_coverage_lookup",
                operator="interest_coverage_from_pbt_and_interest_expense",
                input_operand_ids=coverage_inputs,
                output_name="shadow_result",
                policy="coverage=(profit_before_tax+abs(interest_expense))/abs(interest_expense); zero denominator blocks",
            ),
        ],
    )


def shadow_readiness(
    evidence_set: Mapping[str, Any], program: QueryProgram | None,
) -> dict[str, Any]:
    """Report why a compiled program may not evaluate an EvidenceSet yet."""
    if program is None:
        return {"status": "not_applicable", "reason_codes": ["formula_not_query_program_allowlisted"]}
    reasons: list[str] = []
    if str((evidence_set.get("formula") or {}).get("definition_status") or "") != "defined":
        reasons.append("formula_definition_not_defined")
    if str(evidence_set.get("evidence_completeness") or "") != "complete":
        reasons.append("evidence_set_not_complete")
    if not evidence_set.get("selected_operand_matches"):
        reasons.append("coherent_operand_bindings_missing")
    for reason in evidence_set.get("reason_codes") or []:
        if str(reason) not in reasons:
            reasons.append(str(reason))
    return {
        "status": "shadow_ready" if not reasons else "shadow_blocked",
        "reason_codes": reasons,
        "submission_eligible": False,
        "review_status_promotion_allowed": False,
    }


def evaluate_shadow_query_program(
    program: QueryProgram,
    operand_values: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one coherent, already-grounded operand map in shadow mode.

    This function deliberately accepts only values keyed by the program's exact
    operand IDs. Evidence/provenance validation is performed before this call
    by the Formula EvidenceSet and caller; this evaluator does not access raw
    tables or construct bindings.
    """
    missing = [operand_id for operand_id in program.required_operand_ids if operand_id not in operand_values]
    if missing:
        return {
            "status": "shadow_blocked",
            "reason_codes": ["required_operand_values_missing"],
            "missing_operand_ids": missing,
            "submission_eligible": False,
        }
    try:
        values = {operand_id: _as_decimal(operand_values[operand_id]) for operand_id in program.required_operand_ids}

        def operand(entity: str, role: str, year: int) -> Decimal:
            key = _binding_key(entity, role, year)
            try:
                return values[program.operand_bindings[key]]
            except KeyError as error:
                raise QueryProgramError(f"Program operand binding is missing: {key}") from error

        quick: dict[str, Decimal] = {}
        for entity in program.entities:
            current_assets = operand(entity, "quick_ratio_numerator_base", program.old_year)
            inventory = operand(entity, "quick_ratio_subtract", program.old_year)
            current_liabilities = operand(entity, "quick_ratio_denominator", program.old_year)
            if current_liabilities == 0:
                raise QueryProgramError(f"Quick-ratio denominator is zero: {entity}")
            quick[entity] = (current_assets - inventory) / current_liabilities
        threshold = Decimal(str(median(quick.values())))
        eligible = [entity for entity in program.entities if quick[entity] < threshold]
        if not eligible:
            raise QueryProgramError("No entity is strictly below the group median")

        changes: dict[str, Decimal] = {}
        for entity in eligible:
            old_revenue = operand(entity, "gross_margin_denominator", program.old_year)
            new_revenue = operand(entity, "gross_margin_denominator", program.new_year)
            if old_revenue == 0 or new_revenue == 0:
                raise QueryProgramError(f"Gross-margin denominator is zero: {entity}")
            old_margin = operand(entity, "gross_margin_numerator", program.old_year) / old_revenue
            new_margin = operand(entity, "gross_margin_numerator", program.new_year) / new_revenue
            changes[entity] = new_margin - old_margin
        best_change = max(changes.values())
        winners = [entity for entity in eligible if changes[entity] == best_change]
        if len(winners) != 1:
            raise QueryProgramError("Gross-margin change has no unique winner")
        winner = winners[0]
        profit_before_tax = operand(winner, "interest_coverage_pbt_component", program.new_year)
        interest_expense = abs(operand(winner, "interest_coverage_denominator", program.new_year))
        if interest_expense == 0:
            raise QueryProgramError("Interest-coverage denominator is zero")
        result = (profit_before_tax + interest_expense) / interest_expense
    except QueryProgramError as error:
        return {
            "status": "shadow_blocked",
            "reason_codes": ["query_program_arithmetic_precondition_failed"],
            "detail": str(error),
            "submission_eligible": False,
        }
    return {
        "status": "shadow_complete",
        "winner_entity": winner,
        "result_value": format(result, "f"),
        "result_unit": "times",
        "stage_trace": {
            "quick_ratio": {entity: format(value, "f") for entity, value in quick.items()},
            "median": format(threshold, "f"),
            "eligible_entities": eligible,
            "gross_margin_change": {entity: format(value, "f") for entity, value in changes.items()},
        },
        "submission_eligible": False,
        "review_status_promotion_allowed": False,
        "execution_mode": "shadow_only",
    }
