#!/usr/bin/env python3
"""Build deterministic, fail-closed regression canaries by question fingerprint.

This is an audit sidecar, never a retrieval, label, training or submission
input.  Each selected fingerprint has one deterministic representative (the
lowest question id).  The representative is marked ``exact_bound`` only after
its stored Formula EvidenceSet operands are re-read from V2 and V3, or a direct
EvidenceSet is independently replayed against V2/V3.  Every other result is an
explicit block with machine-readable reasons.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT / "src", ROOT / "scripts"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from analyze_formula_evidence import (  # noqa: E402
    evidence_context_path,
    source_completion_paths,
    validate_manifest as validate_formula_manifest,
    validate_operand_matches,
)
from build_execution_ledger import load_evidence_contexts, load_v2_tables  # noqa: E402
from finance_query.artifact_registry import sha256_file  # noqa: E402
from finance_query.independent_critic import critique_direct_evidence  # noqa: E402
from finance_query.plan_overrides import apply_plan_overrides, validate_plan_overrides  # noqa: E402
from finance_query.typed_planner import TYPED_OPERAND_PLAN_PROTOCOL  # noqa: E402


CANARY_SCHEMA_VERSION = 1
CANARY_PROTOCOL = "fingerprint_exact_binding_canary_v1"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write(path: Path, payload: Any, *, jsonl: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        if jsonl:
            for row in payload:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        else:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _validate_sidecar(
    bundle: Path,
    sidecar: Path,
    *,
    protocol: str | None = None,
) -> dict[str, Any]:
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Canary input manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if protocol is not None and str(manifest.get("protocol") or "") != protocol:
        raise ValueError(f"Unexpected canary input protocol for {sidecar.name}")
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError(f"Canary input sidecar hash mismatch: {sidecar.name}")
    review_hash = manifest.get("bundle_review_items_sha256", manifest.get("review_items_sha256"))
    if review_hash is not None and str(review_hash) != sha256_file(bundle / "review_items.jsonl"):
        raise ValueError(f"Canary input review-item lineage mismatch: {sidecar.name}")
    return manifest


def _direct_manifest(bundle: Path, sidecar: Path) -> dict[str, Any]:
    manifest = _validate_sidecar(bundle, sidecar)
    # ``raw_tables_sha256`` is the established field in the direct-replay
    # protocol.  Early canary fixtures used ``bundle_tables_sha256`` instead;
    # accept that spelling only as an exact alias, never as missing lineage.
    required = (
        (("raw_tables_sha256", "bundle_tables_sha256"), bundle / "tables.jsonl"),
        (("structured_tables_sha256",), bundle / "tables_structured_v2.jsonl"),
    )
    for aliases, path in required:
        recorded = next((manifest.get(key) for key in aliases if manifest.get(key) is not None), None)
        if str(recorded or "") != sha256_file(path):
            raise ValueError(f"Direct canary input lineage mismatch: {aliases[0]}")
    return manifest


def _proof_from_formula(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    proof: list[dict[str, Any]] = []
    for operand_id, matches in sorted((row.get("selected_operand_matches") or {}).items()):
        for match in matches or []:
            binding = match.get("binding") or {}
            proof.append(
                {
                    "operand_id": str(operand_id),
                    "internal_table_uid": str(match.get("internal_table_uid") or ""),
                    "row_index": binding.get("row_index"),
                    "column_index": binding.get("column_index"),
                    "raw_value": binding.get("raw_value"),
                    "canonical_header": binding.get("column_label"),
                    "entity": match.get("ticker"),
                    "year": match.get("report_year"),
                    "scope": match.get("scope"),
                    "unit": match.get("source_unit"),
                }
            )
    return proof


def _formula_canary(
    formula: Mapping[str, Any],
    bundle: Path,
    context_path: Path,
    completion_tables: Path | None,
    completion_context: Path | None,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    # validate_operand_matches re-reads the raw V2 value and V3 source header.
    try:
        checked = validate_operand_matches(
            [dict(formula)],
            bundle,
            context_path,
            completion_tables,
            completion_context,
        )
    except ValueError as error:
        return "explicit_block", ["formula_binding_revalidation_failed", str(error)], []
    if str(formula.get("evidence_completeness") or "") != "complete":
        reasons = ["formula_evidence_not_complete"] + [
            str(reason) for reason in formula.get("reason_codes") or []
        ]
        return "explicit_block", sorted(set(reasons)), _proof_from_formula(formula)
    if formula.get("missing_operand_ids"):
        return "explicit_block", ["formula_operand_missing"], _proof_from_formula(formula)
    if checked <= 0:
        return "explicit_block", ["formula_has_no_selected_exact_operand"], []
    return "exact_bound", ["formula_operands_revalidated_against_v2_v3"], _proof_from_formula(formula)


def _direct_canary(
    evidence: Mapping[str, Any],
    plan: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    replay = critique_direct_evidence(evidence, plan, tables, contexts)
    if replay.get("status") != "independent_ready":
        reasons = [str(reason) for reason in replay.get("reason_codes") or []]
        reasons.extend(sorted(str(key) for key in (replay.get("rejection_counts") or {})))
        return "explicit_block", sorted(set(reasons)), []
    proof = [
        {
            "internal_table_uid": row.get("internal_table_uid"),
            "row_index": row.get("row_index"),
            "column_index": row.get("column_index"),
            "raw_value": row.get("raw_value"),
            "canonical_header": row.get("canonical_header"),
            "unit": row.get("source_unit"),
        }
        for row in replay.get("valid_candidates") or []
    ]
    return "exact_bound", ["direct_evidence_independently_replayed_against_v2_v3"], proof


def build_canaries(
    bundle: Path,
    census_path: Path,
    typed_path: Path,
    output: Path,
    *,
    formula_path: Path | None = None,
    direct_path: Path | None = None,
    context_path: Path | None = None,
    question_plan_overrides: Path | None = None,
    min_question_count: int = 5,
    max_fingerprints: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if min_question_count < 1 or max_fingerprints < 1:
        raise ValueError("min_question_count and max_fingerprints must be positive")
    _validate_sidecar(bundle, census_path, protocol="deterministic_question_plan_fingerprint_v1")
    _validate_sidecar(bundle, typed_path, protocol=TYPED_OPERAND_PLAN_PROTOCOL)
    census = _load_jsonl(census_path)
    typed = {int(row["question_id"]): row for row in _load_jsonl(typed_path)}
    items = {int(row["id"]): row for row in _load_jsonl(bundle / "review_items.jsonl")}
    if set(typed) != set(items):
        raise ValueError("Typed canary input must cover exactly the bundle review questions")
    if {int(row["question_id"]) for row in census} != set(items):
        raise ValueError("Fingerprint census must cover exactly the bundle review questions")

    formula_by_id: dict[int, dict[str, Any]] = {}
    formula_manifest: dict[str, Any] | None = None
    formula_context: Path | None = None
    completion_tables = completion_context = None
    if formula_path is not None:
        formula_manifest = validate_formula_manifest(bundle, formula_path)
        formula_by_id = {int(row["id"]): row for row in _load_jsonl(formula_path)}
        formula_context = evidence_context_path(bundle, formula_manifest)
        completion = formula_manifest.get("source_completion") or {}
        if bool(completion.get("enabled")):
            completion_tables, completion_context = source_completion_paths(bundle, formula_manifest)

    direct_by_id: dict[int, dict[str, Any]] = {}
    v2_tables: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    direct_manifest: dict[str, Any] | None = None
    if direct_path is not None:
        direct_manifest = _direct_manifest(bundle, direct_path)
        direct_by_id = {int(row["id"]): row for row in _load_jsonl(direct_path)}
        resolved_context = context_path or bundle / "tables_evidence_context_v3.jsonl"
        if not resolved_context.is_file():
            raise FileNotFoundError(f"Direct canary V3 context missing: {resolved_context}")
        v2_tables = load_v2_tables(bundle)
        contexts = load_evidence_contexts(bundle, resolved_context)
        override_name = str(direct_manifest.get("question_plan_override_file") or "")
        if override_name:
            override_path = question_plan_overrides or (bundle / override_name)
            if override_path.name != override_name:
                raise ValueError("Direct canary override filename differs from direct-evidence lineage")
            if str(direct_manifest.get("question_plan_overrides_sha256") or "") != sha256_file(override_path):
                raise ValueError("Direct canary override lineage mismatch")
            overrides = validate_plan_overrides(list(items.values()), _load_jsonl(override_path))
            items = {
                int(row["id"]): row
                for row in apply_plan_overrides(list(items.values()), overrides)
            }

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in census:
        grouped[str(row["fingerprint"])].append(row)
    selected = sorted(
        ((fingerprint, rows) for fingerprint, rows in grouped.items() if len(rows) >= min_question_count),
        key=lambda pair: (-len(pair[1]), pair[0]),
    )[:max_fingerprints]

    rows: list[dict[str, Any]] = []
    for fingerprint, members in selected:
        member = min(members, key=lambda row: int(row["question_id"]))
        qid = int(member["question_id"])
        typed_row = typed[qid]
        verdict, reasons, proof = "explicit_block", [], []
        if qid in formula_by_id and formula_context is not None:
            verdict, reasons, proof = _formula_canary(
                formula_by_id[qid], bundle, formula_context, completion_tables, completion_context
            )
        elif qid in direct_by_id:
            plan = items[qid].get("effective_question_plan") or items[qid].get("question_plan") or {}
            verdict, reasons, proof = _direct_canary(direct_by_id[qid], plan, v2_tables, contexts)
        elif str(typed_row.get("decomposition_status") or "") == "abstain":
            reasons = ["typed_plan_abstain"] + [str(reason) for reason in typed_row.get("reason_codes") or []]
        elif str(typed_row.get("decomposition_status") or "") == "typed_non_executable":
            reasons = ["typed_plan_not_executable"] + [str(reason) for reason in typed_row.get("reason_codes") or []]
        else:
            reasons = ["no_exact_evidence_sidecar_for_representative"]
        rows.append(
            {
                "schema_version": CANARY_SCHEMA_VERSION,
                "protocol": CANARY_PROTOCOL,
                "fingerprint": fingerprint,
                "fingerprint_question_count": len(members),
                "selection_policy": "lowest_question_id_per_fingerprint",
                "question_id": qid,
                "family": member.get("family"),
                "census_route": member.get("route"),
                "typed_plan_fingerprint": typed_row.get("plan_fingerprint"),
                "typed_plan_status": typed_row.get("decomposition_status"),
                "verdict": verdict,
                "reason_codes": sorted(set(reasons)),
                "exact_binding_proof": proof,
                "answer_eligible": False,
                "training_eligible": False,
                "submission_eligible": False,
                "provenance_promotion_allowed": False,
            }
        )
    _write(output, rows, jsonl=True)
    manifest = {
        "schema_version": CANARY_SCHEMA_VERSION,
        "protocol": CANARY_PROTOCOL,
        "question_count": len(items),
        "selected_fingerprint_count": len(rows),
        "selection_policy": "largest_fingerprints_then_lowest_question_id_per_fingerprint",
        "min_question_count": min_question_count,
        "max_fingerprints": max_fingerprints,
        "verdict_counts": dict(sorted(Counter(str(row["verdict"]) for row in rows).items())),
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "fingerprint_census_sha256": sha256_file(census_path),
        "fingerprint_census_manifest_sha256": sha256_file(census_path.with_suffix(".manifest.json")),
        "typed_operand_plans_sha256": sha256_file(typed_path),
        "typed_operand_plans_manifest_sha256": sha256_file(typed_path.with_suffix(".manifest.json")),
        "formula_evidence_sha256": sha256_file(formula_path) if formula_path else None,
        "formula_evidence_manifest_sha256": sha256_file(formula_path.with_suffix(".manifest.json")) if formula_path else None,
        "direct_evidence_sha256": sha256_file(direct_path) if direct_path else None,
        "direct_evidence_manifest_sha256": sha256_file(direct_path.with_suffix(".manifest.json")) if direct_path else None,
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "sidecar_sha256": sha256_file(output),
    }
    _write(output.with_suffix(".manifest.json"), manifest)
    return rows, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--fingerprint-census", type=Path, required=True)
    parser.add_argument("--typed-plans", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path)
    parser.add_argument("--direct-evidence", type=Path)
    parser.add_argument("--evidence-context", type=Path)
    parser.add_argument("--question-plan-overrides", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-question-count", type=int, default=5)
    parser.add_argument("--max-fingerprints", type=int, default=50)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, manifest = build_canaries(
        args.bundle_dir.resolve(),
        args.fingerprint_census.resolve(),
        args.typed_plans.resolve(),
        args.output.resolve(),
        formula_path=args.formula_evidence.resolve() if args.formula_evidence else None,
        direct_path=args.direct_evidence.resolve() if args.direct_evidence else None,
        context_path=args.evidence_context.resolve() if args.evidence_context else None,
        question_plan_overrides=(
            args.question_plan_overrides.resolve() if args.question_plan_overrides else None
        ),
        min_question_count=args.min_question_count,
        max_fingerprints=args.max_fingerprints,
    )
    print(json.dumps({"output": str(args.output), "rows": len(rows), **manifest["verdict_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
