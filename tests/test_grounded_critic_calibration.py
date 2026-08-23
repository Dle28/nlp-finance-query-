from __future__ import annotations
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"artifacts/research/grounded_critic_v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result_manifest(path: Path) -> Path:
    manifest = path.parent / "results.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "protocol": "grounded_critic_results_v2",
                "inputs": {
                    "packets": {"sha256": _sha(BASE / "grounded_critic_packets_v1.jsonl")},
                    "packets_manifest": {"sha256": _sha(BASE / "grounded_critic_packets_v1.manifest.json")},
                },
                "outputs": {"results": {"sha256": _sha(BASE / "grounded_critic_dry_run_v1.jsonl")}},
                "counts": {
                    "packet_count": 3,
                    "result_count": 3,
                    "schema_valid_count": 3,
                    "machine_provisional_count": 3,
                    "invalid_schema_count": 0,
                    "external_evidence_reference_count": 0,
                    "numeric_invention_count": 0,
                    "candidate_selection_count": 0,
                },
                "source_contract": {
                    "candidate_only": True,
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                    "may_select_final_candidate": False,
                    "may_select_value": False,
                    "may_execute_formula": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest

def test_calibration_without_independent_labels_promotes_nothing(tmp_path:Path)->None:
 items_dir=tmp_path/"items"; score_dir=tmp_path/"score"
 result_manifest = _result_manifest(tmp_path)
 subprocess.run([sys.executable,"scripts/build_grounded_critic_calibration_set.py","--critic-packets",str(BASE/"grounded_critic_packets_v1.jsonl"),"--critic-packets-manifest",str(BASE/"grounded_critic_packets_v1.manifest.json"),"--critic-results",str(BASE/"grounded_critic_dry_run_v1.jsonl"),"--critic-results-manifest",str(result_manifest),"--output-dir",str(items_dir)],cwd=ROOT,check=True)
 subprocess.run([sys.executable,"scripts/score_grounded_critic_calibration.py","--items",str(items_dir/"calibration_items_v1.jsonl"),"--items-manifest",str(items_dir/"grounded_critic_calibration_v1.manifest.json"),"--output-dir",str(score_dir)],cwd=ROOT,check=True)
 assert (score_dir/"promoted_strata_v1.jsonl").read_text()==""
 assert all(json.loads(x)["state"] in {"machine_provisional","needs_human"} for x in (score_dir/"unresolved_review_queue_v1.jsonl").read_text().splitlines())
 calibration_manifest = json.loads((items_dir / "grounded_critic_calibration_v1.manifest.json").read_text())
 assert calibration_manifest["inputs"]["critic_results_manifest"]["sha256"] == _sha(result_manifest)


def test_calibration_rejects_results_without_hash_bound_closed_world_manifest(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_calibration_set.py",
            "--critic-packets",
            str(BASE / "grounded_critic_packets_v1.jsonl"),
            "--critic-packets-manifest",
            str(BASE / "grounded_critic_packets_v1.manifest.json"),
            "--critic-results",
            str(BASE / "grounded_critic_dry_run_v1.jsonl"),
            "--critic-results-manifest",
            str(tmp_path / "missing.manifest.json"),
            "--output-dir",
            str(tmp_path / "items"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "missing.manifest.json" in result.stderr


def test_calibration_rejects_manifest_that_weakens_candidate_only_contract(tmp_path: Path) -> None:
    result_manifest = _result_manifest(tmp_path)
    manifest = json.loads(result_manifest.read_text())
    manifest["source_contract"]["candidate_only"] = False
    result_manifest.write_text(json.dumps(manifest))
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_calibration_set.py",
            "--critic-packets",
            str(BASE / "grounded_critic_packets_v1.jsonl"),
            "--critic-packets-manifest",
            str(BASE / "grounded_critic_packets_v1.manifest.json"),
            "--critic-results",
            str(BASE / "grounded_critic_dry_run_v1.jsonl"),
            "--critic-results-manifest",
            str(result_manifest),
            "--output-dir",
            str(tmp_path / "items"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "candidate-only" in result.stderr


def test_calibration_rejects_malformed_declared_source_bundle_provenance(tmp_path: Path) -> None:
    result_manifest = _result_manifest(tmp_path)
    manifest = json.loads(result_manifest.read_text())
    manifest["inputs"]["source_bundle"] = {"source_tree_sha256": "short"}
    result_manifest.write_text(json.dumps(manifest))
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_calibration_set.py",
            "--critic-packets",
            str(BASE / "grounded_critic_packets_v1.jsonl"),
            "--critic-packets-manifest",
            str(BASE / "grounded_critic_packets_v1.manifest.json"),
            "--critic-results",
            str(BASE / "grounded_critic_dry_run_v1.jsonl"),
            "--critic-results-manifest",
            str(result_manifest),
            "--output-dir",
            str(tmp_path / "items"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "source bundle" in result.stderr


def test_source_bound_calibration_requires_matching_passed_gpu_audit(tmp_path: Path) -> None:
    result_manifest = _result_manifest(tmp_path)
    manifest = json.loads(result_manifest.read_text())
    bundle = {
        "manifest": {"sha256": "a" * 64},
        "archive": {"sha256": "b" * 64},
        "source_tree_sha256": "c" * 64,
        "git_revision": "deadbeef",
    }
    manifest["inputs"]["source_bundle"] = bundle
    result_manifest.write_text(json.dumps(manifest))
    common = [
        sys.executable,
        "scripts/build_grounded_critic_calibration_set.py",
        "--critic-packets",
        str(BASE / "grounded_critic_packets_v1.jsonl"),
        "--critic-packets-manifest",
        str(BASE / "grounded_critic_packets_v1.manifest.json"),
        "--critic-results",
        str(BASE / "grounded_critic_dry_run_v1.jsonl"),
        "--critic-results-manifest",
        str(result_manifest),
    ]
    missing = subprocess.run(
        [*common, "--output-dir", str(tmp_path / "missing-audit")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "require a matching passed GPU run audit" in missing.stderr

    audit = tmp_path / "gpu-audit.json"
    audit.write_text(
        json.dumps(
            {
                "protocol": "grounded_critic_gpu_run_audit_v2",
                "audit_passed": True,
                "inputs": {
                    "packets": {"sha256": _sha(BASE / "grounded_critic_packets_v1.jsonl")},
                    "packets_manifest": {"sha256": _sha(BASE / "grounded_critic_packets_v1.manifest.json")},
                    "results": {"sha256": _sha(BASE / "grounded_critic_dry_run_v1.jsonl")},
                    "results_manifest": {"sha256": _sha(result_manifest)},
                    "source_bundle": {
                        "declared_input": {"manifest": bundle["manifest"], "archive": bundle["archive"]},
                        "verified": {
                            "manifest_sha256": bundle["manifest"]["sha256"],
                            "archive_sha256": bundle["archive"]["sha256"],
                            "source_tree_sha256": bundle["source_tree_sha256"],
                            "git_revision": bundle["git_revision"],
                        },
                    },
                },
                "source_contract": manifest["source_contract"],
            }
        )
    )
    subprocess.run(
        [*common, "--gpu-run-audit", str(audit), "--output-dir", str(tmp_path / "audited")],
        cwd=ROOT,
        check=True,
    )
    calibration_manifest = json.loads(
        (tmp_path / "audited" / "grounded_critic_calibration_v1.manifest.json").read_text()
    )
    assert calibration_manifest["inputs"]["gpu_run_audit"]["sha256"] == _sha(audit)


def test_source_bound_calibration_rejects_unmanifested_independent_labels(tmp_path: Path) -> None:
    result_manifest = _result_manifest(tmp_path)
    manifest = json.loads(result_manifest.read_text())
    bundle = {
        "manifest": {"sha256": "a" * 64},
        "archive": {"sha256": "b" * 64},
        "source_tree_sha256": "c" * 64,
        "git_revision": "deadbeef",
    }
    manifest["inputs"]["source_bundle"] = bundle
    result_manifest.write_text(json.dumps(manifest))
    audit = tmp_path / "gpu-audit.json"
    audit.write_text(
        json.dumps(
            {
                "protocol": "grounded_critic_gpu_run_audit_v2",
                "audit_passed": True,
                "inputs": {
                    "packets": {"sha256": _sha(BASE / "grounded_critic_packets_v1.jsonl")},
                    "packets_manifest": {"sha256": _sha(BASE / "grounded_critic_packets_v1.manifest.json")},
                    "results": {"sha256": _sha(BASE / "grounded_critic_dry_run_v1.jsonl")},
                    "results_manifest": {"sha256": _sha(result_manifest)},
                    "source_bundle": {
                        "declared_input": {"manifest": bundle["manifest"], "archive": bundle["archive"]},
                        "verified": {
                            "manifest_sha256": bundle["manifest"]["sha256"],
                            "archive_sha256": bundle["archive"]["sha256"],
                            "source_tree_sha256": bundle["source_tree_sha256"],
                            "git_revision": bundle["git_revision"],
                        },
                    },
                },
                "source_contract": manifest["source_contract"],
            }
        )
    )
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"question_id": 6, "provenance": "independent_ai_source_review"}) + "\n")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_calibration_set.py",
            "--critic-packets",
            str(BASE / "grounded_critic_packets_v1.jsonl"),
            "--critic-packets-manifest",
            str(BASE / "grounded_critic_packets_v1.manifest.json"),
            "--critic-results",
            str(BASE / "grounded_critic_dry_run_v1.jsonl"),
            "--critic-results-manifest",
            str(result_manifest),
            "--gpu-run-audit",
            str(audit),
            "--independent-labels",
            str(labels),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "matching label manifest" in result.stderr


def test_calibration_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    result_manifest = _result_manifest(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_calibration_set.py",
            "--critic-packets",
            str(BASE / "grounded_critic_packets_v1.jsonl"),
            "--critic-packets-manifest",
            str(BASE / "grounded_critic_packets_v1.manifest.json"),
            "--critic-results",
            str(BASE / "grounded_critic_dry_run_v1.jsonl"),
            "--critic-results-manifest",
            str(result_manifest),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr


def test_source_bound_calibration_accepts_only_complete_manifested_blind_human_labels(tmp_path: Path) -> None:
    packets = BASE / "grounded_critic_packets_v1.jsonl"
    packets_manifest = BASE / "grounded_critic_packets_v1.manifest.json"
    results = BASE / "grounded_critic_dry_run_v1.jsonl"
    packet_rows = [json.loads(line) for line in packets.read_text().splitlines()]
    result_manifest = _result_manifest(tmp_path)
    result_payload = json.loads(result_manifest.read_text())
    bundle = {
        "manifest": {"sha256": "a" * 64},
        "archive": {"sha256": "b" * 64},
        "source_tree_sha256": "c" * 64,
        "git_revision": "deadbeef",
    }
    result_payload["inputs"]["source_bundle"] = bundle
    result_manifest.write_text(json.dumps(result_payload))
    audit = tmp_path / "gpu-audit.json"
    audit.write_text(
        json.dumps(
            {
                "protocol": "grounded_critic_gpu_run_audit_v2",
                "audit_passed": True,
                "inputs": {
                    "packets": {"sha256": _sha(packets)},
                    "packets_manifest": {"sha256": _sha(packets_manifest)},
                    "results": {"sha256": _sha(results)},
                    "results_manifest": {"sha256": _sha(result_manifest)},
                    "source_bundle": {
                        "declared_input": {"manifest": bundle["manifest"], "archive": bundle["archive"]},
                        "verified": {
                            "manifest_sha256": bundle["manifest"]["sha256"],
                            "archive_sha256": bundle["archive"]["sha256"],
                            "source_tree_sha256": bundle["source_tree_sha256"],
                            "git_revision": bundle["git_revision"],
                        },
                    },
                },
                "source_contract": result_payload["source_contract"],
            }
        )
    )
    contract = result_payload["source_contract"]
    assignments = tmp_path / "assignments.jsonl"
    assignment_rows = []
    labels = []
    for packet in packet_rows:
        question_id = packet["question_id"]
        blind = {
            key: packet.get(key)
            for key in (
                "schema_version", "protocol", "question_id", "question_context", "execution_status",
                "deterministic_execution_trace", "bounded_source_excerpts", "allowed_decisions",
                "allowed_reason_codes", "allowed_packet_evidence_ids", "source_contract",
            )
        }
        immutable = hashlib.sha256(json.dumps(blind, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assignment_id = f"critic-v2-reviewer_a-q{question_id}"
        assignment_rows.append({
            "protocol": "grounded_critic_independent_review_assignment_v1",
            "assignment_id": assignment_id,
            "reviewer_slot": "reviewer_a",
            "question_id": question_id,
            "immutable_packet_sha256": immutable,
            "packet": blind,
            "source_contract": contract,
        })
        labels.append({
            "question_id": question_id,
            "assignment_id": assignment_id,
            "immutable_packet_sha256": immutable,
            "status": "accept",
            "provenance": "human_verified",
            "source_coordinates_checked": True,
            "source_coordinate_agree": True,
            "unit_period_agree": True,
            "deterministic_replay_agree": True,
            "unsupported_evidence": False,
            "source_contract": contract,
        })
    assignments.write_text("".join(json.dumps(row) + "\n" for row in assignment_rows))
    assignment_manifest = tmp_path / "assignments.manifest.json"
    assignment_manifest.write_text(
        json.dumps(
            {
                "protocol": "grounded_critic_independent_review_assignment_v1",
                "blind_to_qwen_decision": True,
                "labels_prepopulated": False,
                "source_contract": contract,
                "outputs": {"reviewer_a": {"assignment_sha256": _sha(assignments)}},
                "inputs": {
                    "packets": {"sha256": _sha(packets)},
                    "packets_manifest": {"sha256": _sha(packets_manifest)},
                    "critic_results": {"sha256": _sha(results)},
                    "critic_results_manifest": {"sha256": _sha(result_manifest)},
                    "gpu_run_audit": {"sha256": _sha(audit)},
                },
            }
        )
    )
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text("".join(json.dumps(row) + "\n" for row in labels))
    labels_manifest = tmp_path / "labels.manifest.json"
    labels_manifest.write_text(
        json.dumps(
            {
                "protocol": "grounded_critic_independent_human_review_v1",
                "blind_to_qwen_decision": True,
                "reviewer_slot": "reviewer_a",
                "source_contract": contract,
                "counts": {"label_count": len(labels)},
                "inputs": {
                    "assignment": {"path": str(assignments), "sha256": _sha(assignments)},
                    "assignment_manifest": {"path": str(assignment_manifest), "sha256": _sha(assignment_manifest)},
                },
                "outputs": {"labels": {"sha256": _sha(labels_path)}},
            }
        )
    )
    output = tmp_path / "calibration"
    subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_calibration_set.py",
            "--critic-packets", str(packets),
            "--critic-packets-manifest", str(packets_manifest),
            "--critic-results", str(results),
            "--critic-results-manifest", str(result_manifest),
            "--gpu-run-audit", str(audit),
            "--independent-labels", str(labels_path),
            "--independent-labels-manifest", str(labels_manifest),
            "--output-dir", str(output),
        ],
        cwd=ROOT,
        check=True,
    )
    manifest = json.loads((output / "grounded_critic_calibration_v1.manifest.json").read_text())
    assert manifest["counts"]["independent_label_count"] == len(packet_rows)
    assert manifest["inputs"]["independent_labels_manifest"]["sha256"] == _sha(labels_manifest)


def test_source_bound_calibration_rejects_duplicate_independent_labels(tmp_path: Path) -> None:
    result_manifest = _result_manifest(tmp_path)
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        "".join(json.dumps({"question_id": 6, "provenance": "independent_ai_source_review"}) + "\n" for _ in range(2))
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_grounded_critic_calibration_set.py",
            "--critic-packets", str(BASE / "grounded_critic_packets_v1.jsonl"),
            "--critic-packets-manifest", str(BASE / "grounded_critic_packets_v1.manifest.json"),
            "--critic-results", str(BASE / "grounded_critic_dry_run_v1.jsonl"),
            "--critic-results-manifest", str(result_manifest),
            "--independent-labels", str(labels),
            "--output-dir", str(tmp_path / "out"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unique question IDs" in result.stderr

def test_calibration_scores_hash_bound_independent_ai_source_review(tmp_path:Path)->None:
 items=tmp_path/"items.jsonl"; manifest=tmp_path/"items.manifest.json"; score_dir=tmp_path/"score"
 row={"question_id":6,"stratum":{"scope":"separate"},"critic_result":{"status":"accept","provenance":"machine_provisional"},"calibration_eligible":True,"independent_label":{"status":"accept","provenance":"independent_ai_source_review","source_coordinate_agree":True,"unit_period_agree":True,"deterministic_replay_agree":True,"unsupported_evidence":False,"source_contract":{"evidence_eligible":False,"training_eligible":False,"submission_eligible":False,"promotion_allowed":False}}}
 items.write_text(json.dumps(row)+"\n")
 import hashlib
 manifest.write_text(json.dumps({"outputs":{"items":{"sha256":hashlib.sha256(items.read_bytes()).hexdigest()}}}))
 subprocess.run([sys.executable,"scripts/score_grounded_critic_calibration.py","--items",str(items),"--items-manifest",str(manifest),"--output-dir",str(score_dir)],cwd=ROOT,check=True)
 scored=json.loads((score_dir/"calibration_scores_v1.json").read_text())
 result=scored["strata"][0]
 assert result["sample_size"]==1 and result["agreement"]==1.0
 assert result["source_coordinate_agreement"]==1.0
 assert result["promotion_recommendation"]=="needs_human"
 assert scored["training_quality_metrics"]["independent_audit_sample_size"]==0
 assert scored["training_quality_metrics"]["independent_audit_precision"] is None
