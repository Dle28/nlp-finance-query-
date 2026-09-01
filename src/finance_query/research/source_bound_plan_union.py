"""Research-only union of source-bound answer plans.

The production builder historically chooses the first route that returns an
answer and only then exposes a bounded set of alternatives to the answer-level
selector.  This module provides an offline experiment for the complementary
population-level hypothesis:

``complete_whole_question_plan_before_route_priority``

It joins proposal ledgers produced by several frozen, full-population runs,
re-hydrates every cited coordinate from one current structured-table asset,
recomputes operand values and the operation AST, and only then asks the
existing answer-level selector to choose among complete plans.  The module is
deliberately independent from the submission builder so the control and its
historical outputs remain untouched.

This is a candidate lane.  The output can contain answer values, but it never
creates a certificate, uses a gold answer, authorizes a submission, or claims
an accuracy gain.  ``question_id`` is used only to join records belonging to
the same question; it never changes the validation or ordering rule.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from finance_query.e2e.core.candidate_plan_selector import (
    AnswerLevelSelector as WholeQuestionAnswerLevelSelector,
    CandidatePlanSet,
)
from finance_query.e2e.decimal_executor import (
    execute_ast,
    parse_decimal,
    validate_operation_ast,
)
from finance_query.research.semantic_binding_feedback import audit_selected_plan


SOURCE_BOUND_PLAN_UNION_PROTOCOL = "vifinqa_source_bound_plan_union_v1"
SOURCE_BOUND_PLAN_UNION_SCHEMA_VERSION = 1
HYPOTHESIS = "complete_whole_question_plan_before_route_priority"
MAX_LEDGER_SOURCES = 64
MAX_PLANS_PER_QUESTION = 64
REPLAY_TOLERANCE = Decimal("0.000000001")
METRIC_ROW_BINDING_GATE_REASON = "METRIC_ROW_BINDING_GATE_FAILED"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INCOMPLETE_ROUTES = {
    "",
    "fallback_zero",
    "best_surviving_candidate",
    "semantic_cell_heuristic",
    "program_growth_heuristic",
    "program_subtract_heuristic",
    "program_ratio_heuristic",
}
_UNIT_DIVISORS: dict[str, Decimal] = {
    "vnd": Decimal("1"),
    "dong": Decimal("1"),
    "million_vnd": Decimal("1000000"),
    "million_dong": Decimal("1000000"),
    "trieu_vnd": Decimal("1000000"),
    "trieu_dong": Decimal("1000000"),
    "billion_vnd": Decimal("1000000000"),
    "billion_dong": Decimal("1000000000"),
    "ty_vnd": Decimal("1000000000"),
    "ty_dong": Decimal("1000000000"),
    "thousand_vnd": Decimal("1000"),
    "thousand_dong": Decimal("1000"),
    "nghin_vnd": Decimal("1000"),
    "nghin_dong": Decimal("1000"),
    "percent": Decimal("1"),
    "ratio": Decimal("1"),
    "count": Decimal("1"),
}
_UNIT_TEXT_MULTIPLIERS: tuple[tuple[tuple[str, ...], Decimal], ...] = (
    (("billion", "vnd"), Decimal("1000000000")),
    (("billion", "dong"), Decimal("1000000000")),
    (("ty", "vnd"), Decimal("1000000000")),
    (("ty", "dong"), Decimal("1000000000")),
    (("million", "vnd"), Decimal("1000000")),
    (("million", "dong"), Decimal("1000000")),
    (("trieu", "vnd"), Decimal("1000000")),
    (("trieu", "dong"), Decimal("1000000")),
    (("thousand", "vnd"), Decimal("1000")),
    (("thousand", "dong"), Decimal("1000")),
    (("nghin", "vnd"), Decimal("1000")),
    (("nghin", "dong"), Decimal("1000")),
)


class SourceBoundPlanUnionError(ValueError):
    """Raised when an experiment input would make the A/B result ambiguous."""


@dataclass(frozen=True, slots=True)
class HydratedProposal:
    """A proposal row plus a source-run label and source-bound plan."""

    source_label: str
    row: Mapping[str, Any]
    plan: Mapping[str, Any]
    reasons: tuple[str, ...]
    replay_status: str
    source_closure_status: str


def _text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _token(value: object) -> str:
    return "_".join(_text(value).casefold().split())


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        return value if value == value and abs(value) != float("inf") else None
    return value


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    # Answers and replay values emitted by the ledgers are canonical Decimal
    # text.  Parse that representation first: ``2030418.476`` must remain a
    # decimal answer, while the locale-aware parser below is still needed for
    # raw table cells such as ``2.030.418.476``.
    text = str(value).strip()
    try:
        canonical = Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        canonical = None
    if canonical is not None and canonical.is_finite():
        return canonical
    try:
        parsed = parse_decimal(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is not None and parsed.value is not None:
        try:
            result = Decimal(str(parsed.value))
        except (InvalidOperation, ValueError):
            result = None
        if result is not None and result.is_finite():
            return result
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _raw_cell_decimal(value: object) -> Decimal | None:
    """Parse a source cell with the repository's locale-aware rules."""

    try:
        parsed = parse_decimal(value)
    except (TypeError, ValueError):
        return None
    if parsed.value is None:
        return None
    try:
        result = Decimal(str(parsed.value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _normalised_text(value: object) -> str:
    text = _text(value).casefold()
    replacements = {
        "đ": "d",
        "ă": "a",
        "â": "a",
        "ê": "e",
        "ô": "o",
        "ơ": "o",
        "ư": "u",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return " ".join(text.split())


def _unit_divisor(value: object) -> Decimal:
    token = _token(value)
    if token in _UNIT_DIVISORS:
        return _UNIT_DIVISORS[token]
    text = _normalised_text(value).replace(" ", "_")
    return _UNIT_DIVISORS.get(text, Decimal("1"))


def _table_unit_multiplier(table: Mapping[str, Any]) -> Decimal:
    parts: list[str] = []
    for key in ("headers", "column_labels", "unit_hint"):
        value = table.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(_normalised_text(item) for item in value)
        elif value:
            parts.append(_normalised_text(value))
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        labels = trace.get("unit_labels")
        if isinstance(labels, (list, tuple)):
            parts.extend(_normalised_text(item) for item in labels)
        elif labels:
            parts.append(_normalised_text(labels))
    text = " ".join(parts)
    for tokens, multiplier in _UNIT_TEXT_MULTIPLIERS:
        if all(token in text for token in tokens):
            return multiplier
    return Decimal("1")


def _source_hash(table: Mapping[str, Any]) -> str:
    for key in ("table_sha256", "source_sha256"):
        value = _text(table.get(key)).lower()
        if _SHA256_RE.fullmatch(value):
            return value
    return ""


def _question_contract(question_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Extract whole-question constraints without using a question ID."""

    contract: dict[str, Any] = {}
    ast = question_plan.get("operation_ast")
    if isinstance(ast, Mapping) and ast:
        contract["operation_ast"] = _json_safe(ast)
    operands = [
        operand
        for operand in question_plan.get("operands") or []
        if isinstance(operand, Mapping)
    ]
    operand_ids = [
        _text(operand.get("operand_id") or operand.get("id"))
        for operand in operands
    ]
    operand_ids = [value for value in operand_ids if value]
    if operand_ids:
        contract["required_operand_ids"] = operand_ids
        contract["required_operand_count"] = len(operand_ids)

    periods: list[object] = []
    for operand in operands:
        period = operand.get("period", operand.get("year"))
        if period is None:
            years = operand.get("years")
            if isinstance(years, (list, tuple)) and len(years) == 1:
                period = years[0]
        if period is not None and period not in periods:
            periods.append(period)
    if not periods:
        for key in ("periods", "years"):
            value = question_plan.get(key)
            if isinstance(value, (list, tuple)):
                periods.extend(item for item in value if item not in periods)
            elif value is not None and value not in periods:
                periods.append(value)
    if periods:
        contract["required_periods"] = _json_safe(periods)

    entities = question_plan.get("entities") or question_plan.get("tickers") or []
    if not isinstance(entities, (list, tuple, set, frozenset)):
        entities = [entities] if entities else []
    entity_values = [_text(value) for value in entities if _text(value)]
    if not entity_values:
        entity_values = [
            _text(operand.get("entity") or operand.get("ticker"))
            for operand in operands
            if _text(operand.get("entity") or operand.get("ticker"))
        ]
    if entity_values:
        contract["required_entities"] = list(dict.fromkeys(entity_values))

    scope = question_plan.get("reporting_scope") or question_plan.get("scope")
    if scope:
        contract["required_scope"] = scope
    unit = (
        question_plan.get("requested_unit")
        or question_plan.get("source_unit")
        or question_plan.get("unit")
    )
    if unit:
        contract["required_unit"] = unit
    return contract


def _candidate_operands(
    row: Mapping[str, Any], question_plan: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    claims = row.get("claims")
    if isinstance(claims, Mapping):
        operands = claims.get("operands")
        if isinstance(operands, (list, tuple)) and operands:
            return [operand for operand in operands if isinstance(operand, Mapping)]
    operands = question_plan.get("operands")
    if isinstance(operands, (list, tuple)):
        return [operand for operand in operands if isinstance(operand, Mapping)]
    return []


def _candidate_evidence(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    evidence = row.get("evidence")
    if not isinstance(evidence, (list, tuple)):
        return []
    return [item for item in evidence if isinstance(item, Mapping)]


def _current_cell(
    evidence: Mapping[str, Any], tables_by_uid: Mapping[str, Mapping[str, Any]]
) -> tuple[Mapping[str, Any] | None, Decimal | None, list[str]]:
    reasons: list[str] = []
    uid = _text(evidence.get("internal_table_uid") or evidence.get("table_uid"))
    table = tables_by_uid.get(uid)
    if not uid or table is None:
        reasons.append("SOURCE_TABLE_MISSING")
        return table, None, reasons
    row_index = _int_or_none(evidence.get("row_index"))
    column_index = _int_or_none(evidence.get("column_index"))
    rows = table.get("rows")
    if row_index is None or column_index is None:
        reasons.append("SOURCE_COORDINATE_MISSING")
        return table, None, reasons
    if not isinstance(rows, list) or row_index < 0 or row_index >= len(rows):
        reasons.append("SOURCE_ROW_OUT_OF_RANGE")
        return table, None, reasons
    selected_row = rows[row_index]
    if not isinstance(selected_row, list) or column_index < 0 or column_index >= len(selected_row):
        reasons.append("SOURCE_COLUMN_OUT_OF_RANGE")
        return table, None, reasons
    actual = _raw_cell_decimal(selected_row[column_index])
    if actual is None:
        reasons.append("SOURCE_CELL_NOT_NUMERIC")
        return table, None, reasons
    claimed = evidence.get("raw_value")
    if claimed is None:
        claimed = evidence.get("source_value", evidence.get("raw_value_decimal"))
    if claimed is None:
        reasons.append("SOURCE_RAW_VALUE_MISSING")
    else:
        claimed_decimal = _raw_cell_decimal(claimed)
        if claimed_decimal is None or claimed_decimal != actual:
            reasons.append("SOURCE_RAW_VALUE_MISMATCH")
    if not _source_hash(table):
        reasons.append("SOURCE_HASH_MISSING")
    return table, actual, reasons


def _operand_value(
    evidence: Mapping[str, Any],
    table: Mapping[str, Any],
    actual: Decimal,
    question_plan: Mapping[str, Any],
) -> Decimal:
    multiplier = _decimal(
        evidence.get("source_to_vnd_multiplier")
        or evidence.get("source_multiplier")
        or evidence.get("unit_multiplier")
    )
    if multiplier is None or multiplier <= 0:
        multiplier = _table_unit_multiplier(table)
    divisor = _unit_divisor(
        question_plan.get("requested_unit")
        or question_plan.get("output_unit")
        or question_plan.get("unit")
    )
    return actual * multiplier / divisor


def _replay(
    ast: Mapping[str, Any],
    operands: Sequence[Mapping[str, Any]],
    answer: Decimal,
) -> tuple[str, Decimal | None, list[str]]:
    reasons: list[str] = []
    ast_errors = validate_operation_ast(ast)
    if ast_errors:
        return "FAIL", None, ["OPERATION_AST_INVALID", *ast_errors]
    values: dict[str, Any] = {}
    operation = _token(ast.get("op"))
    for operand in operands:
        operand_id = _text(operand.get("operand_id") or operand.get("id"))
        value = _decimal(operand.get("raw_value"))
        if not operand_id or value is None:
            reasons.append("OPERAND_VALUE_MISSING")
            continue
        if operation == "ARG_EXTREME_PERIOD":
            period = _int_or_none(operand.get("period", operand.get("year")))
            if period is None:
                reasons.append("OPERAND_PERIOD_MISSING_FOR_EXTREME")
            else:
                values[operand_id] = {"period": period, "value": value}
        else:
            values[operand_id] = value
    if reasons or not values:
        return "FAIL", None, reasons or ["OPERAND_VALUES_EMPTY"]
    try:
        result = execute_ast(ast, values)
    except (ArithmeticError, KeyError, TypeError, ValueError) as error:
        return "FAIL", None, ["AST_REPLAY_FAILED", type(error).__name__]
    replayed = _decimal(result)
    if replayed is None:
        return "FAIL", None, ["AST_REPLAY_NON_NUMERIC"]
    if abs(replayed - answer) > REPLAY_TOLERANCE:
        return "FAIL", replayed, ["REPLAY_ANSWER_MISMATCH"]
    return "PASS", replayed, []


def _route_is_structural(route: str) -> bool:
    normalized = _token(route)
    if normalized in {_token(item) for item in _INCOMPLETE_ROUTES}:
        return False
    return "HEURISTIC" not in normalized and normalized != "UNKNOWN"


def _plan_signature(plan: Mapping[str, Any]) -> str:
    evidence = plan.get("evidence")
    return canonical_sha256(
        {
            "answer": plan.get("answer_decimal"),
            "route": plan.get("route_family"),
            "ast": plan.get("operation_ast"),
            "operands": plan.get("operands"),
            "coordinates": plan.get("source_coordinates"),
            "evidence": evidence,
        }
    )


def hydrate_proposal(
    row: Mapping[str, Any],
    *,
    source_label: str,
    question_plan: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    is_baseline: bool = False,
) -> HydratedProposal | None:
    """Rebuild a candidate plan from current source coordinates.

    ``row`` may have been produced by an older run.  Its answer is never used
    as operand evidence: each cited cell is read from ``tables_by_uid`` and
    the AST is replayed against the rehydrated values.
    """

    question_id = row.get("question_id")
    route = _text(row.get("answer_route") or row.get("tier"))
    answer = _decimal(row.get("answer_decimal"))
    ast = row.get("operation_ast")
    if not isinstance(ast, Mapping):
        ast = question_plan.get("operation_ast")
    ast = deepcopy(dict(ast)) if isinstance(ast, Mapping) else {}
    claim_operands = _candidate_operands(row, question_plan)
    evidence = _candidate_evidence(row)
    reasons: list[str] = []
    if not isinstance(question_id, int) or isinstance(question_id, bool):
        return None
    if answer is None:
        reasons.append("ANSWER_MISSING_OR_INVALID")
    if not claim_operands:
        reasons.append("OPERANDS_MISSING")
    if len(evidence) != len(claim_operands):
        reasons.append("OPERAND_EVIDENCE_COUNT_MISMATCH")

    operands: list[dict[str, Any]] = []
    coordinates: list[dict[str, Any]] = []
    source_hashes: list[str] = []
    for index, claim in enumerate(claim_operands):
        prepared = deepcopy(dict(claim))
        operand_id = _text(prepared.get("operand_id") or prepared.get("id"))
        if not operand_id:
            reasons.append("OPERAND_ID_MISSING")
        prepared["operand_id"] = operand_id
        source = evidence[index] if index < len(evidence) else {}
        table, actual, source_reasons = _current_cell(source, tables_by_uid)
        reasons.extend(source_reasons)
        uid = _text(source.get("internal_table_uid") or source.get("table_uid"))
        row_index = _int_or_none(source.get("row_index"))
        column_index = _int_or_none(source.get("column_index"))
        if uid and row_index is not None and column_index is not None:
            coordinate = {
                "source_uid": uid,
                "table_uid": uid,
                "row_index": row_index,
                "column_index": column_index,
            }
            if table is not None and _source_hash(table):
                coordinate["source_hash"] = _source_hash(table)
                source_hashes.append(_source_hash(table))
            coordinates.append(coordinate)
            prepared["source"] = coordinate
            prepared.update(
                {
                    "source_uid": uid,
                    "table_uid": uid,
                    "row_index": row_index,
                    "column_index": column_index,
                }
            )
        if actual is not None and table is not None:
            prepared["raw_value"] = _operand_value(
                source, table, actual, question_plan
            )
        else:
            prepared["raw_value"] = None
        if _text(prepared.get("period", prepared.get("year"))):
            prepared["period"] = prepared.get("period", prepared.get("year"))
        for key in ("entity", "ticker", "scope", "unit", "source_unit"):
            if key in prepared and prepared[key] is not None:
                prepared[key] = prepared[key]
        if not prepared.get("scope"):
            scope = (row.get("claims") or {}).get("reporting_scope") if isinstance(row.get("claims"), Mapping) else None
            if scope:
                prepared["scope"] = scope
        if not prepared.get("unit"):
            unit = question_plan.get("requested_unit") or question_plan.get("unit")
            if unit:
                prepared["unit"] = unit
        operands.append(prepared)

    if answer is None:
        answer = Decimal("0")
    replay_status, replayed, replay_reasons = _replay(ast, operands, answer)
    reasons.extend(replay_reasons)
    if not _route_is_structural(route):
        reasons.append("ROUTE_PLAN_INCOMPLETE")
    question_ast = question_plan.get("operation_ast")
    if isinstance(question_ast, Mapping) and _canonical(ast) != _canonical(question_ast):
        reasons.append("QUESTION_OPERATION_AST_MISMATCH")

    unique_reasons = tuple(dict.fromkeys(str(reason) for reason in reasons if reason))
    closure_pass = not any(
        reason
        in {
            "SOURCE_TABLE_MISSING",
            "SOURCE_COORDINATE_MISSING",
            "SOURCE_ROW_OUT_OF_RANGE",
            "SOURCE_COLUMN_OUT_OF_RANGE",
            "SOURCE_CELL_NOT_NUMERIC",
            "SOURCE_RAW_VALUE_MISSING",
            "SOURCE_RAW_VALUE_MISMATCH",
            "SOURCE_HASH_MISSING",
            "OPERAND_EVIDENCE_COUNT_MISMATCH",
            "OPERANDS_MISSING",
            "OPERAND_ID_MISSING",
        }
        for reason in unique_reasons
    )
    complete = (
        not unique_reasons
        and replay_status == "PASS"
        and closure_pass
        and isinstance(ast, Mapping)
    )
    verification_status = "REPLAY_READY" if complete else "CANDIDATE"
    if is_baseline and not complete:
        # Baseline is passed separately to the selector and may be retained by
        # its explicit fallback rule.  It is never made structurally complete.
        verification_status = "CANDIDATE"
    plan: dict[str, Any] = {
        "plan_id": f"{source_label}:{question_id}:{_plan_signature({'answer_decimal': str(answer), 'route_family': route, 'operation_ast': ast, 'operands': operands, 'source_coordinates': coordinates})[:16]}",
        "candidate_id": f"{source_label}:{question_id}:{_plan_signature({'answer_decimal': str(answer), 'route_family': route, 'operation_ast': ast, 'operands': operands, 'source_coordinates': coordinates})[:16]}",
        "question_id": question_id,
        "operation_ast": _json_safe(ast),
        "operands": _json_safe(operands),
        "answer_decimal": format(answer, "f"),
        "replay_answer_decimal": format(replayed, "f") if replayed is not None else None,
        "replay_status": replay_status,
        "execution_status": replay_status,
        "semantic_completeness": "COMPLETE" if complete else "PARTIAL",
        "operand_completeness": "COMPLETE" if complete else "INCOMPLETE",
        "verification_status": verification_status,
        "route_family": route or "unknown",
        "route_priority": row.get("route_priority") or 0.0,
        "candidate_score": row.get("retrieval_score") or row.get("filter_score") or 0.0,
        "filter_passed": bool(complete),
        "survived_filter": bool(complete),
        "candidate_status": "SURVIVED_FILTER" if complete else "REJECTED",
        "source_coordinates": _json_safe(coordinates),
        "source_hash": canonical_sha256(sorted(source_hashes)) if source_hashes else None,
        "source_hashes": sorted(set(source_hashes)),
        "source_closure_status": "PASS" if closure_pass else "FAIL",
        "source_run_label": source_label,
        "structural_complete": complete,
        "selection_evidence": _json_safe(evidence),
        "rejection_reason_codes": list(unique_reasons),
    }
    if is_baseline:
        plan["is_baseline"] = True
    return HydratedProposal(
        source_label=source_label,
        row=dict(row),
        plan=plan,
        reasons=unique_reasons,
        replay_status=replay_status,
        source_closure_status="PASS" if closure_pass else "FAIL",
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise SourceBoundPlanUnionError(
                    f"invalid JSONL at {path}:{line_number}"
                ) from error
            if not isinstance(value, dict):
                raise SourceBoundPlanUnionError(
                    f"JSONL row is not an object at {path}:{line_number}"
                )
            rows.append(value)
    return rows


def load_review_items(path: Path) -> dict[int, dict[str, Any]]:
    rows = load_jsonl(path)
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = _int_or_none(row.get("id"))
        if question_id is None or question_id in result:
            raise SourceBoundPlanUnionError("review items must have unique integer id")
        result[question_id] = row
    return result


def load_tables_for_uids(path: Path, uids: set[str]) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    tables: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                continue
            uid = _text(row.get("internal_table_uid"))
            if uid in uids:
                tables[uid] = dict(row)
    return tables


def _question_id_from_row(row: Mapping[str, Any]) -> int | None:
    value = row.get("question_id", row.get("id"))
    return _int_or_none(value)


def collect_ledger_rows(
    ledger_paths: Sequence[Path],
) -> tuple[dict[int, list[tuple[str, dict[str, Any]]]], dict[str, Any]]:
    if not ledger_paths:
        raise SourceBoundPlanUnionError("at least one candidate ledger is required")
    if len(ledger_paths) > MAX_LEDGER_SOURCES:
        raise SourceBoundPlanUnionError("too many candidate ledger sources")
    by_question: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    stats: Counter[str] = Counter()
    seen_source_labels: set[str] = set()
    for path in ledger_paths:
        label = path.parent.name or path.stem
        if label in seen_source_labels:
            label = f"{label}:{canonical_sha256(str(path))[:8]}"
        seen_source_labels.add(label)
        rows = load_jsonl(path)
        stats["ledger_sources"] += 1
        stats["ledger_rows"] += len(rows)
        for row in rows:
            question_id = _question_id_from_row(row)
            if question_id is None:
                stats["rows_missing_question_id"] += 1
                continue
            by_question.setdefault(question_id, []).append((label, row))
    return by_question, dict(stats)


def _deduplicate_hydrated(
    candidates: Iterable[HydratedProposal],
) -> list[HydratedProposal]:
    unique: list[HydratedProposal] = []
    seen: set[str] = set()
    for candidate in candidates:
        signature = _plan_signature(candidate.plan)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(candidate)
    return unique


def apply_metric_row_binding_gate(
    candidate: HydratedProposal,
    *,
    question_plan: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[HydratedProposal, bool]:
    """Reject only direct-lookup plans with a proven metric-row conflict.

    This is an opt-in research gate.  It is family-level and applies the same
    contract to every direct lookup; ``question_id`` is never consulted.  A
    ``REVIEW`` or ``UNKNOWN`` result remains eligible for the existing
    candidate selector so this experiment does not silently turn uncertainty
    into a hard answer rule.
    """

    audit = audit_selected_plan(question_plan, candidate.plan, tables_by_uid)
    if audit.get("family") != "direct_lookup" or audit.get("status") != "FAIL":
        return candidate, False
    plan = deepcopy(dict(candidate.plan))
    plan["metric_row_binding_gate_status"] = "FAIL"
    plan["metric_row_binding_gate_reasons"] = list(audit.get("reason_codes") or [])
    plan["filter_passed"] = False
    plan["survived_filter"] = False
    plan["candidate_status"] = "REJECTED"
    reasons = tuple(
        dict.fromkeys(
            [
                *candidate.reasons,
                METRIC_ROW_BINDING_GATE_REASON,
                *list(audit.get("reason_codes") or []),
            ]
        )
    )
    return (
        HydratedProposal(
            source_label=candidate.source_label,
            row=candidate.row,
            plan=plan,
            reasons=reasons,
            replay_status=candidate.replay_status,
            source_closure_status=candidate.source_closure_status,
        ),
        True,
    )


def select_population_union(
    *,
    question_ids: Sequence[int],
    review_items: Mapping[int, Mapping[str, Any]],
    ledger_rows: Mapping[int, Sequence[tuple[str, Mapping[str, Any]]]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    control_rows: Mapping[int, Mapping[str, Any]],
    max_plans: int = MAX_PLANS_PER_QUESTION,
    metric_row_binding_gate: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select complete plans for every population record.

    The selector receives the same whole-question contract for every plan in
    a question.  It therefore cannot prefer a route simply because it appears
    earlier in a route-priority chain.
    """

    if max_plans < 1 or max_plans > MAX_PLANS_PER_QUESTION:
        raise ValueError("max_plans is outside the supported bound")
    selected_rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for question_id in question_ids:
        review = review_items.get(question_id)
        if not isinstance(review, Mapping):
            raise SourceBoundPlanUnionError(
                f"review population is missing question {question_id}"
            )
        question_plan = review.get("question_plan")
        if not isinstance(question_plan, Mapping):
            raise SourceBoundPlanUnionError(
                f"question {question_id} is missing question_plan"
            )
        control_row = control_rows.get(question_id)
        if not isinstance(control_row, Mapping):
            raise SourceBoundPlanUnionError(
                f"control population is missing question {question_id}"
            )
        control = hydrate_proposal(
            control_row,
            source_label="CONTROL",
            question_plan=question_plan,
            tables_by_uid=tables_by_uid,
            is_baseline=True,
        )
        if control is None:
            raise SourceBoundPlanUnionError(
                f"control proposal cannot be hydrated for question {question_id}"
            )
        hydrated: list[HydratedProposal] = []
        for source_label, row in ledger_rows.get(question_id, ()):
            candidate = hydrate_proposal(
                row,
                source_label=source_label,
                question_plan=question_plan,
                tables_by_uid=tables_by_uid,
            )
            if candidate is not None:
                if metric_row_binding_gate:
                    candidate, gate_rejected = apply_metric_row_binding_gate(
                        candidate,
                        question_plan=question_plan,
                        tables_by_uid=tables_by_uid,
                    )
                    if gate_rejected:
                        stats["candidate_rows_metric_row_binding_gate_rejected"] += 1
                hydrated.append(candidate)
                stats["candidate_rows_hydrated"] += 1
                stats[
                    "candidate_rows_structurally_complete"
                    if candidate.plan.get("structural_complete")
                    else "candidate_rows_structurally_rejected"
                ] += 1
                stats[f"candidate_route_{_token(row.get('answer_route') or row.get('tier'))}"] += 1
        unique = _deduplicate_hydrated(hydrated)
        stats["unique_plans_before_cap"] += len(unique)
        unique.sort(
            key=lambda candidate: (
                0 if candidate.plan.get("structural_complete") else 1,
                -float(candidate.plan.get("candidate_score") or 0.0),
                -float(candidate.plan.get("route_priority") or 0.0),
                str(candidate.plan.get("plan_id") or ""),
            )
        )
        retained = unique[:max_plans]
        stats["truncated_plans"] += max(0, len(unique) - len(retained))
        plan_set = CandidatePlanSet(
            question_id=question_id,
            question_contract=_question_contract(question_plan),
            plans=[candidate.plan for candidate in retained],
            baseline_plan=control.plan,
            max_plans=max_plans,
        )
        decision = WholeQuestionAnswerLevelSelector(
            max_candidates=max_plans,
        ).select(plan_set)
        selected_id = _text(decision.get("selected_plan_id")) or None
        selected_candidate = next(
            (
                candidate
                for candidate in [*retained, control]
                if _text(candidate.plan.get("plan_id")) == selected_id
            ),
            None,
        )
        if selected_candidate is None:
            # Keep the control answer in the candidate artifact when the
            # fail-closed selector abstains.  This is not a promotion path.
            selected_candidate = control
            stats["selector_abstain_control_retained"] += 1
        else:
            stats["selector_selected"] += 1
            if selected_id == _text(control.plan.get("plan_id")):
                stats["selector_control_retained"] += 1
            else:
                stats["selector_candidate_selected"] += 1
        selected_plan = dict(selected_candidate.plan)
        selected_row = dict(selected_candidate.row)
        selected_row["question_id"] = question_id
        selected_row["selected_plan_id"] = selected_plan.get("plan_id")
        selected_row["selected_source_run"] = selected_candidate.source_label
        selected_row["selected_answer_decimal"] = selected_plan.get("answer_decimal")
        selected_row["selected_replay_answer_decimal"] = selected_plan.get(
            "replay_answer_decimal"
        )
        selected_row["selected_route_family"] = selected_plan.get("route_family")
        selected_row["selected_plan_complete"] = bool(
            selected_plan.get("structural_complete")
        )
        selected_row["selected_source_closure_status"] = selected_plan.get(
            "source_closure_status"
        )
        selected_row["selected_reason_codes"] = list(
            dict.fromkeys(
                [
                    *list(decision.get("reason_codes") or []),
                    *list(selected_candidate.reasons),
                ]
            )
        )
        selected_row["selector_decision"] = _json_safe(decision)
        selected_row["selected_plan"] = _json_safe(selected_plan)
        selected_rows.append(selected_row)
        stats["population_questions"] += 1
        if selected_candidate.source_label == "CONTROL":
            stats["selected_source_CONTROL"] += 1
        else:
            stats["selected_source_CANDIDATE"] += 1
    return selected_rows, dict(stats)


def answer_diff_counts(
    *,
    control_submission: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> dict[str, int | str]:
    control = {
        _int_or_none(row.get("id")): row
        for row in control_submission
        if _int_or_none(row.get("id")) is not None
    }
    candidate = {
        _int_or_none(row.get("question_id")): row
        for row in candidate_rows
        if _int_or_none(row.get("question_id")) is not None
    }
    if set(control) != set(candidate):
        raise SourceBoundPlanUnionError("control/candidate populations differ")
    changed = 0
    unchanged = 0
    for question_id in control:
        left = _decimal(control[question_id].get("answer"))
        right = _decimal(candidate[question_id].get("selected_answer_decimal"))
        # The competition JSON serializes answers as JSON numbers.  Compare
        # the rendered numeric representation as well as the high-precision
        # ledger text; otherwise a control value such as ``0.3333333333333333``
        # is falsely reported as changed when the candidate plan preserves it
        # as a longer Decimal before the final float serialization.
        if (
            left is not None
            and right is not None
            and float(left) == float(right)
        ):
            unchanged += 1
        else:
            changed += 1
    return {
        "population": len(control),
        "changed_answer": changed,
        "unchanged_answer": unchanged,
        "improved": "NOT_MEASURED",
        "regressed": "NOT_MEASURED",
        "unresolved": "NOT_MEASURED",
    }


__all__ = [
    "HYPOTHESIS",
    "MAX_LEDGER_SOURCES",
    "MAX_PLANS_PER_QUESTION",
    "METRIC_ROW_BINDING_GATE_REASON",
    "REPLAY_TOLERANCE",
    "SOURCE_BOUND_PLAN_UNION_PROTOCOL",
    "SourceBoundPlanUnionError",
    "HydratedProposal",
    "answer_diff_counts",
    "apply_metric_row_binding_gate",
    "canonical_sha256",
    "collect_ledger_rows",
    "hydrate_proposal",
    "load_jsonl",
    "load_review_items",
    "load_tables_for_uids",
    "select_population_union",
    "sha256_file",
]
