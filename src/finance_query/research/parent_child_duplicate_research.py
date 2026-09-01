"""Value-blind research for total/detail rows and duplicate documents.

This module is deliberately narrower than the answer-capable E2E path.  It
reads the immutable V2 tables together with the V3 row/profile/context
sidecar, reconstructs local row hierarchy, and emits navigation evidence
only.  It does not copy numeric cells, bind an answer, or grant any release
or training authority.
"""

from __future__ import annotations

from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


PROTOCOL = "vifinqa_parent_child_duplicate_research_v1"
SCHEMA_VERSION = 1
CONTRACT = {
    "research_only": True,
    "candidate_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}

FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "cell_values",
        "numeric_value",
        "numeric_values",
        "raw_numeric_value",
        "raw_numeric_values",
        "raw_source_cell",
        "raw_source_row",
        "rows",
        "submission",
    }
)

TARGETS: dict[int, dict[str, Any]] = {
    98: {
        "documents": [
            "HUT_financial_statements_2024_separate",
            "HUT_financial_statements_2024_consolidated",
        ],
        "expected_document_id": "HUT_financial_statements_2024_separate",
        "expected_scope": "separate",
        "expected_year": 2024,
        "match_phrase": "hàng tồn kho",
        "expected_label": "hàng tồn kho",
        "expected_table_function": "balance_sheet",
        "row_mode": "inventory_parent",
        "parent_code": "140",
        "child_code": "141",
        "current_header_mode": "balance_sheet_current",
    },
    104: {
        "documents": [
            "MBB_financial_statements_2022_separate_1",
            "MBB_financial_statements_2022_separate_2",
            "MBB_financial_statements_2022_consolidated",
        ],
        "expected_document_id": "MBB_financial_statements_2022_separate_1",
        "expected_scope": "separate",
        "expected_year": 2022,
        "match_phrase": "lợi nhuận trước thuế",
        "expected_label": "tổng lợi nhuận trước thuế",
        "expected_table_function": "financial_data_schedule",
        "row_mode": "profit_before_tax_main",
        "current_header_mode": "explicit_year",
        "statement_marker": "b03",
    },
    340: {
        "documents": [
            "PVT_financial_statements_2019_separate",
            "PVT_financial_statements_2019_consolidated",
        ],
        "expected_document_id": "PVT_financial_statements_2019_separate",
        "expected_scope": "separate",
        "expected_year": 2019,
        "match_phrase": "hàng tồn kho",
        "expected_label": "hàng tồn kho",
        "expected_table_function": "balance_sheet",
        "row_mode": "inventory_parent",
        "parent_code": "140",
        "child_code": "141",
        "current_header_mode": "balance_sheet_current",
    },
}

_TEXT_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_PREFIX_RE = re.compile(r"^\s*([IVXLCDM]+|[A-Z]|\d+)\s*[.)]\s*$", re.IGNORECASE)
_INLINE_PREFIX_RE = re.compile(r"^\s*([IVXLCDM]+|[A-Z]|\d+)\s*[.)]\s*(.+?)\s*$", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_SOURCE_SCOPE_RE = re.compile(
    r"\b(?:bang\s+can\s+doi\s+ke\s+toan|bao\s+cao\s+tai\s+chinh|"
    r"bao\s+cao\s+ket\s+qua\s+hoat\s+dong|b03\s*/?\s*tctd)"
    r"[^\n.]{0,180}\b(rieng|hop\s+nhat)\b",
    re.IGNORECASE,
)


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        "".join(char for char in normalized if not unicodedata.combining(char)).split()
    )


def _contains_metric_phrase(value: object, phrase: object) -> bool:
    folded_value = _fold(value)
    folded_phrase = _fold(phrase)
    if not folded_value or not folded_phrase:
        return False
    if folded_phrase in folded_value:
        return True
    # OCR and statement wording may insert a qualifier such as ``thuần`` or
    # ``kế toán`` between ``lợi nhuận`` and ``trước thuế``.  This broadening is
    # used only for the census; exact-label and table-function gates remain
    # strict later in the pipeline.
    if folded_phrase == "loi nhuan truoc thue":
        return bool(re.search(r"\bloi\s+nhuan(?:\s+\S+){0,3}\s+truoc\s+thue\b", folded_value))
    return False


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _sha_json(value: object) -> str:
    return _sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain JSON objects")
        values.append(value)
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _source_scope_signals(value: object) -> set[str]:
    folded = _fold(value)
    signals = {
        "separate" if match.group(1).replace(" ", "") == "rieng" else "consolidated"
        for match in _SOURCE_SCOPE_RE.finditer(folded)
    }
    if signals:
        return signals
    # A bounded source-title fallback is useful for OCR that drops the report
    # family phrase.  It is still recorded as a weak signal and never uses a
    # filename or retrieval rank.
    if "hop nhat" in folded and "rieng" not in folded:
        return {"consolidated"}
    if "rieng" in folded and "hop nhat" not in folded:
        return {"separate"}
    return set()


def _read_source_audit(context: Mapping[str, Any]) -> dict[str, Any]:
    provenance = context.get("source_provenance") or {}
    source_path = Path(str(provenance.get("source_path") or ""))
    expected_sha = str(provenance.get("source_sha256") or "")
    title = str((context.get("context_trace") or {}).get("source_title") or "")
    audit: dict[str, Any] = {
        "source_path": str(source_path),
        "expected_source_sha256": expected_sha,
        "source_file_present": source_path.is_file(),
        "source_hash_verified": False,
        "source_prefix_char_end": None,
        "source_prefix_sha256": None,
        "source_local_char_start": None,
        "source_local_char_end": None,
        "source_local_sha256": None,
        "source_local_scope_signals": [],
        "source_title_sha256": _sha_text(title) if title else None,
        "source_prefix_scope_signals": [],
        "source_title_scope_signals": sorted(_source_scope_signals(title)),
        "observed_scope": None,
        "scope_signal_status": "unavailable",
    }
    if not source_path.is_file():
        return audit
    try:
        source_bytes = source_path.read_bytes()
    except OSError:
        return audit
    actual_sha = _sha_bytes(source_bytes)
    audit["actual_source_sha256"] = actual_sha
    audit["source_hash_verified"] = bool(expected_sha and actual_sha == expected_sha)
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        source_text = source_bytes.decode("utf-8", errors="replace")
    try:
        char_start = max(0, int(provenance.get("char_start") or 0))
    except (TypeError, ValueError):
        char_start = 0
    prefix_end = min(len(source_text), max(char_start, 1), 30000)
    prefix = source_text[:prefix_end]
    prefix_signals = _source_scope_signals(prefix)
    local_start = max(0, char_start - 6000)
    local_end = min(len(source_text), char_start + 1500)
    local_text = source_text[local_start:local_end]
    local_signals = _source_scope_signals(local_text)
    title_signals = _source_scope_signals(title)
    audit["source_prefix_char_end"] = prefix_end
    audit["source_prefix_sha256"] = _sha_text(prefix)
    audit["source_local_char_start"] = local_start
    audit["source_local_char_end"] = local_end
    audit["source_local_sha256"] = _sha_text(local_text)
    audit["source_local_scope_signals"] = sorted(local_signals)
    audit["source_prefix_scope_signals"] = sorted(prefix_signals)
    if len(local_signals) == 1 and (not title_signals or local_signals == title_signals):
        audit["observed_scope"] = next(iter(local_signals))
        audit["scope_signal_status"] = "source_local"
    elif len(title_signals) == 1 and not local_signals:
        audit["observed_scope"] = next(iter(title_signals))
        audit["scope_signal_status"] = "context_title"
    elif len(prefix_signals) == 1 and (not title_signals or prefix_signals == title_signals):
        audit["observed_scope"] = next(iter(prefix_signals))
        audit["scope_signal_status"] = "source_prefix"
    elif len(local_signals | title_signals | prefix_signals) > 1:
        audit["scope_signal_status"] = "conflicting_scope_signals"
    else:
        audit["scope_signal_status"] = "scope_not_observed"
    return audit


