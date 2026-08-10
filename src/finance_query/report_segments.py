"""Source-bounded normalization for financial-report table context.

The normalized segment is a navigation/retrieval aid, never a replacement for
the source grid.  It retains the table UID and raw-context digest, avoids
numeric cells, and states only metadata or headings observable in the report.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from bs4 import BeautifulSoup

from .table_structure import normalize_space, sha256_file


SEGMENT_VERSION = 1
NORMALIZATION_POLICY = "source_heading_metadata_only_no_numeric_inference_v2"
MAX_SOURCE_HEADING_CHARS = 140
MAX_READER_HEADING_CHARS = 108
PAGE_RE = re.compile(r"={3,}\s*PAGE\s*\d+\s*={3,}", re.IGNORECASE)
# A note title can be ``35. Tên note`` or ``9.6 Tên sub-note``.  Restrict
# numeric components to one/two digits and require an uppercase title token:
# accepting arbitrary dotted numbers turns OCR prose such as ``4.997 tỷ`` into
# a fake heading.  This deliberately drops uncertain headings; V3 function,
# period and unit metadata remains available as the readable fallback.
NUMBERED_HEADING_RE = re.compile(
    r"(?<![\w.])"
    r"(?:"
    r"(?:[IVXLCDM]+|\d{1,2})\s*[.)]"
    r"|\d{1,2}(?:\.\d{1,2}){1,2}(?:\s*[.)])?"
    r")"
    r"(?=\s+[A-ZÀ-ỸĐ])",
    re.IGNORECASE,
)
NUMERIC_SUBHEADING_RE = re.compile(r"^(\d+)(?:\.\d+)+\s*[.)]?\s+", re.IGNORECASE)
STATEMENT_RE = re.compile(
    r"(?:bảng\s+cân\s+đối\s+kế\s+toán|báo\s+cáo\s+kết\s+quả\s+hoạt\s+động\s+kinh\s+doanh|báo\s+cáo\s+lưu\s+chuyển\s+tiền\s+tệ)",
    re.IGNORECASE,
)
# A phrase naming a report in narrative prose is not a report title.  The
# source must immediately show a title-only qualifier such as the reporting
# date, financial year, scope, or form number.  This is presentation metadata,
# but failing closed avoids a misleading title above the source grid.
STATEMENT_TITLE_SUFFIX_RE = re.compile(
    r"\s*(?:(?:hợp\s+nhất|riêng)\s*)?"
    r"(?:tại\s+ngày|cho\s+năm(?:\s+tài\s+chính)?|năm\s+tài\s+chính\s+kết\s+thúc|mẫu\s+số)\b",
    re.IGNORECASE,
)
BOILERPLATE_RE = re.compile(
    r"\s+(?:mẫu\s+(?:số\s+)?[A-Z0-9 .\-–/]+|\(\s*ban\s+hành\s+theo\b).*",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"\w+", re.UNICODE)
# These are source-observed sentence starters, not accounting-topic guesses.
# They are only used to hide the explanatory continuation after an already
# recognised numbered title in the compact reader view. The full source
# excerpt stays in ``source_heading`` for audit and direct-evidence provenance.
READER_PROSE_START_RE = re.compile(
    r"\s+(?=(?:"
    r"ngoài\s+(?:các\s+)?(?:số\s+dư|khoản|thông\s+tin)\b"
    r"|dưới\s+đây\b"
    r"|số\s+(?:thuế|tiền|dư|liệu)\b"
    r"|thay\s+đổi\s+(?:tài\s+sản|dự\s+phòng|vốn|số\s+dư|các\s+khoản)\b"
    r"|độ\s+nhạy\s+đối\s+với\b"
    r"|tổng\s+(?:doanh\s+thu|giá\s+trị)\s+(?:thể\s+hiện|bao\s+gồm)\b"
    r"|quỹ\s+này\s+(?:được|là)\b"
    r"|tại\s+ngày\b|trong\s+năm\b|với\s+giả\s+định\b|mỗi\s+cổ\s+phiếu\b"
    r"))",
    re.IGNORECASE,
)


def _html_tail(raw_context: object) -> str:
    """Drop prior tables/pages before extracting a source heading."""
    tail = str(raw_context or "").rsplit("</table>", 1)[-1]
    tail = PAGE_RE.split(tail)[-1]
    text = BeautifulSoup(tail, "lxml").get_text(" ", strip=True)
    return normalize_space(text)


def _source_page_text(raw_context: object) -> str:
    """Return current-page source text while retaining parent headings.

    Unlike ``_html_tail`` this deliberately retains preceding source tables on
    the page.  It is used only to recover a numbered parent heading for a
    numbered child heading; raw grid cells remain out of report segments.
    """
    page = PAGE_RE.split(str(raw_context or ""))[-1]
    return normalize_space(BeautifulSoup(page, "lxml").get_text(" ", strip=True))


def _parent_heading(raw_context: object, heading: str) -> str:
    """Find an explicit root heading for a numbered child heading, if any."""
    match = NUMERIC_SUBHEADING_RE.match(heading)
    if match is None:
        return ""
    root = match.group(1)
    # A root note heading in these reports often appears as ``9 CHO VAY ...``
    # whereas its child table is ``9.6 Theo ...``.  Require an uppercase
    # source title and reject ``9.`` so row numbers/subheadings cannot become
    # an invented parent context.
    pattern = re.compile(
        rf"(?:^|\s){re.escape(root)}(?!\s*\.)\s+([A-ZÀ-ỸĐ][A-ZÀ-ỸĐ\s\-–]{{4,120}})(?=\s*\(|\s*$)",
        re.UNICODE,
    )
    matches = list(pattern.finditer(_source_page_text(raw_context)))
    if not matches:
        return ""
    return normalize_space(f"{root} {matches[-1].group(1)}")[:MAX_SOURCE_HEADING_CHARS]


def _heading(text: str) -> tuple[str, str]:
    statement = [
        match
        for match in STATEMENT_RE.finditer(text)
        if STATEMENT_TITLE_SUFFIX_RE.match(text[match.end() :])
    ]
    if statement:
        match = statement[-1]
        # Start precisely at the recognised report name.  The context preceding
        # it frequently contains the entity name, a prior page tail, or OCR
        # debris; none of that describes this table's function.
        heading = normalize_space(text[match.start() : min(len(text), match.end() + 180)])
        return (
            normalize_space(BOILERPLATE_RE.sub("", heading))[:MAX_SOURCE_HEADING_CHARS],
            "statement_heading",
        )
    numbered = list(NUMBERED_HEADING_RE.finditer(text))
    if numbered:
        # OCR can merge the prose following a numbered note heading into one
        # text node.  A bounded excerpt is less misleading than treating that
        # prose as a table title; the unchanged raw context remains available
        # for audit in the V2 source record.
        heading = _trim_immediate_repeated_phrase(
            text[numbered[-1].start() : numbered[-1].start() + 180]
        )
        return normalize_space(heading)[:MAX_SOURCE_HEADING_CHARS], "numbered_heading"
    return "", "none"


def _trim_immediate_repeated_phrase(text: str) -> str:
    """Remove OCR's repeated note-subheading before its explanatory prose.

    A common source shape is ``6. Investments (a) Securities Securities
    reflect ...``: the first occurrence is a subheading and the immediately
    repeated one begins prose.  We only cut when three or more adjacent source
    words repeat exactly, so this cannot rewrite numeric cells or manufacture
    a title from a semantic guess.
    """
    words = [(match.group(0).casefold(), match.start()) for match in WORD_RE.finditer(text)]
    for start in range(len(words)):
        for width in range(8, 2, -1):
            end = start + 2 * width
            if end > len(words):
                continue
            if [word for word, _ in words[start : start + width]] == [
                word for word, _ in words[start + width : end]
            ]:
                return text[: words[start + width][1]].rstrip()
    return text


def _reader_heading(source_heading: str, heading_kind: str) -> tuple[str, str]:
    """Make a short display title while retaining the raw title excerpt.

    Long heading-like OCR often contains the opening sentence of a note. A
    compact reader must not paraphrase that sentence. It may display a prefix
    only at one explicit source sentence boundary; otherwise it falls back to
    V3's table-function descriptor rather than a noisy pseudo-title.
    """
    heading = normalize_space(source_heading)
    if not heading:
        return "", "fallback_to_descriptor"
    if heading_kind == "numbered_heading":
        boundary = READER_PROSE_START_RE.search(heading)
        if boundary is not None:
            prefix = normalize_space(heading[: boundary.start()])
            if prefix and len(prefix) <= MAX_READER_HEADING_CHARS:
                return prefix, "source_title_before_prose"
    if len(heading) <= MAX_READER_HEADING_CHARS:
        return heading, "source_heading"
    # Do not truncate a long source phrase into a new implied title.
    return "", "fallback_to_descriptor"


def _unique(values: list[str], limit: int) -> list[str]:
    """Keep source labels in order while dropping literal duplicates."""
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = normalize_space(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            output.append(cleaned)
            seen.add(key)
        if len(output) >= limit:
            break
    return output


def _descriptor(
    function: Mapping[str, Any],
    section: Mapping[str, Any],
    heading: str,
    period_labels: list[str],
    unit_labels: list[str],
) -> str:
    """Render a small navigation sentence without serialising table cells.

    Function, period and unit labels are canonical/source-observed metadata.
    We intentionally exclude row labels, formulas and numeric values: they are
    evidence only when displayed from the exact V2 grid.
    """
    function_label = normalize_space(str(function.get("label") or ""))
    section_label = normalize_space(str(section.get("label") or ""))
    parts: list[str] = []
    if function_label:
        parts.append(function_label)
    section_kind = str(section.get("kind") or "")
    if (
        section_label
        and section_kind != "unknown"
        and section_label.casefold() not in function_label.casefold()
        and function_label.casefold() not in section_label.casefold()
    ):
        parts.append("phần: " + section_label)
    if not parts and heading:
        parts.append(heading)
    if period_labels:
        parts.append("kỳ: " + ", ".join(period_labels))
    if unit_labels:
        parts.append("đơn vị: " + ", ".join(unit_labels))
    return " · ".join(parts)[:520]


def validate_report_segment_sidecar(bundle: Path, sidecar: Path) -> dict[str, Any]:
    """Validate a segment sidecar against its immutable bundle inputs.

    A segment is navigation metadata only.  Hash validation prevents a clean
    heading generated for a different report/table grid from being silently
    shown beside the current source rows.
    """
    bundle = bundle.resolve()
    sidecar = sidecar.resolve()
    if sidecar.parent != bundle:
        raise ValueError("Report-segment sidecar must reside directly in bundle-dir")
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not sidecar.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Report-segment sidecar or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != SEGMENT_VERSION:
        raise ValueError("Unsupported report-segment sidecar schema")
    context_name = str(manifest.get("evidence_context_file") or "")
    if not context_name or Path(context_name).name != context_name:
        raise ValueError("Report-segment manifest has an invalid evidence-context filename")
    expected_paths = {
        "bundle_tables_sha256": bundle / "tables.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
        "evidence_context_sha256": bundle / context_name,
    }
    for key, path in expected_paths.items():
        if not path.is_file() or str(manifest.get(key) or "") != sha256_file(path):
            raise ValueError(f"Report-segment manifest does not match {path.name}")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError("Report-segment sidecar checksum mismatch")
    if manifest.get("evidence_eligible") is not False or manifest.get("training_eligible") is not False:
        raise ValueError("Report-segment sidecar must remain non-evidence and non-training metadata")
    return manifest


def build_report_segment(
    table: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Produce a compact source-derived context record for one raw table."""
    raw_context = str(table.get("context_before") or "")
    clean_context = _html_tail(raw_context)
    heading, heading_source = _heading(clean_context)
    parent_heading = _parent_heading(raw_context, heading)
    trace = context.get("context_trace") or table.get("context_trace") or {}
    function = context.get("table_function") or table.get("table_function") or {}
    section = context.get("table_section") or table.get("table_section") or {}
    columns = (context.get("canonical_headers") or {}).get("columns") or []
    period_labels = [
        str(label)
        for column in columns
        for label in column.get("period_labels") or []
        if str(label)
    ]
    unit_labels = [
        str(label)
        for column in columns
        for label in column.get("unit_labels") or []
        if str(label)
    ]
    if not period_labels:
        period_labels = [str(value) for value in trace.get("period_labels") or [] if str(value)]
    if not unit_labels:
        unit_labels = [str(value) for value in trace.get("unit_labels") or [] if str(value)]
    period_labels = _unique(period_labels, 8)
    unit_labels = _unique(unit_labels, 4)
    reader_heading, reader_heading_status = _reader_heading(heading, heading_source)
    return {
        "schema_version": SEGMENT_VERSION,
        "internal_table_uid": str(table["internal_table_uid"]),
        "document_id": table.get("document_id"),
        "ticker": table.get("ticker"),
        "report_year": table.get("report_year"),
        "scope": table.get("scope"),
        "local_ordinal": table.get("local_ordinal"),
        "source_context_sha256": hashlib.sha256(raw_context.encode("utf-8")).hexdigest(),
        "source_heading": heading,
        "source_heading_kind": heading_source,
        "source_parent_heading": parent_heading,
        "reader_heading": reader_heading,
        "reader_heading_status": reader_heading_status,
        "table_function": function,
        "table_section": section,
        "period_labels": period_labels,
        "unit_labels": unit_labels,
        "compact_descriptor": _descriptor(
            function, section, heading, period_labels, unit_labels
        ),
        "normalization_policy": NORMALIZATION_POLICY,
        "evidence_eligible": False,
        "training_eligible": False,
    }
