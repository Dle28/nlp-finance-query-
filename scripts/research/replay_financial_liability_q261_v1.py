#!/usr/bin/env python3
"""Independently replay the BVH financial-liability maturity total for Q261.

The existing source-first route recovered the generic ``TỔNG CỘNG`` row from
the liability maturity schedule.  This checker deliberately does not import
that route.  It binds the raw table, source/table hashes, byte/character
coordinates, source line, liability context, requested date, total column and
million-VND value before emitting a PASS replay record suitable for the
generic UID-closure materializer.

The question ID is tracking metadata only.  It is not used to select a table
or to compute the answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping


EXPECTED_UID = "d3eff4436ee4bd8227a656ad9b18952a1a49f08dba8c86f03f0c13e4534e528f"
EXPECTED_DOCUMENT = "BVH_financial_statements_2019_consolidated"
EXPECTED_SOURCE_PATH = (
    "/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/BVH/"
    "2019/BVH_financial_statements_2019_consolidated/"
    "BVH_financial_statements_2019_consolidated_extracted.txt"
)
EXPECTED_SOURCE_SHA256 = "9e9762cd6c305ea70e40561255b3528230b9684e449667bce693576f710a990e"
EXPECTED_TABLE_SHA256 = "67c05eec239e176e6d3a38c9e515bdcd5bc189595f115411e7cccad7bd2d4687"
EXPECTED_SOURCE_LINE = 2958
EXPECTED_CHAR_START = 288645
EXPECTED_CHAR_END = 289751
EXPECTED_BYTE_START = 349248
EXPECTED_BYTE_END = 350453
EXPECTED_LOCAL_ORDINAL = 98
EXPECTED_PAGE_NO = 102
EXPECTED_ROW_INDEX = 8
EXPECTED_COLUMN_INDEX = 6
EXPECTED_VALUE_MILLION_VND = Decimal("174052754")

DEFAULT_ASSET = Path(
    "artifacts/research/document_corpus_round2_assets_v1_20260829_r1/"
    "full_table_assets_v1.jsonl"
)
DEFAULT_SOURCE_LINE_MAP = Path(
    "artifacts/research/document_corpus_round2_assets_v1_20260829_r1/"
    "source_line_map_full_v1.json"
)
DEFAULT_OUTPUT = Path(
    "artifacts/runs/vifinqa_answer_optimization_20260830/"
    "financial_liability_total_ab_v2/independent_replay_q261_v1.json"
)


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def canonical_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize(value)).strip()


def parse_decimal(value: Any) -> Decimal:
    text = str(value or "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("() ").replace(".", "").replace(",", ".")
    if not text or not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        raise InvalidOperation(f"not a numeric cell: {value!r}")
    number = Decimal(text)
    return -number if negative else number


def load_table(asset_path: Path) -> dict[str, Any]:
    found: dict[str, Any] | None = None
    with asset_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            table = json.loads(line)
            if str(table.get("internal_table_uid") or "") != EXPECTED_UID:
                continue
            if found is not None:
                raise ValueError(f"duplicate expected UID at asset line {line_number}")
            found = table
    if found is None:
        raise ValueError(f"missing expected table UID: {EXPECTED_UID}")
    return found


def verify_coordinates(
    table: Mapping[str, Any], source_line_map: Mapping[str, Any]
) -> tuple[str, int]:
    if str(table.get("source_path") or "") != EXPECTED_SOURCE_PATH:
        raise ValueError("source path mismatch")
    source_path = Path(EXPECTED_SOURCE_PATH)
    raw_bytes = source_path.read_bytes()
    actual_source_sha = hashlib.sha256(raw_bytes).hexdigest()
    if actual_source_sha != EXPECTED_SOURCE_SHA256:
        raise ValueError(f"source hash mismatch: {actual_source_sha}")
    if str(table.get("source_sha256") or "") != EXPECTED_SOURCE_SHA256:
        raise ValueError("source hash metadata mismatch")

    source_text = raw_bytes.decode("utf-8", errors="replace")
    char_start = int(table.get("char_start"))
    char_end = int(table.get("char_end"))
    byte_start = int(table.get("byte_start"))
    byte_end = int(table.get("byte_end"))
    if (char_start, char_end) != (EXPECTED_CHAR_START, EXPECTED_CHAR_END):
        raise ValueError("character coordinates mismatch")
    if (byte_start, byte_end) != (EXPECTED_BYTE_START, EXPECTED_BYTE_END):
        raise ValueError("byte coordinates mismatch")
    if not (0 <= char_start < char_end <= len(source_text)):
        raise ValueError("invalid character coordinates")
    if not (0 <= byte_start < byte_end <= len(raw_bytes)):
        raise ValueError("invalid byte coordinates")
    char_slice = source_text[char_start:char_end]
    byte_slice = raw_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
    if char_slice != byte_slice:
        raise ValueError("byte and character slices disagree")
    if hashlib.sha256(char_slice.encode("utf-8")).hexdigest() != EXPECTED_TABLE_SHA256:
        raise ValueError("recomputed table hash mismatch")
    if str(table.get("table_sha256") or "") != EXPECTED_TABLE_SHA256:
        raise ValueError("table hash metadata mismatch")

    mapped_line = source_line_map.get(EXPECTED_UID)
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(mapped_line) != EXPECTED_SOURCE_LINE or actual_line != EXPECTED_SOURCE_LINE:
        raise ValueError(
            f"source line mismatch map={mapped_line!r} actual={actual_line}"
        )
    return char_slice, actual_line


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    table = load_table(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    if not isinstance(source_line_map, dict):
        raise ValueError("source-line map must be a JSON object")

    if str(table.get("document_id") or "") != EXPECTED_DOCUMENT:
        raise ValueError("document mismatch")
    if str(table.get("ticker") or "").upper() != "BVH":
        raise ValueError("ticker mismatch")
    if str(table.get("scope") or "").lower() != "consolidated":
        raise ValueError("scope mismatch")
    if int(table.get("report_year")) != 2019:
        raise ValueError("report year mismatch")
    if int(table.get("local_ordinal")) != EXPECTED_LOCAL_ORDINAL:
        raise ValueError("local ordinal mismatch")
    if int(table.get("page_no")) != EXPECTED_PAGE_NO:
        raise ValueError("page mismatch")
    if str(table.get("internal_table_uid") or "") != EXPECTED_UID:
        raise ValueError("UID mismatch")
    if str(table.get("unit_hint") or "") != "million_vnd":
        raise ValueError("unit hint mismatch")

    table_function = table.get("table_function") or {}
    table_purpose = table.get("table_purpose") or {}
    table_section = table.get("table_section") or {}
    if normalize(table_function.get("kind")) != "financial_note":
        raise ValueError("table function mismatch")
    if normalize(table_purpose.get("kind")) != "period_comparison":
        raise ValueError("table purpose mismatch")
    if normalize(table_section.get("kind")) != "liability":
        raise ValueError("table section mismatch")

    headers = list(table.get("headers") or [])
    header_context = normalize(" ".join(str(value or "") for value in headers))
    if "31 thang 12 nam 2019" not in header_context:
        raise ValueError("requested date is not in the table header")
    if "trieu dong" not in header_context or "tong cong" not in header_context:
        raise ValueError("million-VND total header is missing")

    rows = table.get("rows") or []
    if len(rows) <= EXPECTED_ROW_INDEX or not isinstance(rows[EXPECTED_ROW_INDEX], list):
        raise ValueError("expected total row is missing")
    row = rows[EXPECTED_ROW_INDEX]
    if len(row) <= EXPECTED_COLUMN_INDEX:
        raise ValueError("expected total column is missing")
    if canonical_label(row[0]) != "tong cong":
        raise ValueError(f"unexpected total row label: {row[0]!r}")
    current_value = parse_decimal(row[EXPECTED_COLUMN_INDEX])
    if current_value != EXPECTED_VALUE_MILLION_VND:
        raise ValueError(f"unexpected total value: {current_value}")

    char_slice, source_line = verify_coordinates(table, source_line_map)
    bounded_context = normalize(
        " ".join(
            (
                str(table.get("context_before") or ""),
                json.dumps(table.get("context_trace") or {}, ensure_ascii=False),
                char_slice,
            )
        )
    )
    required_markers = (
        "nghia vu no tai chinh",
        "rủi ro thanh khoản".replace("ủ", "u"),
        "qua han",
        "khong xac dinh ky han",
        "tong cong",
    )
    if "nghia vu no tai chinh" not in bounded_context:
        raise ValueError("liability maturity context is missing")
    if "qua han" not in bounded_context or "khong xac dinh ky han" not in bounded_context:
        raise ValueError("maturity headers are missing from bounded context")
    del required_markers

    return {
        "question_id": 261,
        "document_id": EXPECTED_DOCUMENT,
        "ticker": "BVH",
        "scope": "consolidated",
        "report_year": 2019,
        "internal_table_uid": EXPECTED_UID,
        "source_line": source_line,
        "source_path": EXPECTED_SOURCE_PATH,
        "source_sha256": EXPECTED_SOURCE_SHA256,
        "table_sha256": EXPECTED_TABLE_SHA256,
        "local_ordinal": EXPECTED_LOCAL_ORDINAL,
        "page_no": EXPECTED_PAGE_NO,
        "row_index": EXPECTED_ROW_INDEX,
        "column_index": EXPECTED_COLUMN_INDEX,
        "row_label": str(row[0]),
        "header": str(headers[0]),
        "total_header": str(headers[6]),
        "raw_value_million_vnd": str(row[EXPECTED_COLUMN_INDEX]),
        "replayed_value_million_vnd": str(current_value),
        "source_multiplier_to_vnd": "1000000",
        "replayed_value_vnd": str(current_value * Decimal("1000000")),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "table_hash_recomputed": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "financial_note_context_checked": True,
            "liability_section_checked": True,
            "maturity_headers_checked": True,
            "requested_date_header_checked": True,
            "total_row_checked": True,
            "total_column_checked": True,
            "million_vnd_unit_checked": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, default=DEFAULT_ASSET)
    parser.add_argument("--source-line-map", type=Path, default=DEFAULT_SOURCE_LINE_MAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    record = replay(
        args.asset.expanduser().resolve(),
        args.source_line_map.expanduser().resolve(),
    )
    result = {
        "protocol": "vifinqa_independent_financial_liability_q261_replay_v1",
        "question_id": 261,
        "metric": "Tổng cộng nghĩa vụ nợ tài chính",
        "operation": "direct_source_row",
        "population": 1012,
        "records": [record],
        "winner_value_million_vnd": record["replayed_value_million_vnd"],
        "status": "PASS",
        "failures": [],
        "answer_accuracy": "NOT_MEASURED",
        "execution_accuracy": "NOT_MEASURED",
        "official_scorer": "NOT_AVAILABLE",
        "promotion_allowed": False,
        "strict_certificate": False,
        "authority_status": "CANDIDATE_ONLY",
    }
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite replay output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
