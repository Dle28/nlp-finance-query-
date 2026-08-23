"""Fail-closed semantic bindings for numeric financial operands.

``table_routing_catalog_v1`` deliberately identifies only candidate tables.
This module is the later authority boundary: an :class:`EvidenceBinding`
binds one immutable numeric source cell to the variable, period, unit,
entity/scope and revision semantics that an operand needs. Raw numeric-token
parsing happens earlier in ``exact_cell_bindings``; it is intentionally not a
semantic-binding operation here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


EVIDENCE_BINDING_SCHEMA_VERSION = 2
EVIDENCE_BINDING_PROTOCOL = "vifinqa_evidence_binding_v2"

PASS = "PASS"
NOT_APPLICABLE = "NOT_APPLICABLE"
UNRESOLVED = "UNRESOLVED"
CONFLICT = "CONFLICT"
FAIL = "FAIL"
# Compatibility import only. ``INVALID`` is not a permitted field state.
INVALID = FAIL
FIELD_STATES = frozenset({PASS, NOT_APPLICABLE, UNRESOLVED, CONFLICT, FAIL})

PERIOD_GRAINS = frozenset(
    {"instant", "quarter", "half_year", "nine_month", "fiscal_year", "custom_duration"}
)
FLOW_OR_STOCK = frozenset({"flow", "stock"})
COMPARATIVE_BASES = frozenset(
    {"current", "prior_period", "prior_year", "opening_balance", "closing_balance"}
)
SEMANTIC_UNITS = frozenset({"monetary", "percent", "ratio", "count", "shares", "eps"})
UNIT_RESOLUTION_LEVELS = frozenset({"cell", "row", "column", "table", "document"})
AUDIT_STATUSES = frozenset({"audited", "reviewed", "unaudited"})
REVISION_STATUSES = frozenset({"original", "amended", "restated", "superseded"})
REVISION_POLICIES = frozenset({"latest_valid", "point_in_time", "originally_reported", "restated"})
BINDING_FIELDS = (
    "variable",
    "period",
    "unit",
    "entity",
    "entity_role",
    "scope",
    "revision",
    "source_integrity",
)
_STATUS_KEY_BY_FIELD = {field: f"{field}_status" for field in BINDING_FIELDS}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNIT_SPECIFICITY = {"cell": 5, "row": 4, "column": 3, "table": 2, "document": 1}
_AUDIT_AUTHORITY = {"audited": 3, "reviewed": 2, "unaudited": 1}
_COMPATIBILITY_CONSTRAINTS = {
    "period_grains": ("period_binding", "period_grain"),
    "flow_or_stock": ("period_binding", "flow_or_stock"),
    "comparative_bases": ("period_binding", "comparative_basis"),
    "semantic_units": ("unit_binding", "semantic_unit"),
    "revision_policies": ("revision_binding", "revision_policy"),
}
_COMPATIBILITY_CROSS_FIELDS = {
    "entity": ("entity_scope_binding", "entity"),
    "entity_role": ("entity_role_binding", "role"),
    "scope": ("entity_scope_binding", "scope"),
    "period_years": ("period_binding", "period_years"),
    "period_grain": ("period_binding", "period_grain"),
    "flow_or_stock": ("period_binding", "flow_or_stock"),
    "comparative_basis": ("period_binding", "comparative_basis"),
    "normalized_currency": ("unit_binding", "normalized_currency"),
    "semantic_unit": ("unit_binding", "semantic_unit"),
    "revision_policy": ("revision_binding", "revision_policy"),
}


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_date(value: object) -> bool:
    try:
        date.fromisoformat(_text(value))
    except ValueError:
        return False
    return True


def _valid_sha256(value: object) -> bool:
    return bool(_SHA256_RE.fullmatch(_text(value)))


def _anchor_errors(
    anchor: object,
    *,
    document_uid: str,
    label: str,
    require_table_coordinate: bool = False,
) -> list[str]:
    if not isinstance(anchor, Mapping):
        return [f"{label}_NOT_OBJECT"]
    errors: list[str] = []
    if _text(anchor.get("document_uid")) != document_uid:
        errors.append(f"{label}_DOCUMENT_UID_MISMATCH")
    if not _valid_sha256(anchor.get("raw_text_sha256")):
        errors.append(f"{label}_RAW_TEXT_SHA256_INVALID")
    kind = _text(anchor.get("kind"))
    if kind not in {"raw_cell", "document_metadata", "document_text_line"}:
        errors.append(f"{label}_KIND_INVALID")
    if kind == "document_text_line":
        if not _is_int(anchor.get("line_number")) or int(anchor["line_number"]) < 1:
            errors.append(f"{label}_LINE_NUMBER_INVALID")
        if not _valid_sha256(anchor.get("source_file_sha256")):
            errors.append(f"{label}_SOURCE_FILE_SHA256_INVALID")
    if kind == "raw_cell" or require_table_coordinate:
        if not _text(anchor.get("internal_table_uid")):
            errors.append(f"{label}_TABLE_UID_MISSING")
        for field in ("row_index", "column_index"):
            if not _is_int(anchor.get(field)) or int(anchor[field]) < 0:
                errors.append(f"{label}_{field.upper()}_INVALID")
    return errors


def _anchors_errors(anchors: object, *, document_uid: str, label: str) -> list[str]:
    if not isinstance(anchors, Sequence) or isinstance(anchors, (str, bytes)) or not anchors:
        return [f"{label}_SOURCE_ANCHORS_MISSING"]
    errors: list[str] = []
    for index, anchor in enumerate(anchors):
        errors.extend(_anchor_errors(anchor, document_uid=document_uid, label=f"{label}_ANCHOR_{index}"))
    return errors


def _field_status(value: object, *, label: str, errors: list[str]) -> str:
    status = _text(value)
    if status not in FIELD_STATES:
        errors.append(f"{label}_STATUS_INVALID")
        return FAIL
    return status


def _period_errors(binding: Mapping[str, Any], *, document_uid: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    status = _field_status(binding.get("status"), label="PERIOD", errors=errors)
    if status != PASS:
        return status, errors
    if not _text(binding.get("raw_period_label")):
        errors.append("PERIOD_RAW_LABEL_MISSING")
    errors.extend(_anchors_errors(binding.get("source_anchors"), document_uid=document_uid, label="PERIOD"))
    grain = _text(binding.get("period_grain"))
    if grain not in PERIOD_GRAINS:
        errors.append("PERIOD_GRAIN_INVALID")
    if _text(binding.get("flow_or_stock")) not in FLOW_OR_STOCK:
        errors.append("PERIOD_FLOW_OR_STOCK_INVALID")
    if _text(binding.get("comparative_basis")) not in COMPARATIVE_BASES:
        errors.append("PERIOD_COMPARATIVE_BASIS_INVALID")
    years = binding.get("period_years")
    if (
        not isinstance(years, list)
        or not years
        or any(not _is_int(value) or value < 1900 or value > 2200 for value in years)
        or len(set(years)) != len(years)
    ):
        errors.append("PERIOD_YEARS_INVALID")
    if grain == "instant":
        if not _valid_date(binding.get("as_of_date")):
            errors.append("PERIOD_AS_OF_DATE_REQUIRED")
        if binding.get("start_date") not in (None, "") or binding.get("end_date") not in (None, ""):
            errors.append("PERIOD_INSTANT_MAY_NOT_DECLARE_DURATION")
    elif grain in PERIOD_GRAINS:
        if not _valid_date(binding.get("start_date")) or not _valid_date(binding.get("end_date")):
            errors.append("PERIOD_DURATION_DATES_REQUIRED")
        elif _text(binding.get("start_date")) > _text(binding.get("end_date")):
            errors.append("PERIOD_DURATION_ORDER_INVALID")
        if binding.get("as_of_date") not in (None, ""):
            errors.append("PERIOD_DURATION_MAY_NOT_DECLARE_AS_OF_DATE")
    if grain in {"quarter", "half_year", "nine_month", "fiscal_year"} and not _is_int(binding.get("fiscal_year")):
        errors.append("PERIOD_FISCAL_YEAR_REQUIRED")
    if grain == "quarter" and binding.get("fiscal_quarter") not in {1, 2, 3, 4}:
        errors.append("PERIOD_FISCAL_QUARTER_REQUIRED")
    return (PASS if not errors else FAIL), errors


def _unit_errors(binding: Mapping[str, Any], *, document_uid: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    status = _field_status(binding.get("status"), label="UNIT", errors=errors)
    if status != PASS:
        return status, errors
    if not _text(binding.get("raw_unit_label")):
        errors.append("UNIT_RAW_LABEL_MISSING")
    errors.extend(_anchors_errors(binding.get("source_anchors"), document_uid=document_uid, label="UNIT"))
    if _text(binding.get("semantic_unit")) not in SEMANTIC_UNITS:
        errors.append("UNIT_SEMANTIC_UNIT_INVALID")
    if _text(binding.get("resolution_level")) not in UNIT_RESOLUTION_LEVELS:
        errors.append("UNIT_RESOLUTION_LEVEL_INVALID")
    try:
        if Decimal(_text(binding.get("scale"))) <= 0:
            errors.append("UNIT_SCALE_INVALID")
    except (InvalidOperation, ValueError):
        errors.append("UNIT_SCALE_INVALID")
    semantic_unit = _text(binding.get("semantic_unit"))
    currency = _text(binding.get("normalized_currency"))
    if semantic_unit == "monetary" and not currency:
        errors.append("UNIT_CURRENCY_REQUIRED")
    if semantic_unit != "monetary" and currency:
        errors.append("UNIT_CURRENCY_NOT_ALLOWED")
    return (PASS if not errors else FAIL), errors


def resolve_unit_precedence(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve only explicit, source-anchored unit declarations."""
    explicit: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("explicit") is not True:
            continue
        level = _text(candidate.get("resolution_level"))
        if level in _UNIT_SPECIFICITY:
            explicit.append(dict(candidate))
    if not explicit:
        return {"status": UNRESOLVED, "reason_codes": ["UNIT_NO_EXPLICIT_SOURCE_DECLARATION"], "selected": None}
    specificity = max(_UNIT_SPECIFICITY[_text(value["resolution_level"])] for value in explicit)
    contenders = [value for value in explicit if _UNIT_SPECIFICITY[_text(value["resolution_level"])] == specificity]
    signatures = {
        (_text(value.get("semantic_unit")), _text(value.get("normalized_currency")), _text(value.get("scale")))
        for value in contenders
    }
    if len(signatures) != 1:
        return {"status": CONFLICT, "reason_codes": ["UNIT_EXPLICIT_DECLARATIONS_CONFLICT"], "selected": None}
    return {
        "status": PASS,
        "reason_codes": [],
        "selected": sorted(
            contenders,
            key=lambda value: (_text(value.get("raw_unit_label")), json.dumps(value.get("source_anchors") or [], ensure_ascii=False, sort_keys=True)),
        )[0],
    }


