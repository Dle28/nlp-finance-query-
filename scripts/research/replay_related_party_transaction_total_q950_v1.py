#!/usr/bin/env python3
"""Independently replay the VRE related-party transaction-total maximum.

The question asks which of 2019--2022 has the largest total transaction value
with related parties in VRE's separate-company report.  The disclosure is
split across consecutive source tables for every year.  This checker binds
the exact table/cell closure, replays the current-year ``Giá trị giao dịch``
column in million VND, and sums the leaf transaction rows for each year.

Question ID 950 is tracking metadata only.  The checker does not import the
argmax adapter.  PASS is source/replay evidence, not a gold label, official
score, strict certificate, promotion grant, or release authorization.
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


ASSET_DEFAULT = Path(
    "artifacts/research/document_corpus_round2_assets_v1_20260829_r1/"
    "full_table_assets_v1.jsonl"
)
SOURCE_LINE_MAP_DEFAULT = Path(
    "artifacts/research/document_corpus_round2_assets_v1_20260829_r1/"
    "source_line_map_full_v1.json"
)


EXPECTED_TABLES_BY_YEAR: dict[int, list[dict[str, Any]]] = {
    2019: [
        {
            "ordinal": 68,
            "uid": "96061a9997109f195ccb546a50b61ce29e288924a65f43fe796053bd82d233d5",
            "page": 48,
            "line": 1374,
            "char": (94925, 97057),
            "byte": (112751, 115121),
            "source_sha": "b5700bef6756a566ae461d391b1fb13b755274fe31fd0fe7b2064723614dd932",
            "table_sha": "b918ec46749772d645a73359155022ad41172cac38f163e04bb551872cbab93d",
            "kind": "related_party_schedule",
            "rows": [4, 6, 9, 10, 12, 13, 14, 17, 18, 19, 20, 21, 22, 24, 25, 28, 29, 30],
            "raw": [
                "201.798", "788.582", "323.040", "7.200", "289.495",
                "796.000", "449.101", "197.413", "100.250", "305.000",
                "1.037.293", "800.000", "750.000", "206.603", "88.699",
                "1.900.000", "380.000", "1.000",
            ],
        },
        {
            "ordinal": 69,
            "uid": "06a0040d0d16946ce816c9a171bea591a14388f0946af6f26782abdc75ee0181",
            "page": 49,
            "line": 1389,
            "char": (97310, 99598),
            "byte": (115410, 117989),
            "source_sha": "b5700bef6756a566ae461d391b1fb13b755274fe31fd0fe7b2064723614dd932",
            "table_sha": "9708cb489cecdd905872ec5ae098141e3a71eff7601478a5916111db850285e0",
            "kind": "financial_note",
            "rows": [3, 4, 7, 10, 11, 13, 16, 17, 19, 20, 21, 23, 25, 27, 28, 29, 31, 32, 33],
            "raw": [
                "2.827", "291.810", "380.000", "6.602", "314.179",
                "273.485", "197.485", "12.388", "108.931", "6.575",
                "37.126", "64.836", "20.814", "32.286", "81.000",
                "7.812", "37.617", "98.000", "9.451",
            ],
        },
        {
            "ordinal": 70,
            "uid": "04322695ab65876e88d9b1c26d065ec88e580b4c3a97099d4cc6c8f3f14cb971",
            "page": 50,
            "line": 1404,
            "char": (99851, 100419),
            "byte": (118278, 118926),
            "source_sha": "b5700bef6756a566ae461d391b1fb13b755274fe31fd0fe7b2064723614dd932",
            "table_sha": "7aacc2d9d151ea7980ef8230279215fcf843050be0f7e7b36100fbf67a7516dd",
            "kind": "governance_roster",
            "rows": [3, 4, 5, 7],
            "raw": ["1.020.000", "800.000", "90.989", "28.064"],
        },
    ],
    2020: [
        {
            "ordinal": 65,
            "uid": "b2388846531ac8cb9152f04203285aa7522d6f8d4efdcb68488b1fa035949a85",
            "page": 48,
            "line": 1368,
            "char": (91095, 92641),
            "byte": (108312, 110032),
            "source_sha": "8de0a855c57971b5ef6093024f614197d3c396d5ce86db6208359dc0a2aa8cb9",
            "table_sha": "30ee9d18b9f12dcc7eadf9f6b4e4cf0a85a3b65dba442c710aa259ff97a45e7b",
            "kind": "related_party_schedule",
            "rows": [9, 11, 16, 17, 18, 19, 21],
            "raw": ["239.365", "579.587", "137.562", "31.627", "40.000", "350.000", "750.000"],
        },
        {
            "ordinal": 66,
            "uid": "30c1c4a99aa489861ae3ec076741c6e5fbaf1a1e32310861da094bb44a440f10",
            "page": 49,
            "line": 1389,
            "char": (92919, 95190),
            "byte": (110349, 112890),
            "source_sha": "8de0a855c57971b5ef6093024f614197d3c396d5ce86db6208359dc0a2aa8cb9",
            "table_sha": "51faf85f9d6434de37bde2df6dc1f0730913b06d52b761686efc1cf7dbff6fed",
            "kind": "financial_note",
            "rows": [3, 4, 5, 6, 17, 18, 19, 22, 24, 26, 28, 31, 32],
            "raw": [
                "159.612", "107.233", "648.294", "590.000", "6.454",
                "156.667", "649.806", "35.824", "40.890", "230",
                "117.768", "930.282", "44.587",
            ],
        },
        {
            "ordinal": 67,
            "uid": "73dc0561bd0de18ef04fd25519045f729b28a6e97e92979a1794778c25f1788e",
            "page": 50,
            "line": 1406,
            "char": (95444, 96847),
            "byte": (113179, 114773),
            "source_sha": "8de0a855c57971b5ef6093024f614197d3c396d5ce86db6208359dc0a2aa8cb9",
            "table_sha": "7a937ec2ddbea8d52fa12225a1f872face7b49f9b4908182d7d2192e25c479b1",
            "kind": "financial_note",
            "rows": [4, 5, 7, 9, 11, 13, 14, 15, 17, 19],
            "raw": [
                "550.294", "5.693", "295.420", "56.453", "711.641",
                "25.698", "878.000", "878.000", "16.345", "28.438",
            ],
        },
    ],
    2021: [
        {
            "ordinal": 57,
            "uid": "2c1c11d6d003d978aa862bba2ed8de49a2d1a0e13d67f2b778eb866f1dbd5309",
            "page": 47,
            "line": 1274,
            "char": (89754, 91794),
            "byte": (107485, 109706),
            "source_sha": "81833240b3f0d51c6ec76d27666612c651b65a9155d90827bb7011d817729d7a",
            "table_sha": "be9ac581748c7fb215c55d3368673faa0d8178e08e82e878038538ccbe6064f5",
            "kind": "related_party_schedule",
            "rows": [4, 5, 6, 9, 10, 12, 15, 16, 17, 18, 21, 22, 24, 26, 27, 28, 29],
            "raw": [
                "209.514", "1.070.000", "543.745", "74.825", "3.647",
                "190.000", "132.330", "28.392", "229.000", "1.187.294",
                "6.912", "151.360", "242.172", "1.150.000", "1.150.000",
                "45.685", "16.550",
            ],
        },
        {
            "ordinal": 58,
            "uid": "9a3dc17bc97c806541bb82a74c951100b325f832ef629987c64a6215cc314152",
            "page": 48,
            "line": 1289,
            "char": (92047, 94127),
            "byte": (109995, 112345),
            "source_sha": "81833240b3f0d51c6ec76d27666612c651b65a9155d90827bb7011d817729d7a",
            "table_sha": "ca9e9968eade27476d0240b1f0767b96f37fe944d3c1314b87875b9ced826479",
            "kind": "financial_note",
            "rows": [4, 5, 7, 10, 12, 13, 19, 27, 29],
            "raw": ["295.000", "5.173", "49.421", "41.656", "192.118", "116.129", "65.742", "16.300", "27.845"],
        },
    ],
    2022: [
        {
            "ordinal": 55,
            "uid": "877f8876750295f276ae61419574656b482bc620b75b407dddb25f1e53c37e56",
            "page": 46,
            "line": 1396,
            "char": (89916, 91692),
            "byte": (107634, 109583),
            "source_sha": "a11485985591bb5ce0fc7b96068219db5bfd53b4b4dc286b11c1b0cf75cbf22d",
            "table_sha": "e0e05647eddce43867c8f2a30963db457dc371f5bd222d9febe393985c0d2859",
            "kind": "related_party_schedule",
            "rows": [4, 5, 6, 7, 14, 19, 22, 23, 25],
            "raw": ["155.982", "2.293.180", "336.000", "81.170", "360.822", "62.266", "7.414", "132.959", "1.226.153"],
        },
        {
            "ordinal": 56,
            "uid": "0514f034b70420f242616e200c0b75f61ffb75fe42554e519cfd6f1c69404aa5",
            "page": 47,
            "line": 1411,
            "char": (91935, 93880),
            "byte": (109860, 112007),
            "source_sha": "a11485985591bb5ce0fc7b96068219db5bfd53b4b4dc286b11c1b0cf75cbf22d",
            "table_sha": "6613e6b6257d4a83e64e8f4511eb32a4dccd6e3c1f8bb5f6c831a33660862d72",
            "kind": "financial_note",
            "rows": [6, 7, 8, 10, 11, 12, 14, 16, 20, 22, 24, 26, 28],
            "raw": [
                "3.600", "20.784", "70.312", "3.250.000", "3.545.000",
                "54.547", "58.430", "4.281", "38.575", "65.742",
                "39.638", "7.149", "16.300",
            ],
        },
        {
            "ordinal": 57,
            "uid": "ff3e6bb5cb267f596cba68777d439c876ec3b42e999e9e5fbb14cc1f7e686a23",
            "page": 48,
            "line": 1428,
            "char": (94147, 95239),
            "byte": (112309, 113528),
            "source_sha": "a11485985591bb5ce0fc7b96068219db5bfd53b4b4dc286b11c1b0cf75cbf22d",
            "table_sha": "f254a5ccaef17bc7dded47b17093d9dcb057988a8b25eb79b62823adb45b07f9",
            "kind": "governance_roster",
            "rows": [7, 8, 11, 12, 14],
            "raw": ["880", "880", "11.710", "16.795", "737"],
        },
    ],
}


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower().replace("đ", "d")).strip()


def canonical(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize(value)).strip()


def parse_decimal(value: Any) -> Decimal:
    text = str(value or "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("() ").replace(".", "").replace(",", ".")
    if not text or not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        raise InvalidOperation(f"not a numeric cell: {value!r}")
    number = Decimal(text)
    return -number if negative else number


def expected_document(year: int) -> str:
    return f"VRE_financial_statements_{year}_separate"


def expected_source_path(year: int) -> str:
    return str(
        Path("/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/VRE")
        / str(year)
        / f"VRE_financial_statements_{year}_separate"
        / f"VRE_financial_statements_{year}_separate_extracted.txt"
    )


def load_expected_tables(asset_path: Path) -> dict[str, dict[str, Any]]:
    expected = {
        entry["uid"]
        for entries in EXPECTED_TABLES_BY_YEAR.values()
        for entry in entries
    }
    found: dict[str, dict[str, Any]] = {}
    with asset_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                table = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(table, dict):
                continue
            uid = str(table.get("internal_table_uid") or "")
            if uid not in expected:
                continue
            if uid in found:
                raise ValueError(f"duplicate expected table UID at line {line_number}: {uid}")
            found[uid] = table
    missing = sorted(expected - set(found))
    if missing:
        raise ValueError(f"missing expected table UIDs: {missing}")
    return found


def verify_table(
    table: Mapping[str, Any],
    *,
    year: int,
    expected: Mapping[str, Any],
    source_line_map: Mapping[str, Any],
) -> dict[str, Any]:
    uid = str(expected["uid"])
    if str(table.get("internal_table_uid") or "") != uid:
        raise ValueError(f"{year}/{expected['ordinal']}: UID mismatch")
    if str(table.get("document_id") or "").removesuffix(".txt") != expected_document(year):
        raise ValueError(f"{year}/{expected['ordinal']}: document mismatch")
    if str(table.get("ticker") or "").upper() != "VRE":
        raise ValueError(f"{year}/{expected['ordinal']}: ticker mismatch")
    if str(table.get("scope") or "").lower() != "separate":
        raise ValueError(f"{year}/{expected['ordinal']}: scope mismatch")
    if int(table.get("report_year")) != year:
        raise ValueError(f"{year}/{expected['ordinal']}: report year mismatch")
    if int(table.get("local_ordinal")) != int(expected["ordinal"]):
        raise ValueError(f"{year}/{expected['ordinal']}: local ordinal mismatch")
    if int(table.get("page_no")) != int(expected["page"]):
        raise ValueError(f"{year}/{expected['ordinal']}: page mismatch")

    raw_kind = normalize((table.get("table_function") or {}).get("kind"))
    if raw_kind != normalize(expected["kind"]):
        raise ValueError(f"{year}/{expected['ordinal']}: raw kind mismatch: {raw_kind!r}")
    headers = list(table.get("headers") or [])
    if len(headers) != 3:
        raise ValueError(f"{year}/{expected['ordinal']}: unexpected headers")
    header_text = canonical(" ".join(headers))
    if "gia tri giao dich" not in header_text or "trieu vnd" not in header_text:
        raise ValueError(f"{year}/{expected['ordinal']}: transaction/million-VND header missing")
    if str(year) not in canonical(headers[1]) or str(year - 1) not in canonical(headers[2]):
        raise ValueError(f"{year}/{expected['ordinal']}: period headers mismatch")

    source_path = str(table.get("source_path") or "")
    expected_path = expected_source_path(year)
    if source_path != expected_path:
        raise ValueError(f"{year}/{expected['ordinal']}: source path mismatch")
    raw_bytes = Path(source_path).read_bytes()
    expected_source_sha = str(expected["source_sha"])
    actual_source_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if actual_source_sha != expected_source_sha or str(table.get("source_sha256") or "").lower() != expected_source_sha:
        raise ValueError(f"{year}/{expected['ordinal']}: source hash mismatch")

    source_text = raw_bytes.decode("utf-8", errors="replace")
    char_start, char_end = (int(value) for value in expected["char"])
    byte_start, byte_end = (int(value) for value in expected["byte"])
    actual_coords = (
        int(table.get("char_start")),
        int(table.get("char_end")),
        int(table.get("byte_start")),
        int(table.get("byte_end")),
    )
    if actual_coords != (char_start, char_end, byte_start, byte_end):
        raise ValueError(f"{year}/{expected['ordinal']}: source coordinates mismatch")
    if not (0 <= char_start < char_end <= len(source_text)):
        raise ValueError(f"{year}/{expected['ordinal']}: invalid character range")
    if not (0 <= byte_start < byte_end <= len(raw_bytes)):
        raise ValueError(f"{year}/{expected['ordinal']}: invalid byte range")
    char_slice = source_text[char_start:char_end]
    byte_slice = raw_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
    if char_slice != byte_slice:
        raise ValueError(f"{year}/{expected['ordinal']}: byte/character slices disagree")
    expected_table_sha = str(expected["table_sha"])
    actual_table_sha = hashlib.sha256(char_slice.encode("utf-8")).hexdigest().lower()
    if actual_table_sha != expected_table_sha or str(table.get("table_sha256") or "").lower() != expected_table_sha:
        raise ValueError(f"{year}/{expected['ordinal']}: table hash mismatch")

    mapped_line = source_line_map.get(uid)
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(mapped_line) != int(expected["line"]) or actual_line != int(expected["line"]):
        raise ValueError(
            f"{year}/{expected['ordinal']}: source line mismatch: "
            f"map={mapped_line!r} actual={actual_line} expected={expected['line']}"
        )
    context_window = normalize(source_text[max(0, char_start - 7000) : char_start + 200])
    if "giao dich chu yeu" not in context_window or "ben lien quan" not in context_window:
        raise ValueError(f"{year}/{expected['ordinal']}: main related-party disclosure context missing")
    if "trieu vnd" not in canonical(char_slice):
        raise ValueError(f"{year}/{expected['ordinal']}: table slice lacks million-VND unit")

    rows = table.get("rows") or []
    expected_indices = [int(index) for index in expected["rows"]]
    expected_raw = [str(value) for value in expected["raw"]]
    if len(expected_indices) != len(expected_raw):
        raise ValueError(f"{year}/{expected['ordinal']}: checker fixture is inconsistent")
    if len(rows) <= max(expected_indices, default=-1):
        raise ValueError(f"{year}/{expected['ordinal']}: expected source rows missing")
    observed_raw: list[str] = []
    cells: list[dict[str, Any]] = []
    current_column = 1
    for row_index, expected_value in zip(expected_indices, expected_raw):
        row = rows[row_index]
        if not isinstance(row, list) or len(row) <= current_column:
            raise ValueError(f"{year}/{expected['ordinal']}: malformed cell at row {row_index}")
        raw_value = str(row[current_column])
        if raw_value != expected_value:
            raise ValueError(
                f"{year}/{expected['ordinal']} row {row_index}: raw mismatch "
                f"expected={expected_value!r} actual={raw_value!r}"
            )
        observed_raw.append(raw_value)
        cells.append(
            {
                "internal_table_uid": uid,
                "row_index": row_index,
                "column_index": current_column,
                "row_label": str(row[0]),
                "raw_value": raw_value,
                "value_million_vnd": str(parse_decimal(raw_value)),
            }
        )
    current_million = sum((parse_decimal(value) for value in observed_raw), Decimal(0))
    current_vnd = current_million * Decimal("1000000")
    return {
        "year": year,
        "document_id": expected_document(year),
        "ticker": "VRE",
        "scope": "separate",
        "internal_table_uid": uid,
        "source_line": int(expected["line"]),
        "source_path": source_path,
        "source_sha256": expected_source_sha,
        "table_sha256": expected_table_sha,
        "local_ordinal": int(expected["ordinal"]),
        "page_no": int(expected["page"]),
        "raw_table_kind": raw_kind,
        "semantic_family": "related_party_transaction_total_main_disclosure",
        "row_indices": expected_indices,
        "column_index": current_column,
        "current_header": str(headers[current_column]),
        "prior_header": str(headers[2]),
        "current_raw_values": observed_raw,
        "source_multiplier": "1000000",
        "source_multiplier_basis": "explicit_million_vnd_table_header",
        "replayed_value_million_vnd": str(current_million),
        "replayed_value_vnd": str(current_vnd),
        "cells": cells,
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "table_hash_recomputed": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "main_related_party_disclosure_context_checked": True,
            "current_period_column_checked": True,
            "explicit_million_vnd_header_checked": True,
            "same_separate_scope_checked": True,
            "leaf_row_coordinates_checked": True,
        },
    }


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    tables = load_expected_tables(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    if not isinstance(source_line_map, dict):
        raise ValueError("source-line map must be a JSON object")

    year_records: list[dict[str, Any]] = []
    for year in sorted(EXPECTED_TABLES_BY_YEAR):
        expected_entries = EXPECTED_TABLES_BY_YEAR[year]
        ordinals = [int(entry["ordinal"]) for entry in expected_entries]
        if ordinals != list(range(ordinals[0], ordinals[-1] + 1)):
            raise ValueError(f"{year}: expected continuation ordinals are not contiguous")
        table_records = [
            verify_table(
                tables[str(entry["uid"])],
                year=year,
                expected=entry,
                source_line_map=source_line_map,
            )
            for entry in expected_entries
        ]
        cells = [cell for record in table_records for cell in record["cells"]]
        total = sum(
            (Decimal(record["replayed_value_vnd"]) for record in table_records),
            Decimal(0),
        )
        year_records.append(
            {
                "question_id": 950,
                "year": year,
                "ticker": "VRE",
                "scope": "separate",
                "semantic_family": "related_party_transaction_total_main_disclosure",
                "aggregate_value_vnd": str(total),
                "aggregate_value_million_vnd": str(total / Decimal("1000000")),
                "table_count": len(table_records),
                "cell_count": len(cells),
                "table_records": table_records,
                "cells": cells,
                "sources": cells,
            }
        )

    values = [Decimal(record["aggregate_value_vnd"]) for record in year_records]
    maximum = max(values)
    winners = [
        record["year"]
        for record, value in zip(year_records, values)
        if value == maximum
    ]
    if winners != [2019]:
        raise ValueError(f"unexpected unique maximum: {winners}")
    return {
        "protocol": "vifinqa_independent_related_party_transaction_total_q950_replay_v1",
        "question_id": 950,
        "ticker": "VRE",
        "scope": "separate",
        "metric": "Tổng giá trị giao dịch với bên liên quan",
        "operation": "argmax_period_over_leaf_transaction_sum",
        "years": sorted(EXPECTED_TABLES_BY_YEAR),
        "records": year_records,
        "winner_year": winners[0],
        "winner_value_vnd": str(maximum),
        "checks": {
            "expected_table_count": sum(len(value) for value in EXPECTED_TABLES_BY_YEAR.values()),
            "verified_table_count": sum(record["table_count"] for record in year_records),
            "verified_source_cell_count": sum(record["cell_count"] for record in year_records),
            "source_hashes_checked": True,
            "table_hashes_recomputed": True,
            "source_line_coordinates_checked": True,
            "exact_main_related_party_context_checked": True,
            "exact_leaf_transaction_cells_checked": True,
            "same_separate_scope": len({record["scope"] for record in year_records}) == 1,
            "same_million_vnd_multiplier": True,
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
    parser.add_argument("--asset", type=Path, default=ASSET_DEFAULT)
    parser.add_argument("--source-line-map", type=Path, default=SOURCE_LINE_MAP_DEFAULT)
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
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
