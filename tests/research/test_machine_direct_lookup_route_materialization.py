from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.machine_direct_lookup_route_materialization import (
    MACHINE_CONTRACT,
    _candidate_from_diagnostic,
    _header_period_matches_requested_year,
    build_machine_direct_lookup_route_materialization,
    validate_machine_direct_lookup_route_materialization,
)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_direct_lookup_materialization_is_source_bound_and_value_blind(tmp_path: Path) -> None:
    triage = tmp_path / "triage.jsonl"
    plans = tmp_path / "plans.jsonl"
    period_packets = tmp_path / "periods.jsonl"
    overlays = tmp_path / "overlays.jsonl"
    tables = tmp_path / "tables.jsonl"
    context = tmp_path / "context.jsonl"
    diagnostics = tmp_path / "diagnostics.jsonl"
    row_candidates = tmp_path / "rows.jsonl"
    locator = {
        "source_path": "/source/AAA.txt",
        "source_sha256": "a" * 64,
        "table_sha256": "b" * 64,
        "local_ordinal": 3,
        "char_start": 123,
    }
    _write_jsonl(triage, [{"question_id": 1, "primary_blocker": "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"}])
    _write_jsonl(
        plans,
        [
            {
                "question_id": 1,
                "decomposition_status": "complete",
                "effective_family": "direct_lookup",
                "operation_ast": {"op": "lookup"},
                "plan_fingerprint": "fingerprint",
                "operands": [{"role": "reported_value", "scope": "consolidated"}],
                "entities": ["AAA"],
                "years": [2023],
            }
        ],
    )
    _write_jsonl(
        period_packets,
        [{"question_id": 1, "packet_status": "packet_blocked", "stages": []}],
    )
    _write_jsonl(
        overlays,
        [
            {
                "question_id": 1,
                "route_status": "route_incomplete",
                "required_operations": ["reported_value"],
                "missing_operations": ["reported_value"],
            }
        ],
    )
    _write_jsonl(
        tables,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "local_ordinal": 3,
                "source_provenance": {key: locator[key] for key in ("source_path", "source_sha256", "table_sha256", "char_start")},
                "rows": [["Chỉ tiêu", "2023 triệu đồng"], ["Doanh thu", "123456"]],
                "cell_provenance": [
                    [{"source_row": 0, "source_cell": 0}, {"source_row": 0, "source_cell": 1}],
                    [{"source_row": 1, "source_cell": 0}, {"source_row": 1, "source_cell": 1}],
                ],
            }
        ],
    )
    _write_jsonl(
        context,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "source_provenance": {key: locator[key] for key in ("source_sha256", "table_sha256")},
                "grid": {"rectangular": True, "provenance_complete": True},
                "quality": {"status": "review_ready"},
                "canonical_headers": {
                    "columns": [
                        {
                            "column_index": 1,
                            "header_source_cells": [{"row_index": 0, "column_index": 1}],
                            "period_labels": ["2023"],
                            "source_label": "2023 triệu đồng",
                            "unit_labels": ["triệu đồng"],
                        }
                    ]
                },
                "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
            }
        ],
    )
    header_sha = hashlib.sha256("2023 triệu đồng".encode("utf-8")).hexdigest()
    _write_jsonl(
        diagnostics,
        [
            {
                "diagnostic_id": "d1",
                "question_id": 1,
                "document_id": "AAA_2023",
                "internal_table_uid": "u1",
                "requested_year": 2023,
                "exact_table_locator": locator,
                "exact_table_locator_sha256": "locator",
                "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
                "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
                "scope_status": "SCOPE_MATCH",
                "source_unit_candidate": "trieu_dong",
                "matching_year_column_indices": [1],
                "column_headers": [{"column_index": 1, "header_sha256": header_sha}],
            }
        ],
    )
    _write_jsonl(
        row_candidates,
        [
            {
                "diagnostic_id": "d1",
                "row_index": 1,
                "row_rank": 1,
                "row_label": "Doanh thu",
                "row_label_sha256": "row",
                "row_label_token_jaccard": 1.0,
                "numeric_column_indices": [1],
            }
        ],
    )
    period_manifest = tmp_path / "period.manifest.json"
    overlay_manifest = tmp_path / "overlay.manifest.json"
    context_manifest = tmp_path / "context.manifest.json"
    _write_json(
        period_manifest,
        {"outputs": {"period_packets": {"sha256": _sha(period_packets)}}, "inputs": {"structured_tables_v2": {"sha256": _sha(tables)}}},
    )
    _write_json(overlay_manifest, {"outputs": {"overlay": {"sha256": _sha(overlays)}}})
    _write_json(context_manifest, {"sidecar_sha256": _sha(context)})

    output = tmp_path / "output"
    summary = build_machine_direct_lookup_route_materialization(
        triage_path=triage,
        plans_path=plans,
        base_period_packets_path=period_packets,
        base_period_manifest_path=period_manifest,
        base_route_overlay_path=overlays,
        base_route_overlay_manifest_path=overlay_manifest,
        table_diagnostics_path=diagnostics,
        row_candidates_path=row_candidates,
        structured_tables_path=tables,
        evidence_context_path=context,
        evidence_context_manifest_path=context_manifest,
        output_dir=output,
        expected_question_count=1,
    )

    output_period = json.loads((output / "period_column_candidate_packets_v1.jsonl").read_text(encoding="utf-8"))
    output_overlay = json.loads((output / "route_completeness_overlay_v3.jsonl").read_text(encoding="utf-8"))
    audit_text = (output / "machine_direct_lookup_route_audit_v1.jsonl").read_text(encoding="utf-8")
    assert summary["materialized_question_count"] == 1
    assert output_period["packet_status"] == "unique_period_column_candidate"
    assert output_overlay["route_status"] == "route_complete"
    assert "123456" not in audit_text
    assert "human_verified" not in audit_text
    assert "source_label" not in audit_text
    assert json.loads(audit_text)["source_contract"] == MACHINE_CONTRACT
    assert validate_machine_direct_lookup_route_materialization(output, expected_question_count=1)["status"] == "PASS"