def _revision_candidate_errors(revision: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if _text(revision.get("status")) != PASS:
        errors.append("REVISION_CANDIDATE_STATUS_NOT_PASS")
    if _text(revision.get("audit_status")) not in _AUDIT_AUTHORITY:
        errors.append("REVISION_CANDIDATE_AUDIT_STATUS_INVALID")
    if _text(revision.get("revision_status")) not in REVISION_STATUSES:
        errors.append("REVISION_CANDIDATE_STATUS_INVALID")
    if not _text(revision.get("source_document_uid")):
        errors.append("REVISION_CANDIDATE_DOCUMENT_UID_MISSING")
    if not _valid_date(revision.get("publication_date")):
        errors.append("REVISION_CANDIDATE_PUBLICATION_DATE_INVALID")
    if not _valid_date(revision.get("effective_date")):
        errors.append("REVISION_CANDIDATE_EFFECTIVE_DATE_INVALID")
    return errors


def _revision_winners(candidates: Sequence[Mapping[str, Any]], *, prefer_latest: bool) -> list[Mapping[str, Any]]:
    publication_date = (max if prefer_latest else min)(
        _text(item["revision_binding"]["publication_date"]) for item in candidates
    )
    date_contenders = [item for item in candidates if _text(item["revision_binding"]["publication_date"]) == publication_date]
    authority = max(_AUDIT_AUTHORITY[_text(item["revision_binding"]["audit_status"])] for item in date_contenders)
    return [
        item
        for item in date_contenders
        if _AUDIT_AUTHORITY[_text(item["revision_binding"]["audit_status"])] == authority
    ]


def resolve_revision_hierarchy(
    candidates: Sequence[Mapping[str, Any]],
    *,
    revision_policy: str = "latest_valid",
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Resolve versions under an explicit query/formula revision policy.

    ``latest_valid`` is not a global rule: callers select it, ``point_in_time``,
    ``originally_reported`` or ``restated``. Publication date, effective date,
    audit authority and declared supersession are deterministic tiebreak inputs;
    source order is never used.
    """
    policy = _text(revision_policy)
    if policy not in REVISION_POLICIES:
        return {"status": FAIL, "reason_codes": ["REVISION_POLICY_INVALID"], "selected_document_uid": None}
    if policy == "point_in_time" and not _valid_date(as_of_date):
        return {"status": FAIL, "reason_codes": ["REVISION_POINT_IN_TIME_DATE_INVALID"], "selected_document_uid": None}
    usable: list[dict[str, Any]] = []
    group_keys: set[tuple[str, str, str, str]] = set()
    invalid_candidates = False
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            invalid_candidates = True
            continue
        revision = dict(candidate.get("revision_binding") or {})
        if _revision_candidate_errors(revision):
            invalid_candidates = True
            continue
        group_key = (
            _text(candidate.get("entity")),
            _text(candidate.get("scope")),
            _text(candidate.get("period_key")),
            _text(candidate.get("statement_type")),
        )
        group_keys.add(group_key)
        usable.append({**dict(candidate), "revision_binding": revision})
    if not usable:
        return {"status": UNRESOLVED, "reason_codes": ["REVISION_NO_SOURCE_VERIFIED_CANDIDATE"], "selected_document_uid": None}
    if len(group_keys) != 1 or "" in next(iter(group_keys)):
        return {"status": FAIL, "reason_codes": ["REVISION_CANDIDATES_NOT_ONE_ECONOMIC_FACT"], "selected_document_uid": None}

    if policy == "point_in_time":
        usable = [
            item
            for item in usable
            if _text(item["revision_binding"]["publication_date"]) <= _text(as_of_date)
            and _text(item["revision_binding"]["effective_date"]) <= _text(as_of_date)
        ]
    elif policy == "originally_reported":
        usable = [item for item in usable if _text(item["revision_binding"]["revision_status"]) == "original"]
    elif policy == "restated":
        usable = [item for item in usable if _text(item["revision_binding"]["revision_status"]) == "restated"]
    if not usable:
        return {"status": UNRESOLVED, "reason_codes": [f"REVISION_NO_CANDIDATE_FOR_POLICY:{policy}"], "selected_document_uid": None}

    superseded_document_uids = {
        _text(item["revision_binding"].get("supersedes_document_uid"))
        for item in usable
        if _text(item["revision_binding"].get("supersedes_document_uid"))
    }
    if policy in {"latest_valid", "point_in_time"}:
        eligible = [
            item
            for item in usable
            if _text(item["revision_binding"]["revision_status"]) != "superseded"
            and _text(item["revision_binding"]["source_document_uid"]) not in superseded_document_uids
        ]
    else:
        eligible = usable
    if not eligible:
        return {"status": UNRESOLVED, "reason_codes": ["REVISION_ALL_CANDIDATES_SUPERSEDED"], "selected_document_uid": None}
    winners = _revision_winners(eligible, prefer_latest=policy != "originally_reported")
    document_uids = {_text(item["revision_binding"]["source_document_uid"]) for item in winners}
    if len(document_uids) != 1:
        return {"status": CONFLICT, "reason_codes": ["REVISION_PUBLICATION_AND_AUDIT_AUTHORITY_TIE"], "selected_document_uid": None}
    return {
        "status": PASS,
        "reason_codes": ["REVISION_INVALID_CANDIDATE_IGNORED"] if invalid_candidates else [],
        "selected_document_uid": next(iter(document_uids)),
        "revision_policy": policy,
        "as_of_date": as_of_date if policy == "point_in_time" else None,
        "candidate_document_uids": sorted(_text(item["revision_binding"]["source_document_uid"]) for item in eligible),
    }


def _variable_errors(binding: Mapping[str, Any], *, document_uid: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    status = _field_status(binding.get("status"), label="VARIABLE", errors=errors)
    if status != PASS:
        return status, errors
    for field in ("variable_id", "raw_row_label", "recognition_method"):
        if not _text(binding.get(field)):
            errors.append(f"VARIABLE_{field.upper()}_MISSING")
    errors.extend(_anchors_errors(binding.get("source_anchors"), document_uid=document_uid, label="VARIABLE"))
    return (PASS if not errors else FAIL), errors


def _entity_scope_errors(binding: Mapping[str, Any], *, document_uid: str) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    entity_status = _field_status(binding.get("entity_status"), label="ENTITY", errors=errors)
    scope_status = _field_status(binding.get("scope_status"), label="SCOPE", errors=errors)
    if entity_status == PASS and not _text(binding.get("entity")):
        errors.append("ENTITY_VALUE_MISSING")
    if scope_status == PASS and not _text(binding.get("scope")):
        errors.append("SCOPE_VALUE_MISSING")
    if entity_status == PASS or scope_status == PASS:
        errors.extend(_anchors_errors(binding.get("source_anchors"), document_uid=document_uid, label="ENTITY_SCOPE"))
    if errors:
        if entity_status == PASS:
            entity_status = FAIL
        if scope_status == PASS:
            scope_status = FAIL
    return entity_status, scope_status, errors


def _entity_role_errors(binding: Mapping[str, Any], *, document_uid: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    status = _field_status(binding.get("status"), label="ENTITY_ROLE", errors=errors)
    if status != PASS:
        return status, errors
    if _text(binding.get("role")) not in {"parent", "subsidiary"}:
        errors.append("ENTITY_ROLE_VALUE_INVALID")
    if not _text(binding.get("recognition_method")):
        errors.append("ENTITY_ROLE_RECOGNITION_METHOD_MISSING")
    errors.extend(_anchors_errors(binding.get("source_anchors"), document_uid=document_uid, label="ENTITY_ROLE"))
    return (PASS if not errors else FAIL), errors


def _revision_errors(binding: Mapping[str, Any], *, document_uid: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    status = _field_status(binding.get("status"), label="REVISION", errors=errors)
    if status != PASS:
        return status, errors
    if _text(binding.get("source_document_uid")) != document_uid:
        errors.append("REVISION_SOURCE_DOCUMENT_UID_MISMATCH")
    if _text(binding.get("revision_policy")) not in REVISION_POLICIES:
        errors.append("REVISION_POLICY_INVALID")
    if _text(binding.get("audit_status")) not in AUDIT_STATUSES:
        errors.append("REVISION_AUDIT_STATUS_INVALID")
    revision_status = _text(binding.get("revision_status"))
    if revision_status not in REVISION_STATUSES:
        errors.append("REVISION_STATUS_INVALID")
    if not _valid_date(binding.get("publication_date")):
        errors.append("REVISION_PUBLICATION_DATE_INVALID")
    if not _valid_date(binding.get("effective_date")):
        errors.append("REVISION_EFFECTIVE_DATE_INVALID")
    if not _text(binding.get("effective_version")):
        errors.append("REVISION_EFFECTIVE_VERSION_MISSING")
    if revision_status in {"amended", "restated", "superseded"} and not _text(binding.get("supersedes_document_uid")):
        errors.append("REVISION_SUPERSEDES_DOCUMENT_UID_REQUIRED")
    errors.extend(_anchors_errors(binding.get("source_anchors"), document_uid=document_uid, label="REVISION"))
    return (PASS if not errors else FAIL), errors


def _source_integrity_errors(binding: Mapping[str, Any], *, document_uid: str, table_uid: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    status = _field_status(binding.get("status"), label="SOURCE_INTEGRITY", errors=errors)
    if status != PASS:
        return status, errors
    value_cell = binding.get("value_cell")
    errors.extend(_anchor_errors(value_cell, document_uid=document_uid, label="SOURCE_INTEGRITY_VALUE_CELL", require_table_coordinate=True))
    if isinstance(value_cell, Mapping) and _text(value_cell.get("internal_table_uid")) != table_uid:
        errors.append("SOURCE_INTEGRITY_VALUE_CELL_TABLE_UID_MISMATCH")
    return (PASS if not errors else FAIL), errors


def _required_fields(value: object, *, errors: list[str]) -> set[str]:
    if not isinstance(value, Mapping):
        errors.append("BINDING_REQUIREMENTS_MISSING")
        return set(BINDING_FIELDS)
    fields = value.get("required_fields")
    if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields):
        errors.append("BINDING_REQUIRED_FIELDS_INVALID")
        return set(BINDING_FIELDS)
    normalized = {_text(field) for field in fields}
    if not normalized.issubset(BINDING_FIELDS) or len(normalized) != len(fields):
        errors.append("BINDING_REQUIRED_FIELDS_INVALID")
        return set(BINDING_FIELDS)
    return normalized


def _header_evidence_hash(period_binding: Mapping[str, Any], unit_binding: Mapping[str, Any]) -> str:
    return _stable_hash(
        {
            "period_source_anchors": period_binding.get("source_anchors") or [],
            "unit_source_anchors": unit_binding.get("source_anchors") or [],
        }
    )


def _lineage_errors(
    lineage: object,
    *,
    source_integrity: Mapping[str, Any],
    period_binding: Mapping[str, Any],
    unit_binding: Mapping[str, Any],
) -> list[str]:
    if not isinstance(lineage, Mapping):
        return ["BINDING_LINEAGE_MISSING"]
    errors: list[str] = []
    for field in ("document_sha256", "table_sha256", "raw_cell_sha256", "header_evidence_sha256"):
        if not _valid_sha256(lineage.get(field)):
            errors.append(f"BINDING_LINEAGE_{field.upper()}_INVALID")
    try:
        lineage_schema = int(lineage.get("binding_schema_version") or 0)
    except (TypeError, ValueError):
        lineage_schema = 0
    if lineage_schema != EVIDENCE_BINDING_SCHEMA_VERSION:
        errors.append("BINDING_LINEAGE_SCHEMA_VERSION_INVALID")
    if not _text(lineage.get("resolver_version")):
        errors.append("BINDING_LINEAGE_RESOLVER_VERSION_MISSING")
    raw_cell = _mapping(source_integrity.get("value_cell"))
    if _valid_sha256(lineage.get("raw_cell_sha256")) and _text(lineage.get("raw_cell_sha256")) != _text(raw_cell.get("raw_text_sha256")):
        errors.append("BINDING_LINEAGE_RAW_CELL_HASH_MISMATCH")
    if _valid_sha256(lineage.get("header_evidence_sha256")) and _text(lineage.get("header_evidence_sha256")) != _header_evidence_hash(period_binding, unit_binding):
        errors.append("BINDING_LINEAGE_HEADER_EVIDENCE_HASH_MISMATCH")
    return errors


def _binding_statuses(binding: Mapping[str, Any], *, document_uid: str, table_uid: str) -> tuple[dict[str, str], list[str]]:
    variable_status, variable_errors = _variable_errors(_mapping(binding.get("variable_binding")), document_uid=document_uid)
    period_status, period_errors = _period_errors(_mapping(binding.get("period_binding")), document_uid=document_uid)
    unit_status, unit_errors = _unit_errors(_mapping(binding.get("unit_binding")), document_uid=document_uid)
    entity_status, scope_status, entity_scope_errors = _entity_scope_errors(_mapping(binding.get("entity_scope_binding")), document_uid=document_uid)
    entity_role_status, entity_role_errors = _entity_role_errors(_mapping(binding.get("entity_role_binding")), document_uid=document_uid)
    revision_status, revision_errors = _revision_errors(_mapping(binding.get("revision_binding")), document_uid=document_uid)
    source_status, source_errors = _source_integrity_errors(_mapping(binding.get("source_integrity")), document_uid=document_uid, table_uid=table_uid)
    return (
        {
            "variable_status": variable_status,
            "period_status": period_status,
            "unit_status": unit_status,
            "entity_status": entity_status,
            "entity_role_status": entity_role_status,
            "scope_status": scope_status,
            "revision_status": revision_status,
            "source_integrity_status": source_status,
        },
        variable_errors + period_errors + unit_errors + entity_scope_errors + entity_role_errors + revision_errors + source_errors,
    )


def validate_evidence_binding(binding: Mapping[str, Any]) -> list[str]:
    """Validate immutable semantic binding, lineage and deployment boundary.

    ``NOT_APPLICABLE`` is valid only for a field declared non-required in the
    hash-bound ``requirements``. A valid Evidence Binding still has no
    permission to train, promote or serve a model; that is a separate gate.
    """
    if not isinstance(binding, Mapping):
        return ["BINDING_NOT_OBJECT"]
    errors: list[str] = []
    try:
        schema_version = int(binding.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != EVIDENCE_BINDING_SCHEMA_VERSION:
        errors.append("BINDING_SCHEMA_VERSION_INVALID")
    if _text(binding.get("protocol")) != EVIDENCE_BINDING_PROTOCOL:
        errors.append("BINDING_PROTOCOL_INVALID")
    for field in ("binding_id", "operand_id", "document_uid", "internal_table_uid"):
        if not _text(binding.get(field)):
            errors.append(f"BINDING_{field.upper()}_MISSING")
    document_uid, table_uid = _text(binding.get("document_uid")), _text(binding.get("internal_table_uid"))
    if not document_uid or not table_uid:
        return sorted(set(errors))
    statuses, _field_errors = _binding_statuses(binding, document_uid=document_uid, table_uid=table_uid)
    errors.extend(_lineage_errors(binding.get("binding_lineage"), source_integrity=_mapping(binding.get("source_integrity")), period_binding=_mapping(binding.get("period_binding")), unit_binding=_mapping(binding.get("unit_binding"))))
    required_fields = _required_fields(binding.get("requirements"), errors=errors)
    for field, status_key in _STATUS_KEY_BY_FIELD.items():
        status = statuses[status_key]
        if status == NOT_APPLICABLE and field in required_fields:
            errors.append(f"BINDING_NOT_APPLICABLE_FIELD_REQUIRED:{field}")
    observed = binding.get("field_statuses")
    if not isinstance(observed, Mapping):
        errors.append("BINDING_FIELD_STATUSES_MISSING")
    elif {key: _text(observed.get(key)) for key in statuses} != statuses:
        errors.append("BINDING_FIELD_STATUSES_DO_NOT_MATCH_FIELDS")
    eligible = not errors and all(status in {PASS, NOT_APPLICABLE} for status in statuses.values())
    if bool(binding.get("operand_eligible")) != eligible:
        errors.append("BINDING_OPERAND_ELIGIBILITY_INVALID")
    if _text(binding.get("binding_status")) != ("BOUND" if eligible else "BLOCKED"):
        errors.append("BINDING_STATUS_INVALID")
    if binding.get("training_eligible") is not False:
        errors.append("BINDING_MAY_NOT_AUTHORIZE_TRAINING")
    if binding.get("promotion_allowed") is not False:
        errors.append("BINDING_MAY_NOT_AUTHORIZE_PROMOTION")
    if _text(binding.get("binding_id")):
        unsigned = {key: value for key, value in binding.items() if key != "binding_id"}
        if _text(binding.get("binding_id")) != _stable_hash(unsigned):
            errors.append("BINDING_ID_DOES_NOT_MATCH_CONTENT")
    return sorted(set(errors))


def build_evidence_binding(
    *,
    operand_id: object,
    document_uid: object,
    internal_table_uid: object,
    source_integrity: Mapping[str, Any],
    variable_binding: Mapping[str, Any],
    period_binding: Mapping[str, Any],
    unit_binding: Mapping[str, Any],
    entity_scope_binding: Mapping[str, Any],
    entity_role_binding: Mapping[str, Any],
    revision_binding: Mapping[str, Any],
    binding_lineage: Mapping[str, Any],
    required_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Materialize a source-only Evidence Binding and derive all gate states."""
    document, table = _text(document_uid), _text(internal_table_uid)
    requirements = {"required_fields": list(BINDING_FIELDS if required_fields is None else required_fields)}
    payload: dict[str, Any] = {
        "schema_version": EVIDENCE_BINDING_SCHEMA_VERSION,
        "protocol": EVIDENCE_BINDING_PROTOCOL,
        "operand_id": _text(operand_id),
        "document_uid": document,
        "internal_table_uid": table,
        "variable_binding": dict(variable_binding),
        "period_binding": dict(period_binding),
        "unit_binding": dict(unit_binding),
        "entity_scope_binding": dict(entity_scope_binding),
        "entity_role_binding": dict(entity_role_binding),
        "revision_binding": dict(revision_binding),
        "source_integrity": dict(source_integrity),
        "binding_lineage": dict(binding_lineage),
        "requirements": requirements,
        "training_eligible": False,
        "promotion_allowed": False,
    }
    if document and table:
        statuses, field_errors = _binding_statuses(payload, document_uid=document, table_uid=table)
        field_errors.extend(_lineage_errors(payload["binding_lineage"], source_integrity=payload["source_integrity"], period_binding=payload["period_binding"], unit_binding=payload["unit_binding"]))
        required = _required_fields(requirements, errors=field_errors)
        for field, status_key in _STATUS_KEY_BY_FIELD.items():
            status = statuses[status_key]
            if status == NOT_APPLICABLE and field in required:
                field_errors.append(f"BINDING_NOT_APPLICABLE_FIELD_REQUIRED:{field}")
    else:
        statuses = {key: FAIL for key in _STATUS_KEY_BY_FIELD.values()}
        field_errors = ["BINDING_DOCUMENT_OR_TABLE_UID_MISSING"]
    payload["field_statuses"] = statuses
    payload["operand_eligible"] = not field_errors and all(status in {PASS, NOT_APPLICABLE} for status in statuses.values())
    payload["binding_status"] = "BOUND" if payload["operand_eligible"] else "BLOCKED"
    payload["reason_codes"] = sorted(set(field_errors))
    payload["binding_id"] = _stable_hash(payload)
    return payload


def _binding_value(binding: Mapping[str, Any], path: tuple[str, str]) -> str:
    return _text(_mapping(binding.get(path[0])).get(path[1]))


def validate_formula_compatibility(operand_bindings: Mapping[str, Mapping[str, Any]], contract: object) -> list[str]:
    """Validate only formula-declared relationships between bound operands.

    The contract names each operand's admissible semantics and only equality
    relations truly required by that formula. It supports flow plus
    opening/closing stock (for example ROA) without a global equality rule.
    """
    if not isinstance(contract, Mapping):
        return ["FORMULA_COMPATIBILITY_CONTRACT_MISSING"]
    if not _text(contract.get("formula_id")):
        return ["FORMULA_COMPATIBILITY_FORMULA_ID_MISSING"]
    constraints = contract.get("operand_constraints")
    if not isinstance(constraints, Mapping):
        return ["FORMULA_COMPATIBILITY_OPERAND_CONSTRAINTS_MISSING"]
    errors: list[str] = []
    binding_ids = set(operand_bindings)
    constraint_ids = {_text(operand_id) for operand_id in constraints}
    if binding_ids != constraint_ids:
        errors.append("FORMULA_COMPATIBILITY_OPERAND_SET_MISMATCH")
    for operand_id, binding in operand_bindings.items():
        operand_constraints = constraints.get(operand_id)
        if not isinstance(operand_constraints, Mapping):
            errors.append(f"FORMULA_COMPATIBILITY_CONSTRAINTS_MISSING:{operand_id}")
            continue
        for constraint_name, path in _COMPATIBILITY_CONSTRAINTS.items():
            allowed = operand_constraints.get(constraint_name)
            if not isinstance(allowed, list) or not allowed or any(not _text(item) for item in allowed):
                errors.append(f"FORMULA_COMPATIBILITY_CONSTRAINT_INVALID:{operand_id}:{constraint_name}")
                continue
            if _binding_value(binding, path) not in {_text(item) for item in allowed}:
                errors.append(f"FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION:{operand_id}:{constraint_name}")
        unknown = set(operand_constraints) - set(_COMPATIBILITY_CONSTRAINTS)
        errors.extend(f"FORMULA_COMPATIBILITY_CONSTRAINT_UNKNOWN:{operand_id}:{name}" for name in sorted(unknown))
    rules = contract.get("cross_operand_rules", [])
    if not isinstance(rules, list):
        errors.append("FORMULA_COMPATIBILITY_CROSS_OPERAND_RULES_INVALID")
        rules = []
    for index, rule in enumerate(rules):
        prefix = f"FORMULA_COMPATIBILITY_RULE:{index}"
        if not isinstance(rule, Mapping) or _text(rule.get("kind")) != "same":
            errors.append(f"{prefix}:INVALID")
            continue
        field, operands = _text(rule.get("field")), rule.get("operands")
        if field not in _COMPATIBILITY_CROSS_FIELDS or not isinstance(operands, list) or len(operands) < 2:
            errors.append(f"{prefix}:INVALID")
            continue
        operand_ids = [_text(value) for value in operands]
        if len(set(operand_ids)) != len(operand_ids) or any(value not in operand_bindings for value in operand_ids):
            errors.append(f"{prefix}:OPERANDS_INVALID")
            continue
        values = [_binding_value(operand_bindings[operand_id], _COMPATIBILITY_CROSS_FIELDS[field]) for operand_id in operand_ids]
        if not all(values) or len(set(values)) != 1:
            errors.append(f"{prefix}:SAME_FIELD_VIOLATION:{field}")
    return sorted(set(errors))
