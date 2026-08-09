from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

from .schemas import OperandSpec, QuestionFamily, QuestionPlan


YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
PERCENT_RE = re.compile(r"%|phần trăm|tỷ lệ|tỉ lệ", re.IGNORECASE)

SCOPE_PATTERNS = {
    "separate": re.compile(r"\bcông ty mẹ\b|\briêng lẻ\b", re.IGNORECASE),
    "consolidated": re.compile(r"\bhợp nhất\b", re.IGNORECASE),
}

UNIT_PATTERNS = [
    ("thousand_vnd", re.compile(r"\bnghìn đồng\b", re.IGNORECASE)),
    ("million_vnd", re.compile(r"\btriệu đồng\b", re.IGNORECASE)),
    ("billion_vnd", re.compile(r"\btỷ đồng\b", re.IGNORECASE)),
    ("trillion_vnd", re.compile(r"\bnghìn tỷ đồng\b", re.IGNORECASE)),
    ("percent", re.compile(r"%|\bphần trăm\b", re.IGNORECASE)),
    ("days", re.compile(r"\bbao nhiêu ngày\b|\bsố ngày\b", re.IGNORECASE)),
    ("times", re.compile(r"\bbao nhiêu lần\b|\bvòng\b", re.IGNORECASE)),
]

FAMILY_RULES: list[tuple[QuestionFamily, re.Pattern[str], float]] = [
    (
        "conditional_analytical",
        re.compile(
            r"trung vị|xếp hạng|đứng thứ|thỏa mãn|lớn hơn.*trung bình|"
            r"nhỏ hơn.*trung bình|nếu .* tăng|nếu .* giảm|giả sử|kịch bản|"
            r"công ty nào|năm nào|bao nhiêu công ty",
            re.IGNORECASE,
        ),
        0.92,
    ),
    (
        "multi_entity_or_period_aggregation",
        re.compile(
            r"trung bình|bình quân|tổng cộng|tổng .* của các|cao nhất|thấp nhất|"
            r"lớn nhất|nhỏ nhất|bao nhiêu năm|bao nhiêu công ty|đếm",
            re.IGNORECASE,
        ),
        0.86,
    ),
    (
        "cross_entity_comparison",
        re.compile(
            r"so với|cao hơn .* bao nhiêu|thấp hơn .* bao nhiêu|chênh lệch giữa|"
            r"giữa .* và .*",
            re.IGNORECASE,
        ),
        0.84,
    ),
    (
        "ratio_or_derived",
        re.compile(
            r"tỷ lệ|tỉ lệ|biên lợi nhuận|ROE|ROA|CAGR|vòng quay|"
            r"khả năng thanh toán|trên doanh thu|trên tổng tài sản|"
            r"loan.?to.?deposit|LDR",
            re.IGNORECASE,
        ),
        0.9,
    ),
    (
        "temporal_change",
        re.compile(
            r"tăng bao nhiêu|giảm bao nhiêu|thay đổi bao nhiêu|chênh lệch|"
            r"tăng trưởng|từ năm .* đến năm|so với năm",
            re.IGNORECASE,
        ),
        0.88,
    ),
]