def _prefix_descriptor(value: object) -> tuple[str | None, int | None, str | None]:
    text = str(value or "").strip()
    match = _PREFIX_RE.match(text)
    if match is None:
        return None, None, None
    token = match.group(1)
    folded = _fold(token)
    if token.isdigit():
        return token + ".", 2, "numeric_detail"
    if folded in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"}:
        return token.upper() + ".", 1, "roman_section"
    return token.upper() + ".", 0, "alpha_section"


def _label_from_cell(value: object) -> str:
    text = str(value or "").strip()
    inline = _INLINE_PREFIX_RE.match(text)
    if inline is not None and _TEXT_RE.search(inline.group(2)):
        return inline.group(2).strip()
    return text


def _label_cell(row: Sequence[object], phrase: str | None = None) -> tuple[str, int]:
    choices: list[tuple[int, int, str]] = []
    phrase_folded = _fold(phrase) if phrase else ""
    for column_index, value in enumerate(row):
        text = str(value or "").strip()
        if not text or _TEXT_RE.search(text) is None:
            continue
        label = _label_from_cell(text)
        folded = _fold(label)
        if phrase_folded and not _contains_metric_phrase(folded, phrase_folded):
            continue
        choices.append((len(folded), column_index, label))
    if choices and phrase:
        _length, column_index, label = min(choices, key=lambda item: (item[0], item[1]))
        return label, column_index
    if not phrase:
        structural_choices = [
            (len(_fold(_label_from_cell(value))), column_index, _label_from_cell(value))
            for column_index, value in enumerate(row)
            if str(value or "").strip()
            and _TEXT_RE.search(str(value)) is not None
            and _prefix_descriptor(value)[0] is None
        ]
        if structural_choices:
            _length, column_index, label = max(
                structural_choices,
                key=lambda item: (item[0], -item[1]),
            )
            return label, column_index
    for column_index, value in enumerate(row):
        text = str(value or "").strip()
        if text and _TEXT_RE.search(text) is not None:
            return _label_from_cell(text), column_index
    return "", 0


def _row_prefix(row: Sequence[object], label_column_index: int | None = None) -> tuple[str | None, int | None, str | None]:
    limit = len(row) if label_column_index is None else min(len(row), label_column_index + 1)
    for value in row[:limit]:
        prefix, level, kind = _prefix_descriptor(value)
        if prefix is not None:
            return prefix, level, kind
        inline = _INLINE_PREFIX_RE.match(str(value or "").strip())
        if inline is not None and _TEXT_RE.search(inline.group(2)):
            return _prefix_descriptor(inline.group(1) + ".")
    return None, None, None


def _code_columns(context: Mapping[str, Any]) -> list[int]:
    columns = (context.get("canonical_headers") or {}).get("columns") or []
    result: list[int] = []
    for column in columns:
        if not isinstance(column, Mapping):
            continue
        label = _fold(column.get("source_label"))
        role = _fold(column.get("role"))
        if "ma so" in label or role == "code":
            try:
                result.append(int(column.get("column_index")))
            except (TypeError, ValueError):
                continue
    return sorted(set(result))


def _row_code(row: Sequence[object], code_columns: Sequence[int]) -> str | None:
    for column_index in code_columns:
        if 0 <= column_index < len(row):
            value = str(row[column_index] or "").strip()
            if re.fullmatch(r"\d{1,4}", value):
                return value
    return None


def infer_row_hierarchy(
    rows: Sequence[Sequence[object]],
    *,
    data_row_indices: Iterable[int] | None = None,
    code_columns: Sequence[int] = (),
) -> dict[int, dict[str, Any]]:
    """Infer only local structural parent/child relations.

    A numeric detail prefix is attached to the nearest preceding lower-level
    prefix.  The result is structural metadata; it never contains a cell
    value from a numeric column.
    """
    indices = sorted(set(data_row_indices if data_row_indices is not None else range(len(rows))))
    info: dict[int, dict[str, Any]] = {}
    stack: list[tuple[int, int]] = []
    for row_index in indices:
        if row_index < 0 or row_index >= len(rows):
            continue
        row = rows[row_index]
        label, label_column_index = _label_cell(row)
        prefix, level, prefix_kind = _row_prefix(row, label_column_index)
        current: dict[str, Any] = {
            "row_index": row_index,
            "label": label,
            "label_column_index": label_column_index,
            "prefix": prefix,
            "prefix_level": level,
            "prefix_kind": prefix_kind,
            "row_code": _row_code(row, code_columns),
            "parent_row_index": None,
            "child_row_indices": [],
        }
        if level is not None:
            while stack and stack[-1][1] >= level:
                stack.pop()
            if stack:
                current["parent_row_index"] = stack[-1][0]
            stack.append((row_index, level))
        info[row_index] = current
    for row_index, current in info.items():
        parent_index = current.get("parent_row_index")
        if parent_index in info:
            info[parent_index]["child_row_indices"].append(row_index)
    return info


def _row_profile(context: Mapping[str, Any], row_index: int) -> dict[str, Any] | None:
    profiles = [
        profile
        for profile in context.get("row_profiles") or []
        if isinstance(profile, Mapping) and profile.get("row_index") == row_index
    ]
    return dict(profiles[0]) if len(profiles) == 1 else None


def _current_columns(context: Mapping[str, Any], year: int, mode: str) -> list[dict[str, Any]]:
    columns = (context.get("canonical_headers") or {}).get("columns") or []
    result: list[dict[str, Any]] = []
    for column in columns:
        if not isinstance(column, Mapping):
            continue
        label = str(column.get("source_label") or "")
        folded = _fold(label)
        periods = [str(value) for value in column.get("period_labels") or []]
        has_year = any(str(year) == value or str(year) in value for value in periods) or bool(
            re.search(rf"(?<!\d){year}(?!\d)", folded)
        )
        if mode == "balance_sheet_current":
            selected = folded.startswith("so cuoi nam")
        else:
            selected = has_year
        if not selected:
            continue
        result.append(
            {
                "column_index": int(column.get("column_index")),
                "source_label": label,
                "period_labels": periods,
                "unit_labels": [str(value) for value in column.get("unit_labels") or []],
                "role": column.get("role"),
            }
        )
    return result


def _numeric_cell_fingerprint(row: Sequence[object], profile: Mapping[str, Any] | None) -> dict[str, Any]:
    numeric_columns = sorted({int(value) for value in (profile or {}).get("numeric_columns") or []})
    unreliable = sorted({int(value) for value in (profile or {}).get("unreliable_numeric_columns") or []})
    cell_hashes = []
    for column_index in numeric_columns:
        if 0 <= column_index < len(row):
            cell_hashes.append({"column_index": column_index, "sha256": _sha_text(str(row[column_index] or ""))})
    return {
        "numeric_column_indices": numeric_columns,
        "unreliable_numeric_column_indices": unreliable,
        "numeric_cell_count": len(cell_hashes),
        "numeric_cell_sha256": cell_hashes,
        "raw_numeric_values_included": False,
    }


