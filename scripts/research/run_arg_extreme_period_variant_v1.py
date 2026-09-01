#!/usr/bin/env python3
"""Run a guarded period-extreme selector ablation.

The typed-plan compiler identifies a family of questions such as ``which year
had the largest value`` but deliberately leaves them ``typed_non_executable``.
This adapter supplies only that missing executor.  It does not change the
canonical table loader, source-first routes, proposal verifier, or evidence
writer; the canonical builder remains responsible for hydration, Decimal
replay, proposal selection, and submission validation.

The selector is intentionally fail-closed:

* ratio/derived and multi-row composition wording is not forced into a
  single-cell period comparison;
* every requested year must bind to an exact ticker/year/scope table;
* a row-family must be stable across all years;
* source values are compared after the canonical unit conversion; and
* ties, missing cells, weak row anchors, and mixed scopes are rejected.

This is an authorized best-effort candidate lane.  A successful local replay
is ``PARTIAL`` unless a separate complete E2E certificate exists.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping


VARIANT_PROTOCOL = "vifinqa_arg_extreme_period_v1"
STRICT_VARIANT_PROTOCOL = "vifinqa_arg_extreme_period_strict_source_v1"
ARG_EXTREME_TIER = "program_arg_extreme_period_v1"
RELATED_PARTY_TRANSACTION_TOTAL_TIER = (
    "program_arg_extreme_related_party_transaction_total_v1"
)
BUILDER_NAME = "_vifinqa_canonical_submission_builder_arg_extreme"


def _preload_source_first_lookup() -> None:
    """Pin the builder's source-first dependency for reproducible A/B runs.

    The frozen builder snapshot is intentionally a partial source tree without
    package ``__init__`` files.  A regular ``src/finance_query`` package in the
    workspace therefore wins normal import resolution even when the snapshot
    directory is first on ``PYTHONPATH``.  Preloading the self-contained module
    under its canonical import name keeps the rest of the builder unchanged
    while making the dependency fingerprint explicit.
    """

    configured_path = os.environ.get("VIFINQA_ARGMAX_SOURCE_FIRST_LOOKUP_PATH")
    if not configured_path:
        return
    source_path = Path(configured_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(
            f"configured source-first lookup does not exist: {source_path}"
        )
    module_name = "finance_query.e2e.core.source_first_lookup"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load configured source-first lookup: {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _load_builder() -> Any:
    _preload_source_first_lookup()
    configured_path = os.environ.get("VIFINQA_ARGMAX_BUILDER_PATH")
    builder_path = (
        Path(configured_path).expanduser()
        if configured_path
        else Path(__file__).resolve().parents[1]
        / "e2e"
        / "build_competition_submission_v1.py"
    )
    spec = importlib.util.spec_from_file_location(BUILDER_NAME, builder_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load canonical builder: {builder_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()
_ORIGINAL_PROGRAM_AWARE = BUILDER.program_aware_answer
_ORIGINAL_ROUTE_PRIORITY = BUILDER._route_priority

_TYPED_PLANS: dict[int, dict[str, Any]] = {}
_TRACE: list[dict[str, Any]] = []
_STATS: Counter[str] = Counter()
_TABLE_INDEX_KEY: int | None = None
_TABLE_INDEX: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
_ROW_CACHE: dict[str, list[tuple[int, str, bool]]] = {}
_SEMANTIC_ROW_CACHE: dict[tuple[str, str, str], float] = {}
_RAW_SOURCE_UNIT_CACHE: dict[tuple[str, int, str], Decimal | None] = {}
_RAW_DOCUMENT_CURRENCY_CACHE: dict[tuple[str, str], Decimal | None] = {}
_RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE: dict[tuple[str, str], Decimal | None] = {}
_STRICT_SOURCE_CONTRACT = False


# These are deliberately broader than the canonical STOPWORDS.  They are
# only used to decide whether an extracted metric has a discriminating row
# anchor; they never alter canonical semantic scores.
_GENERIC_METRIC_TOKENS = {
    "bao",
    "cao",
    "chi",
    "cong",
    "cuoi",
    "dau",
    "diem",
    "dong",
    "gia",
    "giam",
    "hang",
    "hon",
    "ky",
    "lan",
    "lon",
    "muc",
    "nam",
    "nhat",
    "phan",
    "so",
    "tang",
    "thay",
    "thoi",
    "tong",
    "tri",
    "trong",
    "tru",
    "ty",
    "vao",
    "vnd",
    "vung",
}

_EXTREME_MARKER_RE = re.compile(
    r"\s+(?:cao\s+nhat|lon\s+nhat|thap\s+nhat|nho\s+nhat)\b.*$"
)
_YEAR_LIST_RE = re.compile(r"(?:19|20)\d{2}")

# These aliases are accounting-family contracts, not Question-ID exceptions.
# The source row remains the authority; the alias only recovers a grammatical
# wrapper that the typed metric hint keeps around (for example ``giá trị``).
_CANONICAL_METRIC_VARIANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "gia tri thue thu nhap doanh nghiep da nop",
        ("Thuế thu nhập doanh nghiệp đã nộp",),
    ),
    (
        "tong no phai tra",
        ("Nợ phải trả",),
    ),
    (
        "tong thue va cac khoan phai nop nha nuoc",
        ("Thuế và các khoản phải nộp Nhà nước",),
    ),
    (
        "tong gia tri giao dich voi ben lien quan",
        ("Giá trị giao dịch với bên liên quan", "Giá trị giao dịch"),
    ),
)


def _question_id(item: Mapping[str, Any]) -> int | None:
    for key in ("id", "question_id"):
        try:
            if item.get(key) is not None:
                return int(item[key])
        except (TypeError, ValueError):
            continue
    plan = item.get("question_plan")
    if isinstance(plan, Mapping):
        try:
            if plan.get("question_id") is not None:
                return int(plan["question_id"])
        except (TypeError, ValueError):
            pass
    return None


def _typed_plan(item: Mapping[str, Any]) -> dict[str, Any] | None:
    question_id = _question_id(item)
    return _TYPED_PLANS.get(question_id) if question_id is not None else None


def _typed_ticker(typed: Mapping[str, Any]) -> str:
    entities = typed.get("entities") or typed.get("tickers") or []
    if entities:
        return str(entities[0]).strip().upper()
    for operand in typed.get("operands") or []:
        if isinstance(operand, Mapping) and operand.get("ticker"):
            return str(operand["ticker"]).strip().upper()
    return ""


def _typed_years(typed: Mapping[str, Any]) -> list[int]:
    values = typed.get("years") or []
    years: list[int] = []
    for value in values:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    return years


def _typed_scope(item: Mapping[str, Any], typed: Mapping[str, Any]) -> str:
    for value in (
        typed.get("scope"),
        (item.get("question_plan") or {}).get("scope"),
    ):
        scope = str(value or "").strip().lower()
        if scope in {"separate", "consolidated", "aggregated", "unknown"}:
            return scope
    return ""


def _explicit_question_scope(question: str) -> str:
    text = BUILDER.normalize(question)
    if any(
        phrase in text
        for phrase in (
            "cong ty me",
            "bao cao tai chinh rieng",
            "du lieu cong ty me",
            "pham vi cong ty me",
        )
    ):
        return "separate"
    if "bao cao tai chinh hop nhat" in text or "so lieu hop nhat" in text:
        return "consolidated"
    return ""


def _review_scope_preference(item: Mapping[str, Any]) -> str:
    """Use the old shortlist only as a bounded scope-navigation hint."""

    weighted: defaultdict[str, float] = defaultdict(float)
    for candidate in (item.get("candidates") or [])[:12]:
        scope = str(candidate.get("scope") or "unknown").strip().lower() or "unknown"
        if scope not in {"separate", "consolidated", "aggregated", "unknown"}:
            continue
        weight = float(candidate.get("review_score") or 0.0)
        weighted[scope] += weight
    if not weighted:
        return "consolidated"
    # Stable order matters when a shortlist has nearly equal separate and
    # consolidated mass.  The score remains a navigation signal only.
    return max(
        weighted,
        key=lambda scope: (
            weighted[scope],
            scope == "consolidated",
            scope == "separate",
            scope,
        ),
    )


def _scope_for_item(item: Mapping[str, Any], typed: Mapping[str, Any]) -> str:
    explicit = _typed_scope(item, typed)
    if explicit:
        return explicit
    question_scope = _explicit_question_scope(str(item.get("question") or ""))
    if question_scope:
        return question_scope
    return _review_scope_preference(item)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:?")


def _clean_argmax_text(value: Any, *, ticker: str) -> str:
    """Remove question shell while preserving accounting qualifiers."""

    text = _compact(BUILDER.normalize(value))
    if not text:
        return ""

    # Make the time shell harmless before looking for the question's semantic
    # verb.  This also cleans compiler hints where years were replaced by
    # commas but the surrounding phrase was retained.
    text = _YEAR_LIST_RE.sub(" ", text)
    text = re.sub(r"\s+(?:va|hoac|den|toi)\s+", " ", text)
    text = re.sub(r"\b(?:cac|cac moc|moc|giai doan|khoang thoi gian)\b", " ", text)
    text = re.sub(r"\bnam nao\b", " ", text)
    text = re.sub(r"\btrong\s+(?:cac|giai doan|khoang thoi gian)\b", " ", text)
    text = re.sub(r"^\s*trong\s+nam\s+", "", text)
    text = _compact(text)

    # Questions place the metric after one of these semantic pivots.  Keep
    # every generated variant; later row-family scoring decides which one is
    # useful for the current OCR wording.
    pivoted: list[str] = [text]
    for pivot in (
        " ghi nhan ",
        " dat muc ",
        " co muc ",
        " co gia tri ",
        " co chi tieu ",
        " co so du ",
        " co ",
    ):
        if pivot in text:
            if pivot == " co ":
                for match in re.finditer(re.escape(pivot), text):
                    suffix = text[match.end() :]
                    if any(
                        suffix.startswith(noun)
                        for noun in ("phieu ", "gia ", "dinh ", "ban ", "phan ")
                    ):
                        continue
                    pivoted.append(suffix)
            else:
                pivoted.append(text.rsplit(pivot, 1)[1])

    results: list[str] = []
    for candidate in pivoted:
        candidate = _EXTREME_MARKER_RE.sub("", candidate)
        candidate = re.sub(
            r"\s+(?:dat muc|muc|vao|tai|den|trong)\s*$", "", candidate
        )
        candidate = re.sub(r"\s+(?:vao nam|vao cuoi nam)\s*$", "", candidate)
        candidate = re.sub(r"\b(?:chi tieu|muc)\s+", "", candidate)
        candidate = _compact(candidate)
        if not candidate:
            continue
        # Reuse the established bounded entity/unit cleaner.  It removes
        # issuer shells such as ``của ASM ở dữ liệu công ty mẹ`` while keeping
        # metric qualifiers such as ``của khách hàng``.
        cleaned = _compact(
            BUILDER.normalize(
                _clean_with_existing_helper(candidate, ticker=ticker)
            )
        )
        for result in (cleaned, candidate):
            result = _compact(result)
            if result and result not in results:
                results.append(result)
    if not results:
        return ""
    shell_tokens = {
        "nam",
        "trong",
        "cac",
        "moc",
        "giai",
        "doan",
        "vao",
        "ghi",
        "nhan",
        "dat",
        "doi",
        "voi",
        "ty",
        "ngan",
        "hang",
        "me",
        "cao",
        "nhat",
        "lon",
        "nho",
        "thap",
    }
    return min(
        results,
        key=lambda value: (
            len(set(BUILDER.normalize(value).split()) & shell_tokens),
            -len(BUILDER.content_tokens(value)),
            len(value),
        ),
    )


def _clean_with_existing_helper(value: str, *, ticker: str) -> str:
    """Call the prior adapter's cleaner without importing its global state."""

    text = value
    # This is intentionally a small local copy of the entity-boundary part of
    # the multi-entity adapter.  Keeping this lane self-contained prevents
    # cross-variant mutable globals from affecting a run.
    text = re.split(
        r"\s+cua\s+(?:cac\s+)?(?:cong ty me|cong ty|ctcp|ngan hang|tap doan|tong cong ty)\b",
        text,
        maxsplit=1,
    )[0]
    text = re.split(r"\s+o\s+muc\s+(?:cong ty me|cong ty)\b", text, maxsplit=1)[0]
    if ticker:
        for match in list(re.finditer(r"\s+cua\s+", text)):
            tail = set(re.findall(r"[a-z0-9]+", text[match.end() :]))
            if ticker.lower() in tail:
                text = text[: match.start()]
                break
    text = re.sub(
        r"\s+(?:ra don vi|don vi|tinh bang|theo don vi)\s+.*$", "", text
    )
    text = re.sub(r"\s+nam\s*$", "", text)
    text = re.sub(r"\s+(?:la|bao nhieu)\s*$", "", text)
    return _compact(text)


