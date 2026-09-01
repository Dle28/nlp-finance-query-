"""Independent producer for source-bound composed and temporal candidates.

This module is a producer boundary, not a retriever, selector, or authority
layer.  It accepts a typed question plan and a *complete* set of exact cell
bindings that a previous source-binding stage has already materialised.  It
then performs the checks that make a composed candidate useful to a later
answer-level component:

* the question family and operation AST are explicit and supported by the
  existing Decimal executor;
* AST references, typed operands, and bound operands are exactly the same set;
* every binding is tied to the current V2 table/context coordinates and exact
  source/table SHA-256 values;
* period, entity, scope, and source-unit contracts are checked without
  inferring values from question text or route names; and
* the whole AST is replayed through ``execute_typed_plan_shadow``.

The result is candidate-only.  A replay-ready result is not a semantic
certificate, a verified answer, a training label, or a release/submission
authorization.  No Question-ID allowlist and no gold answer are consulted.

Canonical binding input
-----------------------

``bound_operands`` is either a sequence of records or a mapping from operand
ID to records.  Each record must have the following fields::

    {
        "operand_id": "x_old",
        "document_id": "AAA_2023_consolidated",
        "internal_table_uid": "table-2023",
        "ticker": "AAA",
        "report_year": 2023,
        "scope": "consolidated",
        "source_unit": "million_vnd",
        "source_sha256": "<64 lowercase hex characters>",
        "table_sha256": "<64 lowercase hex characters>",
        "binding": {
            "status": "cell_bound",
            "row_index": 1,
            "column_index": 2,
            "raw_value": "100",
            "parsed_value": "100",
            "parse_warnings": [],
            "column_label": "2023",
            "source_cell": {"row": 1, "column": 2}
        }
    }

``source_tables`` and ``source_contexts`` use the existing V2/V3 shapes
consumed by ``execute_grounded_ast_shadow``.  The producer deliberately does
not accept a bare numeric value as an operand: without the exact source
coordinate and hash lineage there is no candidate plan to produce.

``source_hash`` in the returned ``CandidatePlan`` is a deterministic closure
hash over all operand source/table hashes and coordinates.  Callers may pass
the same value through ``expected_source_hash`` to detect stale or mixed
binding bundles.  It is not a replacement for the individual source hashes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any

from ..decimal_executor import (
    TYPED_OPERAND_PLAN_PROTOCOL,
    execute_typed_plan_shadow,
    parse_decimal,
    validate_operation_ast,
)
from .candidate_plan_contract import (
    CandidateOperand,
    CandidatePlan,
    CandidatePlanValidationError,
)
from .currency_units import is_fixed_vnd_scale


COMPOSED_CANDIDATE_PLAN_PROTOCOL = "vifinqa_composed_candidate_plan_producer_v1"
COMPOSED_CANDIDATE_PLAN_SCHEMA_VERSION = 1
PRODUCTION_REPLAY_READY = "REPLAY_READY"
PRODUCTION_BLOCKED = "BLOCKED"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_RELIABLE_PARSE_WARNINGS = frozenset({"percent_value_not_scaled"})

# These are explicit family contracts, not fuzzy text rules.  A caller may
# use one of the documented aliases below, but an unknown family is blocked.
_FAMILY_ALIASES = {
    "temporal": "temporal_change",
    "temporal_comparison": "temporal_change",
    "composed": "composed_arithmetic",
}
_FAMILY_ROOT_OPERATORS: dict[str, frozenset[str]] = {
    "temporal_change": frozenset(
        {"subtract", "absolute_difference", "percentage_change", "arg_extreme_period"}
    ),
    "composed_arithmetic": frozenset(
        {
            "add",
            "sum",
            "subtract",
            "absolute_difference",
            "absolute",
            "scalar_multiply",
            "divide",
            "ratio_to_percent",
            "percentage_change",
            "mean",
            "median",
            "min",
            "max",
            "count",
            "arg_extreme_period",
        }
    ),
    "cross_entity_comparison": frozenset({"subtract", "absolute_difference"}),
    "multi_entity_or_period_aggregation": frozenset(
        {"add", "sum", "mean", "median", "min", "max", "count", "arg_extreme_period"}
    ),
    "ratio_or_derived": frozenset(
        {"divide", "ratio_to_percent", "percentage_change", "scalar_multiply"}
    ),
}
_COMPOSED_FAMILIES = frozenset(_FAMILY_ROOT_OPERATORS)
_AUTHORITY_FIELDS = {
    "candidate_only": True,
    "answer_authority": False,
    "evidence_authorized": False,
    "strict_answer_authorized": False,
    "release_authorized": False,
    "submission_eligible": False,
    "training_eligible": False,
    "promotion_allowed": False,
}


class ComposedCandidatePlanError(ValueError):
    """Raised only by strict helpers; public production is fail-closed."""


@dataclass(frozen=True, slots=True)
class ComposedCandidatePlanResult:
    """One candidate-only production receipt.

    ``candidate_plan`` is present only when the complete binding bundle and
    whole-AST replay passed.  The class intentionally carries no authority
    flag that can become true.
    """

    status: str
    question_id: int | str | None
    candidate_plan: CandidatePlan | None
    reason_codes: tuple[str, ...]
    replay_receipt: Mapping[str, Any]
    source_hash: str | None

    @property
    def plan(self) -> CandidatePlan | None:
        """Short alias for callers that use ``result.plan``."""

        return self.candidate_plan

    @property
    def candidate_only(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol": COMPOSED_CANDIDATE_PLAN_PROTOCOL,
            "schema_version": COMPOSED_CANDIDATE_PLAN_SCHEMA_VERSION,
            "status": self.status,
            "question_id": self.question_id,
            "candidate_plan": (
                self.candidate_plan.to_dict() if self.candidate_plan is not None else None
            ),
            "reason_codes": list(self.reason_codes),
            "replay_receipt": _json_safe(self.replay_receipt),
            "source_hash": self.source_hash,
        }
        payload.update(_AUTHORITY_FIELDS)
        return payload


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        text = format(value.normalize(), "f")
        return "0" if text in {"-0", "-0.0"} else text
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(nested) for nested in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _canonical_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ComposedCandidatePlanError("source closure is not JSON canonical") from error
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _normalise_family(value: object) -> str:
    raw = "_".join(_text(value).casefold().split())
    return _FAMILY_ALIASES.get(raw, raw)


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _question_payload(question_plan: object) -> dict[str, Any] | None:
    if isinstance(question_plan, Mapping):
        return deepcopy(dict(question_plan))
    to_dict = getattr(question_plan, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return deepcopy(dict(value))
    return None


def _question_id(payload: Mapping[str, Any]) -> int | str | None:
    value = payload.get("question_id", payload.get("id"))
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value.strip() if isinstance(value, str) else value


def _collect_ast_references(node: object, output: set[str]) -> None:
    """Collect only executor-style operand references.

    The current executor treats all string leaves in ``args`` as input names,
    except the explicit dimensionless scalar literal.  Mirroring that shape
    here prevents a producer from accepting a second AST vocabulary that the
    executor would not actually replay.
    """

    if isinstance(node, Mapping):
        if node.get("kind") == "dimensionless_scalar":
            return
        if "op" not in node:
            return
        args = node.get("args")
        if isinstance(args, list):
            for argument in args:
                _collect_ast_references(argument, output)
        return
    if isinstance(node, list):
        for item in node:
            _collect_ast_references(item, output)
        return
    if isinstance(node, str) and node.strip():
        output.add(node.strip())


def _strict_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1900 <= value <= 2100 else None
    text = _text(value)
    if not _YEAR_RE.fullmatch(text):
        return None
    return int(text)


def _spec_period(spec: Mapping[str, Any]) -> int | None:
    if "period" in spec and spec.get("period") is not None:
        return _strict_year(spec.get("period"))
    years = spec.get("years")
    if isinstance(years, list) and len(years) == 1:
        return _strict_year(years[0])
    return None


def _spec_entity(spec: Mapping[str, Any]) -> str:
    entity = _text(spec.get("entity"))
    ticker = _text(spec.get("ticker"))
    if entity and ticker and entity.casefold() != ticker.casefold():
        return ""
    return entity or ticker


def _source_provenance_hash(record: Mapping[str, Any], field: str) -> str:
    direct = _text(record.get(field))
    nested = record.get("source_provenance")
    nested_value = _text(nested.get(field)) if isinstance(nested, Mapping) else ""
    if direct and nested_value and direct != nested_value:
        return ""
    return direct or nested_value


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _record_sequence(value: object) -> list[Mapping[str, Any]] | None:
    if isinstance(value, Mapping):
        records: list[Mapping[str, Any]] = []
        for operand_id, raw in value.items():
            if not isinstance(raw, Mapping):
                return None
            if _text(raw.get("operand_id")) != _text(operand_id):
                return None
            records.append(raw)
        return records
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        records = list(value)
        if not all(isinstance(record, Mapping) for record in records):
            return None
        return records  # type: ignore[return-value]
    return None


def _unit_contract(
    spec: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    raw = spec.get("unit_contract")
    top_level_requested = payload.get("requested_unit")
    top_level_text = _text(top_level_requested) if top_level_requested is not None else ""

    if raw is not None and not isinstance(raw, Mapping):
        return None, "UNIT_CONTRACT_INVALID"
    if isinstance(raw, Mapping):
        if "requested_unit" not in raw:
            return None, "UNIT_CONTRACT_REQUESTED_UNIT_MISSING"
        requested_value = raw.get("requested_unit")
        requested = _text(requested_value) if requested_value is not None else ""
        if top_level_text and requested and requested.casefold() != top_level_text.casefold():
            return None, "UNIT_CONTRACT_QUESTION_UNIT_MISMATCH"
        conversion_allowed = raw.get("conversion_allowed")
        conversion_policy = _text(raw.get("conversion_policy"))
        if not isinstance(conversion_allowed, bool):
            return None, "UNIT_CONTRACT_CONVERSION_PERMISSION_MISSING"
        if requested and is_fixed_vnd_scale(requested):
            if conversion_allowed is not True or conversion_policy != "exact_fixed_vnd_scale_only":
                return None, "UNIT_CONTRACT_FIXED_VND_POLICY_INVALID"
        elif conversion_allowed is not False or conversion_policy != "not_applicable":
            return None, "UNIT_CONTRACT_NON_CURRENCY_POLICY_INVALID"
        return {
            "requested_unit": requested or None,
            "conversion_allowed": conversion_allowed,
            "conversion_policy": conversion_policy,
        }, None

    # A plain QuestionPlan has no unit_contract field.  Its explicit
    # question-level requested_unit is still a typed contract; permission is
    # derived only from the executor's canonical fixed-scale registry.
    if top_level_text:
        fixed = is_fixed_vnd_scale(top_level_text)
        return {
            "requested_unit": top_level_text,
            "conversion_allowed": fixed,
            "conversion_policy": "exact_fixed_vnd_scale_only" if fixed else "not_applicable",
        }, None
    return {
        "requested_unit": None,
        "conversion_allowed": False,
        "conversion_policy": "not_applicable",
    }, None


def _normalise_typed_operands(
    payload: Mapping[str, Any],
    operand_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_operands = payload.get("operands")
    if not isinstance(raw_operands, list) or not raw_operands:
        return [], ["QUESTION_PLAN_OPERANDS_MISSING"]

    errors: list[str] = []
    typed: list[dict[str, Any]] = []
    seen: set[str] = set()
    question_scope = _text(payload.get("scope"))

    for raw in raw_operands:
        if not isinstance(raw, Mapping):
            errors.append("QUESTION_PLAN_OPERAND_INVALID")
            continue
        operand_id = _text(raw.get("operand_id"))
        if not operand_id:
            errors.append("QUESTION_PLAN_OPERAND_ID_MISSING")
            continue
        if operand_id in seen:
            errors.append(f"DUPLICATE_OPERAND_ID:{operand_id}")
            continue
        seen.add(operand_id)
        period = _spec_period(raw)
        entity = _spec_entity(raw)
        scope = _text(raw.get("scope")) or question_scope
        if period is None:
            errors.append(f"OPERAND_PERIOD_MISSING:{operand_id}")
        if not entity:
            errors.append(f"OPERAND_ENTITY_MISSING:{operand_id}")
        if not scope:
            errors.append(f"OPERAND_SCOPE_MISSING:{operand_id}")
        unit_contract, unit_error = _unit_contract(raw, payload)
        if unit_error:
            errors.append(f"{unit_error}:{operand_id}")
        if period is None or not entity or not scope or unit_contract is None:
            continue
        typed.append(
            {
                "operand_id": operand_id,
                "role": _text(raw.get("role")) or operand_id,
                "metric_hints": [
                    _text(value)
                    for value in raw.get("metric_hints") or []
                    if _text(value)
                ],
                "entity": entity,
                "ticker": entity,
                "years": [period],
                "scope": scope,
                "unit_contract": unit_contract,
                "required": True,
            }
        )

    if seen != operand_ids:
        errors.append("QUESTION_PLAN_OPERAND_SET_MISMATCH")
    return typed, list(dict.fromkeys(errors))


def _validate_family_contract(
    family: str,
    ast: Mapping[str, Any],
    typed_operands: Sequence[Mapping[str, Any]],
    references: set[str],
) -> list[str]:
    errors: list[str] = []
    if family not in _COMPOSED_FAMILIES:
        return [f"FAMILY_UNSUPPORTED:{family or 'missing'}"]
    root = _text(ast.get("op"))
    allowed = _FAMILY_ROOT_OPERATORS[family]
    if root not in allowed:
        errors.append(f"ROOT_OPERATOR_NOT_ALLOWED:{family}:{root or 'missing'}")
    if len(references) < 2:
        errors.append("COMPOSED_REQUIRES_AT_LEAST_TWO_OPERANDS")

    periods = [int(operand["years"][0]) for operand in typed_operands]
    if family == "temporal_change":
        if len(set(periods)) < 2:
            errors.append("TEMPORAL_PERIOD_SET_INCOMPLETE")
        if root in {"subtract", "absolute_difference", "percentage_change"} and len(references) != 2:
            errors.append("TEMPORAL_BINARY_OPERAND_COUNT_INVALID")
    if root == "arg_extreme_period":
        if len(periods) < 2 or len(set(periods)) != len(periods):
            errors.append("PERIOD_SELECTOR_PERIOD_SET_INVALID")
        if ast.get("direction") not in {"min", "max"}:
            errors.append("PERIOD_SELECTOR_DIRECTION_INVALID")

    requested_units = {
        _text((operand.get("unit_contract") or {}).get("requested_unit"))
        for operand in typed_operands
        if _text((operand.get("unit_contract") or {}).get("requested_unit"))
    }
    if len(requested_units) > 1:
        errors.append("QUESTION_OUTPUT_UNIT_INCONSISTENT")
    return list(dict.fromkeys(errors))


def _validate_bound_record(
    record: Mapping[str, Any],
    *,
    operand_id: str,
    source_tables: Mapping[str, Mapping[str, Any]],
    source_contexts: Mapping[str, Mapping[str, Any]],
    typed_operand: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Validate one canonical binding and return executor/source rows."""

    errors: list[str] = []
    if _text(record.get("operand_id")) != operand_id:
        errors.append(f"OPERAND_ID_MISMATCH:{operand_id}")

    required_text_fields = ("document_id", "internal_table_uid", "ticker", "scope", "source_unit")
    for field in required_text_fields:
        if not _text(record.get(field)):
            errors.append(f"BINDING_FIELD_MISSING:{operand_id}:{field}")
    report_year = _strict_year(record.get("report_year"))
    if report_year is None:
        errors.append(f"BINDING_PERIOD_INVALID:{operand_id}")

    source_sha = _source_provenance_hash(record, "source_sha256")
    table_sha = _source_provenance_hash(record, "table_sha256")
    if not _valid_sha(source_sha) or not _valid_sha(table_sha):
        errors.append(f"SOURCE_HASH_INVALID:{operand_id}")

    nested = record.get("binding")
    binding = dict(nested) if isinstance(nested, Mapping) else None
    if binding is None:
        errors.append(f"EXACT_BINDING_MISSING:{operand_id}")
        return None, None, list(dict.fromkeys(errors))
    if binding.get("status") != "cell_bound":
        errors.append(f"BINDING_STATUS_INVALID:{operand_id}")

    row_index = binding.get("row_index")
    column_index = binding.get("column_index")
    if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0:
        errors.append(f"BINDING_ROW_INVALID:{operand_id}")
    if isinstance(column_index, bool) or not isinstance(column_index, int) or column_index < 0:
        errors.append(f"BINDING_COLUMN_INVALID:{operand_id}")
    if not _text(binding.get("column_label")):
        errors.append(f"BINDING_COLUMN_LABEL_MISSING:{operand_id}")
    if "source_cell" not in binding:
        errors.append(f"BINDING_SOURCE_CELL_MISSING:{operand_id}")

    expected_period = _spec_period(typed_operand)
    if expected_period is None or report_year != expected_period:
        errors.append(f"PERIOD_MISMATCH:{operand_id}")
    if _text(record.get("ticker")).casefold() != _text(typed_operand.get("entity")).casefold():
        errors.append(f"ENTITY_MISMATCH:{operand_id}")
    if _text(record.get("scope")).casefold() != _text(typed_operand.get("scope")).casefold():
        errors.append(f"SCOPE_MISMATCH:{operand_id}")

    uid = _text(record.get("internal_table_uid"))
    table = source_tables.get(uid)
    context = source_contexts.get(uid)
    if table is None:
        errors.append(f"SOURCE_TABLE_MISSING:{operand_id}")
    if context is None:
        errors.append(f"SOURCE_CONTEXT_MISSING:{operand_id}")
    if table is None or context is None:
        return None, None, list(dict.fromkeys(errors))

    table_source_sha = _source_provenance_hash(table, "source_sha256")
    table_table_sha = _source_provenance_hash(table, "table_sha256")
    if source_sha != table_source_sha or table_sha != table_table_sha:
        errors.append(f"SOURCE_HASH_MISMATCH:{operand_id}")
    for source_record, label in ((context, "context"),):
        context_source_sha = _source_provenance_hash(source_record, "source_sha256")
        context_table_sha = _source_provenance_hash(source_record, "table_sha256")
        if context_source_sha and context_source_sha != source_sha:
            errors.append(f"SOURCE_CONTEXT_HASH_MISMATCH:{operand_id}:{label}:source")
        if context_table_sha and context_table_sha != table_sha:
            errors.append(f"SOURCE_CONTEXT_HASH_MISMATCH:{operand_id}:{label}:table")

    table_uid = _text(table.get("internal_table_uid"))
    if table_uid != uid:
        errors.append(f"SOURCE_TABLE_UID_MISMATCH:{operand_id}")
    if _text(table.get("document_id")) != _text(record.get("document_id")):
        errors.append(f"SOURCE_DOCUMENT_MISMATCH:{operand_id}")
    if _text(table.get("ticker")).casefold() != _text(record.get("ticker")).casefold():
        errors.append(f"SOURCE_ENTITY_MISMATCH:{operand_id}")
    if _text(table.get("scope")).casefold() != _text(record.get("scope")).casefold():
        errors.append(f"SOURCE_SCOPE_MISMATCH:{operand_id}")
    if _strict_year(table.get("report_year")) != report_year:
        errors.append(f"SOURCE_PERIOD_MISMATCH:{operand_id}")
    table_unit = _text(table.get("unit_hint"))
    if table_unit and table_unit.casefold() != _text(record.get("source_unit")).casefold():
        errors.append(f"SOURCE_UNIT_MISMATCH:{operand_id}")

    rows = table.get("rows")
    provenance = table.get("cell_provenance")
    if not isinstance(rows, list) or not isinstance(provenance, list):
        errors.append(f"SOURCE_GRID_MISSING:{operand_id}")
    elif (
        isinstance(row_index, int)
        and isinstance(column_index, int)
        and 0 <= row_index < len(rows)
        and isinstance(rows[row_index], list)
        and 0 <= column_index < len(rows[row_index])
    ):
        raw_value = str(rows[row_index][column_index])
        if raw_value != _text(binding.get("raw_value")):
            errors.append(f"RAW_VALUE_MISMATCH:{operand_id}")
        if (
            row_index >= len(provenance)
            or not isinstance(provenance[row_index], list)
            or column_index >= len(provenance[row_index])
            or provenance[row_index][column_index] != binding.get("source_cell")
        ):
            errors.append(f"SOURCE_CELL_PROVENANCE_MISMATCH:{operand_id}")
        parsed = parse_decimal(raw_value)
        recorded_warnings = binding.get("parse_warnings")
        if not isinstance(recorded_warnings, list):
            errors.append(f"PARSE_WARNINGS_MISSING:{operand_id}")
        else:
            if set(map(str, recorded_warnings)) != set(parsed.warnings):
                errors.append(f"PARSE_WARNINGS_NOT_REPRODUCIBLE:{operand_id}")
            if set(parsed.warnings) - _RELIABLE_PARSE_WARNINGS:
                errors.append(f"UNRELIABLE_NUMERIC_PARSE:{operand_id}")
        if parsed.value is None or _text(binding.get("parsed_value")) != parsed.value:
            errors.append(f"PARSED_VALUE_NOT_REPRODUCIBLE:{operand_id}")
    else:
        errors.append(f"SOURCE_COORDINATE_INVALID:{operand_id}")

    executor_record = {
        "internal_table_uid": uid,
        "ticker": _text(record.get("ticker")),
        "report_year": report_year,
        "scope": _text(record.get("scope")),
        "source_unit": _text(record.get("source_unit")),
        "document_id": _text(record.get("document_id")),
        "binding": binding,
    }
    return executor_record, dict(table), list(dict.fromkeys(errors))


