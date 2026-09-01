"""Value-free, population-level semantic binding feedback.

This module audits the source-bound plan selected by a research candidate.  It
does not inspect a gold answer and it never emits answer values.  The useful
signal here is a reusable contract check: for a direct lookup, the row bound
by the plan must contain the requested financial metric, with domain
abbreviations and qualifiers handled explicitly.  Source coordinates and
hashes remain locators only.

The audit is intentionally a feedback lane.  A ``FAIL`` or ``REVIEW`` status
identifies a plan that needs semantic validation; it is not a claim that the
answer is wrong.  ``question_id`` is accepted only as a population join and
tracking field, never as a rule or an exception.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Any


SEMANTIC_BINDING_FEEDBACK_PROTOCOL = "vifinqa_semantic_binding_feedback_v1"
SEMANTIC_BINDING_FEEDBACK_SCHEMA_VERSION = 1

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ENTITY_SUFFIX_PATTERNS = (
    re.compile(r"\bngan hang tmcp\b.*$"),
    re.compile(r"\bngan hang thuong mai co phan\b.*$"),
    re.compile(r"\bcong ty co phan\b.*$"),
    re.compile(r"\bcong ty tnhh\b.*$"),
    re.compile(r"\bctcp\b.*$"),
    re.compile(r"\bcong ty\b.*$"),
    re.compile(r"\btap doan\b.*$"),
)

# These are grammatical or corporate tokens, not the metric itself.  The
# list is deliberately conservative: finance direction words such as "vay",
# "phai", "tra", "lai", "chi", and "phi" remain meaningful.
_GENERIC_TOKENS = frozenset(
    {
        "a",
        "c",
        "cac",
        "cho",
        "co",
        "cong",
        "cua",
        "den",
        "dong",
        "hang",
        "la",
        "ngan",
        "ngay",
        "nam",
        "phan",
        "quy",
        "so",
        "tai",
        "tap",
        "thang",
        "tmcp",
        "trong",
        "tu",
        "ty",
        "va",
        "voi",
        "vnd",
        "du",
        "ky",
    }
)

# Canonical domain abbreviations.  These substitutions are shared semantic
# vocabulary, not question-specific rules.
_PHRASE_REPLACEMENTS = (
    ("thue thu nhap doanh nghiep", "tndn"),
    ("bao cao tai chinh", "bctc"),
)

# Qualifiers that distinguish otherwise similar financial rows.  A plan that
# asks for one qualifier but binds a row carrying another is a semantic review
# blocker even when generic words such as "khoan" or "vay" overlap.
_QUALIFIER_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("state_bank", frozenset({"nha", "nuoc", "nhnn"})),
    ("customer", frozenset({"khach", "hang"})),
    ("related_party", frozenset({"lien", "quan"})),
    ("foreign_currency", frozenset({"ngoai", "te"})),
    ("domestic_currency", frozenset({"vnd", "viet", "nam"})),
)

_UNIT_MULTIPLIERS: tuple[tuple[tuple[str, ...], Decimal], ...] = (
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
_KNOWN_UNIT_DIVISORS = {
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


def _fold_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.casefold().replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("đ", "d")
    for phrase, replacement in _PHRASE_REPLACEMENTS:
        text = text.replace(phrase, replacement)
    return " ".join(text.split())


def _metric_text(value: object) -> str:
    text = _fold_text(value)
    for pattern in _ENTITY_SUFFIX_PATTERNS:
        text = pattern.sub("", text, count=1)
    # Dates and source-note numbering are context, not metric tokens.
    text = re.sub(r"\b\d+(?:[./-]\d+)*\b", " ", text)
    return " ".join(text.split())


def metric_tokens(value: object) -> frozenset[str]:
    """Return conservative, entity-free metric tokens for binding checks."""

    return frozenset(
        token
        for token in _TOKEN_RE.findall(_metric_text(value))
        if token not in _GENERIC_TOKENS
    )


def _row_tokens(value: object) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN_RE.findall(_fold_text(value))
        if token not in _GENERIC_TOKENS
    )


def _qualifier_conflicts(
    expected: frozenset[str], observed: frozenset[str]
) -> list[str]:
    conflicts: list[str] = []
    for name, tokens in _QUALIFIER_GROUPS:
        expected_hit = expected & tokens
        observed_hit = observed & tokens
        # The customer/state-bank pair is the important direction split in
        # loan schedules; other groups remain conservative review signals.
        if expected_hit and observed_hit and expected_hit != observed_hit:
            conflicts.append(name)
    if expected & {"nha", "nuoc", "nhnn"} and observed & {"khach"}:
        conflicts.append("state_bank_vs_customer")
    return list(dict.fromkeys(conflicts))


def audit_metric_row_binding(
    metric: object,
    row_label: object,
) -> dict[str, Any]:
    """Audit one metric/row pair without reading numeric cell contents."""

    expected = metric_tokens(metric)
    observed = _row_tokens(row_label)
    if not expected or not observed:
        return {
            "status": "UNKNOWN",
            "reason_codes": ["METRIC_ROW_LABEL_MISSING"],
            "metric_token_count": len(expected),
            "overlap_token_count": 0,
            "coverage": None,
        }
    overlap = expected & observed
    coverage = len(overlap) / len(expected)
    conflicts = _qualifier_conflicts(expected, observed)
    if conflicts:
        status = "FAIL"
        reasons = ["METRIC_ROW_QUALIFIER_CONFLICT"]
    elif expected <= observed or coverage >= 0.75:
        status = "PASS"
        reasons = ["METRIC_ROW_BOUND"]
    elif coverage >= 0.5:
        status = "REVIEW"
        reasons = ["METRIC_ROW_PARTIAL_OVERLAP"]
    else:
        status = "FAIL"
        reasons = ["METRIC_ROW_LOW_OVERLAP"]
    return {
        "status": status,
        "reason_codes": reasons,
        "metric_token_count": len(expected),
        "overlap_token_count": len(overlap),
        "coverage": round(coverage, 6),
        "qualifier_conflicts": conflicts,
    }


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _unit_token(value: object) -> str:
    return "_".join(_fold_text(value).split())


def _table_unit_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    parts: list[str] = []
    for key in ("headers", "unit_hint", "column_labels"):
        value = table.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(_fold_text(item) for item in value)
        elif value:
            parts.append(_fold_text(value))
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        labels = trace.get("unit_labels")
        if isinstance(labels, (list, tuple)):
            parts.extend(_fold_text(item) for item in labels)
        elif labels:
            parts.append(_fold_text(labels))
    text = " ".join(parts)
    return _unit_multiplier_from_text(text)


def _unit_multiplier_from_text(text: str) -> Decimal | None:
    """Infer a unit only from whole-word markers, never substrings."""

    def has_marker(marker: str) -> bool:
        if marker == "ty":
            # ``tỷ`` is also the first token of ``tỷ lệ`` and ``công ty``.
            # Only treat it as a billion marker when it forms the compact
            # ``tỷ VND/đồng`` unit phrase.
            return bool(re.search(r"\bty\b\s*(?:[·,:;/-]\s*)?\b(?:vnd|dong)\b", text))
        return bool(re.search(rf"\b{re.escape(marker)}\b", text))

    for markers, multiplier in _UNIT_MULTIPLIERS:
        if all(has_marker(marker) for marker in markers):
            return multiplier
    if re.search(r"\bpercent\b|%", text):
        return Decimal("1")
    if re.search(r"\bvnd\b|\bdong\b", text):
        return Decimal("1")
    return None


def _source_for_operand(
    operand: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    source = operand.get("source")
    if isinstance(source, Mapping):
        merged = dict(evidence)
        merged.update(source)
        return merged
    return evidence


def audit_unit_binding(
    question_plan: Mapping[str, Any],
    selected_plan: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Check source-unit metadata and requested output-unit vocabulary."""

    requested = _unit_token(
        question_plan.get("requested_unit")
        or question_plan.get("output_unit")
        or question_plan.get("unit")
    )
    requested_known = requested in _KNOWN_UNIT_DIVISORS
    expected_operands = [
        item
        for item in question_plan.get("operands") or []
        if isinstance(item, Mapping)
    ]
    selected_operands = [
        item
        for item in selected_plan.get("operands") or []
        if isinstance(item, Mapping)
    ]
    evidence = [
        item
        for item in selected_plan.get("selection_evidence") or []
        if isinstance(item, Mapping)
    ]
    statuses: list[str] = []
    reasons: list[str] = []
    for index, _expected in enumerate(expected_operands):
        operand = selected_operands[index] if index < len(selected_operands) else {}
        source = _source_for_operand(
            operand,
            evidence[index] if index < len(evidence) else {},
        )
        uid = str(source.get("source_uid") or source.get("internal_table_uid") or source.get("table_uid") or "").strip()
        table = tables_by_uid.get(uid)
        table_multiplier = _table_unit_multiplier(table) if table else None
        evidence_multiplier = _decimal(
            source.get("source_to_vnd_multiplier")
            or source.get("source_multiplier")
            or source.get("unit_multiplier")
        )
        if not requested_known:
            statuses.append("UNKNOWN")
            reasons.append("REQUESTED_UNIT_UNKNOWN")
        elif table_multiplier is None and evidence_multiplier is None:
            statuses.append("UNKNOWN")
            reasons.append("SOURCE_UNIT_METADATA_MISSING")
        elif (
            table_multiplier is not None
            and evidence_multiplier is not None
            and table_multiplier != evidence_multiplier
        ):
            statuses.append("MISMATCH")
            reasons.append("SOURCE_UNIT_MULTIPLIER_MISMATCH")
        else:
            statuses.append("PASS")
            reasons.append("SOURCE_UNIT_BOUND")
    if not statuses:
        status = "UNKNOWN"
        reasons.append("UNIT_OPERANDS_MISSING")
    elif "MISMATCH" in statuses:
        status = "MISMATCH"
    elif "UNKNOWN" in statuses:
        status = "UNKNOWN"
    else:
        status = "PASS"
    return {
        "status": status,
        "operand_statuses": statuses,
        "reason_codes": list(dict.fromkeys(reasons)),
        "requested_unit_known": requested_known,
    }