def test_v3_header_recovery_requires_one_exact_provenance_bound_year() -> None:
    locator = {
        "source_path": "/source/AAA.txt",
        "source_sha256": "a" * 64,
        "table_sha256": "b" * 64,
        "local_ordinal": 3,
        "char_start": 123,
    }
    table = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2023",
        "local_ordinal": 3,
        "source_provenance": {key: locator[key] for key in ("source_path", "source_sha256", "table_sha256", "char_start")},
        "rows": [["Chỉ tiêu", "2023 triệu đồng"], ["Doanh thu", "123456"]],
        "cell_provenance": [[{}, {}], [{}, {"source_row": 1, "source_cell": 1}]],
    }
    context = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2023",
        "source_provenance": {key: locator[key] for key in ("source_sha256", "table_sha256")},
        "grid": {"rectangular": True, "provenance_complete": True},
        "quality": {"status": "review_ready"},
        "canonical_headers": {
            "columns": [
                {
                    "column_index": 1,
                    "header_source_cells": [{"row_index": 0, "column_index": 1}],
                    "period_labels": ["2023"],
                    "source_label": "2023 triệu đồng",
                    "unit_labels": ["triệu đồng"],
                }
            ]
        },
        "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
    }
    diagnostic = {
        "question_id": 1,
        "document_id": "AAA_2023",
        "internal_table_uid": "u1",
        "requested_year": 2023,
        "exact_table_locator": locator,
        "exact_table_locator_sha256": "locator",
        "period_status": "NO_YEAR_HEADER_CANDIDATE",
        "unit_status": "NO_HEADER_UNIT_CANDIDATE",
        "scope_status": "SCOPE_MATCH",
        "source_unit_candidate": "trieu_dong",
        "matching_year_column_indices": [],
        "column_headers": [],
    }
    rows = [{"row_index": 1, "row_rank": 1, "row_label": "Doanh thu", "row_label_sha256": "row", "row_label_token_jaccard": 1.0, "numeric_column_indices": [1]}]

    candidate, checks = _candidate_from_diagnostic(
        diagnostic=diagnostic,
        rows=rows,
        table=table,
        context=context,
        minimum_row_jaccard=0.8,
        minimum_row_margin=0.2,
        allow_v3_header_recovery=True,
    )

    assert checks["v3_exact_header_recovery"] is True
    assert candidate is not None
    assert candidate["header_selection_method"] == "v3_exact_year_header_recovery_v1"
    assert "123456" not in json.dumps(candidate, ensure_ascii=False)


