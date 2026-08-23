from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_ccl_phase5_component_selection_final_review_calibration.py"
SCORER = ROOT / "scripts" / "score_ccl_phase5_component_selection_final_review_calibration.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _packet(packet_id: str, table_id: str, context: str, header: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": "vifinqa_ccl_phase5_component_selection_v1",
        "component_selection_packet_id": packet_id,
        "internal_table_uid": table_id,
        "phase3_request_id": f"p3-{packet_id}",
        "phase45_assertion_id": f"p45-{packet_id}",
        "route_id": "route-1",
        "source_first_semantic_route_id": f"source-{packet_id}",
        "source_first_route_status": "DETERMINISTIC_NOTE_CONTEXT_COMPONENTS_REQUIRED",
        "table_structure_context_id": f"structure-{packet_id}",
        "source_quality_status": "needs_review",
        "source_quality_reason_codes": [],
        "component_menu_stats": {"header_candidate_count": 1, "numeric_value_header_exclusion_count": 0},
        "components": [
            {"component_id": f"context-{packet_id}", "literal": context, "role": "report_scope_or_selected_relation_context"},
            {"component_id": f"header-{packet_id}", "literal": header, "role": "column_header"},
        ],
        "task": {"name": "bounded_literal_table_context_component_selection"},
        "training_eligible": False,
        "certification_allowed": False,
    }


def _model(model_id: str, packet_id: str, support: list[str]) -> dict[str, object]:
    return {
        "model_id": model_id,
        "result_status": "VALID_COMPONENT_SELECTION_ONLY",
        "primary_component_id": f"context-{packet_id}",
        "supporting_component_ids": support,
        "unresolved_conditions": [],
    }


