from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.period_column_candidates import (
    _candidate_source_contract,
    _exclusive_cause,
    _prefer_exact_document_year_when_equivalent,
    enumerate_period_columns,
    materialize_period_column_packets,
    sha256_file,
)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _legacy_contract() -> dict[str, bool]:
    return {
        "navigation_metadata_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_execute_formula": False,
    }


def _cell_provenance(rows: list[list[str]]) -> list[list[dict[str, object]]]:
    return [[{"source_row": r, "source_cell": c, "anchor_row": r, "anchor_column": c, "covered_by_span": False} for c in range(len(row))] for r, row in enumerate(rows)]


def _table(uid: str = "u1", document_id: str = "d1", *, unreliable: bool = False) -> tuple[dict, dict]:
    rows = [["", "Mã số", "Năm 2023 VND", "Năm 2022 VND"], ["Tài sản", "100", "100", "90"]]
    source = {"source_path": "fixture", "source_sha256": "fixture", "char_start": 1, "table_sha256": "fixture"}
    v2 = {"internal_table_uid": uid, "document_id": document_id, "rows": rows, "cell_provenance": _cell_provenance(rows), "source_provenance": source}
    v3 = {
        "internal_table_uid": uid,
        "document_id": document_id,
        "grid": {"rectangular": True, "provenance_complete": True, "width": 4, "reason_codes": []},
        "source_provenance": source,
        "canonical_headers": {"columns": [
            {"column_index": 0, "source_label": "", "header_source_cells": [], "period_labels": [], "unit_labels": [], "role": "row_label"},
            {"column_index": 1, "source_label": "Mã số", "header_source_cells": [{"row_index": 0, "column_index": 1}], "period_labels": [], "unit_labels": [], "role": "reference"},
            {"column_index": 2, "source_label": "Năm 2023 VND", "header_source_cells": [{"row_index": 0, "column_index": 2}], "period_labels": ["2023"], "unit_labels": ["VND"], "role": "value_or_text"},
            {"column_index": 3, "source_label": "Năm 2022 VND", "header_source_cells": [{"row_index": 0, "column_index": 3}], "period_labels": ["2022"], "unit_labels": ["VND"], "role": "value_or_text"},
        ]},
        "row_profiles": [
            {"row_index": 0, "role": "header", "numeric_columns": [], "structural_numeric_columns": [], "unreliable_numeric_columns": []},
            {"row_index": 1, "role": "data", "numeric_columns": [1, 3] if unreliable else [1, 2, 3], "structural_numeric_columns": [1, 2, 3], "unreliable_numeric_columns": [2] if unreliable else []},
        ],
    }
    return v2, v3


def _navigation_row(uid: str = "u1", document_id: str = "d1") -> dict:
    return {"internal_table_uid": uid, "document_id": document_id, "row_index": 1, "raw_source_row": ["Tài sản", "100", "100", "90"], "source_contract": _legacy_contract()}


def test_exact_year_header_and_source_coordinates_are_preserved() -> None:
    v2, v3 = _table()
    status, candidates, _reasons, unreliable = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="duration", requested_years={2023}, v2=v2, v3=v3
    )
    assert status == "unique_period_column_candidate"
    assert unreliable == 0
    assert candidates == [{
        "internal_table_uid": "u1", "row_index": 1, "column_index": 2, "requested_year": 2023,
        "source_label": "Năm 2023 VND", "header_source_cells": [{"row_index": 0, "column_index": 2}],
        "period_labels": ["2023"], "unit_labels": ["VND"], "raw_source_cell": "100",
        "cell_provenance": v2["cell_provenance"][1][2],
        "reason_codes": ["EXACT_REQUESTED_YEAR", "DURATION_PERIOD_LABEL"],
        "source_contract": _candidate_source_contract(),
    }]
    assert "selected" not in candidates[0]
    assert "value" not in candidates[0]


