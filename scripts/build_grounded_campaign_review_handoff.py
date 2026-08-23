#!/usr/bin/env python3
"""Build a hash-bound campaign handoff from locked campaign-only certificates."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


PROTOCOL = "vifinqa_grounded_campaign_review_handoff_v1"
CANDIDATE_PROTOCOL = "vifinqa_grounded_campaign_candidate_v1"
DECISION_PROTOCOL = "vifinqa_grounded_campaign_human_decision_v1"
SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "may_materialize_answer": False,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def require_bound_file(record: Mapping[str, Any], label: str) -> Path:
    path = Path(str(record.get("path") or ""))
    expected = record.get("sha256")
    if not path.is_file() or not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError(f"Immutable artifact mismatch: {label}")
    return path


def build(*, run_receipt: Path, output_dir: Path, review_bundle: Path | None = None) -> dict[str, Any]:
    run = load_json(run_receipt)
    if (
        run.get("protocol") != "vifinqa_grounded_e2e_v1"
        or run.get("run_status") != "complete_research_only"
        or len(run.get("reproducibility") or {}) != 5
        or not all(value is True for value in (run.get("reproducibility") or {}).values())
    ):
        raise ValueError("Campaign handoff requires a fully locked grounded replay")
    contract = run.get("source_contract") or {}
    if contract.get("promotion_allowed") is not False or contract.get("may_materialize_answer") is not False:
        raise ValueError("Grounded replay source contract is unsafe")

    authorization = ((run.get("outputs") or {}).get("authorization") or {})
    certificates_path = require_bound_file(
        {"path": authorization.get("answer_certificates_path"), "sha256": authorization.get("answer_certificates_sha256")},
        "answer certificates",
    )
    readiness_path = require_bound_file(
        {"path": authorization.get("readiness_path"), "sha256": authorization.get("readiness_sha256")},
        "authorization readiness",
    )
    readiness = load_json(readiness_path)
    if (
        readiness.get("release_authorized") is not False
        or readiness.get("authorization_status") != "blocked"
        or readiness.get("independent_audit_required") is not True
        or readiness.get("release_gate_required") is not True
    ):
        raise ValueError("Campaign handoff requires a blocked fail-closed release gate")

    run_inputs = run.get("inputs") or {}
    route_overlay_path = require_bound_file(run_inputs.get("route_overlay") or {}, "route overlay")
    evidence_context_path = require_bound_file(run_inputs.get("evidence_context") or {}, "evidence context")
    route_by_question = {int(row["question_id"]): row for row in load_jsonl(route_overlay_path)}
    context_by_uid = {
        str(row["internal_table_uid"]): row for row in load_jsonl(evidence_context_path)
    }

    rows = load_jsonl(certificates_path)
    review_items: dict[int, Mapping[str, Any]] = {}
    review_bundle_sha: str | None = None
    if review_bundle is not None:
        bundle = load_json(review_bundle)
        review_items = {
            int(item["queue"]["question_id"]): item
            for item in bundle.get("items") or []
            if isinstance(item, Mapping) and isinstance(item.get("queue"), Mapping)
        }
        review_bundle_sha = sha256_file(review_bundle)
    candidates: list[dict[str, Any]] = []
    role_issue_briefs: list[dict[str, Any]] = []
    entity_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    period_method_counts: Counter[str] = Counter()
    for row in rows:
        certificate = row.get("answer_certificate") or {}
        if certificate.get("status") != "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY":
            continue
        if (
            certificate.get("promotion_allowed") is not False
            or certificate.get("serving_eligible") is not False
            or certificate.get("training_eligible") is not False
            or certificate.get("next_gate") != "campaign_review_and_release_policy"
            or certificate.get("abstain_reason_codes") != []
        ):
            raise ValueError("Campaign-only certificate violates the review boundary")
        bindings = certificate.get("binding_receipts") or []
        if not bindings:
            raise ValueError("Campaign-only certificate lacks binding receipts")
        entities = sorted({str((binding.get("entity_scope") or {}).get("entity")) for binding in bindings})
        scopes = sorted({str((binding.get("entity_scope") or {}).get("scope")) for binding in bindings})
        period_methods = sorted({str((binding.get("period") or {}).get("recognition_method")) for binding in bindings})
        entity_counts.update(entities)
        scope_counts.update(scopes)
        period_method_counts.update(period_methods)
        payload = {
            "schema_version": 1,
            "protocol": CANDIDATE_PROTOCOL,
            "question_id": row.get("question_id"),
            "stage_id": row.get("stage_id"),
            "answer_certificate_id": certificate.get("answer_certificate_id"),
            "binding_ids": [binding.get("binding_id") for binding in bindings],
            "entities": entities,
            "scopes": scopes,
            "period_recognition_methods": period_methods,
            "source_value_cells": [binding.get("source_value_cell") for binding in bindings],
            "answer_decimal": (certificate.get("execution_receipt") or {}).get("answer_decimal"),
            "claim_entity_role": None,
            "entity_role_status": "NOT_APPLICABLE",
            "review_decision": None,
            "source_contract": SOURCE_CONTRACT,
        }
        review_item = review_items.get(int(row.get("question_id") or 0))
        route = route_by_question.get(int(row.get("question_id") or 0)) or {}
        question_text = str((review_item or {}).get("question") or route.get("question") or "")
        source_uid = str(((bindings[0].get("source_value_cell") or {}).get("internal_table_uid")) or "")
        source_title = str(
            ((review_item or {}).get("queue") or {}).get("source_title")
            or (((context_by_uid.get(source_uid) or {}).get("context_trace") or {}).get("source_title"))
            or ""
        )
        requested_role = str(((route.get("question_context") or {}).get("entity_role")) or "")
        if requested_role == "parent" or re.search(r"\bcông ty mẹ\b", question_text, re.IGNORECASE):
            payload["claim_entity_role"] = "parent"
            certified_roles = [
                str((binding.get("entity_role") or {}).get("role") or "")
                for binding in bindings
                if str((binding.get("entity_role") or {}).get("status") or "") == "PASS"
            ]
            role_is_certified = len(certified_roles) == len(bindings) and set(certified_roles) == {"parent"}
            payload["entity_role_status"] = "PASS" if role_is_certified else "UNRESOLVED"
            title_has_role_literal = bool(re.search(r"\bcông ty m(?:ẹ|ệ)\b", source_title, re.IGNORECASE))
            issue_payload = {
                "schema_version": 1,
                "protocol": "vifinqa_entity_role_issue_brief_v1",
                "question_id": row.get("question_id"),
                "question": question_text,
                "claim_entity_identity": entities[0] if len(entities) == 1 else None,
                "claim_entity_role": "parent",
                "source_issuer": entities[0] if len(entities) == 1 else None,
                "source_reporting_scope": scopes[0] if len(scopes) == 1 else None,
                "source_title": source_title,
                "source_title_sha256": hashlib.sha256(source_title.encode("utf-8")).hexdigest(),
                "source_role_literal_present": title_has_role_literal,
                "strict_status": "UNRESOLVED",
                "reason_codes": ["LEGACY_CERTIFICATE_ENTITY_ROLE_FIELD_MISSING"],
                "review_prompts": [
                    {"id": "issuer_identity_matches_claim", "question": "Source issuer có đúng là pháp nhân được claim nhắc tới không?"},
                    {"id": "parent_role_proven_by_source", "question": "Nguồn có chứng minh pháp nhân này giữ vai trò công ty mẹ không?"},
                    {"id": "reporting_scope_matches_claim", "question": "Reporting perimeter separate có khớp phần phạm vi của claim không?"},
                    {"id": "counterfactual_covers_entity_role", "question": "Counterfactual hiện tại có kiểm tra riêng entity role không?"},
                ],
                "required_outcome": "needs_revision_unless_hash_bound_role_provenance_is_added",
            }
            if not role_is_certified:
                role_issue_briefs.append({**issue_payload, "issue_brief_sha256": canonical_sha256(issue_payload)})
        candidates.append({**payload, "campaign_candidate_sha256": canonical_sha256(payload)})

    expected_complete = ((authorization.get("counts") or {}).get("answer_certificate_status_counts") or {}).get(
        "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
    )
    authorization_counts = authorization.get("counts") or {}
    approval_count = authorization_counts.get("effective_semantic_approval_count")
    if approval_count is None:
        # Backward compatibility for locked runs produced before explicit
        # human-equivalent ChatGPT gate accounting was added.
        approval_count = authorization_counts.get("human_semantic_approval_count")
    if (
        not candidates
        or len(candidates) != expected_complete
        or not isinstance(approval_count, int)
        or approval_count < len(candidates)
    ):
        raise ValueError("Campaign candidate count does not match authorized complete certificates")
    candidates.sort(key=lambda row: int(row["question_id"]))

    output_dir.mkdir(parents=True, exist_ok=False)
    candidates_path = output_dir / "grounded_campaign_candidates_v1.jsonl"
    packet_path = output_dir / "grounded_campaign_review_packet_v1.json"
    role_issues_path = output_dir / "entity_role_issue_briefs_v1.jsonl"
    response_path = output_dir / "grounded_campaign_human_response_template_v1.json"
    manifest_path = output_dir / "grounded_campaign_review_handoff_v1.manifest.json"
    candidates_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in candidates),
        encoding="utf-8",
    )
    role_issue_briefs.sort(key=lambda row: int(row["question_id"]))
    role_issues_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in role_issue_briefs),
        encoding="utf-8",
    )
    run_sha = sha256_file(run_receipt)
    candidate_sha = sha256_file(candidates_path)
    campaign_id = canonical_sha256(
        {"run_receipt_sha256": run_sha, "candidate_manifest_sha256": candidate_sha, "candidate_count": len(candidates)}
    )
    packet = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "campaign_id": campaign_id,
        "campaign_status": "semantic_revision_required" if role_issue_briefs else "awaiting_independent_campaign_review",
        "candidate_count": len(candidates),
        "question_ids": [row["question_id"] for row in candidates],
        "strata": {
            "entity_counts": dict(sorted(entity_counts.items())),
            "scope_counts": dict(sorted(scope_counts.items())),
            "period_recognition_method_counts": dict(sorted(period_method_counts.items())),
        },
        "review_requirements": {
            "stratified_source_reopen": True,
            "high_severity_false_certification_review": True,
            "mutation_suite_review": True,
            "rule_and_code_hash_review": True,
            "exact_candidate_manifest_diff_review": True,
            "independent_reviewer_required": True,
            "entity_role_issue_review": bool(role_issue_briefs),
        },
        "entity_role_issue_count": len(role_issue_briefs),
        "source_code_sha256": run.get("source_code"),
        "release_authorized": False,
        "source_contract": SOURCE_CONTRACT,
    }
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    response = {
        "schema_version": 1,
        "protocol": DECISION_PROTOCOL,
        "campaign_id": campaign_id,
        "campaign_candidate_manifest_sha256": candidate_sha,
        "decision_options": ["approve_campaign", "reject_campaign", "needs_revision"],
        "decision": None,
        "decision_provenance": None,
        "reviewer_role": "independent_campaign_reviewer",
        "reviewed_at": None,
        "review_evidence": {
            "stratified_source_reopen_complete": None,
            "high_severity_review_complete": None,
            "mutation_suite_review_complete": None,
            "rule_and_code_hash_review_complete": None,
            "candidate_manifest_diff_review_complete": None,
            "entity_role_issue_review_complete": None,
        },
        "blocking_issue_count": len(role_issue_briefs),
        "semantic_issue_reviews": [],
        "notes": "",
        "release_authorized": False,
        "release_policy_gate_required": True,
        "promotion_allowed": False,
        "source_contract": SOURCE_CONTRACT,
    }
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "campaign_id": campaign_id,
        "campaign_status": "semantic_revision_required" if role_issue_briefs else "awaiting_independent_campaign_review",
        "candidate_count": len(candidates),
        "human_campaign_decision_count": 0,
        "inputs": {
            "grounded_run": {"path": str(run_receipt), "sha256": run_sha},
            "answer_certificates": {"path": str(certificates_path), "sha256": sha256_file(certificates_path)},
            "authorization_readiness": {"path": str(readiness_path), "sha256": sha256_file(readiness_path)},
            "route_overlay": {"path": str(route_overlay_path), "sha256": sha256_file(route_overlay_path)},
            "evidence_context": {"path": str(evidence_context_path), "sha256": sha256_file(evidence_context_path)},
            "review_bundle": None if review_bundle is None else {"path": str(review_bundle), "sha256": review_bundle_sha},
        },
        "outputs": {
            "candidates": {"path": str(candidates_path), "sha256": candidate_sha},
            "review_packet": {"path": str(packet_path), "sha256": sha256_file(packet_path)},
            "blank_human_response": {"path": str(response_path), "sha256": sha256_file(response_path)},
            "entity_role_issue_briefs": {"path": str(role_issues_path), "sha256": sha256_file(role_issues_path)},
        },
        "source_contract": SOURCE_CONTRACT,
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-bundle", type=Path)
    args = parser.parse_args()
    result = build(
        run_receipt=args.run_receipt.resolve(),
        output_dir=args.output_dir.resolve(),
        review_bundle=args.review_bundle.resolve() if args.review_bundle else None,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