def test_explicit_year_end_date_is_allowed_only_for_the_date_recheck() -> None:
    header = {"period_labels": ["31/12/2023"], "source_label": "31/12/2023 VND"}
    diagnostic = {"period_resolution": "explicit_year_end_header"}
    assert _header_period_matches_requested_year(header=header, diagnostic=diagnostic, requested_year=2023)
    assert not _header_period_matches_requested_year(
        header=header,
        diagnostic={"period_resolution": "source_title_plus_current_header"},
        requested_year=2023,
    )
    assert not _header_period_matches_requested_year(
        header={"period_labels": ["01/01/2023"], "source_label": "01/01/2023 VND"},
        diagnostic=diagnostic,
        requested_year=2023,
    )


def test_v3_header_subset_reconciliation_allows_only_ocr_redaction_noise() -> None:
    locator = {
        "source_path": "/source/AAA.txt",
        "source_sha256": "a" * 64,
        "table_sha256": "b" * 64,
        "local_ordinal": 3,
        "char_start": 123,
    }
    table = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2023",
        "local_ordinal": 3,
        "source_provenance": {key: locator[key] for key in ("source_path", "source_sha256", "table_sha256", "char_start")},
        "rows": [["Chỉ tiêu", "2023 triệu đồng"], ["Doanh thu", "123456"], ["", "masked"]],
        "cell_provenance": [[{}, {}], [{}, {"source_row": 1, "source_cell": 1}], [{}, {}]],
    }
    context = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2023",
        "source_provenance": {key: locator[key] for key in ("source_sha256", "table_sha256")},
        "grid": {"rectangular": True, "provenance_complete": True},
        "quality": {"status": "review_ready"},
        "canonical_headers": {
            "header_row_indices": [0],
            "raw_header_row_indices": [0, 2],
            "columns": [
                {
                    "column_index": 1,
                    "header_source_cells": [{"row_index": 0, "column_index": 1}],
                    "period_labels": ["2023"],
                    "source_label": "2023 triệu đồng",
                    "unit_labels": ["triệu đồng"],
                }
            ],
        },
        "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
    }
    diagnostic = {
        "question_id": 1,
        "document_id": "AAA_2023",
        "internal_table_uid": "u1",
        "requested_year": 2023,
        "exact_table_locator": locator,
        "exact_table_locator_sha256": "locator",
        "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
        "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
        "scope_status": "SCOPE_MATCH",
        "source_unit_candidate": "trieu_dong",
        "matching_year_column_indices": [1],
        "column_headers": [{"column_index": 1, "header_sha256": "stale", "header_labels": ["2023 triệu đồng", "[SỐ_ĐÃ_ẨN]"]}],
    }
    rows = [{"row_index": 1, "row_rank": 1, "row_label": "Doanh thu", "row_label_sha256": "row", "row_label_token_jaccard": 1.0, "numeric_column_indices": [1]}]

    candidate, checks = _candidate_from_diagnostic(
        diagnostic=diagnostic,
        rows=rows,
        table=table,
        context=context,
        minimum_row_jaccard=0.8,
        minimum_row_margin=0.2,
        allow_v3_header_recovery=True,
    )

    assert checks["v2_header_hash_matches_v3"] is True
    assert candidate is not None
    assert candidate["header_selection_method"] == "v3_canonical_header_subset_reconciliation_v1"
