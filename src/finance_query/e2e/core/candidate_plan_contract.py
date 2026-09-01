"""Typed, candidate-only contracts for multi-route answer planning.

This module is deliberately an adapter contract.  It does not select an
authoritative answer, execute a formula, or promote a proposal to
``VERIFIED``.  A :class:`CandidatePlan` records enough information for a
later independent checker to inspect one complete explanation of a question.

``question_id`` is retained for tracing and joins only.  It is intentionally
excluded from plan deduplication and ordering so this contract cannot become a
Question-ID allowlist in disguise.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, ClassVar


CANDIDATE_PLAN_SCHEMA_VERSION = 1
CANDIDATE_PLAN_PROTOCOL = "candidate_plan_fail_closed_v1"
CANDIDATE_PLAN_SET_SCHEMA_VERSION = 1
CANDIDATE_PLAN_SET_PROTOCOL = "candidate_plan_set_fail_closed_v1"

DEFAULT_MAX_PLANS = 8
MAX_MAX_PLANS = 64

# These are intentionally candidate-side states.  A verified or authorized
# state belongs to a separate certificate/authority contract and must never be
# smuggled into this value-carrying planning lane.
CANDIDATE_VERIFICATION_STATUSES = frozenset(
    {
        "ABSTAIN",
        "CANDIDATE",
        "FALLBACK",
        "PARTIAL",
        "REPLAY_READY",
        "UNRESOLVED",
    }
)
FORBIDDEN_AUTHORITY_STATUSES = frozenset(
    {
        "AUTHORIZED",
        "RELEASE_AUTHORIZED",
        "RELEASED",
        "SUBMISSION_READY",
        "VERIFIED",
    }
)
FALLBACK_ROUTE_FAMILIES = frozenset(
    {
        "baseline_fallback",
        "fallback",
        "semantic_cell_heuristic_fallback",
    }
)

_OPERAND_REFERENCE_KEYS = frozenset(
    {
        "args",
        "denominator",
        "input",
        "inputs",
        "left",
        "numerator",
        "operand",
        "operand_id",
        "operands",
        "over_operands",
        "right",
        "return_operand",
        "select_operand",
        "target_operand",
    }
)
_NONFINITE_TEXT = frozenset(
    {
        "nan",
        "+nan",
        "-nan",
        "inf",
        "+inf",
        "-inf",
        "infinity",
        "+infinity",
        "-infinity",
    }
)


class CandidatePlanValidationError(ValueError):
    """Raised when a candidate plan would be unsafe to pass downstream."""


def _error(field: str, message: str) -> CandidatePlanValidationError:
    return CandidatePlanValidationError(f"{field}: {message}")


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _error(field, "must be a non-empty string")
    result = value.strip()
    if not result:
        raise _error(field, "must be a non-empty string")
    return result


def _tracking_question_id(value: object) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _error("question_id", "must be an integer, string, or null")
    if isinstance(value, str) and not value.strip():
        raise _error("question_id", "must not be blank when provided")
    return value.strip() if isinstance(value, str) else value


def _coordinate(value: object, field: str) -> int | str:
    if isinstance(value, bool):
        raise _error(field, "must be a non-negative integer or non-empty string")
    if isinstance(value, int):
        if value < 0:
            raise _error(field, "must be non-negative")
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _error(field, "must be a non-negative integer or non-empty string")


def _period(value: object) -> int | str:
    if isinstance(value, bool) or value is None:
        raise _error("period", "is required and must be an integer or non-empty string")
    if isinstance(value, int):
        if value < 0:
            raise _error("period", "must be non-negative")
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _error("period", "is required and must be an integer or non-empty string")


def _finite_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise _error(field, "must be a finite decimal")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise _error(field, "must be a finite decimal")
        result = Decimal(str(value))
    elif isinstance(value, (int, str)):
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError):
            raise _error(field, "must be a finite decimal") from None
    else:
        raise _error(field, "must be a finite decimal")
    if not result.is_finite():
        raise _error(field, "must be a finite decimal")
    return result


def _decimal_text(value: Decimal) -> str:
    """Return one deterministic, exponent-free representation for a Decimal."""

    result = format(value.normalize(), "f")
    return "0" if result in {"-0", "-0.0"} else result


def _raw_value(value: object) -> str:
    if value is None or isinstance(value, bool):
        raise _error("raw_value", "is required")
    if isinstance(value, Decimal):
        return _decimal_text(_finite_decimal(value, "raw_value"))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error("raw_value", "must not be non-finite")
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        result = value.strip()
        if not result:
            raise _error("raw_value", "must be non-empty")
        if result.casefold() in _NONFINITE_TEXT:
            raise _error("raw_value", "must not be non-finite")
        return result
    raise _error("raw_value", "must be text or a finite numeric value")


def _json_ready(value: object, field: str = "value") -> Any:
    """Validate JSON-compatible values and normalize Decimal instances."""

    if isinstance(value, Decimal):
        return _decimal_text(_finite_decimal(value, field))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error(field, "contains a non-finite number")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise _error(field, "object keys must be strings")
            result[key] = _json_ready(nested, f"{field}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_json_ready(item, f"{field}[]") for item in value]
    raise _error(field, f"contains unsupported value type {type(value).__name__}")


def _validate_ast_shape(ast: Mapping[str, Any]) -> None:
    if not isinstance(ast, Mapping) or not ast:
        raise _error("operation_ast", "is required and must be a non-empty object")
    operation = ast.get("op")
    if not isinstance(operation, str) or not operation.strip():
        raise _error("operation_ast.op", "is required and must be a non-empty string")
    # This checks nested values, including Decimal/float constants, without
    # constraining the AST to one executor's operator registry.  The executor
    # remains the authority for supported operators and arithmetic semantics.
    _json_ready(ast, "operation_ast")


def _collect_operand_references(value: object, output: set[str]) -> None:
    if isinstance(value, str):
        if value.strip():
            output.add(value.strip())
        return
    if isinstance(value, Mapping):
        _collect_ast_references(value, output)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_operand_references(item, output)


def _collect_ast_references(node: object, output: set[str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in _OPERAND_REFERENCE_KEYS:
                _collect_operand_references(value, output)
            elif isinstance(value, (Mapping, list, tuple)):
                _collect_ast_references(value, output)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_ast_references(item, output)


def _arg_extreme_periods_cover_operands(
    ast: Mapping[str, Any], operands: Sequence["CandidateOperand"]
) -> bool:
    """Allow the brief's period-list argmax shape only when it is grounded.

    Some argmax/argmin plans name their population through ``over_periods``
    rather than putting operand IDs in ``args``.  The explicit period list is
    accepted only when it is a non-empty, duplicate-free exact match for the
    typed operand periods; it never authorizes a missing or substitute cell.
    """

    operation = str(ast.get("op") or "").strip().casefold()
    if operation not in {"argmax", "argmin", "arg_extreme_period"}:
        return False
    periods = ast.get("over_periods")
    if not isinstance(periods, (list, tuple)) or not periods:
        return False
    period_tokens = [str(period).strip() for period in periods]
    if any(not token for token in period_tokens) or len(set(period_tokens)) != len(period_tokens):
        return False
    operand_periods = [str(operand.period).strip() for operand in operands]
    return sorted(period_tokens) == sorted(operand_periods)


def _normalise_status(value: object) -> str:
    status = _nonempty_text(value, "verification_status").upper()
    if status in FORBIDDEN_AUTHORITY_STATUSES:
        raise _error(
            "verification_status",
            f"{status} is an authority state and cannot be carried by CandidatePlan",
        )
    if status not in CANDIDATE_VERIFICATION_STATUSES:
        allowed = ", ".join(sorted(CANDIDATE_VERIFICATION_STATUSES))
        raise _error("verification_status", f"unsupported candidate state; allowed={allowed}")
    return status


def _normalise_completeness(value: object) -> float:
    if isinstance(value, bool):
        raise _error("semantic_completeness", "must be a finite number in [0, 1]")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise _error("semantic_completeness", "must be a finite number in [0, 1]") from None
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise _error("semantic_completeness", "must be a finite number in [0, 1]")
    return result


@dataclass(frozen=True, slots=True)
class CandidateOperand:
    """One source-bound numeric operand in a candidate plan.

    ``source`` and ``table`` are the canonical serialized names.  They are
    source/table UIDs rather than free-form evidence, and compatibility
    properties named ``source_uid``/``table_uid`` are provided for callers
    using the longer vocabulary from the research brief.
    """

    operand_id: str
    source: str
    table: str
    row: int | str
    column: int | str
    period: int | str
    entity: str
    scope: str
    unit: str
    raw_value: str | Decimal | int | float

    def __post_init__(self) -> None:
        object.__setattr__(self, "operand_id", _nonempty_text(self.operand_id, "operand_id"))
        object.__setattr__(self, "source", _nonempty_text(self.source, "source"))
        object.__setattr__(self, "table", _nonempty_text(self.table, "table"))
        object.__setattr__(self, "row", _coordinate(self.row, "row"))
        object.__setattr__(self, "column", _coordinate(self.column, "column"))
        object.__setattr__(self, "period", _period(self.period))
        object.__setattr__(self, "entity", _nonempty_text(self.entity, "entity"))
        object.__setattr__(self, "scope", _nonempty_text(self.scope, "scope"))
        object.__setattr__(self, "unit", _nonempty_text(self.unit, "unit"))
        object.__setattr__(self, "raw_value", _raw_value(self.raw_value))

    @property
    def source_uid(self) -> str:
        return self.source

    @property
    def table_uid(self) -> str:
        return self.table

    @property
    def row_index(self) -> int | str:
        return self.row

    @property
    def column_index(self) -> int | str:
        return self.column

    def to_dict(self) -> dict[str, Any]:
        return {
            "operand_id": self.operand_id,
            "source": self.source,
            "table": self.table,
            "row": self.row,
            "column": self.column,
            "period": self.period,
            "entity": self.entity,
            "scope": self.scope,
            "unit": self.unit,
            "raw_value": self.raw_value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateOperand":
        if not isinstance(payload, Mapping):
            raise _error("operand", "must be an object")
        return cls(
            operand_id=payload.get("operand_id"),
            source=payload.get("source", payload.get("source_uid")),
            table=payload.get("table", payload.get("table_uid")),
            row=payload.get("row", payload.get("row_index")),
            column=payload.get("column", payload.get("column_index")),
            period=payload.get("period"),
            entity=payload.get("entity", payload.get("ticker")),
            scope=payload.get("scope"),
            unit=payload.get("unit", payload.get("source_unit")),
            raw_value=payload.get("raw_value", payload.get("value")),
        )


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    """One complete, non-authoritative explanation of a question.

    A normal plan requires at least one typed operand, a non-empty AST, a
    query, a source hash, and AST references that cover exactly the operand
    IDs.  The only zero-operand exception is the explicit ``fallback``
    factory, which is visibly candidate-only and sorted behind complete plans.
    """

    question_id: int | str | None
    operation_ast: Mapping[str, Any] | None
    operands: Sequence[CandidateOperand | Mapping[str, Any]] | None
    pandas_query: str | None
    replay_answer_decimal: Decimal | str | int | float | None
    source_hash: str | None
    semantic_completeness: float
    route_family: str
    verification_status: str = "CANDIDATE"

    _AUTHORITY_FIELDS: ClassVar[dict[str, bool]] = {
        "candidate_only": True,
        "answer_authority": False,
        "release_authorized": False,
        "submission_eligible": False,
        "training_eligible": False,
        "promotion_allowed": False,
    }

    def __post_init__(self) -> None:
        question_id = _tracking_question_id(self.question_id)
        object.__setattr__(self, "question_id", question_id)

        if not isinstance(self.operation_ast, Mapping):
            raise _error("operation_ast", "is required and must be a non-empty object")
        ast = deepcopy(dict(self.operation_ast))
        _validate_ast_shape(ast)
        object.__setattr__(self, "operation_ast", ast)

        if self.operands is None or isinstance(self.operands, (str, bytes)):
            normalized_operands: tuple[CandidateOperand, ...] = ()
        else:
            try:
                normalized_operands = tuple(
                    operand
                    if isinstance(operand, CandidateOperand)
                    else CandidateOperand.from_dict(operand)
                    for operand in self.operands
                )
            except TypeError:
                raise _error("operands", "must be a sequence of operand objects") from None
        seen_ids: set[str] = set()
        for operand in normalized_operands:
            if operand.operand_id in seen_ids:
                raise _error("operands", f"duplicate operand_id={operand.operand_id}")
            seen_ids.add(operand.operand_id)
        object.__setattr__(self, "operands", normalized_operands)

        route_family = _nonempty_text(self.route_family, "route_family")
        object.__setattr__(self, "route_family", route_family)
        status = _normalise_status(self.verification_status)
        object.__setattr__(self, "verification_status", status)
        completeness = _normalise_completeness(self.semantic_completeness)
        object.__setattr__(self, "semantic_completeness", completeness)

        if self.pandas_query is None:
            pandas_query = None
        elif isinstance(self.pandas_query, str):
            pandas_query = self.pandas_query.strip() or None
        else:
            raise _error("pandas_query", "must be a string or null")
        object.__setattr__(self, "pandas_query", pandas_query)

        if self.source_hash is None:
            source_hash = None
        elif isinstance(self.source_hash, str):
            source_hash = self.source_hash.strip() or None
        else:
            raise _error("source_hash", "must be a non-empty string or null")
        object.__setattr__(self, "source_hash", source_hash)

        replay = None
        if self.replay_answer_decimal is not None:
            replay = _finite_decimal(self.replay_answer_decimal, "replay_answer_decimal")
        object.__setattr__(self, "replay_answer_decimal", replay)

        is_fallback = self._is_fallback_route(route_family, ast)
        if is_fallback:
            if normalized_operands:
                raise _error("operands", "fallback candidates must not carry operands")
            if str(ast.get("op")).strip().casefold() != "fallback":
                raise _error("operation_ast", "fallback candidates require op=fallback")
            if completeness != 0.0:
                raise _error("semantic_completeness", "fallback candidates must be 0")
            if source_hash is not None:
                raise _error("source_hash", "fallback candidates cannot claim source provenance")
            if pandas_query is not None:
                raise _error("pandas_query", "fallback candidates cannot claim a source query")
            if status != "FALLBACK":
                object.__setattr__(self, "verification_status", "FALLBACK")
            return

        if not normalized_operands:
            raise _error("operands", "at least one operand is required")
        if source_hash is None:
            raise _error("source_hash", "is required for non-fallback candidates")
        if pandas_query is None:
            raise _error("pandas_query", "is required for non-fallback candidates")

        references: set[str] = set()
        _collect_ast_references(ast, references)
        if not references:
            if _arg_extreme_periods_cover_operands(ast, normalized_operands):
                return
            raise _error("operation_ast", "must reference at least one operand")
        missing = sorted(seen_ids - references)
        unknown = sorted(references - seen_ids)
        if missing:
            raise _error(
                "operation_ast",
                f"missing operand references: {', '.join(missing)}",
            )
        if unknown:
            raise _error(
                "operation_ast",
                f"references unknown operands: {', '.join(unknown)}",
            )

    @staticmethod
    def _is_fallback_route(route_family: str, ast: Mapping[str, Any]) -> bool:
        return (
            route_family.casefold() in FALLBACK_ROUTE_FAMILIES
            or str(ast.get("op") or "").strip().casefold() == "fallback"
        )

    @classmethod
    def fallback(
        cls,
        question_id: int | str | None,
        *,
        replay_answer_decimal: Decimal | str | int | float | None = None,
        route_family: str = "fallback",
    ) -> "CandidatePlan":
        """Create an explicit zero-operand fallback candidate.

        This is intentionally not a shortcut for a valid plan: no source hash,
        query, or authority is attached, and the set ordering places it after
        source-bound plans.
        """

        return cls(
            question_id=question_id,
            operation_ast={"op": "fallback", "args": []},
            operands=(),
            pandas_query=None,
            replay_answer_decimal=replay_answer_decimal,
            source_hash=None,
            semantic_completeness=0.0,
            route_family=route_family,
            verification_status="FALLBACK",
        )

    @property
    def candidate_only(self) -> bool:
        return True

    @property
    def answer_authority(self) -> bool:
        return False

    @property
    def release_authorized(self) -> bool:
        return False

    @property
    def is_verified(self) -> bool:
        return False

    @property
    def is_fallback(self) -> bool:
        return self._is_fallback_route(self.route_family, self.operation_ast)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol": CANDIDATE_PLAN_PROTOCOL,
            "schema_version": CANDIDATE_PLAN_SCHEMA_VERSION,
            "question_id": self.question_id,
            "operation_ast": _json_ready(self.operation_ast, "operation_ast"),
            "operands": [operand.to_dict() for operand in self.operands],
            "pandas_query": self.pandas_query,
            "replay_answer_decimal": (
                _decimal_text(self.replay_answer_decimal)
                if self.replay_answer_decimal is not None
                else None
            ),
            "source_hash": self.source_hash,
            "semantic_completeness": self.semantic_completeness,
            "route_family": self.route_family,
            "verification_status": self.verification_status,
        }
        payload.update(self._AUTHORITY_FIELDS)
        return payload

    def stable_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    # ``to_json`` is a friendly alias for callers that use the surrounding
    # repository's serialization vocabulary.
    def to_json(self) -> str:
        return self.stable_json()

    def fingerprint(self) -> str:
        return hashlib.sha256(self.stable_json().encode("utf-8")).hexdigest()

    def dedup_key(self) -> str:
        """Hash plan semantics while ignoring tracking and authority fields."""

        payload = self.to_dict()
        for key in (
            "answer_authority",
            "candidate_only",
            "question_id",
            "release_authorized",
            "schema_version",
            "protocol",
            "submission_eligible",
            "training_eligible",
            "promotion_allowed",
            "verification_status",
        ):
            payload.pop(key, None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidatePlan":
        if not isinstance(payload, Mapping):
            raise _error("candidate_plan", "must be an object")
        if payload.get("protocol") not in (None, CANDIDATE_PLAN_PROTOCOL):
            raise _error("protocol", "does not match CandidatePlan contract")
        if payload.get("schema_version") not in (None, CANDIDATE_PLAN_SCHEMA_VERSION):
            raise _error("schema_version", "does not match CandidatePlan contract")
        for field, expected in cls._AUTHORITY_FIELDS.items():
            if field in payload and payload[field] is not expected:
                raise _error(field, f"must remain {str(expected).lower()}")
        return cls(
            question_id=payload.get("question_id"),
            operation_ast=payload.get("operation_ast"),
            operands=payload.get("operands"),
            pandas_query=payload.get("pandas_query"),
            replay_answer_decimal=payload.get("replay_answer_decimal"),
            source_hash=payload.get("source_hash"),
            semantic_completeness=payload.get("semantic_completeness"),
            route_family=payload.get("route_family"),
            verification_status=payload.get("verification_status", "CANDIDATE"),
        )


def _status_order(status: str) -> int:
    return {
        "CANDIDATE": 0,
        "REPLAY_READY": 1,
        "PARTIAL": 2,
        "UNRESOLVED": 3,
        "ABSTAIN": 4,
        "FALLBACK": 5,
    }[status]


def _plan_order_key(plan: CandidatePlan) -> tuple[Any, ...]:
    # Candidate completeness is the primary ordering signal.  Route family is
    # only a lexical tie-breaker; question_id is deliberately absent.
    return (
        plan.is_fallback,
        -plan.semantic_completeness,
        not (plan.replay_answer_decimal is not None),
        not bool(plan.source_hash),
        -len(plan.operands),
        _status_order(plan.verification_status),
        plan.route_family.casefold(),
        # ``dedup_key`` excludes question_id and authority metadata.  It is
        # therefore safe as the final deterministic tie-breaker for a set
        # that accidentally contains plans from more than one tracking ID.
        plan.dedup_key(),
    )


@dataclass(frozen=True, slots=True)
class CandidatePlanSet:
    """A bounded, deterministic, deduplicated collection of candidates."""

    plans: Sequence[CandidatePlan | Mapping[str, Any]] = ()
    max_plans: int = DEFAULT_MAX_PLANS

    def __post_init__(self) -> None:
        if isinstance(self.max_plans, bool) or not isinstance(self.max_plans, int):
            raise _error("max_plans", "must be an integer")
        if not 1 <= self.max_plans <= MAX_MAX_PLANS:
            raise _error("max_plans", f"must be in [1, {MAX_MAX_PLANS}]")
        if isinstance(self.plans, (str, bytes)):
            raise _error("plans", "must be a sequence of CandidatePlan objects")
        try:
            raw_plans = tuple(self.plans)
        except TypeError:
            raise _error("plans", "must be a sequence of CandidatePlan objects") from None
        normalized: list[CandidatePlan] = []
        for plan in raw_plans:
            if isinstance(plan, CandidatePlan):
                normalized.append(plan)
            elif isinstance(plan, Mapping):
                normalized.append(CandidatePlan.from_dict(plan))
            else:
                raise _error("plans", "contains a non-CandidatePlan value")

        ordered = sorted(normalized, key=_plan_order_key)
        unique: list[CandidatePlan] = []
        seen: set[str] = set()
        for plan in ordered:
            key = plan.dedup_key()
            if key in seen:
                continue
            seen.add(key)
            unique.append(plan)
            if len(unique) >= self.max_plans:
                break
        object.__setattr__(self, "plans", tuple(unique))

    def __len__(self) -> int:
        return len(self.plans)

    def __iter__(self) -> Iterator[CandidatePlan]:
        return iter(self.plans)

    def __getitem__(self, index: int) -> CandidatePlan:
        return self.plans[index]

    def best(self) -> CandidatePlan | None:
        return self.plans[0] if self.plans else None

    def add(self, plan: CandidatePlan | Mapping[str, Any]) -> "CandidatePlanSet":
        """Return a new bounded set containing ``plan``."""

        return CandidatePlanSet((*self.plans, plan), max_plans=self.max_plans)

    def extend(
        self,
        plans: Sequence[CandidatePlan | Mapping[str, Any]],
    ) -> "CandidatePlanSet":
        return CandidatePlanSet((*self.plans, *plans), max_plans=self.max_plans)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "protocol": CANDIDATE_PLAN_SET_PROTOCOL,
            "schema_version": CANDIDATE_PLAN_SET_SCHEMA_VERSION,
            "max_plans": self.max_plans,
            "plans": [plan.to_dict() for plan in self.plans],
        }
        payload.update(CandidatePlan._AUTHORITY_FIELDS)
        return payload

    def stable_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def to_json(self) -> str:
        return self.stable_json()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidatePlanSet":
        if not isinstance(payload, Mapping):
            raise _error("candidate_plan_set", "must be an object")
        if payload.get("protocol") not in (None, CANDIDATE_PLAN_SET_PROTOCOL):
            raise _error("protocol", "does not match CandidatePlanSet contract")
        if payload.get("schema_version") not in (None, CANDIDATE_PLAN_SET_SCHEMA_VERSION):
            raise _error("schema_version", "does not match CandidatePlanSet contract")
        for field, expected in CandidatePlan._AUTHORITY_FIELDS.items():
            if field in payload and payload[field] is not expected:
                raise _error(field, f"must remain {str(expected).lower()}")
        plans = payload.get("plans", payload.get("candidates", ()))
        return cls(plans=plans, max_plans=payload.get("max_plans", DEFAULT_MAX_PLANS))


def validate_candidate_plan(
    plan: CandidatePlan | Mapping[str, Any],
) -> CandidatePlan:
    """Validate/coerce one plan without granting it authority."""

    return plan if isinstance(plan, CandidatePlan) else CandidatePlan.from_dict(plan)


def validate_candidate_plan_set(
    plans: CandidatePlanSet | Sequence[CandidatePlan | Mapping[str, Any]],
    *,
    max_plans: int = DEFAULT_MAX_PLANS,
) -> CandidatePlanSet:
    """Validate/coerce a bounded plan set."""

    return plans if isinstance(plans, CandidatePlanSet) else CandidatePlanSet(plans, max_plans=max_plans)


__all__ = [
    "CANDIDATE_PLAN_PROTOCOL",
    "CANDIDATE_PLAN_SCHEMA_VERSION",
    "CANDIDATE_PLAN_SET_PROTOCOL",
    "CANDIDATE_PLAN_SET_SCHEMA_VERSION",
    "CANDIDATE_VERIFICATION_STATUSES",
    "CandidateOperand",
    "CandidatePlan",
    "CandidatePlanSet",
    "CandidatePlanValidationError",
    "DEFAULT_MAX_PLANS",
    "FALLBACK_ROUTE_FAMILIES",
    "MAX_MAX_PLANS",
    "validate_candidate_plan",
    "validate_candidate_plan_set",
]
