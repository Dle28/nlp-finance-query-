from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _builder_module():
    path = Path(__file__).resolve().parents[2] / "scripts/e2e/build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location("competition_submission_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_map_module():
    path = Path(__file__).resolve().parents[2] / "scripts/e2e/build_source_line_map_v1.py"
    spec = importlib.util.spec_from_file_location("source_line_map_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_corpus_table_asset_is_normalized_to_the_v2_runtime_contract() -> None:
    builder = _builder_module()
    table = builder.normalize_structured_table(
        {
            "internal_table_uid": "uid-flat",
            "headers": ["Nhãn dòng", "2024"],
            "rows": [["Doanh thu", "10"]],
            "source_path": "/corpus/report.txt",
            "char_start": 12,
            "source_sha256": "abc",
        }
    )
    assert table["column_labels"] == ["Nhãn dòng", "2024"]
    assert table["source_provenance"] == {
        "source_path": "/corpus/report.txt",
        "char_start": 12,
        "source_sha256": "abc",
    }


def test_source_line_map_accepts_flat_full_corpus_asset(tmp_path: Path) -> None:
    source = tmp_path / "report.txt"
    source.write_text("header\nunit\nTABLE\n", encoding="utf-8")
    asset = tmp_path / "full_table_assets_v1.jsonl"
    asset.write_text(
        json.dumps(
            {
                "internal_table_uid": "uid-flat",
                "source_path": str(source),
                "char_start": source.read_text(encoding="utf-8").index("TABLE"),
                "rows": [["Doanh thu", "10"]],
                "headers": ["Nhãn dòng", "2024"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    source_map = _source_map_module()
    mapping, stats = source_map.build_source_line_map(asset)
    assert mapping == {"uid-flat": 3}
    assert stats["table_count"] == 1
    assert stats["map_entry_count"] == 1


def test_table_line_locator_uses_precomputed_ocr_line_when_source_is_remote() -> None:
    builder = _builder_module()
    table = {
        "internal_table_uid": "uid-remote",
        "local_ordinal": 50,
        "source_provenance": {
            "source_path": "/kaggle/input/unavailable/report_extracted.txt",
            "char_start": 123,
        },
    }
    stats = builder.Counter()
    assert builder.table_line_locator(
        table,
        {},
        {"uid-remote": 1179},
        stats,
    ) == 1179
    assert stats["source_line_map"] == 1
    assert stats["local_ordinal_fallback"] == 0


def test_table_line_locator_only_uses_local_ordinal_as_last_resort() -> None:
    builder = _builder_module()
    table = {
        "internal_table_uid": "uid-missing",
        "local_ordinal": 50,
        "source_provenance": {"source_path": "/missing/report.txt"},
    }
    stats = builder.Counter()
    assert builder.table_line_locator(table, {}, {}, stats) == 51
    assert stats["source_line_map"] == 0
    assert stats["local_ordinal_fallback"] == 1


def test_production_source_line_map_must_cover_the_v2_table_population() -> None:
    builder = _builder_module()
    tables = {
        "uid-1": {"internal_table_uid": "uid-1"},
        "uid-2": {"internal_table_uid": "uid-2"},
    }
    assert builder.validate_source_line_map_coverage(
        {"uid-1": 10, "uid-2": 20},
        tables,
    ) == {
        "table_uid_count": 2,
        "map_entry_count": 2,
        "missing_count": 0,
        "extra_count": 0,
        "extra_entries_allowed": False,
    }


def test_direct_source_replay_recomputes_current_table_value_and_gates_provisional(tmp_path: Path) -> None:
    builder = _builder_module()
    replay = tmp_path / "direct_replay.jsonl"
    replay.write_text(
        json.dumps(
            {
                "question_id": 1,
                "status": "shadow_replay_ready",
                "machine_consensus_status": "machine_calibrated",
                "machine_selected_uid": "uid-direct",
                "distinct_exact_value_count": 1,
                "replay_unit": "million_vnd",
                "replay_value": "12.5",
                "valid_exact_candidates": [
                    {
                        "internal_table_uid": "uid-direct",
                        "row_index": 1,
                        "column_index": 1,
                        "raw_value": "12.500.000",
                        "parsed_value": "12500000",
                        "comparison_value": "12.5",
                        "source_unit": "vnd",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    items = {
        1: {
            "question_plan": {"requested_unit": "million_vnd"},
            "question": "Giá trị là bao nhiêu triệu đồng?",
        }
    }
    tables = {
        "uid-direct": {
            "internal_table_uid": "uid-direct",
            "document_id": "ABC_financial_statements_2024_separate",
            "rows": [["", "2024VND"], ["Doanh thu", "12.500.000"]],
        }
    }

    answers, stats = builder.load_direct_evidence_replay_candidates(
        [replay], items_by_question=items, tables_by_uid=tables
    )
    assert answers[1]["answer"] == builder.Decimal("12.5")
    assert answers[1]["selection"]["direct_replay"] is True
    assert stats["answers_accepted_machine_calibrated"] == 1

    provisional = json.loads(replay.read_text(encoding="utf-8"))
    provisional["machine_consensus_status"] = "machine_provisional"
    replay.write_text(json.dumps(provisional) + "\n", encoding="utf-8")
    answers, stats = builder.load_direct_evidence_replay_candidates(
        [replay], items_by_question=items, tables_by_uid=tables
    )
    assert answers == {}
    assert stats["records_rejected_consensus_status"] == 1

    answers, stats = builder.load_direct_evidence_replay_candidates(
        [replay],
        items_by_question=items,
        tables_by_uid=tables,
        include_provisional=True,
    )
    assert answers[1]["tier"] == "direct_source_replay_provisional_v1"
    assert stats["answers_accepted_machine_provisional"] == 1


def test_direct_source_replay_prefers_table_header_and_raw_question_unit(tmp_path: Path) -> None:
    builder = _builder_module()
    replay = tmp_path / "compound_unit_replay.jsonl"
    replay.write_text(
        json.dumps(
            {
                "question_id": 1,
                "status": "shadow_replay_ready",
                "machine_consensus_status": "machine_calibrated",
                "machine_selected_uid": "uid-unit",
                "distinct_exact_value_count": 1,
                # The legacy artifact expresses the replay in billion VND;
                # the question itself asks for the compound unit "trăm tỷ".
                "replay_unit": "billion_vnd",
                "replay_value": "0.0125",
                "valid_exact_candidates": [
                    {
                        "internal_table_uid": "uid-unit",
                        "row_index": 1,
                        "column_index": 1,
                        "raw_value": "12.500.000",
                        "parsed_value": "12500000",
                        "comparison_value": "0.0125",
                        # The candidate metadata is vnd, but the immutable
                        # table header says the cells are triệu VND.
                        "source_unit": "vnd",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    items = {
        1: {
            "question": "Giá trị là bao nhiêu trăm tỷ đồng?",
            # This is the known legacy flattening that the loader must not use.
            "question_plan": {"requested_unit": "billion_vnd"},
        }
    }
    tables = {
        "uid-unit": {
            "internal_table_uid": "uid-unit",
            "document_id": "ABC_financial_statements_2024_separate",
            "headers": ["Nhãn dòng", "2024Triệu VND"],
            "rows": [["", "2024Triệu VND"], ["Doanh thu", "12.500.000"]],
        }
    }

    answers, stats = builder.load_direct_evidence_replay_candidates(
        [replay], items_by_question=items, tables_by_uid=tables
    )
    assert answers[1]["answer"] == builder.Decimal("125")
    assert stats["source_unit_corrected_from_current_table"] == 1
    assert stats["output_unit_corrected_from_question_literal"] == 1