def _fixture(root: Path) -> dict[str, Path]:
    packets = root / "packets.jsonl"
    packet_rows = [_packet("packet-1", "table-1", "Scope 1", "Header 1"), _packet("packet-2", "table-2", "Scope 2", "Header 2")]
    _jsonl(packets, packet_rows)
    source_manifest = root / "packets.manifest.json"
    _json(
        source_manifest,
        {
            "protocol": "vifinqa_ccl_phase5_component_selection_v1",
            "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
            "navigation_overlay_required": True,
            "inputs": {
                "report_navigation_overlay_v1.jsonl": {"sha256": "a" * 64},
                "report_navigation_overlay_manifest.json": {"sha256": "b" * 64},
            },
            "outputs": {packets.name: {"sha256": _sha(packets)}},
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    qwen_audit = root / "qwen.audit.json"
    mistral_audit = root / "mistral.audit.json"
    for path, model_id in ((qwen_audit, "Qwen/Qwen3-8B"), (mistral_audit, "mistralai/Mistral-Nemo-Instruct-2407")):
        _json(
            path,
            {
                "protocol": "ccl_phase5_component_selection_smoke_audit_v1",
                "audit_passed": True,
                "route": {"route_id": "route-1", "model_id": model_id},
                "input_hashes": {packets.name: _sha(packets)},
                "numeric_guard": {
                    "applied": True,
                    "source_numeric_header_violation_count": 0,
                    "source_numeric_value_header_exclusion_count": 2,
                },
                "training_eligible": False,
                "certification_allowed": False,
            },
        )
    agreement = root / "agreement.jsonl"
    agreement_rows = [
        {
            "protocol": "ccl_phase5_component_selection_agreement_v1",
            "component_selection_packet_id": "packet-1",
            "internal_table_uid": "table-1",
            "agreement_status": "EXACT_CLOSED_WORLD_AGREEMENT",
            "primary_model": _model("Qwen/Qwen3-8B", "packet-1", ["header-packet-1"]),
            "challenger_model": _model("mistralai/Mistral-Nemo-Instruct-2407", "packet-1", ["header-packet-1"]),
            "supporting_overlap": {"intersection_count": 1, "union_count": 1},
            "training_eligible": False,
            "certification_allowed": False,
        },
        {
            "protocol": "ccl_phase5_component_selection_agreement_v1",
            "component_selection_packet_id": "packet-2",
            "internal_table_uid": "table-2",
            "agreement_status": "PRIMARY_CONTEXT_AGREEMENT_SUPPORT_DIFFERENCE",
            "primary_model": _model("Qwen/Qwen3-8B", "packet-2", ["header-packet-2"]),
            "challenger_model": _model("mistralai/Mistral-Nemo-Instruct-2407", "packet-2", []),
            "supporting_overlap": {"intersection_count": 0, "union_count": 1},
            "training_eligible": False,
            "certification_allowed": False,
        },
    ]
    _jsonl(agreement, agreement_rows)
    agreement_report = root / "agreement.report.json"
    _json(
        agreement_report,
        {
            "protocol": "ccl_phase5_component_selection_agreement_v1",
            "run_status": "component_selection_agreement_complete_not_calibrated",
            "models": ["Qwen/Qwen3-8B", "mistralai/Mistral-Nemo-Instruct-2407"],
            "training_eligible_output_count": 0,
            "certification_allowed": False,
        },
    )
    agreement_manifest = root / "agreement.manifest.json"
    _json(
        agreement_manifest,
        {
            "protocol": "ccl_phase5_component_selection_agreement_v1",
            "run_status": "component_selection_agreement_complete_not_calibrated",
            "inputs": {qwen_audit.name: {"sha256": _sha(qwen_audit)}, mistral_audit.name: {"sha256": _sha(mistral_audit)}},
            "outputs": {agreement.name: {"sha256": _sha(agreement)}, agreement_report.name: {"sha256": _sha(agreement_report)}},
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    return {
        "source_packets": packets,
        "source_manifest": source_manifest,
        "agreement_results": agreement,
        "agreement_report": agreement_report,
        "agreement_manifest": agreement_manifest,
        "qwen_audit": qwen_audit,
        "mistral_audit": mistral_audit,
    }


def _build(paths: dict[str, Path], output_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(BUILDER), *sum(([f"--{key.replace('_', '-')}", str(value)] for key, value in paths.items()), []), "--output-dir", str(output_dir)],
        cwd=ROOT,
        check=True,
    )


def test_final_review_calibration_blinds_models_and_scores_without_promotion(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    package = tmp_path / "package"
    _build(paths, package)
    assignments = [json.loads(line) for line in (package / "component_selection_final_review_assignment_v1.jsonl").read_text().splitlines()]
    assert len(assignments) == 2
    assert all(row["model_decisions_blinded"] is True for row in assignments)
    assert all("primary_model" not in row and "challenger_model" not in row for row in assignments)
    templates = [json.loads(line) for line in (package / "component_selection_final_review_response_template_v1.jsonl").read_text().splitlines()]
    assignment_by_id = {row["calibration_item_id"]: row for row in assignments}
    responses: list[dict[str, object]] = []
    for template in templates:
        item = assignment_by_id[template["calibration_item_id"]]
        packet_id = item["review_packet"]["component_selection_packet_id"]
        responses.append(
            {
                **template,
                "reviewer_id": "human-final-reviewer",
                "reviewed_at_utc": "2026-08-17T08:00:00Z",
                "review_decision": "SELECT_COMPONENTS",
                "primary_component_id": f"context-{packet_id}",
                "supporting_component_ids": [f"header-{packet_id}"],
                "source_coordinates_checked": True,
            }
        )
    responses_path = tmp_path / "responses.jsonl"
    _jsonl(responses_path, responses)
    score_output = tmp_path / "score"
    subprocess.run(
        [
            sys.executable,
            str(SCORER),
            "--assignment",
            str(package / "component_selection_final_review_assignment_v1.jsonl"),
            "--response-template",
            str(package / "component_selection_final_review_response_template_v1.jsonl"),
            "--ledger",
            str(package / "component_selection_model_comparison_ledger_v1.jsonl"),
            "--calibration-manifest",
            str(package / "component_selection_final_review_calibration_manifest.json"),
            "--responses",
            str(responses_path),
            "--output-dir",
            str(score_output),
        ],
        cwd=ROOT,
        check=True,
    )
    score = json.loads((score_output / "component_selection_final_review_calibration_score_v1.json").read_text())
    assert score["run_status"] == "final_review_calibration_scored_not_promoted"
    assert score["global_metrics"]["qwen_primary_match_count"] == 2
    assert score["training_eligible_output_count"] == 0
    assert score["certification_allowed"] is False


def test_final_review_calibration_rejects_primary_disagreement(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    agreement_rows = [json.loads(line) for line in paths["agreement_results"].read_text().splitlines()]
    agreement_rows[1]["agreement_status"] = "DISAGREEMENT_QUARANTINED"
    _jsonl(paths["agreement_results"], agreement_rows)
    agreement_manifest = json.loads(paths["agreement_manifest"].read_text())
    agreement_manifest["outputs"][paths["agreement_results"].name]["sha256"] = _sha(paths["agreement_results"])
    _json(paths["agreement_manifest"], agreement_manifest)
    result = subprocess.run(
        [sys.executable, str(BUILDER), *sum(([f"--{key.replace('_', '-')}", str(value)] for key, value in paths.items()), []), "--output-dir", str(tmp_path / "out")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "separate quarantine campaign" in result.stderr


def test_final_review_calibration_requires_navigation_provenance(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    source_manifest = json.loads(paths["source_manifest"].read_text())
    source_manifest.pop("navigation_overlay_required")
    _json(paths["source_manifest"], source_manifest)

    result = subprocess.run(
        [sys.executable, str(BUILDER), *sum(([f"--{key.replace('_', '-')}", str(value)] for key, value in paths.items()), []), "--output-dir", str(tmp_path / "out")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "required navigation overlay" in result.stderr