def test_prior_year_and_instant_duration_mismatches_are_not_candidates() -> None:
    v2, v3 = _table()
    status, candidates, reasons, _ = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="duration", requested_years={2024}, v2=v2, v3=v3
    )
    assert status == "no_period_column"
    assert candidates == []
    assert reasons["NO_EXACT_REQUESTED_YEAR_PERIOD_LABEL"] == 2
    v3["canonical_headers"]["columns"][2]["source_label"] = "31/12/2023 VND"
    v3["canonical_headers"]["columns"][2]["period_labels"] = ["31/12/2023"]
    status, candidates, reasons, _ = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="duration", requested_years={2023}, v2=v2, v3=v3
    )
    assert status == "no_period_column"
    assert candidates == []
    assert reasons["PERIOD_TYPE_MISMATCH"] == 1


def test_unreliable_numeric_column_is_vetoed() -> None:
    v2, v3 = _table(unreliable=True)
    status, candidates, reasons, unreliable = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="duration", requested_years={2023}, v2=v2, v3=v3
    )
    assert status == "unreliable_numeric_source"
    assert candidates == []
    assert unreliable == 1
    assert reasons["UNRELIABLE_NUMERIC_SOURCE_VETO"] == 1


def test_multiple_exact_period_columns_remain_ambiguous() -> None:
    v2, v3 = _table()
    v3["canonical_headers"]["columns"][3]["source_label"] = "Năm 2023 (trình bày lại) VND"
    v3["canonical_headers"]["columns"][3]["period_labels"] = ["2023"]
    status, candidates, _reasons, _ = enumerate_period_columns(
        navigation_row=_navigation_row(), period_type="duration", requested_years={2023}, v2=v2, v3=v3
    )
    assert status == "ambiguous_period_columns"
    assert [candidate["column_index"] for candidate in candidates] == [2, 3]


def test_exact_document_year_only_collapses_identical_comparative_source_duplicate() -> None:
    base = {
        "row_index": 1,
        "column_index": 3,
        "requested_year": 2023,
        "raw_source_cell": "1.234.000.000",
        "unit_labels": ["VND"],
        "reason_codes": ["EXACT_REQUESTED_YEAR", "DURATION_PERIOD_LABEL"],
    }
    direct = {**base, "internal_table_uid": "direct"}
    comparative = {**base, "internal_table_uid": "comparative", "column_index": 4}
    tables = {
        "direct": {"document_id": "report-2023"},
        "comparative": {"document_id": "report-2024"},
    }
    documents = {"report-2023": {"report_year": 2023}, "report-2024": {"report_year": 2024}}

    selected = _prefer_exact_document_year_when_equivalent(
        candidates=[comparative, direct], v2_by_uid=tables, document_by_id=documents
    )

    assert [candidate["internal_table_uid"] for candidate in selected] == ["direct"]
    assert selected[0]["equivalent_candidate_count"] == 2
    assert selected[0]["reason_codes"][-1] == "DOCUMENT_REPORT_YEAR_EXACT_PREFERENCE_AMONG_EQUAL_SOURCE_VALUES"

    conflicting = _prefer_exact_document_year_when_equivalent(
        candidates=[comparative, {**direct, "raw_source_cell": "1.235.000.000"}],
        v2_by_uid=tables,
        document_by_id=documents,
    )
    assert {candidate["internal_table_uid"] for candidate in conflicting} == {"direct", "comparative"}


def test_minimal_blocker_cause_is_deterministic_and_empty_corpus_is_explicit() -> None:
    assert _exclusive_cause([]) == ("NO_EXACT_CONCEPT_ROW", [])
    assert _exclusive_cause([{"entity", "year"}, {"entity", "scope"}]) == ("ENTITY", ["entity"])
    assert _exclusive_cause([{"entity"}, {"year"}]) == ("MULTIPLE_INSEPARABLE_BLOCKERS", ["entity", "year"])


