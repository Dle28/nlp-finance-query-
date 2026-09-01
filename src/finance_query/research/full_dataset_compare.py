"""Compare a candidate replay with the frozen ViFinQA baseline without gold."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_full_dataset_candidate_comparison_v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _certificate(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("answer_certificate")
    if not isinstance(value, Mapping):
        raise ValueError("answer certificate row is missing answer_certificate")
    return value


def _status(row: Mapping[str, Any]) -> str:
    return str(_certificate(row).get("status") or "MISSING")


def _company_group(taxonomy: Mapping[str, Any]) -> str:
    entities = taxonomy.get("entities_resolved") or []
    if not isinstance(entities, list) or not entities:
        return "company_unresolved"
    if len(entities) == 1:
        return f"single_company:{entities[0]}"
    return f"multi_company:{len(entities)}"


def compare_full_dataset(
    *,
    baseline_rows: Iterable[Mapping[str, Any]],
    candidate_rows: Iterable[Mapping[str, Any]],
    taxonomy_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline = {int(row["question_id"]): row for row in baseline_rows}
    candidate = {int(row["question_id"]): row for row in candidate_rows}
    taxonomy = {int(row["question_id"]): row for row in taxonomy_rows}
    if not baseline or set(baseline) != set(candidate) or set(baseline) != set(taxonomy):
        raise ValueError("baseline, candidate, and taxonomy must cover the same non-empty IDs")

    rows: list[dict[str, Any]] = []
    dimension_counts: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    overall: Counter[str] = Counter()
    baseline_status: Counter[str] = Counter()
    candidate_status: Counter[str] = Counter()
    for question_id in sorted(baseline):
        base = baseline[question_id]
        cand = candidate[question_id]
        tax_record = taxonomy[question_id]
        tax = tax_record.get("taxonomy") or {}
        if not isinstance(tax, Mapping):
            raise ValueError("taxonomy record lacks taxonomy mapping")
        base_cert = _certificate(base)
        cand_cert = _certificate(cand)
        unchanged = base_cert == cand_cert
        outcome = "UNCHANGED" if unchanged else "CHANGED_UNSCORABLE_WITHOUT_GOLD"
        overall[outcome] += 1
        baseline_status[_status(base)] += 1
        candidate_status[_status(cand)] += 1
        dimensions: dict[str, list[str]] = {
            "question_family": [str(tax.get("question_type") or "unresolved")],
            "operation_family": [str(value) for value in tax.get("operation_families") or ["unresolved"]],
            "temporal_family": [str(value) for value in tax.get("temporal_families") or ["unresolved"]],
            "source_topology": [str(tax.get("expected_source_topology") or "unresolved")],
            "entity_family": [str(tax.get("entity_family") or "unresolved")],
            "company_group": [_company_group(tax)],
        }
        for dimension, values in dimensions.items():
            for value in values:
                dimension_counts[dimension][value][outcome] += 1
        rows.append({
            "protocol": PROTOCOL,
            "question_id": question_id,
            "question_id_role": "tracking_only",
            "question_sha256": tax_record.get("question_sha256"),
            "baseline_status": _status(base),
            "candidate_status": _status(cand),
            "outcome": outcome,
            "question_materialized": False,
            "source_contract": {
                "research_only": True,
                "submission_eligible": False,
                "may_materialize_answer": False,
            },
        })
    by_dimension = {
        dimension: {
            value: dict(sorted(counts.items()))
            for value, counts in sorted(groups.items())
        }
        for dimension, groups in sorted(dimension_counts.items())
    }
    baseline_predictions = sum(
        count for status, count in baseline_status.items() if status not in {"ABSTAIN", "MISSING"}
    )
    candidate_predictions = sum(
        count for status, count in candidate_status.items() if status not in {"ABSTAIN", "MISSING"}
    )
    report = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "question_count": len(rows),
        "overall_outcome_counts": dict(sorted(overall.items())),
        "baseline_status_counts": dict(sorted(baseline_status.items())),
        "candidate_status_counts": dict(sorted(candidate_status.items())),
        "by_dimension": by_dimension,
        "abstention": {
            "baseline_count": baseline_status["ABSTAIN"],
            "candidate_count": candidate_status["ABSTAIN"],
            "change": candidate_status["ABSTAIN"] - baseline_status["ABSTAIN"],
        },
        "prediction_count": {
            "baseline": baseline_predictions,
            "candidate": candidate_predictions,
            "change": candidate_predictions - baseline_predictions,
        },
        "false_positive_change": "NOT_MEASURABLE_NO_VIFINQA_GOLD",
        "false_confidence_change": (
            "NOT_ESTIMABLE_NO_PREDICTIONS"
            if baseline_predictions == candidate_predictions == 0
            else "NOT_MEASURABLE_NO_VIFINQA_GOLD"
        ),
        "regression_count": 0 if overall.get("CHANGED_UNSCORABLE_WITHOUT_GOLD", 0) == 0 else None,
        "semantic_improvement_status": "NOT_MEASURABLE_NO_VIFINQA_GOLD",
        "question_materialized": False,
        "source_contract": {
            "research_only": True,
            "evidence_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    return rows, report
