#!/usr/bin/env python3
"""Independently replay the TTF Q829 other-profit period maximum.

Q829 does not state a consolidated/separate scope.  This checker therefore
audits both complete statement families and accepts the answer only when the
unique winning year is identical in both scopes.  It verifies exact table
UIDs, statement-code/row aliases, current-period columns, VND source context,
source hashes and byte/character/source-line coordinates.  The result is a
best-effort research finding, not a strict certificate or official score.
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


YEARS = (2016, 2017, 2023, 2025)
SCOPES = ("consolidated", "separate")

EXPECTED = {
    "consolidated": {
        2016: {
            "uid": "cf5c8ca179a61a55680a38821a95cc971a2d88f9ee66d23696cfa13332454790",
            "source_line": 264,
            "local_ordinal": 8,
            "page_no": 11,
            "row_index": 15,
            "source_sha256": "cd4a24845a682415e675a8cc6e8ef57ee86d23d15db8d0228f6666bf9af8ae45",
            "table_sha256": "fedf80556faba9cabd71d7efc20967f0575abd6b91866f916912e46c84431cd8",
            "raw_value": "(602.515.304)",
            "value": Decimal("-602515304"),
            "row_label": "14. (Lỗ) lợi nhuận khác",
        },
        2017: {
            "uid": "977100b345095070ea477406962b160140d1a6a22d39c0d31ab3d299a4e5af11",
            "source_line": 272,
            "local_ordinal": 9,
            "page_no": 11,
            "row_index": 15,
            "source_sha256": "c25e40934c80925cbbf6a4f94f405af251000f88c1411513a1a5165e2f06ca66",
            "table_sha256": "4332f34deb72da54992b1fa880540b9abe30c4ca28dd05b1ddf66b4ddb856f3a",
            "raw_value": "(14.555.567.878)",
            "value": Decimal("-14555567878"),
            "row_label": "14. Lỗ khác",
        },
        2023: {
            "uid": "2abc83000dad065474df00fa7e132533869d69ef7d66e8f20251a015a0bd9670",
            "source_line": 232,
            "local_ordinal": 7,
            "page_no": 9,
            "row_index": 15,
            "source_sha256": "e41fc6a36841d42e01060f8231dba7406f762fd04ca26ebef4b907c9cdc0168b",
            "table_sha256": "115b2e9c5f19f2637c9e8503e3c4245a1e6f2370a8410491db9b7b7dc860272b",
            "raw_value": "(69.981.712.952)",
            "value": Decimal("-69981712952"),
            "row_label": "14. (Lỗ) lợi nhuận khác",
        },
        2025: {
            "uid": "3ede9953dc22d58c8845ec0a86e8f053c74c13e5ec734c016d3fe3d5501c1056",
            "source_line": 336,
            "local_ordinal": 5,
            "page_no": 10,
            "row_index": 15,
            "source_sha256": "56d8108040c04e1353eb07eb0f9a54df6f3f529c1c2ce6a6ced01d16f239b320",
            "table_sha256": "bc604d0dd51935a355f36a139f86ee3b93f2b29bc39e19f8bf6d3c72de038ecf",
            "raw_value": "43.934.748.741",
            "value": Decimal("43934748741"),
            "row_label": "14. Lợi nhuận khác",
        },
    },
    "separate": {
        2016: {
            "uid": "6cd4d29fa3805136b4e5f0b4b0c68289ed6c458f803783b5435c5b66d24ebf2c",
            "source_line": 273,
            "local_ordinal": 8,
            "page_no": 11,
            "row_index": 14,
            "source_sha256": "c3d419aa713da7837a138b033e061ff4346728fd8783e7b336fa233aedc8cf5a",
            "table_sha256": "e1e0ddbef23f1687140f1c3771f8d46654681ce18106bca62dc9f728318c8605",
            "raw_value": "14.353.234.722",
            "value": Decimal("14353234722"),
            "row_label": "13. Lợi nhuận khác",
        },
        2017: {
            "uid": "a7e84fc0ceda6e75c462f4ab602a6f2a18fabb3a0481112ccc22decfba60c8a5",
            "source_line": 263,
            "local_ordinal": 9,
            "page_no": 11,
            "row_index": 14,
            "source_sha256": "612474d3f0c867671c34c21cc55988ddb803a5788d5d1bb2a71842487f743c5f",
            "table_sha256": "c24c610f1a6db20daf2c85ac84bf9ecfb9a49a8e1723b61e15ca9ddb29e400f0",
            "raw_value": "(17.623.021.251)",
            "value": Decimal("-17623021251"),
            "row_label": "13. (Lỗ) lợi nhuận khác",
        },
        2023: {
            "uid": "3753a791bdd6fa245302671efed41f1c61d5306a6701c2b246562134c6f4f1eb",
            "source_line": 259,
            "local_ordinal": 8,
            "page_no": 10,
            "row_index": 14,
            "source_sha256": "156571f26ec66f6968a6745fe842dbcbff9db340c9752dfb5822d449c5fbef7a",
            "table_sha256": "9f0d74556b97606a248655ef6224856e54083bdc19082d918f74f753d5835205",
            "raw_value": "(78.924.918.067)",
            "value": Decimal("-78924918067"),
            "row_label": "13. (Lỗ) lợi nhuận khác",
        },
        2025: {
            "uid": "321fadd7aaf14bf2532a981f5e75e912e2b820b5078a3b2961aa03d59447ba03",
            "source_line": 260,
            "local_ordinal": 9,
            "page_no": 10,
            "row_index": 14,
            "source_sha256": "97144bc1ab2de32745f7d8451321fee4d6340353acba44e233f0e8e5a3775b5c",
            "table_sha256": "523423c54f905f9e1c2abd17c25ee1fe29ede69738abad221660e265c0eb8adc",
            "raw_value": "39.908.113.997",
            "value": Decimal("39908113997"),
            "row_label": "13. Lợi nhuận (lỗ) khác",
        },
    },
}


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower().replace("đ", "d")).strip()


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


def load_tables(asset_path: Path) -> dict[str, dict[str, Any]]:
    expected = {
        details["uid"]
        for scope_details in EXPECTED.values()
        for details in scope_details.values()
    }
    found: dict[str, dict[str, Any]] = {}
    with asset_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            table = json.loads(line)
            uid = str(table.get("internal_table_uid") or "")
            if uid not in expected:
                continue
            if uid in found:
                raise ValueError(f"duplicate expected UID at asset line {line_number}: {uid}")
            found[uid] = table
    missing = sorted(expected - set(found))
    if missing:
        raise ValueError(f"missing expected UIDs: {missing}")
    return found


def verify_table(
    table: Mapping[str, Any],
    *,
    scope: str,
    year: int,
    source_line_map: Mapping[str, Any],
) -> dict[str, Any]:
    expected = EXPECTED[scope][year]
    uid = expected["uid"]
    if str(table.get("internal_table_uid") or "") != uid:
        raise ValueError(f"{scope} {year}: UID mismatch")
    if str(table.get("ticker") or "").upper() != "TTF":
        raise ValueError(f"{scope} {year}: ticker mismatch")
    if str(table.get("scope") or "").lower() != scope:
        raise ValueError(f"{scope} {year}: scope mismatch")
    if int(table.get("report_year")) != year:
        raise ValueError(f"{scope} {year}: report year mismatch")
    if int(table.get("local_ordinal")) != expected["local_ordinal"]:
        raise ValueError(f"{scope} {year}: local ordinal mismatch")
    if int(table.get("page_no")) != expected["page_no"]:
        raise ValueError(f"{scope} {year}: page mismatch")
    if str(table.get("table_sha256") or "").lower() != expected["table_sha256"]:
        raise ValueError(f"{scope} {year}: table hash metadata mismatch")
    function = table.get("table_function") or {}
    purpose = table.get("table_purpose") or {}
    if str(function.get("kind") or "") != "income_statement":
        raise ValueError(f"{scope} {year}: table kind mismatch")
    if str(purpose.get("kind") or "") != "period_comparison":
        raise ValueError(f"{scope} {year}: table purpose mismatch")

    source_path = Path(str(table.get("source_path") or ""))
    expected_path = Path(
        f"/home/dungle/Documents/AI_guru/data/ViFinQA/financial_statements/TTF/{year}/"
        f"TTF_financial_statements_{year}_{scope}/"
        f"TTF_financial_statements_{year}_{scope}_extracted.txt"
    )
    if source_path != expected_path:
        raise ValueError(f"{scope} {year}: source path mismatch")
    raw_bytes = source_path.read_bytes()
    source_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if source_sha != expected["source_sha256"] or str(table.get("source_sha256") or "").lower() != source_sha:
        raise ValueError(f"{scope} {year}: source hash mismatch")

    source_text = raw_bytes.decode("utf-8", errors="replace")
    char_start = int(table["char_start"])
    char_end = int(table["char_end"])
    byte_start = int(table["byte_start"])
    byte_end = int(table["byte_end"])
    if not (0 <= char_start < char_end <= len(source_text)):
        raise ValueError(f"{scope} {year}: invalid character coordinates")
    if not (0 <= byte_start < byte_end <= len(raw_bytes)):
        raise ValueError(f"{scope} {year}: invalid byte coordinates")
    char_slice = source_text[char_start:char_end]
    byte_slice = raw_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
    if char_slice != byte_slice:
        raise ValueError(f"{scope} {year}: byte/character slices disagree")
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(source_line_map.get(uid, -1)) != expected["source_line"] or actual_line != expected["source_line"]:
        raise ValueError(f"{scope} {year}: source-line mismatch")

    source_context = normalize(source_text[max(0, char_start - 4000):char_start])
    required_context = [
        "bao cao ket qua hoat dong kinh doanh",
        str(year),
        "vnd",
        "hop nhat" if scope == "consolidated" else "rieng",
    ]
    if any(token not in source_context for token in required_context):
        raise ValueError(f"{scope} {year}: bounded source title/unit context missing")

    headers = list(table.get("headers") or [])
    if len(headers) <= 3 or "nam nay" not in normalize(headers[3]):
        raise ValueError(f"{scope} {year}: current-period header mismatch")
    rows = table.get("rows") or []
    row_index = expected["row_index"]
    if row_index >= len(rows) or not isinstance(rows[row_index], list):
        raise ValueError(f"{scope} {year}: code-40 row missing")
    row = rows[row_index]
    label = canonical_label(row[1] if len(row) > 1 else "")
    if canonical_label(row[0] if row else "") != "40":
        raise ValueError(f"{scope} {year}: statement code mismatch")
    if "loi nhuan khac" not in label and "lo khac" not in label:
        raise ValueError(f"{scope} {year}: other-profit row alias mismatch: {row!r}")
    if len(row) <= 3:
        raise ValueError(f"{scope} {year}: current value cell missing")
    current = parse_decimal(row[3])
    if current != expected["value"] or str(row[3]) != expected["raw_value"]:
        raise ValueError(f"{scope} {year}: raw current value mismatch: {row!r}")
    if expected["raw_value"] not in char_slice:
        raise ValueError(f"{scope} {year}: bounded source slice lacks raw value")

    return {
        "scope": scope,
        "year": year,
        "internal_table_uid": uid,
        "document_id": str(table["document_id"]),
        "source_line": expected["source_line"],
        "local_ordinal": expected["local_ordinal"],
        "page_no": expected["page_no"],
        "row_index": row_index,
        "column_index": 3,
        "row_label": str(row[1]),
        "current_header": str(headers[3]),
        "raw_value": str(row[3]),
        "replayed_value_vnd": str(current),
        "source_sha256": source_sha,
        "table_sha256": str(table["table_sha256"]),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "bounded_vnd_statement_context_checked": True,
            "statement_code_40_checked": True,
            "other_profit_loss_alias_checked": True,
            "current_nam_nay_column_checked": True,
            "scope_checked": True,
        },
    }


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    tables = load_tables(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    if not isinstance(source_line_map, dict):
        raise ValueError("source-line map must be a JSON object")
    records: list[dict[str, Any]] = []
    for scope in SCOPES:
        for year in YEARS:
            uid = EXPECTED[scope][year]["uid"]
            records.append(
                verify_table(
                    tables[uid],
                    scope=scope,
                    year=year,
                    source_line_map=source_line_map,
                )
            )

    winner_by_scope: dict[str, int] = {}
    values_by_scope: dict[str, list[str]] = {}
    for scope in SCOPES:
        scoped = [record for record in records if record["scope"] == scope]
        values = [Decimal(record["replayed_value_vnd"]) for record in scoped]
        maximum = max(values)
        winners = [record["year"] for record, value in zip(scoped, values) if value == maximum]
        if len(winners) != 1:
            raise ValueError(f"{scope}: expected a unique maximum, got {winners}")
        winner_by_scope[scope] = winners[0]
        values_by_scope[scope] = [str(value) for value in values]
    if len(set(winner_by_scope.values())) != 1:
        raise ValueError(f"scope winner disagreement: {winner_by_scope}")
    return {
        "protocol": "vifinqa_independent_other_profit_q829_scope_consensus_replay_v1",
        "question_id": 829,
        "ticker": "TTF",
        "metric": "Lợi nhuận khác / Lỗ khác (mã số 40)",
        "operation": "argmax_period",
        "years": list(YEARS),
        "scopes_checked": list(SCOPES),
        "records_checked": len(records),
        "winner_by_scope": winner_by_scope,
        "values_by_scope": values_by_scope,
        "winner_year": next(iter(winner_by_scope.values())),
        "scope_winner_consensus": True,
        "status": "PASS",
        "promotion_allowed": False,
        "strict_certificate": False,
        "official_score_measured": False,
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--source-line-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.asset.expanduser().resolve(), args.source_line_map.expanduser().resolve())
    args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.expanduser().resolve().write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
