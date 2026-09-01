#!/usr/bin/env python3
"""Independently replay the HND Q884 total-liabilities argmax.

Q884 asks for the year with the largest HND total liabilities among 2016,
2017, 2018, 2021 and 2022.  The previous candidate selected a cash-flow
change row.  This checker binds the source family before comparing values:

* exact HND balance-sheet table UID and report year;
* balance-sheet row code 300 (``Nợ phải trả``), including the observed OCR
  spelling ``NỘ PHẢI TRẢ``;
* current 31/12 VND column, rather than the prior-period comparison column;
* source/table hashes and byte/character/source-line coordinates; and
* Decimal replay of the unique maximum.

The checker deliberately does not import the argmax route adapter.  PASS is a
best-effort research finding, not a gold label, strict E2E certificate, or
leaderboard score.
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
    2016: "816b28de52fedd2c6c296459857a6f3b3ca47898360df40aff98db475647a400",
    2017: "967139f4e1ab3dfea4d491bb5ccbc7b5a3db063bbced4c65bc89c8ec12f6526f",
    2018: "cad79a5aa3e928fa9f72baa5edd18e832c7add2ddf22b4d3285f49b256e7f56d",
    2021: "c4e6aa23346af7c3fe8cc788ff768f2c51220dbae08471fd5baeb19f4e100769",
    2022: "5cbaf82ecb92da8cc802145e4da1533060f8eb2685eaaae85fd42a4b99418d5d",
}
EXPECTED_SOURCE_LINE_BY_YEAR = {
    2016: 141,
    2017: 159,
    2018: 139,
    2021: 222,
    2022: 225,
}
EXPECTED_LOCAL_ORDINAL_BY_YEAR = {
    2016: 3,
    2017: 7,
    2018: 3,
    2021: 3,
    2022: 3,
}
EXPECTED_PAGE_BY_YEAR = {
    2016: 8,
    2017: 8,
    2018: 8,
    2021: 9,
    2022: 9,
}
EXPECTED_SOURCE_SHA256_BY_YEAR = {
    2016: "1547f1872b9efb87d789c7e05d46430151fdc609e4495b25da5dbff9dd0eab6f",
    2017: "396a012fb4abdd1372ab65022558dcf1ec96df01c2cde1096ab2379007e24a84",
    2018: "f0cfe3c6a7365640d31f01b6355e84c03ad9fd1b8d12c57978dde85041521535",
    2021: "3d243dc223fc08fc4e82935d973920c185c9eba3c56b9f0d3ab03db16b28fe47",
    2022: "283f308e67154e80ba7d132c6d586ab385e568158eb69dd62098533ab4857dcd",
}
EXPECTED_TABLE_SHA256_BY_YEAR = {
    2016: "65990194b0fe5199e208b81e0c555ac8098c3d07673483a7a0886292a3dbfd4b",
    2017: "f4058c241aace1faa2d235b71470836cd68abfcb23b0a31a37124061fec64ae5",
    2018: "39a189ecb15c0ab2285e09515f2385c0e5dd13ec9218b7213ae4a4fc61b98e89",
    2021: "b0e152ff02051a420521f7197ef0cf91e2d56f8c44b2892863d79e63996a7337",
    2022: "7f5b75af0f96b31ebeda3ee58a32bc897727d2afab3a86864909f67a0b4ca13e",
}
EXPECTED_VALUE_BY_YEAR = {
    2016: Decimal("12393987700725"),
    2017: Decimal("9968932894559"),
    2018: Decimal("8077150487394"),
    2021: Decimal("2475731954180"),
    2022: Decimal("1903239627025"),
}
EXPECTED_PRIOR_VALUE_BY_YEAR = {
    2016: Decimal("13951754818594"),
    2017: Decimal("12393987700725"),
    2018: Decimal("9968932894559"),
    2021: Decimal("4261525941169"),
    2022: Decimal("2475731954180"),
}
EXPECTED_DOCUMENT_BY_YEAR = {
    year: f"HND_financial_statements_{year}" for year in EXPECTED_UID_BY_YEAR
}
EXPECTED_SOURCE_PATH_BY_YEAR = {
    year: (
        f"/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/HND/"
        f"{year}/HND_financial_statements_{year}/"
        f"HND_financial_statements_{year}_extracted.txt"
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
) -> tuple[int, str]:
    source_path = Path(str(table["source_path"]))
    if str(source_path) != EXPECTED_SOURCE_PATH_BY_YEAR[year]:
        raise ValueError(f"{year}: source path mismatch")
    raw_bytes = source_path.read_bytes()
    expected_sha = EXPECTED_SOURCE_SHA256_BY_YEAR[year]
    actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    metadata_sha = str(table.get("source_sha256") or "").lower()
    if actual_sha != expected_sha or metadata_sha != expected_sha:
        raise ValueError(
            f"{year}: source hash mismatch: expected={expected_sha} "
            f"actual={actual_sha} metadata={metadata_sha}"
        )

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

    uid = str(table["internal_table_uid"])
    expected_line = EXPECTED_SOURCE_LINE_BY_YEAR[year]
    mapped_line = source_line_map.get(uid)
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(mapped_line) != expected_line or actual_line != expected_line:
        raise ValueError(
            f"{year}: source-line mismatch: map={mapped_line!r} "
            f"actual={actual_line} expected={expected_line}"
        )

    bounded_source = normalize(
        f"{str(table.get('context_before') or '')[-2500:]} {char_slice}"
    )
    if "vnd" not in bounded_source or "no phai tra" not in bounded_source:
        raise ValueError(f"{year}: bounded source slice lacks liability/VND evidence")
    if "bang can doi ke toan" not in bounded_source:
        raise ValueError(f"{year}: bounded source slice lacks balance-sheet context")
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
    if str(table.get("ticker") or "").upper() != "HND":
        raise ValueError(f"{year}: ticker mismatch")
    if str(table.get("scope") or "").lower() != "unknown":
        raise ValueError(f"{year}: expected corpus scope=unknown")
    if int(table.get("report_year")) != year:
        raise ValueError(f"{year}: report year mismatch")
    if int(table.get("local_ordinal")) != EXPECTED_LOCAL_ORDINAL_BY_YEAR[year]:
        raise ValueError(f"{year}: local ordinal mismatch")
    if int(table.get("page_no")) != EXPECTED_PAGE_BY_YEAR[year]:
        raise ValueError(f"{year}: page mismatch")
    if str(table.get("table_sha256") or "").lower() != EXPECTED_TABLE_SHA256_BY_YEAR[year]:
        raise ValueError(f"{year}: table hash metadata mismatch")

    table_function = table.get("table_function") or {}
    table_purpose = table.get("table_purpose") or {}
    table_section = table.get("table_section") or {}
    if normalize(table_function.get("kind")) != "balance_sheet":
        raise ValueError(f"{year}: table function mismatch")
    if normalize(table_purpose.get("kind")) != "period_comparison":
        raise ValueError(f"{year}: table purpose mismatch")
    if normalize(table_section.get("kind")) != "balance_sheet":
        raise ValueError(f"{year}: table section mismatch")

    context = normalize(
        " ".join(
            (
                str(table.get("context_before") or ""),
                json.dumps(table.get("context_trace") or {}, ensure_ascii=False),
                " ".join(str(value) for value in table.get("headers") or []),
            )
        )
    )
    if "bang can doi ke toan" not in context or "vnd" not in context:
        raise ValueError(f"{year}: bounded balance-sheet/VND context missing")
    source_line, _ = verify_coordinates(
        table, year=year, source_line_map=source_line_map
    )

    headers = list(table.get("headers") or [])
    if len(headers) != 5:
        raise ValueError(f"{year}: unexpected headers: {headers!r}")
    current_header = normalize(headers[3])
    prior_header = normalize(headers[4])
    if f"31/12/{year}" not in current_header or "vnd" not in current_header:
        raise ValueError(f"{year}: current VND header mismatch: {headers!r}")
    if f"1/1/{year}" not in prior_header or "vnd" not in prior_header:
        raise ValueError(f"{year}: prior VND header mismatch: {headers!r}")

    rows = table.get("rows") or []
    row_index = 2
    if len(rows) <= row_index or not isinstance(rows[row_index], list):
        raise ValueError(f"{year}: code-300 row missing")
    row = rows[row_index]
    if len(row) <= 4:
        raise ValueError(f"{year}: code-300 row has insufficient columns")
    row_label = canonical_label(row[0])
    if "no phai tra" not in row_label:
        raise ValueError(f"{year}: liability label mismatch: {row!r}")
    if str(row[1]).strip() != "300":
        raise ValueError(f"{year}: expected balance-sheet code 300: {row!r}")
    current = parse_decimal(row[3])
    prior = parse_decimal(row[4])
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
        "column_index": 3,
        "row_label": str(row[0]),
        "current_header": str(headers[3]),
        "prior_header": str(headers[4]),
        "current_raw_value": str(row[3]),
        "prior_raw_value": str(row[4]),
        "source_multiplier": "1",
        "replayed_value_vnd": str(current),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "balance_sheet_context_checked": True,
            "code_300_checked": True,
            "ocr_liability_alias_checked": True,
            "current_vnd_column_checked": True,
            "prior_vnd_column_checked": True,
            "same_unknown_scope_checked": True,
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
    if winners != [2016]:
        raise ValueError(f"unexpected unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_total_liabilities_q884_replay_v1",
        "question_id": 884,
        "ticker": "HND",
        "scope": "unknown",
        "metric": "Tổng nợ phải trả",
        "operation": "argmax_period",
        "years": sorted(EXPECTED_UID_BY_YEAR),
        "records": records,
        "winner_year": winners[0],
        "winner_value_vnd": str(maximum),
        "checks": {
            "uid_count": len(records) == 5,
            "source_hashes_checked": True,
            "source_line_coordinates_checked": True,
            "exact_balance_sheet_context_checked": True,
            "exact_code_300_row_checked": True,
            "same_unknown_scope": len(
                {record["checks"]["same_unknown_scope_checked"] for record in records}
            )
            == 1,
            "same_vnd_multiplier": len(
                {record["source_multiplier"] for record in records}
            )
            == 1,
            "unique_winner": len(winners) == 1,
        },
        "status": "PASS",
        "promotion_allowed": False,
        "strict_certificate": False,
        "official_score_measured": False,
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
