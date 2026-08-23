"""Dynamic candidate-set construction and oracle retrieval diagnostics.

Retrieval rank is navigation metadata.  These helpers deliberately return a
bounded, provenance-bearing candidate universe and make an unresolved coverage
gap explicit; they never select a table/cell as evidence or answer a question.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


CANDIDATE_SET_PROTOCOL = "vifinqa_dynamic_candidate_set_v1"
RETRIEVAL_DIAGNOSTIC_PROTOCOL = "vifinqa_retrieval_ladder_diagnostic_v1"

_DEFAULT_BUDGETS = {"lexical": 20, "dense": 20, "metadata": 10}


class RetrievalDiagnosticError(ValueError):
    """A candidate or oracle contract is malformed."""


def _sha(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalise_source(
    name: str,
    values: Iterable[Mapping[str, Any]],
    *,
    budget: int,
) -> list[dict[str, Any]]:
    if budget < 1:
        raise RetrievalDiagnosticError(f"{name} budget must be positive")
    prepared: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise RetrievalDiagnosticError(f"{name} candidate is not an object")
        uid = str(value.get("internal_table_uid") or value.get("uid") or "").strip()
        if not uid:
            raise RetrievalDiagnosticError(f"{name} candidate has no table UID")
        try:
            score = float(value.get("score", 0.0))
        except (TypeError, ValueError) as error:
            raise RetrievalDiagnosticError(f"{name} candidate has invalid score") from error
        prepared.append({"uid": uid, "score": score, "metadata": dict(value)})
    prepared.sort(key=lambda item: (-item["score"], item["uid"]))
    # A retriever can return the same table repeatedly through chunk hits. One
    # deterministic best rank per source prevents that from consuming budget.
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in prepared:
        if value["uid"] not in seen:
            deduplicated.append(value)
            seen.add(value["uid"])
    return deduplicated[:budget]


def _value_set(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(item) for item in value if str(item)}
    return {str(value)} if str(value) else set()


def build_dynamic_candidate_set(
    *,
    question_id: object,
    lexical: Iterable[Mapping[str, Any]],
    dense: Iterable[Mapping[str, Any]],
    metadata: Iterable[Mapping[str, Any]],
    coverage_requirements: Mapping[str, object] | None = None,
    budgets: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Fuse source-specific retrieval pools and expose missing coverage.

    ``coverage_requirements`` is source-plan metadata such as ``ticker``,
    ``report_year`` and ``scope``.  It is a stop/expand guard, not a semantic
    filter: missing coverage requests an additional retrieval pass instead of
    silently returning a smaller Top-K.
    """
    if question_id is None or str(question_id) == "":
        raise RetrievalDiagnosticError("question_id is required")
    effective_budgets = dict(_DEFAULT_BUDGETS)
    for name, value in (budgets or {}).items():
        if name not in effective_budgets:
            raise RetrievalDiagnosticError(f"unsupported candidate source: {name}")
        effective_budgets[name] = int(value)
    source_rows = {
        "lexical": _normalise_source("lexical", lexical, budget=effective_budgets["lexical"]),
        "dense": _normalise_source("dense", dense, budget=effective_budgets["dense"]),
        "metadata": _normalise_source("metadata", metadata, budget=effective_budgets["metadata"]),
    }
    merged: dict[str, dict[str, Any]] = {}
    for source_name, rows in source_rows.items():
        for rank, item in enumerate(rows, start=1):
            record = merged.setdefault(
                item["uid"],
                {
                    "internal_table_uid": item["uid"],
                    "retrieval_channels": [],
                    "channel_ranks": {},
                    "channel_scores": {},
                    "metadata": dict(item["metadata"]),
                },
            )
            record["retrieval_channels"].append(source_name)
            record["channel_ranks"][source_name] = rank
            record["channel_scores"][source_name] = item["score"]
            # Prefer metadata from an explicit source; otherwise preserve the
            # first deterministic source value only.
            if source_name == "metadata":
                record["metadata"] = dict(item["metadata"])

    candidates = []
    for record in merged.values():
        reciprocal_rank = sum(1.0 / (60.0 + rank) for rank in record["channel_ranks"].values())
        candidates.append(
            {
                **record,
                "retrieval_channels": sorted(record["retrieval_channels"]),
                "fusion_score": reciprocal_rank,
            }
        )
    candidates.sort(key=lambda item: (-float(item["fusion_score"]), item["internal_table_uid"]))

    coverage: dict[str, dict[str, list[str]]] = {}
    for dimension, requested in sorted((coverage_requirements or {}).items()):
        required = _value_set(requested)
        observed: set[str] = set()
        for candidate in candidates:
            metadata_values = _value_set((candidate.get("metadata") or {}).get(dimension))
            observed.update(metadata_values)
        coverage[str(dimension)] = {
            "required": sorted(required),
            "observed": sorted(observed),
            "missing": sorted(required - observed),
        }
    missing_dimensions = sorted(
        dimension for dimension, value in coverage.items() if value["missing"]
    )
    status = "COVERAGE_COMPLETE" if candidates and not missing_dimensions else "EXPANSION_REQUIRED"
    payload = {
        "schema_version": 1,
        "protocol": CANDIDATE_SET_PROTOCOL,
        "question_id": question_id,
        "budgets": effective_budgets,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "coverage": coverage,
        "status": status,
        "next_action": "rerank_and_bind_candidates"
        if status == "COVERAGE_COMPLETE"
        else "expand_retrieval_before_binding_or_abstain",
        "evidence_eligible": False,
        "training_eligible": False,
        "promotion_allowed": False,
    }
    return {"candidate_set_id": _sha(payload), **payload}


