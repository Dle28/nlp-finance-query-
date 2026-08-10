"""Read-only cross-tabulation for ViFinQA review/evidence health.

The dashboard does not score candidates, calculate answers or mutate review
statuses.  It makes bottlenecks observable across question family, existing
semantic metadata, OCR triage and Formula EvidenceSet state.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping


EVALUATION_DASHBOARD_VERSION = 1
EVALUATION_DASHBOARD_PROTOCOL = "read_only_grounding_health_dashboard_v1"
ALLOWED_REVIEW_STATUSES = {
    "human_verified",
    "machine_calibrated",
    "machine_provisional",
    "needs_human",
}


def _unique_by_id(rows: Iterable[Mapping[str, Any]], source: str) -> dict[int, Mapping[str, Any]]:
    output: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        value = row.get("id")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{source} row lacks integer id")
        if value in output:
            raise ValueError(f"{source} has duplicate Q{value}")
        output[value] = row
    return output


def _unique_by_uid(rows: Iterable[Mapping[str, Any]], source: str) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        uid = str(row.get("internal_table_uid") or "")
        if not uid:
            raise ValueError(f"{source} row lacks internal_table_uid")
        if uid in output:
            raise ValueError(f"{source} has duplicate table UID")
        output[uid] = row
    return output


def _review_status(row: Mapping[str, Any]) -> str:
    status = str(row.get("consensus_status") or row.get("machine_status") or row.get("status") or "")
    if status not in ALLOWED_REVIEW_STATUSES:
        raise ValueError(f"Q{row.get('id')}: unsupported review status {status!r}")
    return status


def _cross_tab(
    rows: Iterable[Mapping[str, str]], row_key: str, column_key: str
) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        counts[str(row[row_key])][str(row[column_key])] += 1
    return {
        name: dict(sorted(values.items()))
        for name, values in sorted(counts.items())
    }


def build_evaluation_dashboard(
    reviews: Iterable[Mapping[str, Any]],
    formula_evidence: Iterable[Mapping[str, Any]],
    semantic_catalog: Iterable[Mapping[str, Any]],
    ocr_quality_profiles: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Join immutable IDs only and return diagnostic counts, never decisions."""
    review_by_id = _unique_by_id(reviews, "machine reviews")
    formula_by_id = _unique_by_id(formula_evidence, "formula evidence")
    catalog_by_uid = _unique_by_uid(semantic_catalog, "semantic catalog")
    quality_by_uid = _unique_by_uid(ocr_quality_profiles, "OCR quality profile")
    rows: list[dict[str, str]] = []
    missing_catalog = 0
    missing_quality = 0
    for qid, review in sorted(review_by_id.items()):
        uid = str(review.get("machine_candidate_uid") or "")
        catalog = catalog_by_uid.get(uid) if uid else None
        quality = quality_by_uid.get(uid) if uid else None
        if uid and catalog is None:
            missing_catalog += 1
        if uid and quality is None:
            missing_quality += 1
        formula = formula_by_id.get(qid)
        formula_spec = (formula or {}).get("formula") or {}
        formula_id = str(formula_spec.get("formula_id") or "")
        formula_state = (
            str((formula or {}).get("evidence_completeness") or "unknown")
            if formula_id
            else "not_formula"
        )
        rows.append(
            {
                "family": str(review.get("family") or "unknown"),
                "review_status": _review_status(review),
                "document_role": str((catalog or {}).get("document_role") or ("no_candidate" if not uid else "catalog_missing")),
                "ocr_triage": str(((quality or {}).get("triage") or {}).get("action") or ("no_candidate" if not uid else "profile_missing")),
                "formula_evidence_state": formula_state,
                "candidate_provenance": str(review.get("machine_candidate_source") or "no_candidate"),
            }
        )
    return {
        "evaluation_dashboard_version": EVALUATION_DASHBOARD_VERSION,
        "protocol": EVALUATION_DASHBOARD_PROTOCOL,
        "source_contract": {
            "read_only": True,
            "answer_eligible": False,
            "evidence_eligible": False,
            "training_eligible": False,
            "may_change_review_status": False,
        },
        "question_count": len(rows),
        "join_diagnostics": {
            "review_questions_without_formula_evidence": len(set(review_by_id) - set(formula_by_id)),
            "formula_evidence_without_review": len(set(formula_by_id) - set(review_by_id)),
            "candidate_uid_missing_catalog": missing_catalog,
            "candidate_uid_missing_ocr_profile": missing_quality,
        },
        "status_counts": dict(sorted(Counter(row["review_status"] for row in rows).items())),
        "formula_evidence_counts": dict(sorted(Counter(row["formula_evidence_state"] for row in rows).items())),
        "cross_tabs": {
            "family_by_review_status": _cross_tab(rows, "family", "review_status"),
            "family_by_document_role": _cross_tab(rows, "family", "document_role"),
            "family_by_ocr_triage": _cross_tab(rows, "family", "ocr_triage"),
            "family_by_formula_evidence_state": _cross_tab(rows, "family", "formula_evidence_state"),
            "document_role_by_review_status": _cross_tab(rows, "document_role", "review_status"),
            "ocr_triage_by_review_status": _cross_tab(rows, "ocr_triage", "review_status"),
            "candidate_provenance_by_review_status": _cross_tab(rows, "candidate_provenance", "review_status"),
        },
    }