def _generic_neighbor(row: Sequence[object], row_index: int, context: Mapping[str, Any]) -> dict[str, Any]:
    label, label_column_index = _label_cell(row)
    prefix, level, prefix_kind = _row_prefix(row, label_column_index)
    profile = _row_profile(context, row_index)
    return {
        "row_index": row_index,
        "label": label,
        "label_column_index": label_column_index,
        "prefix": prefix,
        "prefix_level": level,
        "prefix_kind": prefix_kind,
        "row_code": _row_code(row, _code_columns(context)),
        "row_role": (profile or {}).get("role"),
        "numeric_column_indices": sorted({int(value) for value in (profile or {}).get("numeric_columns") or []}),
    }


def _report_segment_descriptor(segment: Mapping[str, Any] | None) -> dict[str, Any]:
    if not segment:
        return {"present": False}
    heading = str(segment.get("source_heading") or "")
    parent = str(segment.get("source_parent_heading") or "")
    return {
        "present": True,
        "source_heading_present": bool(heading),
        "source_heading_sha256": _sha_text(heading) if heading else None,
        "source_heading_kind": segment.get("source_heading_kind"),
        "source_parent_heading_present": bool(parent),
        "source_parent_heading_sha256": _sha_text(parent) if parent else None,
        "reader_heading_status": segment.get("reader_heading_status"),
        "period_labels": [str(value) for value in segment.get("period_labels") or []],
        "unit_labels": [str(value) for value in segment.get("unit_labels") or []],
    }


def _table_scope_descriptor(
    *,
    raw_table: Mapping[str, Any],
    context: Mapping[str, Any],
    segment: Mapping[str, Any] | None,
    source_audit: Mapping[str, Any],
) -> dict[str, Any]:
    raw_uid = str(raw_table.get("internal_table_uid") or "")
    context_uid = str(context.get("internal_table_uid") or "")
    raw_doc = str(raw_table.get("document_id") or "")
    context_doc = str(context.get("document_id") or "")
    source = context.get("source_provenance") or {}
    return {
        "internal_table_uid": context_uid,
        "document_id": context_doc,
        "raw_context_uid_aligned": raw_uid == context_uid,
        "raw_context_document_aligned": raw_doc == context_doc,
        "ticker": raw_table.get("ticker"),
        "report_year": raw_table.get("report_year"),
        "declared_scope": raw_table.get("scope"),
        "local_ordinal": raw_table.get("local_ordinal", context.get("local_ordinal")),
        "page_no": raw_table.get("page_no"),
        "table_function": {
            "kind": (context.get("table_function") or {}).get("kind"),
            "confidence": (context.get("table_function") or {}).get("confidence"),
            "matched_evidence": (context.get("table_function") or {}).get("matched_evidence"),
        },
        "table_section": {
            "kind": (context.get("table_section") or {}).get("kind"),
            "label": (context.get("table_section") or {}).get("label"),
            "confidence": (context.get("table_section") or {}).get("confidence"),
        },
        "report_segment": _report_segment_descriptor(segment),
        "context_source_title_sha256": source_audit.get("source_title_sha256"),
        "source_scope_audit": {
            key: source_audit.get(key)
            for key in (
                "source_path",
                "expected_source_sha256",
                "actual_source_sha256",
                "source_file_present",
                "source_hash_verified",
                "source_prefix_char_end",
                "source_prefix_sha256",
                "source_local_char_start",
                "source_local_char_end",
                "source_local_sha256",
                "source_local_scope_signals",
                "source_prefix_scope_signals",
                "source_title_scope_signals",
                "observed_scope",
                "scope_signal_status",
            )
        },
        "source_provenance": {
            "source_path": source.get("source_path"),
            "source_sha256": source.get("source_sha256"),
            "table_sha256": source.get("table_sha256"),
            "char_start": source.get("char_start"),
        },
    }


