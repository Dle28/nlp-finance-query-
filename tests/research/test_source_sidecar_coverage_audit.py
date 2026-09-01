from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.e2e.core.table_structure import parse_html_table
from finance_query.research.source_sidecar_coverage_audit import (
    CONTRACT,
    build_source_sidecar_coverage_audit,
    validate_source_sidecar_coverage_audit,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_source_sidecar_coverage_audit_reconstructs_missing_sidecar_without_values(tmp_path: Path) -> None:
    source = tmp_path / "AAA_2024_extracted.txt"
    raw = "prefix<table><tr><th>Chỉ tiêu</th><th>2024 triệu đồng</th></tr><tr><td>Doanh thu</td><td>123456</td></tr></table>suffix"
    source.write_text(raw, encoding="utf-8")
    start, end = raw.index("<table>"), raw.index("</table>") + len("</table>")
    table_html = raw[start:end]
    parsed = parse_html_table(table_html, context="Đơn vị: triệu đồng")
    asset = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2024_consolidated",
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
    review = tmp_path / "review.jsonl"
    _write_jsonl(review, [{"internal_table_uid": "u1", "human_verified": False, "raw_value": "123456"}])
    v2, v3 = tmp_path / "v2.jsonl", tmp_path / "v3.jsonl"
    _write_jsonl(v2, [])
    _write_jsonl(v3, [])

    output = tmp_path / "audit"
    summary = build_source_sidecar_coverage_audit(
        row_review_queue_path=review,
        full_assets_path=assets,
        full_assets_manifest_path=assets_manifest,
        structured_tables_path=v2,
        evidence_context_path=v3,
        output_dir=output,
    )
    rendered = (output / "source_sidecar_coverage_audit_v1.jsonl").read_text(encoding="utf-8")
    assert summary["reconstruction_status_counts"] == {"SOURCE_RECONSTRUCTABLE": 1}
    assert "123456" not in rendered and "human_verified" not in rendered and "rows" not in rendered
    row = json.loads(rendered)
    assert row["source_contract"] == CONTRACT
    assert validate_source_sidecar_coverage_audit(output)["status"] == "PASS"
