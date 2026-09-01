#!/usr/bin/env python3
"""Independently replay the STB Q989 accrued-interest period candidates.

The argmax runner sees different row labels in the three report editions.  This
checker accepts the alias only inside the exact ``Các khoản lãi, phí phải thu``
source disclosure, with fixed source UIDs, coordinates, consolidated scope,
and a declared million-VND header.  It deliberately does not import the
argmax adapter, so a successful result is still a best-effort research finding
and not an official gold-label or strict E2E certificate.
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
    2017: "2132330cc2677f1f38f4850d8a6ba4c33ae3a01bce1f280f31db65ebc8bcd057",
    2022: "595d2481283d87757ea5d2cd7c8c72969793d84e0074bdc4c875fe0528b0db87",
    2024: "04653e89a874343aaa45cf7cd99810786e8b5cabff6e7fc8170efec119d77321",
}
EXPECTED_LINE_BY_YEAR = {2017: 1554, 2022: 1678, 2024: 2054}
EXPECTED_ROW_LABEL_BY_YEAR = {
    2017: "lai tu cho vay khach hang (i)",
    2022: "lai tu cho vay khach hang (*)",
    2024: "lai du thu tu cho vay khach hang",
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
    headers = " ".join(str(value) for value in table.get("headers") or [])
    normalized_headers = normalize(headers)
    if "trieu dong" not in normalized_headers and "trieu vnd" not in normalized_headers:
        raise ValueError(f"missing million-VND header: {headers!r}")
    return Decimal("1000000")


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
    structured_kinds: set[str] = set()
    for year in sorted(EXPECTED_UID_BY_YEAR):
        uid = EXPECTED_UID_BY_YEAR[year]
        table = tables[uid]
        if str(table.get("ticker") or "").upper() != "STB":
            raise ValueError(f"{year}: ticker mismatch")
        if str(table.get("scope") or "").lower() != "consolidated":
            raise ValueError(f"{year}: scope mismatch")
        if int(table.get("report_year")) != year:
            raise ValueError(f"{year}: report_year mismatch")
        context = normalize(table.get("context_before") or "")
        if "cac khoan lai" not in context or "phi phai thu" not in context:
            raise ValueError(f"{year}: accrued-interest disclosure context missing")
        table_kind = normalize((table.get("table_function") or {}).get("kind"))
        structured_kinds.add(table_kind)
        if normalize((table.get("table_purpose") or {}).get("kind")) != "period_comparison":
            raise ValueError(f"{year}: table is not a period comparison")
        if source_line_map.get(uid) != EXPECTED_LINE_BY_YEAR[year]:
            raise ValueError(f"{year}: source-line map mismatch")

        expected_label = EXPECTED_ROW_LABEL_BY_YEAR[year]
        matches = []
        for row_index, row in enumerate(table.get("rows") or []):
            if not isinstance(row, list) or len(row) < 2:
                continue
            if normalize(row[0]) == expected_label:
                matches.append((row_index, row))
        if len(matches) != 1:
            raise ValueError(
                f"{year}: expected one exact accrued-interest alias row, got {len(matches)}"
            )
        row_index, row = matches[0]

        headers = list(table.get("headers") or [])
        if len(headers) < 2:
            raise ValueError(f"{year}: missing source headers")
        current_header = normalize(headers[1])
        if "so cuoi nam" not in current_header and f"31/12/{year}" not in current_header:
            raise ValueError(f"{year}: current-year header is not exact: {headers!r}")
        raw_value = row[1]
        source_multiplier = source_unit_multiplier(table)
        value_million_vnd = parse_decimal(raw_value)
        value_vnd = value_million_vnd * source_multiplier
        records.append(
            {
                "year": year,
                "internal_table_uid": uid,
                "source_line": EXPECTED_LINE_BY_YEAR[year],
                "row_index": row_index,
                "column_index": 1,
                "row_label": str(row[0]),
                "raw_value": str(raw_value),
                "comparison_unit": "million_vnd",
                "source_to_vnd_multiplier": str(source_multiplier),
                "replayed_value_million_vnd": str(value_million_vnd),
                "replayed_value_vnd": str(value_vnd),
                "source_sha256": table.get("source_sha256"),
                "table_function_kind": table_kind,
            }
        )

    values = [Decimal(record["replayed_value_million_vnd"]) for record in records]
    maximum = max(values)
    winners = [
        record["year"]
        for record, value in zip(records, values)
        if value == maximum
    ]
    if len(winners) != 1:
        raise ValueError(f"non-unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_accrued_interest_q989_replay_v1",
        "question_id": 989,
        "ticker": "STB",
        "scope": "consolidated",
        "metric": "Số dư lãi dự thu từ cho vay khách hàng cuối kỳ",
        "operation": "argmax_period",
        "years": sorted(EXPECTED_UID_BY_YEAR),
        "records": records,
        "winner_year": winners[0],
        "winner_value_million_vnd": str(maximum),
        "checks": {
            "uid_count": len(records) == 3,
            "source_hashes_checked": all(record["source_sha256"] for record in records),
            "source_line_coordinates_checked": True,
            "exact_alias_row_checked": True,
            "same_accrued_interest_disclosure_context": True,
            "same_consolidated_scope": True,
            "same_period_comparison": True,
            "same_million_vnd_unit": len(
                {record["comparison_unit"] for record in records}
            )
            == 1,
            "unique_winner": len(winners) == 1,
        },
        "structured_table_kinds_observed": sorted(structured_kinds),
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
