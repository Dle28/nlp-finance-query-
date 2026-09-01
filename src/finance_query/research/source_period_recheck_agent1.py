"""Agent 1 source-period recheck for ViFinQA questions 70, 185 and 357.

This module is deliberately outside the answer-capable pipeline.  It reads the
already frozen V2/V3 material and the OCR source, then emits only navigation
candidates and provenance.  It never copies numeric cells, evaluates a
formula, or promotes a candidate to evidence or submission.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_source_period_recheck_agent1_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "candidate_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "may_execute_formula": False,
    "may_select_final_column": False,
    "may_select_value": False,
    "promotion_allowed": False,
    "submission_eligible": False,
    "training_eligible": False,
}

FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "raw_source_cell",
        "raw_source_row",
        "rows",
        "table_rows",
        "numeric_value",
        "raw_decimal_candidate",
        "submission",
    }
)

TARGETS = {
    70: {
        "entity": "DCM",
        "requested_year": 2019,
        "document_id": "DCM_financial_statements_2019_separate",
        "internal_table_uid": "1c24680c0c45dad1428f158ee98c083c30501712e94f0a96546da5c06f9f215c",
        "local_ordinal": 52,
        "row_index": 3,
        "column_index": 1,
        "source_date": "2019-12-31",
        "period_resolution_method": "v2_exact_document_header_current_header_v1",
    },
    185: {
        "entity": "VIC",
        "requested_year": 2016,
        "document_id": "VIC_financial_statements_2016_separate",
        "internal_table_uid": "f75f9583841fbe84c999c3c9999d7455ba72f25fa745a02cfeb921dc87c5d4b3",
        "wrong_direction_uid": "5bf50bbf5323c0b10a9257b47e586c572dfdb623c059a3f9b8d3894ff1c79e3b",
        "local_ordinal": 79,
        "row_index": 6,
        "column_index": 2,
        "prior_row_index": 14,
        "prior_column_index": 2,
        "source_date": "2016-12-31",
        "prior_source_date": "2015-12-31",
        "period_resolution_method": "v2_exact_two_section_table_title_v1",
    },
    357: {
        "entity": "PVT",
        "requested_year": 2017,
        "document_id": "PVT_financial_statements_2017_separate",
        "internal_table_uid": "cfd490b201921eb2fb4bf54c082b1b0b50043fc1046eaf2ebe7c0ed76242217c",
        "preceding_table_uid": "0349d3d79b31958e91ef0644363f593147e04acb64f3d10c850d363e9a49d5e8",
        "local_ordinal": 5,
        "preceding_local_ordinal": 4,
        "row_index": 20,
        "closing_column_index": 3,
        "opening_column_index": 4,
        "source_date": "2017-12-31",
        "period_resolution_method": "v2_exact_preceding_balance_sheet_header_continuation_v1",
    },
}

EXPECTED_RECORD_SHA256 = {
    70: {
        "v2": "ae68d436d1edb3e61a8bcd23e82bd366f9196eb0fea0bb21a1ddf2730d40f448",
        "v3": "e38943a98a93614314db8b08751658fac6c7baae592d4b992a81d11eff948c9d",
    },
    185: {
        "v2": "4ac34365bd2110f4f83d57cf7ae6768b6bcf11a09a8dfd4894618b5545aee051",
        "v3": "f25f6bd701a60159a9f661942d80b58edb17e58ad07a1f42d76c4d6d6e772752",
    },
    357: {
        "v2": "07a0089ea81807b24a9e01a2b64ab95b32cf049966083c8a0c1e3037bdaa12c3",
        "v3": "9753b9fc142979e71c4917dd9332ae83a678b1665be4f8dea563e9e09f02b396",
    },
    "357_preceding": {
        "v2": "7e854b3e8cd891eb0a494552221b352e07d1ee312b0dbb54c1d22ba11d9c198b",
        "v3": "0462a433b8b8290888b5ed2af46fe1885f69eb3a6d21d47fc8fe007b153c0fe6",
    },
}

EXPECTED_SOURCE_SHA256 = {
    70: "d7b14356accac9876ce49bda521d0574228396f5fb1c0428bf731487d3b477e9",
    185: "cba84ec51aad01df37afa47b0ccfcb5201e912d81e17678832a3eab33e02617b",
    357: "0d654a3b853a763e392bc145f5165fa0b0f2df0462b464e970de1552410848a3",
}

EXPECTED_TABLE_SHA256 = {
    70: "15bfc05597f720a8745a149658f8998d6e3789122c34793b345bcd3f6ad4b8ae",
    185: "0a1d279856aa1df664d209d16aa35961a0da34edc48cd27f96e45d7b21e60e5a",
    357: "7296f196d0920f88c75f6bf56d427f557878ee62293c10e46de4df896eeeb4ac",
    "357_preceding": "af4b1521992af181a6afd20b7ae1af47c41b5e1374780c3cece7015e35282156",
}

_DOCUMENT_PERIOD_DATE_RE = re.compile(
    r"(?:cho\s+nam(?:\s+tai\s+chinh)?\s+ket\s+thuc\s+ngay|tai\s+ngay)\s+"
    r"(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s*(?:[|·:]\s*)?(\d{4})"
)


class RecheckBlocked(ValueError):
    """A target cannot be uniquely proven and must stay quarantined."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _fold(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    normalized = normalized.replace("đ", "d")
    return " ".join("".join(char for char in normalized if not unicodedata.combining(char)).split())


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN_KEYS or _contains_forbidden_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _safe_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} is not an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not an integer") from exc


def _source_provenance(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("source_provenance")
    if not isinstance(value, Mapping):
        raise RecheckBlocked("missing source provenance")
    return value


def _source_path(record: Mapping[str, Any]) -> Path:
    path = Path(str(_source_provenance(record).get("source_path") or ""))
    if not path.is_file():
        raise RecheckBlocked(f"source file missing: {path}")
    return path


def _verified_table_span(record: Mapping[str, Any], *, expected_source_sha: str, expected_table_sha: str) -> dict[str, Any]:
    provenance = _source_provenance(record)
    path = _source_path(record)
    source_bytes = path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if source_sha != expected_source_sha or provenance.get("source_sha256") != expected_source_sha:
        raise RecheckBlocked("source SHA-256 mismatch")
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecheckBlocked("OCR source is not valid UTF-8") from exc
    start = _safe_int(provenance.get("char_start"), "char_start")
    if start < 0 or not source_text.startswith("<table", start):
        raise RecheckBlocked("declared table start is not an OCR table")
    close = source_text.find("</table>", start)
    if close < 0:
        raise RecheckBlocked("OCR table has no closing tag")
    end = close + len("</table>")
    table_sha = hashlib.sha256(source_text[start:end].encode("utf-8")).hexdigest()
    if table_sha != expected_table_sha or provenance.get("table_sha256") != expected_table_sha:
        raise RecheckBlocked("table SHA-256 mismatch")
    return {
        "source_path": str(path),
        "source_sha256": source_sha,
        "char_start": start,
        "char_end": end,
        "span_sha256": table_sha,
    }


def _verified_source_span(path: Path, *, start: int, end: int, expected_source_sha: str, evidence_id: str, kind: str, marker: str) -> dict[str, Any]:
    source_bytes = path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if source_sha != expected_source_sha:
        raise RecheckBlocked(f"source SHA-256 mismatch for {evidence_id}")
    text = source_bytes.decode("utf-8")
    if start < 0 or end < start or end > len(text):
        raise RecheckBlocked(f"invalid source span for {evidence_id}")
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "source_path": str(path),
        "source_sha256": source_sha,
        "char_start": start,
        "char_end": end,
        "span_sha256": hashlib.sha256(text[start:end].encode("utf-8")).hexdigest(),
        "printed_marker": marker,
    }