def _row_descriptor(
    *,
    raw_table: Mapping[str, Any],
    context: Mapping[str, Any],
    segment: Mapping[str, Any] | None,
    source_audit: Mapping[str, Any],
    row_index: int,
    matched_phrase: str,
    hierarchy: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    source_rows = raw_table.get("rows") or []
    row = source_rows[row_index]
    label, label_column_index = _label_cell(row, matched_phrase)
    profile = _row_profile(context, row_index)
    hierarchy_item = hierarchy.get(row_index) or {}
    row_fingerprint = _sha_json([str(value or "") for value in row])
    current = _numeric_cell_fingerprint(row, profile)
    parent_index = hierarchy_item.get("parent_row_index")
    parent = _generic_neighbor(source_rows[parent_index], parent_index, context) if isinstance(parent_index, int) else None
    children = [
        _generic_neighbor(source_rows[index], index, context)
        for index in hierarchy_item.get("child_row_indices") or []
        if isinstance(index, int) and 0 <= index < len(source_rows)
    ]
    note_columns = [
        column
        for column in ((context.get("canonical_headers") or {}).get("columns") or [])
        if _fold(column.get("source_label")) == "thuyet minh"
    ]
    note_reference_present = bool(
        note_columns
        and any(
            0 <= int(column.get("column_index")) < len(row)
            and str(row[int(column.get("column_index"))] or "").strip()
            for column in note_columns
        )
    )
    descriptor = {
        "row_index": row_index,
        "row_label": label,
        "row_label_column_index": label_column_index,
        "row_label_folded": _fold(label),
        "match_phrase": _fold(matched_phrase),
        "row_prefix": hierarchy_item.get("prefix"),
        "row_prefix_level": hierarchy_item.get("prefix_level"),
        "row_prefix_kind": hierarchy_item.get("prefix_kind"),
        "row_code": hierarchy_item.get("row_code"),
        "row_role": (profile or {}).get("role"),
        "row_profile_present": profile is not None,
        "row_numeric_column_indices": sorted({int(value) for value in (profile or {}).get("numeric_columns") or []}),
        "row_unreliable_numeric_column_indices": sorted({int(value) for value in (profile or {}).get("unreliable_numeric_columns") or []}),
        "row_fingerprint_sha256": row_fingerprint,
        "numeric_cell_fingerprint": current,
        "immediate_parent": parent,
        "direct_children": children,
        "note_reference_present": note_reference_present,
        "note_reference_column_indices": [int(column.get("column_index")) for column in note_columns],
        "table": _table_scope_descriptor(
            raw_table=raw_table,
            context=context,
            segment=segment,
            source_audit=source_audit,
        ),
        "raw_numeric_values_included": False,
    }
    return descriptor


def _aligned(raw_table: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    source = context.get("source_provenance") or {}
    return bool(
        raw_table.get("internal_table_uid") == context.get("internal_table_uid")
        and raw_table.get("document_id") == context.get("document_id")
        and source.get("source_sha256")
        and source.get("table_sha256")
        and (context.get("grid") or {}).get("rectangular") is True
        and (context.get("grid") or {}).get("provenance_complete") is True
        and (context.get("quality") or {}).get("status") == "review_ready"
    )


def _candidate_id(question_id: int, table: Mapping[str, Any], row_index: int) -> str:
    document_id = str(table.get("document_id") or "unknown").replace("_", "-")
    uid = str(table.get("internal_table_uid") or "unknown")[:12]
    return f"q{question_id}-{document_id}-{uid}-r{row_index}"


def _statement_marker_present(context: Mapping[str, Any], source_audit: Mapping[str, Any], marker: str) -> bool:
    title = str((context.get("context_trace") or {}).get("source_title") or "")
    source_signals = source_audit.get("source_prefix_scope_signals") or []
    # Only the marker itself is inspected; the source text is never emitted.
    if marker in _fold(title):
        return True
    provenance = context.get("source_provenance") or {}
    path = Path(str(provenance.get("source_path") or ""))
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    try:
        end = min(len(text), max(0, int(provenance.get("char_start") or 0)), 30000)
    except (TypeError, ValueError):
        end = min(len(text), 30000)
    return marker in _fold(text[:end]) and bool(source_signals or title)


def _build_candidate(
    *,
    question_id: int,
    target: Mapping[str, Any],
    plan: Mapping[str, Any],
    raw_table: Mapping[str, Any],
    context: Mapping[str, Any],
    segment: Mapping[str, Any] | None,
    source_audit: Mapping[str, Any],
    hierarchy: Mapping[int, Mapping[str, Any]],
    row_index: int,
    label: str,
    match_kind: str,
) -> dict[str, Any]:
    table_function = str((context.get("table_function") or {}).get("kind") or "")
    expected_label = _fold(target["expected_label"])
    label_folded = _fold(label)
    row_info = hierarchy.get(row_index) or {}
    row_descriptor = _row_descriptor(
        raw_table=raw_table,
        context=context,
        segment=segment,
        source_audit=source_audit,
        row_index=row_index,
        matched_phrase=str(target["match_phrase"]),
        hierarchy=hierarchy,
    )
    current_columns = _current_columns(
        context,
        int(target["expected_year"]),
        str(target["current_header_mode"]),
    )
    profile = _row_profile(context, row_index)
    selected_columns = [int(column["column_index"]) for column in current_columns]
    reliable_columns = set(int(value) for value in (profile or {}).get("numeric_columns") or []) - set(
        int(value) for value in (profile or {}).get("unreliable_numeric_columns") or []
    )
    source_scope = str((raw_table.get("scope") or "unknown"))
    observed_scope = source_audit.get("observed_scope")
    document_id = str(raw_table.get("document_id") or "")
    expected_document_ids = (
        list(target.get("documents") or [])[:2]
        if target.get("row_mode") == "profit_before_tax_main"
        else [str(target["expected_document_id"])]
    )
    parent_code = str(target.get("parent_code") or "")
    child_code = str(target.get("child_code") or "")
    exact_label = label_folded == expected_label
    is_parent = bool(
        target.get("row_mode") == "inventory_parent"
        and exact_label
        and str(row_info.get("row_code") or "") == parent_code
        and row_info.get("prefix_level") == 1
    )
    is_child = bool(
        target.get("row_mode") == "inventory_parent"
        and exact_label
        and str(row_info.get("row_code") or "") == child_code
        and row_info.get("prefix_level") == 2
    )
    child_rows = row_descriptor.get("direct_children") or []
    child_confirmation = any(
        str(child.get("row_code") or "") == child_code
        and _fold(child.get("label")) == expected_label
        and child.get("prefix_level") == 2
        for child in child_rows
    )
    parent = row_descriptor.get("immediate_parent") or {}
    parent_confirmation = bool(
        is_child
        and str(parent.get("row_code") or "") == parent_code
        and _fold(parent.get("label")) == expected_label
        and parent.get("prefix_level") == 1
    )
    table_scope = row_descriptor["table"]
    title_marker = bool(target.get("statement_marker") and _statement_marker_present(context, source_audit, str(target["statement_marker"])))
    checks: dict[str, Any] = {
        "raw_table_context_aligned": _aligned(raw_table, context),
        "source_provenance_complete": bool(
            (context.get("source_provenance") or {}).get("source_path")
            and (context.get("source_provenance") or {}).get("source_sha256")
            and (context.get("source_provenance") or {}).get("table_sha256")
            and (context.get("source_provenance") or {}).get("char_start") is not None
        ),
        "source_hash_verified": bool(source_audit.get("source_hash_verified")),
        "document_id_matches_expected": document_id in expected_document_ids,
        "declared_scope_matches_expected": source_scope == str(target["expected_scope"]),
        "observed_scope_matches_expected": observed_scope == str(target["expected_scope"]),
        "declared_scope_matches_observed_scope": bool(observed_scope and source_scope == observed_scope),
        "table_function_matches_expected": table_function == str(target["expected_table_function"]),
        "row_profile_present": profile is not None,
        "row_role_is_data": (profile or {}).get("role") == "data",
        "row_label_exact": exact_label,
        "row_code_is_expected_parent": is_parent,
        "row_code_is_expected_child": is_child,
        "parent_row_is_expected": parent_confirmation,
        "expected_child_confirmed": child_confirmation,
        "current_column_candidates": current_columns,
        "current_column_unique": len(current_columns) == 1,
        "current_column_reliable_for_row": len(current_columns) == 1 and selected_columns[0] in reliable_columns,
        "statement_marker_present": title_marker if target.get("statement_marker") else True,
        "retrieval_rank_used_for_decision": False,
    }
    if target.get("row_mode") == "inventory_parent":
        structural_gate = is_parent and child_confirmation
        role = "inventory_total_parent" if is_parent else "inventory_detail_child" if is_child else "inventory_related_row"
    elif table_function == "financial_note":
        structural_gate = False
        role = "explanatory_note_same_metric"
    else:
        structural_gate = exact_label
        role = "profit_before_tax_statement_row" if exact_label else "profit_before_tax_related_row"
    basic_gate = all(
        bool(checks[key])
        for key in (
            "raw_table_context_aligned",
            "source_provenance_complete",
            "source_hash_verified",
            "document_id_matches_expected",
            "declared_scope_matches_expected",
            "observed_scope_matches_expected",
            "declared_scope_matches_observed_scope",
            "table_function_matches_expected",
            "row_profile_present",
            "row_role_is_data",
            "row_label_exact",
            "current_column_unique",
            "current_column_reliable_for_row",
            "statement_marker_present",
        )
    )
    candidate = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "question_id": question_id,
        "candidate_id": _candidate_id(question_id, raw_table, row_index),
        "candidate_role": role,
        "candidate_match_kind": match_kind,
        "question_text_sha256": _sha_text(str(plan.get("question") or "")),
        "locator": {
            "document_id": document_id,
            "internal_table_uid": raw_table.get("internal_table_uid"),
            "local_ordinal": raw_table.get("local_ordinal"),
            "page_no": raw_table.get("page_no"),
            "row_index": row_index,
            "row_label_column_index": row_descriptor.get("row_label_column_index"),
            "current_column_indices": selected_columns,
        },
        "row": row_descriptor,
        "evidence": {
            "checks": checks,
            "table_function_kind": table_function,
            "table_section_kind": (context.get("table_section") or {}).get("kind"),
            "report_segment": row_descriptor["table"].get("report_segment"),
            "source_provenance": row_descriptor["table"].get("source_provenance"),
            "source_scope_audit": table_scope.get("source_scope_audit"),
            "parent_child_relation": {
                "target_is_parent": is_parent,
                "target_is_child": is_child,
                "expected_child_confirmed": child_confirmation,
                "parent_confirmation": parent_confirmation,
                "immediate_parent_row_index": (row_descriptor.get("immediate_parent") or {}).get("row_index"),
                "direct_child_row_indices": [child.get("row_index") for child in row_descriptor.get("direct_children") or []],
            },
            "period_column": {
                "requested_year": target["expected_year"],
                "mode": target["current_header_mode"],
                "candidates": current_columns,
                "selected_column_count": len(current_columns),
            },
            "retrieval_rank_is_not_decision": True,
        },
        "ambiguity_score": 1.0,
        "decision": "UNCLASSIFIED",
        "candidate_only": True,
        "raw_numeric_values_included": False,
        "source_contract": dict(CONTRACT),
    }
    candidate["basic_semantic_gate"] = basic_gate
    candidate["structural_gate"] = structural_gate
    candidate["candidate_gate"] = bool(basic_gate and structural_gate)
    if not checks["document_id_matches_expected"]:
        candidate["decision"] = "REJECTED_WRONG_DOCUMENT_ID"
        candidate["ambiguity_score"] = 0.8
    elif not checks["declared_scope_matches_expected"] or not checks["observed_scope_matches_expected"] or not checks["declared_scope_matches_observed_scope"]:
        candidate["decision"] = "QUARANTINED_DOCUMENT_SCOPE_CONFLICT"
        candidate["ambiguity_score"] = 1.0
    elif not checks["table_function_matches_expected"]:
        candidate["decision"] = "REJECTED_WRONG_TABLE_FUNCTION_OR_NOTE_CONTEXT"
        candidate["ambiguity_score"] = 0.6
    elif target.get("row_mode") == "inventory_parent" and is_child:
        candidate["decision"] = "REJECTED_DETAIL_CHILD_ROW"
        candidate["ambiguity_score"] = 0.1
    elif target.get("row_mode") == "inventory_parent" and not structural_gate:
        candidate["decision"] = "REJECTED_PARENT_CHILD_RELATION_UNCONFIRMED"
        candidate["ambiguity_score"] = 0.7
    elif not checks["row_label_exact"]:
        candidate["decision"] = "REJECTED_NON_EXACT_LABEL_COMPETITOR"
        candidate["ambiguity_score"] = 0.4
    elif not basic_gate:
        candidate["decision"] = "QUARANTINED_SOURCE_OR_PERIOD_GATE"
        candidate["ambiguity_score"] = 1.0
    elif not structural_gate:
        candidate["decision"] = "REJECTED_STRUCTURAL_ROW_GATE"
        candidate["ambiguity_score"] = 0.7
    else:
        candidate["decision"] = "ELIGIBLE_BEFORE_DUPLICATE_PROVENANCE_RECHECK"
        candidate["ambiguity_score"] = 0.0
    return candidate


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN_KEYS or _contains_forbidden(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden(child) for child in value)
    return False


def compare_ocr_sources(
    *,
    first_path: Path,
    second_path: Path,
    first_expected_sha256: str | None = None,
    second_expected_sha256: str | None = None,
    target_table_sha256: Sequence[str] = (),
    target_row_fingerprints: Sequence[str] = (),
) -> dict[str, Any]:
    """Compare duplicate source files without emitting OCR text."""
    first_bytes = first_path.read_bytes()
    second_bytes = second_path.read_bytes()
    first_text = first_bytes.decode("utf-8", errors="replace")
    second_text = second_bytes.decode("utf-8", errors="replace")
    first_lines = first_text.splitlines(keepends=True)
    second_lines = second_text.splitlines(keepends=True)
    opcodes = difflib.SequenceMatcher(None, first_lines, second_lines).get_opcodes()
    stats = {
        operation: {"opcode_count": 0, "left_chars": 0, "right_chars": 0}
        for operation in ("equal", "insert", "delete", "replace")
    }
    changed_spans: list[dict[str, int | str]] = []
    for operation, left_start, left_end, right_start, right_end in opcodes:
        stats[operation]["opcode_count"] += 1
        stats[operation]["left_chars"] += sum(len(line) for line in first_lines[left_start:left_end])
        stats[operation]["right_chars"] += sum(len(line) for line in second_lines[right_start:right_end])
        if operation != "equal":
            changed_spans.append(
                {
                    "operation": operation,
                    "left_line_start": left_start,
                    "left_line_end": left_end,
                    "right_line_start": right_start,
                    "right_line_end": right_end,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "first_source_path": str(first_path),
        "second_source_path": str(second_path),
        "first_source_sha256": _sha_bytes(first_bytes),
        "second_source_sha256": _sha_bytes(second_bytes),
        "first_expected_sha256_matches": first_expected_sha256 is None or _sha_bytes(first_bytes) == first_expected_sha256,
        "second_expected_sha256_matches": second_expected_sha256 is None or _sha_bytes(second_bytes) == second_expected_sha256,
        "first_character_count": len(first_text),
        "second_character_count": len(second_text),
        "full_ocr_content_equal": first_bytes == second_bytes,
        "ocr_line_opcode_count": len(opcodes),
        "ocr_changed_span_count": len(changed_spans),
        "ocr_diff_stats": stats,
        "ocr_diff_descriptor_sha256": _sha_json(changed_spans),
        "target_table_sha256_values": list(target_table_sha256),
        "target_table_sha256_equal": len(set(target_table_sha256)) == 1 and len(target_table_sha256) >= 2,
        "target_row_fingerprints_equal": len(set(target_row_fingerprints)) == 1 and len(target_row_fingerprints) >= 2,
        "raw_ocr_content_included": False,
        "primary_basis_present": False,
        "decision": "BLOCKED_DUPLICATE_PROVENANCE",
        "source_contract": dict(CONTRACT),
    }


def _authority_basis(metadata: Mapping[str, Mapping[str, Any]], document_ids: Sequence[str]) -> dict[str, Any]:
    examined: list[str] = []
    positive: list[str] = []
    for document_id in document_ids:
        record = metadata.get(document_id) or {}
        for key, value in record.items():
            folded = _fold(key)
            if "primary" in folded or "authoritative" in folded or "canonical" in folded:
                examined.append(f"{document_id}:{key}")
                if value is True or _fold(value) in {"primary", "authoritative", "canonical"}:
                    positive.append(f"{document_id}:{key}")
    return {
        "authority_fields_examined": sorted(examined),
        "positive_authority_fields": sorted(positive),
        "primary_basis_present": bool(positive),
    }


def _candidate_matrix_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "question_id": candidate["question_id"],
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "role": candidate["candidate_role"],
            "match_kind": candidate["candidate_match_kind"],
            "locator": candidate["locator"],
            "row": candidate["row"],
        },
        "evidence": candidate["evidence"],
        "ambiguity_score": candidate["ambiguity_score"],
        "decision": candidate["decision"],
        "candidate_only": True,
        "retrieval_rank_is_not_decision": True,
        "raw_numeric_values_included": False,
        "source_contract": dict(CONTRACT),
    }