def _materialization_fixture(tmp_path: Path, *, packet_status: str = "bounded", duplicate_packets: bool = False, duplicate_v2: bool = False, no_exact: bool = False) -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    v2, v3 = _table()
    tables = [v2, dict(v2)] if duplicate_v2 else [v2]
    contexts = [v3]
    candidate_concept = "other" if no_exact else "assets"
    candidate = {
        "internal_table_uid": "u1", "document_id": "d1", "row_index": 1, "raw_source_row": v2["rows"][1],
        "table_type": "balance_sheet", "table_type_status": "source_structural", "match_status": "exact_unique",
        "concept_candidates": [{"concept_id": candidate_concept}], "navigation_gate_status": "ready", "source_contract": {"evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False},
    }
    routing = {"internal_table_uid": "u1", "document_id": "d1", "company": "ACME", "report_year": 2023, "report_scope": "separate", "table_type": "balance_sheet", "table_type_status": "source_structural", "routing_eligible": True, "available_period_years": [2023]}
    document = {"document_id": "d1", "company": "ACME", "report_year": 2023, "report_scope": "separate"}
    role = {"internal_table_uid": "u1", "document_id": "d1", "existing_table_type": "balance_sheet", "existing_table_type_status": "source_structural", "proposed_table_type": "balance_sheet", "status": "source_structural", "reason_codes": []}
    sector = {"document_id": "d1", "sector": "unknown"}
    context = {"entities": ["ACME"], "years": [2023], "scope": "separate"}
    operand = {"role": "assets", "concept_id": "assets", "period_type": "duration", "statement_types": ["balance_sheet"]}
    stage = {"stage_id": "s1", "route_kind": "metric", "metric_id": "assets", "concept_id": None, "required_operands": [operand], "retrieval_filters": {"sectors": []}}
    route = {"question_id": 1, "route_status": "metric_candidate", "question_context": context, "stages": [stage], "source_contract": _legacy_contract()}
    packet_operand = {"role": "assets", "concept_id": "assets", "period_type": "duration", "expected_table_types": ["balance_sheet"], "candidate_count_total": 1 if packet_status == "bounded" else 0, "navigation_candidates": [_navigation_row()] if packet_status == "bounded" else []}
    packet_stage = {"stage_id": "s1", "route_kind": "metric", "metric_id": "assets", "concept_id": None, "required_operands": [packet_operand]}
    packet = {"question_id": 1, "route_status": "metric_candidate", "packet_status": packet_status, "question_context": context, "stages": [packet_stage], "source_contract": _legacy_contract()}
    files = {name: tmp_path / name for name in ("packets.jsonl", "packets.manifest.json", "routes.jsonl", "routes.manifest.json", "candidates.jsonl", "candidates.manifest.json", "roles.jsonl", "sectors.jsonl", "routing.jsonl", "routing.manifest.json", "docs.jsonl", "v2.jsonl", "v2.manifest.json", "v3.jsonl", "v3.manifest.json", "out.jsonl", "audit.jsonl")}
    _write_jsonl(files["routes.jsonl"], [route])
    _write_jsonl(files["packets.jsonl"], [packet, packet] if duplicate_packets else [packet])
    _write_jsonl(files["candidates.jsonl"], [candidate])
    _write_jsonl(files["roles.jsonl"], [role])
    _write_jsonl(files["sectors.jsonl"], [sector])
    _write_jsonl(files["routing.jsonl"], [routing])
    _write_jsonl(files["docs.jsonl"], [document])
    _write_jsonl(files["v2.jsonl"], tables)
    _write_jsonl(files["v3.jsonl"], contexts)
    _write_json(files["routes.manifest.json"], {"output": {"sha256": sha256_file(files["routes.jsonl"])}, "source_contract": _legacy_contract()})
    _write_json(files["candidates.manifest.json"], {"outputs": {"row_candidates": {"sha256": sha256_file(files["candidates.jsonl"])}, "table_role_candidates": {"sha256": sha256_file(files["roles.jsonl"])}, "sector_candidates": {"sha256": sha256_file(files["sectors.jsonl"])}}, "source_contract": _legacy_contract()})
    _write_json(files["routing.manifest.json"], {"table_catalog_sha256": sha256_file(files["routing.jsonl"]), "document_metadata_sha256": sha256_file(files["docs.jsonl"]), "input_structure_sha256": sha256_file(files["v2.jsonl"])})
    _write_json(files["v2.manifest.json"], {"sidecar_sha256": sha256_file(files["v2.jsonl"])})
    _write_json(files["v3.manifest.json"], {"sidecar_sha256": sha256_file(files["v3.jsonl"]), "input_structure_sha256": sha256_file(files["v2.jsonl"])})
    packet_inputs = {
        "question_routes": {"sha256": sha256_file(files["routes.jsonl"])}, "question_routes_manifest": {"sha256": sha256_file(files["routes.manifest.json"])},
        "taxonomy_candidates": {"sha256": sha256_file(files["candidates.jsonl"])}, "taxonomy_candidates_manifest": {"sha256": sha256_file(files["candidates.manifest.json"])},
        "table_roles": {"sha256": sha256_file(files["roles.jsonl"])}, "sectors": {"sha256": sha256_file(files["sectors.jsonl"])},
        "routing_catalog": {"sha256": sha256_file(files["routing.jsonl"])}, "routing_manifest": {"sha256": sha256_file(files["routing.manifest.json"])},
        "document_metadata": {"sha256": sha256_file(files["docs.jsonl"])}, "structured_tables": {"sha256": sha256_file(files["v2.jsonl"])}, "structure_manifest": {"sha256": sha256_file(files["v2.manifest.json"])},
    }
    _write_json(files["packets.manifest.json"], {"question_count": 1, "question_id_count": 1, "outputs": {"packets": {"sha256": sha256_file(files["packets.jsonl"])}}, "inputs": packet_inputs, "source_contract": _legacy_contract()})
    return files


def _run(files: dict[str, Path]) -> dict:
    return materialize_period_column_packets(
        packets_path=files["packets.jsonl"], packets_manifest_path=files["packets.manifest.json"], routes_path=files["routes.jsonl"], routes_manifest_path=files["routes.manifest.json"], candidates_path=files["candidates.jsonl"], candidates_manifest_path=files["candidates.manifest.json"], table_roles_path=files["roles.jsonl"], sectors_path=files["sectors.jsonl"], routing_catalog_path=files["routing.jsonl"], routing_manifest_path=files["routing.manifest.json"], document_metadata_path=files["docs.jsonl"], structured_tables_path=files["v2.jsonl"], structure_manifest_path=files["v2.manifest.json"], evidence_context_path=files["v3.jsonl"], evidence_manifest_path=files["v3.manifest.json"], output=files["out.jsonl"], no_candidate_audit_output=files["audit.jsonl"],
    )


def test_hash_mismatch_duplicate_ids_and_duplicate_table_uid_fail_closed(tmp_path: Path) -> None:
    files = _materialization_fixture(tmp_path / "hash")
    files["packets.manifest.json"].parent.mkdir(parents=True, exist_ok=True)
    _write_json(files["packets.manifest.json"], {"question_count": 1, "question_id_count": 1, "outputs": {"packets": {"sha256": "0" * 64}}, "inputs": {}, "source_contract": _legacy_contract()})
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _run(files)
    with pytest.raises(ValueError, match="duplicate question_id"):
        _run(_materialization_fixture(tmp_path / "duplicate-question", duplicate_packets=True))
    with pytest.raises(ValueError, match="duplicate internal_table_uid"):
        _run(_materialization_fixture(tmp_path / "duplicate-table", duplicate_v2=True))


def test_materialized_packet_is_nonpromotable_and_no_exact_audit_is_explicit(tmp_path: Path) -> None:
    manifest = _run(_materialization_fixture(tmp_path / "bounded"))
    output = Path(manifest["outputs"]["period_packets"]["path"])
    packet = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert packet["packet_status"] == "unique_period_column_candidate"
    assert packet["source_contract"] == _candidate_source_contract()
    assert "answer" not in packet
    assert "formula_result" not in packet
    no_exact = _run(_materialization_fixture(tmp_path / "no-exact", packet_status="no_candidate", no_exact=True))
    audit = json.loads(Path(no_exact["outputs"]["no_candidate_audit"]["path"]).read_text(encoding="utf-8").splitlines()[0])
    assert audit["exclusive_primary_cause"] == "NO_EXACT_CONCEPT_ROW"
    assert audit["minimal_blocker_sets"] == [["NO_EXACT_CONCEPT_ROW"]]
    blocked = json.loads(Path(no_exact["outputs"]["period_packets"]["path"]).read_text(encoding="utf-8").splitlines()[0])
    assert blocked["packet_status"] == "packet_blocked"
    assert blocked["stages"][0]["required_operands"][0]["period_column_candidates"] == []
    assert no_exact["raw_rejection_counts"] == {}
    assert no_exact["exclusive_primary_cause_counts"] == {"NO_EXACT_CONCEPT_ROW": 1}