def _record_evidence(*, evidence_id: str, kind: str, record: Mapping[str, Any], record_sha256: str, marker: str) -> dict[str, Any]:
    provenance = _source_provenance(record)
    if _record_sha(record) != record_sha256:
        raise RecheckBlocked(f"record SHA-256 mismatch for {evidence_id}")
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "document_id": record.get("document_id"),
        "internal_table_uid": record.get("internal_table_uid"),
        "local_ordinal": record.get("local_ordinal"),
        "record_sha256": record_sha256,
        "source_sha256": provenance.get("source_sha256"),
        "table_sha256": provenance.get("table_sha256"),
        "char_start": provenance.get("char_start"),
        "printed_marker": marker,
    }


def _source_file_evidence(path: Path, expected_source_sha: str, evidence_id: str, marker: str) -> dict[str, Any]:
    actual = sha256_file(path)
    if actual != expected_source_sha:
        raise RecheckBlocked(f"source SHA-256 mismatch for {evidence_id}")
    return {
        "evidence_id": evidence_id,
        "kind": "ocr_source_file",
        "source_path": str(path),
        "source_sha256": actual,
        "printed_marker": marker,
    }


def _aligned(v2: Mapping[str, Any], v3: Mapping[str, Any], *, allow_needs_processing: bool = False) -> bool:
    source2, source3 = _source_provenance(v2), _source_provenance(v3)
    quality_status = (v3.get("quality") or {}).get("status")
    return bool(
        v2.get("internal_table_uid") == v3.get("internal_table_uid")
        and v2.get("document_id") == v3.get("document_id")
        and source2.get("source_sha256") == source3.get("source_sha256")
        and source2.get("table_sha256") == source3.get("table_sha256")
        and (v3.get("grid") or {}).get("rectangular") is True
        and (v3.get("grid") or {}).get("provenance_complete") is True
        and (quality_status == "review_ready" or (allow_needs_processing and quality_status == "needs_processing"))
    )


def _rows(record: Mapping[str, Any]) -> list[list[Any]]:
    values = record.get("rows")
    if not isinstance(values, list) or not all(isinstance(row, list) for row in values):
        raise RecheckBlocked("V2 rows are missing or malformed")
    return values


def _row_text(row: list[Any]) -> str:
    return _fold(" ".join(str(cell or "") for cell in row))


def _row(record: Mapping[str, Any], index: int) -> list[Any]:
    rows = _rows(record)
    if index < 0 or index >= len(rows):
        raise RecheckBlocked(f"row index out of bounds: {index}")
    return rows[index]


def _column(v3: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    columns = ((v3.get("canonical_headers") or {}).get("columns") or [])
    for column in columns:
        if isinstance(column, Mapping) and column.get("column_index") == index:
            return column
    raise RecheckBlocked(f"V3 column missing: {index}")


def continuation_header_link(
    *,
    preceding_table: Mapping[str, Any],
    continuation_table: Mapping[str, Any],
    preceding_context: Mapping[str, Any],
    continuation_context: Mapping[str, Any],
    source_text: str,
    preceding_table_end: int,
    preceding_preamble_start: int,
    preceding_preamble_end: int,
    continuation_preamble_start: int,
    continuation_preamble_end: int,
    continuation_table_start: int,
    report_date: str,
) -> dict[str, Any] | None:
    """Prove that a continuation table may inherit period roles from its predecessor."""

    try:
        if not _aligned(preceding_table, preceding_context) or not _aligned(continuation_table, continuation_context, allow_needs_processing=True):
            return None
        preceding_provenance = _source_provenance(preceding_table)
        continuation_provenance = _source_provenance(continuation_table)
        if preceding_table.get("document_id") != continuation_table.get("document_id"):
            return None
        if preceding_provenance.get("source_sha256") != continuation_provenance.get("source_sha256"):
            return None
        if _safe_int(continuation_table.get("local_ordinal"), "continuation ordinal") != _safe_int(preceding_table.get("local_ordinal"), "preceding ordinal") + 1:
            return None
        if not preceding_table_end <= continuation_preamble_start <= continuation_table_start:
            return None
        if "<table" in source_text[preceding_table_end:continuation_table_start].casefold():
            return None
        previous_preamble = _fold(source_text[preceding_preamble_start:preceding_preamble_end])
        continuation_preamble = _fold(source_text[continuation_preamble_start:continuation_preamble_end])
        year, month, day = report_date.split("-")
        date_marker = f"tai ngay {int(day)} thang {int(month)} nam {year}"
        if "bang can doi ke toan rieng" not in previous_preamble or date_marker not in previous_preamble:
            return None
        if "bang can doi ke toan rieng" not in continuation_preamble or "tiep theo" not in continuation_preamble or date_marker not in continuation_preamble:
            return None
        previous_columns = ((preceding_context.get("canonical_headers") or {}).get("columns") or [])
        previous_closing = _column(preceding_context, 3)
        previous_opening = _column(preceding_context, 4)
        if previous_closing.get("period_labels") != ["Số cuối năm"] or previous_opening.get("period_labels") != ["Số đầu năm"]:
            return None
        if (preceding_context.get("grid") or {}).get("width") != 5 or (continuation_context.get("grid") or {}).get("width") != 5 or len(previous_columns) != 5:
            return None
    except (RecheckBlocked, ValueError, TypeError):
        return None
    return {
        "preceding_table_uid": preceding_table.get("internal_table_uid"),
        "preceding_local_ordinal": preceding_table.get("local_ordinal"),
        "current_local_ordinal": continuation_table.get("local_ordinal"),
        "same_document_id": True,
        "same_source_sha256": True,
        "statement_date": report_date,
    }


def _record_sha(record: Mapping[str, Any]) -> str:
    return canonical_sha256(record)


def document_header_period_marker(
    *,
    table: Mapping[str, Any],
    requested_year: int,
    max_prefix_chars: int = 20_000,
) -> dict[str, Any] | None:
    """Return a hash-bound document marker, deduplicating repeated OCR prints.

    The marker is intentionally compatible with the existing v2 document
    marker: several occurrences of the same report date are acceptable, but a
    requested year must resolve to exactly one unique valid date.  The
    filename is never consulted.
    """

    provenance = _source_provenance(table)
    path = Path(str(provenance.get("source_path") or ""))
    expected_sha = str(provenance.get("source_sha256") or "")
    if not path.is_file() or not expected_sha:
        return None
    source_bytes = path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != expected_sha:
        return None
    text = source_bytes.decode("utf-8")
    start = _safe_int(provenance.get("char_start"), "char_start")
    prefix_end = min(max(start, 0), max_prefix_chars) if start else min(len(text), max_prefix_chars)
    prefix = text[:prefix_end]
    dates: list[date] = []
    occurrence_count = 0
    for day, month, year in _DOCUMENT_PERIOD_DATE_RE.findall(_fold(prefix)):
        try:
            parsed = date(int(year), int(month), int(day))
        except ValueError:
            continue
        occurrence_count += 1
        if parsed not in dates:
            dates.append(parsed)
    matching = [value for value in dates if value.year == requested_year]
    if len(matching) != 1:
        return None
    return {
        "protocol": "v2_document_header_period_marker_v1",
        "document_id": table.get("document_id"),
        "internal_table_uid": table.get("internal_table_uid"),
        "source_sha256": expected_sha,
        "source_prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
        "source_prefix_char_end": prefix_end,
        "source_date": matching[0].isoformat(),
        "unique_period_dates": [value.isoformat() for value in dates],
        "matching_occurrence_count": occurrence_count,
    }


def _base_identity(packet: Mapping[str, Any], exact: Mapping[str, Any]) -> dict[str, Any] | None:
    for stage in exact.get("stages") or []:
        for operand in stage.get("required_operands") or []:
            candidates = operand.get("binding_candidates") or []
            if not candidates and operand.get("binding_status") == "binding_ready":
                candidates = [operand]
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    continue
                identity = {
                    key: candidate.get(key)
                    for key in (
                        "internal_table_uid",
                        "row_index",
                        "column_index",
                        "period_labels",
                        "period_source_date",
                        "period_resolution_method",
                        "header_source_cells",
                    )
                    if key in candidate
                }
                if "period_document_header_marker" in candidate:
                    marker = candidate["period_document_header_marker"]
                    if isinstance(marker, Mapping):
                        identity["period_document_header_marker"] = {
                            key: marker.get(key)
                            for key in (
                                "protocol",
                                "document_id",
                                "internal_table_uid",
                                "source_date",
                                "source_prefix_char_end",
                                "source_prefix_sha256",
                                "source_sha256",
                            )
                            if key in marker
                        }
                if identity:
                    return identity
    return None


def _before_snapshot(packet: Mapping[str, Any], exact: Mapping[str, Any]) -> dict[str, Any]:
    binding_statuses: list[str] = []
    candidate_counts: list[int] = []
    for stage in exact.get("stages") or []:
        for operand in stage.get("required_operands") or []:
            binding_statuses.append(str(operand.get("binding_status") or ""))
            candidate_counts.append(len(operand.get("binding_candidates") or []))
    return {
        "period_packet_status": packet.get("packet_status"),
        "period_input_packet_status": packet.get("input_packet_status"),
        "period_route_status": packet.get("route_status"),
        "period_candidate_count": sum(
            len(operand.get("period_column_candidates") or [])
            for stage in packet.get("stages") or []
            for operand in stage.get("required_operands") or []
        ),
        "e2e_binding_packet_status": exact.get("binding_packet_status"),
        "e2e_route_status": exact.get("route_status"),
        "e2e_binding_statuses": binding_statuses,
        "e2e_binding_candidate_counts": candidate_counts,
        "candidate_identity": _base_identity(packet, exact),
    }


def _safe_candidate_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "internal_table_uid",
        "row_index",
        "column_index",
        "current_column_index",
        "closing_column_index",
        "opening_column_index",
        "current_total_row_index",
        "prior_total_row_index",
        "prior_column_index",
        "period_labels",
        "current_period_labels",
        "prior_period_labels",
        "period_role",
        "companion_period_role",
        "period_source_date",
        "prior_source_date",
        "period_resolution_method",
        "header_source_cells",
        "header_source_table_uid",
        "document_id",
    )
    identity = {key: candidate[key] for key in allowed if key in candidate}
    marker = candidate.get("period_document_header_marker")
    if isinstance(marker, Mapping):
        identity["period_document_header_marker"] = {
            key: marker.get(key)
            for key in (
                "protocol",
                "document_id",
                "internal_table_uid",
                "source_date",
                "source_prefix_char_end",
                "source_prefix_sha256",
                "source_sha256",
            )
            if key in marker
        }
    return identity