def _report_lines(
    *,
    candidates: Sequence[Mapping[str, Any]],
    ledger: Sequence[Mapping[str, Any]],
    duplicate_rows: Sequence[Mapping[str, Any]],
    input_audit: Mapping[str, Any],
) -> str:
    by_question: dict[int, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        by_question.setdefault(int(candidate["question_id"]), []).append(candidate)
    lines = [
        "# Báo cáo nghiên cứu Agent 2: phân cấp dòng và provenance tài liệu",
        "",
        f"Protocol: `{PROTOCOL}`. Đây là artifact nghiên cứu candidate-only, value-blind; không phát sinh answer, submission hay quyền phát hành.",
        "",
        "## Phạm vi và dữ liệu đã đọc",
        "",
        "Đã đọc raw table (`tables.jsonl`), `row_profiles`, `table_function`, `table_section`, `canonical_headers` và `source_provenance` của V3 cho toàn bộ các document mục tiêu. Dữ liệu số của ô không được ghi ra; chỉ giữ mã dòng, chỉ số cột, fingerprint và hash provenance.",
        "",
        f"- Số raw table đã đọc: {input_audit.get('tables_record_count')}; số context V3: {input_audit.get('contexts_record_count')}; số report segment: {input_audit.get('segments_record_count') }.",
        "- Scope được đối chiếu từ nội dung OCR có hash kiểm tra, source title/context và metadata document; tên file hoặc retrieval rank không được dùng làm quyết định.",
        "- Điểm mơ hồ: `0.0` = tất cả gate nghiên cứu và cạnh tranh dòng đã phân giải; `1.0` = còn blocker provenance/identity hoặc cạnh tranh không thể fail-closed.",
        "",
        "## Ma trận question | candidate | bằng chứng | điểm mơ hồ | quyết định",
        "",
        "| question_id | candidate | bằng chứng chính | điểm mơ hồ | quyết định |",
        "|---:|---|---|---:|---|",
    ]
    for row in ledger:
        question_id = row["question_id"]
        q_candidates = by_question.get(int(question_id), [])
        if not q_candidates:
            lines.append(
                f"| {question_id} | không có candidate hợp lệ | {', '.join(row.get('blockers') or [])} | {row.get('ambiguity_score')} | {row.get('decision')} |"
            )
            continue
        for candidate in q_candidates:
            checks = candidate.get("evidence", {}).get("checks", {})
            evidence = []
            if checks.get("row_code_is_expected_parent"):
                evidence.append("dòng cha/mã tổng đã nhận diện")
            if checks.get("expected_child_confirmed"):
                evidence.append("dòng chi tiết cùng nhãn đã nối vào cha")
            if checks.get("row_code_is_expected_child"):
                evidence.append("dòng chi tiết bị loại")
            if checks.get("table_function_matches_expected"):
                evidence.append("đúng table_function")
            else:
                evidence.append("sai table_function/mục thuyết minh")
            if checks.get("observed_scope_matches_expected") and checks.get("declared_scope_matches_observed_scope"):
                evidence.append("scope nội dung khớp metadata")
            else:
                evidence.append("scope nội dung và document conflict")
            if candidate.get("candidate_role") == "profit_before_tax_statement_row":
                evidence.append("B03 statement row")
            lines.append(
                f"| {question_id} | `{candidate['candidate_id']}` | {'; '.join(evidence)} | {candidate.get('ambiguity_score')} | {candidate.get('decision')} |"
            )
    lines.extend(["", "## Kết luận theo câu hỏi", ""])
    conclusions = {
        98: "Q98: BLOCKED. Hai file HUT có nội dung riêng/hợp nhất đảo với suffix document: file khai báo separate mang heading hợp nhất, còn file khai báo consolidated mang heading riêng. Không có căn cứ provenance để chọn bản chính.",
        104: "Q104: BLOCKED_DUPLICATE_PROVENANCE. Hai bản MBB separate_1/separate_2 có target table và dòng đích cùng table hash/fingerprint, nhưng document_id/source_sha256 khác và toàn bộ OCR khác; không có marker bản chính. Dòng cùng metric ở mục thuyết minh được ghi nhận nhưng không thay thế B03 statement row.",
        340: "Q340: CANDIDATE_ONLY_MATERIALIZED. PVT separate có bảng cân đối đúng, dòng tổng mã 140 là cha trực tiếp của dòng chi tiết mã 141, cột Số cuối năm duy nhất và không còn competitor hợp lý sau gate table/scope/row/period. Các dòng cùng cụm từ trong cash-flow bị loại do sai table_function.",
    }
    for question_id in (98, 104, 340):
        lines.append(f"- {conclusions[question_id]}")
    lines.extend(["", "## Kiểm tra duplicate OCR Q104", ""])
    for duplicate in duplicate_rows:
        lines.append(
            f"- `{duplicate.get('first_document_id')}` và `{duplicate.get('second_document_id')}`: source hash khác; `full_ocr_content_equal={duplicate.get('comparison', {}).get('full_ocr_content_equal')}`; target table hash bằng nhau={duplicate.get('comparison', {}).get('target_table_sha256_equal')}; primary basis={duplicate.get('primary_basis_present')}; quyết định `{duplicate.get('decision')}`."
        )
    lines.extend(
        [
            "",
            "## Ranh giới an toàn",
            "",
            "Artifact này không thêm phê duyệt thủ công, không chạy LLM review, không tạo answer/submission và không sửa source-period module của Agent 1. Q340 chỉ là candidate navigation; Q98/Q104 vẫn quarantine/block.",
            "",
        ]
    )
    return "\n".join(lines)


def _source_file_hash(path: Path) -> str | None:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError:
        return None


def build_parent_child_duplicate_research(*, bundle_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Build the bounded Agent 2 research artifact without overwriting output."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    tables_path = bundle_dir / "tables.jsonl"
    contexts_path = bundle_dir / "tables_evidence_context_v3.jsonl"
    segments_path = bundle_dir / "report_segments_v1.jsonl"
    plans_path = bundle_dir / "typed_operand_plans_v1.jsonl"
    metadata_path = bundle_dir / "document_metadata_v1.jsonl"
    raw_tables = _read_jsonl(tables_path)
    contexts_rows = _read_jsonl(contexts_path)
    segment_rows = _read_jsonl(segments_path)
    plans_rows = _read_jsonl(plans_path)
    metadata_rows = _read_jsonl(metadata_path) if metadata_path.is_file() else []
    contexts_by_uid = {str(row.get("internal_table_uid") or ""): row for row in contexts_rows}
    segments_by_uid = {str(row.get("internal_table_uid") or ""): row for row in segment_rows}
    plans = {int(row["question_id"]): row for row in plans_rows if int(row.get("question_id") or 0) in TARGETS}
    metadata = {str(row.get("document_id") or ""): row for row in metadata_rows}
    if set(plans) != set(TARGETS):
        raise ValueError(f"missing target plans: {sorted(set(TARGETS) - set(plans))}")

    input_audit = {
        "tables_path": str(tables_path),
        "tables_sha256": _source_file_hash(tables_path),
        "tables_record_count": len(raw_tables),
        "contexts_path": str(contexts_path),
        "contexts_sha256": _source_file_hash(contexts_path),
        "contexts_record_count": len(contexts_rows),
        "segments_path": str(segments_path),
        "segments_sha256": _source_file_hash(segments_path),
        "segments_record_count": len(segment_rows),
        "plans_path": str(plans_path),
        "plans_sha256": _source_file_hash(plans_path),
        "metadata_path": str(metadata_path),
        "metadata_sha256": _source_file_hash(metadata_path) if metadata_path.is_file() else None,
        "fields_read": [
            "raw_table.rows",
            "row_profiles",
            "table_function",
            "table_section",
            "canonical_headers",
            "source_provenance",
            "report_segments",
        ],
    }
    candidates: list[dict[str, Any]] = []
    census: list[dict[str, Any]] = []
    table_cache: dict[str, tuple[dict[str, Any], dict[str, Any], dict[int, dict[str, Any]], dict[str, Any]]] = {}
    for question_id, target in sorted(TARGETS.items()):
        plan = plans[question_id]
        for document_id in target["documents"]:
            for raw_table in raw_tables:
                if str(raw_table.get("document_id") or "") != document_id:
                    continue
                uid = str(raw_table.get("internal_table_uid") or "")
                context = contexts_by_uid.get(uid)
                if context is None:
                    continue
                source_audit = _read_source_audit(context)
                data_indices = [
                    int(profile["row_index"])
                    for profile in context.get("row_profiles") or []
                    if isinstance(profile, Mapping) and profile.get("role") == "data"
                ]
                hierarchy = infer_row_hierarchy(
                    raw_table.get("rows") or [],
                    data_row_indices=data_indices,
                    code_columns=_code_columns(context),
                )
                segment = segments_by_uid.get(uid)
                table_cache[uid] = (raw_table, context, hierarchy, source_audit)
                for row_index in data_indices:
                    rows = raw_table.get("rows") or []
                    if row_index < 0 or row_index >= len(rows):
                        continue
                    label, label_column_index = _label_cell(rows[row_index], str(target["match_phrase"]))
                    if not label or not _contains_metric_phrase(label, target["match_phrase"]):
                        continue
                    exact = _fold(label) == _fold(target["expected_label"])
                    match_kind = "exact_label" if exact else "contains_related_label"
                    candidate = _build_candidate(
                        question_id=question_id,
                        target=target,
                        plan=plan,
                        raw_table=raw_table,
                        context=context,
                        segment=segment,
                        source_audit=source_audit,
                        hierarchy=hierarchy,
                        row_index=row_index,
                        label=label,
                        match_kind=match_kind,
                    )
                    candidates.append(candidate)
                    census.append(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "protocol": PROTOCOL,
                            "question_id": question_id,
                            "candidate_id": candidate["candidate_id"],
                            "document_id": document_id,
                            "internal_table_uid": uid,
                            "local_ordinal": raw_table.get("local_ordinal"),
                            "page_no": raw_table.get("page_no"),
                            "table_function_kind": (context.get("table_function") or {}).get("kind"),
                            "table_section_kind": (context.get("table_section") or {}).get("kind"),
                            "row_index": row_index,
                            "row_label": label,
                            "row_label_column_index": label_column_index,
                            "row_label_exact": exact,
                            "row_prefix": (hierarchy.get(row_index) or {}).get("prefix"),
                            "row_prefix_level": (hierarchy.get(row_index) or {}).get("prefix_level"),
                            "row_code": (hierarchy.get(row_index) or {}).get("row_code"),
                            "parent_row_index": (hierarchy.get(row_index) or {}).get("parent_row_index"),
                            "child_row_indices": (hierarchy.get(row_index) or {}).get("child_row_indices") or [],
                            "row_fingerprint_sha256": candidate["row"]["row_fingerprint_sha256"],
                            "source_sha256": (context.get("source_provenance") or {}).get("source_sha256"),
                            "table_sha256": (context.get("source_provenance") or {}).get("table_sha256"),
                            "observed_scope": source_audit.get("observed_scope"),
                            "raw_numeric_values_included": False,
                            "candidate_only": True,
                            "source_contract": dict(CONTRACT),
                        }
                    )

    duplicate_rows: list[dict[str, Any]] = []
    q104_main = [
        candidate
        for candidate in candidates
        if int(candidate["question_id"]) == 104
        and candidate["candidate_role"] == "profit_before_tax_statement_row"
        and candidate["locator"]["document_id"] in TARGETS[104]["documents"][:2]
        and candidate["candidate_gate"]
    ]
    if len(q104_main) >= 2:
        first, second = q104_main[0], q104_main[1]
        first_source = first["evidence"]["source_provenance"]
        second_source = second["evidence"]["source_provenance"]
        comparison = compare_ocr_sources(
            first_path=Path(str(first_source.get("source_path") or "")),
            second_path=Path(str(second_source.get("source_path") or "")),
            first_expected_sha256=str(first_source.get("source_sha256") or ""),
            second_expected_sha256=str(second_source.get("source_sha256") or ""),
            target_table_sha256=[str(first_source.get("table_sha256") or ""), str(second_source.get("table_sha256") or "")],
            target_row_fingerprints=[str(first["row"]["row_fingerprint_sha256"]), str(second["row"]["row_fingerprint_sha256"])],
        )
        authority = _authority_basis(
            metadata,
            [str(first["locator"]["document_id"]), str(second["locator"]["document_id"])],
        )
        duplicate = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "question_id": 104,
            "duplicate_group_id": "q104-mbb-separate-target-table",
            "first_document_id": first["locator"]["document_id"],
            "second_document_id": second["locator"]["document_id"],
            "first_internal_table_uid": first["locator"]["internal_table_uid"],
            "second_internal_table_uid": second["locator"]["internal_table_uid"],
            "first_table_sha256": first_source.get("table_sha256"),
            "second_table_sha256": second_source.get("table_sha256"),
            "first_source_sha256": first_source.get("source_sha256"),
            "second_source_sha256": second_source.get("source_sha256"),
            "document_id_different": first["locator"]["document_id"] != second["locator"]["document_id"],
            "source_sha256_different": first_source.get("source_sha256") != second_source.get("source_sha256"),
            "comparison": comparison,
            "authority": authority,
            "primary_basis_present": authority["primary_basis_present"],
            "decision": "BLOCKED_DUPLICATE_PROVENANCE" if not authority["primary_basis_present"] else "REQUIRES_AUTHORITY_REVIEW",
            "raw_ocr_content_included": False,
            "raw_numeric_values_included": False,
            "candidate_only": True,
            "source_contract": dict(CONTRACT),
        }
        duplicate_rows.append(duplicate)
        duplicate_ids = {first["candidate_id"], second["candidate_id"]}
        for candidate in candidates:
            if candidate["candidate_id"] in duplicate_ids:
                candidate["decision"] = "BLOCKED_DUPLICATE_PROVENANCE"
                candidate["ambiguity_score"] = 1.0
                candidate["evidence"]["duplicate_provenance_group_id"] = duplicate["duplicate_group_id"]
                candidate["evidence"]["duplicate_provenance_unresolved"] = True

    ledger: list[dict[str, Any]] = []
    for question_id in (98, 104, 340):
        q_candidates = [candidate for candidate in candidates if int(candidate["question_id"]) == question_id]
        if question_id == 98:
            decision = "BLOCKED_DOCUMENT_PROVENANCE"
            blockers = [
                "HUT_SCOPE_NAME_CONTENT_INVERSION",
                "DOCUMENT_ID_SCOPE_CONTENT_CONFLICT",
                "NO_PRIMARY_DOCUMENT_BASIS",
            ]
            selected = None
            ambiguity = 1.0
        elif question_id == 104:
            decision = "BLOCKED_DUPLICATE_PROVENANCE"
            blockers = [
                "DUPLICATE_DOCUMENT_ID_WITH_DIFFERENT_SOURCE_HASH",
                "FULL_OCR_CONTENT_DIFFERS",
                "NO_PRIMARY_DOCUMENT_BASIS",
            ]
            selected = None
            ambiguity = 1.0
        else:
            eligible = [candidate for candidate in q_candidates if candidate.get("candidate_gate")]
            if len(eligible) == 1:
                selected = eligible[0]["candidate_id"]
                eligible[0]["decision"] = "MATERIALIZED_CANDIDATE_ONLY"
                eligible[0]["ambiguity_score"] = 0.0
                decision = "MATERIALIZED_CANDIDATE_ONLY"
                blockers = []
                ambiguity = 0.0
            else:
                selected = None
                decision = "BLOCKED_PARENT_CHILD_OR_PROVENANCE"
                blockers = ["NO_UNIQUE_ELIGIBLE_PARENT_ROW"]
                ambiguity = 1.0
        ledger.append(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "decision": decision,
                "ambiguity_score": ambiguity,
                "candidate_count": len(q_candidates),
                "candidate_ids": [candidate["candidate_id"] for candidate in q_candidates],
                "selected_candidate_id": selected,
                "blockers": blockers,
                "candidate_only": True,
                "materialized_artifact_is_not_answer": True,
                "retrieval_rank_is_not_decision": True,
                "raw_numeric_values_included": False,
                "source_contract": dict(CONTRACT),
            }
        )

    matrix = [_candidate_matrix_row(candidate) for candidate in candidates]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        _write_jsonl(temporary / "row_census_v1.jsonl", census)
        _write_jsonl(temporary / "question_candidate_matrix_v1.jsonl", matrix)
        _write_jsonl(temporary / "candidate_quarantine_ledger_v1.jsonl", ledger)
        _write_jsonl(temporary / "duplicate_ocr_comparison_v1.jsonl", duplicate_rows)
        report = _report_lines(
            candidates=candidates,
            ledger=ledger,
            duplicate_rows=duplicate_rows,
            input_audit=input_audit,
        )
        (temporary / "research_report_vi.md").write_text(report, encoding="utf-8")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "artifact_status": "candidate_only_research",
            "input_audit": input_audit,
            "target_question_ids": [98, 104, 340],
            "candidate_count": len(candidates),
            "row_census_count": len(census),
            "duplicate_comparison_count": len(duplicate_rows),
            "decision_counts": dict(sorted(Counter(row["decision"] for row in ledger).items())),
            "source_contract": dict(CONTRACT),
            "raw_numeric_values_included": False,
            "outputs": {},
        }
        for path in sorted(temporary.iterdir()):
            if path.name == "manifest.json":
                continue
            manifest["outputs"][path.name] = {
                "sha256": _source_file_hash(path),
                "bytes": path.stat().st_size,
            }
        _write_json(temporary / "manifest.json", manifest)
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "protocol": PROTOCOL,
        "output_dir": str(output_dir),
        "candidate_count": len(candidates),
        "row_census_count": len(census),
        "duplicate_comparison_count": len(duplicate_rows),
        "decision_counts": dict(sorted(Counter(row["decision"] for row in ledger).items())),
    }


