#!/usr/bin/env python3
"""Independently replay the SJG Q866 tax-payable period maximum.

Q866 asks which of 2018--2021 has the largest ``Thuế và các khoản phải nộp
Nhà nước`` balance.  The generic family contract binds the balance-sheet row
code 313 and the current-period column (``Số cuối năm``) before comparing
values.  This checker deliberately does not import the route adapter: it
replays the source/table/cell closure independently and emits a PASS report
for the generic UID-closure materializer.

The question ID is tracking metadata only.  It is not used to select a table
or compute the answer.  PASS is candidate/replay evidence, not a gold label,
strict E2E certificate, leaderboard score, or promotion authorization.
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
    2018: "c59a5ca75a84db105f19c96dbebebe99782db3a77fea09c820c238de07b83ef1",
    2019: "e402fb40b3051846c8e6677777dfb53b2c5291adffadff454c07440e680c87bc",
    2020: "df32878ddc0701997a2a7f8c9539f39aeffceb8c7b6ecf5164711d50c69a7396",
    2021: "136a07c2bb57670e5e2ac17733f8c5c0e8b327c93632e4cf88a64ff4117e3a89",
}
EXPECTED_SOURCE_LINE_BY_YEAR = {
    2018: 244,
    2019: 310,
    2020: 341,
    2021: 310,
}
EXPECTED_LOCAL_ORDINAL_BY_YEAR = {year: 8 for year in EXPECTED_UID_BY_YEAR}
EXPECTED_PAGE_BY_YEAR = {
    2018: 9,
    2019: 11,
    2020: 12,
    2021: 11,
}
EXPECTED_SOURCE_SHA256_BY_YEAR = {
    2018: "378421c4d4e3473f27280733f31d0b93a7a5bc138c4fbd2b3cf676a603963c19",
    2019: "3bb300c304275f2d65bfbe95ddc30eabe4c388911e469be7c3b6b9ec5a85ce4e",
    2020: "4c596a6bf207ec0deb0ae4006816d371d397546a9b2f84d68c2a73fa510b5530",
    2021: "c3d4155d46ab8c05298002a9141834ed251d8c4ed7d701969a94f79620a62d99",
}
EXPECTED_TABLE_SHA256_BY_YEAR = {
    2018: "17701756a31a5e9c9a60a88b2c009d1d0a37977bded8ad85e37541de57b17f47",
    2019: "6977e0ec32960b090070cb87eb7718e4d7d750849955518f94b2719fa4c1d085",
    2020: "b481079df6912ea458aab1950bb03986a97863b8ca60e864c36238b195b36a22",
    2021: "d8de67917057339432965d19c410c741e0210c1ac40fd2b340e50c9653259346",
}
EXPECTED_VALUE_BY_YEAR = {
    2018: Decimal("386945215579"),
    2019: Decimal("307749988310"),
    2020: Decimal("249462096512"),
    2021: Decimal("249374016597"),
}
EXPECTED_PRIOR_VALUE_BY_YEAR = {
    2018: Decimal("476194675539"),
    2019: Decimal("386945215579"),
    2020: Decimal("307749988310"),
    2021: Decimal("249462096512"),
}
EXPECTED_ROW_INDEX_BY_YEAR = {
    2018: 5,
    2019: 5,
    2020: 6,
    2021: 5,
}
EXPECTED_DOCUMENT_BY_YEAR = {
    year: f"SJG_financial_statements_{year}_consolidated"
    for year in EXPECTED_UID_BY_YEAR
}
EXPECTED_SOURCE_PATH_BY_YEAR = {
    year: (
        "/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/SJG/"
        f"{year}/SJG_financial_statements_{year}_consolidated/"
        f"SJG_financial_statements_{year}_consolidated_extracted.txt"
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
    char_start = int(table.get("char_start"))
    char_end = int(table.get("char_end"))
    byte_start = int(table.get("byte_start"))
    byte_end = int(table.get("byte_end"))
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
    if str(table.get("ticker") or "").upper() != "SJG":
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
    table_section = table.get("table_section") or {}
    if normalize(table_function.get("kind")) != "balance_sheet":
        raise ValueError(f"{year}: table function mismatch")
    if normalize(table_purpose.get("kind")) != "period_comparison":
        raise ValueError(f"{year}: table purpose mismatch")
    if normalize(table_section.get("kind")) != "balance_sheet":
        raise ValueError(f"{year}: table section mismatch")

    source_line, char_slice, source_text = verify_coordinates(
        table, year=year, source_line_map=source_line_map
    )
    normalized_source = canonical_label(source_text)
    if "bang can doi ke toan" not in normalized_source:
        raise ValueError(f"{year}: full-source balance-sheet context missing")
    if "don vi tien te su dung trong ke toan la dong viet nam vnd" not in normalized_source:
        raise ValueError(f"{year}: explicit VND accounting-currency declaration missing")
    if "nguon von" not in canonical_label(char_slice):
        raise ValueError(f"{year}: table slice lacks source-capital header")

    headers = list(table.get("headers") or [])
    if len(headers) != 6:
        raise ValueError(f"{year}: unexpected headers: {headers!r}")
    current_index = 4
    prior_index = 5
    if canonical_label(headers[current_index]) != "so cuoi nam":
        raise ValueError(f"{year}: current-period header mismatch: {headers!r}")
    if canonical_label(headers[prior_index]) != "so dau nam":
        raise ValueError(f"{year}: prior-period header mismatch: {headers!r}")

    rows = table.get("rows") or []
    expected_row_index = EXPECTED_ROW_INDEX_BY_YEAR[year]
    if len(rows) <= expected_row_index or not isinstance(rows[expected_row_index], list):
        raise ValueError(f"{year}: expected code-313 row is missing")
    row = rows[expected_row_index]
    if len(row) <= prior_index:
        raise ValueError(f"{year}: code-313 row has insufficient columns")
    if canonical_label(row[1]) != "thue va cac khoan phai nop nha nuoc":
        raise ValueError(f"{year}: tax-payable label mismatch: {row!r}")
    if str(row[2]).strip() != "313":
        raise ValueError(f"{year}: expected balance-sheet code 313: {row!r}")
    matching_rows = [
        index
        for index, candidate in enumerate(rows)
        if isinstance(candidate, list)
        and len(candidate) > prior_index
        and canonical_label(candidate[1] if len(candidate) > 1 else "")
        == "thue va cac khoan phai nop nha nuoc"
        and str(candidate[2]).strip() == "313"
    ]
    if matching_rows != [expected_row_index]:
        raise ValueError(f"{year}: code-313 row is not unique: {matching_rows}")

    current = parse_decimal(row[current_index])
    prior = parse_decimal(row[prior_index])
    if current != EXPECTED_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: current value mismatch: {current}")
    if prior != EXPECTED_PRIOR_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: prior value mismatch: {prior}")

    return {
        "year": year,
        "document_id": str(table["document_id"]),
        "ticker": "SJG",
        "scope": "consolidated",
        "internal_table_uid": uid,
        "source_line": source_line,
        "source_path": str(table["source_path"]),
        "source_sha256": str(table["source_sha256"]),
        "table_sha256": str(table["table_sha256"]),
        "local_ordinal": int(table["local_ordinal"]),
        "page_no": int(table["page_no"]),
        "row_index": expected_row_index,
        "column_index": current_index,
        "row_label": str(row[1]),
        "row_code": str(row[2]),
        "current_header": str(headers[current_index]),
        "prior_header": str(headers[prior_index]),
        "current_raw_value": str(row[current_index]),
        "prior_raw_value": str(row[prior_index]),
        "source_multiplier": "1",
        "source_multiplier_basis": "document_accounting_currency_vnd",
        "replayed_value_vnd": str(current),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "table_hash_recomputed": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "balance_sheet_context_checked": True,
            "document_currency_checked": True,
            "code_313_checked": True,
            "tax_payable_label_checked": True,
            "current_period_column_checked": True,
            "prior_period_column_checked": True,
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
    if winners != [2018]:
        raise ValueError(f"unexpected unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_tax_payable_q866_replay_v1",
        "question_id": 866,
        "ticker": "SJG",
        "scope": "consolidated",
        "metric": "Thuế và các khoản phải nộp Nhà nước",
        "operation": "argmax_period",
        "years": sorted(EXPECTED_UID_BY_YEAR),
        "records": records,
        "winner_year": winners[0],
        "winner_value_vnd": str(maximum),
        "checks": {
            "uid_count": len(records) == 4,
            "source_hashes_checked": True,
            "table_hashes_recomputed": True,
            "source_line_coordinates_checked": True,
            "exact_balance_sheet_context_checked": True,
            "exact_code_313_rows_checked": True,
            "same_consolidated_scope": len(
                {record["scope"] for record in records}
            )
            == 1,
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
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