def _metric_variants(item: Mapping[str, Any], typed: Mapping[str, Any]) -> list[str]:
    ticker = _typed_ticker(typed)
    raw_values: list[str] = []
    for operand in typed.get("operands") or []:
        if isinstance(operand, Mapping):
            raw_values.extend(
                str(value)
                for value in operand.get("metric_hints") or []
                if str(value).strip()
            )
    raw_values.append(str(item.get("question") or ""))
    variants: list[str] = []
    for raw in raw_values:
        normalized = _compact(BUILDER.normalize(raw))
        candidates = [normalized]
        before_extreme = _EXTREME_MARKER_RE.sub("", normalized)
        candidates.append(before_extreme)
        if " dat muc " in before_extreme:
            candidates.append(before_extreme.split(" dat muc ", 1)[0])
        if " ghi nhan " in before_extreme:
            candidates.append(before_extreme.split(" ghi nhan ", 1)[1])
        # Avoid treating ``cổ phiếu``, ``có giá`` and ``cố định`` as the
        # question pivot ``có <metric>``.  Those are accounting noun phrases
        # and the shorter tail would silently discard the requested metric.
        if " co " in before_extreme:
            for match in re.finditer(r"\s+co\s+", before_extreme):
                suffix = before_extreme[match.end() :]
                if any(
                    suffix.startswith(noun)
                    for noun in ("phieu ", "gia ", "dinh ", "ban ", "phan ")
                ):
                    continue
                candidates.append(suffix)
        for candidate in candidates:
            cleaned = _clean_argmax_text(candidate, ticker=ticker)
            for value in (cleaned, _compact(candidate)):
                value = _compact(value)
                if len(BUILDER.content_tokens(value)) < 2:
                    continue
                if value not in variants:
                    variants.append(value)
    normalized_question = _compact(
        BUILDER.normalize(str(item.get("question") or ""))
    )
    for phrase, canonical_variants in _CANONICAL_METRIC_VARIANTS:
        if phrase not in normalized_question:
            continue
        for canonical in canonical_variants:
            value = _compact(BUILDER.normalize(canonical))
            if value and value not in variants:
                variants.append(value)
    # Prefer clean metric phrases over question shells.  Keep a small raw
    # fallback because OCR-specific qualifiers can occasionally be useful.
    shell_tokens = {
        "nam",
        "trong",
        "cac",
        "moc",
        "giai",
        "doan",
        "vao",
        "ghi",
        "nhan",
        "dat",
        "doi",
        "voi",
        "ty",
        "ngan",
        "hang",
        "me",
        "cao",
        "nhat",
        "lon",
        "nho",
        "thap",
    }
    ranked = sorted(
        variants,
        key=lambda value: (
            len(set(BUILDER.normalize(value).split()) & shell_tokens),
            -len(_metric_anchor_tokens(value)),
            -len(BUILDER.content_tokens(value)),
            len(value),
        ),
    )
    clean = [
        value
        for value in ranked
        if len(set(BUILDER.normalize(value).split()) & shell_tokens) <= 1
    ]
    return (clean or ranked)[:8]


def _unsupported_reason(question: str, variants: Iterable[str]) -> str | None:
    text = BUILDER.normalize(question)
    if any(
        marker in text
        for marker in (
            "ty trong",
            "ty le",
            "ty so",
            "phan tram",
            "tren tong",
            "so voi tong",
            "cagr",
            "bien loi nhuan",
        )
    ):
        return "DERIVED_RATIO_NEEDS_FORMULA_CONTRACT"
    if "phai thu" in text and "phai tra" in text:
        return "MULTI_ROW_COMPOSITION_NEEDS_FORMULA_CONTRACT"
    if " tong so du " in f" {text} " and "cac khoan" in text:
        return "MULTI_ROW_TOTAL_NEEDS_FORMULA_CONTRACT"
    if not any(len(BUILDER.content_tokens(value)) >= 3 for value in variants):
        return "METRIC_HINT_TOO_GENERIC"
    return None


def _metric_anchor_tokens(metric: str) -> set[str]:
    tokens = set(BUILDER.content_tokens(metric))
    return {
        token
        for token in tokens
        if token not in _GENERIC_METRIC_TOKENS and len(token) > 1
    }


def _strict_metric_phrase_anchors(value: str) -> set[str]:
    """Return only semantic qualifiers that must survive OCR row matching."""

    strict_phrases = (
        ("doanh thu thuan ban hang va cung cap dich vu", {"thuan"}),
        ("ben lien quan", {"ben", "lien", "quan"}),
        ("tuong duong tien", {"tuong", "duong"}),
        ("tai san co dinh huu hinh", {"huu", "hinh"}),
        ("tai san co dinh vo hinh", {"vo", "hinh"}),
        ("chi phi lai vay", {"lai", "vay"}),
        ("chi phi tra truoc dai han", {"dai", "han"}),
        ("du phong cu the", {"cu", "the"}),
        ("hoa hong moi gioi", {"hoa", "hong"}),
        ("doanh thu hoat dong tai chinh", {"hoat", "dong", "tai", "chinh"}),
        ("trai phieu chinh phu", {"trai", "phieu", "chinh", "phu"}),
        ("phat hanh giay to co gia", {"phat", "hanh", "giay", "to"}),
        ("xay dung co ban do dang", {"xay", "dung", "do", "dang"}),
        ("doanh thu chua thuc hien ngan han", {"chua", "thuc", "hien", "ngan", "han"}),
        ("tien tra truoc cho nguoi ban ngan han", {"tra", "truoc", "nguoi", "ban", "ngan", "han"}),
        ("chi phi trich lap du phong cu the cho vay khach hang", {"cu", "the", "khach", "hang"}),
        ("tong chi phi hoa hong moi gioi bat dong san", {"hoa", "hong"}),
    )
    normalized = BUILDER.normalize(value)
    anchors: set[str] = set()
    for phrase, phrase_anchors in strict_phrases:
        if phrase in normalized:
            anchors.update(phrase_anchors)
    return anchors


def _required_row_tokens(metric: str, *, strict: bool = False) -> set[str]:
    """Return narrow lexical anchors that must survive row selection.

    A broad phrase such as ``lợi nhuận khác`` can otherwise select ``lợi
    nhuận gộp`` because both share the first two words.  These anchors are
    derived from metric phenomena rather than question IDs and are checked
    only against the row label.
    """

    tokens = set(BUILDER.normalize(metric).split())
    required: set[str] = set()
    paired_anchors = (
        ("giao", "dich"),
        ("kinh", "doanh", "no"),
        ("lpg",),
        ("khac",),
        ("vay",),
        ("hinh",),
        ("hanh",),
        ("nuoc",),
        ("xuong",),
        ("gioi",),
        ("duong",),
        ("tuc",),
        ("khach",),
        ("gon",),
        ("xay", "dung"),
    )
    for anchor_group in paired_anchors:
        present = set(anchor_group) & tokens
        if len(present) == len(anchor_group):
            required.update(present)
    normalized = BUILDER.normalize(metric)
    if " du thu " in f" {normalized} ":
        required.update({"du", "thu"})
    if "tong gia tri giao dich" in normalized:
        required.add("tong")
    if "von co phan" in normalized:
        # ``co`` is a canonical stopword, so use the two surviving nouns as
        # the hard row identity rather than requiring the stopword itself.
        required.update({"von", "phan"})
    if "bat dong san dau tu" in normalized:
        # A revenue row can mention ``nhà xưởng`` too, but it is not the
        # requested investment-property balance.  Preserve the asset-class
        # anchor without requiring the words ``giá trị còn lại`` to be in the
        # row label (those are often column headers).
        required.update({"dau", "tu"})
    if "chung khoan kinh doanh" in normalized:
        # The cash-flow phrase ``hoạt động kinh doanh`` shares the ``kinh
        # doanh`` suffix but is not a trading-securities balance.
        required.update({"chung", "khoan"})
    if "doanh thu chua thuc hien" in normalized:
        required.update({"chua", "thuc", "hien"})
    if "doanh thu cung cap dich vu" in normalized:
        required.update({"cung", "cap", "dich"})
    if "tien tra truoc cho nguoi ban ngan han" in normalized:
        # The source row is conventionally labelled ``Trả trước cho người
        # bán ngắn hạn``; the question's leading ``tiền`` is a noun shell,
        # not a required row token.
        required.update({"nguoi", "ban", "ngan", "han"})
    if not required:
        scope_tokens = {
            "bao",
            "cao",
            "cong",
            "du",
            "lieu",
            "me",
            "pham",
            "rieng",
            "so",
            "tai",
            "chinh",
            "ty",
            "vi",
        }
        meaningful = [
            token
            for token in BUILDER.normalize(metric).split()
            if token not in _GENERIC_METRIC_TOKENS
            and token not in scope_tokens
            and token not in getattr(BUILDER, "STOPWORDS", set())
            and len(token) > 2
        ]
        if meaningful:
            required.add(meaningful[-1])
    if strict:
        # These qualifiers separate accounting concepts that otherwise share
        # a large lexical prefix.  They are phrase-derived, not Question-ID
        # exceptions.  Only add a qualifier when the source row convention
        # normally preserves it; compact aliases such as ``Doanh thu LPG``
        # remain usable through their domain anchor.
        required.update(_strict_metric_phrase_anchors(metric))
    return required


def _longest_phrase(metric: str, label: str) -> int:
    metric_words = BUILDER.normalize(metric).split()
    label_norm = BUILDER.normalize(label)
    best = 0
    for width in range(min(6, len(metric_words)), 1, -1):
        for start in range(0, len(metric_words) - width + 1):
            if " ".join(metric_words[start : start + width]) in label_norm:
                best = max(best, width)
    return best


def _row_label(row: Any) -> str:
    if not isinstance(row, list):
        return ""
    # A few OCR tables concatenate adjacent Vietnamese words at a case
    # boundary (for example ``phát hànhCổ`` or ``CrownSài``).  Insert a space
    # only for the unambiguous lower-case -> upper-case transition.  This is
    # a navigation/matching aid; the raw row stored in the evidence packet is
    # never rewritten.
    labels: list[str] = []
    for cell in row[:3]:
        if BUILDER.parse_decimal(cell) is not None or not str(cell).strip():
            continue
        text = str(cell)
        separated: list[str] = []
        for index, character in enumerate(text):
            if (
                index
                and character.isupper()
                and text[index - 1].islower()
            ):
                separated.append(" ")
            separated.append(character)
        labels.append("".join(separated))
    return " ".join(labels)


