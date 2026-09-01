"""Value-blind semantic-cell candidate reranking.

This module is an intentionally independent research lane.  It ranks *cell
coordinates* from textual and structural metadata; it does not inspect a
cell's numeric payload, an answer field, a model score, or any gold label.
Consequently it cannot authorize an answer or replace the canonical builder
and selector.  The result is suitable for navigation/review experiments and
for a later, independently audited binding stage.

The guards are family-level rather than Question-ID rules.  They cover the
common failure classes seen in financial tables:

* current/prior and start/end period confusion;
* total/aggregate versus component/segment confusion;
* explicit scope and unit incompatibility; and
* metric qualifiers such as net/gross, long/short term, lending/borrowing,
  book value/original cost, and interest direction.

The public function deliberately projects each candidate into a small
allow-list of safe fields.  Unknown fields are never traversed, so a caller
can pass a full candidate record without making its numeric answer part of
the ranking contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
import unicodedata
from typing import Any


SEMANTIC_CANDIDATE_RERANKER_PROTOCOL = "semantic_candidate_reranker_value_blind_v1"
DEFAULT_MAX_CANDIDATES = 64
MAX_MAX_CANDIDATES = 64
DEFAULT_SELECTION_MARGIN = 1.0


# These are the only candidate keys read by this module.  In particular,
# ``value``, ``answer``, ``raw_value``, numeric confidence fields and model
# scores are intentionally absent.
SAFE_CANDIDATE_FIELDS = frozenset(
    {
        "candidate_id",
        "cell_id",
        "uid",
        "internal_table_uid",
        "document_id",
        "row_index",
        "column_index",
        "row",
        "column",
        "row_text",
        "row_label",
        "row_context",
        "row_path",
        "row_header",
        "row_qualifier",
        "row_role",
        "column_text",
        "column_label",
        "column_context",
        "column_header",
        "column_qualifier",
        "header_text",
        "period_label",
        "period_context",
        "column_period",
        "column_role",
        "table_text",
        "table_section",
        "table_purpose",
        "source_title",
        "metric",
        "metric_label",
        "concept",
        "semantic_metric",
        "metric_qualifier",
        "scope",
        "scope_label",
        "statement_scope",
        "report_scope",
        "unit",
        "unit_label",
        "source_unit",
        "column_unit",
        "semantic_unit",
        "unit_context",
        "table_unit",
        "table_unit_hint",
        "period",
        "period_role",
        "report_year",
        "year",
        "entity",
        "entity_label",
        "ticker",
        "issuer",
        "is_total",
        "is_component",
        "aggregation",
        "aggregation_role",
        "row_aggregation",
        "column_aggregation",
        "is_aggregate",
        "rank",
        "pre_rerank_rank",
    }
)

SAFE_QUESTION_METADATA_FIELDS = frozenset(
    {
        "family",
        "question_family",
        "metric",
        "metric_label",
        "scope",
        "unit",
        "requested_unit",
        "period",
        "period_role",
        "year",
        "report_year",
        "entity",
        "ticker",
        "aggregation",
        "aggregation_role",
        "is_total",
        "is_component",
    }
)


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_WORD_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "bao",
        "be",
        "bi",
        "cach",
        "cac",
        "cho",
        "co",
        "cong",
        "cuoi",
        "cua",
        "da",
        "den",
        "duoc",
        "giai",
        "giua",
        "hai",
        "hien",
        "hoi",
        "la",
        "nam",
        "nay",
        "ngay",
        "nhieu",
        "noi",
        "o",
        "sau",
        "so",
        "tai",
        "te",
        "the",
        "theo",
        "thi",
        "trong",
        "tu",
        "va",
        "ve",
        "voi",
        "xet",
        "year",
        "what",
        "how",
        "much",
        "of",
        "the",
        "for",
        "in",
    }
)

_GENERIC_METRIC_WORDS = frozenset(
    {
        "bao",
        "dong",
        "gtri",
        "gia",
        "hoi",
        "khoan",
        "la",
        "muc",
        "nam",
        "ngay",
        "nhieu",
        "phan",
        "tri",
        "ty",
        "trieu",
        "vnd",
        "viet",
        "vietnam",
        "what",
        "how",
        "much",
    }
)

_START_MARKERS = (
    "dau nam",
    "dau ky",
    "so dau nam",
    "so dau ky",
    "opening",
    "beginning",
    "01 01",
)
_END_MARKERS = (
    "cuoi nam",
    "cuoi ky",
    "so cuoi nam",
    "so cuoi ky",
    "closing",
    "ending",
    "31 12",
)
_CURRENT_MARKERS = (
    "nam nay",
    "ky nay",
    "hien tai",
    "current",
    "this year",
    "this period",
)
_PRIOR_MARKERS = (
    "nam truoc",
    "ky truoc",
    "nam lien truoc",
    "previous year",
    "prior year",
    "previous period",
    "prior period",
)

_TOTAL_MARKERS = (
    "tong cong",
    "tong",
    "cong",
    "tong so",
    "tong gia tri",
    "toan bo",
    "toan nganh",
    "grand total",
    "aggregate",
    "total",
)
_COMPONENT_MARKERS = (
    "component",
    "segment",
    "business segment",
    "phan khuc",
    "thanh phan",
    "bo phan",
    "theo nganh",
    "theo linh vuc",
    "theo loai",
)

_SCOPE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "consolidated",
        (
            "hop nhat",
            "bao cao tai chinh hop nhat",
            "consolidated",
            "group accounts",
            "toan tap doan",
        ),
    ),
    (
        "separate",
        (
            "rieng",
            "cong ty me",
            "bao cao tai chinh rieng",
            "separate",
            "parent company",
        ),
    ),
)

_PERCENT_MARKERS = (
    "phan tram",
    "ty le",
    "quyen bieu quyet",
    "bien loi nhuan",
    "percentage",
    "percent",
)

_UNIT_ALIASES: tuple[str, tuple[str, ...], str, str | None] = (
    # The first matching alias wins.  Scale is a label, not a numeric answer.
    "currency_vnd",
    ("ty dong", "billion vnd", "billion dong", "bn vnd"),
    "1e9",
    None,
), (
    "currency_vnd",
    ("trieu dong", "million vnd", "million dong", "mn vnd"),
    "1e6",
    None,
), (
    "currency_vnd",
    ("nghin dong", "thousand vnd", "thousand dong", "k vnd"),
    "1e3",
    None,
), (
    "currency_vnd",
    ("vnd", "viet nam dong", "dong"),
    "1",
    None,
), (
    "currency_usd",
    ("usd", "us dollar", "dollar"),
    "1",
    None,
), (
    "percent",
    ("phan tram", "percentage", "percent"),
    "1",
    "%",
), (
    "shares",
    ("co phieu", "co phan", "shares", "share"),
    "1",
    None,
), (
    "ratio",
    ("lan", "times", "x"),
    "1",
    None,
),

_QUALIFIER_GROUPS: dict[str, tuple[str, ...]] = {
    "net": ("gia tri thuan", "thu nhap thuan", "loi nhuan thuan", "rong", "net"),
    "gross": ("gia tri gop", "loi nhuan gop", "gross", "gop"),
    "long_term": ("dai han", "long term"),
    "short_term": ("ngan han", "short term"),
    "lending": ("cho vay", "du no cho vay", "khoan cho vay", "lending"),
    "borrowing": ("no vay", "khoan vay", "vay", "borrowing"),
    "interest_expense": ("chi phi lai vay", "chi phi lai tien vay", "lai vay expense"),
    "interest_income": ("thu nhap lai", "lai tien gui", "lai tien cho vay", "interest income"),
    "original_cost": ("nguyen gia", "original cost"),
    "book_value": ("gia tri ghi so", "book value"),
    "depreciation": ("khau hao", "hao mon", "depreciation"),
    "supplier_payable": ("phai tra nguoi ban", "supplier payable"),
    "service_purchase": ("mua dich vu", "mua cac dich vu", "service purchase"),
}

_CONFLICTS: tuple[tuple[str, str, str], ...] = (
    ("net", "gross", "METRIC_NET_VS_GROSS_CONFLICT"),
    ("long_term", "short_term", "METRIC_TERM_QUALIFIER_CONFLICT"),
    ("lending", "borrowing", "METRIC_LENDING_VS_BORROWING_CONFLICT"),
    ("interest_expense", "interest_income", "METRIC_INTEREST_DIRECTION_CONFLICT"),
    ("original_cost", "book_value", "METRIC_COST_VS_BOOK_VALUE_CONFLICT"),
)


def _normalize_text(value: object) -> str:
    """Normalize text without interpreting numeric cell payloads."""

    if not isinstance(value, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    without_marks = without_marks.replace("&", " and ")
    without_marks = re.sub(r"[\s/\\,:;|·•()\[\]{}+*=<>_%$€£¥–—-]+", " ", without_marks)
    return " ".join(_WORD_RE.findall(without_marks))


def _text_value(value: object) -> str:
    """Project text-only values; numeric/list cell payloads are ignored."""

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        # A list is accepted only as a list of textual fragments.  Numeric
        # row/cell arrays therefore contribute nothing to this projection.
        return " ".join(part.strip() for part in value if isinstance(part, str) and part.strip())
    return ""


def _safe_maps(candidate: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return direct and allow-listed metadata maps only."""

    maps: list[Mapping[str, Any]] = [candidate]
    for key in ("metadata", "semantic_metadata", "coordinates"):
        nested = candidate.get(key)
        if isinstance(nested, Mapping):
            maps.append(nested)
    return tuple(maps)


