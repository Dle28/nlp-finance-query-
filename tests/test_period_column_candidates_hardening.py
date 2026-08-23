"""Snapshot and adversarial lineage checks for period-column packets V1.

These tests deliberately exercise only candidate metadata.  They never select a
column/value or alter the frozen V1 artifacts.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

import pytest

from finance_query.period_column_candidates import (
    _candidate_source_contract,
    _exclusive_cause,
    enumerate_period_columns,
    sha256_file,
)
from tests.test_period_column_candidates import (
    _materialization_fixture,
    _navigation_row,
    _run,
    _table,
    _write_json,
)


ROOT = Path(__file__).resolve().parents[1]
PERIOD_DIR = ROOT / "artifacts/research/period_column_candidates_v1"
PACKETS = PERIOD_DIR / "period_column_candidate_packets_v1.jsonl"
MANIFEST = PERIOD_DIR / "period_column_candidate_packets_v1.manifest.json"
EXAMPLES = PERIOD_DIR / "period_column_candidate_packets_v1.examples.jsonl"
NO_CANDIDATE_AUDIT = PERIOD_DIR / "route_packet_no_candidate_audit_v1.jsonl"

EXPECTED_SHA256 = {
    PACKETS: "a3426d35b3e53be02599e2507c442d1ee97df34102e678cca2f74a767df0ab98",
    MANIFEST: "529ff64433567d89e519e9e6b2d694d6c9c33f6dbaaaecc94c3ed974038cc155",
    EXAMPLES: "b8de0855cd6f537ad057882d34b064425b79c2ddac00cd783678acd4d460c97d",
    NO_CANDIDATE_AUDIT: "09ed08f1d444a20fd5776fa24b86aadbcaec9ca7c895d28af3b75f7bd4dc84e3",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _refresh_v2_hash_bindings(files: dict[str, Path]) -> None:
    """Keep a synthetic fixture hash-valid so the structural check is reached."""
    v2_hash = sha256_file(files["v2.jsonl"])
    for manifest_name, path in (("v2.manifest.json", ("sidecar_sha256",)), ("routing.manifest.json", ("input_structure_sha256",))):
        manifest = json.loads(files[manifest_name].read_text(encoding="utf-8"))
        manifest[path[0]] = v2_hash
        _write_json(files[manifest_name], manifest)
    v3_manifest = json.loads(files["v3.manifest.json"].read_text(encoding="utf-8"))
    v3_manifest["input_structure_sha256"] = v2_hash
    _write_json(files["v3.manifest.json"], v3_manifest)
    packet_manifest = json.loads(files["packets.manifest.json"].read_text(encoding="utf-8"))
    packet_manifest["inputs"]["structured_tables"]["sha256"] = v2_hash
    packet_manifest["inputs"]["structure_manifest"]["sha256"] = sha256_file(files["v2.manifest.json"])
    packet_manifest["inputs"]["routing_manifest"]["sha256"] = sha256_file(files["routing.manifest.json"])
    _write_json(files["packets.manifest.json"], packet_manifest)


def _refresh_v3_hash_binding(files: dict[str, Path]) -> None:
    manifest = json.loads(files["v3.manifest.json"].read_text(encoding="utf-8"))
    manifest["sidecar_sha256"] = sha256_file(files["v3.jsonl"])
    _write_json(files["v3.manifest.json"], manifest)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_frozen_period_snapshot_hashes_and_counts() -> None:
    for path, expected in EXPECTED_SHA256.items():
        assert path.exists(), f"missing frozen V1 artifact: {path}"
        assert sha256_file(path) == expected
    records = _read_jsonl(PACKETS)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(records) == 1012
    assert {record["question_id"] for record in records} == set(range(1, 1013))
    assert Counter(record["packet_status"] for record in records) == {
        "unique_period_column_candidate": 15,
        "ambiguous_period_columns": 9,
        "no_period_column": 7,
        "packet_blocked": 981,
    }
    assert manifest["period_column_candidate_count"] == 60
    assert manifest["no_candidate_audit_record_count"] == 91


@pytest.mark.parametrize("packet_status", ["route_blocked", "no_candidate"])
def test_non_bounded_route_packets_cannot_emit_period_candidates(tmp_path: Path, packet_status: str) -> None:
    result = _run(
        _materialization_fixture(
            tmp_path / packet_status,
            packet_status=packet_status,
            # A no-candidate packet needs an actually missing exact concept in
            # this self-contained fixture; otherwise its audit correctly finds
            # the only row to be gate-passing and rejects the synthetic input.
            no_exact=packet_status == "no_candidate",
        )
    )
    record = _read_jsonl(Path(result["outputs"]["period_packets"]["path"]))[0]
    operand = record["stages"][0]["required_operands"][0]
    assert record["packet_status"] == "packet_blocked"
    assert operand["column_status"] == "packet_blocked"
    assert operand["period_column_candidates"] == []


def test_v2_raw_row_tamper_fails_after_hashes_are_refreshed(tmp_path: Path) -> None:
    files = _materialization_fixture(tmp_path / "raw-row")
    rows = _read_jsonl(files["v2.jsonl"])
    rows[0]["rows"][1][2] = "tampered-source-cell"
    files["v2.jsonl"].write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    _refresh_v2_hash_bindings(files)
    with pytest.raises(ValueError, match="raw source row mismatch"):
        _run(files)


def test_v2_cell_provenance_tamper_fails_after_hashes_are_refreshed(tmp_path: Path) -> None:
    files = _materialization_fixture(tmp_path / "v2-provenance")
    rows = _read_jsonl(files["v2.jsonl"])
    rows[0]["cell_provenance"][1].pop()
    files["v2.jsonl"].write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    _refresh_v2_hash_bindings(files)
    with pytest.raises(ValueError, match="cell provenance coverage mismatch"):
        _run(files)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row["canonical_headers"]["columns"].pop(), "canonical header coverage mismatch"),
        (lambda row: row["row_profiles"].pop(), "row profile coverage mismatch"),
    ],
)
def test_v3_header_and_row_profile_tamper_fail_after_hashes_are_refreshed(
    tmp_path: Path, mutate, message: str
) -> None:
    files = _materialization_fixture(tmp_path / message.replace(" ", "-"))
    rows = _read_jsonl(files["v3.jsonl"])
    mutate(rows[0])
    files["v3.jsonl"].write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    _refresh_v3_hash_binding(files)
    with pytest.raises(ValueError, match=message):
        _run(files)


def test_start_date_is_not_instant_end_and_end_date_is_not_duration() -> None:
    v2, v3 = _table()
    column = v3["canonical_headers"]["columns"][2]
    column["source_label"] = "01/01/2023 VND"
    column["period_labels"] = ["01/01/2023"]
    status, candidates, reasons, _ = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="instant", requested_years={2023}, v2=v2, v3=v3
    )
    assert status == "no_period_column"
    assert candidates == []
    assert reasons["PERIOD_TYPE_MISMATCH"] == 1
    column["source_label"] = "31/12/2023 VND"
    column["period_labels"] = ["31/12/2023"]
    status, candidates, reasons, _ = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="duration", requested_years={2023}, v2=v2, v3=v3
    )
    assert status == "no_period_column"
    assert candidates == []
    assert reasons["PERIOD_TYPE_MISMATCH"] == 1


def test_cumulative_period_is_not_treated_as_full_year() -> None:
    v2, v3 = _table()
    column = v3["canonical_headers"]["columns"][2]
    column["source_label"] = "Lũy kế năm 2023 VND"
    column["period_labels"] = ["2023"]
    status, candidates, reasons, _ = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="duration", requested_years={2023}, v2=v2, v3=v3
    )
    assert status == "no_period_column"
    assert candidates == []
    assert reasons["CUMULATIVE_PERIOD_LABEL_UNSUPPORTED"] == 1


def test_overlapping_raw_rejections_are_not_exclusive_primary_causes() -> None:
    raw_rejections = Counter({"entity": 2, "year": 2, "scope": 1})
    cause, minimal = _exclusive_cause([{"entity", "year"}, {"entity", "scope"}])
    assert cause == "ENTITY"
    assert minimal == ["entity"]
    assert dict(raw_rejections) != {cause: sum(raw_rejections.values())}


def test_recursive_candidate_scan_has_no_forbidden_semantic_fields() -> None:
    forbidden = {"selected", "resolved", "value", "parsed_value", "answer", "formula_result"}
    for node in _walk(_read_jsonl(PACKETS)):
        assert not forbidden.intersection(node)


def test_all_candidate_contracts_remain_non_promotable() -> None:
    expected = _candidate_source_contract()
    contracts = [node["source_contract"] for node in _walk(_read_jsonl(PACKETS)) if "source_contract" in node]
    assert contracts
    for contract in contracts:
        for field, value in expected.items():
            assert contract.get(field) is value
