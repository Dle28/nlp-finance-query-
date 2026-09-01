from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.e2e.core.retrieval_probes import (
    NAVIGATION_CONTRACT,
    build_retrieval_probe_artifact,
    validate_retrieval_probe_artifact,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_retrieval_probe_reports_direct_misses_without_copying_query_or_values(tmp_path: Path) -> None:
    probes = tmp_path / "probes.jsonl"
    _write_jsonl(
        probes,
        [
            {
                "probe_id": "direct-hit",
                "query": "Doanh thu thuần năm 2023",
                "ticker": "AAA",
                "report_year": 2023,
                "scope": "consolidated",
                "expected_table_uids": ["u1", "u2"],
            },
            {
                "probe_id": "direct-miss",
                "query": "Lợi nhuận sau thuế năm 2023",
                "ticker": "AAA",
                "report_year": 2023,
                "expected_table_uids": ["u3"],
            },
        ],
    )
    observed_calls: list[tuple[str, str, int, str | None, int]] = []

    def search(query: str, ticker: str, report_year: int, scope: str | None, limit: int):
        observed_calls.append((query, ticker, report_year, scope, limit))
        return (
            [{"internal_table_uid": "u4"}, {"internal_table_uid": "u2"}, {"internal_table_uid": "u1"}]
            if query.startswith("Doanh thu")
            else [{"internal_table_uid": "u5"}]
        )

    artifact_dir = tmp_path / "artifact"
    summary = build_retrieval_probe_artifact(
        probe_path=probes,
        output_dir=artifact_dir,
        retrieval_kind="lexical",
        top_k=3,
        search=search,
    )

    assert observed_calls == [
        ("Doanh thu thuần năm 2023", "AAA", 2023, "consolidated", 3),
        ("Lợi nhuận sau thuế năm 2023", "AAA", 2023, None, 3),
    ]
    assert summary["direct_target_hit_count"] == 1
    assert summary["direct_target_miss_count"] == 1
    assert summary["target_table_uid_recall"] == pytest.approx(2 / 3)
    assert summary["semantic_accuracy"] == "NOT_MEASURED"
    assert summary["source_contract"] == NAVIGATION_CONTRACT
    assert validate_retrieval_probe_artifact(artifact_dir)["status"] == "VALID"

    rendered = (artifact_dir / "navigation_retrieval_probe_results_v1.jsonl").read_text(encoding="utf-8")
    assert "Doanh thu thuần" not in rendered
    assert "Lợi nhuận sau thuế" not in rendered
    rows = [json.loads(line) for line in rendered.splitlines()]
    assert rows[0]["retrieval_status"] == "DIRECT_TARGET_RETRIEVED"
    assert rows[0]["first_match_rank"] == 2
    assert rows[0]["missing_expected_table_uids"] == []
    assert rows[1]["retrieval_status"] == "DIRECT_TARGET_MISSED"
    assert rows[1]["first_match_rank"] is None


def test_retrieval_probe_rejects_value_bearing_input_and_tampered_output(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.jsonl"
    _write_jsonl(
        unsafe,
        [
            {
                "probe_id": "unsafe",
                "query": "Doanh thu",
                "ticker": "AAA",
                "report_year": 2023,
                "expected_table_uids": ["u1"],
                "raw_value": "100",
            }
        ],
    )
    with pytest.raises(ValueError, match="unsupported keys"):
        build_retrieval_probe_artifact(
            probe_path=unsafe,
            output_dir=tmp_path / "unsafe-output",
            retrieval_kind="lexical",
            top_k=1,
            search=lambda *_args: [],
        )

    probes = tmp_path / "safe.jsonl"
    _write_jsonl(
        probes,
        [{"probe_id": "safe", "query": "Doanh thu", "ticker": "AAA", "report_year": 2023, "expected_table_uids": ["u1"]}],
    )
    artifact_dir = tmp_path / "safe-output"
    build_retrieval_probe_artifact(
        probe_path=probes,
        output_dir=artifact_dir,
        retrieval_kind="lexical",
        top_k=1,
        search=lambda *_args: [{"internal_table_uid": "u1"}],
    )
    result_path = artifact_dir / "navigation_retrieval_probe_results_v1.jsonl"
    result_path.write_text(result_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_retrieval_probe_artifact(artifact_dir)
