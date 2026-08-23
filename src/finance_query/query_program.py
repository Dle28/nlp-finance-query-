"""Constrained shadow QueryProgram for explicitly modelled multi-stage QA.

The program layer is deliberately downstream of grounding. It compiles only a
controlled Formula EvidenceSet template and evaluates only caller-supplied,
already coherent operand values. It cannot retrieve a table, repair OCR,
choose a reporting scope, promote a review label, or create a submission
answer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .staged_execution import execute_staged_ast_shadow


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
    config: dict[str, Any] = field(default_factory=dict)
    entity_source: str | None = None

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
    result_name: str = "shadow_result"
    result_unit: str = "source_unit"
    execution_mode: str = "shadow_only"
    submission_eligible: bool = False
    review_status_promotion_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = QUERY_PROGRAM_SCHEMA_VERSION
        payload["protocol"] = QUERY_PROGRAM_PROTOCOL
        payload["stages"] = [stage.to_dict() for stage in self.stages]
        return payload


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
                operator="filter_all_positive",
                input_operand_ids=cfo_inputs,
                output_name="eligible_entities",
                policy="entity qualifies only when every exact CFO operand is > 0",
                config={
                    "input_bindings": {
                        entity: [by_key[(entity, "cfo_positive_screen", year)] for year in screening_years]
                        for entity in entities
                    }
                },
            ),
            QueryProgramStage(
                stage_id="net_margin_rank",
                operator="map_ast",
                input_operand_ids=margin_inputs,
                output_name="net_margin_percent",
                policy="net_margin=after_tax_profit/net_revenue; ties block",
                entity_source="@eligible_entities",
                config={
                    "ast": {
                        "op": "ratio_to_percent",
                        "args": [{"op": "divide", "args": ["profit", "revenue"]}],
                    },
                    "input_bindings": {
                        entity: {
                            "profit": by_key[(entity, "net_margin_numerator", target_year)],
                            "revenue": by_key[(entity, "net_margin_denominator", target_year)],
                        }
                        for entity in entities
                    },
                },
            ),
            QueryProgramStage(
                stage_id="net_margin_winner",
                operator="argmax_unique",
                input_operand_ids=[],
                output_name="winning_entity",
                policy="ties block",
                config={"source": "@net_margin_percent"},
            ),
            QueryProgramStage(
                stage_id="target_output",
                operator="select_entity_value",
                input_operand_ids=[],
                output_name="shadow_result",
                policy="select the computed value for the unique winner",
                config={"values": "@net_margin_percent", "entity": "@winning_entity"},
            ),
        ],
        screen_years=screening_years,
        target_year=target_year,
        result_unit="percent",
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
                stage_id="quick_ratio",
                operator="map_ast",
                input_operand_ids=quick_inputs,
                output_name="quick_ratio",
                policy="quick_ratio=(current_assets-inventory)/current_liabilities",
                config={
                    "ast": {
                        "op": "divide",
                        "args": [
                            {"op": "subtract", "args": ["assets", "inventory"]},
                            "liabilities",
                        ],
                    },
                    "input_bindings": {
                        entity: {
                            "assets": by_key[(entity, "quick_ratio_numerator_base", old_year)],
                            "inventory": by_key[(entity, "quick_ratio_subtract", old_year)],
                            "liabilities": by_key[(entity, "quick_ratio_denominator", old_year)],
                        }
                        for entity in entities
                    },
                },
            ),
            QueryProgramStage(
                stage_id="quick_ratio_median",
                operator="reduce_ast",
                input_operand_ids=[],
                output_name="quick_ratio_median",
                policy="median over exactly the explicit entity group",
                config={"source": "@quick_ratio", "ast": {"op": "median", "args": ["values"]}},
            ),
            QueryProgramStage(
                stage_id="quick_ratio_filter",
                operator="filter_lt",
                input_operand_ids=[],
                output_name="eligible_entities",
                policy="strictly below median; equality is excluded",
                config={"values": "@quick_ratio", "threshold": "@quick_ratio_median"},
            ),
            QueryProgramStage(
                stage_id="gross_margin_old",
                operator="map_ast",
                input_operand_ids=margin_inputs,
                output_name="gross_margin_old",
                policy="gross_profit/net_revenue for old year",
                entity_source="@eligible_entities",
                config={
                    "ast": {"op": "divide", "args": ["profit", "revenue"]},
                    "input_bindings": {
                        entity: {
                            "profit": by_key[(entity, "gross_margin_numerator", old_year)],
                            "revenue": by_key[(entity, "gross_margin_denominator", old_year)],
                        }
                        for entity in entities
                    },
                },
            ),
            QueryProgramStage(
                stage_id="gross_margin_new",
                operator="map_ast",
                input_operand_ids=margin_inputs,
                output_name="gross_margin_new",
                policy="gross_profit/net_revenue for new year",
                entity_source="@eligible_entities",
                config={
                    "ast": {"op": "divide", "args": ["profit", "revenue"]},
                    "input_bindings": {
                        entity: {
                            "profit": by_key[(entity, "gross_margin_numerator", new_year)],
                            "revenue": by_key[(entity, "gross_margin_denominator", new_year)],
                        }
                        for entity in entities
                    },
                },
            ),
            QueryProgramStage(
                stage_id="gross_margin_change",
                operator="map_ast",
                input_operand_ids=[],
                output_name="gross_margin_change",
                policy="signed new minus old change",
                entity_source="@eligible_entities",
                config={
                    "ast": {"op": "subtract", "args": ["new", "old"]},
                    "input_bindings": {
                        entity: {"new": "@gross_margin_new", "old": "@gross_margin_old"}
                        for entity in entities
                    },
                },
            ),
            QueryProgramStage(
                stage_id="gross_margin_winner",
                operator="argmax_unique",
                input_operand_ids=[],
                output_name="winning_entity",
                policy="largest signed change; ties block",
                config={"source": "@gross_margin_change"},
            ),
            QueryProgramStage(
                stage_id="interest_coverage",
                operator="map_ast",
                input_operand_ids=coverage_inputs,
                output_name="interest_coverage",
                policy="coverage=(profit_before_tax+abs(interest_expense))/abs(interest_expense)",
                config={
                    "ast": {
                        "op": "divide",
                        "args": [
                            {"op": "add", "args": ["pbt", {"op": "absolute", "args": ["interest"]}]},
                            {"op": "absolute", "args": ["interest"]},
                        ],
                    },
                    "input_bindings": {
                        entity: {
                            "pbt": by_key[(entity, "interest_coverage_pbt_component", new_year)],
                            "interest": by_key[(entity, "interest_coverage_denominator", new_year)],
                        }
                        for entity in entities
                    },
                },
            ),
            QueryProgramStage(
                stage_id="interest_coverage_lookup",
                operator="select_entity_value",
                input_operand_ids=[],
                output_name="shadow_result",
                policy="select target metric for the unique winning entity",
                config={"values": "@interest_coverage", "entity": "@winning_entity"},
            ),
        ],
        result_unit="times",
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
    """Run a compiled program through the generic staged AST executor."""
    missing = [operand_id for operand_id in program.required_operand_ids if operand_id not in operand_values]
    if missing:
        return {
            "status": "shadow_blocked",
            "reason_codes": ["required_operand_values_missing"],
            "missing_operand_ids": missing,
            "submission_eligible": False,
        }
    return execute_staged_ast_shadow(
        [stage.to_dict() for stage in program.stages],
        operand_values,
        entities=program.entities,
        result_name=program.result_name,
        result_unit=program.result_unit,
    )


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
