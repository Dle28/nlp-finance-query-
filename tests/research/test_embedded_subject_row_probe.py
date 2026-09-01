from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.embedded_subject_row_probe import (
    CONTRACT,
    build_embedded_subject_row_probe,
    validate_embedded_subject_row_probe,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_embedded_subject_probe_is_navigation_only_and_value_blind(tmp_path: Path) -> None:
    reclass = tmp_path / "reclass"
    reclass.mkdir()
    plans = reclass / "typed_operand_plans.jsonl"
    bridge = reclass / "direct_lookup_target_bridge_v1.jsonl"
    _write_jsonl(
        plans,
        [
            {
                "question_id": 1,
                "operands": [{"operand_id": "x0", "scope": "separate"}],
            }
        ],
    )
    _write_jsonl(bridge, [{"question_id": 1}])
    _write_json(
        reclass / "manifest.json",
        {
            "outputs": {
                "plans": {"sha256": _sha(plans)},
                "target_bridge": {"sha256": _sha(bridge)},
            }
        },
    )
    items = tmp_path / "items.jsonl"
    _write_jsonl(
        items,
        [
            {
                "id": 1,
                "question": "Tỷ lệ sở hữu của AAA tại Công ty TNHH Sao Mai đến ngày 31/12/2024 là bao nhiêu phần trăm?",
            }
        ],
    )
    candidates = tmp_path / "candidates.jsonl"
    _write_jsonl(
        candidates,
        [
            {
                "question_id": 1,
                "internal_table_uid": "u1",
                "scope": "separate",
            }
        ],
    )
    candidates_manifest = tmp_path / "candidates.manifest.json"
    _write_json(candidates_manifest, {"outputs": {"table_candidates_v1.jsonl": {"sha256": _sha(candidates)}}})
    assets = tmp_path / "assets.jsonl"
    _write_jsonl(
        assets,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2024_separate",
                "local_ordinal": 7,
                "rows": [["STT", "Tên đơn vị", "Tỷ lệ"], ["1", "Công ty TNHH Sao Mai", "98765"]],
            }
        ],
    )
    assets_manifest = tmp_path / "assets.manifest.json"
    _write_json(assets_manifest, {"outputs": {assets.name: {"sha256": _sha(assets)}}})

    output = tmp_path / "output"
    summary = build_embedded_subject_row_probe(
        reclassification_dir=reclass,
        review_items_path=items,
        table_candidates_path=candidates,
        table_candidates_manifest_path=candidates_manifest,
        full_assets_path=assets,
        full_assets_manifest_path=assets_manifest,
        output_dir=output,
        expected_question_count=1,
    )
    rendered = (output / "embedded_subject_row_probe_v1.jsonl").read_text(encoding="utf-8")
    assert summary["status_counts"] == {"UNIQUE_EXACT_SUBJECT_ROW_NAVIGATION": 1}
    assert "98765" not in rendered and "Sao Mai" not in rendered and "row_label" not in rendered
    assert json.loads(rendered)["source_contract"] == CONTRACT
    assert validate_embedded_subject_row_probe(output, expected_question_count=1)["status"] == "PASS"