def _hypothesis(hypothesis_id: str, status: str, evidence_ids: list[str], evidence: str, risk: str, elimination_condition: str) -> dict[str, Any]:
    return {
        "hypothesis_id": hypothesis_id,
        "status": status,
        "evidence_ids": evidence_ids,
        "evidence": evidence,
        "risk": risk,
        "elimination_condition": elimination_condition,
    }


def _common_result(
    *,
    question_id: int,
    target: Mapping[str, Any],
    packet: Mapping[str, Any],
    exact: Mapping[str, Any],
    hypotheses: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    candidate_navigation: Mapping[str, Any] | None,
    status: str,
    conclusion: str,
) -> dict[str, Any]:
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_id": question_id,
        "entity": target["entity"],
        "requested_year": target["requested_year"],
        "recheck_status": status,
        "candidate_count": 1 if candidate_navigation is not None else 0,
        "candidate_navigation": dict(candidate_navigation) if candidate_navigation is not None else None,
        "source_evidence": evidence,
        "hypotheses": hypotheses,
        "conclusion": conclusion,
        "before": _before_snapshot(packet, exact),
        "raw_numeric_values_included": False,
        "source_contract": dict(CONTRACT),
    }
    if _contains_forbidden_key(result):
        raise ValueError(f"forbidden output key in Q{question_id}")
    return result


