#!/usr/bin/env python3
"""Build a source-bound, non-materializable queue for release-gate blockers.

The queue assigns every currently blocked public question to exactly one
remediation lane.  It is an operational backlog, not an adjudication output:
it does not write a plan, bind a source, select a value, change provenance, or
make an answer eligible.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "production_release_remediation_queue_v1"
RELEASE_GATE_PROTOCOL = "production_release_gate_v1"
AUDIT_PROTOCOL = "production_independent_audit_v1"
TYPED_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"
QUERY_PROGRAM_PROTOCOL = "query_program_shadow_v1"
NON_PROMOTABLE_CONTRACT = {
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
LANE_DEFINITIONS = {
    "typed_plan_source_adjudication": {
        "primary_blocker_code": "TYPED_PLAN_ABSTAIN",
        "required_gate": "Create a typed source-bound plan or retain abstain; no question-ID-specific shortcut.",
    },
    "formula_evidence_completion": {
        "primary_blocker_code": "FORMULA_EVIDENCE_PARTIAL",
        "required_gate": "Complete every required operand with exact source rows/cells or retain partial.",
    },
    "deterministic_executor_compile": {
        "primary_blocker_code": "TYPED_EXECUTOR_NOT_PRODUCTION_COMPILED",
        "required_gate": "Compile and test a deterministic executor only after exact-source evidence is complete.",
    },
    "independent_source_replay": {
        "primary_blocker_code": "NO_INDEPENDENTLY_VALID_CANDIDATE",
        "required_gate": "Independently replay exact source evidence; do not use model confidence as a substitute.",
    },
    "formula_evidence_materialization": {
        "primary_blocker_code": "FORMULA_EVIDENCE_NOT_MATERIALIZED",
        "required_gate": "Materialize a Formula EvidenceSet from the typed contract and exact-source candidate set.",
    },
    "exact_source_conflict_adjudication": {
        "primary_blocker_code": "CONFLICTING_EXACT_SEMANTIC_SOURCE_VALUES",
        "required_gate": "Resolve only with a source-backed conflict adjudication; otherwise retain the block.",
    },
    "query_program_shadow_completion": {
        "primary_blocker_code": "QUERY_PROGRAM_NOT_SHADOW_COMPLETE",
        "required_gate": "Complete a source-bound QueryProgram shadow execution or retain the block.",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(row)
    return rows


def index(rows: list[dict[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if type(value) is not int:
            raise ValueError(f"{label} contains a non-integer {key}")
        if value in indexed:
            raise ValueError(f"{label} contains duplicate Q{value}")
        indexed[value] = row
    return indexed


def require_hash(path: Path, expected: object, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256_file(path)
    if not isinstance(expected, str) or expected != actual:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def artifact_input(path: Path, sha256: str) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256}


def release_artifact_hash(release_gate: Mapping[str, Any], name: str) -> str:
    artifact = (((release_gate.get("inputs") or {}).get(name) or {}).get("artifact") or {})
    value = artifact.get("sha256")
    if not isinstance(value, str):
        raise ValueError(f"Release gate lacks {name} artifact hash")
    return value


def validate_release_gate(path: Path) -> dict[str, Any]:
    gate = load_json(path)
    if (
        gate.get("protocol") != RELEASE_GATE_PROTOCOL
        or gate.get("release_status") != "blocked"
        or gate.get("production_eligible") is not False
        or gate.get("submission_compilation_allowed") is not False
        or gate.get("answer_materialization_allowed") is not False
        or (gate.get("source_contract") or {}) != NON_PROMOTABLE_CONTRACT
    ):
        raise ValueError("Release gate must be a blocked non-promotable record")
    codes = [item.get("code") for item in gate.get("blockers") or [] if isinstance(item, Mapping)]
    if "FULL_CORPUS_INDEPENDENT_AUDIT_INCOMPLETE" not in codes:
        raise ValueError("Release gate does not declare the independent-audit blocker")
    return gate


def validate_inputs(
    *,
    release_gate: Mapping[str, Any],
    audit_path: Path,
    typed_path: Path,
    formula_path: Path,
    query_program_path: Path,
) -> tuple[
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[str, Any],
]:
    audit_manifest = load_json(audit_path.with_suffix(".manifest.json"))
    audit_sha = require_hash(audit_path, audit_manifest.get("sidecar_sha256"), "production audit")
    if (
        audit_manifest.get("protocol") != AUDIT_PROTOCOL
        or audit_sha != release_artifact_hash(release_gate, "production_independent_audit")
    ):
        raise ValueError("Production audit is not bound to the blocked release gate")
    audit = index(load_jsonl(audit_path), "question_id", "production audit")
    if not audit or any(row.get("independent_audit_status") not in {"passed", "blocked"} for row in audit.values()):
        raise ValueError("Production audit rows are invalid")

    typed_manifest = load_json(typed_path.with_suffix(".manifest.json"))
    typed_sha = require_hash(typed_path, typed_manifest.get("sidecar_sha256"), "typed plans")
    if (
        typed_manifest.get("protocol") != TYPED_PROTOCOL
        or typed_sha != release_artifact_hash(release_gate, "typed_plans")
        or typed_manifest.get("review_items_sha256") != audit_manifest.get("review_items_sha256")
    ):
        raise ValueError("Typed plans are not bound to the release-gate audit lineage")
    typed = index(load_jsonl(typed_path), "question_id", "typed plans")
    if set(typed) != set(audit):
        raise ValueError("Typed plans must cover the production-audit corpus exactly")

    formula_manifest = load_json(formula_path.with_suffix(".manifest.json"))
    formula_sha = require_hash(formula_path, formula_manifest.get("sidecar_sha256"), "formula evidence")
    if (
        formula_sha != release_artifact_hash(release_gate, "formula_evidence")
        or formula_manifest.get("bundle_review_items_sha256") != audit_manifest.get("review_items_sha256")
    ):
        raise ValueError("Formula evidence is not bound to the release-gate audit lineage")
    formula = index(load_jsonl(formula_path), "id", "formula evidence")
    if not set(formula) <= set(audit):
        raise ValueError("Formula evidence references unknown production-audit questions")

    program_manifest = load_json(query_program_path.with_suffix(".manifest.json"))
    program_sha = require_hash(query_program_path, program_manifest.get("sidecar_sha256"), "query-program shadow")
    if (
        program_manifest.get("protocol") != QUERY_PROGRAM_PROTOCOL
        or program_sha != release_artifact_hash(release_gate, "query_program")
        or program_manifest.get("formula_evidence_sha256") != formula_sha
        or program_manifest.get("formula_evidence_manifest_sha256") != sha256_file(formula_path.with_suffix(".manifest.json"))
    ):
        raise ValueError("Query-program shadow is not bound to the release-gate formula lineage")
    programs = index(load_jsonl(query_program_path), "id", "query-program shadow")
    if not set(programs) <= set(audit):
        raise ValueError("Query-program shadow references unknown production-audit questions")
    return audit, typed, formula, programs, {
        "production_audit": artifact_input(audit_path, audit_sha),
        "production_audit_manifest": artifact_input(audit_path.with_suffix(".manifest.json"), sha256_file(audit_path.with_suffix(".manifest.json"))),
        "typed_plans": artifact_input(typed_path, typed_sha),
        "typed_plans_manifest": artifact_input(typed_path.with_suffix(".manifest.json"), sha256_file(typed_path.with_suffix(".manifest.json"))),
        "formula_evidence": artifact_input(formula_path, formula_sha),
        "formula_evidence_manifest": artifact_input(formula_path.with_suffix(".manifest.json"), sha256_file(formula_path.with_suffix(".manifest.json"))),
        "query_program": artifact_input(query_program_path, program_sha),
        "query_program_manifest": artifact_input(query_program_path.with_suffix(".manifest.json"), sha256_file(query_program_path.with_suffix(".manifest.json"))),
    }


def choose_lane(
    audit: Mapping[str, Any], typed: Mapping[str, Any], formula: Mapping[str, Any] | None
) -> str:
    reasons = set(audit.get("reason_codes") or [])
    typed_status = typed.get("decomposition_status")
    formula_status = None if formula is None else formula.get("evidence_completeness")
    if typed_status == "abstain":
        return "typed_plan_source_adjudication"
    if formula_status == "partial":
        return "formula_evidence_completion"
    if typed_status == "typed_non_executable":
        return "deterministic_executor_compile"
    if "no_independently_valid_candidate" in reasons:
        return "independent_source_replay"
    if "formula_evidence_not_materialized" in reasons:
        return "formula_evidence_materialization"
    if "conflicting_exact_semantic_source_values" in reasons:
        return "exact_source_conflict_adjudication"
    if "program_not_shadow_complete" in reasons:
        return "query_program_shadow_completion"
    raise ValueError(f"Q{audit.get('question_id')}: no fail-closed remediation lane")


def build(
    *,
    release_gate: Path,
    production_audit: Path,
    typed_plans: Path,
    formula_evidence: Path,
    query_program: Path,
    output: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Write a full, non-promotable queue for every currently blocked audit row."""
    if output.exists() or output.with_suffix(".manifest.json").exists():
        raise FileExistsError(f"Refusing to overwrite remediation queue: {output}")
    gate = validate_release_gate(release_gate)
    audit, typed, formulas, programs, inputs = validate_inputs(
        release_gate=gate,
        audit_path=production_audit,
        typed_path=typed_plans,
        formula_path=formula_evidence,
        query_program_path=query_program,
    )
    blocked_ids = {qid for qid, row in audit.items() if row.get("independent_audit_status") == "blocked"}
    if not blocked_ids:
        raise ValueError("A blocked release gate cannot have an empty independent-audit remediation queue")
    rows: list[dict[str, Any]] = []
    for qid in sorted(blocked_ids):
        audit_row = audit[qid]
        typed_row = typed[qid]
        formula_row = formulas.get(qid)
        program_row = programs.get(qid)
        lane = choose_lane(audit_row, typed_row, formula_row)
        definition = LANE_DEFINITIONS[lane]
        rows.append({
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": qid,
            "remediation_lane": lane,
            "primary_blocker_code": definition["primary_blocker_code"],
            "required_gate": definition["required_gate"],
            "audit": {
                "family": audit_row.get("family"),
                "status": "blocked",
                "reason_codes": sorted(str(reason) for reason in audit_row.get("reason_codes") or []),
            },
            "typed_plan": {
                "status": typed_row.get("decomposition_status"),
                "family": typed_row.get("effective_family"),
                "plan_fingerprint": typed_row.get("plan_fingerprint"),
            },
            "formula_evidence": None if formula_row is None else {
                "status": formula_row.get("evidence_completeness"),
                "formula_id": (formula_row.get("formula") or {}).get("formula_id"),
            },
            "query_program": None if program_row is None else {
                "readiness_status": (program_row.get("readiness") or {}).get("status"),
                "shadow_execution_status": None if program_row.get("shadow_execution") is None else (program_row.get("shadow_execution") or {}).get("status"),
            },
            "materialization_allowed": False,
            "source_contract": NON_PROMOTABLE_CONTRACT,
        })
    counts = Counter(row["remediation_lane"] for row in rows)
    if len({row["question_id"] for row in rows}) != len(rows) or {row["question_id"] for row in rows} != blocked_ids:
        raise ValueError("Remediation queue must assign every blocked question exactly once")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "queue_status": "non_materializable",
        "question_count": len(rows),
        "lane_counts": dict(sorted(counts.items())),
        "inputs": {
            "release_gate": artifact_input(release_gate, sha256_file(release_gate)),
            **inputs,
        },
        "outputs": {"queue": artifact_input(output, sha256_file(output))},
        "source_contract": NON_PROMOTABLE_CONTRACT,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return rows, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-gate", type=Path, required=True)
    parser.add_argument("--production-audit", type=Path, required=True)
    parser.add_argument("--typed-plans", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
    parser.add_argument("--query-program", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _, manifest = build(**vars(args))
    print(json.dumps(manifest["lane_counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
