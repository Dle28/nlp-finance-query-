#!/usr/bin/env python3
"""Build a full-corpus, fail-closed ViFinQA production release gate.

This gate is deliberately a *read-only decision record*.  It joins the
full-corpus independent audit, typed plans, formula evidence, query-program
shadow runs, fingerprint canaries, the V5 critic/route checkpoint, and (only
when supplied) a production execution ledger.  It never writes answers,
changes provenance, or materializes a submission.  A later compiler may run
only when this gate says ``ready_for_submission_compiler``.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "production_release_gate_v1"
AUDIT_PROTOCOL = "production_independent_audit_v1"
TYPED_PROTOCOL = "typed_operand_decomposition_fail_closed_v1"
QUERY_PROGRAM_PROTOCOL = "query_program_shadow_v1"
CANARY_PROTOCOL = "fingerprint_exact_binding_canary_v1"
V5_PROTOCOL = "production_coverage_v5_readiness_v1"
LEDGER_PROTOCOL = "production_execution_lineage_v1"
SHA256_LENGTH = 64


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
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(value)
    return rows


def manifest_path_for(sidecar: Path) -> Path:
    return sidecar.with_suffix(".manifest.json")


def index_ids(
    rows: Iterable[Mapping[str, Any]], *, key: str, label: str, expected_ids: set[int] | None = None
) -> dict[int, Mapping[str, Any]]:
    indexed: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        raw_id = row.get(key)
        if type(raw_id) is not int:
            raise ValueError(f"{label} contains a non-integer {key}")
        if raw_id in indexed:
            raise ValueError(f"{label} contains duplicate question ID Q{raw_id}")
        indexed[raw_id] = row
    if expected_ids is not None and set(indexed) != expected_ids:
        raise ValueError(f"{label} must cover exactly the public question IDs")
    return indexed


def require_file_hash(path: Path, expected: object, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def require_false_flags(manifest: Mapping[str, Any], flags: tuple[str, ...], label: str) -> None:
    if any(manifest.get(flag) is not False for flag in flags):
        raise ValueError(f"{label} violates its non-promotable source contract")


def input_record(path: Path, sha256: str) -> dict[str, str]:
    return {"path": str(path), "sha256": sha256}


def validate_v5_readiness(path: Path) -> dict[str, Any]:
    readiness = load_json(path)
    if (
        readiness.get("protocol") != V5_PROTOCOL
        or readiness.get("readiness_status") != "blocked"
        or readiness.get("production_eligible") is not False
        or readiness.get("submission_eligible") is not False
        or readiness.get("promotion_allowed") is not False
    ):
        raise ValueError("V5 readiness checkpoint must remain explicitly blocked")
    source_contract = readiness.get("source_contract") or {}
    require_false_flags(
        source_contract,
        ("evidence_eligible", "training_eligible", "submission_eligible", "promotion_allowed"),
        "V5 readiness checkpoint",
    )
    blockers = readiness.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        raise ValueError("V5 readiness checkpoint must name its blockers")
    codes = [item.get("code") for item in blockers if isinstance(item, Mapping)]
    if any(not isinstance(code, str) or not code for code in codes):
        raise ValueError("V5 readiness checkpoint contains an invalid blocker")
    return {
        **input_record(path, sha256_file(path)),
        "blocker_codes": codes,
        "counts": readiness.get("counts"),
    }


def validate_typed_plans(path: Path, question_ids: set[int], review_items_sha: str) -> dict[str, Any]:
    manifest_path = manifest_path_for(path)
    manifest = load_json(manifest_path)
    sidecar_sha = require_file_hash(path, manifest.get("sidecar_sha256"), "typed plans")
    if (
        manifest.get("protocol") != TYPED_PROTOCOL
        or manifest.get("review_items_sha256") != review_items_sha
        or manifest.get("question_count") != len(question_ids)
    ):
        raise ValueError("Typed-plan manifest lineage or coverage is invalid")
    require_false_flags(
        manifest,
        ("answer_eligible", "training_eligible", "submission_eligible", "provenance_promotion_allowed"),
        "typed plans",
    )
    rows = index_ids(load_jsonl(path), key="question_id", label="typed plans", expected_ids=question_ids)
    statuses = Counter(str(row.get("decomposition_status") or "") for row in rows.values())
    allowed = {"complete", "abstain", "typed_non_executable"}
    if set(statuses) - allowed or manifest.get("status_counts") != dict(sorted(statuses.items())):
        raise ValueError("Typed-plan status counts are invalid")
    incomplete = {key: statuses.get(key, 0) for key in ("abstain", "typed_non_executable")}
    return {
        "artifact": input_record(path, sidecar_sha),
        "manifest": input_record(manifest_path, sha256_file(manifest_path)),
        "status_counts": dict(sorted(statuses.items())),
        "incomplete_count": sum(incomplete.values()),
        "incomplete_counts": incomplete,
    }


def validate_formula_evidence(path: Path, question_ids: set[int], review_items_sha: str) -> dict[str, Any]:
    manifest_path = manifest_path_for(path)
    manifest = load_json(manifest_path)
    sidecar_sha = require_file_hash(path, manifest.get("sidecar_sha256"), "formula evidence")
    if manifest.get("bundle_review_items_sha256") != review_items_sha:
        raise ValueError("Formula-evidence review-item lineage is invalid")
    rows = index_ids(load_jsonl(path), key="id", label="formula evidence")
    if not set(rows) <= question_ids or manifest.get("evidence_set_count") != len(rows):
        raise ValueError("Formula-evidence coverage is invalid")
    completeness = Counter(str(row.get("evidence_completeness") or "") for row in rows.values())
    if set(completeness) - {"complete", "partial"} or manifest.get("completeness_counts") != dict(sorted(completeness.items())):
        raise ValueError("Formula-evidence completeness counts are invalid")
    return {
        "artifact": input_record(path, sidecar_sha),
        "manifest": input_record(manifest_path, sha256_file(manifest_path)),
        "completeness_counts": dict(sorted(completeness.items())),
        "partial_count": completeness.get("partial", 0),
    }


def validate_query_program(
    path: Path,
    question_ids: set[int],
    formula: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_path = manifest_path_for(path)
    manifest = load_json(manifest_path)
    sidecar_sha = require_file_hash(path, manifest.get("sidecar_sha256"), "query-program shadow")
    if (
        manifest.get("protocol") != QUERY_PROGRAM_PROTOCOL
        or manifest.get("formula_evidence_sha256") != formula["artifact"]["sha256"]
        or manifest.get("formula_evidence_manifest_sha256") != formula["manifest"]["sha256"]
        or manifest.get("submission_eligible") is not False
    ):
        raise ValueError("Query-program shadow lineage is invalid")
    rows = index_ids(load_jsonl(path), key="id", label="query-program shadow")
    if not set(rows) <= question_ids:
        raise ValueError("Query-program shadow references an unknown question")
    execution = Counter(
        "not_run"
        if row.get("shadow_execution") is None
        else str((row.get("shadow_execution") or {}).get("status") or "")
        for row in rows.values()
    )
    if set(execution) - {"not_run", "shadow_complete"} or manifest.get("shadow_execution_counts") != dict(sorted(execution.items())):
        raise ValueError("Query-program shadow execution counts are invalid")
    return {
        "artifact": input_record(path, sidecar_sha),
        "manifest": input_record(manifest_path, sha256_file(manifest_path)),
        "shadow_execution_counts": dict(sorted(execution.items())),
        "incomplete_count": sum(value for key, value in execution.items() if key != "shadow_complete"),
    }


def validate_canaries(
    path: Path,
    question_ids: set[int],
    review_items_sha: str,
    typed: Mapping[str, Any],
    formula: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_path = manifest_path_for(path)
    manifest = load_json(manifest_path)
    sidecar_sha = require_file_hash(path, manifest.get("sidecar_sha256"), "fingerprint canaries")
    if (
        manifest.get("protocol") != CANARY_PROTOCOL
        or manifest.get("question_count") != len(question_ids)
        or manifest.get("bundle_review_items_sha256") != review_items_sha
        or manifest.get("typed_operand_plans_sha256") != typed["artifact"]["sha256"]
        or manifest.get("typed_operand_plans_manifest_sha256") != typed["manifest"]["sha256"]
        or manifest.get("formula_evidence_sha256") != formula["artifact"]["sha256"]
        or manifest.get("formula_evidence_manifest_sha256") != formula["manifest"]["sha256"]
        or manifest.get("selected_fingerprint_count") is None
    ):
        raise ValueError("Fingerprint-canary lineage is invalid")
    require_false_flags(
        manifest,
        ("answer_eligible", "training_eligible", "submission_eligible", "provenance_promotion_allowed"),
        "fingerprint canaries",
    )
    rows = index_ids(load_jsonl(path), key="question_id", label="fingerprint canaries")
    if not set(rows) <= question_ids or manifest.get("selected_fingerprint_count") != len(rows):
        raise ValueError("Fingerprint-canary coverage is invalid")
    verdicts = Counter(str(row.get("verdict") or "") for row in rows.values())
    if set(verdicts) - {"exact_bound", "explicit_block"} or manifest.get("verdict_counts") != dict(sorted(verdicts.items())):
        raise ValueError("Fingerprint-canary verdict counts are invalid")
    return {
        "artifact": input_record(path, sidecar_sha),
        "manifest": input_record(manifest_path, sha256_file(manifest_path)),
        "verdict_counts": dict(sorted(verdicts.items())),
        "blocked_count": verdicts.get("explicit_block", 0),
    }


def validate_audit(
    path: Path,
    question_ids: set[int],
    review_items_sha: str,
    typed: Mapping[str, Any],
    formula: Mapping[str, Any],
    query_program: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_path = manifest_path_for(path)
    manifest = load_json(manifest_path)
    sidecar_sha = require_file_hash(path, manifest.get("sidecar_sha256"), "production independent audit")
    if (
        manifest.get("protocol") != AUDIT_PROTOCOL
        or manifest.get("question_count") != len(question_ids)
        or manifest.get("review_items_sha256") != review_items_sha
        or manifest.get("typed_operand_plans_sha256") != typed["artifact"]["sha256"]
        or manifest.get("formula_evidence_sha256") != formula["artifact"]["sha256"]
        or manifest.get("query_program_sha256") != query_program["artifact"]["sha256"]
    ):
        raise ValueError("Production independent-audit lineage is invalid")
    require_false_flags(
        manifest,
        ("answer_eligible", "training_eligible", "provenance_promotion_allowed"),
        "production independent audit",
    )
    rows = index_ids(load_jsonl(path), key="question_id", label="production independent audit", expected_ids=question_ids)
    statuses = Counter(str(row.get("independent_audit_status") or "") for row in rows.values())
    if set(statuses) - {"passed", "blocked"} or manifest.get("status_counts") != dict(sorted(statuses.items())):
        raise ValueError("Production independent-audit status counts are invalid")
    if any(row.get("production_eligible") is not (row.get("independent_audit_status") == "passed") for row in rows.values()):
        raise ValueError("Production independent-audit row eligibility is inconsistent")
    approved = statuses.get("passed", 0) == len(question_ids)
    if (
        manifest.get("audit_status") != ("passed" if approved else "partial")
        or manifest.get("production_eligibility_approved") is not approved
    ):
        raise ValueError("Production independent-audit approval state is inconsistent")
    return {
        "artifact": input_record(path, sidecar_sha),
        "manifest": input_record(manifest_path, sha256_file(manifest_path)),
        "status_counts": dict(sorted(statuses.items())),
        "blocked_count": statuses.get("blocked", 0),
        "approved": approved,
    }


def validate_ledger(
    ledger: Path | None,
    manifest_path: Path | None,
    question_ids: set[int],
    expected_lineage: Mapping[str, str],
) -> dict[str, Any] | None:
    if ledger is None and manifest_path is None:
        return None
    if ledger is None or manifest_path is None:
        raise ValueError("Production execution ledger and manifest must be supplied together")
    manifest = load_json(manifest_path)
    sidecar_sha = require_file_hash(ledger, manifest.get("sidecar_sha256"), "production execution ledger")
    if (
        manifest.get("protocol") != LEDGER_PROTOCOL
        or manifest.get("production_eligible") is not True
        or manifest.get("lineage") != dict(expected_lineage)
    ):
        raise ValueError("Production execution-ledger manifest is invalid")
    rows = index_ids(load_jsonl(ledger), key="id", label="production execution ledger", expected_ids=question_ids)
    for qid, row in rows.items():
        grounding = "staged_exact_cells_replayed" if row.get("execution_mode") == "exact_staged_contract" else "exact_rows_validated"
        eligibility = row.get("production_eligibility") or {}
        if (
            row.get("execution_status") != "grounded"
            or row.get("grounding_status") != grounding
            or row.get("submission_eligible") is not True
            or row.get("provenance_status") not in {"human_verified", "machine_calibrated"}
            or eligibility.get("protocol") != LEDGER_PROTOCOL
            or eligibility.get("status") != "approved"
            or eligibility.get("independent_audit_status") != "passed"
            or row.get("artifact_lineage") != dict(expected_lineage)
        ):
            raise ValueError(f"Production execution ledger row Q{qid} is not eligible")
    return {
        "artifact": input_record(ledger, sidecar_sha),
        "manifest": input_record(manifest_path, sha256_file(manifest_path)),
        "record_count": len(rows),
    }


def build(
    *,
    bundle_dir: Path,
    production_audit: Path,
    typed_plans: Path,
    formula_evidence: Path,
    query_program: Path,
    fingerprint_canaries: Path,
    v5_readiness: Path,
    output: Path,
    execution_ledger: Path | None = None,
    execution_ledger_manifest: Path | None = None,
) -> dict[str, Any]:
    """Validate the complete release lineage and write one immutable decision record."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite release-gate record: {output}")
    review_items = bundle_dir / "review_items.jsonl"
    if not review_items.is_file():
        raise FileNotFoundError(f"Missing bundle review items: {review_items}")
    item_rows = load_jsonl(review_items)
    question_ids = set(index_ids(item_rows, key="id", label="bundle review items"))
    if not question_ids:
        raise ValueError("Bundle review items must not be empty")
    review_items_sha = sha256_file(review_items)

    typed = validate_typed_plans(typed_plans, question_ids, review_items_sha)
    formula = validate_formula_evidence(formula_evidence, question_ids, review_items_sha)
    query_program_info = validate_query_program(query_program, question_ids, formula)
    canaries = validate_canaries(fingerprint_canaries, question_ids, review_items_sha, typed, formula)
    audit = validate_audit(
        production_audit, question_ids, review_items_sha, typed, formula, query_program_info
    )
    v5 = validate_v5_readiness(v5_readiness)
    expected_lineage = {
        "review_items_sha256": review_items_sha,
        "raw_tables_sha256": sha256_file(bundle_dir / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(bundle_dir / "tables_structured_v2.jsonl"),
        "evidence_context_sha256": sha256_file(bundle_dir / "tables_evidence_context_v3.jsonl"),
        "typed_operand_plans_sha256": typed["artifact"]["sha256"],
        "independent_audit_sha256": audit["artifact"]["sha256"],
    }
    ledger = validate_ledger(execution_ledger, execution_ledger_manifest, question_ids, expected_lineage)

    # V5 is a deliberately non-promotable *research* checkpoint.  It remains
    # part of release lineage and continues to block an incomplete full-corpus
    # release, but it must not make a fully re-audited and source-grounded
    # 1,012-ID production ledger permanently unreachable.  In that terminal
    # case the full independent audit plus ledger are the stronger authority;
    # neither reuses nor promotes a V5 critic/route result.
    v5_component_blocks_release = audit["blocked_count"] > 0 or ledger is None
    blockers: list[dict[str, Any]] = []
    if v5_component_blocks_release:
        blockers.append(
            {
                "code": "COMPONENT_V5_READINESS_BLOCKED",
                "count": len(v5["blocker_codes"]),
                "detail": "V5 source-bound critic/route checkpoint is still blocked while the full-corpus audit or ledger is incomplete.",
                "upstream_blocker_codes": v5["blocker_codes"],
            }
        )
    if audit["blocked_count"]:
        blockers.append({
            "code": "FULL_CORPUS_INDEPENDENT_AUDIT_INCOMPLETE",
            "count": audit["blocked_count"],
            "detail": "Every public question must pass the independent audit before compilation.",
        })
    if typed["incomplete_count"]:
        blockers.append({
            "code": "TYPED_PLAN_COVERAGE_INCOMPLETE",
            "count": typed["incomplete_count"],
            "detail": "Abstained and non-executable typed plans cannot enter deterministic production execution.",
            "status_counts": typed["incomplete_counts"],
        })
    if formula["partial_count"]:
        blockers.append({
            "code": "FORMULA_EVIDENCE_PARTIAL",
            "count": formula["partial_count"],
            "detail": "Partial Formula EvidenceSets cannot be converted to answers by confidence or fallback logic.",
        })
    if query_program_info["incomplete_count"]:
        blockers.append({
            "code": "QUERY_PROGRAM_SHADOW_INCOMPLETE",
            "count": query_program_info["incomplete_count"],
            "detail": "Non-complete query-program shadow executions remain non-production artifacts.",
        })
    if canaries["blocked_count"]:
        blockers.append({
            "code": "FINGERPRINT_CANARY_EXACT_BINDING_INCOMPLETE",
            "count": canaries["blocked_count"],
            "detail": "Canaries without an exact binding proof remain explicit blocks.",
        })
    if ledger is None:
        blockers.append({
            "code": "PRODUCTION_EXECUTION_LEDGER_MISSING",
            "count": len(question_ids),
            "detail": "No full-corpus production_execution_lineage_v1 ledger was supplied.",
        })

    ready = not blockers
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "release_status": "ready_for_submission_compiler" if ready else "blocked",
        "production_eligible": ready,
        "submission_compilation_allowed": ready,
        "submission_eligible": False,
        "answer_materialization_allowed": False,
        "blockers": blockers,
        "counts": {
            "public_question_count": len(question_ids),
            "independent_audit_passed_count": audit["status_counts"].get("passed", 0),
            "independent_audit_blocked_count": audit["blocked_count"],
            "typed_plan_incomplete_count": typed["incomplete_count"],
            "formula_evidence_partial_count": formula["partial_count"],
            "query_program_shadow_incomplete_count": query_program_info["incomplete_count"],
            "fingerprint_canary_blocked_count": canaries["blocked_count"],
            "production_execution_ledger_count": None if ledger is None else ledger["record_count"],
            "v5_component_blocking": v5_component_blocks_release,
        },
        "inputs": {
            "bundle_review_items": input_record(review_items, review_items_sha),
            "typed_plans": typed,
            "formula_evidence": formula,
            "query_program": query_program_info,
            "fingerprint_canaries": canaries,
            "production_independent_audit": audit,
            "v5_readiness": v5,
            "production_execution_ledger": ledger,
        },
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--production-audit", type=Path, required=True)
    parser.add_argument("--typed-plans", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
    parser.add_argument("--query-program", type=Path, required=True)
    parser.add_argument("--fingerprint-canaries", type=Path, required=True)
    parser.add_argument("--v5-readiness", type=Path, required=True)
    parser.add_argument("--execution-ledger", type=Path)
    parser.add_argument("--execution-ledger-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build(**vars(args))
    print(result["release_status"])


if __name__ == "__main__":
    main()
