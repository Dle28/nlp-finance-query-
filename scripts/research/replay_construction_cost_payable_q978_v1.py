#!/usr/bin/env python3
"""Independently replay the NVL Q978 construction-cost payable maximum.

Q978 asks which of 2020, 2022 and 2025 has the largest short-term payable
construction-cost balance. The source family is the exact ``Chi phí xây
dựng`` row under the ``Chi phí phải trả`` note. The 2025 extractor labels the
same source reconstruction as a generic financial-data schedule, so this
checker binds the semantic note context and short-term hierarchy instead of
requiring the raw extractor kind to be identical across years.

The checker deliberately does not import the argmax route adapter. Question
ID 978 is tracking metadata only. PASS is candidate/replay evidence, not a
gold label, strict E2E certificate, leaderboard score, or promotion grant.
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
    2020: "bf71c88bf10549ebb44b8b6ca532a18fa373b438ca866f909edd4e22999d073f",
    2022: "73bf47b43dacdfe64542eb60733b0c7103bc5b61c01ac070719241bd016f957e",
    2025: "08662467583dc1c82bae5002d507a130e7403a465797c4f3d97814b1439be9bc",
}
EXPECTED_SOURCE_LINE_BY_YEAR = {2020: 1575, 2022: 1612, 2025: 2264}
EXPECTED_LOCAL_ORDINAL_BY_YEAR = {2020: 66, 2022: 57, 2025: 47}
EXPECTED_PAGE_BY_YEAR = {2020: 61, 2022: 62, 2025: 59}
EXPECTED_SOURCE_SHA256_BY_YEAR = {
    2020: "81ce9140e96d4d82478c614994fa83f8fe23b6098e9b8dceef4cf3e273983746",
    2022: "b1b208b9001cb9b45cfb6677966e8f364c236dcc62e84979df47f29c662c958e",
    2025: "958bceb95853d5e4709276e75747a234bc2eb4dc973957c66e6e6f8ceb386f28",
}
EXPECTED_TABLE_SHA256_BY_YEAR = {
    2020: "f6da7544d7795778156027d24cb49d9c3e8093d2318089e4c072cf8c0473d64a",
    2022: "af85ff6efa75c6d9d3ec45a7c0e31ae3f7962547a4b1a6a495bb341181beffcd",
    2025: "076dbf25a87e4d08e81a715039197d069b1b79825f39085cd32f2e4262336929",
}
EXPECTED_VALUE_BY_YEAR = {
    2020: Decimal("1761909529797"),
    2022: Decimal("3817192873315"),
    2025: Decimal("4735317291614"),
}
EXPECTED_PRIOR_VALUE_BY_YEAR = {
    2020: Decimal("1661156307763"),
    2022: Decimal("3254716258582"),
    2025: Decimal("4244216774226"),
}
EXPECTED_ROW_INDEX_BY_YEAR = {2020: 1, 2022: 1, 2025: 3}
EXPECTED_DOCUMENT_BY_YEAR = {
    year: f"NVL_financial_statements_{year}_consolidated"
    for year in EXPECTED_UID_BY_YEAR
}
EXPECTED_SOURCE_PATH_BY_YEAR = {
    year: (
        "/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/NVL/"
        f"{year}/NVL_financial_statements_{year}_consolidated/"
        f"NVL_financial_statements_{year}_consolidated_extracted.txt"
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


def _short_term_target_row(
    table: Mapping[str, Any], *, year: int
) -> tuple[int, list[Any]]:
    rows = table.get("rows") or []
    expected_index = EXPECTED_ROW_INDEX_BY_YEAR[year]
    if len(rows) <= expected_index or not isinstance(rows[expected_index], list):
        raise ValueError(f"{year}: expected construction-cost row is missing")
    row = rows[expected_index]
    if len(row) < 3 or canonical_label(row[0]) != "chi phi xay dung":
        raise ValueError(f"{year}: construction-cost row mismatch: {row!r}")

    matching_indices = [
        index
        for index, candidate in enumerate(rows)
        if isinstance(candidate, list)
        and candidate
        and canonical_label(candidate[0]) == "chi phi xay dung"
    ]
    if matching_indices != [expected_index]:
        raise ValueError(f"{year}: construction-cost row is not unique: {matching_indices}")

    context_trace = table.get("context_trace") or {}
    source_title = normalize(
        context_trace.get("source_title") if isinstance(context_trace, Mapping) else ""
    )
    context = normalize(
        " ".join(
            (
                str(table.get("context_before") or ""),
                source_title,
                json.dumps(context_trace, ensure_ascii=False),
            )
        )
    )
    if "chi phi phai tra" not in context:
        raise ValueError(f"{year}: payable-cost note context missing")
    if "chi phi phai tra ngan han" not in context:
        short_markers = {
            canonical_label(rows[index][0])
            for index in range(expected_index)
            if isinstance(rows[index], list) and rows[index]
        }
        if "a ngan han" not in short_markers and "ngan han" not in short_markers:
            raise ValueError(f"{year}: short-term hierarchy missing")
        if "b dai han" in short_markers or "dai han" in short_markers:
            raise ValueError(f"{year}: target is after a long-term boundary")
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
    if str(table.get("ticker") or "").upper() != "NVL":
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
    raw_kind = normalize(table_function.get("kind"))
    if raw_kind not in {"financial_note_detail", "financial_data_schedule"}:
        raise ValueError(f"{year}: unexpected raw table kind: {raw_kind!r}")
    if normalize(table_purpose.get("kind")) != "period_comparison":
        raise ValueError(f"{year}: table purpose mismatch")
    if normalize(table_section.get("kind")) not in {"liability", "expense"}:
        raise ValueError(f"{year}: table section mismatch")

    source_line, char_slice, source_text = verify_coordinates(
        table, year=year, source_line_map=source_line_map
    )
    normalized_source = canonical_label(source_text)
    if not (
        "don vi tinh dong viet nam" in normalized_source
        or re.search(
            r"don vi tien te trong ke toan.{0,220}dong viet nam.{0,100}(?:vnd|vn)",
            normalized_source,
        )
        or re.search(
            r"don vi tien te su dung trong ke toan.{0,180}dong viet nam.{0,100}(?:vnd|vn)",
            normalized_source,
        )
    ):
        raise ValueError(f"{year}: explicit VND source declaration missing")
    if "chi phi xay dung" not in canonical_label(char_slice):
        raise ValueError(f"{year}: table slice lacks construction-cost row")

    headers = list(table.get("headers") or [])
    if len(headers) != 3:
        raise ValueError(f"{year}: unexpected headers: {headers!r}")
    current_header = canonical_label(headers[1])
    prior_header = canonical_label(headers[2])
    if str(year) not in current_header or "31 12" not in current_header:
        raise ValueError(f"{year}: current-period header mismatch: {headers!r}")
    # The 2025 NVL schedule presents the opening balance as 01/01/2025;
    # it is the comparison column for the 31/12/2025 balance even though it
    # is not labelled with the preceding calendar year.
    prior_year_tokens = {str(year)} if year == 2025 else {str(year - 1)}
    if not any(token in prior_header for token in prior_year_tokens) or not (
        "31 12" in prior_header or "01 01" in prior_header
    ):
        raise ValueError(f"{year}: prior-period header mismatch: {headers!r}")

    row_index, row = _short_term_target_row(table, year=year)
    current = parse_decimal(row[1])
    prior = parse_decimal(row[2])
    if current != EXPECTED_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: current value mismatch: {current}")
    if prior != EXPECTED_PRIOR_VALUE_BY_YEAR[year]:
        raise ValueError(f"{year}: prior value mismatch: {prior}")

    return {
        "year": year,
        "document_id": str(table["document_id"]),
        "ticker": "NVL",
        "scope": "consolidated",
        "internal_table_uid": uid,
        "source_line": source_line,
        "source_path": str(table["source_path"]),
        "source_sha256": str(table["source_sha256"]),
        "table_sha256": str(table["table_sha256"]),
        "local_ordinal": int(table["local_ordinal"]),
        "page_no": int(table["page_no"]),
        "raw_table_kind": raw_kind,
        "semantic_family": "short_term_construction_cost_payable_note",
        "row_index": row_index,
        "column_index": 1,
        "row_label": str(row[0]),
        "current_header": str(headers[1]),
        "prior_header": str(headers[2]),
        "current_raw_value": str(row[1]),
        "prior_raw_value": str(row[2]),
        "source_multiplier": "1",
        "source_multiplier_basis": "explicit_vnd_document_declaration",
        "replayed_value_vnd": str(current),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "table_hash_recomputed": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "payable_note_context_checked": True,
            "short_term_hierarchy_checked": True,
            "construction_cost_row_checked": True,
            "current_period_column_checked": True,
            "prior_period_column_checked": True,
            "explicit_vnd_document_checked": True,
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
    if winners != [2025]:
        raise ValueError(f"unexpected unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_construction_cost_payable_q978_replay_v1",
        "question_id": 978,
        "ticker": "NVL",
        "scope": "consolidated",
        "metric": "Số dư chi phí xây dựng phải trả ngắn hạn",
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
            "exact_payable_note_context_checked": True,
            "exact_construction_cost_rows_checked": True,
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