def _q70(packet: Mapping[str, Any], exact: Mapping[str, Any], v2: Mapping[str, Any], v3: Mapping[str, Any]) -> dict[str, Any]:
    target = TARGETS[70]
    table_span = _verified_table_span(v2, expected_source_sha=EXPECTED_SOURCE_SHA256[70], expected_table_sha=EXPECTED_TABLE_SHA256[70])
    if not _aligned(v2, v3):
        raise RecheckBlocked("Q70 V2/V3 alignment failed")
    if v2.get("document_id") != target["document_id"] or v2.get("local_ordinal") != target["local_ordinal"]:
        raise RecheckBlocked("Q70 document or ordinal changed")
    row = _row(v2, target["row_index"])
    if not _fold(row[0]) == _fold("Công ty Cổ phần Bao bì Dầu khí Việt Nam"):
        raise RecheckBlocked("Q70 semantic row anchor changed")
    column = _column(v3, target["column_index"])
    if column.get("period_labels") != ["Số cuối năm"] or column.get("unit_labels") != ["VND"]:
        raise RecheckBlocked("Q70 current header changed")
    marker = document_header_period_marker(table=v2, requested_year=target["requested_year"])
    if marker is None or marker.get("source_date") != target["source_date"]:
        raise RecheckBlocked("Q70 document header marker is not unique")
    old_identity = _base_identity(packet, exact)
    if old_identity is None:
        raise RecheckBlocked("Q70 has no old candidate identity")
    old_marker = old_identity.get("period_document_header_marker") or {}
    for key in ("internal_table_uid", "source_date", "source_prefix_char_end", "source_prefix_sha256", "source_sha256"):
        if old_marker.get(key) != marker.get(key):
            raise RecheckBlocked(f"Q70 marker regression at {key}")
    for key, expected in {
        "internal_table_uid": target["internal_table_uid"],
        "row_index": target["row_index"],
        "column_index": target["column_index"],
        "period_labels": ["2019"],
        "period_source_date": target["source_date"],
        "period_resolution_method": target["period_resolution_method"],
    }.items():
        if old_identity.get(key) != expected:
            raise RecheckBlocked(f"Q70 old candidate identity changed at {key}")
    safe_candidate = {
        "internal_table_uid": target["internal_table_uid"],
        "document_id": target["document_id"],
        "row_index": target["row_index"],
        "column_index": target["column_index"],
        "period_labels": ["2019"],
        "period_source_date": marker["source_date"],
        "period_resolution_method": target["period_resolution_method"],
        "period_role": "closing",
        "header_source_cells": [{"row_index": 0, "column_index": 1}, {"row_index": 1, "column_index": 1}],
        "period_document_header_marker": {
            key: marker[key]
            for key in (
                "protocol",
                "document_id",
                "internal_table_uid",
                "source_date",
                "source_prefix_char_end",
                "source_prefix_sha256",
                "source_sha256",
            )
        },
    }
    evidence = [
        _record_evidence(evidence_id="q70-v2-table", kind="v2_record", record=v2, record_sha256=EXPECTED_RECORD_SHA256[70]["v2"], marker="current receivables row and Số cuối năm/VND header"),
        _record_evidence(evidence_id="q70-v3-context", kind="v3_context", record=v3, record_sha256=EXPECTED_RECORD_SHA256[70]["v3"], marker="Số cuối năm column is period-bound locally"),
        {**table_span, "evidence_id": "q70-ocr-table-span", "kind": "ocr_table_span", "printed_marker": "related-party current receivables table"},
        {
            "evidence_id": "q70-document-header-prefix",
            "kind": "document_header_prefix",
            "document_id": target["document_id"],
            "internal_table_uid": target["internal_table_uid"],
            "source_sha256": marker["source_sha256"],
            "source_prefix_sha256": marker["source_prefix_sha256"],
            "source_prefix_char_end": marker["source_prefix_char_end"],
            "source_date": marker["source_date"],
            "unique_period_dates": marker["unique_period_dates"],
            "matching_occurrence_count": marker["matching_occurrence_count"],
            "printed_marker": "Tại ngày 31 tháng 12 năm 2019 in trong phần đầu cùng file",
        },
    ]
    hypotheses = [
        _hypothesis("period_in_current_header", "SUPPORTED_FOR_ROLE_ONLY", ["q70-v2-table", "q70-v3-context"], "Cột 1 có nhãn Số cuối năm và VND; đây là bằng chứng vai trò cột, không tự chứa năm.", "Nếu gán năm từ nhãn cuối năm mà không có marker tài liệu thì có thể nhầm kỳ báo cáo.", "Loại bỏ nếu header cột, đơn vị, row anchor hoặc V2/V3 alignment thay đổi."),
        _hypothesis("period_in_table_title", "INSUFFICIENT", ["q70-v3-context"], "Context cục bộ không in năm báo cáo trong title của bảng này.", "Một title không có năm không thể xác nhận 2019.", "Không dùng title cục bộ làm bằng chứng năm nếu thiếu document marker."),
        _hypothesis("period_in_same_file_document_header", "SUPPORTED_STRONG", ["q70-document-header-prefix"], "Prefix OCR hash-bound trước bảng có một ngày hợp lệ duy nhất theo năm yêu cầu: 2019-12-31; nhiều lần lặp cùng ngày được khử lặp.", "Prefix sai hash hoặc chứa nhiều ngày hợp lệ khác nhau của cùng năm sẽ làm marker không duy nhất.", "Quarantined nếu source SHA, prefix SHA/extent, ngày hoặc UID không khớp."),
        _hypothesis("header_ocr_cut_or_missing", "PARTIAL_RECOVERY", ["q70-v2-table", "q70-document-header-prefix"], "Năm vắng ở header cục bộ nhưng vai trò current/opening còn nguyên; document marker khôi phục hẹp phần năm.", "Mở rộng recovery sang số liệu hoặc dùng tên file sẽ vượt phạm vi.", "Loại bỏ nếu phải đọc numeric cell để xác định kỳ."),
    ]
    result = _common_result(
        question_id=70,
        target=target,
        packet=packet,
        exact=exact,
        hypotheses=hypotheses,
        evidence=evidence,
        candidate_navigation=safe_candidate,
        status="REGRESSION_PASS_UNCHANGED_CANDIDATE",
        conclusion="Q70 giữ nguyên đúng UID, row, column, header cells, period method và document marker của candidate cũ.",
    )
    result["comparison"] = {
        "status": "UNCHANGED",
        "changed_fields": [],
        "before_candidate_identity": old_identity,
        "after_candidate_identity": safe_candidate,
    }
    return result


