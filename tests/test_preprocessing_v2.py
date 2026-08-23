from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from finance_query.preprocessing_v2 import RepairRule, normalize_canonical_text, run_preprocessing


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_text_normalization_is_nfc_audited_and_numeric_invariant() -> None:
    rules = [RepairRule("typo", "tiến gửi", "tiền gửi", 0.98)]
    canonical, events, flags = normalize_canonical_text(
        "<b>tiê\u0301n gửi</b>  1.234", rules=rules, strip_html=True
    )

    assert canonical == "tiền gửi 1.234"
    assert {event["repair_type"] for event in events} >= {
        "unicode_nfc",
        "html_to_text",
        "allowlisted_ocr_repair",
    }
    assert flags == []


def test_numeric_changing_rule_is_blocked() -> None:
    rules = [RepairRule("bad", "2021", "2022", 1.0)]
    canonical, events, flags = normalize_canonical_text("Năm 2021", rules=rules)

    assert canonical == "Năm 2021"
    assert not any(event.get("rule_id") == "bad" for event in events)
    assert flags == ["numeric_mutation_blocked"]


def test_pipeline_builds_hash_bound_non_training_review_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    config_dir = repo / "configs"
    config_dir.mkdir(parents=True)
    raw_path = repo / "artifacts" / "tables.jsonl"
    v2_path = repo / "artifacts" / "v2.jsonl"
    v3_path = repo / "artifacts" / "v3.jsonl"
    labels = repo / "labels"
    uid = "u1"
    raw = {
        "internal_table_uid": uid,
        "document_id": "AAA_financial_statements_2021_consolidated",
        "ticker": "AAA",
        "report_year": 2021,
        "scope": "consolidated",
        "source_path": "/immutable/source.txt",
        "source_sha256": _sha("source"),
        "table_sha256": _sha("table"),
        "context_before": "===== PAGE 2 ===== <p>6. Các khoản đầu tư</p>",
        "headers": ["Chỉ tiêu", "2021 VND"],
        "rows": [["Chỉ tiêu", "2021 VND"], ["Chứng chỉ tiến gửi", "1.234"]],
        "row_paths": ["Chỉ tiêu", "Chứng chỉ tiến gửi > 1.234"],
    }
    provenance = {
        "source_sha256": raw["source_sha256"],
        "table_sha256": raw["table_sha256"],
    }
    v2 = {
        "internal_table_uid": uid,
        "source_provenance": provenance,
        "rows": [["Chỉ tiêu", "2021 VND"], ["Chứng chỉ tiến gửi", "1.234"]],
        "cell_provenance": [[{}, {}], [{}, {}]],
        "table_function": {"kind": "investment_schedule", "label": "Khoản đầu tư"},
        "table_section": {"kind": "asset", "label": "Tài sản"},
    }
    v3 = {
        "internal_table_uid": uid,
        "source_provenance": provenance,
        "grid": {"width": 2},
        "canonical_headers": {
            "columns": [
                {"column_index": 0, "source_label": "Chỉ tiêu", "role": "row_label"},
                {
                    "column_index": 1,
                    "source_label": "2021 VND",
                    "role": "value_or_text",
                    "period_labels": ["2021"],
                    "unit_labels": ["VND"],
                },
            ]
        },
        "table_function": {"kind": "investment_schedule", "label": "Khoản đầu tư"},
        "table_section": {"kind": "asset", "label": "Tài sản"},
        "context_trace": {"topic": {"label": "Các khoản đầu tư"}},
    }
    _write_jsonl(raw_path, [raw])
    _write_jsonl(v2_path, [v2])
    _write_jsonl(v3_path, [v3])
    _write_jsonl(labels / "human.jsonl", [{"id": 1, "annotation_status": "human_verified"}])
    _write_jsonl(
        labels / "machine.jsonl",
        [{"id": 2, "annotation_status": "machine_calibrated", "training_eligible": True}],
    )
    config = {
        "protocol": "vifinqa_preprocessing_v2",
        "schema_version": 2,
        "validate_sidecar_manifests": False,
        "inputs": {
            "table_assets": "artifacts/tables.jsonl",
            "structured_v2": "artifacts/v2.jsonl",
            "evidence_context_v3": "artifacts/v3.jsonl",
        },
        "labels": {
            "human": {
                "path": "labels/human.jsonl",
                "role": "current",
                "active_benchmark_supervision": True,
            },
            "machine": {
                "path": "labels/machine.jsonl",
                "role": "current",
                "active_benchmark_supervision": True,
            },
        },
        "text_repairs": [
            {"id": "ocr_tien_gui", "source": "tiến gửi", "replacement": "tiền gửi", "confidence": 0.98}
        ],
        "review_sample_per_reason": 2,
    }
    config_path = config_dir / "preprocessing_v2.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    output = repo / "artifacts" / "run"

    result = run_preprocessing(config_path, output)
    record = json.loads((output / "normalized_tables_v2.jsonl").read_text())
    ledger = [json.loads(line) for line in (output / "repair_ledger_v2.jsonl").read_text().splitlines()]
    manifest = json.loads((output / "preprocessing_manifest_v2.json").read_text())

    assert result.table_count == 1
    assert result.current_benchmark_label_count == 2
    assert record["canonical_grid"]["rows"][1][0] == "Chứng chỉ tiền gửi"
    assert record["canonical_grid"]["rows"][1][1] == "1.234"
    assert record["training_eligible"] is False
    assert any(event.get("rule_id") == "ocr_tien_gui" for event in ledger)
    assert manifest["training_eligible"] is False
    assert manifest["inputs"][str(raw_path)]["sha256"] == hashlib.sha256(raw_path.read_bytes()).hexdigest()


def test_pipeline_quarantines_source_identity_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "configs").mkdir(parents=True)
    raw_path = repo / "raw.jsonl"
    v2_path = repo / "v2.jsonl"
    v3_path = repo / "v3.jsonl"
    raw = {
        "internal_table_uid": "u1",
        "source_sha256": "raw-source",
        "table_sha256": "raw-table",
        "rows": [["A", "1"]],
    }
    _write_jsonl(raw_path, [raw])
    _write_jsonl(
        v2_path,
        [{"internal_table_uid": "u1", "source_provenance": {"source_sha256": "other"}, "rows": [["A", "1"]]}],
    )
    _write_jsonl(v3_path, [{"internal_table_uid": "u1", "grid": {"width": 2}}])
    label_path = repo / "labels.jsonl"
    _write_jsonl(label_path, [])
    config = {
        "protocol": "vifinqa_preprocessing_v2",
        "schema_version": 2,
        "validate_sidecar_manifests": False,
        "inputs": {
            "table_assets": "raw.jsonl",
            "structured_v2": "v2.jsonl",
            "evidence_context_v3": "v3.jsonl",
        },
        "labels": {"active": {"path": "labels.jsonl", "active_benchmark_supervision": True}},
    }
    path = repo / "configs" / "preprocessing_v2.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = run_preprocessing(path, repo / "output")

    assert result.status_counts == {"quarantined": 1}
    quarantine = json.loads((repo / "output" / "quarantine_v2.jsonl").read_text())
    assert "v2_source_sha256_conflict" in quarantine["quality"]["quarantine_reason_codes"]
