#!/usr/bin/env python3
"""Audit taxonomy candidates against immutable V2/V3 source coordinates.

The audit is diagnostic only. It verifies hashes and source identities, builds
risk strata, and materializes source-grounded examples. It never fills a
review decision or promotes a candidate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from finance_query.financial_taxonomy import FinancialTaxonomy


PROTOCOL = "financial_taxonomy_source_audit_v1"
CRITICAL_CONCEPTS = {
    "current_assets",
    "inventory",
    "current_liabilities",
    "net_income",
    "net_revenue",
    "operating_cash_flow",
    "gross_profit",
    "profit_before_tax",
    "interest_expense",
}
MIXED_CONTEXT_TERMS = (
    "điều chỉnh hồi tố",
    "trình bày lại",
    "restatement",
    "số liệu điều chỉnh",
)
AUXILIARY_CONTEXT_TERMS = (
    "phụ lục",
    "giải trình biến động",
    "số liệu so sánh",
    "phân loại lại",
    "trình bày lại",
    "hồi tố",
    "thuyết minh báo cáo tài chính",
)
PRIMARY_TITLE_CUES = {
    "balance_sheet": ("bảng cân đối kế toán", "báo cáo tình hình tài chính"),
    "income_statement": ("báo cáo kết quả", "kết quả hoạt động kinh doanh"),
    "cash_flow_statement": ("báo cáo lưu chuyển tiền tệ",),
    "equity_change_statement": ("báo cáo thay đổi vốn chủ sở hữu",),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def parse_sha256sums(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, filename = line.split(maxsplit=1)
        filename = filename.lstrip("*").strip()
        if filename in expected:
            raise ValueError(f"Duplicate SHA256SUMS entry: {filename}")
        expected[filename] = digest
    return expected


def verify_bundle(bundle: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    required = {
        "manifest.json",
        "review_items.jsonl",
        "tables.jsonl",
        "tables_structured_v2.jsonl",
        "tables_evidence_context_v3.jsonl",
        "table_routing_catalog_v1.jsonl",
        "document_metadata_v1.jsonl",
        "table_structure_v2.manifest.json",
        "table_evidence_context_v3.manifest.json",
        "table_routing_catalog_v1.manifest.json",
        "SHA256SUMS",
    }
    missing = sorted(name for name in required if not (bundle / name).is_file())
    if missing:
        raise FileNotFoundError(f"Bundle lacks required files: {missing}")

    expected_hashes = parse_sha256sums(bundle / "SHA256SUMS")
    hash_mismatches: list[dict[str, str]] = []
    for filename, expected in sorted(expected_hashes.items()):
        target = bundle / filename
        actual = sha256_file(target) if target.is_file() else "missing"
        if actual != expected:
            hash_mismatches.append(
                {"file": filename, "expected_sha256": expected, "actual_sha256": actual}
            )
    if hash_mismatches:
        raise ValueError(f"Bundle SHA mismatch: {hash_mismatches}")

    tables = load_jsonl(bundle / "tables.jsonl")
    structured = load_jsonl(bundle / "tables_structured_v2.jsonl")
    contexts = load_jsonl(bundle / "tables_evidence_context_v3.jsonl")
    catalog = load_jsonl(bundle / "table_routing_catalog_v1.jsonl")
    documents = load_jsonl(bundle / "document_metadata_v1.jsonl")
    datasets = {
        "tables": tables,
        "structured": structured,
        "contexts": contexts,
        "catalog": catalog,
        "documents": documents,
    }
    uid_sets: dict[str, set[str]] = {}
    for name, rows in datasets.items():
        identity = "document_id" if name == "documents" else "internal_table_uid"
        values = [str(row.get(identity) or "") for row in rows]
        if "" in values or len(values) != len(set(values)):
            raise ValueError(f"{name} contains missing or duplicate {identity}")
        uid_sets[name] = set(values)
    table_uids = uid_sets["tables"]
    for name in ("structured", "contexts", "catalog"):
        if uid_sets[name] != table_uids:
            raise ValueError(f"{name} UID coverage differs from tables.jsonl")
    structured_documents = {str(row.get("document_id") or "") for row in structured}
    if structured_documents != uid_sets["documents"]:
        raise ValueError("document_metadata coverage differs from structured tables")

    structure_manifest = load_json(bundle / "table_structure_v2.manifest.json")
    context_manifest = load_json(bundle / "table_evidence_context_v3.manifest.json")
    routing_manifest = load_json(bundle / "table_routing_catalog_v1.manifest.json")
    manifest_checks = {
        "structure_input_tables": (
            structure_manifest.get("input_bundle_tables_sha256"),
            sha256_file(bundle / "tables.jsonl"),
        ),
        "structure_sidecar": (
            structure_manifest.get("sidecar_sha256"),
            sha256_file(bundle / "tables_structured_v2.jsonl"),
        ),
        "context_input_structure": (
            context_manifest.get("input_structure_sha256"),
            sha256_file(bundle / "tables_structured_v2.jsonl"),
        ),
        "context_sidecar": (
            context_manifest.get("sidecar_sha256"),
            sha256_file(bundle / "tables_evidence_context_v3.jsonl"),
        ),
        "routing_input_structure": (
            routing_manifest.get("input_structure_sha256"),
            sha256_file(bundle / "tables_structured_v2.jsonl"),
        ),
        "routing_input_context": (
            routing_manifest.get("input_evidence_context_sha256"),
            sha256_file(bundle / "tables_evidence_context_v3.jsonl"),
        ),
        "routing_sidecar": (
            routing_manifest.get("table_catalog_sha256"),
            sha256_file(bundle / "table_routing_catalog_v1.jsonl"),
        ),
    }
    bad_checks = {
        key: {"expected": expected, "actual": actual}
        for key, (expected, actual) in manifest_checks.items()
        if expected != actual
    }
    if bad_checks:
        raise ValueError(f"Manifest dependency mismatch: {bad_checks}")
    return (
        {
            "sha256sums_entry_count": len(expected_hashes),
            "manifest_dependency_checks": len(manifest_checks),
            "table_uid_count": len(table_uids),
            "document_id_count": len(uid_sets["documents"]),
            "status": "verified",
        },
        datasets,
    )


def _identity(record: dict[str, Any]) -> tuple[Any, ...]:
    kind = str(record.get("record_kind") or "")
    if kind == "row_semantic_candidate":
        return (kind, str(record.get("internal_table_uid") or ""), int(record["row_index"]))
    if kind == "table_role_candidate":
        return (kind, str(record.get("internal_table_uid") or ""))
    if kind == "document_sector_candidate":
        return (kind, str(record.get("document_id") or ""))
    raise ValueError(f"Unsupported candidate record kind: {kind!r}")


def _context_text(role: dict[str, Any]) -> str:
    return " ".join(str(value) for value in role.get("context_path") or []).casefold()


def _risk_row(
    record: dict[str, Any],
    *,
    taxonomy: FinancialTaxonomy,
    catalog: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    candidate = (record.get("concept_candidates") or [{}])[0]
    concept_id = str(candidate.get("concept_id") or "")
    if concept_id in CRITICAL_CONCEPTS:
        score += 100
        reasons.append("critical_financial_operand")
    if record.get("navigation_gate_status") != "ready":
        score += 40
        reasons.append("navigation_blocked")
    if record.get("table_type_status") != "source_structural":
        score += 30
        reasons.append("table_type_provisional")
    concept = taxonomy.by_id.get(concept_id)
    if concept and "all" not in concept.sectors and record.get("sector_candidate") == "unknown":
        score += 30
        reasons.append("sector_constraint_unresolved")
    if concept and concept.account_codes:
        score += 20
        reasons.append("account_code_constrained")
    uid = str(record.get("internal_table_uid") or "")
    quality = (contexts.get(uid, {}).get("quality") or {})
    if quality.get("status") != "review_ready":
        score += 25
        reasons.append("table_quality_not_review_ready")
    if (catalog.get(uid, {}).get("report_scope") or "unknown") not in {"consolidated", "separate"}:
        score += 20
        reasons.append("report_scope_unresolved")
    return score, reasons


def _risk_role(record: dict[str, Any], *, contexts: dict[str, dict[str, Any]]) -> tuple[int, list[str]]:
    score = 40
    reasons = ["semantic_table_role_requires_review"]
    existing = str(record.get("existing_table_type") or "")
    proposed = str(record.get("proposed_table_type") or "")
    if existing != proposed:
        score += 100
        reasons.append("table_role_disagreement")
    if existing == "other":
        score += 60
        reasons.append("existing_table_type_other")
    if any(term in _context_text(record) for term in MIXED_CONTEXT_TERMS):
        score += 60
        reasons.append("restatement_or_mixed_context")
    if any(term in _context_text(record) for term in AUXILIARY_CONTEXT_TERMS):
        score += 80
        reasons.append("auxiliary_or_comparative_context")
    quality = (contexts.get(str(record.get("internal_table_uid") or ""), {}).get("quality") or {})
    if quality.get("status") != "review_ready":
        score += 25
        reasons.append("table_quality_not_review_ready")
    return score, reasons


def _risk_sector(record: dict[str, Any]) -> tuple[int, list[str]]:
    signals = record.get("matched_signals") or {}
    signal_count = len(signals.get(str(record.get("sector") or "")) or [])
    score = 40
    reasons = ["document_sector_requires_review"]
    if signal_count <= 2:
        score += 60
        reasons.append("sector_at_minimum_signal_threshold")
    scores = sorted((int(value) for value in (record.get("scores") or {}).values()), reverse=True)
    if len(scores) > 1 and scores[0] - scores[1] <= 1:
        score += 40
        reasons.append("sector_competing_signal")
    return score, reasons


def _source_excerpt(
    candidate: dict[str, Any],
    *,
    structured: dict[str, dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    documents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    uid = str(candidate.get("internal_table_uid") or "")
    document_id = str(candidate.get("document_id") or "")
    if uid:
        table = structured[uid]
        rows = table.get("rows") or []
        if candidate.get("row_index") is not None:
            center = int(candidate["row_index"])
            start, end = max(0, center - 2), min(len(rows), center + 3)
        else:
            start, end = 0, min(len(rows), 12)
        context = contexts[uid]
        return {
            "internal_table_uid": uid,
            "document_id": document_id,
            "source_provenance": table.get("source_provenance"),
            "context_trace": context.get("context_trace"),
            "canonical_headers": (context.get("canonical_headers") or {}).get("columns"),
            "quality": context.get("quality"),
            "row_window_start": start,
            "row_window": rows[start:end],
        }
    return {
        "document_id": document_id,
        "document_metadata": documents.get(document_id),
    }


def analyze(
    *,
    bundle: Path,
    taxonomy_path: Path,
    row_candidates_path: Path,
    table_roles_path: Path,
    sectors_path: Path,
    review_queue_path: Path,
    output_summary: Path,
    max_examples: int = 120,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verification, datasets = verify_bundle(bundle)
    candidate_manifest_path = row_candidates_path.with_suffix(".manifest.json")
    review_manifest_path = review_queue_path.with_suffix(".manifest.json")
    if not candidate_manifest_path.is_file() or not review_manifest_path.is_file():
        raise FileNotFoundError("Taxonomy candidate/review manifest is required")
    candidate_manifest = load_json(candidate_manifest_path)
    review_manifest = load_json(review_manifest_path)
    research_hash_checks = {
        "candidate_rows": (
            ((candidate_manifest.get("outputs") or {}).get("row_candidates") or {}).get("sha256"),
            sha256_file(row_candidates_path),
        ),
        "candidate_table_roles": (
            ((candidate_manifest.get("outputs") or {}).get("table_role_candidates") or {}).get("sha256"),
            sha256_file(table_roles_path),
        ),
        "candidate_sectors": (
            ((candidate_manifest.get("outputs") or {}).get("sector_candidates") or {}).get("sha256"),
            sha256_file(sectors_path),
        ),
        "candidate_taxonomy": (
            ((candidate_manifest.get("inputs") or {}).get("taxonomy") or {}).get("sha256"),
            sha256_file(taxonomy_path),
        ),
        "review_rows": (
            ((review_manifest.get("inputs") or {}).get("row_candidates_sha256")),
            sha256_file(row_candidates_path),
        ),
        "review_table_roles": (
            ((review_manifest.get("inputs") or {}).get("table_roles_sha256")),
            sha256_file(table_roles_path),
        ),
        "review_sectors": (
            ((review_manifest.get("inputs") or {}).get("sectors_sha256")),
            sha256_file(sectors_path),
        ),
        "review_queue": (
            review_manifest.get("output_sha256"),
            sha256_file(review_queue_path),
        ),
    }
    bad_research_hashes = {
        key: {"expected": expected, "actual": actual}
        for key, (expected, actual) in research_hash_checks.items()
        if expected != actual
    }
    if bad_research_hashes:
        raise ValueError(f"Research artifact manifest mismatch: {bad_research_hashes}")
    verification["research_manifest_dependency_checks"] = len(research_hash_checks)
    taxonomy = FinancialTaxonomy.load(taxonomy_path)
    rows = load_jsonl(row_candidates_path)
    roles = load_jsonl(table_roles_path)
    sectors = load_jsonl(sectors_path)
    queue = load_jsonl(review_queue_path)
    structured = {str(row["internal_table_uid"]): row for row in datasets["structured"]}
    contexts = {str(row["internal_table_uid"]): row for row in datasets["contexts"]}
    catalog = {str(row["internal_table_uid"]): row for row in datasets["catalog"]}
    documents = {str(row["document_id"]): row for row in datasets["documents"]}

    row_identities: set[tuple[Any, ...]] = set()
    source_row_mismatch_count = 0
    for record in rows:
        identity = _identity(record)
        if identity in row_identities:
            raise ValueError(f"Duplicate row candidate identity: {identity}")
        row_identities.add(identity)
        _, uid, row_index = identity
        source_rows = structured[uid].get("rows") or []
        if row_index >= len(source_rows) or [str(value) for value in source_rows[row_index]] != [
            str(value) for value in record.get("raw_source_row") or []
        ]:
            source_row_mismatch_count += 1
    if source_row_mismatch_count:
        raise ValueError(f"Candidate raw source mismatch count: {source_row_mismatch_count}")

    role_identities = {_identity(record) for record in roles}
    expected_role_identities = {("table_role_candidate", uid) for uid in structured}
    if role_identities != expected_role_identities or len(role_identities) != len(roles):
        raise ValueError("Table-role candidate coverage differs from V2 tables")
    sector_identities = {_identity(record) for record in sectors}
    expected_sector_identities = {("document_sector_candidate", doc) for doc in documents}
    if sector_identities != expected_sector_identities or len(sector_identities) != len(sectors):
        raise ValueError("Sector candidate coverage differs from document metadata")
    candidate_by_identity = {
        **{_identity(record): record for record in rows},
        **{_identity(record): record for record in roles},
        **{_identity(record): record for record in sectors},
    }
    queue_identity_counts: Counter[tuple[Any, ...]] = Counter()
    non_blank_decisions = 0
    for item in queue:
        candidate = dict(item.get("candidate") or {})
        identity = _identity(candidate)
        if identity not in candidate_by_identity:
            raise ValueError(f"Review queue references unknown candidate: {identity}")
        queue_identity_counts[identity] += 1
        if item.get("review_decision") is not None or item.get("reviewer_notes") is not None:
            non_blank_decisions += 1
    duplicates = [identity for identity, count in queue_identity_counts.items() if count > 1]
    if duplicates:
        raise ValueError(f"Review queue contains duplicate candidate identities: {duplicates[:5]}")

    concept_table_gate: Counter[str] = Counter()
    table_status_counts: Counter[str] = Counter()
    account_code_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    period_type_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    candidate_reason_counts: Counter[str] = Counter()
    for record in rows:
        uid = str(record["internal_table_uid"])
        candidate = (record.get("concept_candidates") or [{}])[0]
        concept_id = str(candidate.get("concept_id") or "none")
        concept_table_gate["|".join((concept_id, str(record.get("table_type")), str(record.get("navigation_gate_status"))))] += 1
        table_status_counts[str(record.get("table_type_status") or "unknown")] += 1
        scope_counts[str(catalog[uid].get("report_scope") or "unknown")] += 1
        period_type_counts[str(candidate.get("period_type") or "not_applicable")] += 1
        quality_counts[str((contexts[uid].get("quality") or {}).get("status") or "unknown")] += 1
        candidate_reason_counts.update(str(value) for value in record.get("navigation_reason_codes") or [])
        if concept_id in taxonomy.by_id and taxonomy.by_id[concept_id].account_codes:
            account_code_counts[
                "required_present" if record.get("source_account_codes") else "required_missing"
            ] += 1
        else:
            account_code_counts["not_required"] += 1

    table_role_cross_tab: Counter[str] = Counter()
    role_reason_counts: Counter[str] = Counter()
    role_conflict_triage_counts: Counter[str] = Counter()
    for record in roles:
        if record.get("status") != "semantic_candidate":
            continue
        table_role_cross_tab[
            f"{record.get('existing_table_type')}->{record.get('proposed_table_type')}"
        ] += 1
        if record.get("existing_table_type") != record.get("proposed_table_type"):
            role_reason_counts["table_role_disagreement"] += 1
            uid = str(record.get("internal_table_uid") or "")
            context_text = _context_text(record)
            quality_status = str((contexts[uid].get("quality") or {}).get("status") or "unknown")
            proposed = str(record.get("proposed_table_type") or "")
            if any(term in context_text for term in AUXILIARY_CONTEXT_TERMS):
                role_conflict_triage_counts["auxiliary_or_comparative"] += 1
            elif quality_status != "review_ready":
                role_conflict_triage_counts["source_quality_needs_processing"] += 1
            elif any(cue in context_text for cue in PRIMARY_TITLE_CUES.get(proposed, ())):
                role_conflict_triage_counts["strong_primary_title_candidate"] += 1
            else:
                role_conflict_triage_counts["unresolved"] += 1
        if any(term in _context_text(record) for term in MIXED_CONTEXT_TERMS):
            role_reason_counts["restatement_or_mixed_context"] += 1
        if any(term in _context_text(record) for term in AUXILIARY_CONTEXT_TERMS):
            role_reason_counts["auxiliary_or_comparative_context"] += 1

    sector_status_counts: Counter[str] = Counter()
    sector_signal_counts: Counter[str] = Counter()
    for record in sectors:
        sector = str(record.get("sector") or "unknown")
        sector_status_counts[f"{sector}|{record.get('status')}"] += 1
        count = len((record.get("matched_signals") or {}).get(sector) or [])
        sector_signal_counts[f"{sector}|{count}"] += 1

    priority_rows: list[dict[str, Any]] = []
    for item in queue:
        candidate = dict(item["candidate"])
        kind = str(candidate.get("record_kind") or "")
        if kind == "row_semantic_candidate":
            score, reasons = _risk_row(
                candidate, taxonomy=taxonomy, catalog=catalog, contexts=contexts
            )
        elif kind == "table_role_candidate":
            score, reasons = _risk_role(candidate, contexts=contexts)
        else:
            score, reasons = _risk_sector(candidate)
        priority_rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "record_kind": "source_grounded_audit_example",
                "review_type": item.get("review_type"),
                "risk_score": score,
                "risk_reason_codes": sorted(set(reasons)),
                "candidate": candidate,
                "source_excerpt": _source_excerpt(
                    candidate,
                    structured=structured,
                    contexts=contexts,
                    documents=documents,
                ),
                "review_decision": None,
                "reviewer_notes": None,
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
            }
        )
    priority_rows.sort(
        key=lambda record: (
            -int(record["risk_score"]),
            str(_identity(record["candidate"])),
        )
    )
    examples = priority_rows[:max_examples]
    risk_reason_counts: Counter[str] = Counter(
        reason for record in priority_rows for reason in record["risk_reason_codes"]
    )
    risk_band_counts = Counter(
        "critical" if record["risk_score"] >= 150 else "high" if record["risk_score"] >= 100 else "standard"
        for record in priority_rows
    )
    risk_band_by_review_type: Counter[str] = Counter()
    critical_concept_review_counts: Counter[str] = Counter()
    for record in priority_rows:
        band = (
            "critical"
            if record["risk_score"] >= 150
            else "high"
            if record["risk_score"] >= 100
            else "standard"
        )
        risk_band_by_review_type[f"{record['review_type']}|{band}"] += 1
        candidate = record["candidate"]
        concept_id = str(
            ((candidate.get("concept_candidates") or [{}])[0]).get("concept_id") or ""
        )
        if concept_id in CRITICAL_CONCEPTS:
            critical_concept_review_counts[concept_id] += 1

    output_summary.parent.mkdir(parents=True, exist_ok=True)
    examples_path = output_summary.with_name("financial_taxonomy_audit_examples_v1.jsonl")
    with examples_path.open("w", encoding="utf-8") as handle:
        for record in examples:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "snapshot_verification": verification,
        "candidate_integrity": {
            "row_candidate_count": len(rows),
            "raw_source_row_mismatch_count": source_row_mismatch_count,
            "table_role_count": len(roles),
            "sector_candidate_count": len(sectors),
            "review_queue_count": len(queue),
            "review_queue_non_blank_decision_count": non_blank_decisions,
        },
        "diagnostics": {
            "concept_table_gate_counts": dict(sorted(concept_table_gate.items())),
            "table_type_status_counts": dict(sorted(table_status_counts.items())),
            "account_code_contract_counts": dict(sorted(account_code_counts.items())),
            "report_scope_counts": dict(sorted(scope_counts.items())),
            "period_type_counts": dict(sorted(period_type_counts.items())),
            "table_quality_counts": dict(sorted(quality_counts.items())),
            "navigation_reason_counts": dict(sorted(candidate_reason_counts.items())),
            "table_role_cross_tab": dict(sorted(table_role_cross_tab.items())),
            "table_role_risk_counts": dict(sorted(role_reason_counts.items())),
            "table_role_conflict_triage_counts": dict(
                sorted(role_conflict_triage_counts.items())
            ),
            "sector_status_counts": dict(sorted(sector_status_counts.items())),
            "sector_signal_counts": dict(sorted(sector_signal_counts.items())),
            "risk_band_counts": dict(sorted(risk_band_counts.items())),
            "risk_band_by_review_type_counts": dict(
                sorted(risk_band_by_review_type.items())
            ),
            "risk_reason_counts": dict(sorted(risk_reason_counts.items())),
            "critical_concept_review_counts": dict(
                sorted(critical_concept_review_counts.items())
            ),
        },
        "priority_example_count": len(examples),
        "review_decision_contract": ["accept", "reject", "uncertain"],
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    output_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    inputs = {
        "bundle_sha256sums": bundle / "SHA256SUMS",
        "taxonomy": taxonomy_path,
        "row_candidates": row_candidates_path,
        "candidate_manifest": candidate_manifest_path,
        "table_roles": table_roles_path,
        "sectors": sectors_path,
        "review_queue": review_queue_path,
        "review_queue_manifest": review_manifest_path,
    }
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in inputs.items()
        },
        "outputs": {
            "summary": {"path": str(output_summary), "sha256": sha256_file(output_summary)},
            "examples": {"path": str(examples_path), "sha256": sha256_file(examples_path)},
        },
        "promotion_allowed": False,
    }
    manifest_path = output_summary.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--row-candidates", type=Path, required=True)
    parser.add_argument("--table-roles", type=Path, required=True)
    parser.add_argument("--sectors", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--max-examples", type=int, default=120)
    args = parser.parse_args()
    if args.max_examples < 1:
        raise ValueError("max-examples must be positive")
    summary, _manifest = analyze(
        bundle=args.bundle_dir.resolve(),
        taxonomy_path=args.taxonomy.resolve(),
        row_candidates_path=args.row_candidates.resolve(),
        table_roles_path=args.table_roles.resolve(),
        sectors_path=args.sectors.resolve(),
        review_queue_path=args.review_queue.resolve(),
        output_summary=args.output_summary.resolve(),
        max_examples=args.max_examples,
    )
    print(
        json.dumps(
            {
                "snapshot": summary["snapshot_verification"]["status"],
                "queue": summary["candidate_integrity"]["review_queue_count"],
                "risk_bands": summary["diagnostics"]["risk_band_counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