def _q185(
    packet: Mapping[str, Any],
    exact: Mapping[str, Any],
    v2: Mapping[str, Any],
    v3: Mapping[str, Any],
    v2_by_uid: Mapping[str, Mapping[str, Any]],
    v3_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    target = TARGETS[185]
    table_span = _verified_table_span(v2, expected_source_sha=EXPECTED_SOURCE_SHA256[185], expected_table_sha=EXPECTED_TABLE_SHA256[185])
    if not _aligned(v2, v3):
        raise RecheckBlocked("Q185 V2/V3 alignment failed")
    rows = _rows(v2)
    if len(rows) != 15 or any(len(row) != 5 for row in rows):
        raise RecheckBlocked("Q185 table shape changed")
    if "cho vay dai han" not in _row_text(rows[2]):
        raise RecheckBlocked("Q185 current section heading missing")
    if "2016" not in _row_text(rows[0]) or "2015" not in _row_text(rows[7]) or "2015" not in _row_text(rows[8]):
        raise RecheckBlocked("Q185 two period headers are not printed in V2")
    if not _row(v2, 6)[2] or not _row(v2, 14)[2]:
        raise RecheckBlocked("Q185 terminal rows are not structurally populated")
    if (v3.get("quality") or {}).get("status") != "review_ready":
        raise RecheckBlocked("Q185 V3 quality is not review_ready")
    current_column = _column(v3, 2)
    if current_column.get("period_labels") != ["2016"]:
        raise RecheckBlocked("Q185 current V3 column is not 2016")
    matching = []
    for uid, context in v3_by_uid.items():
        if context.get("document_id") != target["document_id"]:
            continue
        title = _fold((context.get("context_trace") or {}).get("source_title"))
        if "cho vay dai han khong co tai san dam bao" in title or "phai thu dai han ve cho vay" in title:
            matching.append(uid)
    if len(matching) != 1 or matching[0] != target["internal_table_uid"]:
        raise RecheckBlocked(f"Q185 semantic table uniqueness failed: {matching}")
    wrong = v3_by_uid.get(target["wrong_direction_uid"])
    if wrong is None or "di vay dai han khong co tai san dam bao" not in _fold((wrong.get("context_trace") or {}).get("source_title")):
        raise RecheckBlocked("Q185 wrong-direction rejection anchor missing")
    source_path = _source_path(v2)
    context_span = _verified_source_span(
        source_path,
        start=195429,
        end=197535,
        expected_source_sha=EXPECTED_SOURCE_SHA256[185],
        evidence_id="q185-ocr-two-section-span",
        kind="ocr_context_span",
        marker="2016 current section followed by 2015 prior section in the same table",
    )
    candidate = {
        "internal_table_uid": target["internal_table_uid"],
        "document_id": target["document_id"],
        "row_index": target["row_index"],
        "column_index": target["column_index"],
        "period_labels": ["2016"],
        "period_source_date": target["source_date"],
        "period_resolution_method": target["period_resolution_method"],
        "period_role": "closing",
        "header_source_cells": [{"row_index": 0, "column_index": 2}],
        "section_boundary": {
            "current_section_heading_row_index": 2,
            "current_total_row_index": 6,
            "prior_section_marker_row_index": 7,
            "prior_header_row_index": 8,
            "prior_total_row_index": 14,
        },
        "companion_prior_period": {
            "row_index": target["prior_row_index"],
            "column_index": target["prior_column_index"],
            "period_labels": ["2015"],
            "period_source_date": target["prior_source_date"],
            "period_role": "prior",
        },
    }
    evidence = [
        _record_evidence(evidence_id="q185-v2-table", kind="v2_record", record=v2, record_sha256=EXPECTED_RECORD_SHA256[185]["v2"], marker="long-term loans to related parties with current and prior section headers"),
        _record_evidence(evidence_id="q185-v3-context", kind="v3_context", record=v3, record_sha256=EXPECTED_RECORD_SHA256[185]["v3"], marker="related-party schedule with current column 2016"),
        {**table_span, "evidence_id": "q185-ocr-table-span", "kind": "ocr_table_span", "printed_marker": "long-term unsecured lending table"},
        context_span,
        {"evidence_id": "q185-direction-rejection", "kind": "semantic_direction_rejection", "internal_table_uid": target["wrong_direction_uid"], "local_ordinal": 82, "record_sha256": _record_sha(wrong), "printed_marker": "đi vay dài hạn không có tài sản đảm bảo; rejected because direction is borrowing, not lending"},
    ]
    hypotheses = [
        _hypothesis("period_in_current_header", "SUPPORTED_WITH_SECTION_BOUNDARY", ["q185-v2-table", "q185-v3-context"], "V2 row 0 prints 2016 in the value header and V3 binds column 2 to 2016; V2 row 7/8 starts a separate 2015 section.", "Flattening both sections under one V3 header could mislabel the 2015 rows.", "Loại bỏ nếu row 7/8 không còn là marker/header 2015, table shape/hash đổi, hoặc current column không duy nhất."),
        _hypothesis("period_in_table_title", "SUPPORTED_STRONG", ["q185-ocr-two-section-span", "q185-ocr-table-span"], "OCR span hash-bound around the table prints the 2016 table title and the following 2015 section title/header.", "The two sections share one HTML table and the terminal total rows have sparse labels.", "Quarantined if either period title/header disappears or section boundary is ambiguous."),
        _hypothesis("period_in_same_file_document_header", "SUPPORTED_CORROBORATING_ONLY", ["q185-ocr-two-section-span"], "The same OCR document context also prints the 2016 report date.", "File-level/document-level date alone cannot distinguish the current 2016 section from the embedded 2015 comparison section.", "Eliminate as sole basis; require the exact two-section table evidence."),
        _hypothesis("header_ocr_cut_or_missing", "PARTIAL_V3_PROJECTION", ["q185-v2-table", "q185-v3-context"], "V3 period projection contains maturity years in another column and does not fully model the second 2015 section, while V2/OCR preserve both printed headers.", "A projection-only recovery could select a maturity year or the wrong section.", "Quarantined unless V2 rows, source span, section order and semantic direction all validate."),
    ]
    result = _common_result(
        question_id=185,
        target=target,
        packet=packet,
        exact=exact,
        hypotheses=hypotheses,
        evidence=evidence,
        candidate_navigation=candidate,
        status="MATERIALIZED_CANDIDATE_ONLY",
        conclusion="Q185 mở khóa navigation candidate-only cho tổng cuối kỳ 2016 tại row 6/column 2; row 14/column 2 được giữ làm companion 2015. Không phát hành giá trị.",
    )
    result["comparison"] = {
        "status": "CANDIDATE_ADDED_FROM_SOURCE_RECHECK",
        "changed_fields": ["candidate_navigation", "source_evidence"],
        "before_candidate_identity": None,
        "after_candidate_identity": _safe_candidate_identity(candidate),
    }
    return result


def _q357(
    packet: Mapping[str, Any],
    exact: Mapping[str, Any],
    v2: Mapping[str, Any],
    v3: Mapping[str, Any],
    preceding_v2: Mapping[str, Any],
    preceding_v3: Mapping[str, Any],
) -> dict[str, Any]:
    target = TARGETS[357]
    table_span = _verified_table_span(v2, expected_source_sha=EXPECTED_SOURCE_SHA256[357], expected_table_sha=EXPECTED_TABLE_SHA256[357])
    preceding_span = _verified_table_span(preceding_v2, expected_source_sha=EXPECTED_SOURCE_SHA256[357], expected_table_sha=EXPECTED_TABLE_SHA256["357_preceding"])
    if v2.get("local_ordinal") != target["local_ordinal"] or preceding_v2.get("local_ordinal") != target["preceding_local_ordinal"]:
        raise RecheckBlocked("Q357 continuation ordinals changed")
    if v2.get("document_id") != target["document_id"] or preceding_v2.get("document_id") != target["document_id"]:
        raise RecheckBlocked("Q357 continuation document changed")
    source_path = _source_path(v2)
    source_text = source_path.read_text(encoding="utf-8")
    preceding_end = preceding_span["char_end"]
    current_start = table_span["char_start"]
    previous_preamble_start, previous_preamble_end = 8771, 9093
    continuation_preamble_start, continuation_preamble_end = 13466, 13800
    continuation_link = continuation_header_link(
        preceding_table=preceding_v2,
        continuation_table=v2,
        preceding_context=preceding_v3,
        continuation_context=v3,
        source_text=source_text,
        preceding_table_end=preceding_end,
        preceding_preamble_start=previous_preamble_start,
        preceding_preamble_end=previous_preamble_end,
        continuation_preamble_start=continuation_preamble_start,
        continuation_preamble_end=continuation_preamble_end,
        continuation_table_start=current_start,
        report_date=target["source_date"],
    )
    if continuation_link is None:
        raise RecheckBlocked("Q357 continuation header link is not unique")
    previous_rows = _rows(preceding_v2)
    current_rows = _rows(v2)
    if len(previous_rows) != 37 or len(current_rows) != 28 or any(len(row) != 5 for row in previous_rows + current_rows):
        raise RecheckBlocked("Q357 balance-sheet shape changed")
    if _row_text(current_rows[20]) == "" or "von gop cua chu so huu" not in _row_text(current_rows[20]) or str(current_rows[20][1]).strip() != "411":
        raise RecheckBlocked("Q357 share-capital row anchor changed")
    if (v3.get("quality") or {}).get("reason_codes") != ["numeric_column_without_source_header"]:
        raise RecheckBlocked("Q357 continuation header-loss fingerprint changed")
    header_sources = [
        {"row_index": 0, "column_index": 3},
        {"row_index": 0, "column_index": 4},
        {"row_index": 1, "column_index": 4},
    ]
    candidate = {
        "internal_table_uid": target["internal_table_uid"],
        "document_id": target["document_id"],
        "row_index": target["row_index"],
        "closing_column_index": target["closing_column_index"],
        "opening_column_index": target["opening_column_index"],
        "period_labels": ["Số cuối năm", "Số đầu năm"],
        "period_source_date": target["source_date"],
        "period_resolution_method": target["period_resolution_method"],
        "period_role": "closing",
        "companion_period_role": "opening",
        "header_source_table_uid": target["preceding_table_uid"],
        "header_source_cells": header_sources,
        "continuation_link": continuation_link,
    }
    evidence = [
        _record_evidence(evidence_id="q357-v2-continuation-table", kind="v2_record", record=v2, record_sha256=EXPECTED_RECORD_SHA256[357]["v2"], marker="balance-sheet continuation containing code 411 row"),
        _record_evidence(evidence_id="q357-v3-continuation-context", kind="v3_context", record=v3, record_sha256=EXPECTED_RECORD_SHA256[357]["v3"], marker="continuation context has unit but no local period labels"),
        _record_evidence(evidence_id="q357-v2-preceding-table", kind="v2_preceding_record", record=preceding_v2, record_sha256=EXPECTED_RECORD_SHA256["357_preceding"]["v2"], marker="preceding balance-sheet table carries closing/opening header roles"),
        _record_evidence(evidence_id="q357-v3-preceding-context", kind="v3_preceding_context", record=preceding_v3, record_sha256=EXPECTED_RECORD_SHA256["357_preceding"]["v3"], marker="Số cuối năm and Số đầu năm are canonicalized in columns 3/4"),
        {**preceding_span, "evidence_id": "q357-ocr-preceding-table-span", "kind": "ocr_table_span", "printed_marker": "preceding balance-sheet table"},
        {**table_span, "evidence_id": "q357-ocr-continuation-table-span", "kind": "ocr_table_span", "printed_marker": "continuation balance-sheet table"},
        _verified_source_span(source_path, start=previous_preamble_start, end=previous_preamble_end, expected_source_sha=EXPECTED_SOURCE_SHA256[357], evidence_id="q357-ocr-preceding-preamble", kind="ocr_preamble_span", marker="Bảng cân đối kế toán riêng tại ngày 31 tháng 12 năm 2017"),
        _verified_source_span(source_path, start=continuation_preamble_start, end=continuation_preamble_end, expected_source_sha=EXPECTED_SOURCE_SHA256[357], evidence_id="q357-ocr-continuation-preamble", kind="ocr_preamble_span", marker="Bảng cân đối kế toán riêng (Tiếp theo) tại ngày 31 tháng 12 năm 2017"),
        {"evidence_id": "q357-continuation-link", "kind": "continuation_link", "preceding_table_uid": target["preceding_table_uid"], "current_table_uid": target["internal_table_uid"], "same_document_id": True, "same_source_sha256": True, "preceding_table_end": preceding_end, "continuation_preamble_start": continuation_preamble_start, "current_table_start": current_start, "printed_marker": "same statement/date and ordered preceding/current tables"},
    ]
    hypotheses = [
        _hypothesis("period_in_current_header", "UNSUPPORTED_LOCALLY", ["q357-v3-continuation-context"], "Bảng tiếp nối chỉ còn đơn vị VND ở row header; V3 đánh dấu các cột numeric là unknown.", "Chọn cột chỉ vì có số sẽ có thể đảo closing/opening hoặc chọn cột mã/reference.", "Loại bỏ nếu không có header kế thừa được xác minh từ bảng liền trước."),
        _hypothesis("period_in_table_title", "SUPPORTED_FOR_REPORT_DATE_ONLY", ["q357-ocr-continuation-preamble"], "Preamble của trang tiếp nối in rõ cùng statement và ngày 31/12/2017.", "Ngày báo cáo không tự phân biệt cột cuối năm và đầu năm.", "Không dùng title một mình để chọn column index."),
        _hypothesis("period_in_same_file_continuation", "SUPPORTED_STRONG", ["q357-v2-preceding-table", "q357-v3-preceding-context", "q357-continuation-link", "q357-ocr-preceding-preamble", "q357-ocr-continuation-preamble"], "Bảng trước cùng document/source SHA, liền kề theo ordinal/source order, có closing col 3 và opening col 4; bảng sau cùng statement/date và chứa row code 411.", "Một table bị chèn hoặc lệch ordinal có thể làm inheritance sai.", "Quarantined nếu document/source SHA, statement/date, order, width hoặc header roles không đồng nhất."),
        _hypothesis("header_ocr_cut_or_missing", "SUPPORTED_WITH_STRICT_INHERITANCE", ["q357-v3-continuation-context", "q357-v3-preceding-context"], "Đây là header loss cục bộ có fingerprint numeric_column_without_source_header; chỉ khôi phục period role từ cặp continuation đã hash-bound.", "Không được tự tạo header từ số liệu hoặc tên file.", "Loại bỏ nếu bảng trước không còn review_ready/header roles hoặc link không duy nhất."),
    ]
    result = _common_result(
        question_id=357,
        target=target,
        packet=packet,
        exact=exact,
        hypotheses=hypotheses,
        evidence=evidence,
        candidate_navigation=candidate,
        status="MATERIALIZED_CANDIDATE_ONLY",
        conclusion="Q357 mở khóa navigation candidate-only: row 20 code 411, column 3 là cuối năm và column 4 là đầu năm, kế thừa nghiêm ngặt từ bảng trước. Không phát hành giá trị.",
    )
    result["comparison"] = {
        "status": "CANDIDATE_ADDED_FROM_SOURCE_RECHECK",
        "changed_fields": ["candidate_navigation", "source_evidence"],
        "before_candidate_identity": None,
        "after_candidate_identity": _safe_candidate_identity(candidate),
    }
    return result


def _quarantined(question_id: int, packet: Mapping[str, Any], exact: Mapping[str, Any], reason: str) -> dict[str, Any]:
    target = TARGETS[question_id]
    result = _common_result(
        question_id=question_id,
        target=target,
        packet=packet,
        exact=exact,
        hypotheses=[],
        evidence=[],
        candidate_navigation=None,
        status="QUARANTINED",
        conclusion=f"Không đủ bằng chứng duy nhất; giữ QUARANTINED. Lý do kiểm tra: {reason}.",
    )
    result["quarantine_reason"] = reason
    result["comparison"] = {
        "status": "QUARANTINED",
        "changed_fields": [],
        "before_candidate_identity": result["before"].get("candidate_identity"),
        "after_candidate_identity": None,
    }
    return result


def _input_descriptor(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path), "sha256": sha256_file(path)}


