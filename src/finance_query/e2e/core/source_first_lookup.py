"""Source-first exact-row resolution for direct financial lookups.

This module is deliberately narrower than a general semantic answerer.  It is
used for questions whose compiled operation is a single ``lookup``.  Retrieval
and learned ranking can miss the table that contains the answer, so the
resolver searches the already-frozen structured corpus by ``ticker + report
year`` and then applies three independent constraints:

* the requested metric must match a row label (including small OCR edits),
* the table function must fit the question's stock/flow intent, and
* the current table cell must be replayable through the caller's Decimal and
  period-column contracts.

The resolver returns a proposal with coordinates and provenance.  It is not a
certificate and must remain behind the normal submission verifier/policy.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any


PROTOCOL = "source_first_exact_row_v1"
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_UPPER_TICKER_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")

# These are context words, not financial metric words.  In particular, do
# not remove ``ngan``, ``dai``, ``truoc``, ``phai``, ``khac`` or ``cho``: they
# distinguish short/long term, before/after, payable/receivable, other and
# leasing metrics.
_METRIC_NOISE = {
    "bao",
    "biet",
    "cac",
    "cao",
    "co",
    "cong",
    "cp",
    "ctcp",
    "cuoi",
    "cua",
    "dau",
    "den",
    "dong",
    "dung",
    "gia",
    "hop",
    "la",
    "me",
    "nam",
    "nghin",
    "nhat",
    "nhieu",
    "phan",
    "so",
    "tai",
    "thang",
    "thoi",
    "trieu",
    "tram",
    "tu",
    "ty",
    "vao",
    "va",
    "xem",
    "y",
    "duoc",
    "theo",
}

# Row labels contain the same generic markers plus numbering and period
# labels.  Keep ``cho`` here because it is part of ``cho thue``.
# Keep ``tai`` in row tokens for the strict metric variant.  It is semantic in
# ``tài sản``, ``tài chính`` and ``tại Ngân hàng Nhà nước``; the relaxed metric
# population still provides the OCR-shortened fallback when the source row
# itself omits that token.
_LABEL_NOISE = (_METRIC_NOISE - {"tai"}) | {
    "cuoi",
    "dau",
    "ngay",
    "so",
    "trong",
}

_ENTITY_MARKERS = (
    "cong ty tnhh",
    "tong cong ty",
    "ngan hang",
    "tap doan",
    "cong ty",
    "ctcp",
)

_KNOWN_NON_TICKERS = {
    "CTCP",
    "TMCP",
    "VND",
    "VAMC",
    "TNDN",
    "LNST",
    "STT",
}

_STOCK_CUES = (
    "so du",
    "cuoi nam",
    "dau nam",
    "tai ngay",
    "den ngay",
    "so cuoi nam",
    "so dau nam",
)

_FLOW_CUES = ("trong nam", "phat sinh trong nam", "nam nay")
_SEMANTIC_NUMBER_CONTEXT = {"vay", "khoan", "nhom", "loai", "muc", "lan"}
_OPTIONAL_OCR_TOKENS = {"cac", "cua", "theo", "va"}
_LABEL_TAIL_DECORATION = {
    "a",
    "b",
    "c",
    "d",
    "vnd",
    "usd",
    "eur",
    "jpy",
    "cny",
    "cp",
    "krw",
    "nam",
    "nay",
    "truoc",
}


def _normalize(value: Any) -> str:
    # The caller already owns the canonical Vietnamese normalizer.  Keeping a
    # tiny local normalizer avoids importing the submission script into this
    # reusable core module and is sufficient for row/metric comparison.
    import unicodedata

    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.replace("đ", "d")).strip()


def _tokenize(
    value: Any,
    *,
    noise: set[str],
    keep_semantic_numbers: bool = False,
) -> list[str]:
    tokens: list[str] = []
    raw_tokens = _normalize(value).split()
    for index, token in enumerate(raw_tokens):
        previous = raw_tokens[index - 1] if index else ""
        if token in noise:
            continue
        if token.isdigit():
            # ``vay 1``/``khoản 4`` are semantic row identifiers, not dates
            # or formatting ordinals.  Retain them so a loan-number question
            # cannot be silently bound to another loan in the same schedule.
            if (
                keep_semantic_numbers
                and previous in _SEMANTIC_NUMBER_CONTEXT
                and not _YEAR_RE.fullmatch(token)
            ):
                tokens.append(token)
            continue
        if len(token) == 1 and not token.isalpha():
            continue
        if re.fullmatch(r"[a-z]*\d+[a-z\d]*", token):
            continue
        tokens.append(token)
    return tokens


def _compact_tokens(tokens: Iterable[str]) -> str:
    return " ".join(tokens)


def _plan(item: Mapping[str, Any]) -> Mapping[str, Any]:
    value = item.get("question_plan") or {}
    return value if isinstance(value, Mapping) else {}


def _candidate_tickers(
    item: Mapping[str, Any], *, known_tickers: set[str]
) -> list[str]:
    plan = _plan(item)
    planned = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip().upper() in known_tickers
    ]
    question = str(item.get("question") or "")
    mentioned = [
        token
        for token in _UPPER_TICKER_RE.findall(question)
        if token not in _KNOWN_NON_TICKERS and token in known_tickers
    ]
    if planned or mentioned:
        # Keep the explicit compiler/question order.  The document-id
        # recovery below is only for the narrow case where the planner could
        # not resolve an issuer at all.
        return list(dict.fromkeys([*planned, *mentioned]))

    # A few source questions contain only the Vietnamese company name.  The
    # hydrated retrieval candidates still carry a ticker-bearing document id
    # (for example ``TTF_financial_statements_2020_consolidated``), so recover
    # that identity from navigation metadata without allowing arbitrary
    # corpus-wide ticker search.  This is deliberately limited to one unique
    # known ticker; multiple inferred issuers remain unresolved.
    inferred: set[str] = set()
    for candidate in item.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        raw_ticker = str(candidate.get("ticker") or "").strip().upper()
        if raw_ticker in known_tickers:
            inferred.add(raw_ticker)
            continue
        document_id = str(candidate.get("document_id") or "").strip()
        match = re.match(
            r"^([A-Za-z][A-Za-z0-9]{1,5})_financial_statements(?:_|$)",
            document_id,
        )
        if match:
            ticker = match.group(1).upper()
            if ticker in known_tickers:
                inferred.add(ticker)
    if len(inferred) == 1:
        return [next(iter(inferred))]
    # The compiler can emit both the issuer brand and its ticker (for example
    # FPT and FTS).  Keep all candidates for now; row-level metric/context
    # matching will eliminate the brand-only path.
    return []


def _strip_entity_tail(text: str) -> str:
    """Keep the metric before an issuer phrase in a compiled operand."""

    positions = [
        text.find(marker)
        for marker in _ENTITY_MARKERS
        if text.find(marker) >= 0
    ]
    if positions:
        text = text[: min(positions)].strip()
    # ``tại Tập đoàn ...`` is an issuer qualifier in the source questions,
    # not part of the reported row metric.  It is safe to cut only when it is
    # not the first token and the operand already has a target phrase.
    # Do not cut generic ``tài sản``.  Only remove ``tại`` when it introduces
    # an issuer/counterparty phrase; the normalized spelling is identical.
    for marker in (
        " tai cong ty",
        " tai tong cong ty",
        " tai ngan hang",
        " tai tap doan",
        " tai ctcp",
    ):
        position = text.find(marker)
        if position > 0:
            text = text[:position].strip()
            break
    return text


def _has_specific_counterparty_qualifier(raw_metric: str) -> bool:
    """Return true for a named borrower/issuer beyond the report company.

    The source-first index generally has a row label but not the full
    counterparty column.  Accepting a generic row such as ``phải thu về cho
    vay ngắn hạn`` for ``cho vay Công ty X`` is therefore unsafe.  Existing
    evidence/model routes can still resolve these questions with their full
    table coordinates, so this lane fails closed.
    """

    text = _normalize(raw_metric)
    return any(
        marker in text
        for marker in (
            " den cong ty",
            " den ctcp",
            " den tap doan",
            " cho vay cong ty",
            " cho vay ctcp",
            " cho vay tap doan",
            " phai thu cong ty",
            " phai thu ctcp",
            " phai thu tap doan",
        )
    )


def _qualifier_suffix(
    normalized: str,
    *,
    marker: str,
    tickers: set[str],
) -> str:
    """Extract a named-party suffix without splitting inside ``công ty``."""

    position = normalized.find(marker)
    if position < 0:
        return ""
    if marker == " tai ":
        # ``tại`` is also the first token of ordinary metrics such as
        # ``tài sản`` after Vietnamese diacritics are removed.  Only treat it
        # as a named-party boundary when an entity marker follows; otherwise
        # a generic asset label could manufacture a false qualifier variant.
        suffix_start = normalized[position + len(marker) :]
        if not re.match(
            r"(?:ctcp|cong ty|tong cong ty|ngan hang|tap doan|dai dien chu so huu)\b",
            suffix_start,
        ):
            return ""
    suffix = normalized[position + len(marker) :]
    if marker == " tai ":
        # The planner may remove ``của`` and leave the reporting issuer
        # immediately after the named counterparty, e.g.
        # ``... tại CTCP Sài Gòn - Rạch Giá Ngân hàng TMCP Kiên Long``.
        # Keep the first entity phrase and stop at the next entity marker so
        # the qualifier remains the investee/borrower rather than the issuer.
        entity_positions = [
            suffix.find(entity_marker)
            for entity_marker in _ENTITY_MARKERS
            if suffix.find(entity_marker) > 0
        ]
        if entity_positions:
            suffix = suffix[: min(entity_positions)].strip()
    if marker == " den " and suffix.startswith("ngay "):
        return ""
    words: list[str] = []
    stop_words = {
        "cuoi",
        "dau",
        "nam",
        "la",
        "bao",
        "nhieu",
        "trieu",
        "tram",
        "nghin",
        "dong",
        "cua",
    }
    for token in suffix.split():
        if token in tickers or token.isdigit() or token in stop_words:
            break
        words.append(token)
    return " ".join(words)


def _metric_variants(item: Mapping[str, Any], *, tickers: Sequence[str]) -> list[str]:
    plan = _plan(item)
    raw_metrics = [
        str(operand.get("metric") or "")
        for operand in plan.get("operands") or []
        if isinstance(operand, Mapping) and str(operand.get("metric") or "").strip()
    ]
    if not raw_metrics:
        raw_metrics = [str(item.get("question") or "")]
    ticker_tokens = {str(ticker).lower() for ticker in tickers}
    output: list[str] = []
    seen: set[str] = set()

    def append_variant(
        text: str,
        *,
        strip_entity: bool = True,
        noise: set[str] = _METRIC_NOISE,
    ) -> None:
        """Add one normalized row/column phrase to the metric population."""

        text = _normalize(text)
        # ``số dư`` is a grammatical wrapper.  Keep standalone ``dư``/``đủ``
        # because ``Dư nợ đủ tiêu chuẩn`` is a distinct risk row.
        text = re.sub(r"\bso du\b", " ", text)
        if strip_entity:
            text = _strip_entity_tail(text)
        words: list[str] = []
        raw_tokens = text.split()
        for index, token in enumerate(raw_tokens):
            if token in ticker_tokens:
                continue
            if _YEAR_RE.fullmatch(token):
                continue
            if token.isdigit():
                previous = raw_tokens[index - 1] if index else ""
                if previous not in _SEMANTIC_NUMBER_CONTEXT:
                    continue
                # A loan/section number is part of the metric identity.  Do
                # not let the generic alphanumeric-token guard below discard
                # it after it has passed the semantic-context check.
                words.append(token)
                continue
            if re.fullmatch(r"[a-z]*\d+[a-z\d]*", token):
                continue
            if token in noise:
                continue
            words.append(token)
        # Do not pass a company/period-only phrase to the row matcher.
        if len(words) < 2:
            return
        metric = _compact_tokens(words)
        if metric not in seen:
            seen.add(metric)
            output.append(metric)

    for raw in raw_metrics:
        # ``tài`` is usually harmless OCR/function context in older plans, but
        # it is semantic in phrases such as ``tài sản``, ``tài chính`` and
        # ``tại Ngân hàng Nhà nước``.  Keep a strict variant first and retain
        # the historical relaxed variant as a fallback for OCR-shortened rows.
        append_variant(raw, noise=_METRIC_NOISE - {"tai"})
        append_variant(raw)

        # ``tổng`` is often a question wrapper around a canonical reported
        # line (for example ``Tổng vốn góp`` -> ``Vốn góp của chủ sở hữu``).
        # Keep the strict form above, then add a relaxed form only as a
        # fallback.  This does not erase the distinction when the source has
        # an actual ``Tổng ...`` row.
        normalized = _normalize(raw)
        if re.search(r"\btong\b", normalized):
            append_variant(
                re.sub(r"\btong\b", " ", normalized),
                noise=_METRIC_NOISE - {"tai"},
            )
            append_variant(re.sub(r"\btong\b", " ", normalized))

        # A named counterparty is present in the row or in the table context
        # for a small but important class of questions.  Add the qualifier as
        # a separate matching phrase; the candidate gate below still requires
        # the requested action and qualifier together, so a company-name row
        # cannot authorize an unrelated metric.
        for marker in (" den ", " cho vay ", " tai "):
            if marker == " cho vay " and not _has_specific_counterparty_qualifier(normalized):
                continue
            suffix = _qualifier_suffix(
                normalized,
                marker=marker,
                tickers=ticker_tokens,
            )
            if not suffix:
                continue
            # ``tại đại diện chủ sở hữu`` already has an explicit combined
            # related-party variant below.  Keeping an extra qualifier-only
            # variant would let that phrase outrank the action metric in a
            # first-column related-party schedule.
            if marker == " tai " and _normalize(suffix) == "dai dien chu so huu":
                continue
            append_variant(suffix, strip_entity=False)

        # ``tại đại diện chủ sở hữu`` is a semantic qualifier, not the
        # reporting issuer.  Preserve it for related-party schedules.
        match = re.search(r"\btai\s+(dai dien chu so huu)\b", normalized)
        if match:
            # The qualifier-only fallback is useful for a row where the
            # counterparty occupies a separate column.  A row such as
            # ``Tiền gửi của BIDV tại đại diện chủ sở hữu`` also needs the
            # action and qualifier together to survive the label-tail gate.
            # Preserve ``số`` here: it is part of the fixed qualifier, not the
            # generic ``số dư`` wrapper removed above.  Do not add a
            # qualifier-only variant: it would win by a smaller prefix over
            # the action metric when the related-party table puts the
            # counterparty in its first column.
            base_metric = _strip_entity_tail(normalized)
            append_variant(
                f"{base_metric} {match.group(1)}",
                strip_entity=False,
                noise=_METRIC_NOISE - {"tai", "so"},
            )
    return output


def _qualifier_phrases(item: Mapping[str, Any]) -> list[str]:
    """Return named-party qualifiers that must occur in the selected row."""

    plan = _plan(item)
    raw_metrics = [
        str(operand.get("metric") or "")
        for operand in plan.get("operands") or []
        if isinstance(operand, Mapping)
    ]
    phrases: list[str] = []
    tickers = {
        str(value).strip().lower()
        for value in plan.get("tickers") or []
        if str(value).strip()
    }
    for raw in raw_metrics:
        normalized = _normalize(raw)
        for marker in (" den ", " cho vay ", " tai "):
            if marker == " cho vay " and not _has_specific_counterparty_qualifier(normalized):
                continue
            suffix = _qualifier_suffix(
                normalized,
                marker=marker,
                tickers=tickers,
            )
            if not suffix:
                continue
            if (
                marker == " tai "
                and _normalize(suffix).startswith("dai dien chu so huu")
            ):
                # This qualifier has its own exact related-party contract
                # below.  Do not add a second, noise-shortened phrase such as
                # ``dai dien chu huu``; it can make a first-column related
                # party row fail the all-qualifiers gate.
                continue
            words = _tokenize(
                suffix,
                noise=_METRIC_NOISE,
                keep_semantic_numbers=False,
            )
            if len(words) >= 2:
                phrases.append(_compact_tokens(words))
        if re.search(r"\btai\s+dai dien chu so huu\b", normalized):
            phrases.append("dai dien chu so huu")
    return list(dict.fromkeys(phrases))


def _action_phrases(item: Mapping[str, Any]) -> list[str]:
    """Return the action that must accompany a named qualifier."""

    text = _normalize(item.get("question") or "")
    return [
        phrase
        for phrase in ("trai phieu", "cho vay", "tien gui")
        if phrase in text
    ]


def _candidate_hint_ranks(item: Mapping[str, Any]) -> dict[str, int]:
    """Map hydrated candidate table UIDs to their best retrieval rank."""

    result: dict[str, int] = {}
    for candidate in item.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        uid = str(candidate.get("internal_table_uid") or "").strip()
        if not uid:
            continue
        raw_rank = candidate.get("rank")
        if raw_rank is None:
            raw_rank = candidate.get("original_retrieval_rank")
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError):
            continue
        if rank < 1:
            continue
        result[uid] = min(rank, result.get(uid, rank))
    return result


def _table_kind(table: Mapping[str, Any]) -> str:
    context = _table_context(table)
    generic_kinds = {"", "unknown", "financial_data_schedule"}
    for field in ("table_function", "table_section"):
        value = table.get(field)
        if isinstance(value, Mapping):
            kind = str(value.get("kind") or "").strip()
            if kind and kind not in generic_kinds:
                return kind
            if kind:
                # The corpus classifier occasionally labels a primary
                # statement as a generic schedule.  The source title/header
                # is a stronger semantic signal for direct lookup routing.
                if "bang can doi ke toan" in context:
                    return "balance_sheet"
                if "bao cao ket qua hoat dong" in context:
                    return "income_statement"
                if "bao cao luu chuyen tien te" in context:
                    return "cash_flow_statement"
                return kind
    direct = str(table.get("table_kind") or table.get("kind") or "").strip()
    if direct in generic_kinds:
        if "bang can doi ke toan" in context:
            return "balance_sheet"
        if "bao cao ket qua hoat dong" in context:
            return "income_statement"
        if "bao cao luu chuyen tien te" in context:
            return "cash_flow_statement"
    return direct or "unknown"


def _table_context(table: Mapping[str, Any]) -> str:
    parts: list[str] = []
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        parts.extend(str(trace.get(key) or "") for key in ("source_title", "summary"))
        topic = trace.get("topic")
        if isinstance(topic, Mapping):
            parts.append(str(topic.get("label") or ""))
    for field in ("table_function", "table_purpose", "table_section"):
        value = table.get(field)
        if isinstance(value, Mapping):
            parts.extend(str(value.get(key) or "") for key in ("kind", "label", "matched_evidence"))
    parts.extend(str(value) for value in table.get("headers") or [])
    parts.extend(str(value) for value in table.get("column_labels") or [])
    return _normalize(" ".join(parts))


def _scope_compatible(
    table: Mapping[str, Any], *, requested_scope: str | None
) -> bool:
    if not requested_scope:
        return True
    table_scope = str(table.get("scope") or "").strip().lower()
    if table_scope == requested_scope:
        return True
    # Securities/brokerage reports often omit separate/consolidated in the
    # structured table record.  An unknown scope is safe for an explicitly
    # separate question only when the document itself is not consolidated.
    if requested_scope == "separate" and table_scope in {"", "unknown"}:
        return "consolidated" not in str(table.get("document_id") or "").lower()
    return False


def _question_scope(item: Mapping[str, Any]) -> str | None:
    plan_scope = _plan(item).get("scope")
    if plan_scope:
        return str(plan_scope).strip().lower()
    question = _normalize(item.get("question") or "")
    if "cong ty me" in question:
        return "separate"
    return None


def _is_stock_question(item: Mapping[str, Any]) -> bool:
    question = _normalize(item.get("question") or "")
    return any(cue in question for cue in _STOCK_CUES)


def _period_intent(item: Mapping[str, Any]) -> str | None:
    """Infer only the explicit start/end intent needed by column lookup."""

    question = _normalize(item.get("question") or "")
    if (
        "dau nam" in question
        or "dau ky" in question
        or re.search(r"\b1\s+1\b", question)
    ):
        return "start"
    if (
        "cuoi nam" in question
        or "cuoi ky" in question
        or "tai ngay" in question
        or re.search(r"\b31\s+12\b", question)
    ):
        return "end"
    return None


def _table_period_context(table: Mapping[str, Any]) -> str:
    """Return local table-period labels without inheriting the report title.

    A financial statement title commonly says ``kết thúc ngày 31/12/YYYY``
    even when the selected table is a comparative/reclassification table that
    contains only ``01/01/YYYY``.  Using :func:`_table_context` here would
    therefore make the report title look like evidence for the selected cell.
    The period gate must inspect only the table's own period labels and header
    rows.
    """

    parts: list[str] = []
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        labels = trace.get("period_labels")
        if isinstance(labels, (list, tuple)):
            parts.extend(str(value or "") for value in labels)
        elif labels:
            parts.append(str(labels))
    for field in ("headers", "column_labels"):
        labels = table.get(field)
        if isinstance(labels, (list, tuple)):
            parts.extend(str(value or "") for value in labels)
    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if rows:
        # The normalizer may mark numeric rows as headers while leaving the
        # actual semantic column labels in row zero.  Row zero is safe to
        # inspect here because this helper is entered only after a contextual
        # row contract has matched the table.
        header_indices.add(0)
    for index in sorted(header_indices):
        if 0 <= index < len(rows) and isinstance(rows[index], list):
            parts.extend(str(value or "") for value in rows[index])
    return _normalize(" ".join(parts))


def _table_period_compatible(
    table: Mapping[str, Any], *, item: Mapping[str, Any], requested_year: int
) -> bool:
    """Reject a table whose local period contradicts an explicit question.

    This is deliberately a contradiction gate, not a requirement that every
    table expose a full date.  ``Năm nay``/``Năm trước`` and
    ``Số cuối năm``/``Số đầu năm`` remain usable through the existing column
    contract.  A table containing only ``01/01/YYYY`` cannot, however, answer
    an explicit end-of-year question; the reverse is also unsafe.
    """

    if not _is_stock_question(item):
        return True
    intent = _period_intent(item)
    if intent is None:
        return True
    context = _table_period_context(table)
    year = str(requested_year)
    has_start = any(
        marker in context
        for marker in (
            f"01 01 {year}",
            f"1 1 {year}",
            "so dau nam",
            "dau ky",
            "opening balance",
            "beginning balance",
        )
    )
    has_end = any(
        marker in context
        for marker in (
            f"31 12 {year}",
            "so cuoi nam",
            "cuoi ky",
            "closing balance",
        )
    )
    if intent == "end" and has_start and not has_end:
        return False
    if intent == "start" and has_end and not has_start:
        return False
    return True


def _period_row_score(label: str, *, intent: str | None) -> float:
    """Score a row label as a period selector for a transposed table."""

    text = _normalize(label)
    if not text or intent is None:
        return 0.0
    score = 0.0
    if intent == "end":
        if "cuoi nam" in text or "cuoi ky" in text:
            score += 5.0
        if "nam nay" in text:
            score += 1.5
        if "dau nam" in text or "dau ky" in text:
            score -= 5.0
        if "nam truoc" in text:
            score -= 1.5
    else:
        if "dau nam" in text or "dau ky" in text:
            score += 5.0
        if "nam truoc" in text and "dau" in text:
            score -= 1.5
        if "cuoi nam" in text or "cuoi ky" in text:
            score -= 5.0
        if "nam nay" in text and "dau" in text:
            score += 1.5
    return score


def _financial_liability_maturity_total_table_contract(
    item: Mapping[str, Any],
    table: Mapping[str, Any],
    *,
    table_context: str | None = None,
) -> bool:
    """Recognize the narrow maturity schedule used by a financial-liability total."""

    question = _normalize(item.get("question") or "")
    if "tong cong" not in question or "nghia vu no tai chinh" not in question:
        return False
    context = _table_context(table) if table_context is None else table_context
    # Asset and liability maturity schedules share the same columns.  The
    # question asks for the liability schedule, so a generic maturity header
    # is insufficient: the local table context must explicitly describe the
    # financial-liability block.  This prevents the asset table in the same
    # risk-management note from winning on the identical ``TỔNG CỘNG`` row.
    rows = table.get("rows") or []
    row_context = _normalize(
        " ".join(
            str(cell or "")
            for row in rows
            if isinstance(row, list)
            for cell in row[:2]
        )
    )
    if (
        "khoan no tai chinh" not in context
        and "no tai chinh" not in row_context
        and "nghia vu no" not in row_context
    ):
        return False
    if not all(
        marker in context
        for marker in (
            "qua han",
            "khong xac dinh ky han",
            "den 01 nam",
            "tu 01 05 nam",
            "tong cong",
        )
    ):
        return False
    table_section = table.get("table_section")
    section_kind = (
        str(table_section.get("kind") or "").strip().lower()
        if isinstance(table_section, Mapping)
        else ""
    )
    if section_kind != "liability":
        return False
    question_years = _YEAR_RE.findall(question)
    if not question_years:
        return False
    requested_year = question_years[-1]
    # The surrounding report title may mention the current filing year even
    # when this table is the comparative prior-year schedule.  Bind the
    # requested period to the table's own header/first row rather than to the
    # broader context, otherwise a 2018 comparative table inside the 2019
    # report can compete with the requested 2019 schedule.
    header_parts: list[str] = []
    for field in ("headers", "column_labels"):
        values = table.get(field)
        if isinstance(values, list):
            header_parts.extend(str(value or "") for value in values)
    if rows and isinstance(rows[0], list):
        header_parts.extend(str(value or "") for value in rows[0])
    header_context = _normalize(" ".join(header_parts))
    return (
        f"31 12 {requested_year}" in header_context
        or f"31 thang 12 nam {requested_year}" in header_context
    )


def _financial_receivables_total_table_contract(
    item: Mapping[str, Any],
    table: Mapping[str, Any],
    *,
    table_context: str | None = None,
) -> bool:
    """Recognize the disclosed doubtful-receivables total row.

    Some brokerage reports disclose a generic ``Tổng cộng`` row under the
    note ``Chi tiết dự phòng suy giảm giá trị các khoản phải thu``.  The
    question compiler can lose that table heading and emit only
    ``Tổng cộng các khoản phải thu``.  A bare total row is not sufficient;
    bind it only when the note heading and the gross-receivables closing
    column are both present in the same financial-note table.
    """

    question = _normalize(item.get("question") or "")
    if "tong cong" not in question or "cac khoan phai thu" not in question:
        return False
    context = _table_context(table) if table_context is None else table_context
    if "chi tiet du phong suy giam gia tri" not in context:
        return False
    if "cac khoan phai thu" not in context:
        return False
    table_function = table.get("table_function")
    table_kind = (
        str(table_function.get("kind") or "").strip().lower()
        if isinstance(table_function, Mapping)
        else str(table.get("table_kind") or table.get("kind") or "").strip().lower()
    )
    if table_kind not in {"financial_note", "financial_note_detail"}:
        return False
    return "cuoi nam" in context or "tai ngay" in context


def _contextual_direct_row_kind(
    item: Mapping[str, Any],
    table: Mapping[str, Any],
    row_label: str,
    *,
    row_index: int | None = None,
    parse_decimal: Callable[[Any], Decimal | None] | None = None,
    table_context: str | None = None,
    financial_liability_maturity_total: bool | None = None,
    financial_receivables_total: bool | None = None,
) -> str | None:
    """Recognize a small set of table-context/row contracts.

    OCR tables often put the semantic metric in the note heading and leave a
    generic row such as ``TỔNG CỘNG`` or ``Trích khấu hao trong năm``.  A
    keyword-only row matcher cannot bind those rows safely.  These contracts
    require the question phrase, the local table context and the row role to
    agree before allowing a contextual candidate.
    """

    question = _normalize(item.get("question") or "")
    context = _table_context(table) if table_context is None else table_context
    row = _normalize(row_label)
    if (
        "tien thue" in question
        and "toi thieu" in question
        and "thue hoat dong" in question
        and "tien thue" in context
        and "toi thieu" in context
        and "thue hoat dong" in context
        and row in {"tong cong", "tong"}
    ):
        return "rent_commitment_total"
    if (
        "cam ket" in question
        and "thue hoat dong" in question
        and any(
            marker in question
            for marker in (
                "den han trong 1 nam",
                "trong vong 1 nam",
                "den mot nam",
                "den 1 nam",
            )
        )
        and "thue hoat dong" in context
        and any(
            marker in context
            for marker in (
                "cam ket",
                "tien thue toi thieu",
                "hop dong thue hoat dong",
            )
        )
        and row
        in {
            "den han trong 1 nam",
            "trong vong 1 nam",
            "den mot nam",
            "den 1 nam",
        }
    ):
        # Operating-lease schedules use several OCR row-label variants for
        # the same bounded maturity bucket.  The question and local table
        # context must both name the operating-lease commitment; a bare
        # ``đến hạn trong 1 năm`` row from a liquidity/debt schedule is not
        # sufficient.  Column selection remains a separate explicit
        # end-of-year contract below.
        return "rent_commitment_maturity_1y"
    if (
        "khau hao" in question
        and "bat dong san dau tu" in question
        and "cho thue" in question
        and ("khau hao" in context or "hao mon" in context)
        and "bat dong san dau tu" in context
        and "cho thue" in context
        and ("trich khau hao" in row or "khau hao trong nam" in row)
    ):
        return "investment_property_depreciation_total"
    if (
        "tong so tai san tai chinh" in question
        and "tai san tai chinh" in context
        and row in {"", "tong", "tong cong"}
        and row_index is not None
        and parse_decimal is not None
    ):
        # The financial-instrument note normally has two blank/total rows:
        # one closes the ``Tài sản tài chính`` section and one closes the
        # following ``Nợ phải trả tài chính`` section.  Bind only the former
        # so the total-column contract cannot leak into the liability block.
        rows = table.get("rows") or []
        last_section = ""
        for previous_index in range(row_index - 1, -1, -1):
            previous_row = rows[previous_index]
            if not isinstance(previous_row, list):
                continue
            previous_label = _normalize(
                _row_label(previous_row, parse_decimal=parse_decimal)
            )
            if previous_label == "tai san tai chinh":
                last_section = "asset"
                break
            if previous_label == "no phai tra tai chinh":
                last_section = "liability"
                break
        if last_section == "asset":
            return "financial_assets_total"
    if financial_liability_maturity_total is None:
        financial_liability_maturity_total = (
            _financial_liability_maturity_total_table_contract(
                item,
                table,
                table_context=context,
            )
        )
    if financial_liability_maturity_total and row in {"tong", "tong cong"}:
        # Liquidity-maturity schedules use a generic ``TỔNG CỘNG`` row and
        # put the requested value in the explicitly labelled total column.
        # Require the question's reporting year in the local table headers;
        # otherwise a prior-year continuation table can look identical and
        # silently replace the requested period.
        return "financial_liability_maturity_total"
    if financial_receivables_total is None:
        financial_receivables_total = _financial_receivables_total_table_contract(
            item,
            table,
            table_context=context,
        )
    if financial_receivables_total and row in {"tong", "tong cong"}:
        return "financial_receivables_total"
    if (
        "tong trai phieu thuong" in question
        and "trai phieu thuong" in context
        and row == "tong cong"
    ):
        return "ordinary_bond_total"
    if (
        "tien va cac khoan tuong duong tien" in question
        and "tien va cac khoan tuong duong tien" in context
        and row in {"tong", "tong cong"}
    ):
        return "cash_and_equivalents_total"
    return None


def _contextual_period_column_choice(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: Sequence[Any],
    item: Mapping[str, Any],
    requested_report_year: int,
    parse_decimal: Callable[[Any], Decimal | None],
) -> tuple[int, Decimal, str] | None:
    """Choose a numeric column from an explicit opening/closing header."""

    intent = _period_intent(item)
    if intent is None:
        return None
    options: list[tuple[int, Decimal, float]] = []
    for column_index, cell in enumerate(row):
        raw_value = parse_decimal(cell)
        if raw_value is None:
            continue
        period_parts = _column_period_texts(
            table,
            column_index=column_index,
            row_index=row_index,
        )
        rows = table.get("rows") or []
        if rows and isinstance(rows[0], list) and column_index < len(rows[0]):
            # As with the other contextual helpers, row zero can contain the
            # real period label even when the normalizer's header index list
            # points at later numeric rows.
            period_parts.append(str(rows[0][column_index] or ""))
        period_text = _normalize(" ".join(dict.fromkeys(period_parts)))
        score = 0.0
        if intent == "end":
            if "so cuoi nam" in period_text or "cuoi nam" in period_text:
                score += 5.0
            if f"31 12 {requested_report_year}" in period_text:
                score += 4.0
            if "so dau nam" in period_text or "dau nam" in period_text:
                score -= 5.0
        else:
            if "so dau nam" in period_text or "dau nam" in period_text:
                score += 5.0
            if f"1 1 {requested_report_year}" in period_text:
                score += 4.0
            if "so cuoi nam" in period_text or "cuoi nam" in period_text:
                score -= 5.0
        if score > 0.0:
            options.append((column_index, raw_value, score))
    if not options:
        return None
    best_score = max(option[2] for option in options)
    best = [option for option in options if option[2] == best_score]
    if len(best) != 1:
        return None
    column_index, raw_value, _ = best[0]
    return column_index, raw_value, "contextual_explicit_period_column"


def _contextual_total_column_choice(
    table: Mapping[str, Any],
    *,
    row: Sequence[Any],
    parse_decimal: Callable[[Any], Decimal | None],
) -> tuple[int, Decimal, str] | None:
    """Choose the unique numeric column labelled ``Tổng``."""

    labels: list[str] = []
    for key in ("column_labels", "headers"):
        values = table.get(key)
        if isinstance(values, list):
            labels.extend(str(value or "") for value in values)
    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if rows:
        header_indices.add(0)
    options: list[tuple[int, Decimal]] = []
    for column_index, cell in enumerate(row):
        raw_value = parse_decimal(cell)
        if raw_value is None:
            continue
        column_texts = list(labels[column_index : column_index + 1]) if column_index < len(labels) else []
        for header_index in sorted(header_indices):
            if 0 <= header_index < len(rows) and isinstance(rows[header_index], list):
                header_row = rows[header_index]
                if column_index < len(header_row):
                    column_texts.append(str(header_row[column_index] or ""))
        normalized = _normalize(" ".join(column_texts))
        if re.search(r"\btong\b", normalized) and "phan tram" not in normalized:
            options.append((column_index, raw_value))
    if len(options) != 1:
        return None
    column_index, raw_value = options[0]
    return column_index, raw_value, "contextual_total_column"


def _contextual_receivables_total_column_choice(
    table: Mapping[str, Any],
    *,
    row: Sequence[Any],
    parse_decimal: Callable[[Any], Decimal | None],
) -> tuple[int, Decimal, str] | None:
    """Choose the unique gross-receivables closing-value column.

    OCR may collapse words in the header (``Giá trịphải thu khó đòicuối
    nămVND``), so the contract uses independent semantic fragments rather
    than one exact header string.  The ``dự phòng`` exclusion prevents the
    adjacent provision columns from satisfying the same question.
    """

    labels: list[str] = []
    for key in ("column_labels", "headers"):
        values = table.get(key)
        if isinstance(values, list):
            labels.extend(str(value or "") for value in values)
    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if rows:
        header_indices.add(0)
    options: list[tuple[int, Decimal]] = []
    for column_index, cell in enumerate(row):
        raw_value = parse_decimal(cell)
        if raw_value is None:
            continue
        column_texts = (
            list(labels[column_index : column_index + 1])
            if column_index < len(labels)
            else []
        )
        for header_index in sorted(header_indices):
            if 0 <= header_index < len(rows) and isinstance(rows[header_index], list):
                header_row = rows[header_index]
                if column_index < len(header_row):
                    column_texts.append(str(header_row[column_index] or ""))
        normalized = _normalize(" ".join(column_texts))
        if (
            "gia tri" in normalized
            and "phai thu" in normalized
            and "cuoi nam" in normalized
            and "du phong" not in normalized
        ):
            options.append((column_index, raw_value))
    if len(options) != 1:
        return None
    column_index, raw_value = options[0]
    return column_index, raw_value, "contextual_receivables_total_column"


def _hierarchical_section_metric_choice(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: Sequence[Any],
    item: Mapping[str, Any],
    parse_decimal: Callable[[Any], Decimal | None],
) -> tuple[int, Decimal, str] | None:
    """Bind ``section row × metric header`` in a transposed note table."""

    question = _normalize(item.get("question") or "")
    if "gia tri con lai" not in question or "quyen su dung dat" not in question:
        return None
    current_label = _normalize(_row_label(row, parse_decimal=parse_decimal))
    if current_label != "tai ngay cuoi nam":
        return None
    rows = table.get("rows") or []
    section_found = False
    for previous_index in range(row_index - 1, max(-1, row_index - 6), -1):
        if previous_index >= len(rows) or not isinstance(rows[previous_index], list):
            continue
        previous_label = _normalize(
            _row_label(rows[previous_index], parse_decimal=parse_decimal)
        )
        if previous_label == "gia tri con lai":
            section_found = True
            break
    if not section_found:
        return None

    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if rows:
        header_indices.add(0)
    options: list[tuple[int, Decimal]] = []
    for header_index in sorted(header_indices):
        if header_index > row_index or header_index < 0 or header_index >= len(rows):
            continue
        header_row = rows[header_index]
        if not isinstance(header_row, list):
            continue
        for column_index, header_cell in enumerate(header_row):
            if "quyen su dung dat" not in _normalize(header_cell):
                continue
            if column_index >= len(row):
                continue
            raw_value = parse_decimal(row[column_index])
            if raw_value is not None:
                options.append((column_index, raw_value))
    if len(options) != 1:
        return None
    column_index, raw_value = options[0]
    return column_index, raw_value, "contextual_section_metric_period"


def _transposed_header_metric_choice(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: Sequence[Any],
    item: Mapping[str, Any],
    metrics: Sequence[str],
    parse_decimal: Callable[[Any], Decimal | None],
) -> tuple[int, Decimal, str, str] | None:
    """Find ``metric column × Số cuối/đầu năm row`` layouts.

    OCR normalizers sometimes move the real column labels into the first
    extracted row while retaining a flattened ``headers`` field.  Matching
    that local header row lets a question such as ``vay ngắn hạn`` bind the
    ``Số cuối năm`` cell without trusting a retrieval preview.
    """

    intent = _period_intent(item)
    if intent is None:
        return None
    row_label = _row_label(row, parse_decimal=parse_decimal)
    if _period_row_score(row_label, intent=intent) < 2.0:
        return None
    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if rows:
        header_indices.add(0)
    options: list[tuple[int, Decimal, float, str]] = []
    for header_index in sorted(header_indices):
        if header_index >= row_index or header_index < 0 or header_index >= len(rows):
            continue
        header_row = rows[header_index]
        if not isinstance(header_row, list):
            continue
        for column_index, header_cell in enumerate(header_row):
            if column_index >= len(row):
                continue
            best: tuple[float, str, str] = (0.0, "none", "")
            label_tokens = _tokenize(
                header_cell,
                noise=_LABEL_NOISE,
                keep_semantic_numbers=True,
            )
            for metric in metrics:
                metric_tokens = _tokenize(
                    metric,
                    noise=set(),
                    keep_semantic_numbers=True,
                )
                score, mode, prefix = _ordered_match(
                    metric_tokens,
                    label_tokens,
                    allow_compact=True,
                )
                if score >= 0.999 and prefix <= 1:
                    if score > best[0]:
                        best = (score, mode, metric)
            if best[0] <= 0.0:
                continue
            raw_value = parse_decimal(row[column_index])
            if raw_value is not None:
                options.append((column_index, raw_value, best[0], best[2]))
    if not options:
        return None
    best_score = max(option[2] for option in options)
    best = [option for option in options if option[2] == best_score]
    distinct_columns = {option[0] for option in best}
    if len(distinct_columns) != 1:
        return None
    column_index, raw_value, _, metric = best[0]
    return column_index, raw_value, "contextual_header_metric_period", metric


def _row_label(row: Sequence[Any], *, parse_decimal: Callable[[Any], Decimal | None]) -> str:
    parts: list[str] = []
    for cell in row:
        text = str(cell or "").strip()
        if text and parse_decimal(cell) is None:
            parts.append(text)
    return " ".join(parts)


def _row_period_context_score(
    rows: Sequence[Any],
    *,
    row_index: int,
    report_year: int,
) -> float:
    """Score a repeated row using the nearest explicit year section.

    Change-of-equity and note tables often repeat ``Lợi nhuận thuần trong
    năm`` for two historical sections.  The row itself can be identical, so
    the nearest preceding ``1/1/YYYY`` or ``31/12/YYYY`` section is the only
    safe local discriminator.  A zero score deliberately remains ambiguous.
    """

    for index in range(row_index - 1, max(-1, row_index - 7), -1):
        if index < 0 or index >= len(rows) or not isinstance(rows[index], list):
            continue
        raw = " ".join(str(cell or "") for cell in rows[index])
        years = [int(value) for value in _YEAR_RE.findall(raw)]
        if not years:
            continue
        return 2.0 if report_year in years else -2.0
    return 0.0


def _ordered_match(
    target: Sequence[str],
    label: Sequence[str],
    *,
    allow_compact: bool = False,
) -> tuple[float, str, int]:
    if not target or not label:
        return 0.0, "none", 999
    target_tuple = tuple(target)
    label_tuple = tuple(label)
    width = len(target_tuple)
    for start in range(0, len(label_tuple) - width + 1):
        if label_tuple[start : start + width] == target_tuple:
            return 1.0, "exact_contiguous", start

    positions: list[int] = []
    cursor = 0
    for token in target_tuple:
        try:
            index = label_tuple.index(token, cursor)
        except ValueError:
            positions = []
            break
        positions.append(index)
        cursor = index + 1
    if positions:
        gaps = (positions[-1] - positions[0] + 1) - len(target_tuple)
        if gaps <= 2:
            score = 0.96 - 0.04 * gaps
            return score, "ordered_with_ocr_gap", positions[0]

    # OCR frequently removes the boundary between adjacent header words
    # (``Chi phíthuế mặt bằng``).  This is useful for column headers, where
    # the surrounding period/unit contract supplies an independent gate.  It
    # is deliberately opt-in so a compact header cannot loosen row matching.
    if allow_compact:
        target_compact = "".join(target_tuple)
        label_compact = "".join(label_tuple)
        if len(target_compact) >= 8 and target_compact in label_compact:
            return 0.94, "fuzzy_ocr_compact", 0

    # Fuzzy matching is retained for genuine OCR substitutions (for example
    # ``các khoản`` -> ``khác khoản``), but not for a row that merely shares
    # a broad noun phrase.  Require at most one missing target token and use
    # the best local label window so the caller can penalize semantic prefix
    # material such as ``phải thu về``.
    target_text = _compact_tokens(target_tuple)
    best_fuzzy: tuple[float, int] | None = None
    # Permit one missing token only as a candidate; the caller applies a
    # table-context gate for the semantically dangerous ``Dư nợ`` shorthand.
    minimum_width = max(1, len(target_tuple) - 1)
    maximum_width = min(len(label_tuple), len(target_tuple) + 2)
    for window_width in range(minimum_width, maximum_width + 1):
        for start in range(0, len(label_tuple) - window_width + 1):
            window = label_tuple[start : start + window_width]
            coverage = len(set(target_tuple) & set(window)) / max(1, len(set(target_tuple)))
            missing = set(target_tuple) - set(window)
            if coverage < 0.88 or len(missing) > 1:
                continue
            if missing and not missing.issubset(_OPTIONAL_OCR_TOKENS):
                continue
            ratio = SequenceMatcher(
                None, target_text, _compact_tokens(window)
            ).ratio()
            if ratio < 0.84:
                continue
            score = min(0.93, ratio) - 0.01 * max(0, window_width - len(target_tuple))
            if best_fuzzy is None or (score, -start) > (best_fuzzy[0], -best_fuzzy[1]):
                best_fuzzy = (score, start)
    if best_fuzzy is not None:
        return best_fuzzy[0], "fuzzy_ocr_label", best_fuzzy[1]
    return 0.0, "none", 999


def _kind_score(kind: str, *, stock: bool) -> float:
    if stock:
        return {
            "balance_sheet": 8.0,
            "equity_change_statement": 7.0,
            "debt_schedule": 6.0,
            "financial_data_schedule": 5.0,
            "financial_note_detail": 4.0,
            "financial_note": 3.5,
            "related_party_schedule": 1.5,
            "income_statement": 1.5,
            "cash_flow_statement": 1.0,
            "unknown": 0.5,
        }.get(kind, 0.5)
    return {
        "income_statement": 8.0,
        "equity_change_statement": 6.0,
        "financial_note_detail": 7.0,
        "financial_note": 6.5,
        "financial_data_schedule": 5.5,
        "cash_flow_statement": 5.0,
        "debt_schedule": 3.5,
        "balance_sheet": 2.0,
        "related_party_schedule": 1.5,
        "unknown": 0.5,
    }.get(kind, 0.5)


def _context_score(
    table: Mapping[str, Any],
    *,
    item: Mapping[str, Any],
    metric_tokens: Sequence[str],
    match_mode: str,
    prefix_extra: int,
    row_label: str,
) -> float:
    context = _table_context(table)
    question = _normalize(item.get("question") or "")
    target = set(metric_tokens)
    row_text = _normalize(row_label)
    score = 0.0
    if _is_stock_question(item):
        if any(token in context for token in ("so cuoi nam", "so dau nam", "tai ngay")):
            score += 1.0
    elif any(cue in question for cue in _FLOW_CUES):
        if any(token in context for token in ("nam nay", "cho nam tai chinh", "trong nam")):
            score += 0.5

    # A reported amount and a provision/expense are different financial
    # concepts even when the row contains the same noun phrase.  Exclude a
    # provision context unless the question/metric explicitly asks for it.
    asks_provision = "du phong" in question or "du phong" in " ".join(metric_tokens)
    if not asks_provision and ("du phong" in context or "trich lap" in context):
        score -= 3.0
    if "tai khoan" in context and "tai khoan" not in target:
        score -= 1.2
    if "du no" in question or "du no" in " ".join(metric_tokens):
        if "no cho vay" in context or "chat luong no cho vay" in context:
            score += 2.0
        if "chung khoan" in context:
            score -= 2.0
        if "tien gui" in context and "to chuc tin dung" in context:
            score -= 1.0
    if prefix_extra:
        # Rows such as ``Dự phòng trái phiếu ...`` should lose to the exact
        # reported row ``Trái phiếu ...`` when both contain the target phrase.
        score -= min(1.2, 0.55 * prefix_extra)
    if match_mode == "fuzzy_ocr_label":
        score -= 0.1

    # A few high-frequency financial qualifiers are mutually exclusive.  A
    # broad fuzzy match must not turn ``tổng chi phí`` into a ``dở dang dài
    # hạn`` detail row, or VND into an FX row.  These are generic semantic
    # penalties, not Question-ID overrides.
    if "tong" in question and "do dang" in row_text and "do dang" not in target:
        score -= 3.0
    if "vo hinh" in question and "thanh ly" in row_text and "thanh ly" not in target:
        score -= 3.0
    if "thanh ly" in question and "vo hinh" in row_text and "vo hinh" not in target:
        score -= 3.0
    if "vnd" in target and "ngoai te" in row_text and "ngoai te" not in target:
        score -= 3.0
    if "ngoai te" in target and "vnd" in row_text and "vnd" not in target:
        score -= 3.0
    if "ky han" in target and "thanh toan" in row_text and "thanh toan" not in target:
        score -= 3.0
    if "thanh toan" in target and "ky han" in row_text and "ky han" not in target:
        score -= 3.0
    if "chung khoan no" in target and "thu lai" in row_text:
        score -= 3.0
    if "von gop" in target:
        kind = _table_kind(table)
        if kind in {"balance_sheet", "equity_change_statement"}:
            score += 2.0
        if kind == "cash_flow_statement" or "phat hanh co phieu" in row_text:
            score -= 2.0
    if "dai dien chu so huu" in question and "tra lai" in row_text:
        score -= 5.0
    return score


def _candidate_value_signature(value: Decimal) -> str:
    return format(value, "f")


def _semantic_label_suffix_extra(
    label_tokens: Sequence[str],
    *,
    prefix_extra: int,
    target_tokens: Sequence[str],
) -> int:
    """Count meaningful tokens after a matched metric phrase.

    Short OCR decorations such as ``(a)`` and ``(VND)`` do not distinguish a
    reported line from its canonical label.  A real tail such as ``và thành
    phẩm`` does: it narrows the line to a different reported concept.  This
    signal is deliberately soft and is used only after exact metric matching;
    it must not reject legitimate note suffixes on its own.
    """

    start = max(0, prefix_extra + len(target_tokens))
    return sum(
        token not in _LABEL_TAIL_DECORATION
        for token in label_tokens[start:]
    )


def _candidate_semantic_quality(row: Mapping[str, Any]) -> float:
    """Return the non-retrieval quality used for duplicate adjudication."""

    return float(
        row["kind_score"]
        + row["context_score"]
        + row["row_period_score"]
        + row.get("label_tail_score", 0.0)
    )


def _column_period_texts(
    table: Mapping[str, Any], *, column_index: int, row_index: int
) -> list[str]:
    """Collect period hints for one column without reading numeric values."""

    texts: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            texts.append(text)

    for key in ("column_labels", "headers"):
        labels = table.get(key)
        if isinstance(labels, list) and column_index < len(labels):
            add(labels[column_index])

    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if not header_indices and rows:
        header_indices = {0}
    for header_index in sorted(header_indices):
        if header_index > row_index or header_index < 0 or header_index >= len(rows):
            continue
        header_row = rows[header_index]
        if isinstance(header_row, list) and column_index < len(header_row):
            add(header_row[column_index])
    return texts


def _phrase_present(text: Any, phrase: str) -> bool:
    """Match a normalized phrase on token boundaries.

    Source-first lookup is deliberately phrase based, but plain substring
    checks are unsafe for directional/aggregate terms: ``vay`` is contained
    in ``cho vay`` and ``cong`` is contained in ``cong cu``.  Keeping this
    helper local makes the feedback contracts independent from the builder's
    broader semantic scorer.
    """

    normalized_text = _normalize(text)
    normalized_phrase = _normalize(phrase)
    if not normalized_text or not normalized_phrase:
        return False
    pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", normalized_text) is not None


def _row_hierarchy_context(
    table: Mapping[str, Any],
    *,
    row_index: int,
    parse_decimal: Callable[[Any], Decimal | None],
) -> str:
    """Return nearby non-numeric section labels for the selected row.

    A note can put a family label (for example ``Dự phòng``) on a text-only
    parent row and the numeric value on a child row.  The parent is useful for
    proving a requested family, but it is never treated as an aggregate
    binding by itself.  This is intentionally limited to a short local window
    so a report title or a distant section cannot authorize a cell.
    """

    rows = table.get("rows") or []
    if not isinstance(rows, list):
        return ""
    labels: list[str] = []
    for previous_index in range(row_index - 1, max(-1, row_index - 6), -1):
        if previous_index < 0 or previous_index >= len(rows):
            continue
        previous_row = rows[previous_index]
        if not isinstance(previous_row, list):
            continue
        if any(parse_decimal(cell) is not None for cell in previous_row):
            continue
        label = _row_label(previous_row, parse_decimal=parse_decimal)
        if label:
            labels.append(label)
    return _normalize(" ".join(reversed(labels)))


def _selected_column_context(
    table: Mapping[str, Any], *, column_index: int, row_index: int
) -> str:
    """Return only the semantic labels attached to the selected column."""

    return _normalize(
        " ".join(
            _column_period_texts(
                table,
                column_index=column_index,
                row_index=row_index,
            )
        )
    )


def _is_total_row_label(row_text: str) -> bool:
    """Recognize an explicit aggregate row without treating ``công cụ`` as total."""

    tokens = _normalize(row_text).split()
    if not tokens:
        return False
    if "tong" in tokens:
        return True
    # ``Cộng`` is used as a standalone total in a few OCR tables.  Do not
    # accept a prefix such as ``Công cụ`` as an aggregate marker.
    return tokens == ["cong"]


def _is_total_column_context(column_text: str) -> bool:
    """Recognize a selected total column, not merely any column containing ``cộng``."""

    tokens = _normalize(column_text).split()
    if not tokens:
        return False
    if "tong" in tokens or "total" in tokens:
        return True
    return tokens == ["cong"]


def _table_has_explicit_total_column(table: Mapping[str, Any]) -> bool:
    """Return whether the table exposes a total-labelled column anywhere."""

    labels: list[str] = []
    for key in ("headers", "column_labels"):
        values = table.get(key)
        if isinstance(values, list):
            labels.extend(str(value or "") for value in values)
    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if rows:
        header_indices.add(0)
    for label in labels:
        if _is_total_column_context(_normalize(label)):
            return True
    for header_index in sorted(header_indices):
        if not (0 <= header_index < len(rows)):
            continue
        header_row = rows[header_index]
        if not isinstance(header_row, list):
            continue
        if any(
            _is_total_column_context(_normalize(cell))
            for cell in header_row
        ):
            return True
    return False


_TOTAL_BALANCE_WRAPPER_PHRASES = (
    "tong so du",
    "tong gia goc",
    "tong so luong",
)

# These are broad statement-level parents.  An exact match against the
# relaxed metric variant (for example ``Tổng tài sản`` -> ``Tài sản``) is not
# enough to bind a total: the source still needs its explicit total row or
# total column.  More specific named lines (for example ``Chi phí thuế ...``)
# remain eligible for the exact-row alias used by the typed proxy lanes.
_TOTAL_PARENT_METRIC_PREFIXES = (
    "tai san",
    "tai san tai chinh",
    "no phai tra",
    "nghia vu no tai chinh",
    "von chu so huu",
    "du no",
    "cho vay",
    "tien gui",
    "chi phi hoat dong",
    "chi phi san xuat",
    "doanh thu",
    "gia von",
    "gia tri",
)

_TOTAL_COMPONENT_MARKERS = (
    "ngoai te",
    "tien gui",
    "ngan hang",
    "ngan han",
    "dai han",
    "ben lien quan",
    "thanh phan",
    "du phong",
    "nhnn",
    "cong ty",
    "den han tra",
    "cong cu",
    "cho vay khach hang",
    "khach hang",
    "dich vu",
    "san pham",
    "tiet kiem",
    "co ky han",
)


def _aggregate_total_requested(item: Mapping[str, Any], target_text: str) -> bool:
    """Return true only for an actual aggregate request.

    ``Tổng số dư tiền...`` and similar phrases are often Vietnamese wrappers
    around one named balance line.  Requiring a literal total row for those
    wrappers would break a valid single-line lookup.  Other explicit ``Tổng``
    requests remain aggregate-bound and need a row/column total marker.
    """

    question = _normalize(item.get("question") or "")
    target = _normalize(target_text)
    request = f"{question} {target}".strip()
    if not _phrase_present(request, "tong"):
        return False
    return not any(_phrase_present(request, phrase) for phrase in _TOTAL_BALANCE_WRAPPER_PHRASES)


def _is_single_line_total_alias(
    *,
    item: Mapping[str, Any],
    target_text: str,
    row_text: str,
) -> bool:
    """Allow the established balance-sheet alias ``Tổng cho vay ...``.

    Some statements label the one reported lending line as
    ``Phải thu về cho vay ...``.  It is a row-level accounting alias, not a
    permission to select arbitrary lending components.  Keep this exception
    phrase-based and require the requested maturity/direction to be present.
    """

    question = _normalize(item.get("question") or "")
    target = _normalize(target_text)
    # A source may omit the word ``Tổng`` from a row whose full metric is
    # otherwise exact (for example a component replay asks for
    # ``Dự phòng phải trả ngắn hạn`` while the outer wording says ``Tổng
    # cộng ...``).  This is a metric-preserving alias, not a permission to
    # accept a broad statement parent such as ``Tài sản`` as the total.
    normalized_row = _normalize(row_text)
    if target.split() == normalized_row.split() and target:
        if not any(
            target == prefix or target.startswith(f"{prefix} ")
            for prefix in _TOTAL_PARENT_METRIC_PREFIXES
        ):
            return True
    return (
        _phrase_present(question, "tong cho vay")
        and _phrase_present(target, "cho vay")
        and _phrase_present(row_text, "phai thu ve cho vay")
        and not _phrase_present(target, "tong du no")
    )


def _direction_flags(text: str) -> tuple[bool, bool]:
    """Return ``(lending, borrowing)`` flags for one selected surface."""

    normalized = _normalize(text)
    lending = _phrase_present(normalized, "cho vay") or _phrase_present(
        normalized, "no cho vay"
    )
    # Remove the lending phrase before looking for a standalone borrowing
    # marker; otherwise every ``cho vay`` row would also look like ``vay``.
    borrowing_surface = re.sub(r"\bno\s+cho\s+vay\b|\bcho\s+vay\b", " ", normalized)
    borrowing = any(
        _phrase_present(borrowing_surface, phrase)
        for phrase in (
            "khoan vay",
            "cac khoan vay",
            "vay ngan han",
            "vay dai han",
            "no vay",
            "vay",
        )
    )
    return lending, borrowing


def _cash_deposit_flags(text: str) -> tuple[bool, bool, bool]:
    """Return ``(cash_family, deposit, term_deposit)`` for a selected surface."""

    normalized = _normalize(text)
    deposit = _phrase_present(normalized, "tien gui") or _phrase_present(
        normalized, "tiet kiem"
    )
    term_deposit = deposit and (
        _phrase_present(normalized, "tien gui co ky han")
        or _phrase_present(normalized, "tien gui tiet kiem")
        or _phrase_present(normalized, "tien gui ky han")
        or _phrase_present(normalized, "co ky han")
        or _phrase_present(normalized, "tiet kiem")
    )
    explicit_cash_markers = (
        "tien gui",
        "tien mat",
        "tien va cac khoan tuong duong tien",
        "lai tien",
        "tien cho vay",
        "ky han",
        "tiet kiem",
    )
    generic_cash = _phrase_present(normalized, "tien") and not any(
        _phrase_present(normalized, marker) for marker in explicit_cash_markers
    )
    cash_equivalents = _phrase_present(
        normalized, "tien va cac khoan tuong duong tien"
    )
    cash_on_hand = _phrase_present(normalized, "tien mat")
    return generic_cash or cash_equivalents or cash_on_hand, deposit, term_deposit


def _is_related_party_table(table: Mapping[str, Any]) -> bool:
    """Detect a related-party schedule from table semantics, not document title."""

    kind = _table_kind(table)
    if kind == "related_party_schedule":
        return True
    context = _table_context(table)
    return any(
        _phrase_present(context, marker)
        for marker in ("ben lien quan", "cac ben lien quan", "related party")
    )


def _question_has_related_party_qualifier(
    item: Mapping[str, Any], *, qualifier_phrases: Sequence[str]
) -> bool:
    """Require an explicit counterparty/scope phrase for related-party tables."""

    question = _normalize(item.get("question") or "")
    if any(
        _phrase_present(question, phrase)
        for phrase in (
            "ben lien quan",
            "cac ben lien quan",
            "dai dien chu so huu",
            "related party",
        )
    ):
        return True
    # The compiled operand is navigation metadata, not authority.  A
    # qualifier that occurs only in the plan must not make an otherwise
    # unqualified question eligible for a related-party schedule.
    if _has_specific_counterparty_qualifier(question):
        return True
    return re.search(
        r"\b(?:tai|den|phai thu)\s+(?:ctcp|cong ty|tap doan|ngan hang)\b",
        question,
    ) is not None


def _source_first_feedback_reason(
    item: Mapping[str, Any],
    table: Mapping[str, Any],
    *,
    row_index: int,
    row_text: str,
    column_index: int,
    target_text: str,
    parse_decimal: Callable[[Any], Decimal | None],
    qualifier_phrases: Sequence[str],
) -> str | None:
    """Apply family-level row/metric/total/direction feedback contracts.

    The helper deliberately returns only a reason code.  It never inspects
    hidden labels, model output or research candidates, and it does not alter
    the source-first authority boundary.
    """

    question = _normalize(item.get("question") or "")
    target = _normalize(target_text)
    row = _normalize(row_text)
    hierarchy = _row_hierarchy_context(
        table,
        row_index=row_index,
        parse_decimal=parse_decimal,
    )
    column = _selected_column_context(
        table,
        column_index=column_index,
        row_index=row_index,
    )
    selected_surface = f"{row} {column}".strip()
    requested_surface = f"{question} {target}".strip()

    # Provision is a row/column family, not a table-title family.  A note
    # heading may support a generic child row, but a selected provision row or
    # provision column cannot answer a non-provision metric.
    asks_provision = _phrase_present(requested_surface, "du phong")
    row_has_provision = _phrase_present(row, "du phong")
    column_has_provision = _phrase_present(column, "du phong")
    if asks_provision and not (
        row_has_provision or column_has_provision
    ):
        return "PROVISION_ROW_REQUIRED"
    if not asks_provision and (row_has_provision or column_has_provision):
        return "PROVISION_ROW_CONFLICT"

    # Direction must be read from the selected row/column.  A broad report
    # title mentioning both loan families is not enough to authorize either.
    requested_lending, requested_borrowing = _direction_flags(requested_surface)
    selected_lending, selected_borrowing = _direction_flags(selected_surface)
    if (
        (requested_lending and selected_borrowing and not requested_borrowing)
        or (requested_borrowing and selected_lending and not requested_lending)
        or (
            selected_lending
            and selected_borrowing
            and (requested_lending or requested_borrowing)
        )
    ):
        return "LENDING_BORROWING_DIRECTION_CONFLICT"

    # Generic cash/deposit wording must not select a term-deposit component.
    # The symmetric cash-on-hand case is also rejected when the requested
    # metric is explicitly a deposit family.
    requested_cash_family, requested_deposit, requested_term_deposit = _cash_deposit_flags(
        requested_surface
    )
    selected_cash_family, selected_deposit, selected_term_deposit = _cash_deposit_flags(
        selected_surface
    )
    if (
        (requested_cash_family and selected_deposit)
        or (
            requested_deposit
            and not requested_term_deposit
            and selected_term_deposit
        )
        or (
            requested_term_deposit
            and selected_deposit
            and not selected_term_deposit
        )
        or (requested_deposit and selected_cash_family)
        or (
            _phrase_present(requested_surface, "tien mat")
            and selected_deposit
        )
        or (
            requested_term_deposit
            and selected_cash_family
        )
    ):
        return "CASH_TERM_DEPOSIT_CONFLICT"

    # Aggregate requests require an explicit total row or selected total
    # column.  A text-only parent labelled ``Tổng ...`` is useful hierarchy
    # evidence for classifying a child as a component, but is not itself a
    # binding for the selected numeric cell.
    if _aggregate_total_requested(item, target):
        selected_is_total_row = _is_total_row_label(row)
        selected_is_total_column = _is_total_column_context(column)
        exact_metric_alias = _is_single_line_total_alias(
            item=item,
            target_text=target,
            row_text=row,
        )
        # If a table has a distinct total column, an exact row label alone
        # cannot authorize a total from a different column.  This prevents a
        # relaxed metric alias from reading a component column beside the
        # reported aggregate.
        if (
            exact_metric_alias
            and _table_has_explicit_total_column(table)
            and not selected_is_total_column
        ):
            exact_metric_alias = False
        if (
            not selected_is_total_row
            and not selected_is_total_column
            and not exact_metric_alias
        ):
            hierarchy_or_row = f"{row} {hierarchy}".strip()
            if any(
                _phrase_present(hierarchy_or_row, marker)
                for marker in _TOTAL_COMPONENT_MARKERS
            ):
                return "TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING"
            return "TOTAL_ROW_NOT_BOUND"
        # A row such as ``Tổng chi phí hoạt động dịch vụ`` is a subtotal for a
        # component, not the requested parent total.  Only reject a component
        # tail when it is explicit and absent from the requested metric.
        if selected_is_total_row and not selected_is_total_column:
            for marker in _TOTAL_COMPONENT_MARKERS:
                if _phrase_present(row, marker) and not _phrase_present(target, marker):
                    return "TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING"
        if selected_is_total_row and hierarchy:
            for marker in _TOTAL_COMPONENT_MARKERS:
                if _phrase_present(hierarchy, marker) and not _phrase_present(
                    target, marker
                ):
                    return "TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING"

    if _is_related_party_table(table) and not _question_has_related_party_qualifier(
        item,
        qualifier_phrases=qualifier_phrases,
    ):
        return "UNQUALIFIED_RELATED_PARTY_TABLE"
    return None


def _qualified_counterparty_metric_column(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: Sequence[Any],
    item: Mapping[str, Any],
    requested_report_year: int,
    parse_decimal: Callable[[Any], Decimal | None],
) -> tuple[int, Decimal, str] | None:
    """Select a metric column for a named-counterparty row.

    Some investment schedules put the counterparty in the first column and
    repeat a pair of metric columns for each comparative period, for example
    ``31/12/2017: Tỷ lệ nắm giữ | Giá gốc`` and
    ``31/12/2016: Tỷ lệ nắm giữ | Giá gốc``.  A row-only lookup can bind the
    correct company but still read the percentage column.  This helper keeps
    that route exact: the requested metric must be visible in the column
    label and the requested year must be visible in the same local column
    group (including the nearest preceding spanning header).

    It intentionally handles only the metric family for which this layout is
    unambiguous today (``Giá gốc``).  Other named-counterparty layouts remain
    on the ordinary row/column contract and can abstain when their period or
    metric binding is not proven.
    """

    question = _normalize(item.get("question") or "")
    if "gia goc" not in question:
        return None
    labels = table.get("column_labels") or table.get("headers") or []
    if not isinstance(labels, list):
        return None
    if not isinstance(row, Sequence):
        return None

    options: list[tuple[int, Decimal, int]] = []
    for column_index, raw_label in enumerate(labels):
        if column_index >= len(row):
            continue
        if "gia goc" not in _normalize(raw_label):
            continue
        raw_value = parse_decimal(row[column_index])
        if raw_value is None:
            continue

        # OCR/table extraction frequently stores a spanning year in the
        # column immediately before its metric subcolumn.  Walk left only
        # until the nearest explicit year; this keeps 2017's ``Giá gốc``
        # separate from 2016's repeated ``Giá gốc``.
        period_parts = _column_period_texts(
            table,
            column_index=column_index,
            row_index=row_index,
        )
        for previous_index in range(column_index - 1, max(-1, column_index - 4), -1):
            period_parts.extend(
                _column_period_texts(
                    table,
                    column_index=previous_index,
                    row_index=row_index,
                )
            )
            if _YEAR_RE.search(_normalize(" ".join(period_parts))):
                break
        explicit_years = {
            int(value)
            for value in _YEAR_RE.findall(_normalize(" ".join(period_parts)))
        }
        if requested_report_year in explicit_years:
            options.append((column_index, raw_value, 3))

    if not options:
        return None
    best_score = max(score for _, _, score in options)
    best = [option for option in options if option[2] == best_score]
    if len(best) != 1:
        return None
    column_index, raw_value, _ = best[0]
    return column_index, raw_value, "qualified_metric_explicit_requested_year"


def _comparative_period_column(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    requested_year: int,
    source_year: int,
    parse_decimal: Callable[[Any], Decimal | None],
) -> tuple[int, Decimal, str] | None:
    """Select a comparative column only when it identifies the target period.

    Generic labels such as ``Năm trước`` are accepted only for a one-year
    fallback, where they unambiguously mean ``source_year - 1``.  Labels such
    as ``Năm nay``/``Số cuối năm`` do not prove the requested prior year and
    therefore fail closed.  If multiple target-period numeric columns exist,
    the result is ambiguous and is rejected.
    """

    numeric = [
        (index, parse_decimal(cell))
        for index, cell in enumerate(row[1:], start=1)
    ]
    numeric = [(index, value) for index, value in numeric if value is not None]
    if not numeric:
        return None

    options: list[tuple[int, Decimal, int, str]] = []
    for column_index, value in numeric:
        period_text = _normalize(
            " ".join(
                _column_period_texts(
                    table,
                    column_index=column_index,
                    row_index=row_index,
                )
            )
        )
        explicit_years = set(int(match) for match in _YEAR_RE.findall(period_text))
        if requested_year in explicit_years and source_year not in explicit_years:
            options.append((column_index, value, 3, "explicit_requested_year"))
            continue
        if (
            source_year == requested_year + 1
            and source_year not in explicit_years
            and any(
                marker in period_text
                for marker in ("nam truoc", "ky truoc", "prior year")
            )
            and not any(
                marker in period_text
                for marker in (
                    "phan loai lai",
                    "reclass",
                    "da trinh bay",
                    "bao cao nam truoc",
                    "presented in prior",
                )
            )
        ):
            options.append((column_index, value, 2, "generic_prior_year"))

    if not options:
        return None
    best_score = max(option[2] for option in options)
    best = [option for option in options if option[2] == best_score]
    if len(best) != 1:
        return None
    column_index, value, _, mode = best[0]
    return column_index, value, mode


def _column_lookup_candidates(
    table: Mapping[str, Any],
    *,
    item: Mapping[str, Any],
    metrics: Sequence[str],
    requested_report_year: int,
    report_year: int,
    parse_decimal: Callable[[Any], Decimal | None],
    candidate_evidence_window: Callable[
        [dict[str, Any], dict[str, Any] | None], list[dict[str, Any]]
    ],
    source_multiplier: Callable[[list[dict[str, Any]], dict[str, Any] | None], Decimal],
    requested_divisor: Callable[[str], Decimal],
    table_hint_rank: int | None,
) -> list[dict[str, Any]]:
    """Find a metric column crossed with an explicit period row.

    A material part of the corpus is transposed: the financial metric is a
    column header and ``Số dư cuối năm``/``Số dư đầu năm`` is the row selector.
    Treat this as a separate contract from row matching.  It is exact-year
    only and requires an explicit temporal row, so a generic header match
    cannot authorize an arbitrary numeric cell.
    """

    if report_year != requested_report_year:
        return []
    intent = _period_intent(item)
    if intent is None:
        return []
    labels = table.get("column_labels") or table.get("headers") or []
    if not isinstance(labels, list):
        return []
    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if not header_indices and rows:
        header_indices = {0}
    evidence = candidate_evidence_window({"evidence_window": []}, dict(table))
    multiplier = source_multiplier(evidence, dict(table))
    output: list[dict[str, Any]] = []
    matched_columns: list[tuple[int, float, str, int, str, int, int]] = []
    for column_index, raw_label in enumerate(labels):
        label = str(raw_label or "")
        label_tokens = _tokenize(
            label,
            noise=_LABEL_NOISE,
            keep_semantic_numbers=True,
        )
        if len(label_tokens) < 2:
            continue
        best: tuple[float, str, int, str, int, int] = (0.0, "none", 999, "", 999, 0)
        for metric_variant_index, metric in enumerate(metrics):
            metric_tokens = _tokenize(
                metric,
                noise=set(),
                keep_semantic_numbers=True,
            )
            match_score, match_mode, prefix = _ordered_match(
                metric_tokens,
                label_tokens,
                allow_compact=True,
            )
            if (match_score, -prefix, -metric_variant_index) > (
                best[0],
                -best[2],
                -best[4],
            ):
                best = (
                    match_score,
                    match_mode,
                    prefix,
                    metric,
                    metric_variant_index,
                    0,
                )
        match_score, match_mode, prefix_extra, metric, metric_variant_index, _ = best
        if match_score <= 0.0 or prefix_extra > 1:
            continue
        metric_tokens = _tokenize(
            metric,
            noise=set(),
            keep_semantic_numbers=True,
        )
        suffix_extra = len(label_tokens) - (prefix_extra + len(metric_tokens))
        if suffix_extra > 5:
            continue
        semantic_suffix_extra = _semantic_label_suffix_extra(
            label_tokens,
            prefix_extra=prefix_extra,
            target_tokens=metric_tokens,
        )
        matched_columns.append(
            (
                column_index,
                match_score,
                match_mode,
                prefix_extra,
                metric,
                metric_variant_index,
                semantic_suffix_extra,
            )
        )

    for (
        column_index,
        match_score,
        match_mode,
        prefix_extra,
        metric,
        metric_variant_index,
        semantic_suffix_extra,
    ) in matched_columns:
        metric_tokens = _tokenize(
            metric,
            noise=set(),
            keep_semantic_numbers=True,
        )
        for row_index, row in enumerate(rows):
            if not isinstance(row, list) or column_index >= len(row):
                continue
            if row_index in header_indices and not any(
                parse_decimal(cell) is not None for cell in row
            ):
                continue
            row_label = _row_label(row, parse_decimal=parse_decimal)
            row_period_score = _period_row_score(row_label, intent=intent)
            if row_period_score < 2.0:
                continue
            raw_value = parse_decimal(row[column_index])
            if raw_value is None:
                continue
            value = raw_value * multiplier / requested_divisor(str(item.get("question") or ""))
            kind = _table_kind(table)
            context_score = _context_score(
                table,
                item=item,
                metric_tokens=metric_tokens,
                match_mode=match_mode,
                prefix_extra=prefix_extra,
                row_label=row_label,
            )
            output.append(
                {
                    "value": value,
                    "raw_value": raw_value,
                    "source_multiplier": multiplier,
                    "row_index": row_index,
                    "column_index": column_index,
                    "row_label": row_label,
                    "document_id": str(table.get("document_id") or "").removesuffix(".txt"),
                    "internal_table_uid": str(table.get("internal_table_uid") or ""),
                    "ticker": str(table.get("ticker") or ""),
                    "requested_report_year": requested_report_year,
                    "source_report_year": report_year,
                    "report_year_offset": report_year - requested_report_year,
                    "period_selection_mode": f"column_metric_row_{intent}",
                    "scope": str(table.get("scope") or "unknown"),
                    "table_kind": kind,
                    "metric": metric,
                    "metric_variant_index": metric_variant_index,
                    "match_score": match_score,
                    "match_mode": match_mode,
                    "prefix_extra": prefix_extra,
                    "label_suffix_extra": semantic_suffix_extra,
                    "label_tail_score": -0.8 * semantic_suffix_extra,
                    "kind_score": _kind_score(kind, stock=_is_stock_question(item)),
                    "context_score": context_score,
                    "row_period_score": row_period_score,
                    "source_title": (
                        ((table.get("context_trace") or {}).get("source_title"))
                        if isinstance(table.get("context_trace"), Mapping)
                        else None
                    ),
                    "table_hint_rank": table_hint_rank,
                    "lookup_mode": "column_metric_row_period",
                    "table": dict(table),
                }
            )
    return output


def resolve_source_first_direct_lookup(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], Sequence[Mapping[str, Any]]],
    parse_decimal: Callable[[Any], Decimal | None],
    candidate_evidence_window: Callable[[dict[str, Any], dict[str, Any] | None], list[dict[str, Any]]],
    choose_year_column: Callable[
        [list[dict[str, Any]], int, list[Any], int | None, str], tuple[int, Decimal] | None
    ],
    source_multiplier: Callable[[list[dict[str, Any]], dict[str, Any] | None], Decimal],
    requested_divisor: Callable[[str], Decimal],
    report_year_neighbor_fallback: int | None = None,
    _source_report_year: int | None = None,
) -> dict[str, Any] | None:
    """Resolve one direct lookup from the frozen table population.

    The function is intentionally fail-closed.  If the same unqualified
    lookup has conflicting values across scopes/tables, it returns ``None`` so
    the existing candidate/model path remains visible instead of silently
    promoting a guess.
    """

    plan = _plan(item)
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    years = [int(value) for value in plan.get("years") or [] if str(value).isdigit()]
    if not years:
        years = [int(value) for value in _YEAR_RE.findall(str(item.get("question") or ""))]
    if not years:
        return None
    requested_report_year = years[0]
    if report_year_neighbor_fallback is not None:
        if report_year_neighbor_fallback not in {1, 2}:
            raise ValueError("report_year_neighbor_fallback must be 1 or 2")
        if _source_report_year is None:
            # Prefer the requested report year without ever pooling it with a
            # neighboring report.  Only a complete miss may fall back to the
            # next annual report, whose comparative column can still contain
            # the requested year.  This prevents a Y+1 duplicate from
            # displacing an unambiguous Y source.
            exact = resolve_source_first_direct_lookup(
                item,
                tables_by_pair=tables_by_pair,
                parse_decimal=parse_decimal,
                candidate_evidence_window=candidate_evidence_window,
                choose_year_column=choose_year_column,
                source_multiplier=source_multiplier,
                requested_divisor=requested_divisor,
                report_year_neighbor_fallback=None,
                _source_report_year=requested_report_year,
            )
            if exact is not None:
                return exact
            return resolve_source_first_direct_lookup(
                item,
                tables_by_pair=tables_by_pair,
                parse_decimal=parse_decimal,
                candidate_evidence_window=candidate_evidence_window,
                choose_year_column=choose_year_column,
                source_multiplier=source_multiplier,
                requested_divisor=requested_divisor,
                report_year_neighbor_fallback=None,
                _source_report_year=requested_report_year + report_year_neighbor_fallback,
            )

    # ``requested_report_year`` is the period the question asks for.  The
    # source report may be a later annual report when the controlled fallback
    # above is active; column selection must still target the requested year.
    report_year = (
        requested_report_year if _source_report_year is None else _source_report_year
    )
    known_tickers = {
        str(ticker).upper()
        for ticker, year in tables_by_pair
        if str(ticker).strip() and int(year) == report_year
    }
    tickers = _candidate_tickers(item, known_tickers=known_tickers)
    if not tickers:
        return None
    metrics = _metric_variants(item, tickers=tickers)
    if not metrics:
        return None
    question = str(item.get("question") or "")
    normalized_question = _normalize(question)
    requested_scope = _question_scope(item)
    stock = _is_stock_question(item)
    stats = Counter()
    candidates: list[dict[str, Any]] = []
    hint_ranks = _candidate_hint_ranks(item)
    qualifier_phrases = _qualifier_phrases(item)
    action_phrases = _action_phrases(item)

    for ticker in tickers:
        tables = tables_by_pair.get((ticker, report_year), ())
        for table in tables:
            stats["tables_considered"] += 1
            if not _scope_compatible(table, requested_scope=requested_scope):
                stats["tables_rejected_scope"] += 1
                continue
            if not _table_period_compatible(
                table,
                item=item,
                requested_year=requested_report_year,
            ):
                stats["tables_rejected_period_intent"] += 1
                continue
            table_context = _table_context(table)
            table_uid = str(table.get("internal_table_uid") or "")
            table_hint_rank = hint_ranks.get(table_uid)
            column_candidates = _column_lookup_candidates(
                table,
                item=item,
                metrics=metrics,
                requested_report_year=requested_report_year,
                report_year=report_year,
                parse_decimal=parse_decimal,
                candidate_evidence_window=candidate_evidence_window,
                source_multiplier=source_multiplier,
                requested_divisor=requested_divisor,
                table_hint_rank=table_hint_rank,
            )
            if column_candidates:
                accepted_column_candidates: list[dict[str, Any]] = []
                for column_candidate in column_candidates:
                    feedback_reason = _source_first_feedback_reason(
                        item,
                        table,
                        row_index=int(column_candidate["row_index"]),
                        row_text=str(column_candidate.get("row_label") or ""),
                        column_index=int(column_candidate["column_index"]),
                        target_text=str(column_candidate.get("metric") or ""),
                        parse_decimal=parse_decimal,
                        qualifier_phrases=qualifier_phrases,
                    )
                    if feedback_reason is not None:
                        stats[f"feedback_{feedback_reason}"] += 1
                        continue
                    accepted_column_candidates.append(column_candidate)
                candidates.extend(accepted_column_candidates)
                stats["column_metric_row_matches"] += len(accepted_column_candidates)
            rows = table.get("rows") or []
            header_indices = {int(value) for value in table.get("header_row_indices") or []}
            maturity_total_table_contract = (
                _financial_liability_maturity_total_table_contract(
                    item,
                    table,
                    table_context=table_context,
                )
            )
            receivables_total_table_contract = (
                _financial_receivables_total_table_contract(
                    item,
                    table,
                    table_context=table_context,
                )
            )
            for row_index, row in enumerate(rows):
                if not isinstance(row, list) or len(row) < 2:
                    continue
                # The table normalizer can mark a real data row as a header in
                # multi-section OCR tables.  Empty/non-numeric header rows
                # are safe to skip; numeric rows still go through metric and
                # period gates so repeated annual sections remain recoverable.
                if row_index in header_indices and not any(
                    parse_decimal(cell) is not None for cell in row
                ):
                    continue
                label = _row_label(row, parse_decimal=parse_decimal)
                row_text = _normalize(label)
                if maturity_total_table_contract and row_text not in {"tong", "tong cong"}:
                    # The metric phrase also occurs in component rows such as
                    # ``Các nghĩa vụ nợ tài chính khác``.  Once the local
                    # maturity-table contract is recognized, only its generic
                    # total row is eligible.
                    stats["contextual_financial_liability_non_total_row_rejected"] += 1
                    continue
                if receivables_total_table_contract and row_text not in {
                    "tong",
                    "tong cong",
                }:
                    stats["contextual_financial_receivables_non_total_row_rejected"] += 1
                    continue
                label_tokens = _tokenize(
                    label,
                    noise=_LABEL_NOISE,
                    keep_semantic_numbers=True,
                )
                # These contracts are evaluated per hydrated table/row.  They
                # recover OCR layouts where the semantic metric lives in the
                # table heading or a column header rather than in the row
                # label.  They remain source-first proposals: the selected
                # cell is still replayed below and the normal scope/duplicate
                # gates remain authoritative.
                contextual_kind = _contextual_direct_row_kind(
                    item,
                    table,
                    label,
                    row_index=row_index,
                    parse_decimal=parse_decimal,
                    table_context=table_context,
                    financial_liability_maturity_total=maturity_total_table_contract,
                    financial_receivables_total=receivables_total_table_contract,
                )
                hierarchical_choice = _hierarchical_section_metric_choice(
                    table,
                    row_index=row_index,
                    row=row,
                    item=item,
                    parse_decimal=parse_decimal,
                )
                transposed_choice = _transposed_header_metric_choice(
                    table,
                    row_index=row_index,
                    row=row,
                    item=item,
                    metrics=metrics,
                    parse_decimal=parse_decimal,
                )
                if (
                    len(label_tokens) < 2
                    and contextual_kind is None
                    and hierarchical_choice is None
                    and transposed_choice is None
                ):
                    continue
                best: tuple[float, str, int, str, int] = (0.0, "none", 999, "", 999)
                for metric_variant_index, metric in enumerate(metrics):
                    metric_tokens = _tokenize(
                        metric,
                        noise=set(),
                        keep_semantic_numbers=True,
                    )
                    match_score, match_mode, prefix = _ordered_match(
                        metric_tokens,
                        label_tokens,
                    )
                    if (match_score, -prefix, -metric_variant_index) > (
                        best[0],
                        -best[2],
                        -best[4],
                    ):
                        best = (
                            match_score,
                            match_mode,
                            prefix,
                            metric,
                            metric_variant_index,
                        )
                match_score, match_mode, prefix_extra, metric, metric_variant_index = best
                contextual_row_contract = contextual_kind is not None
                contextual_section_contract = hierarchical_choice is not None
                contextual_header_contract = transposed_choice is not None
                if contextual_row_contract:
                    match_score = 1.0
                    match_mode = f"contextual_{contextual_kind}"
                    prefix_extra = 0
                    metric = metrics[0]
                    metric_variant_index = 0
                elif contextual_section_contract:
                    match_score = 1.0
                    match_mode = hierarchical_choice[2]
                    prefix_extra = 0
                    metric = metrics[0]
                    metric_variant_index = 0
                elif contextual_header_contract:
                    match_score = 1.0
                    match_mode = transposed_choice[2]
                    prefix_extra = 0
                    metric = transposed_choice[3]
                    metric_variant_index = 0
                if match_score <= 0.0:
                    stats["rows_no_metric_match"] += 1
                    continue
                target_tokens = _tokenize(
                    metric,
                    noise=set(),
                    keep_semantic_numbers=True,
                )
                target_text = _compact_tokens(target_tokens)
                qualified_counterparty_row = bool(
                    qualifier_phrases
                    and "gia goc" in normalized_question
                    and all(
                        all(token in row_text.split() for token in phrase.split())
                        for phrase in qualifier_phrases
                    )
                )
                requested_actions = [
                    action
                    for action in action_phrases
                    if action in target_text
                ]
                qualified_row_layout = bool(
                    qualifier_phrases
                    and requested_actions
                    and all(phrase in row_text for phrase in qualifier_phrases)
                    and any(action in row_text for action in requested_actions)
                )
                # The compiled operand should describe the row, not only a
                # generic suffix inside a longer concept (``vay ngắn hạn``
                # inside ``phải thu về cho vay ngắn hạn``).  Ordinal prefixes
                # have already been removed from the row label, so >2
                # semantic prefix tokens is a safe fail-closed boundary.  A
                # balance-sheet row beginning ``Phải thu về cho vay ...`` is
                # the one generic accounting construction we intentionally
                # admit for a question asking for ``cho vay ...``.
                allowed_receivable_loan_prefix = (
                    prefix_extra == 3
                    and label_tokens[:3] == ["phai", "thu", "ve"]
                    and "cho vay" in _compact_tokens(target_tokens)
                )
                if (
                    prefix_extra > 2
                    and not allowed_receivable_loan_prefix
                    and not qualified_row_layout
                ):
                    stats["rows_rejected_semantic_prefix"] += 1
                    continue
                suffix_extra = len(label_tokens) - (
                    prefix_extra + len(target_tokens)
                )
                # A primary row may have a short ``(a)``/note suffix, but a
                # long explanatory tail is a definition/footnote row, not
                # the reported amount.  Apply this to fuzzy matches too:
                # fuzzy matching ``Nợ đủ tiêu chuẩn`` against the first words
                # of a 70-token definition was the original Q62 failure.
                if suffix_extra > 4 and not qualified_row_layout:
                    stats["rows_rejected_explanatory_suffix"] += 1
                    continue
                if suffix_extra > 10:
                    stats["rows_rejected_explanatory_suffix"] += 1
                    continue
                semantic_suffix_extra = _semantic_label_suffix_extra(
                    label_tokens,
                    prefix_extra=prefix_extra,
                    target_tokens=target_tokens,
                )
                if report_year != requested_report_year and prefix_extra:
                    # A comparative report is already a weaker provenance
                    # route.  Do not let a broad suffix match such as
                    # ``trả lãi tiền gửi`` satisfy a question for ``tiền
                    # gửi`` or let an EPS subtraction row stand in for a
                    # balance-sheet row.  Exact-year lookup retains the
                    # small-prefix behavior above; the neighbor arm is
                    # intentionally stricter.
                    stats["neighbor_rejected_semantic_prefix"] += 1
                    continue
                if (
                    len(label_tokens) < len(target_tokens)
                    and not (
                        contextual_row_contract
                        or contextual_section_contract
                        or contextual_header_contract
                    )
                ):
                    context = table_context
                    if "no cho vay" not in context and "du no" not in context:
                        stats["rows_rejected_short_fuzzy_label"] += 1
                        continue
                if "chung khoan no" in target_text and "thu lai" in row_text:
                    stats["rows_rejected_semantic_row_conflict"] += 1
                    continue
                if (
                    "chi phi du phong" in target_text
                    and "rui ro tin dung" in row_text
                    and "rui ro tin dung" not in target_text
                    and "rui ro tin dung" not in normalized_question
                ):
                    # ``Chi phí dự phòng`` in a note is not interchangeable
                    # with the P&L line ``Chi phí dự phòng rủi ro tín dụng``.
                    # The latter is a tempting exact prefix match, but it is a
                    # different contract and often has a different sign.
                    stats["rows_rejected_generic_provision_risk_row_conflict"] += 1
                    continue
                if "trai phieu" in target_text:
                    collateral_markers = (
                        "the chap",
                        "cam co",
                        "dua di the chap",
                        "giay to co gia",
                        "tai san giay to co gia",
                    )
                    requested_collateral_markers = (
                        "the chap",
                        "cam co",
                        "dua di",
                    )
                    if (
                        contextual_kind != "ordinary_bond_total"
                        and any(marker in table_context for marker in collateral_markers)
                        and not any(
                        marker in normalized_question
                        for marker in requested_collateral_markers
                        )
                    ):
                        # A bond can appear both in an investment schedule and
                        # in a collateral register.  The collateral value is
                        # not the reported investment balance unless the
                        # question asks for pledged/collateral assets.
                        stats["rows_rejected_collateral_bond_context"] += 1
                        continue
                if (
                    "quy khen thuong" in target_text
                    and "tru" in row_text
                    and "tru" not in target_text
                ):
                    stats["rows_rejected_deduction_row_conflict"] += 1
                    continue
                if (
                    "du phong" in target_text
                    and "trich lap" in row_text
                    and "trich lap" not in target_text
                ):
                    stats["rows_rejected_provision_flow_row_conflict"] += 1
                    continue
                if (
                    "co dong" in normalized_question
                    and "khong kiem soat" in row_text
                    and "khong kiem soat" not in normalized_question
                ):
                    stats["rows_rejected_minority_row_conflict"] += 1
                    continue
                if (
                    "dai han" in row_text
                    and "dai han" not in target_text
                    and "chi phi san xuat" in target_text
                ):
                    stats["rows_rejected_long_term_detail_conflict"] += 1
                    continue
                combined_context = f"{row_text} {table_context}"
                if qualifier_phrases and not all(
                    all(token in combined_context.split() for token in phrase.split())
                    for phrase in qualifier_phrases
                ):
                    stats["rows_rejected_missing_named_qualifier"] += 1
                    continue
                if action_phrases and qualifier_phrases and not any(
                    phrase in combined_context for phrase in action_phrases
                ):
                    stats["rows_rejected_missing_qualifier_action"] += 1
                    continue
                evidence = candidate_evidence_window({"evidence_window": []}, dict(table))
                qualified_choice = None
                if qualified_counterparty_row:
                    qualified_choice = _qualified_counterparty_metric_column(
                        table,
                        row_index=row_index,
                        row=row,
                        item=item,
                        requested_report_year=requested_report_year,
                        parse_decimal=parse_decimal,
                    )
                    if qualified_choice is None:
                        # A named row without a proved metric-period column
                        # is exactly the failure mode this route is meant to
                        # prevent (for example reading ownership percentage
                        # as investment cost).  Do not fall back to the
                        # generic numeric-column guess.
                        stats["qualified_counterparty_no_metric_period_column"] += 1
                        continue
                special_choice = None
                if hierarchical_choice is not None:
                    special_choice = hierarchical_choice
                elif transposed_choice is not None:
                    special_choice = transposed_choice[:3]
                elif contextual_kind == "rent_commitment_total":
                    special_choice = _contextual_period_column_choice(
                        table,
                        row_index=row_index,
                        row=row,
                        item=item,
                        requested_report_year=requested_report_year,
                        parse_decimal=parse_decimal,
                    )
                elif contextual_kind == "rent_commitment_maturity_1y":
                    special_choice = _contextual_period_column_choice(
                        table,
                        row_index=row_index,
                        row=row,
                        item=item,
                        requested_report_year=requested_report_year,
                        parse_decimal=parse_decimal,
                    )
                elif contextual_kind == "investment_property_depreciation_total":
                    special_choice = _contextual_total_column_choice(
                        table,
                        row=row,
                        parse_decimal=parse_decimal,
                    )
                elif contextual_kind == "financial_assets_total":
                    special_choice = _contextual_total_column_choice(
                        table,
                        row=row,
                        parse_decimal=parse_decimal,
                    )
                elif contextual_kind == "financial_liability_maturity_total":
                    special_choice = _contextual_total_column_choice(
                        table,
                        row=row,
                        parse_decimal=parse_decimal,
                    )
                elif contextual_kind == "financial_receivables_total":
                    special_choice = _contextual_receivables_total_column_choice(
                        table,
                        row=row,
                        parse_decimal=parse_decimal,
                    )
                elif contextual_kind in {
                    "ordinary_bond_total",
                    "cash_and_equivalents_total",
                }:
                    special_choice = _contextual_period_column_choice(
                        table,
                        row_index=row_index,
                        row=row,
                        item=item,
                        requested_report_year=requested_report_year,
                        parse_decimal=parse_decimal,
                    )
                    if special_choice is None and contextual_kind == "ordinary_bond_total":
                        numeric_cells = [
                            (column_index, parse_decimal(cell))
                            for column_index, cell in enumerate(row[1:], start=1)
                        ]
                        numeric_cells = [
                            (column_index, value)
                            for column_index, value in numeric_cells
                            if value is not None
                        ]
                        if len(numeric_cells) == 1:
                            column_index, raw_value = numeric_cells[0]
                            special_choice = (
                                column_index,
                                raw_value,
                                "contextual_single_period_value",
                            )
                if contextual_row_contract and special_choice is None:
                    stats[f"{contextual_kind}_no_special_column"] += 1
                    continue
                chosen = (
                    (special_choice[0], special_choice[1])
                    if special_choice is not None
                    else (
                        (qualified_choice[0], qualified_choice[1])
                        if qualified_choice is not None
                        else choose_year_column(
                            evidence,
                            row_index,
                            row,
                            requested_report_year,
                            question,
                        )
                    )
                )
                if chosen is None:
                    stats["rows_no_period_column"] += 1
                    continue
                column_index, raw_value = chosen
                feedback_reason = _source_first_feedback_reason(
                    item,
                    table,
                    row_index=row_index,
                    row_text=row_text,
                    column_index=column_index,
                    target_text=target_text,
                    parse_decimal=parse_decimal,
                    qualifier_phrases=qualifier_phrases,
                )
                if feedback_reason is not None:
                    stats[f"feedback_{feedback_reason}"] += 1
                    continue
                period_selection_mode = (
                    special_choice[2]
                    if special_choice is not None
                    else (
                        qualified_choice[2]
                        if qualified_choice is not None
                        else "exact_report_year_column"
                    )
                )
                if report_year != requested_report_year:
                    comparative = _comparative_period_column(
                        table,
                        row_index=row_index,
                        row=row,
                        requested_year=requested_report_year,
                        source_year=report_year,
                        parse_decimal=parse_decimal,
                    )
                    if comparative is None:
                        stats["neighbor_no_target_period_column"] += 1
                        continue
                    column_index, raw_value, period_selection_mode = comparative
                    stats[
                        f"neighbor_period_selection_{period_selection_mode}"
                    ] += 1
                multiplier = source_multiplier(evidence, dict(table))
                value = raw_value * multiplier / requested_divisor(question)
                kind = _table_kind(table)
                context_score = _context_score(
                    table,
                    item=item,
                    metric_tokens=_tokenize(
                        metric,
                        noise=set(),
                        keep_semantic_numbers=True,
                    ),
                    match_mode=match_mode,
                    prefix_extra=prefix_extra,
                    row_label=label,
                )
                row_period_score = _row_period_context_score(
                    rows,
                    row_index=row_index,
                    report_year=report_year,
                )
                candidates.append(
                    {
                        "value": value,
                        "raw_value": raw_value,
                        "source_multiplier": multiplier,
                        "row_index": row_index,
                        "column_index": column_index,
                        "row_label": label,
                        "document_id": str(table.get("document_id") or "").removesuffix(".txt"),
                        "internal_table_uid": str(table.get("internal_table_uid") or ""),
                        "ticker": ticker,
                        "requested_report_year": requested_report_year,
                        "source_report_year": report_year,
                        "report_year_offset": report_year - requested_report_year,
                        "period_selection_mode": period_selection_mode,
                        "scope": str(table.get("scope") or "unknown"),
                        "table_kind": kind,
                        "metric": metric,
                        "metric_variant_index": metric_variant_index,
                        "match_score": match_score,
                        "match_mode": match_mode,
                        "prefix_extra": prefix_extra,
                        "label_suffix_extra": semantic_suffix_extra,
                        "label_tail_score": -0.8 * semantic_suffix_extra,
                        "kind_score": _kind_score(kind, stock=stock),
                        "context_score": context_score,
                        "row_period_score": row_period_score,
                        "table_hint_rank": table_hint_rank,
                        "lookup_mode": (
                            "contextual_row"
                            if contextual_row_contract
                            else (
                                "contextual_section"
                                if contextual_section_contract
                                else (
                                    "contextual_header_metric"
                                    if contextual_header_contract
                                    else "row_metric"
                                )
                            )
                        ),
                        "source_title": (
                            ((table.get("context_trace") or {}).get("source_title"))
                            if isinstance(table.get("context_trace"), Mapping)
                            else None
                        ),
                        "table": dict(table),
                    }
                )
                stats["row_matches"] += 1

    if not candidates:
        return None

    # A contextual gross-receivables contract is stronger than the relaxed
    # ``khoản phải thu`` row matcher.  The same issuer/report can contain
    # income and financial-note rows such as ``Lãi từ các khoản cho vay và
    # phải thu`` alongside the disclosed doubtful-receivables schedule.  If
    # the schedule has produced a replayable total candidate, retain only
    # that explicitly bound family before the generic metric/tie-break gates;
    # otherwise a semantically unrelated row can make a valid contextual
    # answer appear ambiguous.  This does not override scope, period, cell,
    # or same-table duplicate checks below.
    contextual_receivables_candidates = [
        row
        for row in candidates
        if row.get("match_mode") == "contextual_financial_receivables_total"
    ]
    if contextual_receivables_candidates:
        candidates = contextual_receivables_candidates
        stats["rows_restricted_by_receivables_contextual_contract"] += len(
            candidates
        )

    # The operating-lease note normally contains both a headline total and
    # several maturity buckets.  When the question asks for the one-year
    # bucket, the contextual row contract is stronger than a shorter exact
    # metric prefix such as ``cam kết thuê hoạt động`` on the headline row.
    contextual_rent_maturity_candidates = [
        row
        for row in candidates
        if row.get("match_mode") == "contextual_rent_commitment_maturity_1y"
    ]
    if contextual_rent_maturity_candidates:
        candidates = contextual_rent_maturity_candidates
        stats["rows_restricted_by_rent_maturity_contextual_contract"] += len(
            candidates
        )

    # A question-level strict metric (for example ``Tổng chi phí hoạt động``)
    # must outrank the relaxed fallback (``chi phí hoạt động``) whenever the
    # strict form has a clean exact hit.  Without this boundary, a child row
    # can be treated as equivalent to its total merely because both contain
    # the relaxed noun phrase.
    strict_exact_candidates = [
        row
        for row in candidates
        if int(row.get("metric_variant_index", 999)) == 0
        and float(row["match_score"]) >= 0.999
    ]
    if strict_exact_candidates and len(strict_exact_candidates) < len(candidates):
        candidates = strict_exact_candidates
        stats["rows_restricted_by_strict_metric_variant"] += len(candidates)

    # Keep exact/near-exact labels ahead of fuzzy matches.  A fuzzy candidate
    # may still win when it is the only match (the FTS OCR typo case), but it
    # cannot displace a clean metric match from another table.
    best_match_score = max(float(row["match_score"]) for row in candidates)
    exact_candidates = [
        row for row in candidates if float(row["match_score"]) >= 0.999
    ]
    if exact_candidates:
        # A clean row label must not be made ambiguous by a nearby fuzzy row
        # (for example ``... dài hạn`` versus ``... ngắn hạn``).
        candidates = exact_candidates
    else:
        candidates = [
            row for row in candidates if float(row["match_score"]) >= best_match_score - 0.03
        ]

    # Scope is part of the identity of a financial statement, not merely a
    # table-function preference.  Check an unqualified lookup before the
    # preferred-kind reduction below: a consolidated and a separate table can
    # contain the same exact row with different values, and choosing the
    # higher-ranked table kind would otherwise hide that semantic conflict.
    pre_kind_scope_values: dict[str, set[str]] = {}
    for row in candidates:
        pre_kind_scope_values.setdefault(str(row["scope"]), set()).add(
            _candidate_value_signature(row["value"])
        )
    if requested_scope is None and len(pre_kind_scope_values) > 1:
        pre_kind_all_values = {
            value
            for values in pre_kind_scope_values.values()
            for value in values
        }
        if len(pre_kind_all_values) > 1:
            stats["rejected_conflicting_scopes_before_table_function"] += 1
            return None

    # A metric may be repeated in a balance sheet, a supporting note and a
    # segment schedule.  For a single reported value, the table function is a
    # semantic constraint rather than a soft retrieval score: retain the
    # highest-intent table family before testing duplicate values.  This is
    # what prevents a stock lookup such as ``trả trước ... dài hạn`` from
    # being made ambiguous by a note/segment copy of the same phrase.
    best_kind_score = max(float(row["kind_score"]) for row in candidates)
    candidates = [
        row
        for row in candidates
        if float(row["kind_score"]) >= best_kind_score - 0.01
    ]
    stats["rows_retained_preferred_table_function"] += len(candidates)

    # Retrieval rank is navigation metadata, not answer authority.  Keep it
    # in the candidate ledger for auditability, but never use it to discard a
    # semantically valid candidate or choose between conflicting values.
    # Re-sort with context before kind only when the candidate is explicitly
    # in a semantically excluded provision context.  Otherwise table function
    # and label completeness are the stable source-first preferences.
    candidates.sort(
        key=lambda row: (
            -float(row["match_score"]),
            -_candidate_semantic_quality(row),
            int(row["prefix_extra"]),
            int(row.get("label_suffix_extra", 0)),
            str(row["internal_table_uid"]),
            int(row["row_index"]),
        )
    )
    best = candidates[0]

    # If scope was omitted, duplicate separate/consolidated reports are useful
    # evidence only when their replayed values agree.  This prevents a clean
    # row match from choosing one report arbitrarily for MBB/VPI-like cases.
    scope_values: dict[str, set[str]] = {}
    for row in candidates:
        scope_values.setdefault(str(row["scope"]), set()).add(
            _candidate_value_signature(row["value"])
        )
    if requested_scope is None and len(scope_values) > 1:
        all_values = {value for values in scope_values.values() for value in values}
        if len(all_values) > 1:
            stats["rejected_conflicting_scopes"] += 1
            return None

    # Same-scope duplicate schedules are accepted only if the highest-quality
    # contenders agree, or if the selected row has a clear table-context lead
    # (for example reported VAMC principal vs a provision schedule).
    close = [
        row
        for row in candidates
        if (
            float(row["match_score"]) >= float(best["match_score"]) - 0.02
            and _candidate_semantic_quality(row)
            >= _candidate_semantic_quality(best) - 1.0
        )
    ]
    close_values = {_candidate_value_signature(row["value"]) for row in close}
    if len(close_values) > 1:
        lead = (
            _candidate_semantic_quality(best)
            - max(
                _candidate_semantic_quality(row)
                for row in close
                if row is not best
            )
            if len(close) > 1
            else 0.0
        )
        if lead < 1.5:
            stats["rejected_ambiguous_same_quality"] += 1
            return None

    table = best["table"]
    source = {
        "raw_value_decimal": str(best["raw_value"]),
        "value": best["value"],
        "role": "source_first_exact_row_selected",
        "document_id": best["document_id"],
        "internal_table_uid": best["internal_table_uid"],
        "row_index": best["row_index"],
        "column_index": best["column_index"],
        "row_label": best["row_label"],
        "source_to_vnd_multiplier": str(best["source_multiplier"]),
        "question_output_divisor": str(requested_divisor(question)),
        "candidate_source": PROTOCOL,
        "source_first_protocol": PROTOCOL,
        "source_first_match_mode": best["match_mode"],
        "source_first_table_kind": best["table_kind"],
        "source_first_scope": best["scope"],
        "source_first_source_title": best["source_title"],
        "requested_report_year": best["requested_report_year"],
        "source_report_year": best["source_report_year"],
        "report_year_offset": best["report_year_offset"],
        "period_selection_mode": best["period_selection_mode"],
        "promotion_allowed": False,
    }
    selection = {
        "score": 100.0 * float(best["match_score"]),
        "value": best["value"],
        "raw_value": best["raw_value"],
        "source_multiplier": best["source_multiplier"],
        "row_index": best["row_index"],
        "column_index": best["column_index"],
        "row_label": best["row_label"],
        "document_id": best["document_id"],
        "internal_table_uid": best["internal_table_uid"],
        "candidate_rank": 0,
        "candidate_source": PROTOCOL,
        "research_candidate_only": False,
        "source_first": True,
        "source_first_protocol": PROTOCOL,
        "source_first_match_mode": best["match_mode"],
        "source_first_table_kind": best["table_kind"],
        "source_first_scope": best["scope"],
        "source_first_context_score": best["context_score"],
        "source_first_kind_score": best["kind_score"],
        "source_first_row_period_score": best["row_period_score"],
        "requested_report_year": best["requested_report_year"],
        "source_report_year": best["source_report_year"],
        "report_year_offset": best["report_year_offset"],
        "period_selection_mode": best["period_selection_mode"],
        "candidate_filter_status": "SOURCE_FIRST_EXACT_ROW_REPLAY",
        "validity_probability": 1.0,
        "validity_model_status": "source_first_coordinate_replay",
    }
    return {
        "answer": best["value"],
        "sources": [source],
        "selection": selection,
        "tier": PROTOCOL,
        "protocol": PROTOCOL,
        "diagnostics": {
            "ticker": best["ticker"],
            "report_year": requested_report_year,
            "source_report_year": best["source_report_year"],
            "report_year_offset": best["report_year_offset"],
            "period_selection_mode": best["period_selection_mode"],
            "requested_scope": requested_scope,
            "metric_variants": metrics,
            "match_mode": best["match_mode"],
            "match_score": best["match_score"],
            "table_kind": best["table_kind"],
            "table_context_score": best["context_score"],
            "kind_score": best["kind_score"],
            "row_period_score": best["row_period_score"],
            "candidate_count_after_match_gate": len(candidates),
            "candidate_value_count_after_match_gate": len(
                {_candidate_value_signature(row["value"]) for row in candidates}
            ),
            "stats": dict(stats),
            "answer_authority": "current_structured_table_decimal_replay",
            "promotion_allowed": False,
        },
    }


def build_source_first_direct_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], Sequence[Mapping[str, Any]]],
    **resolver_kwargs: Any,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the source-first resolver once per question and collect telemetry."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if str(_plan(item).get("family") or "") != "direct_lookup":
            stats["questions_skipped_non_direct"] += 1
            continue
        result = resolve_source_first_direct_lookup(
            item,
            tables_by_pair=tables_by_pair,
            **resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"match_mode_{result['diagnostics']['match_mode']}"] += 1
        stats[f"kind_{result['diagnostics']['table_kind']}"] += 1
        stats[
            f"report_year_offset_{int(result['diagnostics'].get('report_year_offset') or 0)}"
        ] += 1
    return answers, {
        "protocol": PROTOCOL,
        "question_count": len(items_by_question),
        "answer_count": len(answers),
        "stats": dict(stats),
        "report_year_neighbor_fallback": resolver_kwargs.get(
            "report_year_neighbor_fallback"
        ),
        "exact_report_year_first": True,
        "answer_authority": "current_structured_table_decimal_replay",
        "promotion_allowed": False,
        "candidate_only": True,
    }
