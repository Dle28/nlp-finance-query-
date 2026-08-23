from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_assignments_are_blind_to_qwen_decisions_and_hash_bound(tmp_path: Path) -> None:
    packets = tmp_path / "packets.jsonl"
    results = tmp_path / "results.jsonl"
    queue = tmp_path / "queue.jsonl"
    packet_rows = [
        {"question_id": 6, "execution_status": "execution_replay_ready", "allowed_decisions": ["accept", "reject", "abstain"], "allowed_packet_evidence_ids": ["u:1:2"], "bounded_source_excerpts": [], "deterministic_execution_trace": [], "source_contract": {}},
        {"question_id": 27, "execution_status": "execution_replay_ready", "allowed_decisions": ["accept", "reject", "abstain"], "allowed_packet_evidence_ids": ["u:3:4"], "bounded_source_excerpts": [], "deterministic_execution_trace": [], "source_contract": {}},
    ]
    _write_jsonl(packets, packet_rows)
    packet_manifest = tmp_path / "packets.manifest.json"
    packet_manifest.write_text(json.dumps({"outputs": {"packets": {"sha256": _sha(packets)}}}))
    _write_jsonl(results, [{"question_id": 6, "status": "accept", "provenance": "machine_provisional"}, {"question_id": 27, "status": "reject", "provenance": "machine_provisional"}])
    result_manifest = tmp_path / "results.manifest.json"
    result_manifest.write_text(json.dumps({"inputs": {"packets": {"sha256": _sha(packets)}, "packets_manifest": {"sha256": _sha(packet_manifest)}}, "outputs": {"results": {"sha256": _sha(results)}}}))
    _write_jsonl(queue, [{"question_id": 6, "reason": "INSUFFICIENT_INDEPENDENT_CALIBRATION"}, {"question_id": 27, "reason": "INSUFFICIENT_INDEPENDENT_CALIBRATION"}])
    output_dir = tmp_path / "assignments"

    subprocess.run([sys.executable, "scripts/build_grounded_critic_independent_review_assignments.py", "--packets", str(packets), "--packets-manifest", str(packet_manifest), "--critic-results", str(results), "--critic-results-manifest", str(result_manifest), "--unresolved-queue", str(queue), "--output-dir", str(output_dir)], cwd=ROOT, check=True)

    manifest = json.loads((output_dir / "grounded_critic_independent_review_assignments_v1.manifest.json").read_text())
    assert manifest["blind_to_qwen_decision"] is True
    assert manifest["labels_prepopulated"] is False
    for slot in ("reviewer_a", "reviewer_b"):
        assignment_rows = [json.loads(line) for line in (output_dir / f"grounded_critic_{slot}_assignment_v1.jsonl").read_text().splitlines()]
        template_rows = [json.loads(line) for line in (output_dir / f"grounded_critic_{slot}_label_template_v1.jsonl").read_text().splitlines()]
        assert {row["question_id"] for row in assignment_rows} == {6, 27}
        assert all("critic_result" not in row and "status" not in row["packet"] for row in assignment_rows)
        assert all(row["status"] is None and row["provenance"] is None and row["is_blank_template"] for row in template_rows)


def test_source_bound_assignments_require_gpu_audit(tmp_path: Path) -> None:
    packets = tmp_path / "packets.jsonl"
    results = tmp_path / "results.jsonl"
    queue = tmp_path / "queue.jsonl"
    _write_jsonl(packets, [{"question_id": 6, "execution_status": "execution_replay_ready"}])
    packet_manifest = tmp_path / "packets.manifest.json"
    packet_manifest.write_text(json.dumps({"outputs": {"packets": {"sha256": _sha(packets)}}}))
    _write_jsonl(results, [{"question_id": 6, "status": "accept", "provenance": "machine_provisional"}])
    result_manifest = tmp_path / "results.manifest.json"
    result_manifest.write_text(
        json.dumps(
            {
                "inputs": {
                    "packets": {"sha256": _sha(packets)},
                    "packets_manifest": {"sha256": _sha(packet_manifest)},
                    "source_bundle": {
                        "manifest": {"sha256": "a" * 64},
                        "archive": {"sha256": "b" * 64},
                        "source_tree_sha256": "c" * 64,
                        "git_revision": "deadbeef",
                    },
                },
                "outputs": {"results": {"sha256": _sha(results)}},
            }
        )
    )
    _write_jsonl(queue, [{"question_id": 6, "reason": "INSUFFICIENT_INDEPENDENT_CALIBRATION"}])
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_independent_review_assignments.py",
            "--packets",
            str(packets),
            "--packets-manifest",
            str(packet_manifest),
            "--critic-results",
            str(results),
            "--critic-results-manifest",
            str(result_manifest),
            "--unresolved-queue",
            str(queue),
            "--output-dir",
            str(tmp_path / "assignments"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "require a matching passed GPU run audit" in result.stderr