def _typed_plan_for_executor(
    payload: Mapping[str, Any],
    *,
    family: str,
    operation_ast: Mapping[str, Any],
    typed_operands: Sequence[Mapping[str, Any]],
    route_family: str,
) -> dict[str, Any]:
    operands = [deepcopy(dict(operand)) for operand in typed_operands]
    plan = {
        "schema_version": 1,
        "protocol": TYPED_OPERAND_PLAN_PROTOCOL,
        "question_id": _question_id(payload),
        "question": _text(payload.get("question")),
        "effective_family": family,
        "decomposition_status": "complete",
        "route": route_family,
        "operands": operands,
        "operation_ast": deepcopy(dict(operation_ast)),
    }
    fingerprint_payload = {
        "effective_family": plan["effective_family"],
        "status": plan["decomposition_status"],
        "route": plan["route"],
        "operands": plan["operands"],
        "operation_ast": plan["operation_ast"],
    }
    plan["plan_fingerprint"] = _canonical_sha256(fingerprint_payload)
    return plan


def source_closure_hash(bound_operands: object) -> str | None:
    """Compute the producer's deterministic source-closure hash.

    This helper is intentionally structural.  It does not read or trust a
    numeric answer.  It returns ``None`` for malformed/non-canonical records;
    the main producer still performs the stronger table/context checks.
    """

    records = _record_sequence(bound_operands)
    if records is None:
        return None
    entries: list[dict[str, Any]] = []
    for record in records:
        operand_id = _text(record.get("operand_id"))
        binding = record.get("binding")
        if not operand_id or not isinstance(binding, Mapping):
            return None
        source_sha = _source_provenance_hash(record, "source_sha256")
        table_sha = _source_provenance_hash(record, "table_sha256")
        row_index = binding.get("row_index")
        column_index = binding.get("column_index")
        if not _valid_sha(source_sha) or not _valid_sha(table_sha):
            return None
        if isinstance(row_index, bool) or not isinstance(row_index, int):
            return None
        if isinstance(column_index, bool) or not isinstance(column_index, int):
            return None
        entries.append(
            {
                "operand_id": operand_id,
                "document_id": _text(record.get("document_id")),
                "internal_table_uid": _text(record.get("internal_table_uid")),
                "row_index": row_index,
                "column_index": column_index,
                "source_sha256": source_sha,
                "table_sha256": table_sha,
            }
        )
    if len({entry["operand_id"] for entry in entries}) != len(entries):
        return None
    return _canonical_sha256(sorted(entries, key=lambda entry: entry["operand_id"]))


