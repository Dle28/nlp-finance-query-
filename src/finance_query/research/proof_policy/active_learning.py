"""Versioned, fail-closed active-learning control plane for ViFinQA.

The learner operates on proof-policy patterns, never on numeric answers. It
selects a small representative review batch, learns only provisional policy
candidates from independently sourced decisions, and keeps promotion blocked
until a held-out Wilson lower-bound gate passes. Exact-cell and Decimal checks
remain per-question deterministic obligations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .evidence_closure import canonical_sha256, load_json, load_jsonl, sha256_file, source_contract


PROTOCOL = "vifinqa_active_learning_cycle_v1"
CLUSTER_PROTOCOL = "vifinqa_active_learning_cluster_inventory_v1"
REVIEW_BATCH_PROTOCOL = "vifinqa_active_learning_review_batch_v1"
POLICY_PROTOCOL = "vifinqa_learned_policy_candidate_v1"
TRAINING_PROTOCOL = "vifinqa_trusted_training_registry_v1"
DECISION_PROTOCOL = "vifinqa_active_learning_review_decision_v1"
AUDIT_LEDGER_PROTOCOL = "vifinqa_active_learning_audit_ledger_v1"
AUTHORITY_REGISTRY_PROTOCOL = "vifinqa_active_learning_reviewer_authority_registry_v1"
MODEL_POLICY_PROTOCOL = "vifinqa_open_source_model_policy_v1"
MAX_MODEL_PARAMETERS_BILLIONS = 14.7

QUEUE_OUTPUTS = {
    "formula_definition": "formula_definition_receipt_intake",
    "operand_compatibility": "operand_compatibility_receipt_intake",
    "route_binding": "route_binding_receipt_intake",
    "route_operator": "route_operator_receipt_intake",
    "route_cause": "route_cause_investigation_intake",
    "temporal": "temporal_receipt_intake",
    "v12_recertification": "v12_candidate_recertification_intake",
}


def _required_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _config_path(config_path: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"active-learning config missing {label}")
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    # Production configs live under ``configs/research/proof_policy`` and use
    # repository-root-relative inputs.  Hermetic tests use a small temporary
    # ``configs/`` folder, where the historical two-level-relative contract is
    # retained intentionally.
    for parent in config_path.resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent / candidate
    return config_path.parent.parent / candidate


def _load_config(config_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    config = load_json(config_path)
    if config.get("protocol") != PROTOCOL or config.get("mode") != "offline_shadow_active_learning":
        raise ValueError("invalid active-learning config")
    raw_paths = config.get("input_paths")
    hashes = config.get("locked_input_sha256")
    if not isinstance(raw_paths, Mapping) or not isinstance(hashes, Mapping) or set(raw_paths) != set(hashes):
        raise ValueError("active-learning config must pin every input path and hash")
    if "closure_manifest" not in raw_paths:
        raise ValueError("active-learning config requires closure_manifest")
    paths = {name: _config_path(config_path, value, name) for name, value in raw_paths.items()}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing active-learning input {name}: {path}")
        _required_hash(path, hashes.get(name), name)
    return config, paths


def _output_path(manifest: Mapping[str, Any], name: str) -> Path:
    record = (manifest.get("outputs") or {}).get(name)
    if not isinstance(record, Mapping):
        raise ValueError(f"closure manifest missing output {name}")
    path = Path(str(record.get("path") or ""))
    if not path.is_file() or sha256_file(path) != record.get("sha256"):
        raise ValueError(f"closure output hash mismatch: {name}")
    return path


def _index(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value in result:
            raise ValueError(f"invalid or duplicate {label} key")
        result[value] = dict(row)
    return result


def _feature_payload(queue: str, row: Mapping[str, Any]) -> dict[str, Any]:
    plan = row.get("operation_plan") if isinstance(row.get("operation_plan"), Mapping) else {}
    candidate = row.get("formula_evidence_candidate") if isinstance(row.get("formula_evidence_candidate"), Mapping) else {}
    if queue == "formula_definition":
        return {
            "queue": queue,
            "required_operations": sorted(str(value) for value in row.get("required_operations") or []),
            "family": plan.get("effective_family"),
            "formula_id": plan.get("formula_id"),
            "decomposition_status": plan.get("decomposition_status"),
            "candidate_status": candidate.get("status"),
            "candidate_definition_status": candidate.get("definition_status"),
        }
    if queue == "operand_compatibility":
        return {
            "queue": queue,
            "family": plan.get("effective_family"),
            "formula_id": plan.get("formula_id"),
            "decomposition_status": plan.get("decomposition_status"),
            "operand_count_bucket": min(int(plan.get("operand_count") or 0), 8),
            "compatibility_required": bool(row.get("compatibility_required")),
            "candidate_status": candidate.get("status"),
        }
    if queue in {"route_binding", "route_operator", "route_cause"}:
        return {
            "queue": queue,
            "primary_blocker": row.get("primary_blocker"),
            "missing_operations": sorted(str(value) for value in row.get("missing_operations") or []),
            "covered_operations": sorted(str(value) for value in row.get("covered_operations") or []),
            "route_reason_codes": sorted(str(value) for value in row.get("route_reason_codes") or []),
            "family": plan.get("effective_family"),
            "formula_id": plan.get("formula_id"),
        }
    if queue == "temporal":
        claim = row.get("claim_temporal_requirement") if isinstance(row.get("claim_temporal_requirement"), Mapping) else {}
        source = row.get("source_temporal_observation") if isinstance(row.get("source_temporal_observation"), Mapping) else {}
        return {
            "queue": queue,
            "kind": claim.get("kind"),
            "role": claim.get("role"),
            "requested_year_count": len(claim.get("requested_years") or []),
            "source_period_types": sorted(str(value) for value in source.get("period_types") or []),
            "source_expression_present": bool(source.get("source_expressions")),
        }
    if queue == "v12_recertification":
        pending = [item for item in row.get("pending_v13_obligations") or [] if isinstance(item, Mapping)]
        return {
            "queue": queue,
            "pending_dimensions": sorted(str(item.get("dimension")) for item in pending),
            "pending_statuses": sorted(str(item.get("status")) for item in pending),
        }
    raise ValueError(f"unknown active-learning queue {queue}")


def _candidate_priority(row: Mapping[str, Any]) -> tuple[int, int]:
    candidate = row.get("formula_evidence_candidate")
    available = isinstance(candidate, Mapping) and candidate.get("status") == "AVAILABLE_NON_AUTHORIZING"
    return (0 if available else 1, int(row["question_id"]))


def _review_assignment(role_index: int) -> tuple[str, list[str]]:
    reviewers = [
        "open_source_model_proposer",
        "human_adjudicator",
        "chatgpt_human_equivalent_reviewer",
    ]
    if role_index == 0:
        return "learn_seed", reviewers
    if role_index == 1:
        return "learn_seed", reviewers
    if role_index == 2:
        return "learn_validation", reviewers
    return "learn_validation", reviewers


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _derived_source_group(row: Mapping[str, Any]) -> dict[str, str] | None:
    """Use only packet lineage; reviewers may never declare their own group."""
    candidates = [row]
    for key in ("source_value_cell", "source_anchor", "lineage"):
        value = row.get(key)
        if isinstance(value, Mapping):
            candidates.append(value)
    issuer = next((str(item.get("issuer")) for item in candidates if item.get("issuer")), "")
    document_uid = next((str(item.get("document_uid")) for item in candidates if item.get("document_uid")), "")
    if issuer and document_uid:
        return {"issuer": issuer, "document_uid": document_uid}
    return None


def _bound_review_item(payload: Mapping[str, Any]) -> dict[str, Any]:
    packet_hash = canonical_sha256(payload)
    return {**payload, "packet_payload_sha256": packet_hash, "review_item_id": packet_hash}


def _select_queue(
    queue: str,
    members_by_cluster: Mapping[str, Sequence[Mapping[str, Any]]],
    budget: int,
    raw_claims: Mapping[int, Mapping[str, Any]],
    excluded_question_ids: set[int],
) -> list[dict[str, Any]]:
    ordered_clusters = sorted(members_by_cluster, key=lambda cluster: (-len(members_by_cluster[cluster]), cluster))
    selected: list[dict[str, Any]] = []
    offsets = {cluster: 0 for cluster in ordered_clusters}
    # First maximize pattern diversity, then add seed/holdout support to the
    # largest patterns. This keeps a small batch useful for both discovery and
    # later policy calibration.
    while len(selected) < budget:
        progressed = False
        for cluster in ordered_clusters:
            members = [
                item for item in sorted(members_by_cluster[cluster], key=_candidate_priority)
                if int(item["question_id"]) not in excluded_question_ids
            ]
            offset = offsets[cluster]
            if offset >= len(members) or len(selected) >= budget:
                continue
            row = members[offset]
            assignment_role, reviewers = _review_assignment(offset)
            qid = int(row["question_id"])
            payload = {
                "schema_version": 1,
                "protocol": REVIEW_BATCH_PROTOCOL,
                "question_id": qid,
                "queue": queue,
                "cluster_id": cluster,
                "cluster_size": len(members),
                "source_packet_id": row.get("packet_id"),
                "independent_requirement_packet_id": (raw_claims.get(qid) or {}).get("packet_id"),
                "allowed_source_ref_sha256": sorted({
                    value for value in (
                        row.get("packet_id"),
                        (raw_claims.get(qid) or {}).get("packet_id"),
                        (raw_claims.get(qid) or {}).get("raw_claim_sha256"),
                    ) if _is_sha256(value)
                }),
                "source_group": _derived_source_group(row),
                "raw_claim": (raw_claims.get(qid) or {}).get("raw_claim"),
                "assignment_role": assignment_role,
                "evaluation_role": "active_learning",
                "inclusion_probability": None,
                "required_reviewer_types": reviewers,
                "consensus_reviewer_types": ["open_source_model_proposer"],
                "review_status": "PENDING",
                "decision_protocol": DECISION_PROTOCOL,
                "source_contract": source_contract(),
            }
            selected.append(_bound_review_item(payload))
            offsets[cluster] += 1
            progressed = True
        if not progressed:
            break
    return selected


def _select_population_audit(
    *,
    question_ids: set[int],
    excluded_question_ids: set[int],
    budget: int,
    seed: str,
    raw_claims: Mapping[int, Mapping[str, Any]],
    packet_by_question: Mapping[int, Mapping[str, Any]],
    queue_by_question: Mapping[int, str],
    cluster_by_question: Mapping[int, str],
) -> list[dict[str, Any]]:
    eligible = sorted(question_ids - excluded_question_ids)
    if budget < 0 or budget > len(eligible):
        raise ValueError("invalid independent population-audit budget")
    ordered = sorted(eligible, key=lambda qid: canonical_sha256({"seed": seed, "question_id": qid}))
    inclusion_probability = budget / len(eligible) if eligible else 0.0
    selected: list[dict[str, Any]] = []
    for qid in ordered[:budget]:
        source = packet_by_question[qid]
        payload = {
            "schema_version": 1,
            "protocol": REVIEW_BATCH_PROTOCOL,
            "question_id": qid,
            "queue": queue_by_question[qid],
            "cluster_id": cluster_by_question[qid],
            "cluster_size": None,
            "source_packet_id": source.get("packet_id"),
            "independent_requirement_packet_id": (raw_claims.get(qid) or {}).get("packet_id"),
            "allowed_source_ref_sha256": sorted({
                value for value in (
                    source.get("packet_id"),
                    (raw_claims.get(qid) or {}).get("packet_id"),
                    (raw_claims.get(qid) or {}).get("raw_claim_sha256"),
                ) if _is_sha256(value)
            }),
            "source_group": _derived_source_group(source),
            "raw_claim": (raw_claims.get(qid) or {}).get("raw_claim"),
            "assignment_role": "population_audit",
            "evaluation_role": "independent_population_audit",
            "inclusion_probability": inclusion_probability,
            "sampling_seed_sha256": canonical_sha256(seed),
            "required_reviewer_types": ["independent_auditor", "chatgpt_human_equivalent_reviewer"],
            "consensus_reviewer_types": ["independent_auditor"],
            "review_status": "PENDING",
            "decision_protocol": DECISION_PROTOCOL,
            "source_contract": source_contract(),
        }
        selected.append(_bound_review_item(payload))
    return selected


def wilson_lower_bound(successes: int, total: int, *, z: float = 1.96) -> float:
    if total <= 0 or successes < 0 or successes > total:
        return 0.0
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    adjustment = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
    return (centre - adjustment) / denominator


def _model_policy(path: Path) -> dict[str, dict[str, Any]]:
    policy = load_json(path)
    if policy.get("protocol") != MODEL_POLICY_PROTOCOL or float(policy.get("strict_parameter_cap_billions") or 0) != MAX_MODEL_PARAMETERS_BILLIONS:
        raise ValueError("invalid open-source model policy")
    routes: dict[str, dict[str, Any]] = {}
    for route in policy.get("routes") or []:
        route_id = str(route.get("route_id") or "")
        parameters = float(route.get("parameter_count_billions") or 0)
        if (
            not route_id
            or route_id in routes
            or not route.get("open_weights")
            or not str(route.get("license") or "")
            or not re.fullmatch(r"[0-9a-f]{40}", str(route.get("revision") or ""))
            or not 0 < parameters < MAX_MODEL_PARAMETERS_BILLIONS
        ):
            raise ValueError("open-source model route violates the strict <14.7B policy")
        shards = route.get("weight_shards")
        if (
            not isinstance(shards, list)
            or not shards
            or any(
                not isinstance(shard, Mapping)
                or not str(shard.get("filename") or "").endswith(".safetensors")
                or not _is_sha256(shard.get("sha256"))
                for shard in shards
            )
        ):
            raise ValueError("open-source model route lacks pinned weight-shard hashes")
        routes[route_id] = dict(route)
    if not routes:
        raise ValueError("open-source model policy has no eligible route")
    return routes


def _authority_index(path: Path, model_routes: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    registry = load_json(path)
    if registry.get("protocol") != AUTHORITY_REGISTRY_PROTOCOL:
        raise ValueError("invalid reviewer authority registry")
    result: dict[str, dict[str, Any]] = {}
    for reviewer in registry.get("reviewers") or []:
        reviewer_id = str(reviewer.get("reviewer_id") or "")
        reviewer_type = str(reviewer.get("reviewer_type") or "")
        receipt = reviewer.get("authority_receipt_sha256")
        if not reviewer_id or not reviewer_type or not _is_sha256(receipt) or reviewer_id in result:
            raise ValueError("invalid or duplicate reviewer authority")
        if reviewer_type == "open_source_model_proposer":
            route_id = str(reviewer.get("model_route_id") or "")
            if route_id not in model_routes:
                raise ValueError("model reviewer is not bound to an eligible open-source <14.7B route")
        elif reviewer.get("model_route_id") is not None:
            raise ValueError("non-model reviewer cannot be bound to a competition model route")
        if reviewer_type == "chatgpt_human_equivalent_reviewer" and (
            reviewer.get("training_authority") is not False
            or reviewer.get("competition_model_eligible") is not False
            or reviewer.get("review_only") is not True
        ):
            raise ValueError("ChatGPT authority must remain review-only and outside the competition model")
        result[reviewer_id] = dict(reviewer)
    return result


def _load_prior_audit(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    rows = load_jsonl(path)
    seen: set[int] = set()
    for row in rows:
        qid = row.get("question_id")
        if row.get("protocol") != AUDIT_LEDGER_PROTOCOL or not isinstance(qid, int) or qid in seen:
            raise ValueError("invalid or duplicate prior audit-ledger row")
        if row.get("correctness_label") not in {"CORRECT", "INCORRECT"}:
            raise ValueError("prior audit ledger lacks independent correctness label")
        seen.add(qid)
    return rows


def _decision_registry(
    decisions_path: Path | None,
    selected: Sequence[Mapping[str, Any]],
    clusters: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    authority_registry: Mapping[str, Mapping[str, Any]],
    prior_audit: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected_by_id = {str(item["review_item_id"]): item for item in selected}
    decisions = load_jsonl(decisions_path) if decisions_path is not None else []
    seen: set[tuple[str, str]] = set()
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    training: list[dict[str, Any]] = []
    validated_decisions: list[dict[str, Any]] = []
    for decision in decisions:
        if decision.get("protocol") != DECISION_PROTOCOL:
            raise ValueError("unexpected active-learning decision protocol")
        review_id = str(decision.get("review_item_id") or "")
        reviewer_id = str(decision.get("reviewer_id") or "")
        reviewer_type = str(decision.get("reviewer_type") or "")
        source_item = selected_by_id.get(review_id)
        if source_item is None or not reviewer_id or (review_id, reviewer_id) in seen:
            raise ValueError("decision is not uniquely bound to the selected review batch")
        seen.add((review_id, reviewer_id))
        authority = authority_registry.get(reviewer_id)
        if (
            authority is None
            or authority.get("reviewer_type") != reviewer_type
            or authority.get("authority_receipt_sha256") != decision.get("reviewer_authority_receipt_sha256")
            or reviewer_type not in (source_item.get("required_reviewer_types") or [])
            or authority.get("enabled_for_decisions") is not True
        ):
            raise ValueError("reviewer type or authority is not permitted for this review item")
        if decision.get("packet_payload_sha256") != source_item.get("packet_payload_sha256"):
            raise ValueError("decision is not bound to the immutable review packet")
        verdict = str(decision.get("verdict") or "")
        if verdict not in {"ACCEPT_PROPOSAL", "REJECT_PROPOSAL", "NEEDS_EVIDENCE"}:
            raise ValueError("invalid active-learning verdict")
        refs = sorted(str(value) for value in decision.get("source_refs") or [])
        allowed_refs = set(source_item.get("allowed_source_ref_sha256") or [])
        if verdict != "NEEDS_EVIDENCE" and (
            not refs or any(not _is_sha256(value) or value not in allowed_refs for value in refs)
        ):
            raise ValueError("review decisions require packet-bound SHA-256 source references")
        if int(decision.get("question_id") or -1) != int(source_item["question_id"]):
            raise ValueError("decision question ID differs from its review item")
        if "source_group" in decision:
            raise ValueError("source_group is derived from lineage and cannot be reviewer-supplied")
        correctness = str(decision.get("correctness_label") or "UNESTABLISHED")
        if source_item.get("evaluation_role") == "independent_population_audit":
            if correctness not in {"CORRECT", "INCORRECT"}:
                raise ValueError("population audit requires an independent correctness label")
        elif correctness != "UNESTABLISHED" and reviewer_type not in {"human_adjudicator", "chatgpt_human_equivalent_reviewer"}:
            raise ValueError("open-source proposer cannot establish correctness")
        normalized = {
            **decision,
            "cluster_id": source_item["cluster_id"],
            "queue": source_item["queue"],
            "assignment_role": source_item["assignment_role"],
            "evaluation_role": source_item["evaluation_role"],
            "reviewer_type": reviewer_type,
            "correctness_label": correctness,
            "source_group": source_item.get("source_group"),
        }
        validated_decisions.append(normalized)
        by_cluster[str(source_item["cluster_id"])].append(normalized)
    decisions_by_review: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in validated_decisions:
        decisions_by_review[str(decision["review_item_id"])].append(decision)
    for review_id, item_decisions in decisions_by_review.items():
        lanes = {str(item["reviewer_type"]): item for item in item_decisions}
        proposer = lanes.get("open_source_model_proposer")
        adjudicator = lanes.get("human_adjudicator")
        if proposer is None or adjudicator is None:
            continue
        policies_match = (
            proposer.get("verdict") == adjudicator.get("verdict")
            and isinstance(proposer.get("approved_policy"), Mapping)
            and canonical_sha256(proposer.get("approved_policy")) == canonical_sha256(adjudicator.get("approved_policy"))
        )
        authority = authority_registry[str(adjudicator["reviewer_id"])]
        if not policies_match or adjudicator.get("correctness_label") not in {"CORRECT", "INCORRECT"} or "proposal_training_adjudication" not in set(authority.get("authority_scopes") or []):
            continue
        source_item = selected_by_id[review_id]
        payload = {
            "schema_version": 1,
            "protocol": TRAINING_PROTOCOL,
            "question_id": int(source_item["question_id"]),
            "review_item_id": review_id,
            "cluster_id": source_item["cluster_id"],
            "queue": source_item["queue"],
            "verdict": adjudicator["verdict"],
            "approved_policy": adjudicator.get("approved_policy"),
            "reviewer_id": adjudicator["reviewer_id"],
            "reviewer_type": adjudicator["reviewer_type"],
            "source_refs": adjudicator["source_refs"],
            "source_group": source_item.get("source_group"),
            "correctness_label": adjudicator["correctness_label"],
            "provenance_status": "authorized_adjudicated",
            "training_eligible": True,
            "materialization_eligible": False,
            "submission_eligible": False,
        }
        training.append({**payload, "training_record_id": canonical_sha256(payload)})
    policy_config = config.get("policy_learning") or {}
    minimum_seed = int(policy_config.get("minimum_seed_decisions_per_cluster") or 2)
    minimum_holdout = int(policy_config.get("minimum_holdout_decisions_per_cluster") or 1)
    policies = []
    for cluster in clusters:
        cluster_id = str(cluster["cluster_id"])
        values = by_cluster.get(cluster_id, [])
        by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in values:
            by_item[str(value["review_item_id"])].append(value)
        consensus: list[dict[str, Any]] = []
        disagreements = 0
        for item_values in by_item.values():
            lane = {str(item["reviewer_type"]): item for item in item_values}
            proposer = lane.get("open_source_model_proposer")
            if proposer is None:
                continue
            if proposer.get("verdict") == "ACCEPT_PROPOSAL" and isinstance(proposer.get("approved_policy"), Mapping):
                consensus.append(proposer)
        accepted = consensus
        rejected = [item for item in values if item.get("verdict") == "REJECT_PROPOSAL"]
        policy_hashes = Counter(
            canonical_sha256(item.get("approved_policy")) for item in accepted if isinstance(item.get("approved_policy"), Mapping)
        )
        seed_count = sum(item.get("assignment_role") == "learn_seed" for item in accepted)
        holdout_count = sum(item.get("assignment_role") == "learn_validation" for item in accepted)
        reviewer_types = sorted({str(item.get("reviewer_type") or "") for item in values if item.get("reviewer_type")})
        consistent = len(policy_hashes) == 1 and not rejected
        learned = consistent and seed_count >= minimum_seed and holdout_count >= minimum_holdout
        payload = {
            "schema_version": 1,
            "protocol": POLICY_PROTOCOL,
            "cluster_id": cluster_id,
            "queue": cluster["queue"],
            "cluster_size": cluster["question_count"],
            "review_decision_count": len(values),
            "same_item_consensus_count": len(consensus),
            "same_item_disagreement_count": disagreements,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "seed_approved_count": seed_count,
            "holdout_approved_count": holdout_count,
            "reviewer_types": reviewer_types,
            "policy_agreement": "CONSISTENT" if consistent else "UNESTABLISHED",
            "policy_status": "MACHINE_PROVISIONAL" if learned else "UNTRAINED_OR_UNCALIBRATED",
            "approved_policy_sha256": next(iter(policy_hashes), None) if learned else None,
            "promotion_allowed": False,
            "submission_eligible": False,
            "source_contract": source_contract(),
        }
        policies.append({**payload, "policy_candidate_id": canonical_sha256(payload)})
    calibration_config = config.get("calibration_gate") or {}
    current_audit: list[dict[str, Any]] = []
    prior_qids = {int(item["question_id"]) for item in prior_audit}
    for item in validated_decisions:
        if item.get("evaluation_role") != "independent_population_audit":
            continue
        qid = int(item["question_id"])
        if qid in prior_qids:
            raise ValueError("population audit repeats a question from the prior ledger")
        source_item = selected_by_id[str(item["review_item_id"])]
        payload = {
            "schema_version": 1,
            "protocol": AUDIT_LEDGER_PROTOCOL,
            "question_id": qid,
            "review_item_id": item["review_item_id"],
            "correctness_label": item["correctness_label"],
            "inclusion_probability": source_item["inclusion_probability"],
            "sampling_seed_sha256": source_item["sampling_seed_sha256"],
            "reviewer_id": item["reviewer_id"],
            "reviewer_authority_receipt_sha256": item["reviewer_authority_receipt_sha256"],
            "source_group": source_item.get("source_group"),
        }
        current_audit.append({**payload, "audit_record_id": canonical_sha256(payload)})
    audit_ledger = [dict(item) for item in prior_audit] + current_audit
    if len({int(item["question_id"]) for item in audit_ledger}) != len(audit_ledger):
        raise ValueError("audit ledger contains duplicate questions")
    successes = sum(item.get("correctness_label") == "CORRECT" for item in audit_ledger)
    total = len(audit_ledger)
    lower = wilson_lower_bound(successes, total)
    minimum = int(calibration_config.get("minimum_independent_holdout_decisions") or 125)
    threshold = float(calibration_config.get("minimum_wilson_lower_bound") or 0.97)
    train_groups = {
        canonical_sha256(item.get("source_group"))
        for item in training if isinstance(item.get("source_group"), Mapping)
    }
    audit_groups = {
        canonical_sha256(item.get("source_group"))
        for item in audit_ledger
        if isinstance(item.get("source_group"), Mapping)
    }
    group_overlap = sorted(train_groups.intersection(audit_groups))
    candidate_policy_count = sum(item["policy_status"] == "MACHINE_PROVISIONAL" for item in policies)
    source_groups_established = bool(audit_groups) and all(isinstance(item.get("source_group"), Mapping) for item in audit_ledger)
    promotion_ready = total >= minimum and lower >= threshold and candidate_policy_count > 0 and not group_overlap and source_groups_established
    gate = {
        "calibration_successes": successes,
        "calibration_total": total,
        "wilson_lower_bound_95": lower,
        "minimum_independent_holdout_decisions": minimum,
        "minimum_wilson_lower_bound": threshold,
        "candidate_policy_count": candidate_policy_count,
        "source_group_overlap_count": len(group_overlap),
        "source_group_holdout_status": "PASS" if source_groups_established and not group_overlap else "NOT_ESTABLISHED",
        "population_audit_sampling": "probability_sample_frozen_before_review",
        "promotion_status": "ELIGIBLE_FOR_EXPLICIT_PROMOTION_REVIEW" if promotion_ready else "BLOCKED",
        "automatic_promotion": False,
        "online_weight_update": False,
    }
    return sorted(training, key=lambda item: (item["question_id"], item["reviewer_id"])), policies, sorted(audit_ledger, key=lambda item: item["question_id"]), gate


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def build_active_learning_cycle(*, config_path: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite active-learning cycle: {output_dir}")
    config, inputs = _load_config(config_path)
    if "reviewer_authority_registry" not in inputs or "open_source_model_policy" not in inputs:
        raise ValueError("active learning requires pinned reviewer authority and open-source model policies")
    model_routes = _model_policy(inputs["open_source_model_policy"])
    authority_registry = _authority_index(inputs["reviewer_authority_registry"], model_routes)
    prior_audit = _load_prior_audit(inputs.get("prior_audit_ledger"))
    closure = load_json(inputs["closure_manifest"])
    if closure.get("protocol") != "vifinqa_v13_evidence_closure_workbench_v1":
        raise ValueError("active learning requires the closure-workbench protocol")
    if (closure.get("release_decision") or {}).get("status") != "blocked":
        raise ValueError("active learning cannot consume an authorizing closure artifact")
    raw_claim_rows = _index(load_jsonl(_output_path(closure, "independent_requirement_review_packets")), key="question_id", label="independent packet")
    ledger_rows = _index(load_jsonl(_output_path(closure, "production_execution_ledger_intake")), key="question_id", label="ledger intake")
    question_ids = set(raw_claim_rows)
    if len(question_ids) != 1012 or set(ledger_rows) != question_ids:
        raise ValueError("active-learning universe must cover the same 1,012 questions")
    queue_rows: dict[str, list[dict[str, Any]]] = {}
    question_queue: dict[int, str] = {}
    packet_by_question: dict[int, dict[str, Any]] = {}
    cluster_by_question: dict[int, str] = {}
    cluster_members: dict[str, dict[str, list[dict[str, Any]]]] = {}
    clusters: list[dict[str, Any]] = []
    for queue, output_name in QUEUE_OUTPUTS.items():
        rows = load_jsonl(_output_path(closure, output_name))
        queue_rows[queue] = rows
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            qid = int(row["question_id"])
            if qid in question_queue:
                raise ValueError(f"Q{qid} appears in multiple active-learning queues")
            question_queue[qid] = queue
            packet_by_question[qid] = row
            features = _feature_payload(queue, row)
            cluster_id = canonical_sha256(features)
            cluster_by_question[qid] = cluster_id
            grouped[cluster_id].append(row)
        cluster_members[queue] = grouped
        for cluster_id, members in grouped.items():
            features = _feature_payload(queue, members[0])
            payload = {
                "schema_version": 1,
                "protocol": CLUSTER_PROTOCOL,
                "cluster_id": cluster_id,
                "queue": queue,
                "features": features,
                "question_count": len(members),
                "representative_question_ids": [int(item["question_id"]) for item in sorted(members, key=_candidate_priority)[:5]],
                "learning_target": "proof_policy_not_numeric_answer",
                "source_contract": source_contract(),
            }
            clusters.append(payload)
    if set(question_queue) != question_ids:
        raise ValueError("active-learning queues do not partition all 1,012 questions")
    budgets = config.get("review_budget_by_queue")
    if not isinstance(budgets, Mapping) or set(budgets) != set(QUEUE_OUTPUTS):
        raise ValueError("review budget must cover every active-learning queue")
    sampling = config.get("sampling_policy") or {}
    audit_budget = int(sampling.get("population_audit_budget") or 0)
    audit_seed = str(sampling.get("population_audit_seed") or "")
    if audit_budget and not audit_seed:
        raise ValueError("population audit requires a frozen sampling seed")
    audit_selected = _select_population_audit(
        question_ids=question_ids,
        excluded_question_ids={int(item["question_id"]) for item in prior_audit},
        budget=audit_budget,
        seed=audit_seed,
        raw_claims=raw_claim_rows,
        packet_by_question=packet_by_question,
        queue_by_question=question_queue,
        cluster_by_question=cluster_by_question,
    )
    # Freeze the probability audit before information-gain sampling. This keeps
    # every not-yet-audited question eligible for population-risk estimation.
    active_selected: list[dict[str, Any]] = []
    audit_qids = {int(item["question_id"]) for item in audit_selected}
    for queue in QUEUE_OUTPUTS:
        budget = int(budgets[queue])
        if budget < 0 or budget > len(queue_rows[queue]):
            raise ValueError(f"invalid review budget for {queue}")
        active_selected.extend(_select_queue(queue, cluster_members[queue], budget, raw_claim_rows, audit_qids))
    selected = active_selected + audit_selected
    selected.sort(key=lambda item: (item["evaluation_role"], item["queue"], item["cluster_id"], item["assignment_role"], item["question_id"]))
    decisions_path = inputs.get("review_decisions")
    training, policies, audit_ledger, calibration_gate = _decision_registry(
        decisions_path, selected, clusters, config, authority_registry, prior_audit
    )
    review_budget = len(selected)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "ACTIVE_LEARNING_CYCLE_READY_FOR_REVIEW" if not training else "ACTIVE_LEARNING_CYCLE_SCORED_NOT_PROMOTED",
        "counts": {
            "question_count": len(question_ids),
            "cluster_count": len(clusters),
            "selected_review_count": review_budget,
            "active_learning_review_count": len(active_selected),
            "population_audit_review_count": len(audit_selected),
            "prior_population_audit_count": len(prior_audit),
            "cumulative_population_audit_count": len(audit_ledger),
            "trusted_training_record_count": len(training),
            "machine_provisional_policy_count": sum(item["policy_status"] == "MACHINE_PROVISIONAL" for item in policies),
            "queue_counts": {queue: len(rows) for queue, rows in queue_rows.items()},
            "review_budget_by_queue": {queue: int(budgets[queue]) for queue in QUEUE_OUTPUTS},
        },
        "human_effort": {
            "flat_review_baseline": len(question_ids),
            "cycle_review_budget": review_budget,
            "review_reduction_fraction": 1 - review_budget / len(question_ids),
            "claim": "first-cycle review reduction only; not release coverage",
        },
        "calibration_gate": calibration_gate,
        "release_decision": {
            "status": "blocked",
            "reason_codes": ["ACTIVE_LEARNING_SHADOW_ONLY", "POLICY_PROMOTION_REQUIRES_EXPLICIT_APPROVAL", "EXACT_CELL_AND_EXECUTION_REMAIN_PER_QUESTION"],
        },
        "learning_contract": {
            "learning_mode": "offline_versioned_active_learning",
            "learned_target": "proof_policy_component",
            "competition_model_policy": "open_source_weights_strictly_below_14.7B_parameters",
            "competition_model_route_count": len(model_routes),
            "chatgpt_competition_model_eligible": False,
            "chatgpt_training_or_inference_allowed": False,
            "chatgpt_role": "external_human_equivalent_review_only",
            "numeric_answer_learning": False,
            "online_self_training": False,
            "self_label_feedback": False,
            "active_sampling_may_estimate_population_risk": False,
            "population_audit_lane_independent": True,
            "population_audit_frozen_before_active_sampling": True,
            "calibration_target": "independently_adjudicated_correctness_not_accept_rate",
            "exact_cell_validation_per_question": True,
            "decimal_execution_deterministic": True,
        },
        "source_contract": source_contract(),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        outputs_and_rows = {
            "cluster_inventory": ("active_learning_cluster_inventory_v1.jsonl", sorted(clusters, key=lambda item: (item["queue"], item["cluster_id"]))),
            "review_batch": ("active_learning_review_batch_v1.jsonl", selected),
            "trusted_training_registry": ("trusted_training_registry_v1.jsonl", training),
            "learned_policy_candidates": ("learned_policy_candidates_v1.jsonl", sorted(policies, key=lambda item: (item["queue"], item["cluster_id"]))),
            "audit_ledger": ("active_learning_audit_ledger_v1.jsonl", audit_ledger),
        }
        output_paths: dict[str, Path] = {}
        for name, (filename, rows) in outputs_and_rows.items():
            output_paths[name] = staging / filename
            _write_jsonl(output_paths[name], rows)
        summary_path = staging / "active_learning_cycle_summary.json"
        _write_json(summary_path, summary)
        definition = {
            "implementation_path": str(Path(__file__).resolve()),
            "implementation_sha256": sha256_file(Path(__file__)),
            "config_sha256": sha256_file(config_path),
        }
        manifest = {
            **summary,
            "definition_bundle": definition,
            "inputs": {
                name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
                for name, path in {"config": config_path, **inputs}.items()
            },
            "outputs": {
                name: {"path": str(output_dir / path.name), "sha256": sha256_file(path)}
                for name, path in output_paths.items()
            } | {"summary": {"path": str(output_dir / summary_path.name), "sha256": sha256_file(summary_path)}},
        }
        manifest_path = staging / "active_learning_cycle.manifest.json"
        _write_json(manifest_path, manifest)
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**manifest, "manifest_path": str(output_dir / "active_learning_cycle.manifest.json")}
