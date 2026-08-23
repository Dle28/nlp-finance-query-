"""Typed computational semantics for Vietnamese financial questions.

This module is a conservative bridge between literal question routing and
source-table retrieval.  It describes *what* must be looked up and *how* the
lookups depend on one another.  It never reads a value cell, executes a
formula, repairs OCR, or makes a route eligible for training/submission.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping

from .financial_taxonomy import FinancialTaxonomy, normalize_label
from .metric_registry import FinancialMetricRegistry
from .report_normalization import route_stage_candidates


COMPUTATIONAL_SEMANTICS_PROTOCOL = "computational_semantic_plan_v1"

_COUNT_POPULATION_RE = re.compile(r"\b(?:co bao nhieu|bao nhieu) (?:doanh nghiep|cong ty|ma)\b")
_EXPLICIT_RATIO_RE = re.compile(r"\b(?:tren|chia cho|so voi|chiem bao nhieu phan tram)\b")
_EXPLICIT_CHANGE_RE = re.compile(
    r"\b(?:chenh lech giua|so voi nam|tang so voi|giam so voi|tang bao nhieu|giam bao nhieu)\b"
)
_REPORTED_PERCENTAGE_RE = re.compile(r"\bty le (?:quyen bieu quyet|bieu quyet|so huu)\b")
_REPORTED_NOUN_RE = re.compile(
    r"\b(?:lo chenh lech ty gia|gia tri con lai cua [^?]+|tong cong tai san)\b"
)
_DETAIL_DIMENSION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("industry", re.compile(r"\bnganh\b")),
    ("counterparty", re.compile(r"\b(?:tu|voi) (?:cong ty|ctcp|ngan hang|tap doan|tong cong ty)\b")),
    ("third_party", re.compile(r"\bben thu ba\b")),
    ("foreign_customer", re.compile(r"\bnuoc ngoai\b")),
    ("provision", re.compile(r"\bdu phong\b")),
    ("collateral", re.compile(r"\bkhong co tai san dam bao\b")),
    ("instrument_class", re.compile(r"\bfvtpl\b")),
    ("credit_risk", re.compile(r"\brui ro tin dung\b")),
    ("deferred_tax", re.compile(r"\bthue thu nhap hoan lai\b")),
    ("intangible_subtype", re.compile(r"\btai san vo hinh\b")),
    ("net_measurement", re.compile(r"\bgia tri thuan\b")),
    ("gross_measurement", re.compile(r"\bnguyen gia\b")),
    ("carrying_measurement", re.compile(r"\bgia tri con lai\b")),
    ("fair_value_measurement", re.compile(r"\bgia tri hop ly\b")),
    ("other_line_item", re.compile(r"\bkhac\b")),
)

_OP_ARITY: dict[str, tuple[int, int | None]] = {
    "source_lookup": (0, 0),
    "unresolved_source_lookup": (0, 0),
    "lookup": (1, 1),
    "abs": (1, 1),
    "add": (2, None),
    "subtract": (2, 2),
    "divide": (2, 2),
    "ratio_to_percent": (1, 1),
    "elementwise_divide": (2, 2),
    "elementwise_subtract": (2, 2),
    "year_over_year_change": (1, 1),
    "median": (1, 1),
    "filter_entities_gt": (2, 2),
    "filter_entities_lt_zero": (1, 1),
    "filter_entities_gt_zero": (1, 1),
    "intersect_entities": (2, None),
    "select_entities": (2, 2),
    "select_period": (2, 2),
    "sum": (1, 1),
    "count": (1, 1),
    "argmin_period": (1, 1),
    "unresolved_composition": (1, None),
}


def source_contract() -> dict[str, bool]:
    """Return the immutable non-promotable boundary for semantic plans."""
    return {
        "research_only": True,
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_table_candidate": True,
        "may_select_value_cell": False,
        "may_execute_formula": False,
        "may_compute_answer": False,
        "may_infer_missing_context": False,
    }


def _question_plan(item: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    if isinstance(item.get("effective_question_plan"), Mapping):
        return dict(item["effective_question_plan"]), "effective_question_plan"
    if isinstance(item.get("question_plan"), Mapping):
        return dict(item["question_plan"]), "question_plan"
    return {}, None


def _context(item: Mapping[str, Any]) -> dict[str, Any]:
    plan, source = _question_plan(item)
    entities = [str(value).strip() for value in plan.get("tickers") or [] if str(value).strip()]
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            years.append(int(value))
        except (TypeError, ValueError):
            continue
    year_source = "supplied_question_plan"
    normalized_question = normalize_label(item.get("question") or "")
    range_match = re.search(r"\b(?:tu nam |tu )?(\d{4}) den (?:nam )?(\d{4})\b", normalized_question)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start <= end and end - start <= 20:
            years = list(range(start, end + 1))
            year_source = "explicit_question_range"
    scope = str(plan.get("scope") or "").strip() or None
    return {
        "entities": entities,
        "years": sorted(set(years)),
        "scope": scope,
        "requested_unit": plan.get("requested_unit"),
        "year_resolution": year_source,
        "source": source,
    }


def _node(node_id: str, op: str, inputs: Iterable[str] = (), **metadata: Any) -> dict[str, Any]:
    return {"node_id": node_id, "op": op, "inputs": list(inputs), **metadata}


def _leaf(
    taxonomy: FinancialTaxonomy,
    variable_id: str,
    *,
    node_id: str | None = None,
    entity_source: str = "@question_entities",
    year_source: str = "@question_years",
) -> dict[str, Any]:
    concept = taxonomy.by_id[variable_id]
    return _node(
        node_id or f"source_{variable_id}",
        "source_lookup",
        variable_id=variable_id,
        reported_or_derived="reported",
        period_type=concept.period_type,
        statement_types=list(concept.statement_types),
        entity_source=entity_source,
        year_source=year_source,
        unit_policy="source_unit_then_explicit_conversion",
    )


def _unresolved_leaf(question: str, normalized: str) -> dict[str, Any]:
    match = _REPORTED_PERCENTAGE_RE.search(normalized) or _REPORTED_NOUN_RE.search(normalized)
    phrase = match.group(0) if match else normalized
    return _node(
        "source_unresolved_reported_item",
        "unresolved_source_lookup",
        reported_or_derived="reported",
        exact_question_phrase=phrase,
        entity_source="@question_entities",
        year_source="@question_years",
        period_type="unresolved",
        statement_types=[],
        source_question=question,
    )


def _axes(question: str, taxonomy: FinancialTaxonomy) -> dict[str, list[str]]:
    normalized = normalize_label(question)
    matches: dict[str, list[str]] = {}
    for axis_name, values in taxonomy.semantic_axes.items():
        selected_values: set[str] = set()
        for value in values:
            if axis_name == "aggregation_role" and value.value_id == "other":
                # "Other" is a reported line-item qualifier, not a reliable
                # aggregation instruction in free-form question text.
                continue
            for label in value.labels_vi:
                # Single-word aggregation aliases are unsafe in whole-question
                # prose: after accent folding, ``cộng`` collides with ``công
                # ty`` and ``khác`` is commonly part of a reported line item.
                # ``bao gồm`` normally introduces an entity population, not a
                # table subtotal.  Keep only high-precision question cues.
                if axis_name == "aggregation_role" and label in {"cong", "khac", "bao gom"}:
                    continue
                if axis_name == "aggregation_role" and label == "tong":
                    pattern = r"(?<!\w)tong(?!\s+cong\s+ty)(?!\w)"
                else:
                    pattern = rf"(?<!\w){re.escape(label)}(?!\w)"
                if re.search(pattern, normalized):
                    selected_values.add(value.value_id)
                    break
        selected = sorted(selected_values)
        if selected:
            matches[axis_name] = selected
    return matches


def _inventory_median_share(taxonomy: FinancialTaxonomy) -> tuple[list[dict[str, Any]], str]:
    nodes = [
        _leaf(taxonomy, "inventory"),
        _leaf(taxonomy, "current_liabilities"),
        _node("inventory_to_liabilities", "elementwise_divide", ["source_inventory", "source_current_liabilities"], output_type="entity_series_ratio"),
        _node("group_median", "median", ["inventory_to_liabilities"], output_type="ratio"),
        _node("eligible_entities", "filter_entities_gt", ["inventory_to_liabilities", "group_median"], output_type="entity_set"),
        _node("eligible_liabilities", "select_entities", ["source_current_liabilities", "eligible_entities"], output_type="entity_series_monetary"),
        _node("eligible_liabilities_sum", "sum", ["eligible_liabilities"], output_type="monetary"),
        _node("group_liabilities_sum", "sum", ["source_current_liabilities"], output_type="monetary"),
        _node("share_ratio", "divide", ["eligible_liabilities_sum", "group_liabilities_sum"], output_type="ratio"),
        _node("answer", "ratio_to_percent", ["share_ratio"], output_type="percent"),
    ]
    return nodes, "answer"


def _working_capital_count(taxonomy: FinancialTaxonomy) -> tuple[list[dict[str, Any]], str]:
    nodes = [
        _leaf(taxonomy, "current_assets"),
        _leaf(taxonomy, "current_liabilities"),
        _leaf(taxonomy, "operating_cash_flow"),
        _node("working_capital", "elementwise_subtract", ["source_current_assets", "source_current_liabilities"], output_type="entity_series_monetary"),
        _node("negative_working_capital_entities", "filter_entities_lt_zero", ["working_capital"], output_type="entity_set"),
        _node("positive_ocf_entities", "filter_entities_gt_zero", ["source_operating_cash_flow"], output_type="entity_set"),
        _node("eligible_entities", "intersect_entities", ["negative_working_capital_entities", "positive_ocf_entities"], output_type="entity_set"),
        _node("answer", "count", ["eligible_entities"], output_type="count"),
    ]
    return nodes, "answer"


def _revenue_decline_then_cfo_margin(taxonomy: FinancialTaxonomy) -> tuple[list[dict[str, Any]], str]:
    nodes = [
        _leaf(taxonomy, "net_revenue", year_source="@question_years_with_predecessor"),
        _leaf(taxonomy, "operating_cash_flow"),
        _node("revenue_change", "year_over_year_change", ["source_net_revenue"], output_type="period_series", semantic_parameter="@decline_measure_definition"),
        _node("selected_period", "argmin_period", ["revenue_change"], output_type="period"),
        _node("selected_revenue", "select_period", ["source_net_revenue", "selected_period"], output_type="monetary"),
        _node("selected_ocf", "select_period", ["source_operating_cash_flow", "selected_period"], output_type="monetary"),
        _node("cfo_margin", "divide", ["selected_ocf", "selected_revenue"], output_type="ratio"),
        _node("answer", "ratio_to_percent", ["cfo_margin"], output_type="percent"),
    ]
    return nodes, "answer"


def _revenue_growth_negative_cfo_count(taxonomy: FinancialTaxonomy) -> tuple[list[dict[str, Any]], str]:
    nodes = [
        _leaf(taxonomy, "net_revenue"),
        _leaf(taxonomy, "operating_cash_flow", year_source="@latest_question_year"),
        _node("revenue_growth", "year_over_year_change", ["source_net_revenue"], output_type="entity_series_ratio", semantic_parameter="percentage_change"),
        _node("positive_growth_entities", "filter_entities_gt_zero", ["revenue_growth"], output_type="entity_set"),
        _node("cfo_margin", "elementwise_divide", ["source_operating_cash_flow", "source_net_revenue"], output_type="entity_series_ratio"),
        _node("negative_cfo_margin_entities", "filter_entities_lt_zero", ["cfo_margin"], output_type="entity_set"),
        _node("eligible_entities", "intersect_entities", ["positive_growth_entities", "negative_cfo_margin_entities"], output_type="entity_set"),
        _node("answer", "count", ["eligible_entities"], output_type="count"),
    ]
    return nodes, "answer"


def _formula_nodes(stage: Mapping[str, Any], taxonomy: FinancialTaxonomy) -> tuple[list[dict[str, Any]], str]:
    leaves: list[dict[str, Any]] = []
    role_to_node: dict[str, str] = {}
    concept_to_node: dict[str, str] = {}
    for index, operand in enumerate(stage.get("required_operands") or [], start=1):
        concept_id = str(operand["concept_id"])
        role = str(operand.get("role") or concept_id)
        node_id = f"source_{role}_{index}"
        leaves.append(_leaf(taxonomy, concept_id, node_id=node_id))
        role_to_node[role] = node_id
        concept_to_node.setdefault(concept_id, node_id)

    nodes = list(leaves)
    counter = 0

    def compile_expr(expr: Any) -> str:
        nonlocal counter
        if isinstance(expr, str):
            return role_to_node.get(expr) or concept_to_node.get(expr) or expr
        if not isinstance(expr, Mapping):
            raise ValueError("Formula AST contains a non-mapping expression")
        op = str(expr.get("op") or "")
        inputs = [compile_expr(arg) for arg in expr.get("args") or []]
        counter += 1
        node_id = "answer" if counter == 1 and expr is stage.get("formula_ast") else f"formula_{counter}"
        nodes.append(_node(node_id, op, inputs, output_type="derived_metric"))
        return node_id

    # Compile children before parents, then rename the root deterministically.
    root_expr = stage.get("formula_ast") or {"op": "lookup", "args": [next(iter(role_to_node.values()))]}
    root = compile_expr(root_expr)
    if root != "answer":
        for node in nodes:
            node["inputs"] = ["answer" if value == root else value for value in node["inputs"]]
        for node in nodes:
            if node["node_id"] == root:
                node["node_id"] = "answer"
                break
        root = "answer"
    return nodes, root


def _wrap_incomplete_component(
    nodes: list[dict[str, Any]],
    output_node_id: str,
) -> tuple[list[dict[str, Any]], str]:
    """Retain a known component without claiming it answers the whole question."""
    component_id = "known_component"
    for node in nodes:
        node["inputs"] = [component_id if value == output_node_id else value for value in node["inputs"]]
    for node in nodes:
        if node["node_id"] == output_node_id:
            node["node_id"] = component_id
            break
    nodes.append(
        _node(
            "answer",
            "unresolved_composition",
            [component_id],
            output_type="unresolved",
            whole_question_program_complete=False,
        )
    )
    return nodes, "answer"


def _prefixed_component(
    nodes: list[dict[str, Any]],
    output_node_id: str,
    *,
    prefix: str,
) -> tuple[list[dict[str, Any]], str]:
    """Namespace a known component before placing it in an unresolved program."""
    identifiers = [str(node.get("node_id") or "") for node in nodes]
    if not identifiers or any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("Known semantic component has invalid node identifiers")
    identifier_map = {identifier: f"{prefix}{identifier}" for identifier in identifiers}
    if output_node_id not in identifier_map:
        raise ValueError("Known semantic component has no output node")
    return (
        [
            {
                **node,
                "node_id": identifier_map[str(node["node_id"])],
                "inputs": [identifier_map[str(value)] for value in node.get("inputs") or []],
            }
            for node in nodes
        ],
        identifier_map[output_node_id],
    )


def _staged_known_components(
    stages: list[Mapping[str, Any]],
    taxonomy: FinancialTaxonomy,
) -> tuple[list[dict[str, Any]], str]:
    """Preserve all literal components without inventing their composition.

    A staged route can identify two or more valid financial metrics/concepts,
    but the order, selector, population, comparator and final aggregation may
    still be unknown.  Keeping every source leaf makes routing auditable while
    the explicit unresolved parent prevents accidental execution.
    """
    nodes: list[dict[str, Any]] = []
    component_outputs: list[str] = []
    for index, stage in enumerate(stages, start=1):
        route_kind = str(stage.get("route_kind") or "")
        if route_kind == "metric":
            component_nodes, component_output = _formula_nodes(stage, taxonomy)
        elif route_kind == "reported_concept":
            operands = list(stage.get("required_operands") or [])
            if len(operands) != 1:
                raise ValueError("Reported concept stage must have exactly one operand")
            concept_id = str(operands[0].get("concept_id") or "")
            if concept_id not in taxonomy.by_id:
                raise ValueError("Reported concept stage references unknown concept")
            leaf = _leaf(taxonomy, concept_id)
            component_nodes = [leaf, _node("answer", "lookup", [leaf["node_id"]], output_type="reported_value")]
            component_output = "answer"
        else:
            raise ValueError(f"Unsupported literal route kind in staged component: {route_kind!r}")
        prefixed, output = _prefixed_component(
            component_nodes,
            component_output,
            prefix=f"literal_stage_{index}_",
        )
        nodes.extend(prefixed)
        component_outputs.append(output)
    if len(component_outputs) < 2:
        raise ValueError("Staged known components require at least two literal stages")
    nodes.append(
        _node(
            "answer",
            "unresolved_composition",
            component_outputs,
            output_type="unresolved",
            whole_question_program_complete=False,
            literal_stage_count=len(component_outputs),
        )
    )
    return nodes, "answer"


def _metric_is_whole_question(
    item: Mapping[str, Any],
    stage: Mapping[str, Any],
) -> bool:
    """Accept only narrow one-entity/one-period metric questions as complete."""
    plan, _ = _question_plan(item)
    context = _context(item)
    family = str(plan.get("family") or "")
    old_op = str((plan.get("operation_ast") or {}).get("op") or "")
    formula_op = str((stage.get("formula_ast") or {}).get("op") or "")
    if len(context["entities"]) != 1 or len(context["years"]) != 1:
        return False
    if family == "direct_lookup":
        return old_op == "lookup" and formula_op == "lookup"
    if family == "ratio_or_derived":
        return old_op == "divide" and formula_op in {"divide", "ratio_to_percent"}
    return False


def _unmodelled_detail_dimensions(
    item: Mapping[str, Any],
    registry: FinancialMetricRegistry,
) -> list[str]:
    """Identify high-precision detail cues that a statement-line lookup omits.

    The taxonomy match can identify ``interest_expense`` inside a question about
    a named bank, or ``assets`` inside a deferred-tax/instrument schedule.  A
    canonical statement line is not sufficient in those cases.  This detector
    intentionally blocks only explicit cues; it never invents a replacement
    dimension or source table.
    """
    route = registry.build_question_route(item)
    stages = [stage for stage in route.get("stages") or [] if stage.get("route_kind") == "reported_concept"]
    if len(stages) != 1:
        return []
    stage = stages[0]
    concept_id = str(stage.get("concept_id") or "")
    span = stage.get("match_span") or []
    if not isinstance(span, list) or len(span) != 2:
        return []
    try:
        start, end = int(span[0]), int(span[1])
    except (TypeError, ValueError):
        return []
    normalized = normalize_label(item.get("question") or "")
    # The target line-item phrase is local to its literal taxonomy match.  A
    # bounded window avoids treating an unrelated sector/population clause in
    # a distant complex question as a direct-lookup dimension.
    window = normalized[max(0, start - 64) : min(len(normalized), end + 96)]
    dimensions = {
        dimension_id
        for dimension_id, pattern in _DETAIL_DIMENSION_PATTERNS
        if pattern.search(window)
    }
    suffix = normalized[end : min(len(normalized), end + 120)]
    # A named reporting entity after the matched concept is normally introduced
    # directly by ``của``.  If a named bank appears before that boundary, it is
    # a detail entity that the current company-level table contract cannot bind.
    first_of = re.search(r"\bcua\b", suffix)
    prefix_before_entity = suffix[: first_of.start()] if first_of else suffix
    if re.search(r"\bngan hang\b", prefix_before_entity):
        dimensions.add("embedded_reported_entity")
    # ``Thu nhập khác`` and ``chi phí khác`` are explicit taxonomy concepts;
    # their final token is not an omitted table dimension.  Retain the risk
    # when a broad concept (for example financial_expense) merely happens to
    # contain the word "khác" in surrounding prose.
    if concept_id in {"other_income", "other_expense", "other_profit"}:
        dimensions.discard("other_line_item")
    return sorted(dimensions)


def _simple_or_reported_plan(
    item: Mapping[str, Any],
    taxonomy: FinancialTaxonomy,
    registry: FinancialMetricRegistry,
) -> tuple[list[dict[str, Any]], str, str, list[str]]:
    question = str(item.get("question") or "")
    normalized = normalize_label(question)
    route = registry.build_question_route(item)
    stages = list(route.get("stages") or [])
    reasons: list[str] = []

    reported_exception = bool(_REPORTED_PERCENTAGE_RE.search(normalized) or _REPORTED_NOUN_RE.search(normalized))
    if reported_exception and not _EXPLICIT_RATIO_RE.search(normalized) and not _EXPLICIT_CHANGE_RE.search(normalized):
        concept_stages = [stage for stage in stages if stage.get("route_kind") == "reported_concept"]
        if len(concept_stages) == 1:
            operand = concept_stages[0]["required_operands"][0]
            leaf = _leaf(taxonomy, str(operand["concept_id"]))
        else:
            leaf = _unresolved_leaf(question, normalized)
            reasons.append("UNRESOLVED_REPORTED_LINE_ITEM")
        return [leaf, _node("answer", "lookup", [leaf["node_id"]], output_type="reported_value")], "answer", "reported_value_lookup", reasons

    if len(stages) > 1:
        nodes, output = _staged_known_components(stages, taxonomy)
        reasons.append("UNSUPPORTED_COMPOSITION_TEMPLATE")
        return nodes, output, "known_multiple_components_only", reasons
    if len(stages) == 1 and stages[0].get("route_kind") == "metric":
        nodes, output = _formula_nodes(stages[0], taxonomy)
        if _metric_is_whole_question(item, stages[0]):
            return nodes, output, "derived_metric", reasons
        nodes, output = _wrap_incomplete_component(nodes, output)
        reasons.append("UNSUPPORTED_COMPOSITION_TEMPLATE")
        return nodes, output, "known_metric_component_only", reasons
    if len(stages) == 1 and stages[0].get("route_kind") == "reported_concept":
        operand = stages[0]["required_operands"][0]
        leaf = _leaf(taxonomy, str(operand["concept_id"]))
        nodes = [leaf, _node("answer", "lookup", [leaf["node_id"]], output_type="reported_value")]
        plan, _ = _question_plan(item)
        old_op = str((plan.get("operation_ast") or {}).get("op") or "")
        non_context_route_reasons = [
            str(value)
            for value in route.get("reason_codes") or []
            if not str(value).startswith("MISSING_")
        ]
        if not non_context_route_reasons and str(plan.get("family") or "") == "direct_lookup" and old_op == "lookup":
            return nodes, "answer", "reported_value_lookup", reasons
        nodes, output = _wrap_incomplete_component(nodes, "answer")
        reasons.append("UNSUPPORTED_COMPOSITION_TEMPLATE")
        return nodes, output, "known_reported_component_only", reasons
    reasons.append("UNSUPPORTED_COMPOSITION_TEMPLATE" if stages else "NO_LITERAL_FINANCIAL_VARIABLE")
    leaf = _unresolved_leaf(question, normalized)
    return [leaf, _node("answer", "lookup", [leaf["node_id"]], output_type="unresolved")], "answer", "unresolved", reasons


def _semantic_template(
    item: Mapping[str, Any],
    taxonomy: FinancialTaxonomy,
    registry: FinancialMetricRegistry,
) -> tuple[list[dict[str, Any]], str, str, list[str]]:
    question = str(item.get("question") or "")
    q = normalize_label(question)
    has_inventory_liability = "hang ton kho" in q and "no ngan han" in q
    has_median = "trung vi" in q
    if has_inventory_liability and has_median and "ty trong" in q:
        nodes, output = _inventory_median_share(taxonomy)
        return nodes, output, "median_filter_then_population_share", []
    if "von luu dong rong" in q and "dong" in q and _COUNT_POPULATION_RE.search(q):
        nodes, output = _working_capital_count(taxonomy)
        return nodes, output, "conjunctive_entity_filter_then_count", []
    if "doanh thu thuan" in q and "sut giam sau nhat" in q and "dong tien" in q:
        nodes, output = _revenue_decline_then_cfo_margin(taxonomy)
        return nodes, output, "temporal_selector_then_ratio", ["DECLINE_MEASURE_AMBIGUOUS"]
    if "tang truong doanh thu thuan" in q and "cfo margin" in q and _COUNT_POPULATION_RE.search(q):
        nodes, output = _revenue_growth_negative_cfo_count(taxonomy)
        return nodes, output, "conjunctive_metric_filter_then_count", []
    return _simple_or_reported_plan(item, taxonomy, registry)


def validate_semantic_dag(plan: Mapping[str, Any], taxonomy: FinancialTaxonomy) -> list[str]:
    """Validate identifiers, references, arity, concepts and acyclicity."""
    errors: list[str] = []
    nodes = list(plan.get("nodes") or [])
    node_ids = [str(node.get("node_id") or "") for node in nodes]
    if not node_ids or any(not value for value in node_ids):
        errors.append("NODE_ID_MISSING")
    duplicates = sorted(value for value, count in Counter(node_ids).items() if value and count > 1)
    if duplicates:
        errors.append("DUPLICATE_NODE_ID")
    known = set(node_ids)
    graph: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        op = str(node.get("op") or "")
        inputs = [str(value) for value in node.get("inputs") or []]
        graph[node_id] = inputs
        if op not in _OP_ARITY:
            errors.append(f"UNKNOWN_OPERATOR:{op}")
            continue
        minimum, maximum = _OP_ARITY[op]
        if len(inputs) < minimum or (maximum is not None and len(inputs) > maximum):
            errors.append(f"INVALID_ARITY:{node_id}")
        if any(value not in known for value in inputs):
            errors.append(f"UNKNOWN_INPUT:{node_id}")
        if op == "source_lookup" and str(node.get("variable_id") or "") not in taxonomy.by_id:
            errors.append(f"UNKNOWN_VARIABLE:{node_id}")
    output_node_id = str(plan.get("output_node_id") or "")
    if output_node_id not in known:
        errors.append("OUTPUT_NODE_MISSING")

    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        if state.get(node_id) == 1:
            errors.append("DAG_CYCLE")
            return
        if state.get(node_id) == 2:
            return
        state[node_id] = 1
        for dependency in graph.get(node_id, []):
            if dependency in graph:
                visit(dependency)
        state[node_id] = 2

    for node_id in graph:
        visit(node_id)
    return sorted(set(errors))


def _difficulty(nodes: list[Mapping[str, Any]]) -> str:
    if any(node.get("op") in {"unresolved_source_lookup", "unresolved_composition"} for node in nodes):
        return "unresolved"
    derived = [node for node in nodes if node.get("op") not in {"source_lookup", "unresolved_source_lookup", "lookup"}]
    selectors = [node for node in nodes if str(node.get("op") or "").startswith("filter_") or node.get("op") in {"select_entities", "select_period", "argmin_period", "intersect_entities"}]
    if not derived:
        return "easy"
    if len(derived) <= 2 and not selectors:
        return "medium"
    if len(selectors) <= 1 and len(derived) <= 4:
        return "intermediate"
    return "hard"


def _old_plan_conflicts(item: Mapping[str, Any], nodes: list[Mapping[str, Any]], intent: str) -> list[dict[str, str]]:
    plan, _ = _question_plan(item)
    old_op = str((plan.get("operation_ast") or {}).get("op") or "")
    if not old_op or old_op in {"abstain", "plan_required"}:
        return []
    operations = {str(node.get("op") or "") for node in nodes}
    equivalent = old_op in operations or (old_op == "divide" and "elementwise_divide" in operations)
    if equivalent:
        return []
    return [{"old_operation": old_op, "semantic_intent": intent, "reason_code": "QUESTION_PLAN_OPERATION_CONFLICT"}]


def _route_leaf_tables(
    nodes: list[Mapping[str, Any]],
    context: Mapping[str, Any],
    catalog_rows: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if catalog_rows is None:
        return results
    rows = list(catalog_rows)
    for node in nodes:
        if node.get("op") != "source_lookup":
            continue
        variable_id = str(node["variable_id"])
        if not context.get("entities") or not context.get("years"):
            results.append({
                "node_id": node["node_id"],
                "variable_id": variable_id,
                "status": "context_blocked",
                "candidate_table_uids": [],
                "reason_code": "ENTITY_AND_YEAR_REQUIRED_BEFORE_TABLE_ROUTING",
            })
            continue
        route_years = list(context["years"])
        if node.get("year_source") == "@question_years_with_predecessor" and route_years:
            route_years = sorted(set([min(route_years) - 1, *route_years]))
        elif node.get("year_source") == "@latest_question_year" and route_years:
            route_years = [max(route_years)]
        stage_base = {
                "stage_id": str(node["node_id"]),
                "required_variables": [variable_id],
                "table_types": list(node.get("statement_types") or []),
                "entities": list(context["entities"]),
            }
        if context.get("scope") is None:
            available_scopes = sorted(
                {
                    str(row.get("report_scope") or "")
                    for row in rows
                    if str(row.get("report_scope") or "")
                }
            )
            candidates_by_scope: dict[str, list[str]] = {}
            traces_by_scope: dict[str, list[dict[str, Any]]] = {}
            for candidate_scope in available_scopes:
                traces = [
                    {
                        "year": year,
                        **route_stage_candidates(
                            rows,
                            {**stage_base, "year": year},
                            scope=candidate_scope,
                        ),
                    }
                    for year in route_years
                ]
                traces_by_scope[candidate_scope] = traces
                if traces and all(trace["status"] == "candidate_tables_found" for trace in traces):
                    candidates_by_scope[candidate_scope] = sorted(
                        {uid for trace in traces for uid in trace.get("candidate_table_uids") or []}
                    )
            candidate_uids = sorted({uid for values in candidates_by_scope.values() for uid in values})
            results.append({
                "node_id": node["node_id"],
                "variable_id": variable_id,
                "status": "scope_ambiguous_candidates" if candidate_uids else "context_blocked",
                "candidate_table_uids": candidate_uids,
                "candidate_table_uids_by_scope": candidates_by_scope,
                "by_scope_and_year": traces_by_scope,
                "reason_code": "SCOPE_REQUIRED_BEFORE_TABLE_SELECTION",
            })
            continue
        per_year = [
            {
                "year": year,
                **route_stage_candidates(
                    rows,
                    {**stage_base, "year": year},
                    scope=str(context["scope"]),
                ),
            }
            for year in route_years
        ]
        candidate_uids = sorted({uid for result in per_year for uid in result.get("candidate_table_uids") or []})
        results.append({
            "node_id": node["node_id"],
            "variable_id": variable_id,
            "status": "candidate_tables_found" if per_year and all(result["status"] == "candidate_tables_found" for result in per_year) else "no_candidate",
            "candidate_table_uids": candidate_uids,
            "by_year": per_year,
        })
    return results


def build_computational_semantic_plan(
    item: Mapping[str, Any],
    *,
    taxonomy: FinancialTaxonomy,
    registry: FinancialMetricRegistry,
    table_catalog_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one typed, auditable and non-executing semantic DAG."""
    question = str(item.get("question") or "")
    raw_id = item.get("id") if "id" in item else item.get("question_id")
    if raw_id is None or isinstance(raw_id, bool):
        raise ValueError("Question record is missing a usable id")
    question_id = int(raw_id)
    context = _context(item)
    nodes, output, intent, semantic_reasons = _semantic_template(item, taxonomy, registry)
    detail_dimensions = (
        _unmodelled_detail_dimensions(item, registry)
        if intent == "reported_value_lookup"
        else []
    )
    if detail_dimensions:
        semantic_reasons.append("UNMODELED_DETAIL_DIMENSION")
    conflicts = _old_plan_conflicts(item, nodes, intent)
    reason_codes = list(semantic_reasons)
    if not context["entities"]:
        reason_codes.append("MISSING_ENTITY_CONTEXT")
    if not context["years"]:
        reason_codes.append("MISSING_YEAR_CONTEXT")
    if context["scope"] is None:
        reason_codes.append("MISSING_SCOPE_CONTEXT")
    if conflicts:
        reason_codes.append("QUESTION_PLAN_OPERATION_CONFLICT")
    plan: dict[str, Any] = {
        "schema_version": 1,
        "protocol": COMPUTATIONAL_SEMANTICS_PROTOCOL,
        "question_id": question_id,
        "question": question,
        "normalized_question": normalize_label(question),
        "semantic_intent": intent,
        "question_context": context,
        "semantic_axes": _axes(question, taxonomy),
        "unmodelled_detail_dimensions": detail_dimensions,
        "nodes": nodes,
        "output_node_id": output,
        "difficulty_candidate": _difficulty(nodes),
        "question_plan_conflicts": conflicts,
        "reason_codes": [],
        "table_routes": [],
        "source_contract": source_contract(),
    }
    validation_errors = validate_semantic_dag(plan, taxonomy)
    reason_codes.extend(validation_errors)
    unresolved = any(node.get("op") == "unresolved_source_lookup" for node in nodes)
    if validation_errors:
        status = "abstain"
    elif unresolved or semantic_reasons:
        status = "partial"
    elif any(code.startswith("MISSING_") for code in reason_codes):
        status = "context_blocked"
    else:
        status = "typed_candidate"
    plan["semantic_status"] = status
    plan["reason_codes"] = sorted(set(reason_codes))
    plan["table_routes"] = _route_leaf_tables(nodes, context, table_catalog_rows)
    return plan
