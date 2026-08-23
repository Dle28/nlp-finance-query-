"""Fail-closed metric registry and question-to-concept route candidates.

This layer identifies literal metric/concept phrases and emits retrieval
requirements.  It never retrieves a table or cell, executes a formula,
repairs OCR, derives question context, or promotes provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from .financial_taxonomy import FinancialTaxonomy, normalize_label


METRIC_REGISTRY_PROTOCOL = "financial_metric_registry_v1"
QUESTION_ROUTE_PROTOCOL = "question_concept_route_candidate_v1"
DIRECT_LOOKUP_OPERATION_RE = re.compile(
    r"\b(?:chenh\s+lech|hieu\s+so|tang\s+truong|tang\s+bao\s+nhieu|"
    r"giam\s+bao\s+nhieu|ty\s+le\s+giua|ty\s+trong|trung\s+binh|"
    r"binh\s+quan|cao\s+nhat|thap\s+nhat|lon\s+nhat|nho\s+nhat|"
    r"so\s+voi|trong\s+so|tai\s+nam\s+co)\b"
)


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """One validated, declarative metric definition."""

    metric_id: str
    labels_vi: tuple[str, ...]
    output_unit: str
    definition_status: str
    formula_ast: Mapping[str, Any]
    operands: tuple[Mapping[str, Any], ...]
    sectors: tuple[str, ...]


def _literal_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    """Find a normalized literal phrase without fuzzy expansion."""
    pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")
    return [(match.start(), match.end()) for match in pattern.finditer(text)]


def _source_contract() -> dict[str, bool]:
    """Return the fixed non-promotable contract for every route artifact."""
    return {
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_execute_formula": False,
        "may_compute_answer": False,
        "may_select_table": False,
        "may_select_value_cell": False,
        "may_repair_ocr": False,
    }


def _literal_strings(values: object, *, field: str, metric_id: str) -> tuple[str, ...]:
    """Validate literal aliases/constraints without expanding their meaning."""
    if not isinstance(values, list):
        raise ValueError(f"Metric {metric_id} {field} must be a list")
    strings = tuple(str(value) for value in values)
    if any(not value.strip() for value in strings):
        raise ValueError(f"Metric {metric_id} {field} cannot contain blank values")
    return strings


def _question_context(item: Mapping[str, Any]) -> dict[str, Any]:
    """Read only a supplied plan context; never parse it from question text."""
    if isinstance(item.get("effective_question_plan"), Mapping):
        source_plan = dict(item["effective_question_plan"])
        source_name = "effective_question_plan"
    elif isinstance(item.get("question_plan"), Mapping):
        source_plan = dict(item["question_plan"])
        source_name = "question_plan"
    else:
        source_plan = {}
        source_name = None

    raw_entities = source_plan.get("tickers")
    entities = (
        [str(value).strip() for value in raw_entities if str(value).strip()]
        if isinstance(raw_entities, list)
        else []
    )
    raw_years = source_plan.get("years")
    years: list[int] = []
    if isinstance(raw_years, list):
        for value in raw_years:
            try:
                years.append(int(value))
            except (TypeError, ValueError):
                # An invalid supplied value is not a licence to recover a
                # replacement year from the prose question.
                continue
    raw_scope = source_plan.get("scope")
    scope = str(raw_scope).strip() if raw_scope is not None else ""
    return {
        "entities": entities,
        "years": years,
        "scope": scope or None,
        "source": source_name,
    }


def _missing_context_reason_codes(context: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    if not context.get("entities"):
        missing.append("MISSING_ENTITY_CONTEXT")
    if not context.get("years"):
        missing.append("MISSING_YEAR_CONTEXT")
    if context.get("scope") is None:
        missing.append("MISSING_SCOPE_CONTEXT")
    return missing


def _feedback_for_abstain(reason_codes: list[str]) -> dict[str, Any]:
    """Explain only the missing route contract and a conservative next step."""
    if "NO_LITERAL_METRIC_OR_CONCEPT_MATCH" in reason_codes:
        return {
            "status": "abstain",
            "missing_contract": ["literal_metric_or_concept_candidate"],
            "suggested_next_action": (
                "route the exact normalized question phrase to unmatched-phrase audit; "
                "add an alias only after taxonomy/registry review"
            ),
            "invent_synonyms_or_evidence_allowed": False,
        }
    if "DIRECT_CONCEPT_OPERATION_UNSUPPORTED" in reason_codes:
        missing = ["controlled_operation_contract"]
        missing.extend(
            code.removeprefix("MISSING_").removesuffix("_CONTEXT").lower()
            for code in reason_codes
            if code.startswith("MISSING_") and code.endswith("_CONTEXT")
        )
        return {
            "status": "abstain",
            "missing_contract": missing,
            "suggested_next_action": (
                "send the exact reported-concept route to a controlled typed-operation "
                "planner; do not create an operation, value, or evidence from prose"
            ),
            "invent_synonyms_or_evidence_allowed": False,
        }
    missing = [
        code.removeprefix("MISSING_").removesuffix("_CONTEXT").lower()
        for code in reason_codes
        if code.startswith("MISSING_") and code.endswith("_CONTEXT")
    ]
    return {
        "status": "abstain",
        "missing_contract": missing,
        "suggested_next_action": (
            "supply the missing entity, year, and/or report scope in the source-bound "
            "question plan; do not infer it from question prose or candidate evidence"
        ),
        "invent_synonyms_or_evidence_allowed": False,
    }


class FinancialMetricRegistry:
    """Validated literal metric registry bound to a FinancialTaxonomy."""

    def __init__(
        self,
        payload: Mapping[str, Any],
        *,
        taxonomy: FinancialTaxonomy,
        source_path: Path | None = None,
    ):
        if int(payload.get("schema_version") or 0) != 1:
            raise ValueError("Unsupported metric registry schema version")
        if str(payload.get("protocol") or "") != METRIC_REGISTRY_PROTOCOL:
            raise ValueError("Unsupported metric registry protocol")
        self.source_path = source_path
        self.taxonomy = taxonomy
        metrics: list[MetricDefinition] = []
        seen: set[str] = set()
        raw_metrics = payload.get("metrics")
        if not isinstance(raw_metrics, list):
            raise ValueError("Metric registry metrics must be a list")
        for raw in raw_metrics:
            if not isinstance(raw, Mapping):
                raise ValueError("Metric registry entries must be mappings")
            metric_id = str(raw.get("metric_id") or "").strip()
            if not metric_id or metric_id in seen:
                raise ValueError(f"Invalid or duplicate metric_id: {metric_id!r}")

            raw_labels = raw.get("labels_vi")
            if not isinstance(raw_labels, list):
                raise ValueError(f"Metric {metric_id} labels_vi must be a list")
            labels = tuple(dict.fromkeys(normalize_label(value) for value in raw_labels))
            if not labels or any(not value for value in labels):
                raise ValueError(f"Metric {metric_id} requires literal labels")

            raw_operands = raw.get("operands")
            if not isinstance(raw_operands, list):
                raise ValueError(f"Metric {metric_id} operands must be a list")
            operands = tuple(
                dict(value) if isinstance(value, Mapping) else {} for value in raw_operands
            )
            if not operands:
                raise ValueError(f"Metric {metric_id} requires operands")
            roles: set[str] = set()
            for operand in operands:
                role = str(operand.get("role") or "").strip()
                concept_id = str(operand.get("concept_id") or "").strip()
                if not role or role in roles:
                    raise ValueError(f"Metric {metric_id} has invalid/duplicate operand role")
                if concept_id not in taxonomy.by_id:
                    raise ValueError(
                        f"Metric {metric_id} references unknown concept {concept_id!r}"
                    )
                _literal_strings(
                    operand.get("statement_types"),
                    field=f"operand {role!r} statement_types",
                    metric_id=metric_id,
                )
                roles.add(role)

            raw_formula_ast = raw.get("formula_ast")
            if not isinstance(raw_formula_ast, Mapping):
                raise ValueError(f"Metric {metric_id} formula_ast must be metadata mapping")
            raw_sectors = raw.get("sectors", [])
            sectors = _literal_strings(raw_sectors, field="sectors", metric_id=metric_id)
            metrics.append(
                MetricDefinition(
                    metric_id=metric_id,
                    labels_vi=labels,
                    output_unit=str(raw.get("output_unit") or "source_unit"),
                    definition_status=str(raw.get("definition_status") or "review_required"),
                    # The AST is copied as declarative metadata.  This module
                    # has no executor and intentionally never evaluates it.
                    formula_ast=dict(raw_formula_ast),
                    operands=operands,
                    sectors=sectors,
                )
            )
            seen.add(metric_id)
        self.metrics = tuple(metrics)
        self.by_id = {metric.metric_id: metric for metric in metrics}

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        taxonomy: FinancialTaxonomy,
    ) -> "FinancialMetricRegistry":
        path = path.resolve()
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Metric registry root must be a mapping")
        return cls(payload, taxonomy=taxonomy, source_path=path)

    def _metric_matches(self, question: str) -> list[dict[str, Any]]:
        """Return every literal metric occurrence in question order."""
        normalized = normalize_label(question)
        matches: list[dict[str, Any]] = []
        for metric in self.metrics:
            matches.extend(
                {
                    "metric_id": metric.metric_id,
                    "matched_label": label,
                    "span": [start, end],
                }
                for label in metric.labels_vi
                for start, end in _literal_spans(normalized, label)
            )
        return sorted(
            matches,
            key=lambda value: (
                value["span"][0],
                value["span"][1],
                value["metric_id"],
                value["matched_label"],
            ),
        )

    def _concept_matches(self, question: str) -> list[dict[str, Any]]:
        """Use longest literal, non-overlapping taxonomy aliases only."""
        normalized = normalize_label(question)
        candidates: list[dict[str, Any]] = []
        for concept in self.taxonomy.concepts:
            for label in concept.labels_vi:
                if len(label) < 4:
                    continue
                for start, end in _literal_spans(normalized, label):
                    candidates.append(
                        {
                            "concept_id": concept.concept_id,
                            "matched_label": label,
                            "span": [start, end],
                            "concept_path": self.taxonomy.concept_path(concept.concept_id),
                        }
                    )
        selected: list[dict[str, Any]] = []
        occupied: list[tuple[int, int]] = []
        for candidate in sorted(
            candidates,
            key=lambda value: (
                -(value["span"][1] - value["span"][0]),
                value["span"][0],
                value["concept_id"],
                value["matched_label"],
            ),
        ):
            start, end = candidate["span"]
            if any(start < right and end > left for left, right in occupied):
                continue
            selected.append(candidate)
            occupied.append((start, end))
        return sorted(selected, key=lambda value: (value["span"][0], value["concept_id"]))

    def _metric_stage(
        self,
        *,
        index: int,
        match: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        metric = self.by_id[str(match["metric_id"])]
        operands: list[dict[str, Any]] = []
        for operand in metric.operands:
            concept_id = str(operand["concept_id"])
            concept = self.taxonomy.by_id[concept_id]
            # Preserve all registry constraints verbatim; taxonomy adds only
            # route metadata and never selects source evidence.
            operands.append(
                {
                    **dict(operand),
                    "concept_path": self.taxonomy.concept_path(concept_id),
                    "period_type": concept.period_type,
                }
            )
        return {
            "stage_id": f"stage_{index}_{metric.metric_id}",
            "route_kind": "metric",
            "metric_id": metric.metric_id,
            "matched_label": str(match["matched_label"]),
            "match_span": list(match["span"]),
            "definition_status": metric.definition_status,
            "formula_ast": dict(metric.formula_ast),
            "formula_ast_metadata_only": True,
            "output_unit": metric.output_unit,
            "required_operands": operands,
            "retrieval_filters": {
                "entities": list(context["entities"]),
                "years": list(context["years"]),
                "scope": context["scope"],
                "sectors": list(metric.sectors),
                "table_types": sorted(
                    {
                        str(table_type)
                        for operand in metric.operands
                        for table_type in operand.get("statement_types") or []
                    }
                ),
            },
            "source_contract": _source_contract(),
        }

    def _concept_stage(
        self,
        *,
        index: int,
        match: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        concept = self.taxonomy.by_id[str(match["concept_id"])]
        return {
            "stage_id": f"stage_{index}_{concept.concept_id}",
            "route_kind": "reported_concept",
            "concept_id": concept.concept_id,
            "matched_label": str(match["matched_label"]),
            "match_span": list(match["span"]),
            "formula_ast": {"op": "lookup", "args": [concept.concept_id]},
            "formula_ast_metadata_only": True,
            "required_operands": [
                {
                    "role": concept.concept_id,
                    "concept_id": concept.concept_id,
                    "concept_path": list(match["concept_path"]),
                    "period_type": concept.period_type,
                    "statement_types": list(concept.statement_types),
                }
            ],
            "retrieval_filters": {
                "entities": list(context["entities"]),
                "years": list(context["years"]),
                "scope": context["scope"],
                "sectors": [value for value in concept.sectors if value != "all"],
                "table_types": list(concept.statement_types),
            },
            "source_contract": _source_contract(),
        }

    def build_question_route(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Build a non-executing route or structured abstention for one question."""
        question = str(item.get("question") or "")
        context = _question_context(item)
        metric_matches = self._metric_matches(question)
        concept_matches = [] if metric_matches else self._concept_matches(question)
        stages = [
            self._metric_stage(index=index, match=match, context=context)
            for index, match in enumerate(metric_matches, start=1)
        ]
        if not stages:
            stages = [
                self._concept_stage(index=index, match=match, context=context)
                for index, match in enumerate(concept_matches, start=1)
            ]

        reason_codes: list[str] = []
        if not stages:
            status = "abstain"
            reason_codes.append("NO_LITERAL_METRIC_OR_CONCEPT_MATCH")
        elif len(stages) > 1:
            status = "staged_candidate"
            reason_codes.append("COMPOSED_EXECUTION_REQUIRED")
        elif stages[0]["route_kind"] == "metric":
            status = "metric_candidate"
        else:
            status = "concept_lookup_candidate"

        if (
            len(stages) == 1
            and stages[0]["route_kind"] == "reported_concept"
            and DIRECT_LOOKUP_OPERATION_RE.search(normalize_label(question))
        ):
            # A matched row label is not a program for a comparison, change,
            # ranking, or ratio request.  Retain the literal candidate only
            # for audit, then require a separately controlled operation plan.
            status = "abstain"
            reason_codes.append("DIRECT_CONCEPT_OPERATION_UNSUPPORTED")
        if stages:
            reason_codes.extend(_missing_context_reason_codes(context))
            if any(code.startswith("MISSING_") for code in reason_codes):
                # Preserve candidate stages for audit, but do not claim a
                # retrievable route without all supplied context.
                status = "abstain"
        reason_codes = sorted(set(reason_codes))
        return {
            "schema_version": 1,
            "protocol": QUESTION_ROUTE_PROTOCOL,
            "question_id": int(item.get("id") or item.get("question_id") or 0),
            "question": question,
            "normalized_question": normalize_label(question),
            "route_status": status,
            "reason_codes": reason_codes,
            "question_context": context,
            "stages": stages,
            "feedback": _feedback_for_abstain(reason_codes) if status == "abstain" else None,
            "source_contract": _source_contract(),
        }