def _blocked(
    *,
    question_id: int | str | None,
    reasons: Sequence[str],
    source_hash: str | None = None,
    replay_receipt: Mapping[str, Any] | None = None,
) -> ComposedCandidatePlanResult:
    unique_reasons = tuple(dict.fromkeys(str(reason) for reason in reasons if str(reason)))
    receipt = {
        "protocol": "grounded_typed_plan_shadow_v1",
        "status": "shadow_blocked",
        "result_value": None,
        "result_period": None,
        "output_unit": None,
        "exact_binding_count": 0,
        "reason_codes": list(unique_reasons),
        "submission_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
    }
    if replay_receipt:
        receipt.update(_json_safe(dict(replay_receipt)))
    return ComposedCandidatePlanResult(
        status=PRODUCTION_BLOCKED,
        question_id=question_id,
        candidate_plan=None,
        reason_codes=unique_reasons,
        replay_receipt=receipt,
        source_hash=source_hash,
    )


def produce_composed_candidate_plan(
    question_plan: object,
    bound_operands: object,
    *,
    source_tables: Mapping[str, Mapping[str, Any]] | None = None,
    source_contexts: Mapping[str, Mapping[str, Any]] | None = None,
    pandas_query: str | None = None,
    expected_source_hash: str | None = None,
    route_family: str | None = None,
) -> ComposedCandidatePlanResult:
    """Produce one replay-ready composed/temporal candidate or block it.

    No field is filled from the question text.  In particular, the producer
    does not infer a ticker, period, scope, unit, row, or operation from a
    natural-language question.  A valid plan therefore requires explicit
    operands and explicit source bindings.
    """

    payload = _question_payload(question_plan)
    if payload is None:
        return _blocked(question_id=None, reasons=("QUESTION_PLAN_INVALID",))
    question_id = _question_id(payload)
    if "question_id" in payload and question_id is None and payload.get("question_id") is not None:
        return _blocked(question_id=None, reasons=("QUESTION_ID_INVALID",))

    family = _normalise_family(payload.get("effective_family", payload.get("family")))
    if family not in _COMPOSED_FAMILIES:
        return _blocked(
            question_id=question_id,
            reasons=(f"FAMILY_UNSUPPORTED:{family or 'missing'}",),
        )
    declared_status = payload.get("decomposition_status")
    if declared_status is not None and declared_status != "complete":
        return _blocked(
            question_id=question_id,
            reasons=(f"QUESTION_PLAN_NOT_COMPLETE:{_text(declared_status) or 'missing'}",),
        )

    operation_ast = payload.get("operation_ast")
    if not isinstance(operation_ast, Mapping):
        return _blocked(question_id=question_id, reasons=("OPERATION_AST_MISSING",))
    ast_errors = validate_operation_ast(operation_ast)
    if ast_errors:
        return _blocked(
            question_id=question_id,
            reasons=tuple(f"OPERATION_AST_INVALID:{error}" for error in ast_errors),
        )
    references: set[str] = set()
    _collect_ast_references(operation_ast, references)
    if not references:
        return _blocked(question_id=question_id, reasons=("OPERATION_AST_HAS_NO_OPERANDS",))

    typed_operands, typed_errors = _normalise_typed_operands(payload, references)
    if typed_errors:
        return _blocked(question_id=question_id, reasons=typed_errors)
    family_errors = _validate_family_contract(family, operation_ast, typed_operands, references)
    if family_errors:
        return _blocked(question_id=question_id, reasons=family_errors)

    records = _record_sequence(bound_operands)
    if records is None:
        return _blocked(question_id=question_id, reasons=("BOUND_OPERANDS_INVALID",))
    record_by_id: dict[str, Mapping[str, Any]] = {}
    for record in records:
        operand_id = _text(record.get("operand_id"))
        if not operand_id or operand_id in record_by_id:
            return _blocked(
                question_id=question_id,
                reasons=("BOUND_OPERAND_ID_DUPLICATE_OR_MISSING",),
            )
        record_by_id[operand_id] = record
    if set(record_by_id) != references:
        return _blocked(
            question_id=question_id,
            reasons=("BOUND_OPERAND_SET_MISMATCH",),
        )

    if not isinstance(source_tables, Mapping) or not isinstance(source_contexts, Mapping):
        return _blocked(
            question_id=question_id,
            reasons=("SOURCE_TABLES_AND_CONTEXTS_REQUIRED",),
        )
    if pandas_query is None or not isinstance(pandas_query, str) or not pandas_query.strip():
        return _blocked(question_id=question_id, reasons=("PANDAS_QUERY_REQUIRED",))

    calculated_source_hash = source_closure_hash(records)
    if calculated_source_hash is None:
        return _blocked(question_id=question_id, reasons=("SOURCE_CLOSURE_HASH_INVALID",))
    if expected_source_hash is not None:
        if not _valid_sha(expected_source_hash):
            return _blocked(
                question_id=question_id,
                reasons=("EXPECTED_SOURCE_HASH_INVALID",),
            )
        if expected_source_hash != calculated_source_hash:
            return _blocked(
                question_id=question_id,
                reasons=("EXPECTED_SOURCE_HASH_MISMATCH",),
                source_hash=calculated_source_hash,
            )

    typed_by_id = {str(operand["operand_id"]): operand for operand in typed_operands}
    executor_inputs: dict[str, Mapping[str, Any]] = {}
    executor_tables: dict[str, Mapping[str, Any]] = {}
    binding_errors: list[str] = []
    candidate_operands: list[CandidateOperand] = []
    for operand_id in sorted(references):
        executor_record, table, errors = _validate_bound_record(
            record_by_id[operand_id],
            operand_id=operand_id,
            source_tables=source_tables,
            source_contexts=source_contexts,
            typed_operand=typed_by_id[operand_id],
        )
        binding_errors.extend(errors)
        if executor_record is not None:
            executor_inputs[operand_id] = executor_record
        if table is not None:
            executor_tables[_text(table.get("internal_table_uid"))] = table
        record = record_by_id[operand_id]
        binding = record.get("binding")
        if isinstance(binding, Mapping):
            candidate_operands.append(
                CandidateOperand(
                    operand_id=operand_id,
                    source=_text(record.get("document_id")),
                    table=_text(record.get("internal_table_uid")),
                    row=binding.get("row_index"),
                    column=binding.get("column_index"),
                    period=_strict_year(record.get("report_year")) or 0,
                    entity=_text(record.get("ticker")),
                    scope=_text(record.get("scope")),
                    unit=_text(record.get("source_unit")),
                    raw_value=binding.get("raw_value"),
                )
            )
    if binding_errors:
        return _blocked(
            question_id=question_id,
            reasons=binding_errors,
            source_hash=calculated_source_hash,
        )

    declared_entities = payload.get("entities", payload.get("tickers"))
    if isinstance(declared_entities, list) and declared_entities:
        expected_entities = {_text(value).casefold() for value in declared_entities if _text(value)}
        actual_entities = {str(operand["entity"]).casefold() for operand in typed_operands}
        if expected_entities != actual_entities:
            return _blocked(
                question_id=question_id,
                reasons=("QUESTION_ENTITY_SET_MISMATCH",),
                source_hash=calculated_source_hash,
            )
    declared_years = payload.get("years")
    if isinstance(declared_years, list) and declared_years:
        expected_years = {_strict_year(value) for value in declared_years}
        if None in expected_years:
            return _blocked(
                question_id=question_id,
                reasons=("QUESTION_PERIOD_SET_INVALID",),
                source_hash=calculated_source_hash,
            )
        actual_years = {int(operand["years"][0]) for operand in typed_operands}
        if expected_years != actual_years:
            return _blocked(
                question_id=question_id,
                reasons=("QUESTION_PERIOD_SET_MISMATCH",),
                source_hash=calculated_source_hash,
            )
    declared_scope = _text(payload.get("scope"))
    if declared_scope and any(
        _text(operand.get("scope")).casefold() != declared_scope.casefold()
        for operand in typed_operands
    ):
        return _blocked(
            question_id=question_id,
            reasons=("QUESTION_SCOPE_MISMATCH",),
            source_hash=calculated_source_hash,
        )

    selected_route = _text(route_family) or _text(payload.get("route"))
    if not selected_route:
        selected_route = {
            "temporal_change": "temporal_candidate",
            "composed_arithmetic": "composed_candidate",
        }.get(family, f"{family}_candidate")

    typed_plan = _typed_plan_for_executor(
        payload,
        family=family,
        operation_ast=operation_ast,
        typed_operands=typed_operands,
        route_family=selected_route,
    )
    try:
        replay = execute_typed_plan_shadow(
            typed_plan,
            executor_inputs,
            source_tables=executor_tables,
            source_contexts=source_contexts,
        )
    except (AttributeError, InvalidOperation, KeyError, TypeError, ValueError) as error:
        return _blocked(
            question_id=question_id,
            reasons=(f"EXECUTOR_REPLAY_EXCEPTION:{type(error).__name__}",),
            source_hash=calculated_source_hash,
        )
    if replay.get("status") != "shadow_complete":
        executor_reasons = [
            f"EXECUTOR_REPLAY_BLOCKED:{_text(reason)}"
            for reason in replay.get("reason_codes") or []
            if _text(reason)
        ]
        return _blocked(
            question_id=question_id,
            reasons=executor_reasons or ("EXECUTOR_REPLAY_BLOCKED",),
            source_hash=calculated_source_hash,
            replay_receipt=replay,
        )

    requested_unit = _text(payload.get("requested_unit"))
    replay_output_unit = _text(replay.get("output_unit"))
    if requested_unit and requested_unit.casefold() != replay_output_unit.casefold():
        return _blocked(
            question_id=question_id,
            reasons=("OUTPUT_UNIT_MISMATCH",),
            source_hash=calculated_source_hash,
            replay_receipt=replay,
        )
    replay_value = replay.get("result_value")
    if replay_value is None and replay.get("result_period") is None:
        return _blocked(
            question_id=question_id,
            reasons=("EXECUTOR_REPLAY_RESULT_MISSING",),
            source_hash=calculated_source_hash,
            replay_receipt=replay,
        )
    try:
        candidate_value: Decimal | str
        if replay.get("result_period") is not None:
            candidate_value = str(int(replay["result_period"]))
        else:
            candidate_value = Decimal(str(replay_value))
            if not candidate_value.is_finite():
                raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return _blocked(
            question_id=question_id,
            reasons=("EXECUTOR_REPLAY_RESULT_INVALID",),
            source_hash=calculated_source_hash,
            replay_receipt=replay,
        )

    try:
        candidate_plan = CandidatePlan(
            question_id=question_id,
            operation_ast=operation_ast,
            operands=candidate_operands,
            pandas_query=pandas_query,
            replay_answer_decimal=candidate_value,
            source_hash=calculated_source_hash,
            semantic_completeness=1.0,
            route_family=selected_route,
            verification_status="REPLAY_READY",
        )
    except (CandidatePlanValidationError, TypeError, ValueError) as error:
        return _blocked(
            question_id=question_id,
            reasons=(f"CANDIDATE_PLAN_CONTRACT_REJECTED:{type(error).__name__}",),
            source_hash=calculated_source_hash,
            replay_receipt=replay,
        )

    return ComposedCandidatePlanResult(
        status=PRODUCTION_REPLAY_READY,
        question_id=question_id,
        candidate_plan=candidate_plan,
        reason_codes=("AST_AND_ALL_OPERANDS_REPLAYED",),
        replay_receipt=_json_safe(replay),  # type: ignore[arg-type]
        source_hash=calculated_source_hash,
    )


# Friendly aliases keep the public vocabulary close to the brief while
# preserving one implementation and one contract.
produce_candidate_plan = produce_composed_candidate_plan
produce_composed_candidate_plans = produce_composed_candidate_plan


__all__ = [
    "COMPOSED_CANDIDATE_PLAN_PROTOCOL",
    "COMPOSED_CANDIDATE_PLAN_SCHEMA_VERSION",
    "ComposedCandidatePlanError",
    "ComposedCandidatePlanResult",
    "PRODUCTION_BLOCKED",
    "PRODUCTION_REPLAY_READY",
    "produce_candidate_plan",
    "produce_composed_candidate_plan",
    "produce_composed_candidate_plans",
    "source_closure_hash",
]