# Some Vietnamese report-row labels contain words that otherwise look like an
# operation (``Tổng cộng tài sản``, ``Tỷ lệ sở hữu`` or ``Lỗ chênh lệch tỷ
# giá``).  These rules are intentionally narrow: they re-route only a single
# disclosed value, never a group/range/comparison question, to direct lookup.
REPORTED_RATIO_RE = re.compile(
    r"^(?:tổng\s+)?t(?:ỷ|ỉ)\s+lệ\s+(?:quyền\s+biểu\s+quyết|biểu\s+quyết|"
    r"sở\s+hữu|lợi\s+ích\s+kinh\s+tế|sở\s+hữu\s+trên\s+vốn\s+thực\s+góp)",
    re.IGNORECASE,
)
REPORTED_TOTAL_RE = re.compile(
    r"^(?:giá\s+trị\s+còn\s+lại|tổng\s+cộng|tổng\s+số|"
    r"số\s+(?:lượng\s+)?cổ\s+phiếu\s+phổ\s+thông\s+bình\s+quân\s+gia\s+quyền|"
    r"công\s+ty.+\bcó\s+số\s+lượng\s+cổ\s+phiếu\s+phổ\s+thông\s+bình\s+quân\s+gia\s+quyền)",
    re.IGNORECASE,
)
REPORTED_FX_ROW_RE = re.compile(r"^(?:lỗ|lãi)\s+chênh\s+lệch\s+tỷ\s+giá", re.IGNORECASE)
COMPOSED_LOOKUP_BLOCK_RE = re.compile(
    r"\b(?:trong\s+(?:các\s+)?(?:nhóm|doanh\s+nghiệp)|trong\s+giai\s+đoạn|"
    r"so\s+với|từ\s+năm|giữa\s+(?:năm|hai)|cao\s+nhất|thấp\s+nhất|trung\s+vị|"
    r"đồng\s+thời|tăng\s+bao\s+nhiêu|giảm\s+bao\s+nhiêu|thay\s+đổi\s+bao\s+nhiêu|"
    r"có\s+bao\s+nhiêu\s+(?:doanh\s+nghiệp|công\s+ty|năm)|trừ\s+đi|các\s+năm|"
    r"trong\s+số)\b",
    re.IGNORECASE,
)
MULTI_SUBJECT_RE = re.compile(
    r"\)\s*(?:,|và)\s*(?:ctcp|công\s+ty|tổng\s+công\s+ty)|"
    r"\b(?:ctcp|công\s+ty|tổng\s+công\s+ty)\b.{0,90}\b(?:và|,\s*(?:ctcp|công\s+ty|tổng\s+công\s+ty))\b",
    re.IGNORECASE,
)
TICKER_TOKEN_RE = re.compile(r"\(([A-Z]{2,6})\)|\b([A-Z]{2,6})\b")
NON_TICKER_TOKENS = {"CP", "CTCP", "TMCP", "TNDN", "VND", "HĐQT", "BTC"}
LEGAL_SUFFIX_RE = re.compile(
    r"\s*[-–—]\s*(?:ctcp|tmcp|công\s+ty\s+cổ\s+phần|"
    r"công\s+ty\s+tnhh|joint\s+stock\s+company)\s*$",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", value).strip()


def weak_family_from_id(question_id: int | None) -> QuestionFamily | None:
    if question_id is None:
        return None
    if 1 <= question_id <= 361:
        return "direct_lookup"
    if 362 <= question_id <= 577:
        return "conditional_analytical"
    if 578 <= question_id <= 655:
        return "temporal_change"
    if 656 <= question_id <= 732:
        return "ratio_or_derived"
    if 733 <= question_id <= 812:
        return "cross_entity_comparison"
    if 813 <= question_id <= 1012:
        return "multi_entity_or_period_aggregation"
    return None


def load_ticker_aliases(code_stock_path: Path) -> dict[str, str]:
    """Load normalized company aliases and ticker strings.

    The public CSV schema has changed across releases, so all non-empty text
    columns are treated as candidate aliases while a short uppercase field is
    treated as the ticker.
    """
    aliases: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    if not code_stock_path.is_file():
        return aliases

    with code_stock_path.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            values = [str(value).strip() for value in row.values() if value]
            ticker = next(
                (
                    value.upper()
                    for value in values
                    if re.fullmatch(r"[A-Z]{2,6}", value.upper())
                ),
                None,
            )
            if ticker is None:
                continue
            def add_alias(alias: str) -> None:
                normalized = normalize_text(alias).casefold()
                if len(normalized) < 2 or normalized in ambiguous_aliases:
                    return
                previous = aliases.get(normalized)
                if previous is not None and previous != ticker:
                    aliases.pop(normalized, None)
                    ambiguous_aliases.add(normalized)
                    return
                aliases[normalized] = ticker

            add_alias(ticker)
            for value in values:
                if len(value) >= 2:
                    add_alias(value)
                    # Report questions often omit only a terminal legal form
                    # (for example, ``... Việt Nam`` vs ``... Việt Nam -
                    # CTCP``).  Keep this high-precision variant, but do not
                    # remove meaningful interior words or shorten a name.
                    stripped = LEGAL_SUFFIX_RE.sub("", normalize_text(value)).strip()
                    if stripped != normalize_text(value) and len(stripped.split()) >= 3:
                        add_alias(stripped)
    return aliases


def extract_tickers(question: str, aliases: dict[str, str]) -> list[str]:
    found: list[tuple[int, str]] = []
    lowered = normalize_text(question).casefold()
    known_tickers = set(aliases.values())

    # Corporate abbreviations such as CTCP/TMCP must not become fake tickers.
    for match in re.finditer(r"\(([A-Z]{2,6})\)|\b([A-Z]{2,6})\b", question):
        ticker = next(group for group in match.groups() if group).upper()
        if known_tickers and ticker not in known_tickers:
            continue
        found.append((match.start(), ticker))

    # Company aliases provide recall when no ticker is explicitly written.
    for alias, ticker in aliases.items():
        if alias == ticker.casefold():
            match = re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered)
            position = match.start() if match else -1
        else:
            position = lowered.find(alias)
        if position >= 0:
            found.append((position, ticker))

    deduplicated: list[str] = []
    for _, ticker in sorted(found):
        if ticker not in deduplicated:
            deduplicated.append(ticker)
    return deduplicated


