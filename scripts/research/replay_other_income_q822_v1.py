#!/usr/bin/env python3
"""Independently replay the source cells for the ASM Q822 argmax candidate.

This checker intentionally does not import the argmax adapter.  It verifies
the exact table UID, source hash, source-line coordinate, row code/label,
current-year column, VND declaration, and unique maximum before producing a
small audit JSON.  The result is still a best-effort research finding: it is
not an official gold-label score or a strict E2E certificate.
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


EXPECTED_UID_BY_YEAR = {
    2016: "e3274a1dd38e92184fb11cebf01203117d28070d0210a73eca55b8ccaa0cae93",
    2021: "c8ce46c8081bc9c89f4c8707c06f3bd3e4918871823f2c0d8b17c6c58af0d47a",
    2022: "eef4021e58ec9e41d2ce071b17860bc93870a8882a50fd083ec10a08de7a1ca1",
    2024: "d4b8fe40b671e47d1710c6e53494c9400607b827cfd94e26ced634f67b46648e",
}
EXPECTED_LINE_BY_YEAR = {
    2016: 366,
    2021: 408,
    2022: 353,
    2024: 421,
}


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


def source_unit_multiplier(table: Mapping[str, Any]) -> Decimal:
    source_path = Path(str(table["source_path"]))
    raw_bytes = source_path.read_bytes()
    expected_sha = str(table.get("source_sha256") or "").lower()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if expected_sha and actual_sha != expected_sha:
        raise ValueError(
            f"source hash mismatch for {source_path}: "
            f"expected={expected_sha} actual={actual_sha}"
        )
    source_text = raw_bytes.decode("utf-8", errors="replace")
    char_start = int(table["char_start"])
    if char_start < 0 or char_start > len(source_text):
        raise ValueError(f"invalid char_start={char_start} for {source_path}")
    window = source_text[max(0, char_start - 4000) : char_start]
    for line in window.splitlines():
        line_norm = normalize(line)
        if "don vi" in line_norm and "vnd" in line_norm:
            if any(scale in line_norm for scale in ("trieu", "nghin", "ngan")):
                raise ValueError(f"non-VND scale in unit declaration: {line!r}")
            return Decimal("1")
    raise ValueError(f"no bounded VND declaration before table: {source_path}")


def load_tables(asset_path: Path) -> dict[str, dict[str, Any]]:
    expected = set(EXPECTED_UID_BY_YEAR.values())
    found: dict[str, dict[str, Any]] = {}
    with asset_path.open(encoding="utf-8") as handle:
        for line in handle:
            table = json.loads(line)
            uid = str(table.get("internal_table_uid") or "")
            if uid in expected:
                found[uid] = table
    missing = sorted(expected - set(found))
    if missing:
        raise ValueError(f"missing expected table UIDs: {missing}")
    return found


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    tables = load_tables(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for year in sorted(EXPECTED_UID_BY_YEAR):
        uid = EXPECTED_UID_BY_YEAR[year]
        table = tables[uid]
        if str(table.get("ticker") or "").upper() != "ASM":
            raise ValueError(f"{year}: ticker mismatch")
        if str(table.get("scope") or "").lower() != "separate":
            raise ValueError(f"{year}: scope mismatch")
        if int(table.get("report_year")) != year:
            raise ValueError(f"{year}: report_year mismatch")
        if normalize((table.get("table_function") or {}).get("kind")) != (
            "income_statement"
        ):
            raise ValueError(f"{year}: table kind mismatch")
        if source_line_map.get(uid) != EXPECTED_LINE_BY_YEAR[year]:
            raise ValueError(f"{year}: source-line map mismatch")

        matches = []
        for row_index, row in enumerate(table.get("rows") or []):
            if not isinstance(row, list) or len(row) < 2:
                continue
            if str(row[0]).strip() == "31" and "thu nhap khac" in normalize(row[1]):
                matches.append((row_index, row))
        if len(matches) != 1:
            raise ValueError(f"{year}: expected one exact code-31 row, got {len(matches)}")
        row_index, row = matches[0]

        headers = list(table.get("headers") or table.get("column_labels") or [])
        current_columns = [
            index
            for index, header in enumerate(headers)
            if index > 0
            and (str(year) in normalize(header) or "nam nay" in normalize(header))
        ]
        if len(current_columns) != 1:
            raise ValueError(f"{year}: current-year header is ambiguous: {headers!r}")
        column_index = current_columns[0]
        multiplier = source_unit_multiplier(table)
        raw_value = row[column_index]
        value = parse_decimal(raw_value) * multiplier
        records.append(
            {
                "year": year,
                "internal_table_uid": uid,
                "source_line": EXPECTED_LINE_BY_YEAR[year],
                "row_index": row_index,
                "column_index": column_index,
                "row_label": str(row[1]),
                "raw_value": str(raw_value),
                "source_multiplier": str(multiplier),
                "replayed_value": str(value),
                "source_sha256": table.get("source_sha256"),
            }
        )

    values = [Decimal(record["replayed_value"]) for record in records]
    maximum = max(values)
    winners = [
        record["year"]
        for record, value in zip(records, values)
        if value == maximum
    ]
    if len(winners) != 1:
        raise ValueError(f"non-unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_other_income_q822_replay_v1",
        "question_id": 822,
        "ticker": "ASM",
        "scope": "separate",
        "metric": "Thu nhập khác",
        "operation": "argmax_period",
        "years": sorted(EXPECTED_UID_BY_YEAR),
        "records": records,
        "winner_year": winners[0],
        "winner_value": str(maximum),
        "checks": {
            "uid_count": len(records) == 4,
            "source_hashes_checked": all(record["source_sha256"] for record in records),
            "source_line_coordinates_checked": True,
            "exact_code_31_row_checked": True,
            "same_income_statement_kind": True,
            "same_separate_scope": True,
            "same_vnd_multiplier": len({record["source_multiplier"] for record in records}) == 1,
            "unique_winner": len(winners) == 1,
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
