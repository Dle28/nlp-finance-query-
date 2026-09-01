#!/usr/bin/env python3
"""Independently replay the HBC Q324 short-loan provision total.

This checker intentionally does not import the loan-provision route adapter.
It verifies the exact V2 table UID, source hash, byte/character coordinates,
source-line coordinate, titled schedule, current-period VND column, three
detail rows and the Decimal checksum for the final total.  The answer is the
source total converted from VND to billion VND; no value is inferred from a
retrieval score or a learned candidate.

The result is a best-effort research finding, not an official gold label or a
strict E2E certificate.
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


EXPECTED_UID = "4bee72b67b57d131a6edd3f4f700cc5407054894694b64c05bbb38db6f434d79"
EXPECTED_SOURCE_LINE = 1197
EXPECTED_TOTAL = Decimal("80864684721")
EXPECTED_CHILDREN = [
    Decimal("75075867681"),
    Decimal("1429181347"),
    Decimal("4359635693"),
]


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("đ", "d").replace("√", "v")
    return re.sub(r"\s+", " ", text).strip()


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


def verify_coordinates(table: Mapping[str, Any], source_line_map: Mapping[str, Any]) -> tuple[int, str]:
    source_path = Path(str(table["source_path"]))
    raw_bytes = source_path.read_bytes()
    expected_sha = str(table.get("source_sha256") or "").lower()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if not expected_sha or actual_sha != expected_sha:
        raise ValueError(
            f"source hash mismatch: expected={expected_sha!r} actual={actual_sha!r}"
        )

    source_text = raw_bytes.decode("utf-8", errors="replace")
    char_start = int(table["char_start"])
    char_end = int(table["char_end"])
    byte_start = int(table["byte_start"])
    byte_end = int(table["byte_end"])
    if not (0 <= char_start < char_end <= len(source_text)):
        raise ValueError("invalid character coordinates")
    if not (0 <= byte_start < byte_end <= len(raw_bytes)):
        raise ValueError("invalid byte coordinates")
    byte_slice = raw_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
    char_slice = source_text[char_start:char_end]
    if byte_slice != char_slice:
        raise ValueError("byte and character table slices disagree")

    mapped_line = source_line_map.get(EXPECTED_UID)
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(mapped_line) != EXPECTED_SOURCE_LINE or actual_line != EXPECTED_SOURCE_LINE:
        raise ValueError(
            f"source-line mismatch: map={mapped_line!r} actual={actual_line} "
            f"expected={EXPECTED_SOURCE_LINE}"
        )
    return actual_line, char_slice


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    table = load_table(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    if not isinstance(source_line_map, dict):
        raise ValueError("source-line map must be a JSON object")

    if str(table.get("ticker") or "").upper() != "HBC":
        raise ValueError("ticker mismatch")
    if str(table.get("scope") or "").lower() != "separate":
        raise ValueError("scope mismatch")
    if int(table.get("report_year")) != 2024:
        raise ValueError("report year mismatch")
    if str((table.get("table_function") or {}).get("kind") or "").lower() != "financial_data_schedule":
        raise ValueError("table kind mismatch")

    source_line, source_slice = verify_coordinates(table, source_line_map)
    normalized_source = normalize(source_slice)
    title = "chi tiet du phong cac khoan cho vay ngan han"
    if title not in normalized_source:
        raise ValueError("bounded short-loan provision title missing from source slice")

    rows = table.get("rows") or []
    headers = list(table.get("headers") or [])
    if len(headers) < 3 or normalize(headers[0]) != title:
        raise ValueError(f"unexpected schedule headers: {headers!r}")
    current_columns = [
        index
        for index, header in enumerate(headers)
        if index > 0 and "31/12/2024" in normalize(header)
    ]
    if len(current_columns) != 1:
        raise ValueError(f"ambiguous current-period column: {headers!r}")
    column_index = current_columns[0]
    if "vnd" not in normalize(headers[column_index]):
        raise ValueError("current-period header does not declare VND")

    if len(rows) != 5 or normalize(rows[0][0]) != title:
        raise ValueError("unexpected short-loan schedule shape")
    children = []
    for row_index in (1, 2, 3):
        row = rows[row_index]
        if not isinstance(row, list) or len(row) <= column_index:
            raise ValueError(f"invalid detail row at index {row_index}")
        raw = str(row[column_index]).strip()
        value = parse_decimal(raw)
        children.append(value)
        if raw not in source_slice:
            raise ValueError(f"detail value {raw!r} missing from bounded source slice")

    total_row_index = 4
    total_row = rows[total_row_index]
    if not isinstance(total_row, list) or len(total_row) <= column_index:
        raise ValueError("invalid final total row")
    if str(total_row[0]).strip():
        raise ValueError("final checksum row unexpectedly has a label")
    total_raw = str(total_row[column_index]).strip()
    total = parse_decimal(total_raw)
    if children != EXPECTED_CHILDREN:
        raise ValueError(f"unexpected detail vector: {children!r}")
    if sum(children, Decimal(0)) != total or total != EXPECTED_TOTAL:
        raise ValueError(
            f"checksum mismatch: children={sum(children, Decimal(0))} total={total}"
        )
    if total_raw not in source_slice:
        raise ValueError("total value missing from bounded source slice")

    answer = total / Decimal("1000000000")
    return {
        "protocol": "vifinqa_independent_loan_provision_q324_replay_v1",
        "question_id": 324,
        "ticker": "HBC",
        "scope": "separate",
        "report_year": 2024,
        "metric": "Dự phòng các khoản cho vay ngắn hạn",
        "operation": "checked_short_loan_total",
        "internal_table_uid": EXPECTED_UID,
        "source_line": source_line,
        "source_path": str(table["source_path"]),
        "source_sha256": str(table["source_sha256"]),
        "table_sha256": str(table.get("table_sha256") or ""),
        "row_index": total_row_index,
        "column_index": column_index,
        "row_label": str(total_row[0]),
        "header": str(headers[column_index]),
        "detail_raw_values": [str(value) for value in children],
        "total_raw_value": str(total),
        "source_multiplier": "1",
        "requested_output_divisor": "1000000000",
        "answer_decimal": str(answer),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "exact_titled_schedule_checked": True,
            "current_vnd_column_checked": True,
            "three_detail_rows_checked": True,
            "decimal_checksum_checked": True,
            "same_scope_and_year_checked": True,
        },
        "status": "PASS",
        "promotion_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--source-line-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.asset, args.source_line_map)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
