"""Controlled formula context for financial-ratio review.

This module does not calculate an answer and does not claim gold operands.  It
turns recognised question wording into an auditable review template: formula,
required operand slots, period requirements, and explicit ambiguity notes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
TOKEN_RE = re.compile(r"[a-z0-9%]+")
STOPWORDS = {
    "cua", "va", "la", "bao", "nhieu", "nam", "tai", "vao", "cuoi",
    "dau", "cong", "ty", "ctcp", "me", "tap", "doan", "ngan", "hang",
    "thuong", "mai", "co", "phan", "den", "tu", "trong", "tren",
}


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value).casefold())
    return " ".join(
        "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Mn"
        )
        .replace("đ", "d")
        .split()
    )


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in TOKEN_RE.findall(fold_text(value))
        if len(token) > 1 and token not in STOPWORDS
    }


def _operand(
    operand_id: str,
    label: str,
    metric_hints: list[str],
    years: list[int],
    role: str,
    *,
    entity: str | None = None,
    stage_id: str | None = None,
    allowed_table_functions: list[str] | None = None,
    table_function_column_hints: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    payload = {
        "operand_id": operand_id,
        "label": label,
        "metric_hints": metric_hints,
        "years": years,
        "role": role,
        "required": True,
    }
    if entity:
        payload["entity"] = entity
    if stage_id:
        payload["stage_id"] = stage_id
    if allowed_table_functions:
        payload["allowed_table_functions"] = list(allowed_table_functions)
    if table_function_column_hints:
        payload["table_function_column_hints"] = {
            str(kind): list(hints)
            for kind, hints in table_function_column_hints.items()
        }
    return payload


def _spec(
    formula_id: str,
    label: str,
    expression: str,
    operands: list[dict[str, Any]],
    *,
    confidence: float,
    definition_status: str = "defined",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "formula_id": formula_id,
        "label": label,
        "expression": expression,
        "output_unit": "percent" if "100" in expression else "source_unit",
        "confidence": confidence,
        "definition_status": definition_status,
        "operands": operands,
        "notes": list(notes or []),
        "provenance": "controlled_formula_rule",
    }


def _question_years(question: str) -> list[int]:
    return list(dict.fromkeys(int(year) for year in YEAR_RE.findall(question)))


def _growth_metric(question: str) -> str:
    """Extract the metric without treating an in-metric ``từ`` as a date cue.

    Financial labels commonly contain phrases such as ``chi phí mua khí từ các
    chủ mỏ`` or ``doanh thu từ hoạt động xây dựng``.  The old generic split at
    the first ``từ`` silently widened those metrics.  A growth boundary must
    instead carry an explicit year/date phrase.
    """
    value = re.sub(r"\s+", " ", question).strip(" ?.\n")
    # Vietnamese questions may lead with ``Tính phần trăm tăng trưởng ...``
    # or simply ``Tăng trưởng ...``.  Previously only a few nominal prefixes
    # were recognised, silently yielding the generic metric placeholder for
    # these direct temporal questions. Keep the phrase before the explicit
    # time boundary verbatim; it is later matched against a raw V2 row.
    match = re.search(
        r"(?:tính\s+)?(?:phần\s+trăm\s+)?(?:tỷ\s+lệ|tỉ\s+lệ|"
        r"tốc\s+độ|tỷ\s+suất)?\s*(?:tốc\s+độ\s+)?(?:tăng\s+trưởng|tăng)"
        r"(?:\s*\(\s*%\s*\)|\s*%)?\s+(.+)$",
        value,
        re.IGNORECASE,
    )
    if not match:
        return "Chỉ tiêu cần so sánh"
    metric = match.group(1).strip()
    time_boundary = re.search(
        r"\s+(?:từ\s+(?:(?:đầu|cuối)\s+)?năm\s+|"
        r"từ\s+(?:19|20)\d{2}\b|"
        r"giữa\s+(?:(?:đầu|cuối)\s+)?năm\s+(?:19|20)\d{2}\s+"
        r"(?:và|đến)\s+(?:(?:đầu|cuối)\s+)?năm\s+(?:19|20)\d{2}\b|"
        r"trong\s+năm\s+(?:19|20)\d{2}\s+so\s+với\s+năm\s+(?:19|20)\d{2}\b|"
        r"năm\s+(?:19|20)\d{2}\s+so\s+với\s+năm\s+(?:19|20)\d{2}\b)",
        metric,
        re.IGNORECASE,
    )
    if time_boundary:
        metric = metric[: time_boundary.start()].strip()
    metric = re.sub(r"^(?:\(\s*%\s*\)\s*)?(?:của\s+)?", "", metric, flags=re.IGNORECASE)
    metric = re.sub(r"\s+(?:tính|so)$", "", metric, flags=re.IGNORECASE).strip()
    entity = re.search(
        r"\s+của\s+(?:công ty mẹ|ctcp|công ty cổ phần|ngân hàng|tập đoàn|tổng công ty)\b",
        metric,
        re.IGNORECASE,
    )
    if entity:
        metric = metric[: entity.start()].strip()
    metric = re.sub(r"\s+của\s+[A-Z]{2,6}(?:\s+.*)?$", "", metric).strip()
    return metric or "Chỉ tiêu cần so sánh"


def _ratio(
    formula_id: str,
    label: str,
    numerator: tuple[str, list[str]],
    denominator: tuple[str, list[str]],
    years: list[int],
    *,
    confidence: float = 0.94,
    definition_status: str = "defined",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    period = years[-1:] if years else []
    return _spec(
        formula_id,
        label,
        "numerator / denominator × 100%",
        [
            _operand("numerator", numerator[0], numerator[1], period, "numerator"),
            _operand("denominator", denominator[0], denominator[1], period, "denominator"),
        ],
        confidence=confidence,
        definition_status=definition_status,
        notes=notes,
    )


def _quick_gpm_interest_coverage_plan(
    question_text: str,
    years: list[int],
) -> dict[str, Any] | None:
    required_phrases = (
        "he so thanh toan nhanh",
        "trung vi",
        "bien loi nhuan gop",
        "kha nang thanh toan lai vay",
    )
    if not all(phrase in question_text for phrase in required_phrases):
        return None
    question_tokens = set(TOKEN_RE.findall(question_text))
    entities = [
        ticker
        for ticker in ("HPG", "HSG", "MSR", "NKG")
        if ticker.casefold() in question_tokens
    ]
    if len(entities) != 4:
        return None

    old_year, new_year = (years[0], years[-1]) if len(years) >= 2 else (2022, 2023)
    operands: list[dict[str, Any]] = []
    for entity in entities:
        prefix = entity.casefold()
        operands.extend(
            [
                _operand(
                    f"{prefix}_current_assets_{old_year}",
                    f"{entity} — Tài sản ngắn hạn {old_year}",
                    ["tài sản ngắn hạn"],
                    [old_year],
                    "quick_ratio_numerator_base",
                    entity=entity,
                    stage_id="quick_ratio_filter",
                    allowed_table_functions=["balance_sheet"],
                ),
                _operand(
                    f"{prefix}_inventory_{old_year}",
                    f"{entity} — Hàng tồn kho {old_year}",
                    ["hàng tồn kho"],
                    [old_year],
                    "quick_ratio_subtract",
                    entity=entity,
                    stage_id="quick_ratio_filter",
                    allowed_table_functions=["balance_sheet"],
                ),
                _operand(
                    f"{prefix}_current_liabilities_{old_year}",
                    f"{entity} — Nợ ngắn hạn {old_year}",
                    ["nợ ngắn hạn"],
                    [old_year],
                    "quick_ratio_denominator",
                    entity=entity,
                    stage_id="quick_ratio_filter",
                    allowed_table_functions=["balance_sheet"],
                ),
            ]
        )
        for year in (old_year, new_year):
            operands.extend(
                [
                    _operand(
                        f"{prefix}_gross_profit_{year}",
                        f"{entity} — Lợi nhuận gộp {year}",
                        ["lợi nhuận gộp"],
                        [year],
                        "gross_margin_numerator",
                        entity=entity,
                        stage_id="gross_margin_rank",
                        allowed_table_functions=[
                            "income_statement",
                            "segment_reporting",
                        ],
                        table_function_column_hints={
                            "segment_reporting": ["tổng cộng"],
                        },
                    ),
                    _operand(
                        f"{prefix}_net_revenue_{year}",
                        f"{entity} — Doanh thu thuần {year}",
                        ["doanh thu thuần"],
                        [year],
                        "gross_margin_denominator",
                        entity=entity,
                        stage_id="gross_margin_rank",
                        allowed_table_functions=["income_statement"],
                    ),
                ]
            )
        operands.extend(
            [
                _operand(
                    f"{prefix}_profit_before_tax_{new_year}",
                    f"{entity} — Lợi nhuận kế toán trước thuế {new_year}",
                    ["lợi nhuận kế toán trước thuế", "lợi nhuận trước thuế"],
                    [new_year],
                    "interest_coverage_pbt_component",
                    entity=entity,
                    stage_id="interest_coverage_lookup",
                    allowed_table_functions=[
                        "income_statement",
                        "cash_flow_statement",
                    ],
                ),
                _operand(
                    f"{prefix}_interest_expense_{new_year}",
                    f"{entity} — Chi phí lãi vay {new_year}",
                    ["chi phí lãi vay"],
                    [new_year],
                    "interest_coverage_denominator",
                    entity=entity,
                    stage_id="interest_coverage_lookup",
                    allowed_table_functions=[
                        "income_statement",
                        "cash_flow_statement",
                        "financial_note_detail",
                    ],
                ),
            ]
        )

    spec = _spec(
        "quick_ratio_gpm_interest_coverage_selection",
        "Lọc Quick Ratio → xếp hạng ΔGPM → lấy Interest Coverage",
        "filter(q_y < median(q)) → argmax(GPM_new − GPM_old) → ICR_new",
        operands,
        confidence=0.99,
        definition_status="review_required",
        notes=[
            "Quick Ratio = (Tài sản ngắn hạn − Hàng tồn kho) / Nợ ngắn hạn.",
            "Với bốn giá trị, median là trung bình của hai giá trị giữa sau khi sắp xếp.",
            "Điều kiện lọc là nghiêm ngặt: Quick Ratio < median; không lấy trường hợp bằng median.",
            "GPM = Lợi nhuận gộp / Doanh thu thuần × 100%.",
            "Xếp hạng theo ΔGPM = GPM_new − GPM_old lớn nhất, không dùng trị tuyệt đối.",
            "Interest Coverage = EBIT / |Chi phí lãi vay|.",
            "Khi source không có dòng EBIT trực tiếp, dùng EBIT = Lợi nhuận kế toán trước thuế + |Chi phí lãi vay|; phép dẫn xuất phải giữ tham chiếu tới cả hai exact rows.",
        ],
    )
    spec.update(
        {
            "output_unit": "times",
            "entities": entities,
            "execution_status": "stage_binding_required",
            "stages": [
                {
                    "stage_id": "quick_ratio_filter",
                    "label": f"1. Lọc theo Quick Ratio {old_year}",
                    "expression": "q = (Tài sản ngắn hạn − Hàng tồn kho) / Nợ ngắn hạn",
                    "decision": "Giữ entity khi q < median(q_HPG, q_HSG, q_MSR, q_NKG)",
                },
                {
                    "stage_id": "gross_margin_rank",
                    "label": f"2. Xếp hạng thay đổi GPM {old_year}→{new_year}",
                    "expression": "GPM_y = Lợi nhuận gộp_y / Doanh thu thuần_y × 100%",
                    "decision": "Chọn ΔGPM = GPM_new − GPM_old lớn nhất; không dùng abs",
                },
                {
                    "stage_id": "interest_coverage_lookup",
                    "label": f"3. Truy xuất Interest Coverage {new_year} của entity thắng",
                    "expression": "ICR = (Lợi nhuận trước thuế + |Chi phí lãi vay|) / |Chi phí lãi vay|",
                    "decision": "Chỉ trả chỉ tiêu của entity được chọn ở stage trước",
                },
            ],
        }
    )
    return spec


def infer_formula_spec(question: str) -> dict[str, Any] | None:
    """Infer a review formula only when a controlled rule matches."""
    text = fold_text(question)
    years = _question_years(question)

    staged_plan = _quick_gpm_interest_coverage_plan(text, years)
    if staged_plan:
        return staged_plan

    # Do not collapse a screening/ranking program into whichever ratio keyword
    # appears first.  These questions need entity-wise EvidenceSets and a later
    # selection step; the current flat operand binder must fail closed.
    population_cues = (
        "trong nhom",
        "trong cac doanh nghiep",
        "trong cac cong ty",
        "doanh nghiep co muc",
        "cong ty co muc",
    )
    selection_cues = (
        "trung vi",
        "cao nhat",
        "thap nhat",
        "manh nhat",
        "lien tuc",
        "xet cac cong ty",
    )
    if any(cue in text for cue in population_cues) and any(
        cue in text for cue in selection_cues
    ):
        return _spec(
            "multi_stage_selection_unresolved",
            "Câu hỏi lọc/xếp hạng nhiều giai đoạn",
            "screen entities → compute/rank intermediate metrics → select entity → compute target metric",
            [
                _operand(
                    "screening_stage",
                    "Evidence theo từng entity cho điều kiện lọc/xếp hạng",
                    [],
                    years,
                    "screen_and_rank",
                ),
                _operand(
                    "target_stage",
                    "Evidence cho chỉ tiêu cuối của entity được chọn",
                    [],
                    years[-1:] if years else [],
                    "target",
                ),
            ],
            confidence=0.99,
            definition_status="review_required",
            notes=[
                "Không được rút câu hỏi này thành một công thức đơn lẻ theo keyword đầu tiên.",
                "Cần planner theo stage và EvidenceSet riêng cho từng entity trước khi tạo complete label.",
            ],
        )

    if "roa" in text:
        if len(years) >= 2 and any(term in text for term in ("thay doi", "tu nam", "tang", "giam")):
            old, new = years[0], years[-1]
            operands = []
            for suffix, year in (("old", old), ("new", new)):
                operands.extend(
                    [
                        _operand(
                            f"net_profit_{suffix}",
                            f"Lợi nhuận sau thuế — {year}",
                            ["lợi nhuận sau thuế", "lợi nhuận ròng"],
                            [year],
                            "numerator",
                        ),
                        _operand(
                            f"average_assets_{suffix}",
                            f"Tổng tài sản bình quân — {year}",
                            ["tổng tài sản", "tài sản bình quân"],
                            [year],
                            "denominator",
                        ),
                    ]
                )
            return _spec(
                "roa_change",
                "Thay đổi ROA",
                "ROA_new − ROA_old; ROA_y = LNST_y / Tổng tài sản bình quân_y × 100%",
                operands,
                confidence=0.92,
                notes=[
                    "Tổng tài sản bình quân cần số đầu kỳ và cuối kỳ.",
                    "Nếu câu hỏi có điều kiện xếp hạng/chọn doanh nghiệp, phải hoàn tất bước chọn entity trước.",
                ],
            )
        return _ratio(
            "roa",
            "Tỷ suất sinh lời trên tài sản (ROA)",
            ("Lợi nhuận sau thuế", ["lợi nhuận sau thuế", "lợi nhuận ròng"]),
            ("Tổng tài sản bình quân", ["tổng tài sản", "tài sản bình quân"]),
            years,
            notes=["Tổng tài sản bình quân thường cần số đầu kỳ và cuối kỳ."],
        )

    if "roe" in text:
        return _ratio(
            "roe",
            "Tỷ suất sinh lời trên vốn chủ sở hữu (ROE)",
            ("Lợi nhuận sau thuế", ["lợi nhuận sau thuế", "lợi nhuận ròng"]),
            ("Vốn chủ sở hữu bình quân", ["vốn chủ sở hữu", "vốn chủ sở hữu bình quân"]),
            years,
            notes=["Vốn chủ sở hữu bình quân thường cần số đầu kỳ và cuối kỳ."],
        )

    if "ldr" in text or "cho vay khach hang tren tong tien gui khach hang" in text:
        return _ratio(
            "loan_to_deposit",
            "Hệ số cho vay trên tiền gửi khách hàng (LDR)",
            ("Cho vay khách hàng", ["cho vay khách hàng"]),
            ("Tiền gửi khách hàng", ["tiền gửi của khách hàng", "tiền gửi khách hàng"]),
            years,
        )

    if "he so thanh toan hien hanh" in text or "kha nang thanh toan hien hanh" in text:
        return _ratio(
            "current_ratio",
            "Hệ số thanh toán hiện hành",
            ("Tài sản ngắn hạn", ["tài sản ngắn hạn"]),
            ("Nợ ngắn hạn", ["nợ ngắn hạn"]),
            years,
        )

    if "he so thanh toan nhanh" in text or "kha nang thanh toan nhanh" in text:
        return _spec(
            "quick_ratio",
            "Hệ số thanh toán nhanh",
            "(Tài sản ngắn hạn − Hàng tồn kho) / Nợ ngắn hạn",
            [
                _operand("current_assets", "Tài sản ngắn hạn", ["tài sản ngắn hạn"], years[-1:], "numerator"),
                _operand("inventory", "Hàng tồn kho", ["hàng tồn kho"], years[-1:], "subtract"),
                _operand("current_liabilities", "Nợ ngắn hạn", ["nợ ngắn hạn"], years[-1:], "denominator"),
            ],
            confidence=0.78,
            definition_status="review_required",
            notes=["Một số tài liệu dùng biến thể loại thêm tài sản ngắn hạn kém thanh khoản; human phải xác nhận định nghĩa."],
        )

    if "ket qua thuan tu hoat dong dich vu" in text:
        period = years[-1:] if years else []
        return _spec(
            "net_service_result",
            "Kết quả thuần từ hoạt động dịch vụ",
            "Thu nhập từ hoạt động dịch vụ − Chi phí hoạt động dịch vụ",
            [
                _operand("service_income", "Thu nhập hoạt động dịch vụ", ["thu nhập từ hoạt động dịch vụ", "doanh thu dịch vụ"], period, "minuend"),
                _operand("service_expense", "Chi phí hoạt động dịch vụ", ["chi phí hoạt động dịch vụ"], period, "subtrahend"),
            ],
            confidence=0.94,
        )

    if "loi nhuan thuan tu hoat dong tai chinh" in text:
        period = years[-1:] if years else []
        return _spec(
            "net_finance_result",
            "Lợi nhuận thuần từ hoạt động tài chính",
            "Doanh thu hoạt động tài chính − Chi phí tài chính",
            [
                _operand("finance_income", "Doanh thu hoạt động tài chính", ["doanh thu hoạt động tài chính"], period, "minuend"),
                _operand("finance_expense", "Chi phí tài chính", ["chi phí tài chính"], period, "subtrahend"),
            ],
            confidence=0.94,
        )

    ratio_rules = [
        (
            ("nguyen gia tai san co dinh huu hinh", "tong tai san"),
            ("ppe_cost_to_assets", "Tỷ trọng nguyên giá TSCĐ hữu hình trên tổng tài sản", ("Nguyên giá TSCĐ hữu hình", ["nguyên giá tài sản cố định hữu hình", "nguyên giá tscđ hữu hình"]), ("Tổng tài sản", ["tổng tài sản"])),
        ),
        (
            ("tai san co dinh huu hinh", "tong tai san"),
            ("ppe_to_assets", "Tỷ trọng TSCĐ hữu hình trên tổng tài sản", ("TSCĐ hữu hình", ["tài sản cố định hữu hình", "tscđ hữu hình"]), ("Tổng tài sản", ["tổng tài sản"])),
        ),
        (
            ("dau tu tai chinh dai han", "von chu so huu"),
            ("long_term_investment_to_equity", "Đầu tư tài chính dài hạn trên vốn chủ sở hữu", ("Đầu tư tài chính dài hạn", ["đầu tư tài chính dài hạn"]), ("Vốn chủ sở hữu", ["vốn chủ sở hữu"])),
        ),
        (
            ("no ngan han", "von chu so huu"),
            ("current_liabilities_to_equity", "Nợ ngắn hạn trên vốn chủ sở hữu", ("Nợ ngắn hạn", ["nợ ngắn hạn"]), ("Vốn chủ sở hữu", ["vốn chủ sở hữu"])),
        ),
        (
            ("doanh thu ure phu my", "tong doanh thu thuan"),
            ("product_revenue_share", "Tỷ trọng doanh thu Ure Phú Mỹ", ("Doanh thu Ure Phú Mỹ", ["doanh thu ure phú mỹ", "ure phú mỹ"]), ("Tổng doanh thu thuần hàng hóa sản xuất trong nước", ["tổng doanh thu thuần hàng hóa sản xuất trong nước", "doanh thu thuần hàng hóa sản xuất trong nước"])),
        ),
    ]
    for required, definition in ratio_rules:
        if all(term in text for term in required):
            return _ratio(definition[0], definition[1], definition[2], definition[3], years)

    if "ty trong" in text and "phai tra nguoi ban" in text and "no ngan han" in text:
        return _ratio(
            "trade_payables_share_current_liabilities",
            "Tỷ trọng phải trả người bán trong nợ ngắn hạn",
            ("Phải trả người bán ngắn hạn", ["phải trả người bán ngắn hạn", "phải trả người bán"]),
            ("Tổng nợ ngắn hạn", ["nợ ngắn hạn", "tổng nợ ngắn hạn"]),
            years,
            confidence=0.78,
            definition_status="review_required",
            notes=["Câu hỏi không nêu rõ mẫu số; human cần xác nhận mẫu số là tổng nợ ngắn hạn."],
        )

    if "ty suat sinh loi tu co tuc" in text:
        return _ratio(
            "dividend_investment_yield",
            "Tỷ suất sinh lời từ cổ tức đầu tư",
            ("Cổ tức/lợi nhuận được chia", ["cổ tức", "lợi nhuận được chia"]),
            ("Cơ sở giá trị khoản đầu tư", ["giá trị khoản đầu tư", "đầu tư tài chính"]),
            years,
            confidence=0.55,
            definition_status="ambiguous",
            notes=["Câu hỏi chưa xác định mẫu số là giá gốc, giá trị ghi sổ hay giá trị đầu kỳ của khoản đầu tư."],
        )

    growth_terms = ("tang truong", "toc do tang", "ty le tang", "phan tram toc do tang")
    if any(term in text for term in growth_terms) and len(years) >= 2:
        metric = _growth_metric(question)
        # ``2022 so với 2020`` names the target before its base.  A growth
        # rate is chronological unless a future controlled rule explicitly
        # models a reverse-period comparison.
        old, new = min(years), max(years)
        return _spec(
            "percentage_change",
            "Tỷ lệ tăng trưởng",
            "(x_new − x_old) / |x_old| × 100%",
            [
                _operand("x_old", f"{metric} — {old}", [metric], [old], "denominator"),
                _operand("x_new", f"{metric} — {new}", [metric], [new], "numerator"),
            ],
            confidence=0.96,
            notes=["Hai operand phải cùng entity, scope, đơn vị và cùng quy ước thời điểm."],
        )

    return None


def operand_match_score(
    operand: dict[str, Any],
    evidence_text: str,
    *,
    report_year: int | None,
    period_labels: list[str] | None = None,
    ticker: str | None = None,
) -> float:
    """Score an operand against grounded candidate text and explicit periods."""
    required_entity = str(operand.get("entity") or "").casefold()
    if required_entity and required_entity != str(ticker or "").casefold():
        return 0.0
    required_years = {int(year) for year in operand.get("years") or []}
    available_years = {int(year) for year in YEAR_RE.findall(" ".join(period_labels or []))}
    if report_year is not None:
        available_years.add(int(report_year))
    if required_years and not required_years.intersection(available_years):
        return 0.0

    folded_evidence = fold_text(evidence_text)
    best = 0.0
    for hint in operand.get("metric_hints") or []:
        folded_hint = fold_text(hint)
        if folded_hint and folded_hint in folded_evidence:
            best = max(best, 1.0)
            continue
        hint_tokens = _tokens(hint)
        if not hint_tokens:
            continue
        coverage = len(hint_tokens & _tokens(evidence_text)) / len(hint_tokens)
        best = max(best, coverage)
    return round(best, 4)


def formula_is_multi_operand(spec: dict[str, Any] | None) -> bool:
    return bool(spec and len([op for op in spec.get("operands") or [] if op.get("required")]) > 1)
