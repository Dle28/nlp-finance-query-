#!/usr/bin/env python3
"""Independently replay the MBB Q883 trading-debt-securities maximum.

Q883 asks which of 2022, 2023 and 2025 has the largest balance of debt
securities held for trading.  The source family is the exact ``Chứng khoán
nợ`` row in note 8, ``Chứng khoán kinh doanh``.  The checker deliberately
excludes the similarly named investment-portfolio and listing-status tables.

The checker does not import the argmax route adapter.  Question ID 883 is
tracking metadata only.  PASS is candidate/replay evidence, not a gold label,
strict E2E certificate, leaderboard score, or promotion grant.
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
    2022: "0c124caed4e0beb07f895f45444f9ad92df62446e59af7605e070c53de765ad6",
    2023: "c923005e15b0c9494497aef0f1d0a315eeab7fd6d72b5b11044d53b085187d54",
    2025: "42dfc4a100ecd3e03fe4dfb4b02064a016582d04117c4721d91bbd9d912582e1",
}
EXPECTED_SOURCE_LINE_BY_YEAR = {2022: 1556, 2023: 1477, 2025: 2036}
EXPECTED_LOCAL_ORDINAL_BY_YEAR = {2022: 25, 2023: 27, 2025: 29}
EXPECTED_PAGE_BY_YEAR = {2022: 43, 2023: 45, 2025: 49}
EXPECTED_SOURCE_SHA256_BY_YEAR = {
    2022: "3ee9e2e49aec63aeedd3bbc08a9d064b77486d5d01b5196f1218b20030ca64d3",
    2023: "249803144cdae3dadb09c5defd0cd7c324a4f4564f4d139203c29d8f98bdcb3c",
    2025: "47f2348913b400ea5a6280ff9d93f5a5e409ed45fffdf627c34da73c27d4618f",
}
EXPECTED_TABLE_SHA256_BY_YEAR = {
    2022: "a6fdb2df90cbc9132ac5fd24f51c14d6146cb6036c84c90935d09cdc5f959a2f",
    2023: "40d0d0da3313674fed3aaaf546fac81468116c58f10fd75dde205880a0f937d2",
    2025: "b1928e709ec6197f105d647191c66692846fe1c630c1bca623af3faf747abdcf",
}
EXPECTED_CHAR_COORDINATES_BY_YEAR = {
    2022: (104358, 105306),
    2023: (106943, 107889),
    2025: (124463, 125490),
}
EXPECTED_BYTE_COORDINATES_BY_YEAR = {
    2022: (131463, 132501),
    2023: (134856, 135892),
    2025: (157350, 158473),
}
EXPECTED_RAW_VALUE_BY_YEAR = {
    2022: "4.070.884",
    2023: "44.095.180",
    2025: "4.375.694",
}
EXPECTED_PRIOR_RAW_VALUE_BY_YEAR = {
    2022: "7.243.427",
    2023: "4.070.884",
    2025: "7.719.682",
}
EXPECTED_VALUE_BY_YEAR = {
    2022: Decimal("4070884000000"),
    2023: Decimal("44095180000000"),
    2025: Decimal("4375694000000"),
}
EXPECTED_ROW_INDEX_BY_YEAR = {2022: 1, 2023: 1, 2025: 1}
EXPECTED_DOCUMENT_BY_YEAR = {
    year: f"MBB_financial_statements_{year}_consolidated"
    for year in EXPECTED_UID_BY_YEAR
}
EXPECTED_SOURCE_PATH_BY_YEAR = {
    year: (
        "/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/MBB/"
        f"{year}/MBB_financial_statements_{year}_consolidated/"
        f"MBB_financial_statements_{year}_consolidated_extracted.txt"
    )
    for year in EXPECTED_UID_BY_YEAR
}


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
    uid_to_year = {uid: year for year, uid in EXPECTED_UID_BY_YEAR.items()}
    found: dict[int, dict[str, Any]] = {}
    with asset_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            table = json.loads(line)
            uid = str(table.get("internal_table_uid") or "")
            if uid not in expected:
                continue
            year = uid_to_year[uid]
            if year in found:
                raise ValueError(
                    f"duplicate expected UID at asset line {line_number}: {uid}"
                )
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
) -> tuple[int, str, str]:
    expected_path = EXPECTED_SOURCE_PATH_BY_YEAR[year]
    source_path = str(table.get("source_path") or "")
    if source_path != expected_path:
        raise ValueError(f"{year}: source path mismatch: {source_path!r}")
    raw_bytes = Path(expected_path).read_bytes()
    expected_source_sha = EXPECTED_SOURCE_SHA256_BY_YEAR[year]
    actual_source_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    metadata_source_sha = str(table.get("source_sha256") or "").lower()
    if actual_source_sha != expected_source_sha or metadata_source_sha != expected_source_sha:
        raise ValueError(
            f"{year}: source hash mismatch: expected={expected_source_sha} "
            f"actual={actual_source_sha} metadata={metadata_source_sha}"
        )

    source_text = raw_bytes.decode("utf-8", errors="replace")
    expected_char_start, expected_char_end = EXPECTED_CHAR_COORDINATES_BY_YEAR[year]
    expected_byte_start, expected_byte_end = EXPECTED_BYTE_COORDINATES_BY_YEAR[year]
    char_start = int(table.get("char_start"))
    char_end = int(table.get("char_end"))
    byte_start = int(table.get("byte_start"))
    byte_end = int(table.get("byte_end"))
    if (char_start, char_end) != (expected_char_start, expected_char_end):
        raise ValueError(f"{year}: character coordinates mismatch")
    if (byte_start, byte_end) != (expected_byte_start, expected_byte_end):
        raise ValueError(f"{year}: byte coordinates mismatch")
    if not (0 <= char_start < char_end <= len(source_text)):
        raise ValueError(f"{year}: invalid character coordinates")
    if not (0 <= byte_start < byte_end <= len(raw_bytes)):
        raise ValueError(f"{year}: invalid byte coordinates")
    char_slice = source_text[char_start:char_end]
    byte_slice = raw_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
    if char_slice != byte_slice:
        raise ValueError(f"{year}: byte/character slices disagree")

    expected_table_sha = EXPECTED_TABLE_SHA256_BY_YEAR[year]
    actual_table_sha = hashlib.sha256(char_slice.encode("utf-8")).hexdigest().lower()
    metadata_table_sha = str(table.get("table_sha256") or "").lower()
    if actual_table_sha != expected_table_sha or metadata_table_sha != expected_table_sha:
        raise ValueError(
            f"{year}: table hash mismatch: expected={expected_table_sha} "
            f"actual={actual_table_sha} metadata={metadata_table_sha}"
        )

    uid = str(table.get("internal_table_uid") or "")
    expected_line = EXPECTED_SOURCE_LINE_BY_YEAR[year]
    mapped_line = source_line_map.get(uid)
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(mapped_line) != expected_line or actual_line != expected_line:
        raise ValueError(
            f"{year}: source-line mismatch: map={mapped_line!r} "
            f"actual={actual_line} expected={expected_line}"
        )
    return actual_line, char_slice, source_text


def _trading_debt_row(
    table: Mapping[str, Any], *, year: int
) -> tuple[int, list[Any]]:
    rows = table.get("rows") or []
    expected_index = EXPECTED_ROW_INDEX_BY_YEAR[year]
    if len(rows) <= expected_index or not isinstance(rows[expected_index], list):
        raise ValueError(f"{year}: expected trading-debt row is missing")
    row = rows[expected_index]
    if len(row) < 3 or canonical_label(row[0]) != "chung khoan no":
        raise ValueError(f"{year}: trading-debt row mismatch: {row!r}")

    matching_indices = [
        index
        for index, candidate in enumerate(rows)
        if isinstance(candidate, list)
        and candidate
        and canonical_label(candidate[0]) == "chung khoan no"
    ]
    if matching_indices != [expected_index]:
        raise ValueError(
            f"{year}: trading-debt row is not unique: {matching_indices}"
        )

    context_trace = table.get("context_trace") or {}
    trace_parts = [
        str(table.get("context_before") or ""),
        json.dumps(context_trace, ensure_ascii=False),
    ]
    context = normalize(" ".join(trace_parts))
    if "chung khoan kinh doanh" not in context:
        raise ValueError(f"{year}: trading-securities note context missing")
    if "tinh trang niem yet" in context:
        raise ValueError(f"{year}: listing-status child table selected")
    return expected_index, row


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
    if str(table.get("ticker") or "").upper() != "MBB":
        raise ValueError(f"{year}: ticker mismatch")
    if str(table.get("scope") or "").lower() != "consolidated":
        raise ValueError(f"{year}: scope mismatch")
    if int(table.get("report_year")) != year:
        raise ValueError(f"{year}: report year mismatch")
    if int(table.get("local_ordinal")) != EXPECTED_LOCAL_ORDINAL_BY_YEAR[year]:
        raise ValueError(f"{year}: local ordinal mismatch")
    if int(table.get("page_no")) != EXPECTED_PAGE_BY_YEAR[year]:
        raise ValueError(f"{year}: page mismatch")

    table_function = table.get("table_function") or {}
    table_purpose = table.get("table_purpose") or {}
    raw_kind = normalize(table_function.get("kind"))
    if raw_kind not in {"financial_note", "financial_note_detail"}:
        raise ValueError(f"{year}: unexpected raw table kind: {raw_kind!r}")
    if normalize(table_purpose.get("kind")) != "period_comparison":
        raise ValueError(f"{year}: table purpose mismatch")

    source_line, char_slice, _source_text = verify_coordinates(
        table, year=year, source_line_map=source_line_map
    )
    if "trieu dong" not in canonical_label(" ".join(table.get("headers") or [])):
        raise ValueError(f"{year}: source-unit header is not million VND")
    if "trieu dong" not in canonical_label(char_slice):
        raise ValueError(f"{year}: source slice lacks the million-VND unit")

    headers = list(table.get("headers") or [])
    if len(headers) != 3:
        raise ValueError(f"{year}: unexpected headers: {headers!r}")
    current_header = canonical_label(headers[1])
    prior_header = canonical_label(headers[2])
    if str(year) not in current_header or "31 12" not in current_header:
        raise ValueError(f"{year}: current-period header mismatch: {headers!r}")
    if str(year - 1) not in prior_header or "31 12" not in prior_header:
        raise ValueError(f"{year}: prior-period header mismatch: {headers!r}")

    row_index, row = _trading_debt_row(table, year=year)
    if str(row[1]) != EXPECTED_RAW_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: current raw value mismatch: {row[1]!r}")
    if str(row[2]) != EXPECTED_PRIOR_RAW_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: prior raw value mismatch: {row[2]!r}")
    current_million = parse_decimal(row[1])
    prior_million = parse_decimal(row[2])
    current_vnd = current_million * Decimal("1000000")
    prior_vnd = prior_million * Decimal("1000000")
    if current_vnd != EXPECTED_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: current VND value mismatch: {current_vnd}")

    return {
        "year": year,
        "document_id": str(table["document_id"]),
        "ticker": "MBB",
        "scope": "consolidated",
        "internal_table_uid": uid,
        "source_line": source_line,
        "source_path": str(table["source_path"]),
        "source_sha256": str(table["source_sha256"]),
        "table_sha256": str(table["table_sha256"]),
        "local_ordinal": int(table["local_ordinal"]),
        "page_no": int(table["page_no"]),
        "raw_table_kind": raw_kind,
        "semantic_family": "trading_debt_securities_note",
        "row_index": row_index,
        "column_index": 1,
        "row_label": str(row[0]),
        "current_header": str(headers[1]),
        "prior_header": str(headers[2]),
        "current_raw_value": str(row[1]),
        "prior_raw_value": str(row[2]),
        "source_multiplier": "1000000",
        "source_multiplier_basis": "explicit_million_vnd_table_header",
        "replayed_value_vnd": str(current_vnd),
        "replayed_prior_value_vnd": str(prior_vnd),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "table_hash_recomputed": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "trading_securities_note_context_checked": True,
            "exact_debt_securities_row_checked": True,
            "current_period_column_checked": True,
            "prior_period_column_checked": True,
            "explicit_million_vnd_header_checked": True,
            "same_consolidated_scope_checked": True,
            "same_vnd_multiplier_checked": True,
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
    winners = [
        record["year"] for record, value in zip(records, values) if value == maximum
    ]
    if winners != [2023]:
        raise ValueError(f"unexpected unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_trading_debt_securities_q883_replay_v1",
        "question_id": 883,
        "ticker": "MBB",
        "scope": "consolidated",
        "metric": "Dư nợ chứng khoán kinh doanh nợ",
        "operation": "argmax_period",
        "years": sorted(EXPECTED_UID_BY_YEAR),
        "records": records,
        "winner_year": winners[0],
        "winner_value_vnd": str(maximum),
        "checks": {
            "uid_count": len(records) == 3,
            "source_hashes_checked": True,
            "table_hashes_recomputed": True,
            "source_line_coordinates_checked": True,
            "exact_trading_securities_context_checked": True,
            "exact_debt_securities_rows_checked": True,
            "same_consolidated_scope": len({record["scope"] for record in records}) == 1,
            "same_vnd_multiplier": len(
                {record["source_multiplier"] for record in records}
            )
            == 1,
            "unique_winner": len(winners) == 1,
        },
        "status": "PASS",
        "failures": [],
        "answer_accuracy": "NOT_MEASURED",
        "execution_accuracy": "NOT_MEASURED",
        "official_scorer": "NOT_AVAILABLE",
        "promotion_allowed": False,
        "strict_certificate": False,
        "authority_status": "CANDIDATE_ONLY",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--source-line-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(
        args.asset.expanduser().resolve(),
        args.source_line_map.expanduser().resolve(),
    )
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite replay output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