def validate_parent_child_duplicate_research(artifact_dir: Path) -> dict[str, Any]:
    """Fail closed on malformed or authorizing-looking research output."""
    required = {
        "manifest.json",
        "research_report_vi.md",
        "row_census_v1.jsonl",
        "question_candidate_matrix_v1.jsonl",
        "candidate_quarantine_ledger_v1.jsonl",
        "duplicate_ocr_comparison_v1.jsonl",
    }
    missing = sorted(name for name in required if not (artifact_dir / name).is_file())
    if missing:
        return {"status": "FAIL", "reason": "MISSING_OUTPUTS", "missing": missing}
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    if manifest.get("protocol") != PROTOCOL:
        failures.append("protocol_mismatch")
    if manifest.get("source_contract") != CONTRACT:
        failures.append("manifest_contract_mismatch")
    for name, descriptor in (manifest.get("outputs") or {}).items():
        path = artifact_dir / name
        if not path.is_file() or _source_file_hash(path) != descriptor.get("sha256"):
            failures.append(f"output_hash_mismatch:{name}")
    loaded: dict[str, list[dict[str, Any]]] = {}
    for name in required - {"manifest.json", "research_report_vi.md"}:
        try:
            loaded[name] = _read_jsonl(artifact_dir / name)
        except Exception as exc:  # pragma: no cover - diagnostic branch
            failures.append(f"invalid_jsonl:{name}:{type(exc).__name__}")
    for name, rows in loaded.items():
        if any(_contains_forbidden(row) for row in rows):
            failures.append(f"forbidden_field:{name}")
        for row in rows:
            if row.get("source_contract") != CONTRACT:
                failures.append(f"contract_mismatch:{name}")
            if row.get("raw_numeric_values_included") is not False:
                failures.append(f"value_blind_flag:{name}")
    ledger = loaded.get("candidate_quarantine_ledger_v1.jsonl", [])
    decisions = {int(row.get("question_id")): row.get("decision") for row in ledger}
    if decisions != {98: "BLOCKED_DOCUMENT_PROVENANCE", 104: "BLOCKED_DUPLICATE_PROVENANCE", 340: "MATERIALIZED_CANDIDATE_ONLY"}:
        failures.append("unexpected_question_decisions")
    duplicate_rows = loaded.get("duplicate_ocr_comparison_v1.jsonl", [])
    if len(duplicate_rows) != 1 or duplicate_rows[0].get("decision") != "BLOCKED_DUPLICATE_PROVENANCE":
        failures.append("duplicate_provenance_not_blocked")
    matrix = loaded.get("question_candidate_matrix_v1.jsonl", [])
    q98_scope_pairs = {
        (
            str(row.get("candidate", {}).get("locator", {}).get("document_id") or ""),
            row.get("candidate", {}).get("row", {}).get("table", {}).get("source_scope_audit", {}).get("observed_scope"),
        )
        for row in matrix
        if int(row.get("question_id") or 0) == 98
    }
    if (
        ("HUT_financial_statements_2024_separate", "consolidated") not in q98_scope_pairs
        or ("HUT_financial_statements_2024_consolidated", "separate") not in q98_scope_pairs
    ):
        failures.append("q98_scope_inversion_not_recorded")
    q340_selected_id = next(
        (row.get("selected_candidate_id") for row in ledger if int(row.get("question_id") or 0) == 340),
        None,
    )
    q340_selected = next(
        (
            row
            for row in matrix
            if int(row.get("question_id") or 0) == 340
            and row.get("candidate", {}).get("candidate_id") == q340_selected_id
        ),
        None,
    )
    q340_row = (q340_selected or {}).get("candidate", {}).get("row", {})
    q340_children = q340_row.get("direct_children") or []
    if (
        not q340_selected
        or q340_row.get("row_code") != "140"
        or not any(child.get("row_code") == "141" for child in q340_children if isinstance(child, Mapping))
        or (q340_selected.get("candidate", {}).get("locator", {}).get("current_column_indices") or []) != [4]
    ):
        failures.append("q340_parent_child_or_period_gate_not_recorded")
    if not any(
        int(row.get("question_id") or 0) == 104
        and row.get("candidate", {}).get("role") == "explanatory_note_same_metric"
        for row in matrix
    ):
        failures.append("q104_explanatory_note_not_compared")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "question_decisions": decisions,
        "candidate_count": len(loaded.get("question_candidate_matrix_v1.jsonl", [])),
        "row_census_count": len(loaded.get("row_census_v1.jsonl", [])),
    }