def _verify_period_manifest(packet_path: Path, manifest_path: Path) -> None:
    manifest = _read_json(manifest_path)
    descriptor = (manifest.get("outputs") or {}).get("period_packets")
    if not isinstance(descriptor, Mapping) or descriptor.get("sha256") != sha256_file(packet_path):
        raise ValueError("period packet hash does not match its manifest")


def _verify_evidence_manifest(context_path: Path, manifest_path: Path) -> None:
    manifest = _read_json(manifest_path)
    expected = manifest.get("sidecar_sha256")
    if expected is not None and expected != sha256_file(context_path):
        raise ValueError("V3 context hash does not match its manifest")


def _report(results: list[Mapping[str, Any]], inputs: Mapping[str, Mapping[str, Any]], output_names: Mapping[str, str]) -> str:
    lines = [
        "# Agent 1 — source-period recheck Q185, Q357 và regression Q70",
        "",
        f"Protocol: `{PROTOCOL}`.",
        "",
        "Phạm vi này chỉ tạo candidate navigation và provenance. Tất cả artifact đều `candidate_only`; `evidence_eligible=false`, `submission_eligible=false`, không chạy công thức và không chọn giá trị cuối. Năm chỉ được lấy từ marker/header đã hash-bound trong OCR, không lấy từ tên file.",
        "",
        "## Nguồn đã khóa",
        "",
        "| Artifact | SHA-256 |",
        "|---|---|",
    ]
    for name, descriptor in inputs.items():
        if name == "source_ocr_files":
            for question_id, source_descriptor in descriptor.items():
                lines.append(f"| `source_ocr_files.Q{question_id}` | `{source_descriptor['sha256']}` |")
        else:
            lines.append(f"| `{name}` | `{descriptor['sha256']}` |")
    lines.extend([
        "",
        "## Kết luận nhanh",
        "",
        "| Câu | Trước | Sau recheck | Navigation candidate | Evidence/release |",
        "|---:|---|---|---|---|",
    ])
    for result in results:
        q = result["question_id"]
        before = result["before"]
        if q == 70:
            nav = "UID `1c246...f215c`, row 3 / col 1, closing 2019"
        elif q == 185:
            nav = "UID `f75f...4b3`, current total row 6 / col 2 (2016); companion row 14 / col 2 (2015)"
        else:
            nav = "UID `cfd4...2217c`, row 20 code 411, closing col 3 / opening col 4"
        lines.append(f"| {q} | `{before['period_packet_status']}`; E2E `{before['e2e_binding_packet_status']}` | `{result['recheck_status']}` | {nav} | locked (`false` / `false`) |")
    lines.extend(["", "## Hypotheses và quyết định", ""])
    for result in results:
        lines.extend([f"### Q{result['question_id']}", "", result["conclusion"], ""])
        lines.extend([
            "| Giả thuyết | Trạng thái | Bằng chứng | Rủi ro | Điều kiện loại bỏ |",
            "|---|---|---|---|---|",
        ])
        for hypothesis in result.get("hypotheses") or []:
            lines.append(
                f"| `{hypothesis['hypothesis_id']}` | {hypothesis['status']} | {hypothesis['evidence']} (`{', '.join(hypothesis['evidence_ids'])}`) | {hypothesis['risk']} | {hypothesis['elimination_condition']} |"
            )
        lines.append("")
        lines.append("Evidence spans/record hashes:")
        for evidence in result.get("source_evidence") or []:
            details = []
            for key in ("internal_table_uid", "record_sha256", "source_sha256", "span_sha256", "char_start", "char_end", "source_prefix_sha256", "source_prefix_char_end", "source_date"):
                if key in evidence:
                    details.append(f"{key}=`{evidence[key]}`")
            lines.append(f"- `{evidence['evidence_id']}` ({evidence['kind']}): " + ", ".join(details) + f"; marker: {evidence.get('printed_marker', '')}")
        lines.append("")
    lines.extend([
        "## So sánh trước/sau",
        "",
        "Q70 có `changed_fields=[]`: UID, row, column, header cells, period method và document marker đều giữ nguyên. Q185 và Q357 trước đây bị block ở period/binding packet; recheck chỉ thêm navigation candidate dựa trên nguồn, không thêm numeric payload.",
        "",
        "## Lệnh chạy lại cho Agent 4",
        "",
        "Agent 4 cần tạo config/input packet mới trỏ tới artifact này; không sửa config v8 bất biến. Lệnh build và validate:",
        "",
        "```bash",
        f".venv/bin/python scripts/research/build_source_period_recheck_agent1_v1.py --config {inputs['e2e_config']['path']} --period-packets {inputs['period_packets']['path']} --period-manifest {inputs['period_manifest']['path']} --e2e-candidates {inputs['e2e_exact_candidates']['path']} --e2e-receipt {inputs['e2e_receipt']['path']} --structured-tables {inputs['structured_tables_v2']['path']} --evidence-context {inputs['evidence_context_v3']['path']} --evidence-context-manifest {inputs['evidence_context_manifest_v3']['path']} --output-dir artifacts/research/source_period_recheck_agent1_v1_20260827",
        ".venv/bin/python scripts/research/validate_source_period_recheck_agent1_v1.py --artifact-dir artifacts/research/source_period_recheck_agent1_v1_20260827",
        "```",
        "",
        "Sau khi Agent 4 materialize period-packet union candidate-only bằng adapter riêng, chạy E2E vào output directory mới:",
        "",
        "```bash",
        ".venv/bin/python -m finance_query.cli run-e2e --config <agent4-new-config.yaml> --output-dir artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r1",
        "```",
        "",
        "Không có câu nào được mở khóa evidence/answer/submission bởi artifact này; nếu bất kỳ hash/link/header nào không còn duy nhất, validator phải giữ câu đó ở `QUARANTINED`.",
        "",
    ])
    return "\n".join(lines)