def infer_scope(question: str) -> str | None:
    matches = [scope for scope, pattern in SCOPE_PATTERNS.items() if pattern.search(question)]
    return matches[0] if len(matches) == 1 else None


def infer_unit(question: str) -> str | None:
    for unit, pattern in UNIT_PATTERNS:
        if pattern.search(question):
            return unit
    return "percent" if PERCENT_RE.search(question) else None


def infer_family(question: str, question_id: int | None = None) -> tuple[QuestionFamily, float]:
    normalized = normalize_text(question)
    for family, pattern, confidence in FAMILY_RULES:
        if pattern.search(normalized):
            return family, confidence

    weak = weak_family_from_id(question_id)
    if weak is not None:
        return weak, 0.7
    return "direct_lookup", 0.62


def reported_value_lookup_reason(question: str) -> str | None:
    """Return a high-precision reason when wording asks for one reported row.

    This is classification only.  It does not establish that retrieval found a
    correct table, row, period or value; all later raw-row/cell gates remain
    mandatory.
    """
    normalized = normalize_text(question)
    if COMPOSED_LOOKUP_BLOCK_RE.search(normalized) or MULTI_SUBJECT_RE.search(normalized):
        return None
    years = YEAR_RE.findall(normalized)
    if len(set(years)) > 1:
        return None
    ticker_tokens = {
        next(value for value in match if value)
        for match in TICKER_TOKEN_RE.findall(normalized)
        if next(value for value in match if value) not in NON_TICKER_TOKENS
    }
    if len(ticker_tokens) > 1:
        return None
    if REPORTED_RATIO_RE.search(normalized):
        return "disclosed_ownership_or_voting_ratio"
    if REPORTED_TOTAL_RE.search(normalized):
        return "disclosed_total_or_weighted_average_row"
    if REPORTED_FX_ROW_RE.search(normalized):
        return "disclosed_foreign_exchange_row"
    return None


