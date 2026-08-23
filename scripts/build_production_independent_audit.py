#!/usr/bin/env python3
"""Materialize a full-corpus, reviewer-independent production-audit candidate.

This is intentionally stricter than a reviewer or a shadow executor.  It
describes which question has independently revalidated source evidence, and
which contract blocks it.  The manifest is marked production-approved only if
every public question passes; it never promotes provenance or writes answers.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from analyze_formula_evidence import validate_manifest as validate_formula_manifest  # noqa: E402
from finance_query.independent_critic import INDEPENDENT_CRITIC_PROTOCOL  # noqa: E402


PRODUCTION_AUDIT_SCHEMA_VERSION = 1
PRODUCTION_AUDIT_PROTOCOL = "production_independent_audit_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def validate_typed_plans(bundle: Path, path: Path) -> dict[int, dict[str, Any]]:
    manifest_path = path.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError("Typed-plan manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") != "typed_operand_decomposition_fail_closed_v1"
        or manifest.get("review_items_sha256") != sha256_file(bundle / "review_items.jsonl")
        or manifest.get("sidecar_sha256") != sha256_file(path)
    ):
        raise ValueError("Typed-plan lineage is invalid")
    rows = load_jsonl(path)
    output = {int(row["question_id"]): row for row in rows}
    if len(output) != len(rows):
        raise ValueError("Typed-plan artifact contains duplicate question ids")
    return output


def validate_critic(bundle: Path, path: Path) -> dict[int, dict[str, Any]]:
    manifest_path = path.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError("Independent-critic manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "bundle_review_items_sha256": bundle / "review_items.jsonl",
        "raw_tables_sha256": bundle / "tables.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
        "evidence_context_sha256": bundle / "tables_evidence_context_v3.jsonl",
    }
    if (
        manifest.get("protocol") != INDEPENDENT_CRITIC_PROTOCOL
        or manifest.get("machine_reviews_sha256") is not None
        or manifest.get("reviewer_inputs_used") != []
        or manifest.get("sidecar_sha256") != sha256_file(path)
        or any(manifest.get(key) != sha256_file(value) for key, value in expected.items())
    ):
        raise ValueError("Independent critic is not a valid reviewer-independent source audit")
    rows = load_jsonl(path)
    output = {int(row["question_id"]): row for row in rows}
    if len(output) != len(rows):
        raise ValueError("Independent critic contains duplicate question ids")
    return output


def _index(rows: list[dict[str, Any]], key: str, name: str) -> dict[int, dict[str, Any]]:
    output = {int(row[key]): row for row in rows}
    if len(output) != len(rows):
        raise ValueError(f"{name} contains duplicate ids")
    return output


def audit_question(
    item: dict[str, Any],
    typed_plan: dict[str, Any],
    formula: dict[str, Any] | None,
    critic: dict[str, Any] | None,
    program: dict[str, Any] | None,
) -> dict[str, Any]:
    qid = int(item["id"])
    family = str(typed_plan.get("effective_family") or "unknown")
    typed_status = str(typed_plan.get("decomposition_status") or "abstain")
    formula_status = str((formula or {}).get("evidence_completeness") or "not_applicable")
    critic_status = str((critic or {}).get("status") or "not_applicable")
    program_status = str(((program or {}).get("shadow_execution") or {}).get("status") or "not_run")
    reasons: list[str] = []

    if typed_status == "abstain":
        reasons.extend(str(value) for value in typed_plan.get("reason_codes") or ["typed_plan_abstain"])
    elif family == "direct_lookup":
        if critic_status != "independent_ready":
            reasons.extend(str(value) for value in (critic or {}).get("reason_codes") or ["independent_critic_not_ready"])
    else:
        if formula is None:
            reasons.append("formula_evidence_not_materialized")
        elif formula_status != "complete":
            reasons.extend(str(value) for value in formula.get("reason_codes") or ["formula_evidence_partial"])
            reasons.extend(f"missing_operand:{value}" for value in formula.get("missing_operand_ids") or [])
        if typed_status == "typed_non_executable":
            reasons.append("typed_executor_not_production_compiled")
        if program is not None and program_status != "shadow_complete":
            reasons.extend(
                str(value)
                for value in ((program.get("shadow_execution") or {}).get("reason_codes") or ["program_not_shadow_complete"])
            )

    # A passing source audit is deliberately not an answer or a provenance
    # promotion. Production becomes globally eligible only if every Q passes.
    passed = not reasons
    return {
        "question_id": qid,
        "family": family,
        "typed_plan_status": typed_status,
        "formula_evidence_status": formula_status,
        "independent_critic_status": critic_status,
        "query_program_status": program_status,
        "independent_audit_status": "passed" if passed else "blocked",
        "production_eligible": passed,
        "reason_codes": sorted(set(reasons)),
        "answer_eligible": False,
        "training_eligible": False,
        "provenance_promotion_allowed": False,
    }


def build(
    bundle: Path,
    typed_path: Path,
    formula_path: Path,
    critic_path: Path,
    program_path: Path,
    output: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = load_jsonl(bundle / "review_items.jsonl")
    item_by_id = _index(items, "id", "review_items")
    typed = validate_typed_plans(bundle, typed_path)
    if set(typed) != set(item_by_id):
        raise ValueError("Typed plans must cover exactly review_items")
    validate_formula_manifest(bundle, formula_path)
    formulas = _index(load_jsonl(formula_path), "id", "Formula EvidenceSet")
    critics = validate_critic(bundle, critic_path)
    program_manifest_path = program_path.with_suffix(".manifest.json")
    program_manifest = json.loads(program_manifest_path.read_text(encoding="utf-8"))
    if (
        program_manifest.get("formula_evidence_sha256") != sha256_file(formula_path)
        or program_manifest.get("sidecar_sha256") != sha256_file(program_path)
        or program_manifest.get("submission_eligible") is not False
    ):
        raise ValueError("QueryProgram shadow lineage is invalid")
    programs = _index(load_jsonl(program_path), "id", "QueryProgram")
    rows = [
        audit_question(item, typed[int(item["id"])], formulas.get(int(item["id"])), critics.get(int(item["id"])), programs.get(int(item["id"])))
        for item in items
    ]
    write_jsonl(output, rows)
    counts = Counter(row["independent_audit_status"] for row in rows)
    approved = counts.get("passed", 0) == len(rows)
    manifest = {
        "schema_version": PRODUCTION_AUDIT_SCHEMA_VERSION,
        "protocol": PRODUCTION_AUDIT_PROTOCOL,
        "question_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "audit_status": "passed" if approved else "partial",
        "production_eligibility_approved": approved,
        "reviewer_inputs_used": [],
        "review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "typed_operand_plans_sha256": sha256_file(typed_path),
        "formula_evidence_sha256": sha256_file(formula_path),
        "independent_critic_sha256": sha256_file(critic_path),
        "query_program_sha256": sha256_file(program_path),
        "answer_eligible": False,
        "training_eligible": False,
        "provenance_promotion_allowed": False,
        "sidecar_sha256": sha256_file(output),
    }
    write_json(output.with_suffix(".manifest.json"), manifest)
    return rows, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--typed-plans", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
    parser.add_argument("--independent-critic", type=Path, required=True)
    parser.add_argument("--query-program", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, manifest = build(
        args.bundle_dir.resolve(),
        args.typed_plans.resolve(),
        args.formula_evidence.resolve(),
        args.independent_critic.resolve(),
        args.query_program.resolve(),
        args.output.resolve(),
    )
    print(json.dumps({"rows": len(rows), **manifest["status_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
