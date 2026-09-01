from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts/e2e/build_source_line_map_v1.py"
    spec = importlib.util.spec_from_file_location("source_line_map_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repair_module():
    path = Path(__file__).resolve().parents[2] / "scripts/e2e/rematerialize_submission_coordinates_v1.py"
    spec = importlib.util.spec_from_file_location("submission_coordinate_repair", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_source_line_map_uses_one_based_table_start_line(tmp_path: Path) -> None:
    builder = _module()
    source = tmp_path / "report.txt"
    source.write_text("page 1\n\n<table>first</table>\npage 2\n<table>second</table>\n", encoding="utf-8")
    text = source.read_text(encoding="utf-8")
    first_start = text.index("<table>first")
    tables = tmp_path / "tables_structured_v2.jsonl"
    tables.write_text(
        "\n".join(
            json.dumps(
                {
                    "internal_table_uid": uid,
                    "source_provenance": {
                        "source_path": str(source),
                        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "char_start": start,
                    },
                }
            )
            for uid, start in (("uid-first", first_start), ("uid-second", text.index("<table>second")))
        )
        + "\n",
        encoding="utf-8",
    )

    mapping, stats = builder.build_source_line_map(tables)

    assert mapping == {"uid-first": 3, "uid-second": 5}
    assert stats["table_count"] == 2
    assert stats["source_file_count"] == 1


def test_build_source_line_map_rejects_stale_source_hash(tmp_path: Path) -> None:
    builder = _module()
    source = tmp_path / "report.txt"
    source.write_text("<table>value</table>\n", encoding="utf-8")
    tables = tmp_path / "tables_structured_v2.jsonl"
    tables.write_text(
        json.dumps(
            {
                "internal_table_uid": "uid-stale",
                "source_provenance": {
                    "source_path": str(source),
                    "source_sha256": "0" * 64,
                    "char_start": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source hash mismatch"):
        builder.build_source_line_map(tables)


def test_rematerialize_rows_changes_only_local_table_positions() -> None:
    repair = _repair_module()
    rows = [
        {
            "id": 1,
            "question": "q",
            "answer": 7.0,
            "relevant_tables": ["DOC|2"],
        },
        {
            "id": 2,
            "question": "q2",
            "answer": 8.0,
            "relevant_tables": ["DOC|912"],
        },
    ]

    converted, stats = repair.rematerialize_rows(
        rows,
        local_coordinate_index={("DOC", 2): 1179},
        allow_unmapped_source_overrides=True,
    )

    assert converted[0]["answer"] == 7.0
    assert converted[0]["relevant_tables"] == ["DOC|1179"]
    assert converted[1]["relevant_tables"] == ["DOC|912"]
    assert stats == {
        "references_seen": 2,
        "references_rematerialized": 1,
        "unmapped_source_overrides": 1,
    }
