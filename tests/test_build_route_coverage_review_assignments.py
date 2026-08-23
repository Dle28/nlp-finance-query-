from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finance_query.route_coverage_adjudication import canonical_sha256, sha256_file, source_contract


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _queue_row(question_id: int) -> dict:
    payload = {
        "question_id": question_id,
        "question": f"Question {question_id}",
        "normalized_question": f"question {question_id}",
        "route_status": "abstain",
        "route_reason_codes": ["MISSING_SCOPE_CONTEXT"],
        "question_context": {"scope": None},
        "route_stages": [],
        "review_tracks": ["question_plan_context"],
        "missing_context": ["scope"],
    }
    return {
        "schema_version": 1,
        "protocol": "route_coverage_adjudication_v1",
        **payload,
        "immutable_route_sha256": "a" * 64,
        "immutable_queue_payload_sha256": canonical_sha256(payload),
        "source_contract": source_contract(),
        "review_decision_contract": {
            "decision": None,
            "decision_provenance": None,
            "reviewer_id": None,
            "reviewed_at": None,
            "source_coordinates_checked": None,
            "proposed_question_plan": None,
            "proposed_taxonomy_alias": None,
            "proposed_operation_contract": None,
            "eligible_for_materialization": False,
        },
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    queue = tmp_path / "queue.jsonl"
    template = tmp_path / "template.jsonl"
    rows = [_queue_row(2), _queue_row(5)]
    _write_jsonl(queue, rows)
    _write_jsonl(
        template,
        [
            {
                "schema_version": 1,
                "protocol": "route_coverage_adjudication_label_template_v1",
                "question_id": row["question_id"],
                "immutable_queue_payload_sha256": row["immutable_queue_payload_sha256"],
                "review_tracks": row["review_tracks"],
                "decision": None,
                "decision_provenance": None,
                "reviewer_id": None,
                "reviewed_at": None,
                "source_coordinates_checked": None,
                "proposed_question_plan": None,
                "proposed_taxonomy_alias": None,
                "proposed_operation_contract": None,
                "notes": "",
                "is_blank_template": True,
                "source_contract": source_contract(),
            }
            for row in rows
        ],
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "protocol": "route_coverage_adjudication_v1",
                "outputs": {
                    "queue": {"sha256": sha256_file(queue)},
                    "label_template": {"sha256": sha256_file(template)},
                },
                "repairs_materialized": False,
                "source_contract": source_contract(),
            }
        ),
        encoding="utf-8",
    )
    return queue, template, manifest


def _run(queue: Path, template: Path, manifest: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/build_route_coverage_review_assignments.py",
            "--queue",
            str(queue),
            "--label-template",
            str(template),
            "--queue-manifest",
            str(manifest),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_route_review_assignments_are_blind_blank_and_hash_bound(tmp_path: Path) -> None:
    queue, template, manifest = _inputs(tmp_path)
    completed = _run(queue, template, manifest, tmp_path / "assignments")
    assert completed.returncode == 0, completed.stderr
    result = json.loads((tmp_path / "assignments" / "route_coverage_independent_review_assignments_v1.manifest.json").read_text())
    assert result["assignment_count_per_reviewer"] == 2
    assert result["blind_to_other_review"] is True
    assert result["labels_prepopulated"] is False
    assert result["materialization_allowed"] is False
    for slot in ("reviewer_a", "reviewer_b"):
        assignments = [json.loads(line) for line in (tmp_path / "assignments" / f"route_coverage_{slot}_assignment_v1.jsonl").read_text().splitlines()]
        labels = [json.loads(line) for line in (tmp_path / "assignments" / f"route_coverage_{slot}_label_template_v1.jsonl").read_text().splitlines()]
        assert {row["question_id"] for row in assignments} == {2, 5}
        assert all(row["source_contract"] == source_contract() for row in assignments)
        assert all(row["decision"] is None and row["is_blank_template"] is True for row in labels)


def test_route_review_assignments_reject_tampered_queue_or_prepopulated_template(tmp_path: Path) -> None:
    queue, template, manifest = _inputs(tmp_path)
    queue.write_text(queue.read_text() + "\n", encoding="utf-8")
    completed = _run(queue, template, manifest, tmp_path / "tampered")
    assert completed.returncode != 0
    assert "SHA-256 mismatch" in completed.stderr

    queue, template, manifest = _inputs(tmp_path / "fresh")
    rows = [json.loads(line) for line in template.read_text().splitlines()]
    rows[0]["decision"] = "accept"
    _write_jsonl(template, rows)
    payload = json.loads(manifest.read_text())
    payload["outputs"]["label_template"]["sha256"] = sha256_file(template)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    completed = _run(queue, template, manifest, tmp_path / "prepopulated")
    assert completed.returncode != 0
    assert "already contains a decision" in completed.stderr