def _table_values(candidate_set: Mapping[str, Any], field: str) -> set[str]:
    values: set[str] = set()
    for candidate in candidate_set.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        if field == "internal_table_uid":
            values.add(str(candidate.get(field) or ""))
        else:
            values.update(_value_set((candidate.get("metadata") or {}).get(field)))
    values.discard("")
    return values


def evaluate_retrieval_ladder(
    *,
    candidate_set: Mapping[str, Any],
    oracle: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate document/page/table/operand-set reachability, not answer EM.

    The oracle is evaluation-only ground truth.  Its required fields must be
    explicit; missing oracle fields are not replaced by a convenient answer.
    """
    if candidate_set.get("protocol") != CANDIDATE_SET_PROTOCOL:
        raise RetrievalDiagnosticError("candidate set protocol mismatch")
    if candidate_set.get("training_eligible") is not False:
        raise RetrievalDiagnosticError("candidate set must remain non-promotable")
    required_oracle_fields = ("document_id", "page_no", "internal_table_uid", "operand_anchor_sets")
    if any(field not in oracle for field in required_oracle_fields):
        raise RetrievalDiagnosticError("oracle must declare document, page, table and operand anchor sets")
    if not isinstance(oracle.get("operand_anchor_sets"), list):
        raise RetrievalDiagnosticError("oracle operand_anchor_sets must be a list")

    document_hit = str(oracle["document_id"]) in _table_values(candidate_set, "document_id")
    page_values = {
        (str((candidate.get("metadata") or {}).get("document_id") or ""), str((candidate.get("metadata") or {}).get("page_no") or ""))
        for candidate in candidate_set.get("candidates") or []
        if isinstance(candidate, Mapping)
    }
    page_hit = (str(oracle["document_id"]), str(oracle["page_no"])) in page_values
    table_hit = str(oracle["internal_table_uid"]) in _table_values(candidate_set, "internal_table_uid")
    candidate_anchor_ids: set[str] = set()
    for candidate in candidate_set.get("candidates") or []:
        if isinstance(candidate, Mapping):
            candidate_anchor_ids.update(_value_set((candidate.get("metadata") or {}).get("anchor_ids")))
    operand_sets = [
        {str(anchor_id) for anchor_id in group if str(anchor_id)}
        for group in oracle["operand_anchor_sets"]
        if isinstance(group, list)
    ]
    operand_set_hits = [operand_set.issubset(candidate_anchor_ids) for operand_set in operand_sets]
    complete_operand_set_hit = bool(operand_set_hits) and all(operand_set_hits)
    payload = {
        "schema_version": 1,
        "protocol": RETRIEVAL_DIAGNOSTIC_PROTOCOL,
        "candidate_set_id": candidate_set.get("candidate_set_id"),
        "oracle_id": str(oracle.get("oracle_id") or _sha(dict(oracle))),
        "stages": {
            "document": {"hit": document_hit},
            "page": {"hit": page_hit},
            "table": {"hit": table_hit},
            "complete_operand_set": {
                "hit": complete_operand_set_hit,
                "set_count": len(operand_sets),
                "per_set_hits": operand_set_hits,
            },
        },
        "status": "REACHABLE" if complete_operand_set_hit else "RETRIEVAL_GAP",
        "answer_metric_emitted": False,
        "training_eligible": False,
        "promotion_allowed": False,
    }
    return {"diagnostic_id": _sha(payload), **payload}