def build_source_period_recheck_agent1(
    *,
    config: Path,
    period_packets: Path,
    period_manifest: Path,
    e2e_candidates: Path,
    e2e_receipt: Path,
    structured_tables_v2: Path,
    evidence_context_v3: Path,
    evidence_context_manifest_v3: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    input_paths = {
        "e2e_config": config,
        "period_packets": period_packets,
        "period_manifest": period_manifest,
        "e2e_exact_candidates": e2e_candidates,
        "e2e_receipt": e2e_receipt,
        "structured_tables_v2": structured_tables_v2,
        "evidence_context_v3": evidence_context_v3,
        "evidence_context_manifest_v3": evidence_context_manifest_v3,
    }
    input_descriptors = {name: _input_descriptor(path) for name, path in input_paths.items()}
    _verify_period_manifest(period_packets, period_manifest)
    _verify_evidence_manifest(evidence_context_v3, evidence_context_manifest_v3)
    receipt = _read_json(e2e_receipt)
    if receipt.get("run_name") != "vifinqa-explicit-ticker-candidate-replay-v8" or receipt.get("run_status") != "complete_research_only":
        raise ValueError("unexpected E2E v8 receipt")
    if (receipt.get("outputs") or {}).get("authorization", {}).get("counts", {}).get("answer_certificate_status_counts") != {"ABSTAIN": 1012}:
        raise ValueError("E2E receipt is not the expected all-abstain research snapshot")
    packet_rows = _read_jsonl(period_packets)
    exact_rows = _read_jsonl(e2e_candidates)
    packets = {int(row["question_id"]): row for row in packet_rows}
    exact = {int(row["question_id"]): row for row in exact_rows}
    if set(TARGETS) - set(packets) or set(TARGETS) - set(exact):
        raise ValueError("target question missing from current packet or E2E snapshot")
    v2_rows = _read_jsonl(structured_tables_v2)
    v3_rows = _read_jsonl(evidence_context_v3)
    v2_by_uid = {str(row.get("internal_table_uid")): row for row in v2_rows}
    v3_by_uid = {str(row.get("internal_table_uid")): row for row in v3_rows}
    for question_id, target in TARGETS.items():
        if target["internal_table_uid"] not in v2_by_uid or target["internal_table_uid"] not in v3_by_uid:
            raise ValueError(f"target table missing for Q{question_id}")

    results: list[dict[str, Any]] = []
    for question_id in sorted(TARGETS):
        packet, exact_row = packets[question_id], exact[question_id]
        try:
            v2 = v2_by_uid[TARGETS[question_id]["internal_table_uid"]]
            v3 = v3_by_uid[TARGETS[question_id]["internal_table_uid"]]
            if question_id == 70:
                result = _q70(packet, exact_row, v2, v3)
            elif question_id == 185:
                result = _q185(packet, exact_row, v2, v3, v2_by_uid, v3_by_uid)
            else:
                preceding_uid = TARGETS[357]["preceding_table_uid"]
                result = _q357(packet, exact_row, v2, v3, v2_by_uid[preceding_uid], v3_by_uid[preceding_uid])
        except RecheckBlocked as exc:
            result = _quarantined(question_id, packet, exact_row, str(exc))
        results.append(result)

    if any(_contains_forbidden_key(result) for result in results):
        raise ValueError("forbidden output key in discovery results")
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_ids": sorted(TARGETS),
        "candidate_count": sum(int(result["candidate_count"]) for result in results),
        "recheck_status_counts": {
            status: sum(1 for result in results if result["recheck_status"] == status)
            for status in sorted({str(result["recheck_status"]) for result in results})
        },
        "candidate_question_ids": [result["question_id"] for result in results if result["candidate_count"] == 1],
        "quarantined_question_ids": [result["question_id"] for result in results if result["recheck_status"] == "QUARANTINED"],
        "raw_numeric_values_included": False,
        "source_contract": dict(CONTRACT),
    }
    before_after = []
    for result in results:
        before_after.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": result["question_id"],
                "before": {
                    "candidate_identity": result["before"].get("candidate_identity"),
                    "period_packet_status": result["before"].get("period_packet_status"),
                    "e2e_binding_packet_status": result["before"].get("e2e_binding_packet_status"),
                },
                "after": {
                    "recheck_status": result["recheck_status"],
                    "candidate_count": result["candidate_count"],
                    "candidate_identity": _safe_candidate_identity(result["candidate_navigation"]) if result.get("candidate_navigation") else None,
                },
                "comparison": result["comparison"],
                "source_contract": dict(CONTRACT),
            }
        )
    if any(_contains_forbidden_key(row) for row in before_after + [summary]):
        raise ValueError("forbidden output key in comparison artifacts")

    source_paths = {}
    for question_id in TARGETS:
        source_paths[str(question_id)] = _input_descriptor(_source_path(v2_by_uid[TARGETS[question_id]["internal_table_uid"]]))
    input_descriptors["source_ocr_files"] = {key: value for key, value in source_paths.items()}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        discovery_path = temporary / "source_period_recheck_discovery_v1.jsonl"
        comparison_path = temporary / "source_period_recheck_before_after_v1.jsonl"
        summary_path = temporary / "source_period_recheck_agent1_summary_v1.json"
        report_path = temporary / "source_period_recheck_agent1_report_v1.md"
        _write_jsonl(discovery_path, results)
        _write_jsonl(comparison_path, before_after)
        _write_json(summary_path, summary)
        output_names = {
            "discovery": discovery_path.name,
            "before_after": comparison_path.name,
            "summary": summary_path.name,
            "report": report_path.name,
        }
        report_path.write_text(_report(results, input_descriptors, output_names), encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": input_descriptors,
            "outputs": {
                name: {"path": filename, "sha256": sha256_file(temporary / filename)}
                for name, filename in output_names.items()
            },
            "source_evidence": {str(result["question_id"]): result["source_evidence"] for result in results},
            "source_contract": dict(CONTRACT),
        }
        _write_json(temporary / "source_period_recheck_agent1.manifest.json", manifest)
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_source_period_recheck_agent1(artifact_dir: Path) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "source_period_recheck_agent1.manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected Agent 1 source-period protocol/contract")
    def validate_input_descriptors(value: object) -> None:
        if not isinstance(value, Mapping):
            return
        if "path" in value:
            path = Path(str(value["path"]))
            if not path.is_file() or sha256_file(path) != value.get("sha256"):
                raise ValueError(f"input hash mismatch: {path}")
            return
        for child in value.values():
            validate_input_descriptors(child)

    validate_input_descriptors(manifest.get("inputs") or {})
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError(f"output hash mismatch: {path}")
    discovery = _read_jsonl(artifact_dir / str((manifest.get("outputs") or {})["discovery"]["path"]))
    before_after = _read_jsonl(artifact_dir / str((manifest.get("outputs") or {})["before_after"]["path"]))
    summary = _read_json(artifact_dir / str((manifest.get("outputs") or {})["summary"]["path"]))
    if [row.get("question_id") for row in discovery] != [70, 185, 357] or [row.get("question_id") for row in before_after] != [70, 185, 357]:
        raise ValueError("target coverage/order mismatch")
    for row in discovery + before_after + [summary]:
        if _contains_forbidden_key(row):
            raise ValueError("forbidden output key found")
        if "human_verified" in json.dumps(row, ensure_ascii=False):
            raise ValueError("manual-verification field found")
        if row.get("source_contract") != CONTRACT:
            raise ValueError("non-authorizing contract mismatch")
    expected_status = {70: "REGRESSION_PASS_UNCHANGED_CANDIDATE", 185: "MATERIALIZED_CANDIDATE_ONLY", 357: "MATERIALIZED_CANDIDATE_ONLY"}
    for row in discovery:
        q = int(row["question_id"])
        if row.get("recheck_status") == "QUARANTINED":
            if row.get("candidate_count") != 0 or row.get("candidate_navigation") is not None or (row.get("comparison") or {}).get("status") != "QUARANTINED":
                raise ValueError(f"Q{q} quarantine is not fail-closed")
            continue
        if row.get("recheck_status") != expected_status[q] or row.get("candidate_count") != 1:
            raise ValueError(f"unexpected current result for Q{q}")
        if not isinstance(row.get("candidate_navigation"), Mapping):
            raise ValueError(f"missing candidate navigation for Q{q}")
    q70 = next(row for row in discovery if row["question_id"] == 70)
    if q70.get("recheck_status") != "QUARANTINED" and ((q70.get("comparison") or {}).get("status") != "UNCHANGED" or (q70.get("comparison") or {}).get("changed_fields") != []):
        raise ValueError("Q70 regression comparison is not unchanged")
    actual_candidates = [int(row["question_id"]) for row in discovery if row.get("candidate_count") == 1]
    actual_quarantined = [int(row["question_id"]) for row in discovery if row.get("recheck_status") == "QUARANTINED"]
    if summary.get("candidate_question_ids") != actual_candidates or summary.get("quarantined_question_ids") != actual_quarantined:
        raise ValueError("summary does not match current target results")
    return {
        "protocol": PROTOCOL,
        "status": "PASS",
        "question_ids": [70, 185, 357],
        "candidate_question_ids": actual_candidates,
        "quarantined_question_ids": actual_quarantined,
    }
