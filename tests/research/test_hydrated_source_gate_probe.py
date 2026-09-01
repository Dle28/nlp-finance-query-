from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.e2e.core.table_structure import parse_html_table
from finance_query.research.hydrated_source_gate_probe import (
    CONTRACT,
    _direct_lookup_route_shape,
    build_hydrated_source_gate_probe,
    validate_hydrated_source_gate_probe,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_hydrated_probe_is_value_blind_and_measures_coverage_only(tmp_path: Path) -> None:
    source = tmp_path / "AAA_2024_extracted.txt"
    raw = "prefix<table><tr><th>Chỉ tiêu</th><th>2024 triệu đồng</th></tr><tr><td>Doanh thu</td><td>123456</td></tr></table>suffix"
    source.write_text(raw, encoding="utf-8")
    start, end = raw.index("<table>"), raw.index("</table>") + len("</table>")
    table_html = raw[start:end]
    parsed = parse_html_table(table_html, context="Đơn vị: triệu đồng")
    asset = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2024_separate",
        "source_path": str(source),
        "source_sha256": _sha(source),
        "table_sha256": hashlib.sha256(table_html.encode("utf-8")).hexdigest(),
        "local_ordinal": 1,
        "char_start": start,
        "char_end": end,
        "context_before": "Đơn vị: triệu đồng",
        "rows": parsed["rows"],
    }
    assets = tmp_path / "assets.jsonl"
    _write_jsonl(assets, [asset])
    assets_manifest = tmp_path / "assets.manifest.json"
    _write_json(assets_manifest, {"outputs": {assets.name: {"sha256": _sha(assets)}}})
    triage = tmp_path / "triage.jsonl"
    _write_jsonl(triage, [{"question_id": 1, "primary_blocker": "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"}])
    plans = tmp_path / "plans.jsonl"
    _write_jsonl(plans, [{"question_id": 1, "decomposition_status": "complete", "effective_family": "direct_lookup", "entities": ["AAA"], "years": [2024], "operands": [{"ticker": "AAA", "scope": "separate"}], "operation_ast": {"op": "lookup"}}])
    routes = tmp_path / "routes.jsonl"
    _write_jsonl(routes, [{"question_id": 1, "route_status": "route_incomplete", "required_operations": ["reported_value"], "missing_operations": ["reported_value"]}])
    review = tmp_path / "review.jsonl"
    _write_jsonl(review, [{"question_id": 1, "internal_table_uid": "u1", "row_index": 1, "row_token_jaccard": 1.0, "report_year": 2024, "ticker": "AAA", "observed_scope": "separate", "row_label": "Doanh thu", "human_verified": False}])
    v2, v3 = tmp_path / "v2.jsonl", tmp_path / "v3.jsonl"
    _write_jsonl(v2, [])
    _write_jsonl(v3, [])

    output = tmp_path / "probe"
    summary = build_hydrated_source_gate_probe(
        triage_path=triage,
        plans_path=plans,
        route_overlay_path=routes,
        row_review_queue_path=review,
        full_assets_path=assets,
        full_assets_manifest_path=assets_manifest,
        structured_tables_path=v2,
        evidence_context_path=v3,
        output_dir=output,
        expected_question_count=1,
    )
    rendered = (output / "hydrated_source_gate_probe_v1.jsonl").read_text(encoding="utf-8")
    assert summary["baseline_status_counts"] == {"STRICT_SOURCE_GATES_INCOMPLETE": 1}
    assert summary["hydrated_status_counts"] == {"UNIQUE_STRICT_SOURCE_CANDIDATE": 1}
    assert summary["new_unique_strict_source_question_count"] == 1
    assert "123456" not in rendered and "human_verified" not in rendered and "row_label" not in rendered
    assert json.loads(rendered)["source_contract"] == CONTRACT
    assert validate_hydrated_source_gate_probe(output, expected_question_count=1)["status"] == "PASS"


def test_probe_measures_covered_reported_value_route_that_is_still_incomplete() -> None:
    plan = {
        "decomposition_status": "complete",
        "effective_family": "direct_lookup",
        "entities": ["AAA"],
        "years": [2024],
        "operands": [{"operand_id": "x0"}],
        "operation_ast": {"op": "lookup", "args": ["x0"]},
    }
    assert _direct_lookup_route_shape(
        plan,
        {
            "route_status": "route_incomplete",
            "required_operations": ["reported_value"],
            "covered_operations": ["reported_value"],
            "missing_operations": [],
        },
    ) == "REPORTED_VALUE_COVERED_ROUTE_STILL_INCOMPLETE"
