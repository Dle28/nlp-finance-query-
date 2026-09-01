#!/usr/bin/env python3
"""Independently replay the BSR Q338 supplier-payable source cell.

The checker intentionally does not import the supplier-payable route adapter.
It verifies both agreeing V2 duplicates (separate and consolidated), their
source hashes and coordinates, the local ``Phải trả nhà cung cấp`` section,
the named counterparty row, the 31/12/2018 VND column, and the unit conversion
to thousand-billion VND.  This is a source-first research finding, not a gold
label or a strict E2E certificate.
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


EXPECTED_UIDS = {
    "separate": "7611088abf3e60364ae6ca438477f4745ec0e17747c9455c519e21ba92d6d47a",
    "consolidated": "4c935ffee5296f3f7d86d22032b68ad2f67d9981741d4265f027fb4a0f400f1f",
}
EXPECTED_SOURCE_LINES = {"separate": 1174, "consolidated": 1224}
EXPECTED_SOURCE_SHA256 = {
    "separate": "14d31c26401d759c27bf00ce48d6589be31a8892b3d050c44f5b2753e0f0b5b9",
    "consolidated": "dbffbc622fe0258ba095a6155d1e896178f27be59cd528170a6ebb00f644bfbd",
}
EXPECTED_TABLE_SHA256 = {
    "separate": "995f5f84e810d2ed628399c0d0aedacf0f035028cb2919b62718f15e4f36ec41",
    "consolidated": "0c71de80965786245dc9bff0b1fde56f2e9dda2062292545e7b4e901c942334e",
}
EXPECTED_TABLE_KINDS = {
    "separate": "related_party_schedule",
    "consolidated": "financial_note",
}
EXPECTED_TABLE_PURPOSES = {
    "separate": "relationship_detail",
    "consolidated": "period_comparison",
}
EXPECTED_DOCUMENTS = {
    "separate": "BSR_financial_statements_2018_separate",
    "consolidated": "BSR_financial_statements_2018_consolidated",
}
EXPECTED_ROW_INDEX = {"separate": 22, "consolidated": 3}
EXPECTED_SECTION_INDEX = {"separate": 21, "consolidated": 2}
EXPECTED_RAW_VALUE = Decimal("2499485052166")
EXPECTED_PRIOR_RAW_VALUE = Decimal("3986408656102")
EXPECTED_OUTPUT_DIVISOR = Decimal("1000000000000")
EXPECTED_ANSWER = Decimal("2.499485052166")
EXPECTED_SECTION = "phai tra nha cung cap"
EXPECTED_COUNTERPARTY = "tong cong ty dau viet nam cong ty co phan"


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


def load_tables(asset_path: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    expected_uid_to_scope = {uid: scope for scope, uid in EXPECTED_UIDS.items()}
    with asset_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            table = json.loads(line)
            uid = str(table.get("internal_table_uid") or "")
            scope = expected_uid_to_scope.get(uid)
            if scope is None:
                continue
            if scope in found:
                raise ValueError(f"duplicate expected UID at asset line {line_number}: {uid}")
            found[scope] = table
    missing = sorted(set(EXPECTED_UIDS) - set(found))
    if missing:
        raise ValueError(f"missing expected table scopes: {missing}")
    return found


def verify_coordinates(
    table: Mapping[str, Any],
    *,
    scope: str,
    source_line_map: Mapping[str, Any],
) -> tuple[int, str]:
    source_path = Path(str(table["source_path"]))
    raw_bytes = source_path.read_bytes()
    expected_sha = EXPECTED_SOURCE_SHA256[scope]
    actual_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
    if actual_sha != expected_sha or str(table.get("source_sha256") or "").lower() != expected_sha:
        raise ValueError(
            f"{scope} source hash mismatch: expected={expected_sha} actual={actual_sha}"
        )

    source_text = raw_bytes.decode("utf-8", errors="replace")
    char_start = int(table["char_start"])
    char_end = int(table["char_end"])
    byte_start = int(table["byte_start"])
    byte_end = int(table["byte_end"])
    if not (0 <= char_start < char_end <= len(source_text)):
        raise ValueError(f"{scope} invalid character coordinates")
    if not (0 <= byte_start < byte_end <= len(raw_bytes)):
        raise ValueError(f"{scope} invalid byte coordinates")
    byte_slice = raw_bytes[byte_start:byte_end].decode("utf-8", errors="replace")
    char_slice = source_text[char_start:char_end]
    if byte_slice != char_slice:
        raise ValueError(f"{scope} byte and character table slices disagree")

    expected_line = EXPECTED_SOURCE_LINES[scope]
    mapped_line = source_line_map.get(str(table["internal_table_uid"]))
    actual_line = source_text.count("\n", 0, char_start) + 1
    if int(mapped_line) != expected_line or actual_line != expected_line:
        raise ValueError(
            f"{scope} source-line mismatch: map={mapped_line!r} actual={actual_line} "
            f"expected={expected_line}"
        )
    normalized_source = canonical_label(char_slice)
    if EXPECTED_SECTION not in normalized_source or EXPECTED_COUNTERPARTY not in normalized_source:
        raise ValueError(f"{scope} required section/counterparty missing from bounded source slice")
    if "2.499.485.052.166" not in char_slice:
        raise ValueError(f"{scope} expected raw value missing from bounded source slice")
    return actual_line, char_slice


def verify_table(
    table: Mapping[str, Any],
    *,
    scope: str,
    source_line_map: Mapping[str, Any],
) -> dict[str, Any]:
    uid = EXPECTED_UIDS[scope]
    if str(table.get("internal_table_uid") or "") != uid:
        raise ValueError(f"{scope} UID mismatch")
    if str(table.get("document_id") or "") != EXPECTED_DOCUMENTS[scope]:
        raise ValueError(f"{scope} document mismatch")
    if str(table.get("ticker") or "").upper() != "BSR":
        raise ValueError(f"{scope} ticker mismatch")
    if str(table.get("scope") or "").lower() != scope:
        raise ValueError(f"{scope} reporting scope mismatch")
    if int(table.get("report_year")) != 2018:
        raise ValueError(f"{scope} report year mismatch")
    if int(table.get("local_ordinal")) != (49 if scope == "separate" else 52):
        raise ValueError(f"{scope} local ordinal mismatch")
    if int(table.get("page_no")) != (38 if scope == "separate" else 41):
        raise ValueError(f"{scope} page mismatch")
    if str(table.get("unit_hint") or "").lower() != "vnd":
        raise ValueError(f"{scope} source unit is not VND")
    function = table.get("table_function") or {}
    purpose = table.get("table_purpose") or {}
    if str(function.get("kind") or "") != EXPECTED_TABLE_KINDS[scope]:
        raise ValueError(f"{scope} table kind mismatch")
    if str(purpose.get("kind") or "") != EXPECTED_TABLE_PURPOSES[scope]:
        raise ValueError(f"{scope} table purpose mismatch")
    if str(table.get("table_sha256") or "").lower() != EXPECTED_TABLE_SHA256[scope]:
        raise ValueError(f"{scope} table hash metadata mismatch")

    source_line, _ = verify_coordinates(
        table, scope=scope, source_line_map=source_line_map
    )
    headers = list(table.get("headers") or [])
    if len(headers) != 3:
        raise ValueError(f"{scope} unexpected header count: {headers!r}")
    if "31/12/2018" not in normalize(headers[1]) or "vnd" not in normalize(headers[1]):
        raise ValueError(f"{scope} current-period VND column mismatch: {headers!r}")
    if "01/7/2018" not in normalize(headers[2]) or "vnd" not in normalize(headers[2]):
        raise ValueError(f"{scope} prior-period VND column mismatch: {headers!r}")

    rows = table.get("rows") or []
    section_index = EXPECTED_SECTION_INDEX[scope]
    row_index = EXPECTED_ROW_INDEX[scope]
    if len(rows) <= row_index or len(rows) <= section_index:
        raise ValueError(f"{scope} row indexes outside table")
    section_row = rows[section_index]
    target_row = rows[row_index]
    if not isinstance(section_row, list) or canonical_label(section_row[0]) != EXPECTED_SECTION:
        raise ValueError(f"{scope} supplier section anchor mismatch")
    if not isinstance(target_row, list) or canonical_label(target_row[0]) != EXPECTED_COUNTERPARTY:
        raise ValueError(f"{scope} counterparty row mismatch: {target_row!r}")
    if parse_decimal(target_row[1]) != EXPECTED_RAW_VALUE:
        raise ValueError(f"{scope} current raw value mismatch: {target_row[1]!r}")
    if parse_decimal(target_row[2]) != EXPECTED_PRIOR_RAW_VALUE:
        raise ValueError(f"{scope} prior raw value mismatch: {target_row[2]!r}")
    answer = EXPECTED_RAW_VALUE / EXPECTED_OUTPUT_DIVISOR
    if answer != EXPECTED_ANSWER:
        raise ValueError(f"internal answer arithmetic mismatch: {answer}")
    return {
        "scope": scope,
        "document_id": str(table["document_id"]),
        "internal_table_uid": uid,
        "source_line": source_line,
        "source_path": str(table["source_path"]),
        "source_sha256": str(table["source_sha256"]),
        "table_sha256": str(table["table_sha256"]),
        "local_ordinal": int(table["local_ordinal"]),
        "page_no": int(table["page_no"]),
        "section_index": section_index,
        "row_index": row_index,
        "column_index": 1,
        "section_label": str(section_row[0]),
        "row_label": str(target_row[0]),
        "current_header": str(headers[1]),
        "prior_header": str(headers[2]),
        "current_raw_value": str(EXPECTED_RAW_VALUE),
        "prior_raw_value": str(EXPECTED_PRIOR_RAW_VALUE),
        "source_multiplier": "1",
        "requested_output_divisor": str(EXPECTED_OUTPUT_DIVISOR),
        "answer_decimal": str(answer),
        "checks": {
            "exact_uid": True,
            "source_hash_checked": True,
            "table_hash_checked": True,
            "source_line_checked": True,
            "byte_character_coordinates_checked": True,
            "safe_table_kind_checked": True,
            "supplier_section_checked": True,
            "exact_counterparty_checked": True,
            "current_vnd_column_checked": True,
            "prior_vnd_column_checked": True,
            "scope_and_year_checked": True,
            "decimal_unit_conversion_checked": True,
        },
    }


def replay(asset_path: Path, source_line_map_path: Path) -> dict[str, Any]:
    tables = load_tables(asset_path)
    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    if not isinstance(source_line_map, dict):
        raise ValueError("source-line map must be a JSON object")
    per_scope = {
        scope: verify_table(table, scope=scope, source_line_map=source_line_map)
        for scope, table in tables.items()
    }
    answers = {value["answer_decimal"] for value in per_scope.values()}
    if answers != {str(EXPECTED_ANSWER)}:
        raise ValueError(f"duplicate scope answers disagree: {sorted(answers)}")
    return {
        "protocol": "vifinqa_independent_supplier_payable_q338_replay_v1",
        "question_id": 338,
        "ticker": "BSR",
        "report_year": 2018,
        "metric": "Số dư phải trả cho nhà cung cấp Tổng Công ty Dầu Việt Nam - CTCP",
        "operation": "consensus_named_supplier_payable_lookup",
        "selected_scope": "separate",
        "selected_internal_table_uid": EXPECTED_UIDS["separate"],
        "candidate_scope_count": len(per_scope),
        "candidate_scope_answers_agree": True,
        "candidate_scope_replay": per_scope,
        "answer_decimal": str(EXPECTED_ANSWER),
        "source_multiplier": "1",
        "requested_output_divisor": str(EXPECTED_OUTPUT_DIVISOR),
        "checks": {
            "two_exact_duplicate_scopes_replayed": True,
            "duplicate_scope_consensus_checked": True,
            "selected_scope_is_explicit": True,
            "source_first_decimal_replay_checked": True,
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
