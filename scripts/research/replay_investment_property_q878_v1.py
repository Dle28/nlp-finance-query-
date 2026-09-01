#!/usr/bin/env python3
"""Independently replay the KBC Q878 investment-property argmax.

Q878 asks for the year with the largest closing carrying amount of KBC's
investment-property factories.  The generic semantic candidate previously
selected a construction-in-progress row, so this checker binds the source
family before comparing values:

* exact KBC consolidated table UID, report year, and source-line coordinate;
* the ``Bất động sản đầu tư`` note context;
* the exact ``Nhà xưởng`` asset row (including the known 2015 OCR spelling);
* the ``Giá trị còn lại`` -> closing-balance row hierarchy; and
* a bounded raw-source VND declaration with a checked source hash.

This file deliberately does not import the argmax runner.  A PASS is an
independently replayed best-effort research finding, not an official gold
label, strict E2E certificate, or leaderboard score.
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
    2015: "44772ad6a1109359f03de0a84179228c77247245e04c4eb7c356067e30801914",
    2017: "05c1cfeb23cba4e05ecaa6e78105d18e29f16b0051716afb07adec7f7b7fef5c",
    2019: "b04a2dbeea9bb08086ebfc6dee4f94b3da20295a0f8b6b1aac7a179d675e0b8a",
}
EXPECTED_LINE_BY_YEAR = {2015: 1137, 2017: 1024, 2019: 1110}
EXPECTED_VALUE_ROW_BY_YEAR = {2015: 14, 2017: 12, 2019: 14}
EXPECTED_CARRYING_AMOUNT_ROW_BY_YEAR = {2015: 12, 2017: 10, 2019: 12}


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


def source_vnd_multiplier(table: Mapping[str, Any]) -> Decimal:
    source_path = Path(str(table["source_path"]))
    raw_bytes = source_path.read_bytes()
    expected_sha = str(table.get("source_sha256") or "").lower()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if expected_sha and actual_sha != expected_sha:
        raise ValueError(
            f"source hash mismatch for {source_path}: "
            f"expected={expected_sha} actual={actual_sha}"
        )

    char_start = int(table["char_start"])
    source_text = raw_bytes.decode("utf-8", errors="replace")
    if char_start < 0 or char_start > len(source_text):
        raise ValueError(f"invalid char_start={char_start} for {source_path}")

    # The table starts at the source coordinate and carries its unit in the
    # first row.  Keep the read bounded so a distant note cannot provide the
    # unit declaration.
    window = source_text[char_start : char_start + 1200]
    if "don vi tinh" not in normalize(window) or "vnd" not in normalize(window):
        raise ValueError(f"missing bounded VND declaration: {source_path}")
    if any(scale in normalize(window) for scale in ("trieu dong", "nghin dong", "ngan dong")):
        raise ValueError(f"non-VND scale in unit declaration: {source_path}")
    return Decimal("1")


def _row_label(row: Any) -> str:
    if not isinstance(row, list):
        return ""
    labels: list[str] = []
    for cell in row[:2]:
        if parseable(cell):
            continue
        if str(cell).strip():
            labels.append(str(cell))
    return normalize(" ".join(labels))


def parseable(value: Any) -> bool:
    try:
        parse_decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return True


def _asset_row_matches(label: str) -> bool:
    # Accent removal maps both the 2015 OCR ``Nhà xuống`` and the later
    # ``Nhà xưởng`` to ``nha xuong``.  The rest of the phrase is retained to
    # prevent a generic factory/construction row from entering the family.
    return label == (
        "nha xuong (bao gom chi phi phat trien dat va co so ha tang)"
    )


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    tables = load_tables(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []

    for year in sorted(EXPECTED_UID_BY_YEAR):
        uid = EXPECTED_UID_BY_YEAR[year]
        table = tables[uid]
        if str(table.get("ticker") or "").upper() != "KBC":
            raise ValueError(f"{year}: ticker mismatch")
        if str(table.get("scope") or "").lower() != "consolidated":
            raise ValueError(f"{year}: scope mismatch")
        if int(table.get("report_year")) != year:
            raise ValueError(f"{year}: report_year mismatch")
        if source_line_map.get(uid) != EXPECTED_LINE_BY_YEAR[year]:
            raise ValueError(f"{year}: source-line map mismatch")

        table_function = table.get("table_function") or {}
        if normalize(table_function.get("kind")) != "financial_note":
            raise ValueError(f"{year}: table kind mismatch")
        table_purpose = table.get("table_purpose") or {}
        if normalize(table_purpose.get("kind")) != "movement_schedule":
            raise ValueError(f"{year}: table purpose mismatch")

        context = normalize(table.get("context_before") or "")
        if "bat dong san dau tu" not in context:
            raise ValueError(f"{year}: investment-property note context missing")

        rows = table.get("rows") or []
        asset_rows = [
            (index, row)
            for index, row in enumerate(rows)
            if isinstance(row, list) and _asset_row_matches(_row_label(row))
        ]
        if len(asset_rows) != 1:
            raise ValueError(f"{year}: expected one exact factory row, got {len(asset_rows)}")
        asset_row_index, _ = asset_rows[0]
        if asset_row_index != 1:
            raise ValueError(f"{year}: factory row index drifted: {asset_row_index}")

        carrying_index = EXPECTED_CARRYING_AMOUNT_ROW_BY_YEAR[year]
        value_index = EXPECTED_VALUE_ROW_BY_YEAR[year]
        if carrying_index != value_index - 2:
            raise ValueError(f"{year}: invalid expected hierarchy contract")
        if carrying_index >= len(rows) or value_index >= len(rows):
            raise ValueError(f"{year}: expected value row is missing")
        if normalize(rows[carrying_index][0]) != "gia tri con lai:":
            raise ValueError(f"{year}: carrying-amount section mismatch")
        value_label = normalize(rows[value_index][0])
        if value_label not in {"so du cuoi nam", "so cuoi nam"}:
            raise ValueError(f"{year}: closing-balance label mismatch: {rows[value_index]!r}")
        if len(rows[value_index]) <= 1:
            raise ValueError(f"{year}: closing-balance numeric cell missing")

        headers = list(table.get("headers") or table.get("column_labels") or [])
        header_text = normalize(" ".join(str(value) for value in headers))
        if "don vi tinh" not in header_text or "vnd" not in header_text:
            raise ValueError(f"{year}: table header does not declare VND: {headers!r}")

        multiplier = source_vnd_multiplier(table)
        raw_value = rows[value_index][1]
        value_vnd = parse_decimal(raw_value) * multiplier
        records.append(
            {
                "year": year,
                "internal_table_uid": uid,
                "source_line": EXPECTED_LINE_BY_YEAR[year],
                "asset_row_index": asset_row_index,
                "carrying_amount_section_row_index": carrying_index,
                "value_row_index": value_index,
                "column_index": 1,
                "row_label": str(rows[value_index][0]),
                "asset_row_label": str(rows[asset_row_index][1]),
                "raw_value": str(raw_value),
                "source_multiplier": str(multiplier),
                "replayed_value_vnd": str(value_vnd),
                "source_sha256": table.get("source_sha256"),
                "table_function_kind": normalize(table_function.get("kind")),
                "table_purpose_kind": normalize(table_purpose.get("kind")),
            }
        )

    values = [Decimal(record["replayed_value_vnd"]) for record in records]
    maximum = max(values)
    winners = [
        record["year"]
        for record, value in zip(records, values)
        if value == maximum
    ]
    if len(winners) != 1:
        raise ValueError(f"non-unique maximum: {winners}")

    return {
        "protocol": "vifinqa_independent_investment_property_q878_replay_v1",
        "question_id": 878,
        "ticker": "KBC",
        "scope": "consolidated",
        "metric": "Giá trị còn lại cuối năm của bất động sản đầu tư (nhà xưởng)",
        "operation": "argmax_period",
        "years": sorted(EXPECTED_UID_BY_YEAR),
        "records": records,
        "winner_year": winners[0],
        "winner_value_vnd": str(maximum),
        "checks": {
            "uid_count": len(records) == 3,
            "source_hashes_checked": all(record["source_sha256"] for record in records),
            "source_line_coordinates_checked": True,
            "exact_investment_property_context_checked": True,
            "exact_factory_asset_row_checked": True,
            "exact_carrying_amount_hierarchy_checked": True,
            "same_financial_note_kind": len(
                {record["table_function_kind"] for record in records}
            )
            == 1,
            "same_consolidated_scope": True,
            "same_vnd_multiplier": len(
                {record["source_multiplier"] for record in records}
            )
            == 1,
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
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
