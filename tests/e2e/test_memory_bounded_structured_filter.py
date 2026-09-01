from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest


def _builder_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/e2e/build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location(
        "competition_submission_builder_memory_filter", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_candidate_table_uids_returns_only_nonempty_candidate_uids():
    builder = _builder_module()
    review_items = {
        1: {
            "candidates": [
                {"internal_table_uid": "table-a"},
                {"internal_table_uid": "table-b"},
                {"internal_table_uid": ""},
                {},
            ]
        },
        2: {"candidates": [{"internal_table_uid": "table-a"}]},
        3: {"candidates": None},
        4: {},
    }

    assert builder.review_candidate_table_uids(review_items) == {
        "table-a",
        "table-b",
    }


def test_filtered_source_line_map_allows_extra_but_rejects_missing_filtered_uid():
    builder = _builder_module()
    filtered_tables = {"table-a": {}, "table-b": {}}

    coverage = builder.validate_source_line_map_coverage(
        {"table-a": 10, "table-b": 20, "stale-table": 30},
        filtered_tables,
        allow_extra=True,
    )

    assert coverage == {
        "table_uid_count": 2,
        "map_entry_count": 3,
        "missing_count": 0,
        "extra_count": 1,
        "extra_entries_allowed": True,
    }

    with pytest.raises(ValueError, match=r"missing=1 extra=1"):
        builder.validate_source_line_map_coverage(
            {"table-a": 10, "stale-table": 30},
            filtered_tables,
            allow_extra=True,
        )


def test_strict_legacy_source_line_map_rejects_extra_uid():
    builder = _builder_module()

    with pytest.raises(ValueError, match=r"missing=0 extra=1"):
        builder.validate_source_line_map_coverage(
            {"table-a": 10, "stale-table": 30},
            {"table-a": {}},
            allow_extra=False,
        )


def test_parser_defaults_to_candidate_uid_filter():
    builder = _builder_module()
    parser = builder.configure_parser(argparse.ArgumentParser())

    args = parser.parse_args([])

    assert args.structured_table_filter == "candidate_uids"

