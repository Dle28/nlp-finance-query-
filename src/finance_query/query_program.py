"""Constrained shadow QueryProgram for explicitly modelled multi-stage QA.

The program layer is deliberately downstream of grounding. It compiles only a
controlled Formula EvidenceSet template and evaluates only caller-supplied,
already coherent operand values. It cannot retrieve a table, repair OCR,
choose a reporting scope, promote a review label, or create a submission
answer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Mapping


QUERY_PROGRAM_SCHEMA_VERSION = 1
QUERY_PROGRAM_PROTOCOL = "query_program_shadow_v1"
QUICK_GPM_ICR_FORMULA_ID = "quick_ratio_gpm_interest_coverage_selection"
CFO_NPM_FORMULA_ID = "cfo_positive_multiyear_max_net_margin"


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
    screen_years: list[int] = field(default_factory=list)
    target_year: int | None = None
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


def _controlled_operands(
    formula: Mapping[str, Any],
) -> tuple[list[str], dict[tuple[str, str, int], str]]:
    """Read explicit required operands without deriving entity/year metadata."""
    entities = [str(value).strip() for value in formula.get("entities") or [] if str(value).strip()]
    if len(entities) < 2 or len(set(entities)) != len(entities):
        raise QueryProgramError("Controlled multi-stage program requires unique explicit entities")
    by_key: dict[tuple[str, str, int], str] = {}
    for operand in formula.get("operands") or []:
        if not bool(operand.get("required", True)):
            continue
        entity = str(operand.get("entity") or "").strip()
        role = str(operand.get("role") or "").strip()
        years = [int(value) for value in operand.get("years") or [] if str(value).isdigit()]
        operand_id = str(operand.get("operand_id") or "").strip()
        if entity not in entities or not role or len(years) != 1 or not operand_id:
            continue
        key = (entity, role, years[0])
        if key in by_key:
            raise QueryProgramError(f"Duplicate controlled operand: {key}")
        by_key[key] = operand_id
    return entities, by_key


def _binding_key(entity: str, role: str, year: int) -> str:
    return f"{entity}|{role}|{year}"


def _compile_cfo_positive_max_net_margin(formula: Mapping[str, Any]) -> QueryProgram:
    entities, by_key = _controlled_operands(formula)
    screening_years = sorted(
        {
            year
            for _entity, role, year in by_key
            if role == "cfo_positive_screen"
        }
    )
    target_years = {
        year
        for _entity, role, year in by_key
        if role in {"net_margin_numerator", "net_margin_denominator"}
    }
    if not screening_years or len(target_years) != 1:
        raise QueryProgramError("CFO/NPM program requires screening years and one target year")
    target_year = next(iter(target_years))
    required_roles = [
        *(("cfo_positive_screen", year) for year in screening_years),
        ("net_margin_numerator", target_year),
        ("net_margin_denominator", target_year),
    ]
    missing = [
        f"{entity}:{role}:{year}"
        for entity in entities
        for role, year in required_roles
        if (entity, role, year) not in by_key
    ]
    if missing:
        raise QueryProgramError("CFO/NPM program lacks operands: " + ", ".join(missing))
    cfo_inputs = [
        by_key[(entity, "cfo_positive_screen", year)]
        for entity in entities
        for year in screening_years
    ]
    margin_inputs = [
        by_key[(entity, role, target_year)]
        for entity in entities
        for role in ("net_margin_numerator", "net_margin_denominator")
    ]
    return QueryProgram(
        program_id="cfo_positive_multiyear_max_net_margin_v1",
        source_formula_id=CFO_NPM_FORMULA_ID,
        entities=entities,
        old_year=screening_years[0],
        new_year=target_year,
        required_operand_ids=cfo_inputs + margin_inputs,
        operand_bindings={
            _binding_key(entity, role, year): operand_id
            for (entity, role, year), operand_id in by_key.items()
        },
        stages=[
            QueryProgramStage(
                stage_id="cfo_positive_filter",
                operator="all_strictly_positive_by_screening_year",
                input_operand_ids=cfo_inputs,
                output_name="eligible_entities",
                policy="entity qualifies only when every exact CFO operand is > 0",
            ),
            QueryProgramStage(
                stage_id="net_margin_rank",
                operator="argmax_net_profit_to_net_revenue",
                input_operand_ids=margin_inputs,
                output_name="winning_entity",
                policy="net_margin=after_tax_profit/net_revenue; ties block",
            ),
            QueryProgramStage(
                stage_id="target_output",
                operator="emit_winning_net_margin_percent",
                input_operand_ids=margin_inputs,
                output_name="shadow_result",
                policy="return only the winning entity's exact computed net margin; shadow-only",
            ),
        ],
        screen_years=screening_years,
        target_year=target_year,
    )


def compile_query_program(formula: Mapping[str, Any]) -> QueryProgram | None:
    """Compile one allow-listed Formula EvidenceSet template to a QueryProgram."""
    formula_id = str(formula.get("formula_id") or "")
    if formula_id not in {QUICK_GPM_ICR_FORMULA_ID, CFO_NPM_FORMULA_ID}:
        return None
    if str(formula.get("execution_status") or "") != "stage_binding_required":
        raise QueryProgramError("Controlled QueryProgram requires stage_binding_required source formula")
    if formula_id == CFO_NPM_FORMULA_ID:
        return _compile_cfo_positive_max_net_margin(formula)
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
    selected = evidence_set.get("selected_operand_matches") or {}
    selected_ids = {str(operand_id) for operand_id in selected}
    missing_selected = [operand_id for operand_id in program.required_operand_ids if operand_id not in selected_ids]
    if missing_selected:
        reasons.append("coherent_operand_bindings_missing")
    if not missing_selected and not _selected_binding_metadata_is_coherent(program, selected):
        reasons.append("selected_operand_metadata_not_coherent")
    # Formula EvidenceSet marks staged questions ``partial`` before an
    # allow-listed executor proves the stages. That is not itself an arithmetic
    # defect when every operand is selected and the only remaining reasons are
    # precisely the staged-execution hand-off. All other partial states remain
    # fail-closed.
    allowed_stage_handoff = {
        "formula_requires_stage_binding",
        "question_family_requires_composed_execution",
    }
    source_reasons = [str(reason) for reason in evidence_set.get("reason_codes") or []]
    if str(evidence_set.get("evidence_completeness") or "") != "complete" and not (
        str(evidence_set.get("operand_coverage_status") or "") == "complete"
        and not missing_selected
        and _selected_binding_metadata_is_coherent(program, selected)
        and set(source_reasons).issubset(allowed_stage_handoff)
    ):
        reasons.append("evidence_set_not_complete")
    for reason in source_reasons:
        if reason in allowed_stage_handoff and not missing_selected:
            continue
        if str(reason) not in reasons:
            reasons.append(str(reason))
    return {
        "status": "shadow_ready" if not reasons else "shadow_blocked",
        "reason_codes": reasons,
        "submission_eligible": False,
        "review_status_promotion_allowed": False,
    }


def _selected_binding_metadata_is_coherent(
    program: QueryProgram,
    selected: Mapping[str, Any],
) -> bool:
    """Require selected Formula bindings to agree with the explicit program.

    Formula coverage may show that an operand exists in several reports.  A
    staged executor may use a selected value only after the selection itself
    proves its entity, reporting year and one common reporting scope.  This
    helper never chooses a scope; it merely rejects an already-selected map
    when that contract is absent.
    """
    observed_scopes: set[str] = set()
    source_units: dict[tuple[str, str], str] = {}
    for key, operand_id in program.operand_bindings.items():
        entity, role, year_text = key.split("|", maxsplit=2)
        match = selected.get(operand_id)
        if not isinstance(match, Mapping):
            return False
        ticker = str(match.get("ticker") or "").strip()
        scope = str(match.get("scope") or "").strip()
        try:
            report_year = int(match.get("report_year"))
            expected_year = int(year_text)
        except (TypeError, ValueError):
            return False
        if ticker.casefold() != entity.casefold() or report_year != expected_year or not scope:
            return False
        observed_scopes.add(scope.casefold())
        source_unit = str(match.get("source_unit") or "").strip()
        if role in {"net_margin_numerator", "net_margin_denominator"}:
            if not source_unit:
                return False
            source_units[(entity.casefold(), role)] = source_unit.casefold()
    if len(observed_scopes) != 1:
        return False
    if program.source_formula_id == CFO_NPM_FORMULA_ID:
        return all(
            source_units.get((entity.casefold(), "net_margin_numerator"))
            == source_units.get((entity.casefold(), "net_margin_denominator"))
            for entity in program.entities
        )
    return True


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

        if program.source_formula_id == CFO_NPM_FORMULA_ID:
            return _evaluate_cfo_positive_max_net_margin(program, operand)

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


def _evaluate_cfo_positive_max_net_margin(
    program: QueryProgram,
    operand: Any,
) -> dict[str, Any]:
    """Execute only the second allow-listed staged formula in shadow mode."""
    try:
        if not program.screen_years or program.target_year is None:
            raise QueryProgramError("CFO/NPM program lacks explicit years")
        cfo_values = {
            entity: {
                year: operand(entity, "cfo_positive_screen", year)
                for year in program.screen_years
            }
            for entity in program.entities
        }
        eligible = [
            entity
            for entity in program.entities
            if all(value > 0 for value in cfo_values[entity].values())
        ]
        if not eligible:
            raise QueryProgramError("No entity has positive CFO for every screening year")
        margins: dict[str, Decimal] = {}
        for entity in eligible:
            revenue = operand(entity, "net_margin_denominator", program.target_year)
            if revenue == 0:
                raise QueryProgramError(f"Net-margin denominator is zero: {entity}")
            margins[entity] = operand(entity, "net_margin_numerator", program.target_year) / revenue
        best_margin = max(margins.values())
        winners = [entity for entity in eligible if margins[entity] == best_margin]
        if len(winners) != 1:
            raise QueryProgramError("Net-margin ranking has no unique winner")
        winner = winners[0]
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
        "result_value": format(best_margin * Decimal("100"), "f"),
        "result_unit": "percent",
        "stage_trace": {
            "cfo_by_entity_and_year": {
                entity: {str(year): format(value, "f") for year, value in values.items()}
                for entity, values in cfo_values.items()
            },
            "eligible_entities": eligible,
            "net_margin_fraction": {entity: format(value, "f") for entity, value in margins.items()},
        },
        "submission_eligible": False,
        "review_status_promotion_allowed": False,
        "execution_mode": "shadow_only",
    }


def operand_values_from_selected_matches(
    program: QueryProgram,
    evidence_set: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract only prevalidated parsed values from selected Formula bindings.

    This does not read a table or repair a raw cell.  The caller must validate
    the Formula EvidenceSet manifest before it invokes this helper.
    """
    selected = evidence_set.get("selected_operand_matches") or {}
    values: dict[str, str] = {}
    missing: list[str] = []
    invalid: list[str] = []
    for operand_id in program.required_operand_ids:
        match = selected.get(operand_id)
        if not isinstance(match, Mapping):
            missing.append(operand_id)
            continue
        binding = match.get("binding") or {}
        if str(binding.get("status") or "") != "cell_bound":
            invalid.append(operand_id)
            continue
        warnings = [
            str(warning)
            for warning in binding.get("parse_warnings") or []
            if str(warning) != "percent_value_not_scaled"
        ]
        value = binding.get("parsed_value")
        if warnings or value in {None, ""}:
            invalid.append(operand_id)
            continue
        values[operand_id] = str(value)
    if missing or invalid:
        return {
            "status": "shadow_blocked",
            "reason_codes": ["selected_operand_values_not_exact_cell_bound"],
            "missing_operand_ids": missing,
            "invalid_operand_ids": invalid,
            "submission_eligible": False,
        }
    return {"status": "shadow_values_ready", "operand_values": values}
