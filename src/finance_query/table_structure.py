"""Lossless-enough HTML table reconstruction for the ViFinQA corpus.

The extracted reports are HTML-like OCR output.  A blank ``<td>`` is still a
real column position, so it must never be removed.  This module expands HTML
spans into a rectangular grid and records where every rendered grid cell came
from.  It intentionally does *not* correct OCR text or numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from bs4 import BeautifulSoup


HEADER_HINTS = (
    "chỉ tiêu",
    "mã số",
    "thuyết minh",
    "đơn vị",
    "đvt",
    "năm",
    "vnd",
    "số cuối",
    "số đầu",
    "giá trị",
    "tỷ lệ",
    "31/12",
    "1/1",
)

PAGE_MARKER_RE = re.compile(r"=====\s*PAGE\s*\d+\s*=====", re.IGNORECASE)
PERIOD_RE = re.compile(
    r"(?:31\s*[/.-]\s*12\s*[/.-]\s*(?:19|20)\d{2}|"
    r"0?1\s*[/.-]\s*0?1\s*[/.-]\s*(?:19|20)\d{2}|"
    r"(?:19|20)\d{2}|số cuối năm|số đầu năm|năm nay|năm trước|cuối kỳ|đầu kỳ)",
    re.IGNORECASE,
)
UNIT_LABEL_RE = re.compile(
    r"(?:nghìn|ngàn|triệu|tỷ)\s*(?:vnd|đồng)|vnd|%|phần trăm",
    re.IGNORECASE,
)
NUMBERED_TOPIC_RE = re.compile(
    r"(?:^|\s)(\d{1,2}(?:\.\d+){0,2})[.)]?\s+"
    r"([A-ZÀ-Ỹ][^\n]{2,180}?)(?=(?:\s+\d{1,2}(?:\.\d+){0,2}[.)]?\s+[A-ZÀ-Ỹ])|$)"
)

FUNCTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "cash_flow_statement",
        "Báo cáo lưu chuyển tiền tệ",
        ("báo cáo lưu chuyển tiền tệ", "lưu chuyển tiền tệ"),
    ),
    (
        "income_statement",
        "Báo cáo kết quả hoạt động kinh doanh",
        ("báo cáo kết quả hoạt động kinh doanh", "kết quả hoạt động kinh doanh"),
    ),
    (
        "balance_sheet",
        "Bảng cân đối kế toán",
        ("bảng cân đối kế toán", "bảng cân đối", "báo cáo tình hình tài chính"),
    ),
    (
        "equity_change_statement",
        "Báo cáo biến động vốn chủ sở hữu",
        ("biến động vốn chủ sở hữu", "thay đổi vốn chủ sở hữu"),
    ),
    (
        "segment_reporting",
        "Bảng thông tin bộ phận",
        ("thông tin về bộ phận", "báo cáo bộ phận", "theo lĩnh vực kinh doanh"),
    ),
    (
        "related_party_schedule",
        "Bảng giao dịch/số dư bên liên quan",
        ("các bên liên quan", "giao dịch với các bên liên quan", "bên liên quan khác"),
    ),
    (
        "governance_roster",
        "Danh sách quản trị/điều hành",
        ("hội đồng quản trị", "ban tổng giám đốc", "ban kiểm soát", "ủy ban kiểm toán"),
    ),
    (
        "project_schedule",
        "Bảng dự án/hợp tác đầu tư",
        ("thông tin về dự án", "tên dự án", "hợp đồng hợp tác kinh doanh"),
    ),
    (
        "debt_schedule",
        "Bảng chi tiết khoản vay/nợ",
        ("chi tiết khoản vay", "vay các tctd", "trái phiếu", "tổ chức cho vay"),
    ),
    (
        "investment_schedule",
        "Bảng chi tiết khoản đầu tư",
        ("chi tiết các khoản đầu tư", "đầu tư vào các công ty", "vốn góp liên doanh"),
    ),
    (
        "financial_note",
        "Thuyết minh báo cáo tài chính",
        ("thuyết minh báo cáo tài chính",),
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_structure_sidecar(
    bundle_dir: Path,
    sidecar_path: Path,
) -> dict[str, Any]:
    """Verify the V2 sidecar still matches its immutable bundle and manifest."""
    bundle_dir = bundle_dir.resolve()
    sidecar_path = sidecar_path.resolve()
    tables_path = bundle_dir / "tables.jsonl"
    manifest_path = sidecar_path.with_name("table_structure_v2.manifest.json")
    if not sidecar_path.is_file():
        raise FileNotFoundError(sidecar_path)
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    if not tables_path.is_file():
        raise FileNotFoundError(tables_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("structure_version") or 0) != 2:
        raise ValueError("Unsupported table-structure sidecar version")
    if int(manifest.get("error_count") or 0) != 0:
        raise ValueError("Table-structure sidecar manifest contains repair errors")
    if str(manifest.get("input_bundle_tables_sha256") or "") != sha256_file(
        tables_path
    ):
        raise ValueError("Table-structure sidecar belongs to a different bundle")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar_path):
        raise ValueError("Table-structure sidecar checksum mismatch")
    repaired_count = int(manifest.get("repaired_table_count") or 0)
    table_count = int(manifest.get("table_count") or 0)
    if repaired_count != table_count:
        raise ValueError("Partial table-structure sidecar is not review-safe")
    with sidecar_path.open(encoding="utf-8-sig") as file:
        observed_count = sum(1 for line in file if line.strip())
    if observed_count != repaired_count:
        raise ValueError("Table-structure sidecar row count differs from manifest")
    return manifest

SECTION_HINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("liability", "Nợ phải trả", ("nợ phải trả", "nợ ngắn hạn", "phải trả")),
    ("asset", "Tài sản", ("tài sản",)),
    ("equity", "Vốn chủ sở hữu", ("vốn chủ sở hữu",)),
    (
        "cash_flow",
        "Lưu chuyển tiền tệ",
        ("lưu chuyển tiền", "tiền và tương đương tiền"),
    ),
    ("revenue", "Doanh thu", ("doanh thu",)),
    ("expense", "Chi phí", ("chi phí",)),
)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _positive_span(value: object) -> int:
    try:
        return max(1, int(str(value or "1")))
    except ValueError:
        return 1


@dataclass(slots=True)
class _ActiveSpan:
    remaining_rows: int
    source_row: int
    source_cell: int
    anchor_row: int
    anchor_column: int


def _fill_active_spans(
    row: dict[int, str],
    provenance: dict[int, dict[str, Any]],
    active: dict[int, _ActiveSpan],
    column: int,
) -> int:
    """Fill inherited rowspan positions up to the next free source column."""
    while column in active:
        span = active[column]
        row[column] = ""
        provenance[column] = {
            "source_row": span.source_row,
            "source_cell": span.source_cell,
            "anchor_row": span.anchor_row,
            "anchor_column": span.anchor_column,
            "covered_by_span": True,
        }
        span.remaining_rows -= 1
        if span.remaining_rows == 0:
            del active[column]
        column += 1
    return column


def _header_rows(rows: list[list[str]]) -> list[int]:
    indices: list[int] = []
    for index, row in enumerate(rows[:6]):
        text = " ".join(cell.casefold() for cell in row if cell)
        if any(hint in text for hint in HEADER_HINTS):
            indices.append(index)
    return indices


def _column_labels(rows: list[list[str]], header_rows: list[int]) -> list[str]:
    width = max((len(row) for row in rows), default=0)
    labels: list[str] = []
    for column in range(width):
        pieces: list[str] = []
        for row_index in header_rows:
            value = rows[row_index][column].strip()
            if value and value not in pieces:
                pieces.append(value)
        if pieces:
            labels.append(" · ".join(pieces))
        elif column == 0:
            # This is a UI label, not an inferred source header.  The source
            # header is often intentionally blank for the row-label column.
            labels.append("Nhãn dòng")
        else:
            labels.append(f"Cột nguồn {column + 1}")
    return labels


def _classification_text(context: str, rows: list[list[str]]) -> str:
    preview = " ".join(" ".join(row) for row in rows[:12])
    return normalize_space(f"{context} {preview}").casefold()


def _fold_diacritics(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def _contains_hint(text: str, hint: str) -> bool:
    return hint in text or _fold_diacritics(hint) in _fold_diacritics(text)


def _structural_statement_function(rows: list[list[str]]) -> dict[str, Any] | None:
    """Identify a complete primary statement from its canonical source rows.

    OCR can damage or detach the title immediately preceding a table.  A full
    statement still has a distinctive *set* of line items.  Requiring several
    independent rows avoids turning a one-line note into a primary statement.
    Values and labels remain unchanged; this only classifies their role.
    """
    folded_rows = [
        _fold_diacritics(normalize_space(" ".join(str(cell) for cell in row))).casefold()
        for row in rows
    ]

    # A Vietnamese balance sheet is often extracted as separate HTML tables:
    # current assets, non-current assets, liabilities and equity.  No one
    # fragment can contain the old four-line whole-statement signature below,
    # even though the source rows themselves are exact.  Title text alone is
    # too weak to promote a fragment.  The standard account-code + row-label
    # combinations, however, are a high-specificity fingerprint of the
    # primary statement and distinguish it from an explanatory note.
    def has_code_label(code: str, phrase: str) -> bool:
        for row, folded_row in zip(rows, folded_rows):
            source_codes = {normalize_space(str(cell)) for cell in row}
            if code in source_codes and phrase in folded_row:
                return True
        return False

    asset_fragment_codes = {
        code
        for code, phrase in (
            ("100", "tai san ngan han"),
            ("110", "tien va cac khoan tuong duong tien"),
            ("130", "cac khoan phai thu ngan han"),
            ("140", "hang ton kho"),
            ("150", "tai san ngan han khac"),
        )
        if has_code_label(code, phrase)
    }
    if "100" in asset_fragment_codes and len(asset_fragment_codes) >= 3:
        return {
            "kind": "balance_sheet",
            "label": "Bảng cân đối kế toán",
            "confidence": 0.99,
            "specificity": "structural",
            "matched_evidence": "row_signature:balance_sheet_asset_codes:"
            + ",".join(sorted(asset_fragment_codes)),
        }

    liability_fragment_codes = {
        code
        for code, phrase in (
            ("300", "no phai tra"),
            ("310", "no ngan han"),
            ("330", "no dai han"),
        )
        if has_code_label(code, phrase)
    }
    if liability_fragment_codes == {"300", "310", "330"}:
        return {
            "kind": "balance_sheet",
            "label": "Bảng cân đối kế toán",
            "confidence": 0.99,
            "specificity": "structural",
            "matched_evidence": "row_signature:balance_sheet_liability_codes:"
            + ",".join(sorted(liability_fragment_codes)),
        }

    signatures: tuple[
        tuple[str, str, tuple[str, ...], int], ...
    ] = (
        (
            "cash_flow_statement",
            "Báo cáo lưu chuyển tiền tệ",
            (
                "luu chuyen tien thuan tu hoat dong kinh doanh",
                "luu chuyen tien thuan tu hoat dong dau tu",
                "luu chuyen tien thuan tu hoat dong tai chinh",
                "luu chuyen tien thuan trong ky",
            ),
            2,
        ),
        (
            "income_statement",
            "Báo cáo kết quả hoạt động kinh doanh",
            (
                "doanh thu thuan",
                "loi nhuan gop",
                "ke toan truoc thue",
                "sau thue",
            ),
            3,
        ),
        (
            "balance_sheet",
            "Bảng cân đối kế toán",
            (
                "tai san ngan han",
                "tong cong tai san",
                "no phai tra",
                "von chu so huu",
            ),
            3,
        ),
    )
    for kind, label, phrases, minimum in signatures:
        matched = [
            phrase
            for phrase in phrases
            if any(phrase in row_text for row_text in folded_rows)
        ]
        if len(matched) >= minimum:
            return {
                "kind": kind,
                "label": label,
                "confidence": 0.99,
                "specificity": "structural",
                "matched_evidence": "row_signature:" + ",".join(matched),
            }
    return None


def extract_context_title(context: str) -> str:
    """Return the nearest raw text preceding this table, without prior tables/pages."""
    tail = str(context or "").rsplit("</table>", 1)[-1]
    page_tail = PAGE_MARKER_RE.split(tail)[-1]
    text = BeautifulSoup(page_tail, "lxml").get_text(" ", strip=True)
    return normalize_space(text)[-700:]


def _extract_topic(context_title: str) -> dict[str, Any]:
    matches = list(NUMBERED_TOPIC_RE.finditer(context_title))
    if not matches:
        return {"label": "", "source": "none", "confidence": 0.0}
    match = matches[-1]
    label = normalize_space(f"{match.group(1)}. {match.group(2)}")[:220]
    return {"label": label, "source": "numbered_source_heading", "confidence": 0.92}


def _extract_periods(rows: list[list[str]], column_labels: list[str]) -> list[str]:
    search_values = list(column_labels)
    search_values.extend(cell for row in rows[:5] for cell in row)
    periods: list[str] = []
    for value in search_values:
        for match in PERIOD_RE.finditer(value):
            period = normalize_space(match.group(0))
            if period.casefold() not in {existing.casefold() for existing in periods}:
                periods.append(period)
    return periods[:8]


def _extract_units(context_title: str, rows: list[list[str]]) -> list[str]:
    text = " ".join([context_title, *(cell for row in rows[:5] for cell in row)])
    units: list[str] = []
    for match in UNIT_LABEL_RE.finditer(text):
        unit = normalize_space(match.group(0))
        if unit.casefold() not in {existing.casefold() for existing in units}:
            units.append(unit)
    return units[:4]


def _numeric_cell_count(rows: list[list[str]]) -> int:
    numeric = re.compile(r"^[\s()\-+\d.,%/]+$")
    return sum(
        bool(value and numeric.fullmatch(value) and any(character.isdigit() for character in value))
        for row in rows
        for value in row
    )


def _purpose(
    rows: list[list[str]],
    function: dict[str, Any],
    periods: list[str],
) -> dict[str, Any]:
    row_labels = " ".join(row[0] for row in rows if row).casefold()
    all_text = " ".join(" ".join(row) for row in rows).casefold()
    function_kind = function["kind"]

    if function_kind == "governance_roster":
        return {"kind": "roster", "label": "Liệt kê người và chức vụ", "confidence": 0.96}
    if function_kind == "related_party_schedule":
        return {"kind": "relationship_detail", "label": "Đối chiếu bên, quan hệ và giao dịch", "confidence": 0.94}
    if function_kind == "segment_reporting":
        return {"kind": "segment_comparison", "label": "So sánh kết quả theo bộ phận", "confidence": 0.94}
    if function_kind == "project_schedule":
        return {"kind": "project_detail", "label": "Liệt kê quy mô, vốn và tiến độ dự án", "confidence": 0.9}
    if any(
        hint in row_labels
        for hint in ("số đầu năm", "số cuối năm", "tăng trong năm", "giảm trong năm", "phát sinh trong năm")
    ):
        return {"kind": "movement_schedule", "label": "Đối chiếu biến động đầu kỳ–cuối kỳ", "confidence": 0.94}
    if any(hint in all_text for hint in ("lãi suất", "tỷ lệ (%)", "% sở hữu", "tỷ lệ vốn góp")):
        return {"kind": "rate_schedule", "label": "Trình bày tỷ lệ/lãi suất theo kỳ", "confidence": 0.9}
    if periods and _numeric_cell_count(rows) > 0:
        return {"kind": "period_comparison", "label": "So sánh số dư hoặc giá trị giữa các kỳ", "confidence": 0.86}
    if _numeric_cell_count(rows) > 0:
        return {"kind": "quantitative_detail", "label": "Chi tiết các khoản mục định lượng", "confidence": 0.7}
    if any(len(cell) > 180 for row in rows for cell in row):
        return {"kind": "narrative_detail", "label": "Diễn giải thông tin nguồn", "confidence": 0.72}
    return {"kind": "source_list", "label": "Liệt kê thông tin nguồn", "confidence": 0.6}


def _classify(
    context: str, rows: list[list[str]]
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    context_title = extract_context_title(context)
    text = _classification_text(context_title, rows)
    function = {
        "kind": "unknown",
        "label": "Chưa xác định chức năng bảng",
        "confidence": 0.0,
        "specificity": "unknown",
        "matched_evidence": "",
    }
    structural_function = _structural_statement_function(rows)
    if structural_function:
        function = structural_function
    else:
        for kind, label, hints in FUNCTIONS:
            matched = next((hint for hint in hints if _contains_hint(text, hint)), None)
            if matched:
                specificity = "broad" if kind == "financial_note" else "semantic"
                function = {
                    "kind": kind,
                    "label": label,
                    "confidence": 0.78 if specificity == "broad" else 0.95,
                    "specificity": specificity,
                    "matched_evidence": matched,
                }
                break

    topic = _extract_topic(context_title)
    if function["kind"] == "unknown" and topic["label"] and _numeric_cell_count(rows):
        function = {
            "kind": "financial_note_detail",
            "label": "Bảng chi tiết thuyết minh",
            "confidence": 0.84,
            "specificity": "semantic",
            "matched_evidence": topic["label"],
        }
    elif function["kind"] == "unknown" and _numeric_cell_count(rows):
        function = {
            "kind": "financial_data_schedule",
            "label": "Bảng dữ liệu tài chính",
            "confidence": 0.62,
            "specificity": "generic",
            "matched_evidence": "numeric source cells",
        }
    elif function["kind"] == "unknown" and rows:
        function = {
            "kind": "source_information_table",
            "label": "Bảng thông tin nguồn",
            "confidence": 0.55,
            "specificity": "generic",
            "matched_evidence": "structured source rows",
        }

    section = {
        "kind": "unknown",
        "label": "Chưa xác định phần bảng",
        "confidence": 0.0,
    }
    if function["kind"] == "cash_flow_statement":
        # Cash-flow rows often mention purchases/disposals of assets.  Those
        # words describe transactions, not the accounting side of the table.
        section = {
            "kind": "cash_flow",
            "label": "Lưu chuyển tiền tệ",
            "confidence": 0.98,
            "matched_evidence": "table_function=cash_flow_statement",
        }
        return function, section, context_title, topic
    if function.get("specificity") == "structural" and function["kind"] in {
        "income_statement",
        "balance_sheet",
    }:
        labels = {
            "income_statement": ("income_statement", "Toàn bảng kết quả kinh doanh"),
            "balance_sheet": ("balance_sheet", "Toàn bảng cân đối kế toán"),
        }
        section_kind, section_label = labels[function["kind"]]
        section = {
            "kind": section_kind,
            "label": section_label,
            "confidence": 0.99,
            "matched_evidence": function["matched_evidence"],
        }
        return function, section, context_title, topic
    # Section must come from the table rows themselves.  A report context can
    # mention both assets and liabilities.  The exact nearest topic is allowed,
    # but the broader report context is not.
    section_text = _classification_text(topic["label"], rows)
    for kind, label, hints in SECTION_HINTS:
        matched = next((hint for hint in hints if _contains_hint(section_text, hint)), None)
        if matched:
            section = {
                "kind": kind,
                "label": label,
                "confidence": 0.8,
                "matched_evidence": matched,
            }
            break
    return function, section, context_title, topic


def parse_html_table(table_html: str, *, context: str = "") -> dict[str, Any]:
    """Reconstruct a rectangular table grid from one raw HTML ``<table>``.

    Empty source cells retain their position.  Cells covered by a span render
    as an empty placeholder, while their provenance points to the source cell
    that owns the span.  This keeps display alignment exact without duplicating
    a value into columns where the source did not contain one.
    """
    soup = BeautifulSoup(table_html, "lxml")
    row_maps: list[dict[int, str]] = []
    provenance_maps: list[dict[int, dict[str, Any]]] = []
    active: dict[int, _ActiveSpan] = {}
    empty_source_cells = 0
    expanded_span_cells = 0
    source_row_widths: list[int] = []

    for source_row, tr in enumerate(soup.find_all("tr")):
        source_cells = tr.find_all(["th", "td"], recursive=False)
        # Some OCR HTML omits table section tags and BeautifulSoup may expose
        # descendants only.  The fallback remains limited to this row.
        if not source_cells:
            source_cells = tr.find_all(["th", "td"])
        if not source_cells and not active:
            continue

        row: dict[int, str] = {}
        provenance: dict[int, dict[str, Any]] = {}
        column = 0
        source_row_width = 0
        for source_cell, cell in enumerate(source_cells):
            column = _fill_active_spans(row, provenance, active, column)
            value = normalize_space(cell.get_text(" ", strip=True))
            if not value:
                empty_source_cells += 1
            colspan = _positive_span(cell.get("colspan"))
            rowspan = _positive_span(cell.get("rowspan"))
            source_row_width += colspan
            anchor_column = column
            for offset in range(colspan):
                target = anchor_column + offset
                # Invalid nested/malformed HTML can overlap a live span.  Do
                # not silently move the OCR cell: record the existing position
                # first, then place the new cell in the next free position.
                target = _fill_active_spans(row, provenance, active, target)
                covered = offset > 0
                row[target] = value if not covered else ""
                provenance[target] = {
                    "source_row": source_row,
                    "source_cell": source_cell,
                    "anchor_row": source_row,
                    "anchor_column": column,
                    "covered_by_span": covered,
                }
                if covered:
                    expanded_span_cells += 1
                if rowspan > 1:
                    active[target] = _ActiveSpan(
                        remaining_rows=rowspan - 1,
                        source_row=source_row,
                        source_cell=source_cell,
                        anchor_row=source_row,
                        anchor_column=anchor_column,
                    )
            column = target + 1

        # A rowspan may occupy cells to the right of the final explicit td.
        # Include those cells and genuine missing positions in this row.
        if active:
            while active and column <= max(active):
                column = _fill_active_spans(row, provenance, active, column)
                if active and column <= max(active):
                    row[column] = ""
                    provenance[column] = {
                        "source_row": None,
                        "source_cell": None,
                        "anchor_row": None,
                        "anchor_column": None,
                        "covered_by_span": False,
                    }
                    column += 1
        row_maps.append(row)
        provenance_maps.append(provenance)
        source_row_widths.append(source_row_width)

    width = max((max(row, default=-1) + 1 for row in row_maps), default=0)
    rows = [[row.get(column, "") for column in range(width)] for row in row_maps]
    cell_provenance = [
        [
            provenance.get(
                column,
                {
                    "source_row": None,
                    "source_cell": None,
                    "anchor_row": None,
                    "anchor_column": None,
                    "covered_by_span": False,
                },
            )
            for column in range(width)
        ]
        for provenance in provenance_maps
    ]
    headers = _header_rows(rows)
    column_labels = _column_labels(rows, headers)
    function, section, context_title, topic = _classify(context, rows)
    periods = _extract_periods(rows, column_labels)
    units = _extract_units(context_title, rows)
    purpose = _purpose(rows, function, periods)
    summary_parts = [function["label"], purpose["label"]]
    if topic["label"]:
        summary_parts.append(f"chủ đề nguồn: {topic['label']}")
    if periods:
        summary_parts.append("kỳ: " + ", ".join(periods))
    if units:
        summary_parts.append("đơn vị thấy trong nguồn: " + ", ".join(units))
    context_summary = "; ".join(summary_parts) + "."
    flags = ["empty_cells_preserved"]
    if expanded_span_cells:
        flags.append("span_cells_expanded")
    if len(set(source_row_widths)) > 1:
        flags.append("irregular_source_row_widths")
    if not headers:
        flags.append("header_not_detected")
    confidence = 0.98 if width >= 2 else 0.55
    if not headers:
        confidence -= 0.2
    if not rows:
        confidence = 0.0

    return {
        "structure_version": 2,
        "context_schema_version": 2,
        "rows": rows,
        "column_labels": column_labels,
        "header_row_indices": headers,
        "cell_provenance": cell_provenance,
        "table_function": function,
        "table_section": section,
        "table_purpose": purpose,
        "context_trace": {
            "source_title": context_title,
            "topic": topic,
            "period_labels": periods,
            "unit_labels": units,
            "summary": context_summary,
        },
        "structure_quality": {
            "status": "reconstructed_from_raw_html",
            "confidence": round(max(0.0, confidence), 3),
            "flags": flags,
            "column_count": width,
            "row_count": len(rows),
            "empty_source_cell_count": empty_source_cells,
            "expanded_span_cell_count": expanded_span_cells,
        },
    }