def _first_value(maps: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> object:
    for mapping in maps:
        for key in keys:
            # Every key passed to this helper is part of the explicit allow-list.
            value = mapping.get(key)
            if value is not None:
                return value
    return None


def _first_text(maps: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> str:
    parts: list[str] = []
    for mapping in maps:
        for key in keys:
            value = _text_value(mapping.get(key))
            if value and value not in parts:
                parts.append(value)
    return " ".join(parts)


def _identifier(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _coordinate(maps: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> int | str | None:
    value = _first_value(maps, keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coordinate_sort_key(value: int | str | None) -> tuple[int, object]:
    if isinstance(value, int):
        return (0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return (0, int(value.strip()))
    if isinstance(value, str) and value.strip():
        return (1, value.casefold())
    return (2, "")


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return False
    return normalized_phrase in text


def _has_any(text: str, markers: Sequence[str]) -> bool:
    return any(_contains_phrase(text, marker) for marker in markers)


def _years(text: str) -> frozenset[int]:
    return frozenset(int(value) for value in _YEAR_RE.findall(text))


@dataclass(frozen=True)
class _Period:
    years: frozenset[int]
    intent: str | None
    prior: bool
    explicit: bool


def _period(text: str, *, report_year: object = None) -> _Period:
    normalized = _normalize_text(text)
    normalized_years = set(_years(normalized))
    if isinstance(report_year, int) and not isinstance(report_year, bool):
        normalized_years.add(report_year)
    elif isinstance(report_year, str) and report_year.strip().isdigit():
        normalized_years.add(int(report_year.strip()))

    starts = [normalized.find(_normalize_text(marker)) for marker in _START_MARKERS]
    starts = [position for position in starts if position >= 0]
    ends = [normalized.find(_normalize_text(marker)) for marker in _END_MARKERS]
    ends = [position for position in ends if position >= 0]
    if starts and ends:
        intent = "end" if max(ends) > max(starts) else "start"
    elif starts:
        intent = "start"
    elif ends:
        intent = "end"
    elif _has_any(normalized, _CURRENT_MARKERS) or normalized_years:
        intent = "current"
    else:
        intent = None
    return _Period(
        years=frozenset(normalized_years),
        intent=intent,
        prior=_has_any(normalized, _PRIOR_MARKERS),
        explicit=bool(normalized),
    )


def _scope(text: str) -> str | None:
    found = {
        name
        for name, aliases in _SCOPE_ALIASES
        if _has_any(text, aliases)
    }
    if len(found) == 1:
        return next(iter(found))
    if len(found) > 1:
        return "ambiguous"
    return None


@dataclass(frozen=True)
class _Unit:
    family: str | None
    scale: str | None
    explicit: bool


def _unit(text: str) -> _Unit:
    raw = text
    normalized = _normalize_text(text)
    if "%" in raw or _has_any(normalized, _PERCENT_MARKERS):
        return _Unit("percent", "1", True)
    for family, aliases, scale, _literal in _UNIT_ALIASES:
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            return _Unit(family, scale, True)
    return _Unit(None, None, bool(normalized))


def _tokens(text: str, *, extra_stopwords: Sequence[str] = ()) -> frozenset[str]:
    normalized = _normalize_text(text)
    excluded = set(_STOPWORDS) | set(_GENERIC_METRIC_WORDS)
    excluded.update(_normalize_text(value) for value in extra_stopwords)
    return frozenset(
        token
        for token in normalized.split()
        if token not in excluded and not _YEAR_RE.fullmatch(token) and len(token) > 1
    )


def _qualifier_groups(text: str) -> frozenset[str]:
    normalized = _normalize_text(text)
    return frozenset(
        group
        for group, markers in _QUALIFIER_GROUPS.items()
        if _has_any(normalized, markers)
    )


def _aggregation_role(text: str, maps: Sequence[Mapping[str, Any]]) -> str | None:
    explicit_values: list[str] = []
    for mapping in maps:
        for key in (
            "aggregation",
            "aggregation_role",
            "row_aggregation",
            "column_aggregation",
            "row_role",
            "column_role",
        ):
            value = mapping.get(key)
            if isinstance(value, str) and value.strip():
                explicit_values.append(_normalize_text(value))
        for key in ("is_total", "is_aggregate"):
            value = mapping.get(key)
            if isinstance(value, bool):
                explicit_values.append("total" if value else "not_total")
        value = mapping.get("is_component")
        if isinstance(value, bool):
            explicit_values.append("component" if value else "not_component")

    total_explicit = any(
        value in {"total", "aggregate", "grand total", "tong", "cong", "tong cong"}
        or "total" in value
        or "aggregate" in value
        or "tong" in value
        or "cong" == value
        for value in explicit_values
    )
    component_explicit = any(
        value in {"component", "segment", "subcomponent", "detail"}
        or "component" in value
        or "segment" in value
        or "thanh phan" in value
        or "bo phan" in value
        for value in explicit_values
    )
    total_text = _has_any(text, _TOTAL_MARKERS)
    component_text = _has_any(text, _COMPONENT_MARKERS) or bool(re.search(r"(?:^| )[-•]", text))
    if (total_explicit or total_text) and (component_explicit or component_text):
        return "ambiguous"
    if total_explicit or total_text:
        return "total"
    if component_explicit or component_text:
        return "component"
    return None


def _query_scope(question: str, metadata: Mapping[str, Any] | None) -> str | None:
    maps: list[Mapping[str, Any]] = []
    if isinstance(metadata, Mapping):
        maps.append(metadata)
    explicit = _first_text(tuple(maps), ("scope", "scope_label")) if maps else ""
    return _scope(explicit) or _scope(_normalize_text(question))


def _query_unit(question: str, metadata: Mapping[str, Any] | None) -> _Unit:
    maps: list[Mapping[str, Any]] = []
    if isinstance(metadata, Mapping):
        maps.append(metadata)
    explicit = _first_text(tuple(maps), ("unit", "requested_unit")) if maps else ""
    return _unit(" ".join(part for part in (explicit, question) if part))


def _query_years(question: str, metadata: Mapping[str, Any] | None) -> frozenset[int]:
    values = set(_years(_normalize_text(question)))
    if isinstance(metadata, Mapping):
        for key in ("year", "report_year"):
            value = metadata.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                values.add(value)
            elif isinstance(value, str) and value.strip().isdigit():
                values.add(int(value.strip()))
    return frozenset(values)


def _query_aggregation(question: str, metadata: Mapping[str, Any] | None) -> tuple[bool, bool]:
    normalized = _normalize_text(question)
    total = _has_any(normalized, _TOTAL_MARKERS)
    component = _has_any(normalized, _COMPONENT_MARKERS) or " theo " in f" {normalized} "
    if isinstance(metadata, Mapping):
        for key in ("aggregation", "aggregation_role"):
            value = metadata.get(key)
            if isinstance(value, str):
                value_norm = _normalize_text(value)
                total = total or value_norm in {"total", "aggregate", "tong", "cong"}
                component = component or value_norm in {"component", "segment", "detail"}
        if metadata.get("is_total") is True:
            total = True
        if metadata.get("is_component") is True:
            component = True
    return total, component


def _query_metric_text(question: str, metadata: Mapping[str, Any] | None) -> str:
    if isinstance(metadata, Mapping):
        for key in ("metric", "metric_label"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return question


@dataclass(frozen=True)
class _CandidateView:
    candidate_id: str
    row_index: int | str | None
    column_index: int | str | None
    local_text: str
    table_text: str
    metric_text: str
    scope: str | None
    unit: _Unit
    period: _Period
    entity: str
    aggregation: str | None
    original_position: int
    original_rank: int | str | None


def _candidate_view(candidate: Mapping[str, Any], position: int) -> _CandidateView:
    maps = _safe_maps(candidate)
    candidate_id = _identifier(
        _first_value(
            maps,
            (
                "candidate_id",
                "cell_id",
                "internal_table_uid",
                "uid",
                "document_id",
                "id",
            ),
        )
    ) or f"candidate-{position + 1}"
    row_text = _first_text(
        maps,
        ("row_text", "row_label", "row_context", "row_path", "row_header", "row_qualifier", "row_role"),
    )
    column_text = _first_text(
        maps,
        (
            "column_text",
            "column_label",
            "column_context",
            "column_header",
            "column_qualifier",
            "header_text",
            "period_label",
            "period_context",
            "column_period",
            "column_role",
        ),
    )
    metric_text = _first_text(maps, ("metric", "metric_label", "concept", "semantic_metric", "metric_qualifier"))
    table_text = _first_text(
        maps,
        ("table_text", "table_section", "table_purpose", "source_title", "document_id", "internal_table_uid"),
    )
    scope_text = _first_text(maps, ("scope", "scope_label", "statement_scope", "report_scope"))
    if not scope_text:
        scope_text = _first_text(maps, ("document_id",))
    unit_text = _first_text(
        maps,
        ("unit", "unit_label", "source_unit", "column_unit", "semantic_unit", "unit_context", "table_unit", "table_unit_hint", "column_text", "header_text"),
    )
    period_text = _first_text(
        maps,
        ("period", "period_role", "period_label", "period_context", "column_period", "column_text", "column_context", "row_text", "row_context"),
    )
    report_year = _first_value(maps, ("report_year", "year"))
    entity = _first_text(maps, ("entity", "entity_label", "ticker", "issuer", "document_id"))
    local_text_raw = " ".join(part for part in (row_text, column_text, metric_text) if part)
    local_text = _normalize_text(local_text_raw)
    table_text_normalized = _normalize_text(table_text)
    return _CandidateView(
        candidate_id=candidate_id,
        row_index=_coordinate(maps, ("row_index", "row")),
        column_index=_coordinate(maps, ("column_index", "column")),
        local_text=local_text,
        table_text=table_text_normalized,
        metric_text=_normalize_text(metric_text),
        scope=_scope(_normalize_text(scope_text)),
        unit=_unit(unit_text),
        period=_period(period_text, report_year=report_year),
        entity=_normalize_text(entity),
        aggregation=_aggregation_role(local_text, maps),
        original_position=position,
        original_rank=_first_value(maps, ("rank", "pre_rerank_rank")),
    )


def _candidate_entity_matches(query_metadata: Mapping[str, Any] | None, question: str, candidate: _CandidateView) -> tuple[str | None, bool]:
    requested: list[str] = []
    if isinstance(query_metadata, Mapping):
        for key in ("ticker", "entity"):
            value = query_metadata.get(key)
            if isinstance(value, str) and value.strip():
                requested.append(_normalize_text(value))
    requested_text = " ".join(requested)
    if not requested_text:
        # A ticker-like token in the question is metadata/navigation, not a
        # numeric answer.  Keep this deliberately conservative: only short
        # all-cap ticker tokens are extracted.
        requested = [
            _normalize_text(token)
            for token in re.findall(r"\b[A-Z]{2,5}\b", question)
        ]
    requested = [value for value in requested if value]
    if not requested:
        return None, False
    if not candidate.entity:
        return "ENTITY_UNRESOLVED", True
    if any(value in candidate.entity for value in requested):
        return "ENTITY_MATCH", False
    return "ENTITY_MISMATCH", True


def _reason_append(reasons: list[str], value: str) -> None:
    if value not in reasons:
        reasons.append(value)


def _period_guard(
    query_period: _Period,
    query_years: frozenset[int],
    candidate: _CandidateView,
    reasons: list[str],
    strengths: list[str],
) -> None:
    candidate_years = candidate.period.years
    if query_years and candidate_years and not (query_years & candidate_years):
        _reason_append(reasons, "PERIOD_YEAR_MISMATCH")
    elif query_years and candidate_years & query_years:
        _reason_append(strengths, "PERIOD_YEAR_MATCH")
    elif query_years and not candidate_years:
        _reason_append(reasons, "PERIOD_UNRESOLVED")

    if query_period.intent == "end":
        if candidate.period.intent == "start" and not candidate.period.intent == "end":
            _reason_append(reasons, "END_PERIOD_BOUND_TO_START_COLUMN")
        elif candidate.period.prior:
            _reason_append(reasons, "END_PERIOD_BOUND_TO_PRIOR_PERIOD")
        elif candidate.period.intent == "end":
            _reason_append(strengths, "END_PERIOD_MATCH")
        elif candidate.period.intent is None:
            _reason_append(reasons, "END_PERIOD_UNRESOLVED")
    elif query_period.intent == "start":
        if candidate.period.intent == "end" and not candidate.period.intent == "start":
            _reason_append(reasons, "START_PERIOD_BOUND_TO_END_COLUMN")
        elif candidate.period.intent == "start":
            _reason_append(strengths, "START_PERIOD_MATCH")
        elif candidate.period.intent is None:
            _reason_append(reasons, "START_PERIOD_UNRESOLVED")
    elif query_period.intent == "current":
        if candidate.period.prior:
            _reason_append(reasons, "CURRENT_PERIOD_BOUND_TO_PRIOR_PERIOD")
        elif query_years and candidate_years & query_years:
            _reason_append(strengths, "CURRENT_PERIOD_MATCH")
        elif candidate.period.intent == "current":
            _reason_append(strengths, "CURRENT_PERIOD_MATCH")
        elif query_years and not candidate_years:
            _reason_append(reasons, "CURRENT_PERIOD_UNRESOLVED")


def _metric_guard(
    query_metric_text: str,
    candidate: _CandidateView,
    reasons: list[str],
    strengths: list[str],
) -> None:
    query_normalized = _normalize_text(query_metric_text)
    query_tokens = _tokens(query_metric_text)
    candidate_tokens = _tokens(candidate.local_text)
    overlap = query_tokens & candidate_tokens
    query_qualifiers = _qualifier_groups(query_normalized)
    candidate_qualifiers = _qualifier_groups(candidate.local_text)

    for left, right, code in _CONFLICTS:
        if (left in query_qualifiers and right in candidate_qualifiers) or (
            right in query_qualifiers and left in candidate_qualifiers
        ):
            _reason_append(reasons, code)

    for qualifier in query_qualifiers:
        if qualifier not in candidate_qualifiers:
            _reason_append(reasons, "METRIC_QUALIFIER_UNRESOLVED")
            break

    if not candidate.local_text:
        _reason_append(reasons, "METRIC_BINDING_UNRESOLVED")
    elif not overlap:
        _reason_append(reasons, "METRIC_LABEL_MISMATCH")
    else:
        _reason_append(strengths, "METRIC_TOKEN_OVERLAP")
        if query_tokens and query_tokens.issubset(candidate_tokens):
            _reason_append(strengths, "METRIC_QUALIFIER_COMPLETE")


def _aggregation_guard(
    query_total: bool,
    query_component: bool,
    query_metric_text: str,
    candidate: _CandidateView,
    reasons: list[str],
    strengths: list[str],
) -> None:
    if candidate.aggregation == "ambiguous":
        _reason_append(reasons, "AGGREGATION_ROLE_AMBIGUOUS")
        return
    if query_total:
        if candidate.aggregation == "component":
            _reason_append(reasons, "TOTAL_QUERY_BOUND_TO_COMPONENT")
        elif candidate.aggregation == "total":
            _reason_append(strengths, "TOTAL_ROLE_MATCH")
        else:
            _reason_append(reasons, "TOTAL_ROLE_UNRESOLVED")
    if query_component:
        if candidate.aggregation == "total":
            _reason_append(reasons, "COMPONENT_QUERY_BOUND_TO_TOTAL")
        elif candidate.aggregation == "component":
            _reason_append(strengths, "COMPONENT_ROLE_MATCH")
        else:
            # A named component can be safe without a literal ``component``
            # marker when the row/column carries that exact qualifier.  This
            # uses text only; no sibling values or answer is inspected.
            component_tokens = _tokens(query_metric_text)
            if component_tokens and component_tokens & _tokens(candidate.local_text):
                _reason_append(strengths, "COMPONENT_QUALIFIER_MATCH")
            else:
                _reason_append(reasons, "COMPONENT_ROLE_UNRESOLVED")


def _unit_guard(
    query_unit: _Unit,
    question: str,
    candidate: _CandidateView,
    reasons: list[str],
    strengths: list[str],
) -> None:
    candidate_unit = candidate.unit
    query_percent = query_unit.family == "percent" or _has_any(_normalize_text(question), _PERCENT_MARKERS)
    if query_percent and candidate_unit.family not in {None, "percent"}:
        _reason_append(reasons, "PERCENT_QUERY_BOUND_TO_AMOUNT")
    if not query_percent and candidate_unit.family == "percent":
        _reason_append(reasons, "PERCENTAGE_CELL_FOR_AMOUNT_QUERY")
    if query_unit.family is not None:
        if candidate_unit.family is None:
            _reason_append(reasons, "UNIT_UNRESOLVED")
        elif candidate_unit.family != query_unit.family:
            _reason_append(reasons, "UNIT_FAMILY_MISMATCH")
        elif query_unit.scale == candidate_unit.scale:
            _reason_append(strengths, "UNIT_EXACT_MATCH")
        else:
            _reason_append(strengths, "UNIT_SCALE_CONVERSION_REQUIRED")


def _view_candidate(
    candidate: object,
    position: int,
) -> tuple[_CandidateView | None, list[str]]:
    if not isinstance(candidate, Mapping):
        return None, ["CANDIDATE_METADATA_INVALID"]
    try:
        return _candidate_view(candidate, position), []
    except (AttributeError, TypeError, ValueError):
        # A malformed metadata packet is not allowed to abort a whole
        # navigation batch or to fall through as an accepted candidate.
        return None, ["CANDIDATE_METADATA_INVALID"]


def _candidate_result(
    *,
    view: _CandidateView | None,
    reasons: Sequence[str],
    strengths: Sequence[str],
    score: float,
    accepted: bool,
    position: int,
) -> dict[str, Any]:
    if view is None:
        return {
            "candidate_id": f"candidate-{position + 1}",
            "row_index": None,
            "column_index": None,
            "accepted": False,
            "score": 0.0,
            "reason_codes": list(dict.fromkeys(reasons)),
            "signals": {},
            "navigation_metadata_only": True,
            "may_authorize_answer": False,
        }
    return {
        "candidate_id": view.candidate_id,
        "row_index": view.row_index,
        "column_index": view.column_index,
        "accepted": accepted,
        "score": round(score, 6),
        "reason_codes": list(dict.fromkeys(reasons)),
        "signals": {"strengths": list(dict.fromkeys(strengths))},
        "navigation_metadata_only": True,
        "may_authorize_answer": False,
        "submission_eligible": False,
    }


def rerank_semantic_candidates(
    question: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    question_metadata: Mapping[str, Any] | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    selection_margin: float = DEFAULT_SELECTION_MARGIN,
) -> dict[str, Any]:
    """Rank semantic-cell coordinates using value-blind family guards.

    Parameters
    ----------
    question:
        The natural-language question.  It is used only for textual period,
        unit, aggregation, and metric signals.
    candidates:
        Candidate metadata packets.  Row/column coordinates plus text and
        explicit metadata are accepted.  Numeric cell payloads and answer
        fields are ignored even when present in a packet.
    question_metadata:
        Optional typed metadata such as requested year, scope, unit, metric,
        or family.  Only the allow-listed keys are read.
    max_candidates:
        Bounded candidate budget.  Values above 64 are rejected to keep the
        lane deterministic and auditable.
    selection_margin:
        Minimum score gap required for a navigation-only suggestion.  A tie
        or near tie returns no selected candidate.

    The returned ``selected_candidate_id`` is a navigation suggestion only.
    All authority flags are always false and no answer value is returned.
    """

    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool):
        raise ValueError("max_candidates must be an integer")
    if not 1 <= max_candidates <= MAX_MAX_CANDIDATES:
        raise ValueError(f"max_candidates must be between 1 and {MAX_MAX_CANDIDATES}")
    if not isinstance(selection_margin, (int, float)) or isinstance(selection_margin, bool):
        raise ValueError("selection_margin must be numeric")
    if selection_margin < 0:
        raise ValueError("selection_margin must be non-negative")

    metadata = question_metadata if isinstance(question_metadata, Mapping) else None
    base = {
        "protocol": SEMANTIC_CANDIDATE_RERANKER_PROTOCOL,
        "family": "unknown",
        "status": "ABSTAIN",
        "selected_candidate_id": None,
        "reason_codes": [],
        "ranked_candidates": [],
        "authority": {
            "navigation_only": True,
            "may_authorize_answer": False,
            "strict_answer_authorized": False,
            "release_authorized": False,
            "training_eligible": False,
            "promotion_allowed": False,
        },
    }
    if not isinstance(question, str) or not question.strip():
        base["reason_codes"] = ["QUESTION_TEXT_REQUIRED"]
        return base

    question_normalized = _normalize_text(question)
    query_period = _period(question_normalized)
    query_years = _query_years(question, metadata)
    query_unit = _query_unit(question, metadata)
    query_metric_text = _query_metric_text(question, metadata)
    query_total, query_component = _query_aggregation(question, metadata)

    if query_total:
        base["family"] = "aggregate_lookup"
    elif query_component:
        base["family"] = "component_lookup"
    elif query_period.intent == "end":
        base["family"] = "period_end_lookup"
    elif query_period.intent == "current":
        base["family"] = "period_current_lookup"
    else:
        base["family"] = "semantic_direct_lookup"

    raw_candidates = list(candidates or [])[:max_candidates]
    views: list[tuple[_CandidateView | None, list[str], int]] = []
    for position, raw_candidate in enumerate(raw_candidates):
        view, errors = _view_candidate(raw_candidate, position)
        views.append((view, errors, position))

    seen_ids: set[str] = set()
    scored: list[tuple[dict[str, Any], _CandidateView | None, float, int]] = []
    for view, errors, position in views:
        if view is None:
            result = _candidate_result(
                view=None,
                reasons=errors,
                strengths=(),
                score=0.0,
                accepted=False,
                position=position,
            )
            scored.append((result, None, 0.0, position))
            continue

        reasons = list(errors)
        strengths: list[str] = []
        score = 0.0
        if view.candidate_id in seen_ids:
            _reason_append(reasons, "DUPLICATE_CANDIDATE_ID")
        seen_ids.add(view.candidate_id)

        if view.row_index is None or view.column_index is None:
            _reason_append(reasons, "ROW_COLUMN_COORDINATE_REQUIRED")
        else:
            _reason_append(strengths, "CELL_COORDINATE_PRESENT")
            score += 0.25

        _period_guard(query_period, query_years, view, reasons, strengths)
        _metric_guard(query_metric_text, view, reasons, strengths)
        _aggregation_guard(query_total, query_component, query_metric_text, view, reasons, strengths)
        _unit_guard(query_unit, question, view, reasons, strengths)

        entity_reason, entity_hard = _candidate_entity_matches(metadata, question, view)
        if entity_reason == "ENTITY_MATCH":
            _reason_append(strengths, entity_reason)
        elif entity_reason:
            _reason_append(reasons, entity_reason)
        if entity_hard and entity_reason == "ENTITY_MISMATCH":
            score -= 2.0

        # Text/metadata signals are the only positive ranking terms.  A
        # coordinate is used for determinism, never as a proxy for a value.
        score += 2.0 * len(set(strengths) & {"PERIOD_YEAR_MATCH", "CURRENT_PERIOD_MATCH", "END_PERIOD_MATCH", "START_PERIOD_MATCH"})
        score += 2.5 * len(set(strengths) & {"METRIC_TOKEN_OVERLAP", "METRIC_QUALIFIER_COMPLETE"})
        score += 3.0 * len(set(strengths) & {"TOTAL_ROLE_MATCH", "COMPONENT_ROLE_MATCH", "COMPONENT_QUALIFIER_MATCH"})
        score += 1.5 * len(set(strengths) & {"UNIT_EXACT_MATCH", "UNIT_SCALE_CONVERSION_REQUIRED"})
        score += 1.0 * int("ENTITY_MATCH" in strengths)
        hard_reasons = {
            reason
            for reason in reasons
            if reason
            not in {
                # These are explanatory strengths accidentally carried in a
                # shared list only if a future guard changes implementation.
                "PERIOD_YEAR_MATCH",
            }
        }
        accepted = not hard_reasons
        if accepted:
            _reason_append(strengths, "SEMANTIC_GUARDS_PASS")
        else:
            _reason_append(reasons, "SEMANTIC_GUARDS_FAIL")
        result = _candidate_result(
            view=view,
            reasons=reasons,
            strengths=strengths,
            score=score,
            accepted=accepted,
            position=position,
        )
        scored.append((result, view, score, position))

    scored.sort(
        key=lambda item: (
            not bool(item[0]["accepted"]),
            -item[2],
            _coordinate_sort_key(item[1].row_index if item[1] else None),
            _coordinate_sort_key(item[1].column_index if item[1] else None),
            item[3],
        )
    )
    ranked: list[dict[str, Any]] = []
    for rank, (result, _view, _score, _position) in enumerate(scored, start=1):
        ranked_result = dict(result)
        ranked_result["rank"] = rank
        ranked.append(ranked_result)
    base["ranked_candidates"] = ranked

    accepted = [item for item in scored if item[0]["accepted"]]
    if not accepted:
        base["reason_codes"] = [
            "NO_SEMANTICALLY_ACCEPTED_CANDIDATE"
            if scored
            else "NO_CANDIDATES"
        ]
        return base

    top = accepted[0]
    second = accepted[1] if len(accepted) > 1 else None
    if second is not None and (top[2] - second[2]) < float(selection_margin):
        base["reason_codes"] = ["AMBIGUOUS_TOP_CANDIDATES"]
        return base

    base["status"] = "RANKED_SELECTION"
    base["selected_candidate_id"] = top[0]["candidate_id"]
    base["reason_codes"] = ["TOP_CANDIDATE_SEMANTIC_MATCH", "NAVIGATION_ONLY"]
    return base


def rank_semantic_candidates(
    question: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    question_metadata: Mapping[str, Any] | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    selection_margin: float = DEFAULT_SELECTION_MARGIN,
) -> dict[str, Any]:
    """Compatibility alias with a ranking-oriented name."""

    return rerank_semantic_candidates(
        question,
        candidates,
        question_metadata=question_metadata,
        max_candidates=max_candidates,
        selection_margin=selection_margin,
    )


__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "MAX_MAX_CANDIDATES",
    "SAFE_CANDIDATE_FIELDS",
    "SAFE_QUESTION_METADATA_FIELDS",
    "SEMANTIC_CANDIDATE_RERANKER_PROTOCOL",
    "rank_semantic_candidates",
    "rerank_semantic_candidates",
]
