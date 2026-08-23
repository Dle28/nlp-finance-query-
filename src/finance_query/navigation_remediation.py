"""Scope exact-cell navigation conflicts into a fail-closed human queue."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .binding_conflict_workbench import canonical_sha256, sha256_file, source_contract


PROTOCOL = "vifinqa_navigation_remediation_queue_v1"


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_navigation_remediation_queue(
    *, conflict_workbench: Path, conflict_manifest: Path,
    no_candidate_audit: Path, period_manifest: Path, output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite navigation remediation queue: {output_dir}")
    conflict_meta = json.loads(conflict_manifest.read_text(encoding="utf-8"))
    period_meta = json.loads(period_manifest.read_text(encoding="utf-8"))
    if ((conflict_meta.get("outputs") or {}).get("workbench") or {}).get("sha256") != sha256_file(conflict_workbench):
        raise ValueError("binding conflict workbench SHA-256 mismatch")
    if ((period_meta.get("outputs") or {}).get("no_candidate_audit") or {}).get("sha256") != sha256_file(no_candidate_audit):
        raise ValueError("no-candidate audit SHA-256 mismatch")
    audit_by_question: dict[int, list[dict[str, Any]]] = {}
    for row in _rows(no_candidate_audit):
        audit_by_question.setdefault(int(row["question_id"]), []).append(row)
    queue: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    causes: Counter[str] = Counter()
    for conflict in _rows(conflict_workbench):
        if conflict.get("classification") != "upstream_navigation_candidate_required":
            continue
        question_id = int(conflict["question_id"])
        audits = audit_by_question.get(question_id, [])
        if len(audits) != 1:
            raise ValueError(f"Expected one no-candidate audit for Q{question_id}, got {len(audits)}")
        audit = audits[0]
        cause = str(audit.get("exclusive_primary_cause") or "")
        causes[cause] += 1
        payload = {
            "question_id": question_id,
            "remediation_status": "needs_human_no_eligible_automatic_repair",
            "concept_id": audit.get("concept_id"),
            "exclusive_primary_cause": cause,
            "exclusive_primary_cause_blocker_set": audit.get("exclusive_primary_cause_blocker_set") or [],
            "minimal_blocker_sets": audit.get("minimal_blocker_sets") or [],
            "nearby_exact_concept_candidates": audit.get("nearby_exact_concept_candidates") or [],
            "recommended_review_queue": audit.get("recommended_review_queue"),
            "automatic_materialization_eligible": False,
            "source_conflict_item_sha256": conflict.get("workbench_item_sha256"),
            "source_no_candidate_audit_sha256": canonical_sha256(audit),
        }
        item = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            **payload,
            "remediation_item_sha256": canonical_sha256(payload),
            "source_contract": source_contract(),
        }
        queue.append(item)
        decisions.append({
            "schema_version": 1,
            "protocol": "vifinqa_navigation_remediation_human_decision_v1",
            "question_id": question_id,
            "remediation_item_sha256": item["remediation_item_sha256"],
            "decision": None,
            "approved_candidate": None,
            "decision_provenance": None,
            "reviewer_id": None,
            "reviewed_at": None,
            "notes": "",
            "is_blank_template": True,
            "source_contract": source_contract(),
        })
    output_dir.mkdir(parents=True, exist_ok=False)
    queue_path = output_dir / "navigation_remediation_queue_v1.jsonl"
    decisions_path = output_dir / "navigation_remediation_human_decisions_v1.jsonl"
    _write_jsonl(queue_path, queue)
    _write_jsonl(decisions_path, decisions)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "human_review_required_no_automatic_repairs",
        "inputs": {
            "conflict_workbench": {"path": str(conflict_workbench), "sha256": sha256_file(conflict_workbench)},
            "conflict_manifest": {"path": str(conflict_manifest), "sha256": sha256_file(conflict_manifest)},
            "no_candidate_audit": {"path": str(no_candidate_audit), "sha256": sha256_file(no_candidate_audit)},
            "period_manifest": {"path": str(period_manifest), "sha256": sha256_file(period_manifest)},
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)},
            "blank_decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)},
        },
        "counts": {
            "queue_item_count": len(queue),
            "primary_cause_counts": dict(sorted(causes.items())),
            "automatic_materialization_eligible_count": 0,
            "human_decision_count": 0,
        },
        "source_contract": source_contract(),
    }
    manifest_path = output_dir / "navigation_remediation_queue_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}