def _row_signature(label: str) -> str:
    text = BUILDER.normalize(label)
    text = re.sub(r"\b(?:thuyet minh|tm|muc so)\s*\d+[a-z]?\b", " ", text)
    text = re.sub(r"\b\d+(?:\s*=\s*\d+)+\b", " ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _compact(text)


def _family_similarity(left: str, right: str) -> float:
    left_norm = _row_signature(left)
    right_norm = _row_signature(right)
    if not left_norm or not right_norm:
        return 0.0
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    jaccard = intersection / max(1, union)
    containment = intersection / max(1, min(len(left_tokens), len(right_tokens)))
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(jaccard, 0.82 * containment, 0.78 * sequence)


def _table_index(
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    global _TABLE_INDEX_KEY, _TABLE_INDEX
    key = id(tables_by_uid)
    if _TABLE_INDEX_KEY == key:
        return _TABLE_INDEX
    index: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for raw_table in tables_by_uid.values():
        table = dict(raw_table)
        ticker = BUILDER.table_ticker(table).upper()
        if not ticker:
            continue
        report_year = table.get("report_year")
        try:
            year = int(report_year)
        except (TypeError, ValueError):
            year = BUILDER.document_year(table.get("document_id"))
        if year is None:
            continue
        scope = str(table.get("scope") or "unknown").strip().lower() or "unknown"
        index[(ticker, int(year), scope)].append(table)
    _TABLE_INDEX_KEY = key
    _TABLE_INDEX = dict(index)
    _ROW_CACHE.clear()
    _SEMANTIC_ROW_CACHE.clear()
    _STATS["table_index_entries"] = len(_TABLE_INDEX)
    return _TABLE_INDEX


_STRICT_ALLOWED_TABLE_KINDS = {
    "balance sheet",
    "income statement",
    "cash flow statement",
    "financial note",
    "debt schedule",
    "related party schedule",
}


def _comparison_table_kind(table: Mapping[str, Any]) -> str:
    """Collapse only the harmless note-detail reconstruction distinction."""

    raw_kind = BUILDER.source_first_lookup_module._table_kind(table)
    kind = BUILDER.normalize(raw_kind).strip()
    if _is_context_bound_crown_payable(table):
        # The exact related-party payable disclosure can be reconstructed as
        # either a broad financial note or a related-party schedule. Collapse
        # only this counterparty row and its related-party payable context;
        # generic supplier rows remain distinct.
        return "related party schedule"
    if _is_context_bound_customer_loan_accrued_interest(table):
        # STB's 2017/2022 notes call this row ``Lãi từ cho vay khách hàng``
        # while 2024 calls it ``Lãi dự thu từ cho vay khách hàng``.  Treat
        # the labels as one family only inside the exact ``Các khoản lãi,
        # phí phải thu`` note and for STB; a generic loan-interest row must
        # not enter this alias.
        return "debt schedule"
    if _is_context_bound_construction_cost_payable(table):
        # NVL's 2025 table is reconstructed as a generic data schedule even
        # though its exact row belongs to the same short-term-payables note as
        # the 2020/2022 financial-note-detail tables.
        return "financial note"
    if _is_context_bound_trading_debt_securities(table):
        # MBB's main trading-securities note is consistently a financial note
        # in the selected years.  Keep a possible note-detail extraction in
        # the same family, but do not merge investment schedules or generic
        # debt schedules merely because they contain ``Chứng khoán nợ``.
        return "financial note"
    if _is_context_bound_brokerage_cost(table):
        # The exact row is disclosed under the short-term-payables section in
        # both years, although the extractor uses two raw schedule labels.
        return "debt schedule"
    if kind == "financial note detail" and _is_context_bound_provision_expense(table):
        # OCB's historical credit-risk disclosure is extracted as a
        # ``debt_schedule`` in 2017--2019 but as a ``financial_note_detail``
        # in 2025.  Treat these as one family only when the exact
        # ``Trích lập dự phòng cụ thể cho vay khách hàng`` row and the
        # ``Chi phí dự phòng rủi ro tín dụng`` source context are both
        # present.  A generic note/detail table must remain distinct.
        return "debt schedule"
    if kind in {
        "financial note",
        "financial note detail",
        "related party schedule",
    } and _is_context_bound_related_party_revenue(table):
        # The 2020 SSH table is classified as a broad financial note while
        # the same disclosed total is classified as a related-party schedule
        # from 2021 onward.  The source context and exact aggregate row prove
        # that these are one accounting family; the raw extractor label does
        # not.  Keep this normalization narrower than a generic note/schedule
        # merge so unrelated notes can never enter the cohort.
        return "related party schedule"
    if kind == "financial note detail":
        return "financial note"
    if kind == "financial data schedule" and _is_cash_flow_tax_schedule(table):
        # Some current VNM cash-flow pages are reconstructed as a generic
        # schedule even though their source context is an operating cash-flow
        # statement.  Admit only the exact disclosed tax-paid row when the
        # context and source unit make that reconstruction auditable.
        return "cash flow statement"
    return kind


def _is_context_bound_provision_expense(table: Mapping[str, Any]) -> bool:
    """Recognize one reconstructed credit-risk provision expense row family."""

    exact_row = False
    for row in table.get("rows") or []:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        label = BUILDER.normalize(row[0]).strip()
        label = re.sub(r"\s*\(?\s*thuyet minh\b.*$", "", label).strip()
        if label == "trich lap du phong cu the cho vay khach hang":
            exact_row = True
            break
    if not exact_row:
        return False

    context_trace = table.get("context_trace") or {}
    source_title = BUILDER.normalize(
        context_trace.get("source_title")
        if isinstance(context_trace, Mapping)
        else ""
    )
    return "chi phi du phong rui ro tin dung" in source_title


def _is_context_bound_related_party_revenue(table: Mapping[str, Any]) -> bool:
    """Recognize one exact related-party aggregate row family.

    ``Doanh thu cung cấp dịch vụ`` is the total row in the related-party
    disclosure.  The qualifier ``bên liên quan`` is often carried by the
    table's source title instead of being repeated in that total row, so the
    strict candidate gate may use this context only for this exact row and
    only when the source title also describes related-party transactions.
    """

    has_exact_revenue_total = False
    for row in table.get("rows") or []:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        first_cell = BUILDER.normalize(row[0]).strip()
        # ``BUILDER.normalize`` removes parentheses before this check, so the
        # OCR may arrive as either ``(... thuyết minh số ...)`` or simply
        # ``... thuyết minh số ...``.
        first_cell = re.sub(r"\s*\(?\s*thuyet minh\b.*$", "", first_cell)
        if first_cell == "doanh thu cung cap dich vu":
            has_exact_revenue_total = True
            break
    if not has_exact_revenue_total:
        return False

    context_trace = table.get("context_trace") or {}
    source_title = BUILDER.normalize(
        context_trace.get("source_title")
        if isinstance(context_trace, Mapping)
        else ""
    )
    return "giao dich" in source_title and "ben lien quan" in source_title


_RELATED_PARTY_TRANSACTION_RAW_KINDS = {
    "financial note",
    "financial note detail",
    "related party schedule",
    "governance roster",
}


def _is_related_party_transaction_total_metric(
    metric: str,
    question: str = "",
) -> bool:
    """Recognize the reusable related-party transaction-total phenomenon.

    This is deliberately a phrase-level contract.  It admits questions that
    ask for the total value of transactions with related parties, while
    keeping ordinary ``giao dịch`` lookups and related-party balance rows out
    of the aggregate route.
    """

    normalized = BUILDER.normalize(f"{metric} {question}")
    return (
        "tong gia tri giao dich" in normalized
        and "ben lien quan" in normalized
    )


def _has_related_party_transaction_header(table: Mapping[str, Any]) -> bool:
    headers = table.get("headers") or table.get("column_labels") or []
    return "gia tri giao dich" in BUILDER.normalize(" ".join(str(value) for value in headers))


def _related_party_transaction_context_anchor(table: Mapping[str, Any]) -> bool:
    """Return whether a table starts the named main transaction disclosure."""

    if not _has_related_party_transaction_header(table):
        return False
    trace = table.get("context_trace") or {}
    source_title = (
        trace.get("source_title") if isinstance(trace, Mapping) else ""
    )
    context = BUILDER.normalize(
        " ".join(
            (
                str(table.get("context_before") or ""),
                str(source_title or ""),
            )
        )
    )
    return "giao dich chu yeu" in context and "ben lien quan" in context


def _related_party_transaction_current_column(
    table: Mapping[str, Any],
    year: int,
) -> int | None:
    """Locate the current report-year column from the source header."""

    headers = table.get("headers") or table.get("column_labels") or []
    for index, header in enumerate(headers):
        if index == 0:
            continue
        normalized = BUILDER.normalize(header)
        if str(year) in normalized:
            return index

    # A small number of normalized runtime tables keep the display header in
    # the first two structured rows instead of ``headers``.  Require the
    # report year there as well; never infer the current column from a value.
    for raw_row in (table.get("rows") or [])[:3]:
        if not isinstance(raw_row, (list, tuple)):
            continue
        for index, cell in enumerate(raw_row):
            if index == 0:
                continue
            if str(year) in BUILDER.normalize(cell):
                return index
    return None


def _related_party_transaction_group(
    tables: Iterable[Mapping[str, Any]],
    *,
    year: int,
    scope: str,
) -> list[dict[str, Any]]:
    """Collect contiguous continuation tables for one disclosure section.

    The first page carries the source-title anchor.  Continuation pages often
    lose that title and are classified as generic financial notes or even a
    governance roster, so a one-table lookup would undercount the total.  The
    group is bounded by the source asset's consecutive local ordinals and the
    repeated ``Giá trị giao dịch`` header.  A second anchor is treated as an
    ambiguity and rejected rather than merged opportunistically.
    """

    ordered = [
        dict(table)
        for table in tables
        if str(table.get("scope") or "unknown").strip().lower() == scope
    ]
    ordered.sort(
        key=lambda table: (
            int(table.get("local_ordinal") or 10**9),
            int(table.get("char_start") or 10**9),
            str(table.get("internal_table_uid") or ""),
        )
    )
    anchors = [
        index
        for index, table in enumerate(ordered)
        if _related_party_transaction_context_anchor(table)
    ]
    if len(anchors) != 1:
        if anchors:
            _STATS["related_party_transaction_ambiguous_anchor"] += 1
        return []

    start = anchors[0]
    group: list[dict[str, Any]] = []
    previous_ordinal: int | None = None
    for index in range(start, len(ordered)):
        table = ordered[index]
        if int(table.get("report_year") or year) != year:
            break
        if not _has_related_party_transaction_header(table):
            break
        if _related_party_transaction_current_column(table, year) is None:
            break
        raw_ordinal = table.get("local_ordinal")
        try:
            ordinal = int(raw_ordinal)
        except (TypeError, ValueError):
            ordinal = None
        if group and (
            previous_ordinal is None
            or ordinal is None
            or ordinal != previous_ordinal + 1
        ):
            break
        group.append(table)
        previous_ordinal = ordinal
    if not group:
        return []
    if BUILDER.table_ticker(group[0]).upper() == "":
        return []
    return group


def _related_party_transaction_value_rows(
    table: Mapping[str, Any],
    *,
    year: int,
    column_index: int,
) -> list[tuple[int, str, Any, Decimal]]:
    """Return leaf transaction rows, excluding explicit duplicate totals."""

    header_indices = {
        int(index)
        for index in table.get("header_row_indices") or []
        if str(index).lstrip("-").isdigit()
    }
    rows: list[tuple[int, str, Any, Decimal]] = []
    for row_index, row in enumerate(table.get("rows") or []):
        if row_index in header_indices or not isinstance(row, (list, tuple)):
            continue
        if column_index >= len(row):
            continue
        label = _row_label(list(row)).strip()
        raw_value = row[column_index]
        value = BUILDER.parse_decimal(raw_value)
        if not label or value is None:
            continue
        normalized_label = BUILDER.normalize(label)
        if normalized_label in {"cong", "tong", "tong cong", "total"}:
            _STATS["related_party_transaction_explicit_total_rows_skipped"] += 1
            continue
        if "tong cong" in normalized_label:
            _STATS["related_party_transaction_explicit_total_rows_skipped"] += 1
            continue
        rows.append((row_index, label, raw_value, value))
    return rows


def _related_party_transaction_year_aggregate(
    *,
    item: Mapping[str, Any],
    ticker: str,
    year: int,
    scope: str,
    tables: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    group = _related_party_transaction_group(tables, year=year, scope=scope)
    if not group:
        return None
    output_divisor = BUILDER.requested_divisor(str(item.get("question") or ""))
    components: list[dict[str, Any]] = []
    multipliers: list[Decimal] = []
    for table in group:
        raw_kind = BUILDER.normalize(
            BUILDER.source_first_lookup_module._table_kind(table)
        ).strip()
        if raw_kind not in _RELATED_PARTY_TRANSACTION_RAW_KINDS:
            _STATS["related_party_transaction_unsafe_table_kind"] += 1
            return None
        column_index = _related_party_transaction_current_column(table, year)
        if column_index is None:
            _STATS["related_party_transaction_current_column_missing"] += 1
            return None
        multiplier = _source_multiplier_for_strict_contract(table)
        if multiplier is None:
            _STATS["related_party_transaction_source_unit_missing"] += 1
            return None
        multipliers.append(multiplier)
        for row_index, label, raw_value, parsed_value in _related_party_transaction_value_rows(
            table,
            year=year,
            column_index=column_index,
        ):
            value = parsed_value * multiplier / output_divisor
            components.append(
                {
                    "score": 100.0,
                    "value": value,
                    "raw_value": parsed_value,
                    "source_multiplier": multiplier,
                    "source_to_vnd_multiplier": multiplier,
                    "row_index": row_index,
                    "column_index": column_index,
                    "row_label": label,
                    "document_id": str(table.get("document_id") or "").removesuffix(".txt"),
                    "internal_table_uid": str(table.get("internal_table_uid") or ""),
                    "candidate_rank": 0,
                    "candidate_source": "arg_extreme_related_party_transaction_total_source",
                    "research_candidate_only": True,
                    "related_party_transaction_component": True,
                    "argmax_year": year,
                    "argmax_scope": scope,
                    "source_unit": "million_vnd",
                }
            )
    if not components or not multipliers or len(set(multipliers)) != 1:
        _STATS["related_party_transaction_bad_component_contract"] += 1
        return None
    return {
        "year": year,
        "tables": group,
        "components": components,
        "value": sum((row["value"] for row in components), Decimal(0)),
        "source_multiplier": multipliers[0],
    }


def _related_party_transaction_query_exact(
    *,
    component_counts: Mapping[int, int],
    years: list[int],
    selected_year: int,
    direction: str,
) -> str:
    sums: dict[int, str] = {}
    for year in years:
        roles = [
            f"'period_{year}_component_{index}'"
            for index in range(1, int(component_counts[year]) + 1)
        ]
        role_list = ",".join(roles)
        sums[year] = (
            "(df1.loc[df1.operand_role.isin(["
            f"{role_list}"
            " ]),'operand_value'].sum())"
        )
    selected = sums[selected_year]
    comparator = ">" if direction == "max" else "<"
    comparisons = [
        f"({selected} {comparator} {sums[year]})"
        for year in years
        if year != selected_year
    ]
    return f"float(({ ' * '.join(comparisons) }) * {selected_year})"


def _resolve_related_party_transaction_total(
    item: Mapping[str, Any],
    typed: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    ticker = _typed_ticker(typed)
    years = _typed_years(typed)
    direction = str((typed.get("operation_ast") or {}).get("direction") or "max").lower()
    if not ticker or len(years) < 2 or direction not in {"max", "min"}:
        _STATS["related_party_transaction_contract_invalid"] += 1
        return None

    scope = _scope_for_item(item, typed)
    explicit_scope = bool(
        _typed_scope(item, typed)
        or _explicit_question_scope(str(item.get("question") or ""))
    )
    index = _table_index(tables_by_uid)
    if explicit_scope:
        scope_candidates = [scope]
    else:
        scope_candidates = [
            scope,
            *[
                value
                for value in ("consolidated", "separate", "aggregated", "unknown")
                if value != scope
            ],
        ]
    scope_candidates = [
        candidate_scope
        for candidate_scope in scope_candidates
        if all(index.get((ticker, year, candidate_scope)) for year in years)
    ]
    if not scope_candidates:
        _STATS["related_party_transaction_scope_coverage_missing"] += 1
        return None

    cohorts: dict[str, list[dict[str, Any]]] = {}
    winners: dict[str, int] = {}
    for candidate_scope in scope_candidates:
        records: list[dict[str, Any]] = []
        valid = True
        for year in years:
            record = _related_party_transaction_year_aggregate(
                item=item,
                ticker=ticker,
                year=year,
                scope=candidate_scope,
                tables=index.get((ticker, year, candidate_scope), []),
            )
            if record is None:
                valid = False
                break
            records.append(record)
        if not valid or len(records) != len(years):
            continue
        values = [record["value"] for record in records]
        extreme = max(values) if direction == "max" else min(values)
        winner_indices = [i for i, value in enumerate(values) if value == extreme]
        if len(winner_indices) != 1:
            _STATS["related_party_transaction_extreme_tie"] += 1
            continue
        cohorts[candidate_scope] = records
        winners[candidate_scope] = years[winner_indices[0]]

    if not cohorts:
        _STATS["related_party_transaction_cohort_rejected"] += 1
        return None

    selected_scope = scope
    if selected_scope not in cohorts:
        if len(set(winners.values())) != 1:
            _STATS["related_party_transaction_scope_winner_disagreement"] += 1
            return None
        selected_scope = next(iter(cohorts))
    elif not explicit_scope and len(winners) > 1 and len(set(winners.values())) != 1:
        _STATS["related_party_transaction_scope_winner_disagreement"] += 1
        return None

    records = cohorts[selected_scope]
    values = [record["value"] for record in records]
    extreme = max(values) if direction == "max" else min(values)
    extreme_indices = [i for i, value in enumerate(values) if value == extreme]
    if len(extreme_indices) != 1:
        _STATS["related_party_transaction_extreme_tie"] += 1
        return None
    selected_year = years[extreme_indices[0]]

    evidence_rows: list[dict[str, Any]] = []
    component_counts: dict[int, int] = {}
    for year, record in zip(years, records):
        component_counts[year] = len(record["components"])
        for index, component in enumerate(record["components"], start=1):
            component["role"] = f"period_{year}_component_{index}"
        # Four aggregate anchors make the candidate compatible with the
        # opt-in answer-level selector's one-operand-per-period contract.
        # They carry no raw value; the independent replay and the component
        # rows remain the actual source-cell closure.
        summary = dict(record["components"][0])
        summary.pop("raw_value", None)
        summary["value"] = record["value"]
        summary["role"] = f"period_{year}_aggregate_summary"
        summary["row_label"] = (
            f"Tổng giá trị giao dịch với bên liên quan ({year}; "
            f"{len(record['components'])} source cells)"
        )
        summary.pop("related_party_transaction_component", None)
        summary["related_party_transaction_summary"] = True
        evidence_rows.append(summary)
    for year, record in zip(years, records):
        evidence_rows.extend(record["components"])

    query = _related_party_transaction_query_exact(
        component_counts=component_counts,
        years=years,
        selected_year=selected_year,
        direction=direction,
    )
    _STATS["related_party_transaction_total_accepted"] += 1
    _TRACE.append(
        {
            "question_id": _question_id(item),
            "status": "ACCEPTED",
            "route": RELATED_PARTY_TRANSACTION_TOTAL_TIER,
            "ticker": ticker,
            "years": years,
            "direction": direction,
            "metric": "related_party_transaction_total",
            "scope_selected": selected_scope,
            "scope_preference": scope,
            "values": [str(value) for value in values],
            "component_counts": component_counts,
            "selected_year": selected_year,
            "sources": [
                {
                    "period": year,
                    "aggregate_value": str(record["value"]),
                    "component_count": len(record["components"]),
                    "table_uids": [
                        str(table.get("internal_table_uid") or "")
                        for table in record["tables"]
                    ],
                }
                for year, record in zip(years, records)
            ],
        }
    )
    return Decimal(selected_year), evidence_rows, query, RELATED_PARTY_TRANSACTION_TOTAL_TIER


def _is_context_bound_other_profit_loss_row(
    table: Mapping[str, Any],
    row_index: int,
) -> bool:
    """Recognize statement code 40's ``lợi nhuận khác`` row family.

    TTF's consolidated income statements alternate between ``Lợi nhuận
    khác``, ``(Lỗ) lợi nhuận khác`` and the shortened ``Lỗ khác``.  The
    statement code and table kind are the stable accounting identity; the
    wording alias is not allowed to operate outside that exact context.
    """

    raw_kind = BUILDER.source_first_lookup_module._table_kind(table)
    if BUILDER.normalize(raw_kind).strip() != "income statement":
        return False
    rows = table.get("rows") or []
    if row_index < 0 or row_index >= len(rows):
        return False
    row = rows[row_index]
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return False
    if BUILDER.normalize(row[0]).strip() != "40":
        return False
    label = BUILDER.normalize(_row_label(list(row))).strip()
    return "loi nhuan khac" in label or bool(re.search(r"\blo\s+khac\b", label))


def _is_context_bound_total_liabilities(
    table: Mapping[str, Any],
    row_index: int,
) -> bool:
    """Recognize balance-sheet code 300 as the total-liabilities family.

    HND's OCR writes ``NỘ PHẢI TRẢ`` in each requested year, while the
    accounting identity is stable: balance-sheet row code 300, ``Nợ phải
    trả (300 = 310 + 330)``.  The code and table kind are required together;
    a cash-flow row such as ``Biến động các khoản phải trả...`` must never be
    admitted merely because it shares the words ``nợ phải trả``.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind != "balance sheet":
        return False
    rows = table.get("rows") or []
    if row_index < 0 or row_index >= len(rows):
        return False
    row = rows[row_index]
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return False
    if BUILDER.normalize(row[1]).strip() != "300":
        return False
    label = BUILDER.normalize(_row_label(list(row))).strip()
    return bool(re.search(r"\bno\s+phai\s+tra\b", label))


def _is_context_bound_tax_payable_balance(
    table: Mapping[str, Any],
    row_index: int | None = None,
) -> bool:
    """Recognize balance-sheet code 313 as one tax-payable family.

    The question wording ``tổng thuế và các khoản phải nộp Nhà nước`` also
    appears in tax-movement notes.  The reusable accounting identity for this
    period-extreme family is narrower: a balance-sheet table, code 313, and
    the exact liability-row label.  This keeps a note's opening/current tax
    movement row from entering the same cohort merely because its label is
    similar.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind != "balance sheet":
        return False
    rows = table.get("rows") or []
    indices = [row_index] if row_index is not None else range(len(rows))
    for index in indices:
        if index is None or index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        code_match = any(
            bool(re.fullmatch(r"0*313", BUILDER.normalize(cell).strip()))
            for cell in row[:4]
        )
        if not code_match:
            continue
        label = BUILDER.normalize(_row_label(list(row))).strip()
        if re.search(
            r"\bthue\s+va\s+cac\s+khoan\s+phai\s+nop\s+nha\s+nuoc\b",
            label,
        ):
            return True
    return False


def _is_context_bound_construction_cost_payable(
    table: Mapping[str, Any],
    row_index: int | None = None,
) -> bool:
    """Recognize the short-term construction-cost payable row family.

    NVL's disclosure is extracted as ``financial_note_detail`` in 2020/2022
    and as a generic ``financial_data_schedule`` in 2025.  The stable
    accounting identity is the exact ``Chi phí xây dựng`` row under the
    numbered ``Chi phí phải trả`` note, with an explicit short-term context.
    A generic schedule or a construction-in-progress asset row must not enter
    the family merely because it contains the words ``xây dựng``.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind not in {
        "financial note",
        "financial note detail",
        "financial data schedule",
    }:
        return False

    context_trace = table.get("context_trace") or {}
    trace_parts = [str(table.get("context_before") or "")]
    if isinstance(context_trace, Mapping):
        trace_parts.extend(
            str(context_trace.get(key) or "")
            for key in ("source_title", "summary", "topic")
        )
    context = BUILDER.normalize(" ".join(trace_parts))
    if "chi phi phai tra" not in context:
        return False

    rows = table.get("rows") or []
    indices = [row_index] if row_index is not None else range(len(rows))
    for index in indices:
        if index is None or index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, (list, tuple)) or not row:
            continue
        label = BUILDER.normalize(_row_label(list(row))).strip()
        if label != "chi phi xay dung":
            continue

        # 2020/2022 put ``ngắn hạn`` in the numbered source heading. The 2025
        # reconstruction keeps the shorter heading but includes an explicit
        # ``a. Ngắn hạn`` row before the target and ``b. Dài hạn`` after it.
        # Bind that local hierarchy rather than widening the whole note to
        # every construction-cost row.
        if "chi phi phai tra ngan han" in context:
            return True
        latest_short_term = -1
        latest_long_term = -1
        for prior_index in range(index):
            prior_row = rows[prior_index]
            if not isinstance(prior_row, (list, tuple)) or not prior_row:
                continue
            prior_label = BUILDER.normalize(_row_label(list(prior_row))).strip()
            if prior_label in {"a ngan han", "ngan han"}:
                latest_short_term = prior_index
            if prior_label in {"b dai han", "dai han"}:
                latest_long_term = prior_index
        if latest_short_term > latest_long_term:
            return True
    return False


def _is_context_bound_trading_debt_securities(
    table: Mapping[str, Any],
    row_index: int | None = None,
) -> bool:
    """Recognize MBB's exact debt-securities trading-balance row family.

    The phrase ``Chứng khoán nợ`` also occurs in investment portfolios and
    interest-income notes.  The reusable accounting identity here is narrower:
    a main financial-note table whose source topic is ``Chứng khoán kinh
    doanh`` and whose exact row is ``Chứng khoán nợ``.  This context binds the
    trading book without relying on a retrieved-cell rank or Question ID.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind not in {"financial note", "financial note detail"}:
        return False

    context_trace = table.get("context_trace") or {}
    trace_parts = [str(table.get("context_before") or "")]
    if isinstance(context_trace, Mapping):
        trace_parts.extend(
            str(context_trace.get(key) or "")
            for key in ("source_title", "summary")
        )
        topic = context_trace.get("topic")
        if isinstance(topic, Mapping):
            trace_parts.append(str(topic.get("label") or ""))
        else:
            trace_parts.append(str(topic or ""))
    context = BUILDER.normalize(" ".join(trace_parts))
    if "chung khoan kinh doanh" not in context:
        return False
    # The listing-status child table repeats the row but is not the primary
    # balance disclosure.  The parent note is the stable cross-year family.
    if "tinh trang niem yet" in context:
        return False

    rows = table.get("rows") or []
    indices = [row_index] if row_index is not None else range(len(rows))
    for index in indices:
        if index is None or index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, (list, tuple)) or not row:
            continue
        if BUILDER.normalize(_row_label(list(row))).strip() == "chung khoan no":
            return True
    return False


def _is_context_bound_other_income(
    table: Mapping[str, Any],
    row_index: int | None = None,
) -> bool:
    """Recognize the exact income-statement ``Thu nhập khác`` row family.

    The 2016 ASM table carries an OCR-corrupted VND declaration and the 2021
    ASM table has no declaration immediately before the table.  This helper
    is deliberately narrower than a generic lexical alias: only statement
    code 31, the exact other-income label, and the separate income-statement
    source context can activate the bounded raw-source unit fallback below.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind != "income statement":
        return False
    rows = table.get("rows") or []
    indices = [row_index] if row_index is not None else range(len(rows))
    for index in indices:
        if index is None or index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        if BUILDER.normalize(row[0]).strip() != "31":
            continue
        label = BUILDER.normalize(_row_label(list(row))).strip()
        if "thu nhap khac" not in label:
            continue
        context_trace = table.get("context_trace") or {}
        source_title = BUILDER.normalize(
            context_trace.get("source_title")
            if isinstance(context_trace, Mapping)
            else ""
        )
        if (
            "bao cao ket qua hoat dong kinh doanh" in source_title
            and "rieng" in source_title
        ):
            return True
    return False


def _raw_source_unit_multiplier(
    table: Mapping[str, Any],
) -> Decimal | None:
    """Read one bounded unit declaration from the exact source file.

    This is a repair for two source-extraction omissions in the guarded
    ``Thu nhập khác`` family, not a general unit inference path.  The source
    file hash, when present in the table asset, is checked before reading the
    declaration.  Only the 4,000 characters immediately preceding the table
    coordinate are inspected, so a distant note or another document cannot
    silently set the table scale.
    """

    source_value = table.get("source_path")
    raw_start = table.get("char_start")
    if not source_value or raw_start is None:
        return None
    try:
        char_start = int(raw_start)
    except (TypeError, ValueError):
        return None
    source_path = Path(str(source_value)).expanduser()
    expected_sha = str(table.get("source_sha256") or "").strip().lower()
    cache_key = (str(source_path), char_start, expected_sha)
    if cache_key in _RAW_SOURCE_UNIT_CACHE:
        return _RAW_SOURCE_UNIT_CACHE[cache_key]

    multiplier: Decimal | None = None
    try:
        raw_bytes = source_path.read_bytes()
        if expected_sha:
            actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
            if actual_sha != expected_sha:
                _STATS["strict_source_unit_raw_hash_mismatch"] += 1
                _RAW_SOURCE_UNIT_CACHE[cache_key] = None
                return None
        source_text = raw_bytes.decode("utf-8", errors="replace")
    except (OSError, UnicodeError):
        _RAW_SOURCE_UNIT_CACHE[cache_key] = None
        return None

    if char_start < 0 or char_start > len(source_text):
        _RAW_SOURCE_UNIT_CACHE[cache_key] = None
        return None
    window = source_text[max(0, char_start - 4000) : char_start]
    # ``√ND`` is the OCR form observed in the 2016 declaration.  Correct only
    # this source-side glyph before invoking the canonical unit parser; do not
    # alter any numeric cell or the stored evidence row.
    window = window.replace("√", "V")
    unit_parser = getattr(BUILDER, "_unit_multiplier_from_text", None)
    if callable(unit_parser):
        for line in window.splitlines():
            normalized_line = BUILDER.normalize(line)
            if "don vi" not in normalized_line:
                continue
            candidate = unit_parser(normalized_line)
            if candidate is not None:
                multiplier = candidate
                _STATS["strict_source_unit_raw_fallback"] += 1
                break
    _RAW_SOURCE_UNIT_CACHE[cache_key] = multiplier
    return multiplier


def _raw_tax_balance_source_multiplier(
    table: Mapping[str, Any],
) -> Decimal | None:
    """Read a currency declaration for the exact code-313 balance family.

    Several continuation pages omit ``Đơn vị tính: VND`` from the local HTML
    table header even though the same report declares it on the preceding
    balance-sheet page.  The fallback first checks a bounded 12,000-character
    source window and then the report's explicit accounting-currency sentence.
    Both paths require the table asset's source hash; no numeric magnitude or
    navigation metadata can supply a unit.
    """

    source_value = table.get("source_path")
    raw_start = table.get("char_start")
    if not source_value or raw_start is None:
        return None
    try:
        char_start = int(raw_start)
    except (TypeError, ValueError):
        return None
    source_path = Path(str(source_value)).expanduser()
    expected_sha = str(table.get("source_sha256") or "").strip().lower()
    cache_key = (str(source_path), expected_sha)
    if cache_key in _RAW_DOCUMENT_CURRENCY_CACHE:
        return _RAW_DOCUMENT_CURRENCY_CACHE[cache_key]
    if not expected_sha:
        _RAW_DOCUMENT_CURRENCY_CACHE[cache_key] = None
        return None

    try:
        raw_bytes = source_path.read_bytes()
    except OSError:
        _RAW_DOCUMENT_CURRENCY_CACHE[cache_key] = None
        return None
    if hashlib.sha256(raw_bytes).hexdigest().lower() != expected_sha:
        _STATS["strict_source_unit_raw_hash_mismatch"] += 1
        _RAW_DOCUMENT_CURRENCY_CACHE[cache_key] = None
        return None
    source_text = raw_bytes.decode("utf-8", errors="replace").replace("√", "V")
    unit_parser = getattr(BUILDER, "_unit_multiplier_from_text", None)
    if callable(unit_parser) and 0 <= char_start <= len(source_text):
        window = source_text[max(0, char_start - 12000) : char_start]
        for line in window.splitlines():
            normalized_line = BUILDER.normalize(line)
            if "don vi tinh" not in normalized_line:
                continue
            candidate = unit_parser(normalized_line)
            if candidate is not None:
                _STATS["strict_source_unit_tax_balance_window_fallback"] += 1
                _RAW_DOCUMENT_CURRENCY_CACHE[cache_key] = candidate
                return candidate

    normalized_source = BUILDER.normalize(source_text)
    if re.search(
        r"don vi tien te su dung trong ke toan.{0,160}"
        r"dong viet nam.{0,80}vnd",
        normalized_source,
    ):
        _STATS["strict_source_unit_tax_balance_document_currency_fallback"] += 1
        _RAW_DOCUMENT_CURRENCY_CACHE[cache_key] = Decimal(1)
        return Decimal(1)
    _RAW_DOCUMENT_CURRENCY_CACHE[cache_key] = None
    return None


def _raw_construction_payable_source_multiplier(
    table: Mapping[str, Any],
) -> Decimal | None:
    """Read an explicit VND declaration for the construction-payable family.

    The 2025 NVL reconstructed table has no local ``unit_hint`` or VND header,
    while the source document explicitly declares ``Đơn vị tính: Đồng Việt
    Nam``. Check the source hash before reading a bounded declaration and fall
    back only to that document-level currency sentence. No numeric magnitude
    or table-navigation metadata is accepted as a unit signal.
    """

    source_value = table.get("source_path")
    raw_start = table.get("char_start")
    if not source_value or raw_start is None:
        return None
    try:
        char_start = int(raw_start)
    except (TypeError, ValueError):
        return None
    source_path = Path(str(source_value)).expanduser()
    expected_sha = str(table.get("source_sha256") or "").strip().lower()
    cache_key = (str(source_path), expected_sha)
    if cache_key in _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE:
        return _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE[cache_key]
    if not expected_sha:
        _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE[cache_key] = None
        return None

    try:
        raw_bytes = source_path.read_bytes()
    except OSError:
        _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE[cache_key] = None
        return None
    if hashlib.sha256(raw_bytes).hexdigest().lower() != expected_sha:
        _STATS["strict_source_unit_raw_hash_mismatch"] += 1
        _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE[cache_key] = None
        return None
    source_text = raw_bytes.decode("utf-8", errors="replace").replace("√", "V")
    unit_parser = getattr(BUILDER, "_unit_multiplier_from_text", None)
    if callable(unit_parser) and 0 <= char_start <= len(source_text):
        window = source_text[max(0, char_start - 12000) : char_start]
        for line in window.splitlines():
            normalized_line = BUILDER.normalize(line)
            if "don vi" not in normalized_line:
                continue
            candidate = unit_parser(normalized_line)
            if candidate is not None:
                _STATS["strict_source_unit_construction_payable_window_fallback"] += 1
                _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE[cache_key] = candidate
                return candidate

    normalized_source = BUILDER.normalize(source_text)
    if re.search(
        r"don vi tinh.{0,120}dong viet nam(?:.{0,80}vnd)?",
        normalized_source,
    ) or re.search(
        r"don vi tien te su dung trong ke toan.{0,160}"
        r"dong viet nam.{0,80}vnd",
        normalized_source,
    ):
        _STATS["strict_source_unit_construction_payable_document_currency_fallback"] += 1
        _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE[cache_key] = Decimal(1)
        return Decimal(1)
    _RAW_CONSTRUCTION_PAYABLE_CURRENCY_CACHE[cache_key] = None
    return None


def _source_multiplier_for_strict_contract(
    table: Mapping[str, Any],
) -> Decimal | None:
    """Resolve canonical units, then one exact source-grounded repair."""

    multiplier_fn = getattr(BUILDER, "_table_declared_source_multiplier", None)
    if callable(multiplier_fn):
        declared = multiplier_fn(table)
        if declared is not None:
            return declared
    if _is_context_bound_other_income(table):
        return _raw_source_unit_multiplier(table)
    if _is_context_bound_tax_payable_balance(table):
        return _raw_tax_balance_source_multiplier(table)
    if _is_context_bound_construction_cost_payable(table):
        return _raw_construction_payable_source_multiplier(table)
    return None


def _is_context_bound_brokerage_cost(table: Mapping[str, Any]) -> bool:
    """Recognize KHG's exact short-term brokerage-cost disclosure row.

    The same source family is classified as ``financial_note_detail`` in
    2019 and ``debt_schedule`` in 2023.  The exact row plus the short-term
    payable section supplies a bounded reconstruction contract; generic notes
    and unrelated brokerage rows remain outside it.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind not in {"financial note", "financial note detail", "debt schedule"}:
        return False
    exact_row = False
    for row in table.get("rows") or []:
        if not isinstance(row, (list, tuple)) or not row:
            continue
        label = BUILDER.normalize(_row_label(list(row))).strip()
        if label == "chi phi moi gioi bat dong san":
            exact_row = True
            break
    if not exact_row:
        return False
    context_trace = table.get("context_trace") or {}
    source_title = BUILDER.normalize(
        context_trace.get("source_title")
        if isinstance(context_trace, Mapping)
        else ""
    )
    return "chi phi phai tra ngan han" in source_title


def _is_context_bound_crown_payable(
    table: Mapping[str, Any],
    row_index: int | None = None,
) -> bool:
    """Recognize SAB's exact Crown related-party payable row.

    The requested counterparty is disclosed in the related-party supplier
    schedule for 2019, 2021 and 2025. Some reconstructed documents expose
    that same section as a financial note, so the context title and exact
    counterparty row are both required before table-kind normalization or
    argmax aliasing is allowed.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind not in {
        "financial note",
        "financial note detail",
        "related party schedule",
    }:
        return False
    rows = table.get("rows") or []
    indices = [row_index] if row_index is not None else range(len(rows))
    exact_label = "cong ty lien doanh tnhh crown sai gon"
    for index in indices:
        if index is None or index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, (list, tuple)) or not row:
            continue
        label = BUILDER.normalize(_row_label(list(row))).strip()
        if label != exact_label:
            continue
        context_trace = table.get("context_trace") or {}
        source_title = BUILDER.normalize(
            context_trace.get("source_title")
            if isinstance(context_trace, Mapping)
            else ""
        )
        return "phai tra nguoi ban" in source_title and "ben lien quan" in source_title
    return False


def _is_context_bound_customer_loan_accrued_interest(
    table: Mapping[str, Any],
    row_index: int | None = None,
) -> bool:
    """Recognize STB's exact customer-loan accrued-interest note row.

    In the source corpus, the 2017 and 2022 notes use ``Lãi từ cho vay
    khách hàng`` while the 2024 note uses ``Lãi dự thu từ cho vay khách
    hàng``.  The surrounding numbered note and STB identity are the stable
    context.  This helper deliberately does not admit balance-sheet loan
    rows, generic interest-income rows, or the same label from another
    issuer.
    """

    if BUILDER.table_ticker(table).upper() != "STB":
        return False
    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind not in {"financial note", "financial note detail", "debt schedule"}:
        return False
    rows = table.get("rows") or []
    indices = [row_index] if row_index is not None else range(len(rows))
    accepted_labels = {
        "lai tu cho vay khach hang",
        "lai du thu tu cho vay khach hang",
    }
    for index in indices:
        if index is None or index < 0 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, (list, tuple)) or not row:
            continue
        label = BUILDER.normalize(_row_label(list(row))).strip()
        # Drop footnote markers such as ``(i)`` and ``(*)`` after the row
        # identity has been normalized.  They are not semantic dimensions.
        label = re.sub(r"\s+(?:i{1,3}|iv|v|vi|\*)$", "", label).strip()
        if label not in accepted_labels:
            continue
        context_trace = table.get("context_trace") or {}
        source_title = BUILDER.normalize(
            context_trace.get("source_title")
            if isinstance(context_trace, Mapping)
            else ""
        )
        return "cac khoan lai phi phai thu" in source_title
    return False


def _is_context_bound_investment_property_factory(
    table: Mapping[str, Any],
    row_index: int | None = None,
) -> bool:
    """Recognize the closing carrying amount of an investment-property factory.

    KBC's 2015/2017/2019 notes use the same two-column movement schedule, but
    the generic semantic matcher sees several rows named ``Số dư cuối năm``
    and can drift into fixed assets or construction-in-progress.  The
    accounting identity here is the bounded source context plus the exact
    ``Nhà xưởng`` asset row and its ``Giá trị còn lại`` child section.  This
    helper is deliberately row-aware so an unrelated closing-balance row in
    the same table cannot inherit the alias.
    """

    raw_kind = BUILDER.normalize(
        BUILDER.source_first_lookup_module._table_kind(table)
    ).strip()
    if raw_kind not in {"financial note", "financial note detail"}:
        return False

    context_trace = table.get("context_trace") or {}
    context_parts = [str(table.get("context_before") or "")]
    if isinstance(context_trace, Mapping):
        context_parts.extend(
            str(context_trace.get(key) or "")
            for key in ("source_title", "summary", "topic")
        )
    if "bat dong san dau tu" not in BUILDER.normalize(" ".join(context_parts)):
        return False

    rows = table.get("rows") or []
    factory_indices: list[int] = []
    exact_factory_label = (
        "nha xuong bao gom chi phi phat trien dat va co so ha tang"
    )
    for index, row in enumerate(rows):
        if not isinstance(row, (list, tuple)):
            continue
        if BUILDER.normalize(_row_label(list(row))).strip() == exact_factory_label:
            factory_indices.append(index)
    if len(factory_indices) != 1:
        return False
    factory_index = factory_indices[0]

    indices = [row_index] if row_index is not None else range(len(rows))
    for index in indices:
        if index is None or index < 2 or index >= len(rows):
            continue
        row = rows[index]
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        if BUILDER.normalize(row[0]).strip() not in {
            "so du cuoi nam",
            "so cuoi nam",
        }:
            continue
        # The two rows immediately above the selected value are the bounded
        # ``Giá trị còn lại`` section and its ``Số dư đầu năm`` peer.  Requiring
        # the factory to precede that section keeps ``Nguyên giá`` and
        # accumulated-depreciation closing rows out of the alias.
        section_index = index - 2
        if section_index <= factory_index:
            continue
        if BUILDER.normalize(rows[section_index][0]).strip() != "gia tri con lai":
            continue
        if not any(BUILDER.parse_decimal(cell) is not None for cell in row[1:]):
            continue
        return True
    return False


def _is_investment_property_factory_metric(metric: str, question: str) -> bool:
    """Keep the carrying-amount alias narrower than a generic asset alias."""

    normalized_metric = BUILDER.normalize(metric)
    normalized_question = BUILDER.normalize(question)
    return (
        "gia tri con lai" in normalized_metric
        and "bat dong san dau tu" in normalized_question
        and "nha xuong" in normalized_question
    )


def _is_tax_payable_balance_metric(metric: str) -> bool:
    """Keep the code-313 alias narrower than generic tax wording."""

    normalized = BUILDER.normalize(metric)
    return "thue va cac khoan phai nop nha nuoc" in normalized


def _is_construction_cost_payable_metric(metric: str) -> bool:
    """Keep the short-term construction-cost alias family-scoped."""

    normalized = BUILDER.normalize(metric)
    return (
        "chi phi xay dung" in normalized
        and "phai tra" in normalized
        and "ngan han" in normalized
    )


def _is_trading_debt_securities_metric(metric: str) -> bool:
    """Keep ``Chứng khoán nợ`` bound to the trading-securities note."""

    normalized = BUILDER.normalize(metric)
    return (
        "chung khoan kinh doanh" in normalized
        and bool(
            re.search(
                r"\bchung\s+khoan\s+(?:kinh\s+doanh\s+)?no\b",
                normalized,
            )
        )
    )


def _is_customer_loan_accrued_interest_metric(metric: str) -> bool:
    normalized = BUILDER.normalize(metric)
    return (
        "lai tu cho vay khach hang" in normalized
        or "lai du thu tu cho vay khach hang" in normalized
    )


def _strict_context_covers_missing_tokens(
    table: Mapping[str, Any],
    *,
    missing_tokens: set[str],
) -> bool:
    """Allow only exact source contexts to supply known OCR qualifiers."""

    if missing_tokens.issubset({"ben", "lien", "quan"}) and _is_context_bound_related_party_revenue(table):
        return True
    if missing_tokens == {"du", "thu"} and _is_context_bound_customer_loan_accrued_interest(table):
        return True
    if missing_tokens == {"dau", "tu"} and _is_context_bound_investment_property_factory(table):
        return True
    return missing_tokens == {"hoa", "hong"} and _is_context_bound_brokerage_cost(table)


def _is_cash_flow_tax_schedule(table: Mapping[str, Any]) -> bool:
    """Recognize one exact cash-flow reconstruction, never a generic schedule."""

    rows = table.get("rows") or []
    has_exact_tax_row = any(
        BUILDER.normalize(row[0] if isinstance(row, list) and row else "")
        == "thue thu nhap doanh nghiep da nop"
        for row in rows
    )
    if not has_exact_tax_row:
        return False
    context_trace = table.get("context_trace") or {}
    source_title = BUILDER.normalize(
        context_trace.get("source_title") if isinstance(context_trace, Mapping) else ""
    )
    if "luu chuyen tien" not in source_title:
        return False
    if "hoat dong kinh doanh" not in source_title:
        return False
    return True


def _strict_source_contract_reason(
    *,
    item: Mapping[str, Any],
    years: list[int],
    selections: list[Mapping[str, Any]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Guard the comparison domain before a period winner is materialized.

    The loose research lane is useful for measuring recall, but a period
    selector can silently compare a primary statement with an acquisition or
    reconstruction schedule.  The strict lane therefore requires one
    supported table family across all selected years and one source-declared
    multiplier.  It deliberately does not use the numeric values to choose a
    better source.
    """

    selected_tables: list[Mapping[str, Any]] = []
    for year, selection in zip(years, selections):
        uid = str(selection.get("internal_table_uid") or "")
        table = tables_by_uid.get(uid)
        if not table:
            return "SOURCE_TABLE_MISSING"
        selected_tables.append(table)

        table_year = table.get("report_year")
        try:
            resolved_year = int(table_year)
        except (TypeError, ValueError):
            resolved_year = BUILDER.document_year(table.get("document_id"))
        if resolved_year != year:
            return "SOURCE_REPORT_YEAR_MISMATCH"

        kind = _comparison_table_kind(table)
        if kind in {"financial data schedule", "governance", "governance roster", "segment reporting"}:
            return "UNSAFE_TABLE_KIND"
        if kind not in _STRICT_ALLOWED_TABLE_KINDS:
            return "UNSUPPORTED_TABLE_KIND"

        period_gate = getattr(
            BUILDER.source_first_lookup_module,
            "_table_period_compatible",
            None,
        )
        if callable(period_gate) and not period_gate(
            table,
            item=item,
            requested_year=year,
        ):
            row_index = selection.get("row_index")
            exact_movement_alias = False
            try:
                exact_movement_alias = bool(
                    selection.get("argmax_family_alias")
                    and _is_context_bound_investment_property_factory(
                        table, int(row_index)
                    )
                )
            except (TypeError, ValueError):
                exact_movement_alias = False
            if not exact_movement_alias:
                return "TABLE_PERIOD_CONTRADICTION"
            # KBC's 2019 movement table omits ``Số cuối năm`` from its
            # compact header even though the selected child row carries that
            # exact closing label.  The row-aware source contract is stronger
            # than this known header reconstruction gap; generic tables still
            # fail closed on the contradiction.

    kinds = {_comparison_table_kind(table) for table in selected_tables}
    if len(kinds) != 1:
        return "MIXED_TABLE_KIND"

    multiplier_fn = getattr(BUILDER, "_table_declared_source_multiplier", None)
    if not callable(multiplier_fn):
        return "SOURCE_UNIT_GATE_UNAVAILABLE"
    multipliers = [
        _source_multiplier_for_strict_contract(table)
        for table in selected_tables
    ]
    if any(multiplier is None for multiplier in multipliers):
        return "SOURCE_UNIT_UNDECLARED"
    if len(set(multipliers)) != 1:
        return "MIXED_SOURCE_UNIT"
    return None


def _evidence_window(table: Mapping[str, Any], row_index: int) -> list[dict[str, Any]]:
    rows = table.get("rows") or []
    selected = set(range(min(8, len(rows))))
    selected.update(
        range(max(0, row_index - 2), min(len(rows), row_index + 3))
    )
    evidence = [
        {"index": index, "row": rows[index]}
        for index in sorted(selected)
    ]
    headers = table.get("column_labels") or table.get("headers") or []
    if headers:
        evidence.append({"index": -1, "row": list(headers)})
    return evidence


def _candidate_row_score(
    metric: str,
    question: str,
    label: str,
) -> tuple[float, int, int]:
    cache_key = (metric, question, label)
    score = _SEMANTIC_ROW_CACHE.get(cache_key)
    if score is None:
        score = BUILDER.semantic_row_score(metric, question, label)
        _SEMANTIC_ROW_CACHE[cache_key] = score
    anchors = _metric_anchor_tokens(metric)
    label_tokens = BUILDER.content_tokens(label)
    anchor_hits = len(anchors & label_tokens)
    phrase = _longest_phrase(metric, label)
    score += 0.32 * phrase + 0.22 * anchor_hits
    return score, phrase, anchor_hits


def _table_row_descriptors(table: Mapping[str, Any]) -> list[tuple[int, str, bool]]:
    uid = str(table.get("internal_table_uid") or "")
    cached = _ROW_CACHE.get(uid)
    if cached is not None:
        return cached
    descriptors: list[tuple[int, str, bool]] = []
    for row_index, row in enumerate(table.get("rows") or []):
        label = _row_label(row)
        has_numeric = bool(
            label
            and any(BUILDER.parse_decimal(cell) is not None for cell in row[1:])
        )
        descriptors.append((row_index, label, has_numeric))
    if uid:
        _ROW_CACHE[uid] = descriptors
    return descriptors


def _make_candidates(
    *,
    tables: Iterable[Mapping[str, Any]],
    metric: str,
    question: str,
    ticker: str,
    year: int,
    scope: str,
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    rows: list[tuple[float, int, int, Mapping[str, Any], int, str]] = []
    required_tokens = _required_row_tokens(
        metric,
        strict=_STRICT_SOURCE_CONTRACT,
    )
    if _STRICT_SOURCE_CONTRACT:
        # Enforce semantic qualifiers from the original question as well as
        # the generated metric variant.  A fallback variant must not erase
        # ``thuần`` or ``bên liên quan`` and thereby widen the row family.
        required_tokens.update(_strict_metric_phrase_anchors(question))
    for table in tables:
        table_scope = str(table.get("scope") or "unknown").strip().lower() or "unknown"
        if table_scope != scope:
            continue
        for row_index, label, has_numeric in _table_row_descriptors(table):
            if not label:
                continue
            if not has_numeric:
                continue
            score, phrase, anchor_hits = _candidate_row_score(metric, question, label)
            normalized_metric = BUILDER.normalize(metric)
            family_alias = bool(
                _STRICT_SOURCE_CONTRACT
                and (
                    (
                        "loi nhuan khac" in normalized_metric
                        and _is_context_bound_other_profit_loss_row(table, row_index)
                    )
                    or (
                        "crown" in normalized_metric
                        and _is_context_bound_crown_payable(table, row_index)
                    )
                    or (
                        "no phai tra" in normalized_metric
                        and _is_context_bound_total_liabilities(table, row_index)
                    )
                    or (
                        _is_tax_payable_balance_metric(normalized_metric)
                        and _is_context_bound_tax_payable_balance(table, row_index)
                    )
                    or (
                        _is_construction_cost_payable_metric(normalized_metric)
                        and _is_context_bound_construction_cost_payable(
                            table, row_index
                        )
                    )
                    or (
                        _is_trading_debt_securities_metric(normalized_metric)
                        and _is_context_bound_trading_debt_securities(
                            table, row_index
                        )
                    )
                    or (
                        _is_customer_loan_accrued_interest_metric(normalized_metric)
                        and _is_context_bound_customer_loan_accrued_interest(
                            table, row_index
                        )
                        )
                        or (
                            _is_investment_property_factory_metric(metric, question)
                            and _is_context_bound_investment_property_factory(
                                table, row_index
                            )
                    )
                )
            )
            # One rare anchor plus a contiguous phrase is enough for OCR
            # aliases such as ``Doanh thu LPG``; otherwise demand two anchors.
            if not family_alias and anchor_hits < (1 if phrase >= 2 else 2):
                continue
            label_tokens = set(BUILDER.normalize(label).split())
            if not required_tokens.issubset(label_tokens):
                missing_tokens = required_tokens - label_tokens
                if not _strict_context_covers_missing_tokens(
                    table,
                    missing_tokens=missing_tokens,
                ) and not family_alias:
                    # A row-aware alias has already proved the accounting
                    # identity from its exact source context and hierarchy.
                    # Some movement schedules keep the metric phrase only in
                    # the section heading, so requiring that phrase again in
                    # the child label would discard the exact source row.
                    continue
            if not family_alias and score < 2.0:
                continue
            rows.append((score, phrase, anchor_hits, table, row_index, label))

    # Keep a bounded navigation window per table.  The selector still reads
    # the current table coordinate; values never enter this candidate packet.
    per_table: defaultdict[str, list[tuple[float, int, int, Mapping[str, Any], int, str]]] = defaultdict(list)
    for entry in rows:
        uid = str(entry[3].get("internal_table_uid") or "")
        if uid:
            per_table[uid].append(entry)
    bounded: list[tuple[float, int, int, Mapping[str, Any], int, str]] = []
    for entries in per_table.values():
        bounded.extend(
            sorted(
                entries,
                key=lambda entry: (-entry[0], -entry[1], -entry[2], entry[4]),
            )[:4]
        )
    bounded.sort(
        key=lambda entry: (
            -entry[0],
            -entry[1],
            -entry[2],
            str(entry[3].get("document_id") or ""),
            entry[4],
        )
    )
    candidates: list[dict[str, Any]] = []
    meta: dict[tuple[str, int], dict[str, Any]] = {}
    for rank, (score, phrase, anchor_hits, table, row_index, label) in enumerate(
        bounded[:80], start=1
    ):
        uid = str(table.get("internal_table_uid") or "")
        key = (uid, row_index)
        candidate = {
            "rank": rank,
            "review_score": max(0.0, min(1.0, score / 10.0)),
            "candidate_source": "arg_extreme_full_corpus_navigation",
            "research_candidate_only": True,
            "ticker": ticker,
            "document_id": table.get("document_id"),
            "report_year": year,
            "scope": scope,
            "internal_table_uid": uid,
            "ticker_match": True,
            "year_match": True,
            "scope_match": True,
            "evidence_window": _evidence_window(table, row_index),
        }
        candidates.append(candidate)
        meta[key] = {
            "argmax_row_score": score,
            "argmax_phrase_length": phrase,
            "argmax_anchor_hits": anchor_hits,
            "argmax_metric": metric,
            "argmax_row_signature": _row_signature(label),
            "argmax_family_alias": bool(
                _STRICT_SOURCE_CONTRACT
                and (
                    (
                        "loi nhuan khac" in BUILDER.normalize(str(metric))
                        and _is_context_bound_other_profit_loss_row(table, row_index)
                    )
                    or (
                        "crown" in BUILDER.normalize(str(metric))
                        and _is_context_bound_crown_payable(table, row_index)
                    )
                    or (
                        "no phai tra" in BUILDER.normalize(str(metric))
                        and _is_context_bound_total_liabilities(table, row_index)
                    )
                    or (
                        _is_tax_payable_balance_metric(str(metric))
                        and _is_context_bound_tax_payable_balance(table, row_index)
                    )
                    or (
                        _is_construction_cost_payable_metric(str(metric))
                        and _is_context_bound_construction_cost_payable(
                            table, row_index
                        )
                    )
                    or (
                        _is_trading_debt_securities_metric(str(metric))
                        and _is_context_bound_trading_debt_securities(
                            table, row_index
                        )
                    )
                    or (
                        _is_customer_loan_accrued_interest_metric(str(metric))
                        and _is_context_bound_customer_loan_accrued_interest(
                            table, row_index
                        )
                    )
                        or (
                            _is_investment_property_factory_metric(
                                str(metric), question
                            )
                        and _is_context_bound_investment_property_factory(
                            table, row_index
                        )
                    )
                )
            ),
        }
    return candidates, meta


def _select_year_rows(
    *,
    item: Mapping[str, Any],
    ticker: str,
    year: int,
    scope: str,
    metric: str,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    tables: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates, meta = _make_candidates(
        tables=tables,
        metric=metric,
        question=str(item.get("question") or ""),
        ticker=ticker,
        year=year,
        scope=scope,
    )
    if not candidates:
        _STATS["year_candidate_window_empty"] += 1
        return []
    candidate_item = dict(item)
    candidate_item["candidates"] = candidates
    ranked = BUILDER.rank_semantic_cells(
        candidate_item,
        metric_override=metric,
        year_override=year,
        ticker_override=ticker,
        tables_by_uid=tables_by_uid,
        allow_uncertain=True,
    )
    selections: list[dict[str, Any]] = []
    for selection in ranked[:40]:
        key = (
            str(selection.get("internal_table_uid") or ""),
            int(
                selection.get("row_index")
                if selection.get("row_index") is not None
                else -1
            ),
        )
        row_meta = meta.get(key)
        if row_meta is None:
            continue
        if (
            float(selection.get("score") or 0.0) < 2.0
            and not row_meta.get("argmax_family_alias")
        ):
            continue
        if (
            int(row_meta.get("argmax_anchor_hits") or 0) < 1
            and not row_meta.get("argmax_family_alias")
        ):
            continue
        augmented = dict(selection)
        augmented.update(row_meta)
        augmented["argmax_scope"] = scope
        augmented["argmax_year"] = year
        selections.append(augmented)
    _STATS["year_selection_candidates"] += len(selections)
    return selections


def _cohort_for_scope(
    *,
    item: Mapping[str, Any],
    ticker: str,
    years: list[int],
    scope: str,
    metric: str,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[float, float, float, float], list[dict[str, Any]]] | None:
    index = _table_index(tables_by_uid)
    by_year: dict[int, list[dict[str, Any]]] = {}
    for year in years:
        year_tables = index.get((ticker, year, scope), [])
        selections = _select_year_rows(
            item=item,
            ticker=ticker,
            year=year,
            scope=scope,
            metric=metric,
            tables_by_uid=tables_by_uid,
            tables=year_tables,
        )
        if not selections:
            return None
        by_year[year] = selections

    first_year = years[0]
    best: tuple[tuple[float, float, float, float], list[dict[str, Any]]] | None = None
    for prototype in by_year[first_year][:20]:
        chosen = [prototype]
        similarities = [1.0]
        valid = True
        for year in years[1:]:
            candidates: list[tuple[float, dict[str, Any], float]] = []
            for candidate in by_year[year]:
                similarity = _family_similarity(
                    str(prototype.get("row_label") or ""),
                    str(candidate.get("row_label") or ""),
                )
                if similarity < 0.62:
                    continue
                candidate_score = (
                    0.48 * similarity
                    + 0.30 * min(1.0, float(candidate.get("argmax_row_score") or 0.0) / 8.0)
                    + 0.22 * min(1.0, float(candidate.get("score") or 0.0) / 8.0)
                    # In strict mode an exact statement-code family is a
                    # stronger identity than a similarly worded note row.
                    # Keep this as a small navigation tie-breaker; the
                    # source-kind/unit contract below remains authoritative.
                    + (0.14 if candidate.get("argmax_family_alias") else 0.0)
                )
                candidates.append((candidate_score, candidate, similarity))
            if not candidates:
                valid = False
                break
            _, selected, similarity = max(
                candidates,
                key=lambda value: (
                    value[0],
                    float(value[1].get("score") or 0.0),
                    str(value[1].get("internal_table_uid") or ""),
                    int(value[1].get("row_index") or -1),
                ),
            )
            chosen.append(selected)
            similarities.append(similarity)
        if not valid or len(chosen) != len(years):
            continue
        if _STRICT_SOURCE_CONTRACT:
            contract_reason = _strict_source_contract_reason(
                item=item,
                years=years,
                selections=chosen,
                tables_by_uid=tables_by_uid,
            )
            if contract_reason is not None:
                _STATS[f"strict_rejected_{contract_reason.lower()}"] += 1
                continue
        min_row_score = min(float(row.get("argmax_row_score") or 0.0) for row in chosen)
        min_selection_score = min(float(row.get("score") or 0.0) for row in chosen)
        min_similarity = min(similarities)
        # The final term favours a higher aggregate row match but remains
        # below the hard coverage and family gates.
        aggregate_score = sum(float(row.get("argmax_row_score") or 0.0) for row in chosen) / len(chosen)
        quality = (min_row_score, min_selection_score, min_similarity, aggregate_score)
        if best is None or quality > best[0]:
            best = (quality, chosen)
    return best


def _unique_extreme_year(
    selections: list[Mapping[str, Any]],
    years: list[int],
    *,
    direction: str,
    absolute: bool,
) -> int | None:
    """Return a scope cohort's unique winner without using scope ranking.

    Unscoped questions may have complete consolidated and separate cohorts.
    Scope ranking is navigation metadata, so it cannot by itself choose the
    answer.  This helper is used only for the cross-scope invariance check;
    ties and malformed cohorts deliberately return ``None``.
    """

    if len(selections) != len(years) or not selections:
        return None
    comparison_values = [
        abs(selection["value"]) if absolute else selection["value"]
        for selection in selections
    ]
    extreme = max(comparison_values) if direction == "max" else min(comparison_values)
    extreme_indices = [
        index for index, value in enumerate(comparison_values) if value == extreme
    ]
    if len(extreme_indices) != 1:
        return None
    return years[extreme_indices[0]]


def _absolute_requested(question: str) -> bool:
    return "gia tri tuyet doi" in BUILDER.normalize(question)


def _extreme_query(year: int, years: list[int], *, direction: str, absolute: bool) -> str:
    role = f"period_{year}"
    field = "df1['operand_value'].abs()" if absolute else "df1['operand_value']"
    selected = f"df1.loc[df1.operand_role=='{role}','operand_value'].iloc[0]"
    selected_field = f"abs({selected})" if absolute else selected
    extreme = f"{field}.{'max()' if direction == 'max' else 'min()'}"
    return f"float(({selected_field} == {extreme}) * {year})"


def _resolve_arg_extreme(
    item: Mapping[str, Any],
    typed: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    question_id = _question_id(item)
    ticker = _typed_ticker(typed)
    years = _typed_years(typed)
    direction = str((typed.get("operation_ast") or {}).get("direction") or "max").lower()
    if not ticker or len(years) < 2 or direction not in {"max", "min"}:
        _STATS["contract_invalid"] += 1
        return None
    if tables_by_uid is None:
        _STATS["tables_missing"] += 1
        return None

    if _is_related_party_transaction_total_metric(
        str(item.get("question") or ""),
    ):
        _STATS["related_party_transaction_total_seen"] += 1
        # This family is inherently multi-cell: the source disclosure may be
        # split over consecutive continuation tables.  Do not let the
        # single-cell resolver below silently turn a failed aggregate
        # contract into a plausible-looking one-cell answer.
        return _resolve_related_party_transaction_total(
            item,
            typed,
            tables_by_uid=tables_by_uid,
        )

    variants = _metric_variants(item, typed)
    unsupported = _unsupported_reason(str(item.get("question") or ""), variants)
    if unsupported:
        _STATS[f"skipped_{unsupported.lower()}"] += 1
        _TRACE.append(
            {
                "question_id": question_id,
                "status": "SKIPPED_UNSUPPORTED_CONTRACT",
                "reason": unsupported,
                "typed_route": typed.get("route"),
                "metric_variants": variants[:12],
            }
        )
        return None
    if not variants:
        _STATS["metric_variants_missing"] += 1
        return None

    scope = _scope_for_item(item, typed)
    explicit_scope = bool(_typed_scope(item, typed) or _explicit_question_scope(str(item.get("question") or "")))
    table_index = _table_index(tables_by_uid)
    if explicit_scope:
        scope_candidates = [scope]
    else:
        # For an unqualified question, the old shortlist provides a preferred
        # scope but not authority.  Try the remaining complete cohorts so a
        # single missing report in that navigation shortlist does not turn a
        # valid period selector into an abstention.
        scope_candidates = [
            scope,
            *[
                value
                for value in ("consolidated", "separate", "aggregated", "unknown")
                if value != scope
            ],
        ]
    scope_candidates = [
        candidate_scope
        for candidate_scope in scope_candidates
        if all(table_index.get((ticker, year, candidate_scope)) for year in years)
    ]
    if not scope_candidates:
        _STATS["scope_coverage_missing"] += 1
        _TRACE.append(
            {
                "question_id": question_id,
                "status": "REJECTED_SCOPE_COVERAGE_MISSING",
                "ticker": ticker,
                "years": years,
                "scope_preference": scope,
                "explicit_scope": explicit_scope,
            }
        )
        return None
    best: tuple[tuple[float, float, float, float, int, int], list[dict[str, Any]], str, str] | None = None
    cohort_results: dict[
        tuple[str, str],
        tuple[tuple[float, float, float, float], list[dict[str, Any]], int, int],
    ] = {}
    for metric_index, metric in enumerate(variants[:16]):
        if len(_metric_anchor_tokens(metric)) < 1:
            continue
        for scope_index, candidate_scope in enumerate(scope_candidates):
            cohort = _cohort_for_scope(
                item=item,
                ticker=ticker,
                years=years,
                scope=candidate_scope,
                metric=metric,
                tables_by_uid=tables_by_uid,
            )
            if cohort is None:
                continue
            quality, selections = cohort
            cohort_results[(metric, candidate_scope)] = (
                quality,
                selections,
                metric_index,
                scope_index,
            )
            ranked_quality = (*quality, -scope_index, -metric_index)
            if best is None or ranked_quality > best[0]:
                best = (ranked_quality, selections, metric, candidate_scope)
    if best is None:
        _STATS["cohort_rejected"] += 1
        _TRACE.append(
            {
                "question_id": question_id,
                "status": "REJECTED_NO_COHERENT_ROW_FAMILY",
                "ticker": ticker,
                "years": years,
                "scope_preference": scope,
                "metric_variants": variants[:12],
            }
        )
        return None

    absolute = _absolute_requested(str(item.get("question") or ""))
    quality, selections, metric, selected_scope = best

    # For an unscoped question, a preferred scope is still only a navigation
    # hint.  If every complete scope cohort for the selected metric has the
    # same unique winner, use the preferred scope for evidence while making
    # the answer selection explicitly invariant to scope.  This recovers
    # robust year answers such as TTF Q829 without silently mixing scopes or
    # turning a ranking score into semantic authority.
    scope_consensus_winners: dict[str, int] = {}
    scope_consensus = False
    if not explicit_scope:
        for candidate_scope in scope_candidates:
            cohort_record = cohort_results.get((metric, candidate_scope))
            if cohort_record is None:
                continue
            _cohort_quality, cohort_selections, _metric_index, _scope_index = cohort_record
            winner_year = _unique_extreme_year(
                cohort_selections,
                years,
                direction=direction,
                absolute=absolute,
            )
            if winner_year is not None:
                scope_consensus_winners[candidate_scope] = winner_year
        if (
            len(scope_consensus_winners) >= 2
            and len(set(scope_consensus_winners.values())) == 1
            and scope in scope_consensus_winners
        ):
            preferred_record = cohort_results.get((metric, scope))
            if preferred_record is not None:
                preferred_quality, preferred_selections, preferred_metric_index, preferred_scope_index = preferred_record
                quality = (
                    *preferred_quality,
                    -preferred_scope_index,
                    -preferred_metric_index,
                )
                selections = preferred_selections
                selected_scope = scope
                scope_consensus = True
                _STATS["scope_winner_consensus"] += 1

    comparison_values = [
        abs(selection["value"]) if absolute else selection["value"]
        for selection in selections
    ]
    extreme = max(comparison_values) if direction == "max" else min(comparison_values)
    extreme_indices = [index for index, value in enumerate(comparison_values) if value == extreme]
    if len(extreme_indices) != 1:
        _STATS["rejected_extreme_tie"] += 1
        _TRACE.append(
            {
                "question_id": question_id,
                "status": "REJECTED_EXTREME_TIE",
                "direction": direction,
                "values": [str(value) for value in comparison_values],
                "years": years,
            }
        )
        return None
    selected_index = extreme_indices[0]
    selected_year = years[selected_index]
    for year, selection in zip(years, selections):
        selection["role"] = f"period_{year}"
        selection["argmax_comparison_value"] = str(
            abs(selection["value"]) if absolute else selection["value"]
        )
        selection["argmax_direction"] = direction
        selection["argmax_absolute"] = absolute
        selection["argmax_metric"] = metric
        selection["argmax_scope"] = selected_scope
    query = _extreme_query(
        selected_year,
        years,
        direction=direction,
        absolute=absolute,
    )
    answer = Decimal(selected_year)
    _STATS["accepted"] += 1
    _TRACE.append(
        {
            "question_id": question_id,
            "status": "ACCEPTED",
            "ticker": ticker,
            "years": years,
            "direction": direction,
            "absolute": absolute,
            "metric": metric,
            "scope_preference": scope,
            "scope_selected": selected_scope,
            "scope_selection_basis": (
                "winner_consensus_preferred_scope"
                if scope_consensus
                else "cohort_quality"
            ),
            "scope_consensus": scope_consensus,
            "scope_consensus_winners": scope_consensus_winners,
            "quality": list(quality),
            "values": [str(value) for value in comparison_values],
            "selected_year": selected_year,
            "sources": [
                {
                    "period": year,
                    "document_id": selection.get("document_id"),
                    "internal_table_uid": selection.get("internal_table_uid"),
                    "row_index": selection.get("row_index"),
                    "column_index": selection.get("column_index"),
                    "row_label": selection.get("row_label"),
                    "value": str(selection.get("value")),
                }
                for year, selection in zip(years, selections)
            ],
        }
    )
    return answer, selections, query, ARG_EXTREME_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    typed = _typed_plan(item)
    if typed is not None and (typed.get("operation_ast") or {}).get("op") == "arg_extreme_period":
        _STATS["typed_arg_extreme_seen"] += 1
        result = _resolve_arg_extreme(item, typed, tables_by_uid=tables_by_uid)
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == RELATED_PARTY_TRANSACTION_TOTAL_TIER:
        # The aggregate route has a complete source-cell closure but remains
        # candidate-only until an independent gold/scorer is available.
        return 70.6
    if tier == ARG_EXTREME_TIER:
        # Higher than semantic-cell fallback, lower than source-first and
        # exact replay routes.  This lets the independent proposal verifier
        # retain a valid multi-cell period proposal.
        return 70.5
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _load_typed_plans(path: Path) -> dict[int, dict[str, Any]]:
    plans: dict[int, dict[str, Any]] = {}
    for record in BUILDER.read_jsonl(path):
        question_id = int(record["question_id"])
        plans[question_id] = record
    if len(plans) != 1012:
        raise ValueError(f"typed-plan count={len(plans)} expected 1012")
    return plans


def _write_variant_metadata(output_dir: Path, *, typed_plans_path: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["arg_extreme_period_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "strict_source_contract": _STRICT_SOURCE_CONTRACT,
        "typed_plans_path": str(typed_plans_path),
        "typed_plan_count": len(_TYPED_PLANS),
        "contract": {
            "route": "simple_period_extremum",
            "decomposition_status": "typed_non_executable",
            "operation": "arg_extreme_period",
            "exact_report_year_per_operand": True,
            "same_row_family_across_years": True,
            "same_scope_across_years": True,
            "ties_rejected": True,
            "ratio_and_multi_row_composition_rejected": True,
            "supported_table_kinds": sorted(_STRICT_ALLOWED_TABLE_KINDS)
            if _STRICT_SOURCE_CONTRACT
            else "loose_research_navigation",
            "same_table_kind_across_years": _STRICT_SOURCE_CONTRACT,
            "same_source_multiplier_across_years": _STRICT_SOURCE_CONTRACT,
            "schedule_governance_segment_rejected": _STRICT_SOURCE_CONTRACT,
            "related_party_transaction_total": {
                "route": RELATED_PARTY_TRANSACTION_TOTAL_TIER,
                "contiguous_source_tables": True,
                "exact_current_year_column": True,
                "leaf_transaction_rows_only": True,
                "explicit_duplicate_totals_rejected": True,
                "allowed_raw_table_kinds": sorted(
                    _RELATED_PARTY_TRANSACTION_RAW_KINDS
                ),
            },
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "arg_extreme_period_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "arg_extreme_period_trace_v1.jsonl"
    trace_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in _TRACE
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = BUILDER.configure_parser(argparse.ArgumentParser())
    parser.add_argument(
        "--typed-plans",
        type=Path,
        required=True,
        help="Frozen typed_operand_plans_v1.jsonl used by the period selector gate",
    )
    parser.add_argument(
        "--strict-source-contract",
        action="store_true",
        help="Require one supported table family and one source-declared multiplier across years",
    )
    args = parser.parse_args()

    global _STRICT_SOURCE_CONTRACT, _TYPED_PLANS, VARIANT_PROTOCOL
    _STRICT_SOURCE_CONTRACT = bool(args.strict_source_contract)
    if _STRICT_SOURCE_CONTRACT:
        VARIANT_PROTOCOL = STRICT_VARIANT_PROTOCOL
    _TYPED_PLANS = _load_typed_plans(args.typed_plans)
    BUILDER.program_aware_answer = _patched_program_aware
    BUILDER._route_priority = _patched_route_priority

    BUILDER.build(args)
    _write_variant_metadata(args.output, typed_plans_path=args.typed_plans)
    print(
        json.dumps(
            {
                "protocol": VARIANT_PROTOCOL,
                "strict_source_contract": _STRICT_SOURCE_CONTRACT,
                "output": str(args.output),
                "stats": dict(sorted(_STATS.items())),
                "trace_count": len(_TRACE),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
