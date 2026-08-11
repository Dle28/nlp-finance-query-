#!/usr/bin/env python3
"""Materialize non-promotable execution records from a Qwen staged audit.

The Qwen reviewer selects only immutable V2/V3 source cells.  Its output is
usable here only after ``audit_qwen_staged_review.py`` independently replays
each selected cell and re-executes every deterministic stage.  This script
creates a precise hand-off artifact for evaluation and future production-audit
work; it deliberately cannot create a submission or a training label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "qwen_staged_execution_ledger_v1"
SCHEMA_VERSION = 1
AUDIT_PROTOCOL = "independent_staged_llm_source_audit_v1"
SUPPORTED_FAMILIES = {
    "quick_ratio_median_then_net_profit_margin",
    "quick_ratio_gpm_interest_coverage_selection",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def index_by_id(rows: Iterable[Mapping[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            question_id = int(row["id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{name} has an invalid id") from error
        if question_id in result:
            raise ValueError(f"{name} has duplicate Q{question_id}")
        result[question_id] = dict(row)
    return result


def final_records(rows: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    return index_by_id(
        (
            row
            for row in rows
            if str(row.get("record_type") or "") == "deterministic_final_result"
        ),
        "Qwen deterministic final records",
    )


def _require_non_promotable_qwen_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if bool(manifest.get("dry_run")) or bool(manifest.get("decision_fixture")):
        raise ValueError("Qwen output must be a real non-fixture run")
    if manifest.get("submission_eligible") is not False:
        raise ValueError("Qwen output must explicitly be non-submission-eligible")
    if manifest.get("provenance_promotion_allowed") is not False:
        raise ValueError("Qwen manifest cannot promote provenance")
    if not set(manifest.get("supported_families") or []).issubset(SUPPORTED_FAMILIES):
        raise ValueError("Qwen manifest declares an unsupported staged family")
    return manifest


def _validated_row(
    *,
    audit: Mapping[str, Any],
    final: Mapping[str, Any],
    route: Mapping[str, Any],
    question: Mapping[str, Any],
) -> dict[str, Any]:
    question_id = int(audit["id"])
    if str(audit.get("protocol") or "") != AUDIT_PROTOCOL:
        raise ValueError(f"Q{question_id}: unsupported staged-audit protocol")
    if (
        str(audit.get("annotation_status") or "") != "machine_calibrated"
        or str(audit.get("provenance_status") or "") != "machine_calibrated"
        or bool(audit.get("human_verified"))
    ):
        raise ValueError(f"Q{question_id}: audit is not machine-calibrated-only")
    if audit.get("submission_eligible") is not False or audit.get("training_eligible") is not False:
        raise ValueError(f"Q{question_id}: staged audit must stay non-promotable")
    if str(final.get("annotation_status") or "") != "machine_provisional":
        raise ValueError(f"Q{question_id}: Qwen final record is not literal-verified")
    if final.get("submission_eligible") is not False or bool(final.get("human_verified")):
        raise ValueError(f"Q{question_id}: Qwen final record is unexpectedly promotable")
    if (
        str(final.get("result_value") or "") != str(audit.get("result_value") or "")
        or str(final.get("result_unit") or "") != str(audit.get("result_unit") or "")
    ):
        raise ValueError(f"Q{question_id}: final Qwen value does not match independent audit")
    route_value = dict(route.get("route") or {})
    family = str(route_value.get("family") or "")
    if str(route_value.get("routing_status") or "") != "planned" or family not in SUPPORTED_FAMILIES:
        raise ValueError(f"Q{question_id}: route is not an approved staged family")
    if int(question.get("id")) != question_id:
        raise ValueError(f"Q{question_id}: question source mismatch")
    direct = dict(audit.get("direct_replay_gate") or {})
    critic = dict(audit.get("independent_critic_gate") or {})
    if not direct or not critic:
        raise ValueError(f"Q{question_id}: dual replay gates are missing")
    return {
        "id": question_id,
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "question": str(question.get("question") or ""),
        "execution_status": "grounded",
        "grounding_status": "staged_exact_cells_replayed",
        "answer_kind": "deterministic_staged_metric",
        "result_value": str(audit["result_value"]),
        "result_unit": str(audit["result_unit"]),
        "route_family": family,
        "route_sha256": canonical_sha256(route_value),
        "provenance_status": "machine_calibrated",
        "annotation_status": "machine_calibrated",
        "direct_replay_gate": direct,
        "independent_critic_gate": critic,
        "reason_codes": list(audit.get("reason_codes") or []),
        "human_verified": False,
        "training_eligible": False,
        "submission_eligible": False,
        "requires_production_audit": True,
        "provenance_promotion_allowed": False,
    }


def atomic_write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def build_ledger(
    *,
    bundle_dir: Path,
    routes_path: Path,
    qwen_output_path: Path,
    audit_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    manifest_path = qwen_output_path.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing Qwen manifest: {manifest_path}")
    _require_non_promotable_qwen_manifest(manifest_path)
    questions = index_by_id(load_jsonl(bundle_dir / "review_items.jsonl"), "review items")
    routes = index_by_id(load_jsonl(routes_path), "staged routes")
    audits = index_by_id(load_jsonl(audit_path), "staged audits")
    finals = final_records(load_jsonl(qwen_output_path))
    missing_final = sorted(set(audits) - set(finals))
    missing_routes = sorted(set(audits) - set(routes))
    missing_questions = sorted(set(audits) - set(questions))
    if missing_final or missing_routes or missing_questions:
        raise ValueError(
            "Staged audit inputs are incomplete: "
            f"missing_final={missing_final}, missing_routes={missing_routes}, "
            f"missing_questions={missing_questions}"
        )
    rows = [
        _validated_row(
            audit=audit,
            final=finals[question_id],
            route=routes[question_id],
            question=questions[question_id],
        )
        for question_id, audit in sorted(audits.items())
    ]
    atomic_write_jsonl(output_path, rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "record_count": len(rows),
        "question_ids": [row["id"] for row in rows],
        "coverage_mode": "staged_machine_calibrated_nonproduction",
        "training_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "bundle_review_items_sha256": sha256_file(bundle_dir / "review_items.jsonl"),
        "routes_sha256": sha256_file(routes_path),
        "qwen_output_sha256": sha256_file(qwen_output_path),
        "qwen_manifest_sha256": sha256_file(manifest_path),
        "audit_sha256": sha256_file(audit_path),
        "sidecar_sha256": sha256_file(output_path),
    }
    manifest_output = output_path.with_suffix(".manifest.json")
    manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--routes", type=Path, required=True)
    parser.add_argument("--qwen-output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_ledger(
        bundle_dir=args.bundle_dir.resolve(),
        routes_path=args.routes.resolve(),
        qwen_output_path=args.qwen_output.resolve(),
        audit_path=args.audit.resolve(),
        output_path=args.output.resolve(),
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
