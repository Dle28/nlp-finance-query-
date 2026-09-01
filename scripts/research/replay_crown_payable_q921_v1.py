#!/usr/bin/env python3
"""Independently replay the SAB Q921 Crown-payable period maximum.

The checker intentionally does not import the argmax route adapter.  It
verifies the exact separate-scope source tables for 2019, 2021 and 2025, the
related-party payable context, the Crown row, the current VND column, source
hashes and byte/character/source-line coordinates, then recomputes the unique
maximum with Decimal values.  A PASS is a best-effort research finding, not a
gold label, strict E2E certificate or leaderboard score.
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
    2019: "cf23e7edd543e8dd72a735a1feffafb7497de98228dea855711f15d282ce65da",
    2021: "cbdb64e01520979c016e5c15d4bb7a50689c0a3f36c26ff8adf9d926fc63b1ea",
    2025: "5ee8d1df2c3298dea44b40109b80b93219b8c7f6bd77791f57909991d091fa4f",
}
EXPECTED_SOURCE_LINE_BY_YEAR = {2019: 1154, 2021: 1201, 2025: 1337}
EXPECTED_ROW_INDEX_BY_YEAR = {2019: 3, 2021: 3, 2025: 3}
EXPECTED_SOURCE_SHA256_BY_YEAR = {
    2019: "7f052c7a4d27b4b8cbae893366c765f141165e5be060079de34c5ceae5aa7de9",
    2021: "06651ecf93a1a3081f4d16cd80018ee536111a8df3b141f118ab9f414ab8bb6b",
    2025: "4a3299c9dfb2387fa067b1f58c2ea9c8e651a3f09d350f7b2014897b1be60077",
}
EXPECTED_TABLE_SHA256_BY_YEAR = {
    2019: "82aa68fb57984cb47abde1c0d285d8d545bcdab866f2d72592fe46e5cb5b7a8d",
    2021: "4a7942a484d9235f900e63e57a3c0465891db08a64b92056ce5723fde8f5c065",
    2025: "4944063fa1750fcb5788aedb808a4c3f66a96c326e58536354125b0237030fa1",
}
EXPECTED_DOCUMENT_BY_YEAR = {
    2019: "SAB_financial_statements_2019_separate",
    2021: "SAB_financial_statements_2021_separate",
    2025: "SAB_financial_statements_2025_separate",
}
EXPECTED_SOURCE_PATH_BY_YEAR = {
    year: f"/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/SAB/{year}/{document}/{document}_extracted.txt"
    for year, document in EXPECTED_DOCUMENT_BY_YEAR.items()
}
EXPECTED_VALUE_BY_YEAR = {
    2019: Decimal("226245964160"),
    2021: Decimal("559509431031"),
    2025: Decimal("404695685526"),
}
EXPECTED_PRIOR_VALUE_BY_YEAR = {
    2019: Decimal("217001205735"),
    2021: Decimal("210405537315"),
    2025: Decimal("446313320167"),
}
EXPECTED_COUNTERPARTY = "cong ty lien doanh tnhh crown sai gon"


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


def load_tables(asset_path: Path) -> dict[int, dict[str, Any]]:
    expected = set(EXPECTED_UID_BY_YEAR.values())
    found: dict[int, dict[str, Any]] = {}
    uid_to_year = {uid: year for year, uid in EXPECTED_UID_BY_YEAR.items()}
    with asset_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            table = json.loads(line)
            uid = str(table.get("internal_table_uid") or "")
            if uid not in expected:
                continue
            year = uid_to_year[uid]
            if year in found:
                raise ValueError(f"duplicate expected UID at asset line {line_number}: {uid}")
            found[year] = table
    missing = sorted(set(EXPECTED_UID_BY_YEAR) - set(found))
    if missing:
        raise ValueError(f"missing expected years: {missing}")
    return found


def verify_coordinates(
    table: Mapping[str, Any],
    *,
    year: int,
    source_line_map: Mapping[str, Any],
) -> tuple[int, str]:
    source_path = Path(str(table["source_path"]))
    if str(source_path) != EXPECTED_SOURCE_PATH_BY_YEAR[year]:
        raise ValueError(f"{year}: source path mismatch")
    raw_bytes = source_path.read_bytes()
    expected_sha = EXPECTED_SOURCE_SHA256_BY_YEAR[year]
    actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if actual_sha != expected_sha or str(table.get("source_sha256") or "").lower() != expected_sha:
        raise ValueError(f"{year}: source hash mismatch: expected={expected_sha} actual={actual_sha}")

    source_text = raw_bytes.decode("utf-8", errors="replace")
    char_start = int(table["char_start"])
    char_end = int(table["char_end"])
    byte_start = int(table["byte_start"])
    byte_end = int(table["byte_end"])
    if not (0 <= char_start < char_end <= len(source_text)):
        raise ValueError(f"{year}: invalid character coordinates")
    if not (0 <= byte_start < byte_end <= len(raw_bytes)):
        raise ValueError(f"{year}: invalid byte coordinates")
    char_slice = source_text[char_start:char_end]
    byte_slice = raw_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
    if char_slice != byte_slice:
        raise ValueError(f"{year}: byte/character slices disagree")
    expected_line = EXPECTED_SOURCE_LINE_BY_YEAR[year]
    uid = str(table["internal_table_uid"])
    mapped_line = source_line_map.get(uid)
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(mapped_line) != expected_line or actual_line != expected_line:
        raise ValueError(
            f"{year}: source-line mismatch: map={mapped_line!r} actual={actual_line} expected={expected_line}"
        )
    normalized_source = canonical_label(char_slice)
    if "vnd" not in normalized_source or EXPECTED_COUNTERPARTY not in normalized_source:
        raise ValueError(f"{year}: bounded source slice lacks VND/Crown evidence")
    for token in ("31", str(year), "1", "1"):
        if token not in normalized_source:
            raise ValueError(f"{year}: expected period token missing from source slice: {token}")
    return actual_line, char_slice


def verify_year(
    table: Mapping[str, Any],
    *,
    year: int,
    source_line_map: Mapping[str, Any],
) -> dict[str, Any]:
    uid = EXPECTED_UID_BY_YEAR[year]
    if str(table.get("internal_table_uid") or "") != uid:
        raise ValueError(f"{year}: UID mismatch")
    if str(table.get("document_id") or "") != EXPECTED_DOCUMENT_BY_YEAR[year]:
        raise ValueError(f"{year}: document mismatch")
    if str(table.get("ticker") or "").upper() != "SAB":
        raise ValueError(f"{year}: ticker mismatch")
    if str(table.get("scope") or "").lower() != "separate":
        raise ValueError(f"{year}: scope mismatch")
    if int(table.get("report_year")) != year:
        raise ValueError(f"{year}: report year mismatch")
    expected_local_ordinal = {2019: 53, 2021: 42, 2025: 45}[year]
    expected_page = {2019: 45, 2021: 45, 2025: 47}[year]
    if int(table.get("local_ordinal")) != expected_local_ordinal:
        raise ValueError(f"{year}: local ordinal mismatch")
    if int(table.get("page_no")) != expected_page:
        raise ValueError(f"{year}: page mismatch")
    if str(table.get("table_sha256") or "").lower() != EXPECTED_TABLE_SHA256_BY_YEAR[year]:
        raise ValueError(f"{year}: table hash metadata mismatch")
    function = table.get("table_function") or {}
    purpose = table.get("table_purpose") or {}
    if str(function.get("kind") or "") != "related_party_schedule":
        raise ValueError(f"{year}: table kind mismatch")
    if str(purpose.get("kind") or "") != "relationship_detail":
        raise ValueError(f"{year}: table purpose mismatch")
    context = normalize(table.get("context_before") or "")
    if "phai tra nguoi ban" not in context or "ben lien quan" not in context:
        raise ValueError(f"{year}: related-party payable context missing")

    source_line, _ = verify_coordinates(table, year=year, source_line_map=source_line_map)
    headers = list(table.get("headers") or [])
    if len(headers) != 3:
        raise ValueError(f"{year}: unexpected headers: {headers!r}")
    if f"31/12/{year}" not in normalize(headers[1]) or "vnd" not in normalize(headers[1]):
        raise ValueError(f"{year}: current VND header mismatch: {headers!r}")
    if "1/1/" not in normalize(headers[2]) or "vnd" not in normalize(headers[2]):
        raise ValueError(f"{year}: prior VND header mismatch: {headers!r}")

    rows = table.get("rows") or []
    row_index = EXPECTED_ROW_INDEX_BY_YEAR[year]
    if len(rows) <= row_index or not isinstance(rows[row_index], list):
        raise ValueError(f"{year}: Crown row missing")
    row = rows[row_index]
    if canonical_label(row[0]) != EXPECTED_COUNTERPARTY:
        raise ValueError(f"{year}: counterparty mismatch: {row!r}")
    current = parse_decimal(row[1])
    prior = parse_decimal(row[2])
    if current != EXPECTED_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: current value mismatch: {current}")
    if prior != EXPECTED_PRIOR_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: prior value mismatch: {prior}")
    return {
        "year": year,
        "document_id": str(table["document_id"]),
        "internal_table_uid": uid,
        "source_line": source_line,
        "source_path": str(table["source_path"]),
        "source_sha256": str(table["source_sha256"]),
        "table_sha256": str(table["table_sha256"]),
        "local_ordinal": int(table["local_ordinal"]),
        "page_no": int(table["page_no"]),
        "row_index": row_index,
        "column_index": 1,
        "row_label": str(row[0]),
        "current_header": str(headers[1]),
        "prior_header": str(headers[2]),
        "current_raw_value": str(current),
        "prior_raw_value": str(prior),
        "source_multiplier": "1",
        "replayed_value_vnd": str(current),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "related_party_payable_context_checked": True,
            "exact_crown_row_checked": True,
            "current_vnd_column_checked": True,
            "prior_vnd_column_checked": True,
            "separate_scope_and_year_checked": True,
        },
    }


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    tables = load_tables(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    if not isinstance(source_line_map, dict):
        raise ValueError("source-line map must be a JSON object")
    records = [
        verify_year(tables[year], year=year, source_line_map=source_line_map)
        for year in sorted(EXPECTED_UID_BY_YEAR)
    ]
    values = [Decimal(record["replayed_value_vnd"]) for record in records]
    maximum = max(values)
    winners = [record["year"] for record, value in zip(records, values) if value == maximum]
    if winners != [2021]:
        raise ValueError(f"unexpected unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_crown_payable_q921_replay_v1",
        "question_id": 921,
        "ticker": "SAB",
        "scope": "separate",
        "metric": "Số dư cuối kỳ phải trả cho Công ty Liên doanh TNHH Crown Sài Gòn",
        "operation": "argmax_period",
        "years": sorted(EXPECTED_UID_BY_YEAR),
        "records": records,
        "winner_year": winners[0],
        "winner_value_vnd": str(maximum),
        "checks": {
            "uid_count": len(records) == 3,
            "source_hashes_checked": True,
            "source_line_coordinates_checked": True,
            "exact_related_party_payable_context_checked": True,
            "exact_crown_row_checked": True,
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
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