def infer_operation_ast(family: QuestionFamily, question: str) -> dict:
    q = question.casefold()
    if family == "direct_lookup":
        return {"op": "lookup", "args": ["x0"]}
    if family == "temporal_change":
        if "tỷ lệ" in q or "phần trăm" in q or "tăng trưởng" in q:
            return {"op": "percentage_change", "args": ["x_new", "x_old"]}
        if "chênh lệch" in q or "tăng bao nhiêu" in q or "giảm bao nhiêu" in q:
            return {"op": "subtract", "args": ["x_new", "x_old"]}
    if family == "ratio_or_derived":
        return {"op": "divide", "args": ["numerator", "denominator"]}
    if family == "cross_entity_comparison":
        return {"op": "subtract", "args": ["entity_a", "entity_b"]}
    if family == "multi_entity_or_period_aggregation":
        if "trung bình" in q or "bình quân" in q:
            return {"op": "mean", "args": ["values"]}
        if "cao nhất" in q or "lớn nhất" in q:
            return {"op": "max", "args": ["values"]}
        if "thấp nhất" in q or "nhỏ nhất" in q:
            return {"op": "min", "args": ["values"]}
        if "bao nhiêu" in q and ("năm" in q or "công ty" in q):
            return {"op": "count", "args": ["filtered_values"]}
        return {"op": "sum", "args": ["values"]}
    return {"op": "plan_required", "args": []}


def metric_hint(question: str) -> str:
    """Produce a retrieval-oriented metric hint, not a gold semantic label."""
    value = normalize_text(question)
    value = re.sub(r"\([^)]*\)", " ", value)
    value = YEAR_RE.sub(" ", value)
    value = re.sub(
        r"\b(?:là|bao nhiêu|trong năm|vào cuối năm|đến ngày|tại ngày|"
        r"của công ty mẹ|của công ty|của|triệu đồng|tỷ đồng|nghìn đồng|"
        r"nghìn tỷ đồng|phần trăm)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip(" ?.,")


class RuleQuestionPlanner:
    """Immediate baseline planner.

    It extracts high-confidence metadata and routes question families. Complex
    operand decomposition remains explicit as a warning rather than silently
    inventing unsupported operands.
    """

    def __init__(self, code_stock_path: Path | None = None) -> None:
        self.aliases = load_ticker_aliases(code_stock_path) if code_stock_path else {}

    def plan(self, question: str, question_id: int | None = None) -> QuestionPlan:
        normalized = normalize_text(question)
        reported_lookup_reason = reported_value_lookup_reason(normalized)
        family, confidence = (
            ("direct_lookup", 0.98)
            if reported_lookup_reason
            else infer_family(normalized, question_id)
        )
        tickers = extract_tickers(normalized, self.aliases)
        years = sorted({int(year) for year in YEAR_RE.findall(normalized)})
        scope = infer_scope(normalized)
        unit = infer_unit(normalized)
        hint = metric_hint(normalized)

        operands: list[OperandSpec] = []
        warnings: list[str] = []

        if family == "direct_lookup":
            operands.append(
                OperandSpec(
                    operand_id="x0",
                    metric=hint,
                    ticker=tickers[0] if len(tickers) == 1 else None,
                    period=years[0] if len(years) == 1 else None,
                    scope=scope,
                )
            )
        elif family == "temporal_change" and len(years) >= 2:
            operands.extend(
                [
                    OperandSpec("x_old", hint, period=years[0], scope=scope),
                    OperandSpec("x_new", hint, period=years[-1], scope=scope),
                ]
            )
        else:
            warnings.append(
                "Complex operand decomposition requires the semantic planner or manual gold plan."
            )

        if not tickers:
            warnings.append("No ticker/company alias was resolved.")
        if family != "direct_lookup" and not operands:
            warnings.append("Retrieval should run per operand after semantic decomposition.")
        if reported_lookup_reason:
            warnings.append(
                "Classified as direct_lookup because the question requests one disclosed report row: "
                + reported_lookup_reason
            )

        return QuestionPlan(
            question_id=question_id,
            original_question=question,
            family=family,
            family_confidence=confidence,
            tickers=tickers,
            years=years,
            scope=scope,
            requested_unit=unit,
            operands=operands,
            operation_ast=infer_operation_ast(family, normalized),
            warnings=warnings,
        )