def _source_locator(
    operand: Mapping[str, Any],
    evidence: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    source = _source_for_operand(operand, evidence)
    uid = str(
        source.get("source_uid")
        or source.get("internal_table_uid")
        or source.get("table_uid")
        or ""
    ).strip()
    row_index = source.get("row_index")
    column_index = source.get("column_index")
    table = tables_by_uid.get(uid)
    source_hash = ""
    if isinstance(table, Mapping):
        source_hash = str(
            table.get("table_sha256") or table.get("source_sha256") or ""
        ).strip()
    return {
        "source_uid": uid or None,
        "source_hash": source_hash or None,
        "row_index": row_index if isinstance(row_index, int) else None,
        "column_index": column_index if isinstance(column_index, int) else None,
    }


def _current_row_label(
    row: Sequence[object],
    evidence_label: object,
) -> tuple[object | None, bool]:
    """Find the source label in a structured row that may start with a code."""

    wanted = _fold_text(evidence_label)
    if wanted:
        for cell in row:
            if _fold_text(cell) == wanted:
                return cell, True
        wanted_tokens = set(_TOKEN_RE.findall(wanted))
        if wanted_tokens:
            for cell in row:
                cell_tokens = set(_TOKEN_RE.findall(_fold_text(cell)))
                if cell_tokens and (
                    wanted_tokens <= cell_tokens
                    or cell_tokens <= wanted_tokens
                ):
                    return cell, True
            # Some legacy evidence labels concatenate the visible row label
            # and a note/reference cell.  Treat the label as found when its
            # tokens are closed by the complete row, then use the first
            # textual cell as the metric-bearing label.
            row_tokens = {
                token
                for cell in row
                for token in _TOKEN_RE.findall(_fold_text(cell))
            }
            if wanted_tokens <= row_tokens:
                for cell in row:
                    cell_text = _fold_text(cell)
                    if cell_text and not re.fullmatch(
                        r"[-+]?\d+(?:[.,]\d+)*", cell_text
                    ):
                        return cell, True
    # Structured financial tables often have ``Mã số`` or an ordinal in the
    # first cell.  Pick the first non-numeric text as a diagnostic fallback;
    # the boolean tells the caller that the cited label was not found.
    for cell in row:
        text = _fold_text(cell)
        if text and not re.fullmatch(r"[-+]?\d+(?:[.,]\d+)*", text):
            return cell, False
    return None, False


def audit_selected_plan(
    question_plan: Mapping[str, Any],
    selected_plan: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Create the value-free semantic/unit audit for one selected plan."""

    family = str(question_plan.get("family") or "UNKNOWN")
    ast = question_plan.get("operation_ast")
    operation = ast.get("op") if isinstance(ast, Mapping) else None
    expected_operands = [
        item
        for item in question_plan.get("operands") or []
        if isinstance(item, Mapping)
    ]
    selected_operands = [
        item
        for item in selected_plan.get("operands") or []
        if isinstance(item, Mapping)
    ]
    evidence = [
        item
        for item in selected_plan.get("selection_evidence") or []
        if isinstance(item, Mapping)
    ]
    operand_audits: list[dict[str, Any]] = []
    locators: list[dict[str, Any]] = []
    for index, expected in enumerate(expected_operands):
        operand = selected_operands[index] if index < len(selected_operands) else {}
        source = _source_for_operand(
            operand,
            evidence[index] if index < len(evidence) else {},
        )
        uid = str(
            source.get("source_uid")
            or source.get("internal_table_uid")
            or source.get("table_uid")
            or ""
        ).strip()
        table = tables_by_uid.get(uid)
        row_label = source.get("row_label")
        row_index = source.get("row_index")
        if isinstance(table, Mapping) and isinstance(row_index, int):
            rows = table.get("rows")
            if isinstance(rows, list) and 0 <= row_index < len(rows):
                row = rows[row_index]
                if isinstance(row, list) and row:
                    current_label, label_found = _current_row_label(row, row_label)
                    if row_label and not label_found:
                        row_audit = {
                            "status": "FAIL",
                            "reason_codes": ["SOURCE_ROW_LABEL_STALE"],
                            "metric_token_count": 0,
                            "overlap_token_count": 0,
                            "coverage": None,
                        }
                    else:
                        row_label = current_label
                        row_audit = audit_metric_row_binding(
                            expected.get("metric"), row_label
                        )
                else:
                    row_audit = audit_metric_row_binding(
                        expected.get("metric"), row_label
                    )
            else:
                row_audit = {
                    "status": "UNKNOWN",
                    "reason_codes": ["SOURCE_ROW_NOT_HYDRATED"],
                    "metric_token_count": 0,
                    "overlap_token_count": 0,
                    "coverage": None,
                }
        else:
            row_audit = audit_metric_row_binding(expected.get("metric"), row_label)
        operand_audits.append(
            {
                "operand_index": index,
                "status": row_audit["status"],
                "reason_codes": list(row_audit.get("reason_codes") or []),
                "metric_token_count": row_audit.get("metric_token_count"),
                "overlap_token_count": row_audit.get("overlap_token_count"),
                "coverage": row_audit.get("coverage"),
                "qualifier_conflicts": list(row_audit.get("qualifier_conflicts") or []),
            }
        )
        locators.append(_source_locator(operand, source, tables_by_uid))

    if family == "direct_lookup" and operation == "lookup":
        statuses = [item["status"] for item in operand_audits]
        if not statuses or "FAIL" in statuses:
            status = "FAIL" if statuses else "UNKNOWN"
        elif "REVIEW" in statuses:
            status = "REVIEW"
        elif "UNKNOWN" in statuses:
            status = "UNKNOWN"
        else:
            status = "PASS"
    else:
        status = "NOT_APPLICABLE"
    reason_codes = [
        reason
        for item in operand_audits
        for reason in item.get("reason_codes") or []
    ]
    return {
        "family": family,
        "status": status,
        "operand_count": len(operand_audits),
        "operand_status_counts": dict(Counter(item["status"] for item in operand_audits)),
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "operands": operand_audits,
        "locators": locators,
        "unit": audit_unit_binding(question_plan, selected_plan, tables_by_uid),
    }


def build_feedback_record(
    *,
    question_id: int,
    review_item: Mapping[str, Any],
    selected_row: Mapping[str, Any],
    control_row: Mapping[str, Any],
    output_changed: bool,
    sign_transition: bool,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one value-free feedback record for a population question."""

    question_plan = review_item.get("question_plan")
    if not isinstance(question_plan, Mapping):
        question_plan = {}
    selected_plan = selected_row.get("selected_plan")
    if not isinstance(selected_plan, Mapping):
        selected_plan = {}
    semantic = audit_selected_plan(question_plan, selected_plan, tables_by_uid)
    unit = semantic["unit"]
    reason_codes = list(
        dict.fromkeys(
            [
                *semantic.get("reason_codes", []),
                *unit.get("reason_codes", []),
            ]
        )
    )
    source_class = (
        "CONTROL"
        if str(selected_row.get("selected_source_run") or "") == "CONTROL"
        else "CANDIDATE"
    )
    return {
        "protocol": SEMANTIC_BINDING_FEEDBACK_PROTOCOL,
        "schema_version": SEMANTIC_BINDING_FEEDBACK_SCHEMA_VERSION,
        "question_id": question_id,
        "family": str(question_plan.get("family") or "UNKNOWN"),
        "output_changed": bool(output_changed),
        "output_sign_transition": bool(sign_transition),
        "selected_source_class": source_class,
        "selected_source_run": str(selected_row.get("selected_source_run") or "UNKNOWN"),
        "selected_route_family": str(
            selected_row.get("selected_route_family")
            or selected_row.get("answer_route")
            or "UNKNOWN"
        ),
        "control_route_family": str(control_row.get("answer_route") or "UNKNOWN"),
        "plan_complete": bool(selected_row.get("selected_plan_complete")),
        "replay_status": str(selected_row.get("selected_plan", {}).get("replay_status") or "UNKNOWN")
        if isinstance(selected_row.get("selected_plan"), Mapping)
        else "UNKNOWN",
        "source_closure_status": str(
            selected_row.get("selected_source_closure_status") or "UNKNOWN"
        ),
        "semantic_status": semantic.get("status"),
        "semantic_operand_count": semantic.get("operand_count"),
        "semantic_operand_status_counts": semantic.get("operand_status_counts"),
        "unit_status": unit.get("status"),
        "unit_operand_statuses": unit.get("operand_statuses"),
        "reason_codes": reason_codes,
        "locators": semantic.get("locators"),
        "policy": {
            "value_free_output": True,
            "gold_consumed": False,
            "model_output_consumed": False,
            "research_answer_consumed": False,
            "question_id_exception": False,
            "accuracy_measured": False,
            "authority": "CANDIDATE_ONLY",
        },
    }


__all__ = [
    "SEMANTIC_BINDING_FEEDBACK_PROTOCOL",
    "SEMANTIC_BINDING_FEEDBACK_SCHEMA_VERSION",
    "audit_metric_row_binding",
    "audit_selected_plan",
    "audit_unit_binding",
    "build_feedback_record",
    "metric_tokens",
]
