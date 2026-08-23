from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.materialize_grounded_critic_independent_ai_labels import ROLE_ROW_MARKERS


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _packet() -> dict:
    return {
        "question_id": 6,
        "question_context": {"entities": ["VSC"], "years": [2017], "scope": "separate"},
        "bounded_source_excerpts": [{"internal_table_uid": "table-6", "row_index": 1, "column_index": 2, "role": "operating_cash_flow", "raw_value_decimal": "145731366146"}],
        "deterministic_execution_trace": [{"operand_sources": [{"internal_table_uid": "table-6", "row_index": 1, "column_index": 2, "role": "operating_cash_flow", "base_vnd_value_decimal": "145731366146", "source_to_vnd_multiplier": "1"}], "requested_output_unit": {"vnd_to_output_divisor": "1000000000"}, "converted_output_decimal": "145.731366146"}],
    }


def _make_inputs(tmp_path: Path) -> dict[str, Path]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    raw = source_root / "statement.txt"
    raw.write_text("2017 VND\nLưu chuyển tiền thuần từ hoạt động kinh doanh\n145.731.366.146\n", encoding="utf-8")
    packet = _packet()
    assignment = tmp_path / "assignment.jsonl"
    _write_jsonl(assignment, [{"protocol": "grounded_critic_independent_review_assignment_v1", "assignment_id": "critic-v2-reviewer_a-q6", "reviewer_slot": "reviewer_a", "question_id": 6, "immutable_packet_sha256": hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "packet": packet, "source_contract": {"evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False}}])
    assignment_manifest = tmp_path / "assignment.manifest.json"
    assignment_manifest.write_text(json.dumps({"protocol": "grounded_critic_independent_review_assignment_v1", "blind_to_qwen_decision": True, "labels_prepopulated": False, "source_contract": {"evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False}, "outputs": {"reviewer_a": {"assignment_sha256": _sha(assignment)}}}))
    tables = tmp_path / "tables.jsonl"
    _write_jsonl(tables, [{"internal_table_uid": "table-6", "document_id": "VSC_2017_separate", "rows": [["", "Mã số", "2017 VND"], ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "20", "145.731.366.146"]], "source_provenance": {"source_path": "statement.txt", "source_sha256": _sha(raw), "char_start": 0}}])
    contexts = tmp_path / "contexts.jsonl"
    _write_jsonl(contexts, [{"internal_table_uid": "table-6", "canonical_headers": {"columns": [{"column_index": 2, "source_label": "2017 VND", "period_labels": ["2017"], "unit_labels": ["VND"]}]}}])
    items = tmp_path / "items.jsonl"
    _write_jsonl(items, [{"id": 6, "question": "Lưu chuyển tiền VSC năm 2017 là bao nhiêu tỷ đồng?", "candidates": [{"internal_table_uid": "table-6", "ticker": "VSC", "report_year": 2017, "scope": "separate", "ticker_match": True, "year_match": True, "scope_match": True}]}])
    return {"assignment": assignment, "assignment_manifest": assignment_manifest, "tables": tables, "contexts": contexts, "items": items, "source_root": source_root}


def _command(paths: dict[str, Path], output_dir: Path) -> list[str]:
    return [sys.executable, "scripts/materialize_grounded_critic_independent_ai_labels.py", "--assignment", str(paths["assignment"]), "--assignment-manifest", str(paths["assignment_manifest"]), "--structured-tables", str(paths["tables"]), "--evidence-context", str(paths["contexts"]), "--review-items", str(paths["items"]), "--source-root", str(paths["source_root"]), "--output-dir", str(output_dir), "--reviewed-at", "2026-08-12T00:00:00Z"]


def test_materializes_blind_hash_bound_independent_ai_labels(tmp_path: Path) -> None:
    paths = _make_inputs(tmp_path)
    output = tmp_path / "output"
    subprocess.run(_command(paths, output), cwd=ROOT, check=True)
    labels = [json.loads(line) for line in (output / "grounded_critic_reviewer_a_independent_ai_labels_v1.jsonl").read_text().splitlines()]
    assert labels[0]["status"] == "accept"
    assert labels[0]["provenance"] == "independent_ai_source_review"
    assert labels[0]["blind_to_qwen_decision"] is True
    assert labels[0]["source_contract"]["promotion_allowed"] is False


def test_fails_closed_when_raw_statement_hash_changes(tmp_path: Path) -> None:
    paths = _make_inputs(tmp_path)
    (paths["source_root"] / "statement.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(_command(paths, tmp_path / "output"), cwd=ROOT, check=True)


def test_accepts_provenanced_table_header_unit_when_selected_column_is_merged(tmp_path: Path) -> None:
    paths = _make_inputs(tmp_path)
    contexts = [json.loads(line) for line in paths["contexts"].read_text().splitlines()]
    contexts[0]["canonical_headers"]["columns"][0]["unit_labels"] = []
    contexts[0]["canonical_headers"]["columns"].append(
        {"column_index": 3, "source_label": "Đơn vị tính: VND", "header_source_cells": [{"row_index": 0, "column_index": 2}], "period_labels": [], "unit_labels": ["VND"]}
    )
    _write_jsonl(paths["contexts"], contexts)
    subprocess.run(_command(paths, tmp_path / "output"), cwd=ROOT, check=True)
    label = json.loads((tmp_path / "output" / "grounded_critic_reviewer_a_independent_ai_labels_v1.jsonl").read_text().strip())
    assert label["source_checks"]["table_header_unit_labels"] == ["VND"]


def test_direct_lookup_role_markers_cover_the_q702_other_income_row() -> None:
    assert "thu nhap khac" in ROLE_ROW_MARKERS["other_income"]


def test_direct_lookup_role_markers_cover_the_q251_cash_row() -> None:
    assert "tien va cac khoan tuong duong tien" in ROLE_ROW_MARKERS["cash_and_cash_equivalents"]


def test_direct_lookup_role_markers_cover_the_q292_gross_revenue_row() -> None:
    assert "doanh thu ban hang va cung cap dich vu" in ROLE_ROW_MARKERS["gross_revenue"]


def test_accepts_a_later_report_with_an_exact_comparative_period_column(tmp_path: Path) -> None:
    paths = _make_inputs(tmp_path)
    packet = _packet()
    packet["question_context"]["years"] = [2016]
    assignment = {
        "protocol": "grounded_critic_independent_review_assignment_v1",
        "assignment_id": "critic-v2-reviewer_a-q6",
        "reviewer_slot": "reviewer_a",
        "question_id": 6,
        "immutable_packet_sha256": hashlib.sha256(
            json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "packet": packet,
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    _write_jsonl(paths["assignment"], [assignment])
    manifest = json.loads(paths["assignment_manifest"].read_text())
    manifest["outputs"]["reviewer_a"]["assignment_sha256"] = _sha(paths["assignment"])
    paths["assignment_manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    contexts = [json.loads(line) for line in paths["contexts"].read_text().splitlines()]
    contexts[0]["canonical_headers"]["columns"][0]["source_label"] = "2016 VND"
    contexts[0]["canonical_headers"]["columns"][0]["period_labels"] = ["2016"]
    _write_jsonl(paths["contexts"], contexts)
    items = [json.loads(line) for line in paths["items"].read_text().splitlines()]
    items[0]["question"] = "Lưu chuyển tiền VSC năm 2016 là bao nhiêu tỷ đồng?"
    items[0]["candidates"][0]["report_year"] = 2017
    items[0]["candidates"][0]["year_match"] = False
    _write_jsonl(paths["items"], items)
    raw = paths["source_root"] / "statement.txt"
    raw.write_text("2016 VND\nLưu chuyển tiền thuần từ hoạt động kinh doanh\n145.731.366.146\n", encoding="utf-8")
    tables = [json.loads(line) for line in paths["tables"].read_text().splitlines()]
    tables[0]["rows"][0][2] = "2016 VND"
    tables[0]["source_provenance"]["source_sha256"] = _sha(raw)
    _write_jsonl(paths["tables"], tables)

    subprocess.run(_command(paths, tmp_path / "output"), cwd=ROOT, check=True)


def test_materializes_the_q251_cash_role_only_after_replaying_source_cell(tmp_path: Path) -> None:
    paths = _make_inputs(tmp_path)
    packet = _packet()
    packet["bounded_source_excerpts"][0]["role"] = "cash_and_cash_equivalents"
    packet["deterministic_execution_trace"][0]["operand_sources"][0]["role"] = "cash_and_cash_equivalents"
    assignment = {
        "protocol": "grounded_critic_independent_review_assignment_v1",
        "assignment_id": "critic-v2-reviewer_a-q6",
        "reviewer_slot": "reviewer_a",
        "question_id": 6,
        "immutable_packet_sha256": hashlib.sha256(
            json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "packet": packet,
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    _write_jsonl(paths["assignment"], [assignment])
    assignment_manifest = json.loads(paths["assignment_manifest"].read_text())
    assignment_manifest["outputs"]["reviewer_a"]["assignment_sha256"] = _sha(paths["assignment"])
    paths["assignment_manifest"].write_text(json.dumps(assignment_manifest), encoding="utf-8")
    table_rows = [json.loads(line) for line in paths["tables"].read_text().splitlines()]
    table_rows[0]["rows"][1][0] = "Tiền và các khoản tương đương tiền"
    _write_jsonl(paths["tables"], table_rows)
    raw = paths["source_root"] / "statement.txt"
    raw.write_text("2017 VND\nTiền và các khoản tương đương tiền\n145.731.366.146\n", encoding="utf-8")
    table_rows[0]["source_provenance"]["source_sha256"] = _sha(raw)
    _write_jsonl(paths["tables"], table_rows)

    subprocess.run(_command(paths, tmp_path / "output"), cwd=ROOT, check=True)
    label = json.loads((tmp_path / "output" / "grounded_critic_reviewer_a_independent_ai_labels_v1.jsonl").read_text())
    assert label["source_checks"]["row_index"] == 1
    assert label["source_contract"]["promotion_allowed"] is False
