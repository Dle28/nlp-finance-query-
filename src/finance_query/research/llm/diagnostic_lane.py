"""Bounded proposer-only diagnostic lane for deciding whether an LLM is useful.

The operational pipeline stays deterministic-first.  This module selects a
small, queue-stratified subset from an existing non-authorizing model job and
scores validated proposer responses against independent human review.  It
cannot create training data, certification, promotion, release, or answers.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from ..proof_policy.active_learning_models import (
    MODEL_JOB_PROTOCOL,
    MODEL_ROLES,
    VALIDATED_RESPONSE_PROTOCOL,
)
from ..proof_policy.evidence_closure import canonical_sha256, load_json, load_jsonl, sha256_file


LLM_LANE_CONTROL_PROTOCOL = "vifinqa_llm_lane_control_v1"
LLM_DIAGNOSTIC_BATCH_PROTOCOL = "vifinqa_llm_proposer_diagnostic_batch_v1"
LLM_DIAGNOSTIC_REVIEW_PROTOCOL = "vifinqa_llm_diagnostic_human_review_v1"
LLM_DIAGNOSTIC_DECISION_PROTOCOL = "vifinqa_llm_operational_decision_v1"


@dataclass(frozen=True, slots=True)
class DiagnosticBatchResult:
    output_dir: Path
    manifest_path: Path
    packet_count: int


@dataclass(frozen=True, slots=True)
class DiagnosticDecisionResult:
    output_path: Path
    decision: str
    completed_human_review_count: int


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _record_path(record: object, *, base_dir: Path, label: str) -> Path:
    if not isinstance(record, Mapping):
        raise ValueError(f"missing artifact record: {label}")
    path = Path(str(record.get("path") or ""))
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file() or sha256_file(path) != record.get("sha256"):
        raise ValueError(f"artifact hash mismatch: {label}")
    return path


def _control(path: Path) -> dict[str, Any]:
    value = load_json(path)
    if (
        value.get("protocol") != LLM_LANE_CONTROL_PROTOCOL
        or value.get("mode") != "paused_except_bounded_diagnostic"
        or value.get("default_operational_llm_enabled") is not False
    ):
        raise ValueError("invalid LLM lane control policy")
    diagnostic = value.get("diagnostic")
    thresholds = value.get("decision_thresholds")
    if not isinstance(diagnostic, Mapping) or not isinstance(thresholds, Mapping):
        raise ValueError("LLM lane policy lacks diagnostic or thresholds")
    if (
        diagnostic.get("proposer_only") is not True
        or diagnostic.get("selection_policy") != "queue_cluster_round_robin"
        or diagnostic.get("review_protocol") != "independent_counterbalanced_v1"
        or not 20 <= int(diagnostic.get("max_packets") or 0) <= 150
        or not 1 <= int(diagnostic.get("min_human_reviewed") or 0) <= int(diagnostic["max_packets"])
        or not 1 <= int(diagnostic.get("min_human_reviewed_proposals") or 0) <= int(diagnostic["min_human_reviewed"])
        or int(diagnostic.get("min_human_reviewed_for_keep") or 0) < 125
        or int(diagnostic.get("min_human_reviewed_for_keep") or 0) < int(diagnostic["min_human_reviewed"])
        or int(diagnostic.get("min_human_reviewed_proposals_for_keep") or 0) < 25
        or int(diagnostic.get("min_human_reviewed_proposals_for_keep") or 0)
        > int(diagnostic["min_human_reviewed_for_keep"])
    ):
        raise ValueError("unsafe or unsupported LLM diagnostic contract")
    bounded = (
        "min_schema_valid_rate",
        "min_valid_proposal_rate",
        "min_proposal_precision",
        "min_source_reference_exact_rate",
        "min_reviewer_time_reduction",
        "max_missed_blocker_rate",
        "max_missed_blocker_wilson_upper_95",
    )
    if any(
        isinstance(thresholds.get(name), bool)
        or not isinstance(thresholds.get(name), (int, float))
        or not 0 <= float(thresholds[name]) <= 1
        for name in bounded
    ):
        raise ValueError("LLM diagnostic thresholds must be numeric rates in [0, 1]")
    if value.get("training_eligible") is not False or value.get("release_authorized") is not False:
        raise ValueError("LLM lane policy must remain non-authorizing")
    return value


def _select_requests(requests: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_queue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for request in requests:
        if request.get("model_role") != MODEL_ROLES[0]:
            raise ValueError("diagnostic source includes a non-proposer request")
        review_id = str(request.get("review_item_id") or "")
        packet = request.get("packet")
        if not review_id or review_id in seen or not isinstance(packet, Mapping):
            raise ValueError("diagnostic source requests are not uniquely packet-bound")
        seen.add(review_id)
        by_queue[str(packet.get("queue") or "")].append(request)
    if "" in by_queue:
        raise ValueError("diagnostic source request lacks queue")
    for rows in by_queue.values():
        rows.sort(key=lambda row: (str(row["packet"].get("cluster_id") or ""), int(row["question_id"])))
    selected: list[dict[str, Any]] = []
    offsets = {queue: 0 for queue in by_queue}
    while len(selected) < min(limit, len(requests)):
        progressed = False
        for queue in sorted(by_queue):
            offset = offsets[queue]
            if offset >= len(by_queue[queue]) or len(selected) >= limit:
                continue
            selected.append(by_queue[queue][offset])
            offsets[queue] += 1
            progressed = True
        if not progressed:
            break
    return sorted(selected, key=lambda row: int(row["question_id"]))


def build_diagnostic_batch(
    *,
    source_model_job_manifest_path: Path,
    control_policy_path: Path,
    output_dir: Path,
) -> DiagnosticBatchResult:
    """Build one proposer-only diagnostic batch without invoking a model."""
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite diagnostic batch: {output_dir}")
    control = _control(control_policy_path)
    source = load_json(source_model_job_manifest_path)
    if (
        source.get("protocol") != MODEL_JOB_PROTOCOL
        or source.get("status") != "PREPARED_GPU_EXECUTION_NOT_RUN"
        or source.get("release_status") != "blocked"
    ):
        raise ValueError("diagnostic batch requires a blocked prepared model job")
    proposer_route = (source.get("model_routes") or {}).get(MODEL_ROLES[0])
    if not isinstance(proposer_route, Mapping):
        raise ValueError("source model job lacks proposer route")
    source_base = source_model_job_manifest_path.resolve().parent
    source_requests_path = _record_path(
        (source.get("outputs") or {}).get("proposer_requests"),
        base_dir=source_base,
        label="proposer_requests",
    )
    requests = _select_requests(
        load_jsonl(source_requests_path),
        int(control["diagnostic"]["max_packets"]),
    )
    if len(requests) < int(control["diagnostic"]["min_human_reviewed"]):
        raise ValueError("source job is too small for the diagnostic decision contract")
    review_template = [
        {
            "schema_version": 1,
            "protocol": LLM_DIAGNOSTIC_REVIEW_PROTOCOL,
            "review_item_id": request["review_item_id"],
            "question_id": request["question_id"],
            "queue": request["packet"]["queue"],
            "human_review_status": "PENDING",
            "comparison_design": None,
            "assignment_id": None,
            "baseline_reviewer_id": None,
            "assisted_reviewer_id": None,
            "comparison_order": None,
            "response_correct": None,
            "source_reference_exact": None,
            "missed_blocker": None,
            "baseline_review_seconds": None,
            "assisted_review_seconds": None,
            "notes": None,
        }
        for request in requests
    ]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        requests_path = staging / "proposer_diagnostic_requests_v1.jsonl"
        review_path = staging / "independent_human_review_template_v1.jsonl"
        _write_jsonl(requests_path, requests)
        _write_jsonl(review_path, review_template)
        summary = {
            "schema_version": 1,
            "protocol": LLM_DIAGNOSTIC_BATCH_PROTOCOL,
            "status": "PREPARED_PROPOSER_DIAGNOSTIC_NOT_RUN",
            "packet_count": len(requests),
            "queue_counts": dict(
                sorted(Counter(row["queue"] for row in review_template).items())
            ),
            "default_operational_llm_enabled": False,
            "proposer_only": True,
            "training_eligible": False,
            "certification_allowed": False,
            "release_status": "blocked",
        }
        summary_path = staging / "llm_diagnostic_summary.json"
        _write_json(summary_path, summary)
        manifest = {
            **summary,
            "inputs": {
                "source_model_job_manifest": {
                    "path": str(source_model_job_manifest_path.resolve()),
                    "sha256": sha256_file(source_model_job_manifest_path),
                },
                "control_policy": {
                    "path": str(control_policy_path.resolve()),
                    "sha256": sha256_file(control_policy_path),
                },
                "source_proposer_requests": {
                    "path": str(source_requests_path.resolve()),
                    "sha256": sha256_file(source_requests_path),
                },
            },
            "model_routes": {MODEL_ROLES[0]: dict(proposer_route)},
            "outputs": {
                "proposer_requests": {
                    "path": str(output_dir / requests_path.name),
                    "sha256": sha256_file(requests_path),
                },
                "human_review_template": {
                    "path": str(output_dir / review_path.name),
                    "sha256": sha256_file(review_path),
                },
                "summary": {
                    "path": str(output_dir / summary_path.name),
                    "sha256": sha256_file(summary_path),
                },
            },
        }
        manifest_path = staging / "llm_diagnostic_batch.manifest.json"
        _write_json(manifest_path, manifest)
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return DiagnosticBatchResult(output_dir, output_dir / manifest_path.name, len(requests))


def verify_diagnostic_batch(manifest_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    if (
        manifest.get("protocol") != LLM_DIAGNOSTIC_BATCH_PROTOCOL
        or manifest.get("status") != "PREPARED_PROPOSER_DIAGNOSTIC_NOT_RUN"
        or manifest.get("proposer_only") is not True
    ):
        raise ValueError("invalid proposer-only diagnostic manifest")
    base = manifest_path.resolve().parent
    for group in ("inputs", "outputs"):
        for name, record in (manifest.get(group) or {}).items():
            _record_path(record, base_dir=base, label=f"{group}.{name}")
    requests = load_jsonl(
        _record_path(
            manifest["outputs"]["proposer_requests"],
            base_dir=base,
            label="proposer_requests",
        )
    )
    reviews = load_jsonl(
        _record_path(
            manifest["outputs"]["human_review_template"],
            base_dir=base,
            label="human_review_template",
        )
    )
    if len(requests) != manifest.get("packet_count") or len(reviews) != len(requests):
        raise ValueError("diagnostic row counts differ")
    if any(row.get("model_role") != MODEL_ROLES[0] for row in requests):
        raise ValueError("diagnostic contains a non-proposer request")
    if {row["review_item_id"] for row in requests} != {row["review_item_id"] for row in reviews}:
        raise ValueError("diagnostic review template coverage differs")
    return {
        "status": "VERIFIED_PROPOSER_ONLY_DIAGNOSTIC_BATCH",
        "packet_count": len(requests),
        "gpu_execution_status": "not_run",
        "release_status": "blocked",
        "manifest_sha256": sha256_file(manifest_path),
    }


def _index(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in result:
            raise ValueError(f"{label} has invalid or duplicate {key}")
        result[value] = dict(row)
    return result


def _wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float] | None:
    """Return a 95% Wilson interval for a Bernoulli rate."""

    if total <= 0:
        return None
    proportion = successes / total
    z_squared = z * z
    denominator = 1 + z_squared / total
    center = (proportion + z_squared / (2 * total)) / denominator
    margin = z * math.sqrt(
        (proportion * (1 - proportion) + z_squared / (4 * total)) / total
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def evaluate_diagnostic_batch(
    *,
    diagnostic_manifest_path: Path,
    validated_responses_path: Path,
    human_reviews_path: Path,
    output_path: Path,
) -> DiagnosticDecisionResult:
    """Recommend keeping or removing the proposer from operational use."""
    verify_diagnostic_batch(diagnostic_manifest_path)
    manifest = load_json(diagnostic_manifest_path)
    base = diagnostic_manifest_path.resolve().parent
    control_path = _record_path(manifest["inputs"]["control_policy"], base_dir=base, label="control_policy")
    control = _control(control_path)
    requests = _index(
        load_jsonl(_record_path(manifest["outputs"]["proposer_requests"], base_dir=base, label="proposer_requests")),
        key="review_item_id",
        label="diagnostic requests",
    )
    responses = _index(
        load_jsonl(validated_responses_path),
        key="review_item_id",
        label="validated responses",
    )
    if set(responses) != set(requests):
        raise ValueError("validated responses must cover the diagnostic batch exactly")
    valid_statuses = {"VALID_PROPOSAL", "VALID_ABSTENTION"}
    for review_id, row in responses.items():
        if (
            row.get("protocol") != VALIDATED_RESPONSE_PROTOCOL
            or row.get("model_role") != MODEL_ROLES[0]
            or row.get("question_id") != requests[review_id].get("question_id")
            or row.get("training_eligible") is not False
            or row.get("certification_allowed") is not False
            or row.get("submission_eligible") is not False
        ):
            raise ValueError("validated proposer response crosses the diagnostic boundary")
        payload = {key: value for key, value in row.items() if key != "validated_response_id"}
        if row.get("validated_response_id") != canonical_sha256(payload):
            raise ValueError("validated proposer response self-hash mismatch")
    reviews = _index(load_jsonl(human_reviews_path), key="review_item_id", label="human reviews")
    if not set(reviews) <= set(requests):
        raise ValueError("human review references an item outside the diagnostic batch")
    completed: dict[str, dict[str, Any]] = {}
    for review_id, row in reviews.items():
        if row.get("protocol") != LLM_DIAGNOSTIC_REVIEW_PROTOCOL:
            raise ValueError("unexpected human diagnostic review protocol")
        if row.get("human_review_status") != "COMPLETE":
            continue
        response = responses[review_id]
        comparison_design = row.get("comparison_design")
        assignment_id = str(row.get("assignment_id") or "").strip()
        baseline_reviewer_id = str(row.get("baseline_reviewer_id") or "").strip()
        assisted_reviewer_id = str(row.get("assisted_reviewer_id") or "").strip()
        comparison_order = row.get("comparison_order")
        if (
            row.get("question_id") != requests[review_id].get("question_id")
            or row.get("queue") != requests[review_id]["packet"].get("queue")
            or comparison_design not in {"independent_reviewers", "counterbalanced_crossover"}
            or not assignment_id
            or not baseline_reviewer_id
            or not assisted_reviewer_id
            or comparison_order not in {"baseline_then_assisted", "assisted_then_baseline"}
            or not isinstance(row.get("response_correct"), bool)
            or not isinstance(row.get("missed_blocker"), bool)
            or isinstance(row.get("baseline_review_seconds"), bool)
            or not isinstance(row.get("baseline_review_seconds"), (int, float))
            or float(row["baseline_review_seconds"]) <= 0
            or isinstance(row.get("assisted_review_seconds"), bool)
            or not isinstance(row.get("assisted_review_seconds"), (int, float))
            or not 0 <= float(row["assisted_review_seconds"])
        ):
            raise ValueError("completed human review is malformed")
        if comparison_design == "independent_reviewers" and baseline_reviewer_id == assisted_reviewer_id:
            raise ValueError("independent review requires distinct baseline and assisted reviewers")
        if comparison_design == "counterbalanced_crossover" and baseline_reviewer_id != assisted_reviewer_id:
            raise ValueError("counterbalanced crossover requires one named reviewer per paired review")
        source_exact = row.get("source_reference_exact")
        if response.get("validation_status") == "VALID_PROPOSAL":
            if not isinstance(source_exact, bool):
                raise ValueError("proposal review requires source_reference_exact")
        elif source_exact is not None:
            raise ValueError("non-proposal review must leave source_reference_exact null")
        completed[review_id] = row
    total = len(requests)
    schema_valid_count = sum(row.get("validation_status") in valid_statuses for row in responses.values())
    proposal_ids = {review_id for review_id, row in responses.items() if row.get("validation_status") == "VALID_PROPOSAL"}
    reviewed_proposals = proposal_ids & set(completed)
    correct_proposals = sum(bool(completed[review_id]["response_correct"]) for review_id in reviewed_proposals)
    exact_source_proposals = sum(
        bool(completed[review_id]["source_reference_exact"])
        for review_id in reviewed_proposals
    )
    missed_blockers = sum(bool(row["missed_blocker"]) for row in completed.values())
    baseline_seconds = sum(float(row["baseline_review_seconds"]) for row in completed.values())
    assisted_seconds = sum(float(row["assisted_review_seconds"]) for row in completed.values())
    metrics = {
        "packet_count": total,
        "completed_human_review_count": len(completed),
        "schema_valid_rate": schema_valid_count / total,
        "valid_proposal_rate": len(proposal_ids) / total,
        "reviewed_proposal_count": len(reviewed_proposals),
        "proposal_precision": (
            correct_proposals / len(reviewed_proposals)
            if reviewed_proposals else None
        ),
        "source_reference_exact_rate": (
            exact_source_proposals / len(reviewed_proposals)
            if reviewed_proposals else None
        ),
        "reviewer_time_reduction": (
            1 - assisted_seconds / baseline_seconds if baseline_seconds else None
        ),
        "missed_blocker_rate": (
            missed_blockers / len(completed)
            if completed else None
        ),
        "proposal_precision_wilson_95": _wilson_interval(correct_proposals, len(reviewed_proposals)),
        "source_reference_exact_wilson_95": _wilson_interval(exact_source_proposals, len(reviewed_proposals)),
        "missed_blocker_wilson_95": _wilson_interval(missed_blockers, len(completed)),
        "comparison_design_counts": dict(
            sorted(Counter(str(row["comparison_design"]) for row in completed.values()).items())
        ),
    }
    diagnostic = control["diagnostic"]
    thresholds = control["decision_thresholds"]
    enough_reviews = len(completed) >= int(diagnostic["min_human_reviewed"])
    enough_proposals = len(reviewed_proposals) >= int(diagnostic["min_human_reviewed_proposals"])
    enough_keep_reviews = len(completed) >= int(diagnostic["min_human_reviewed_for_keep"])
    enough_keep_proposals = len(reviewed_proposals) >= int(
        diagnostic["min_human_reviewed_proposals_for_keep"]
    )
    precision_interval = metrics["proposal_precision_wilson_95"]
    source_interval = metrics["source_reference_exact_wilson_95"]
    missed_interval = metrics["missed_blocker_wilson_95"]
    gates = {
        "schema_valid_rate": metrics["schema_valid_rate"] >= float(thresholds["min_schema_valid_rate"]),
        "valid_proposal_rate": metrics["valid_proposal_rate"] >= float(thresholds["min_valid_proposal_rate"]),
        "proposal_precision_wilson_lower_95": precision_interval is not None
        and precision_interval[0] >= float(thresholds["min_proposal_precision"]),
        "source_reference_exact_wilson_lower_95": source_interval is not None
        and source_interval[0] >= float(thresholds["min_source_reference_exact_rate"]),
        "reviewer_time_reduction": metrics["reviewer_time_reduction"] is not None
        and metrics["reviewer_time_reduction"]
        >= float(thresholds["min_reviewer_time_reduction"]),
        "missed_blocker_rate": metrics["missed_blocker_rate"] is not None
        and metrics["missed_blocker_rate"] <= float(thresholds["max_missed_blocker_rate"]),
        "missed_blocker_wilson_upper_95": missed_interval is not None
        and missed_interval[1] <= float(thresholds["max_missed_blocker_wilson_upper_95"]),
    }
    removal_gates = {
        name: gates[name]
        for name in ("schema_valid_rate", "valid_proposal_rate", "missed_blocker_rate")
    }
    if not enough_reviews or (not enough_proposals and len(completed) < total):
        decision = "MORE_DIAGNOSTIC_REVIEW_REQUIRED"
    elif not enough_proposals or not all(removal_gates.values()):
        decision = "REMOVE_FROM_OPERATIONAL_PIPELINE"
    elif not enough_keep_reviews or not enough_keep_proposals:
        decision = "MORE_DIAGNOSTIC_REVIEW_REQUIRED"
    elif not all(gates.values()):
        decision = "REMOVE_FROM_OPERATIONAL_PIPELINE"
    else:
        decision = "KEEP_LIMITED_PROPOSER_ONLY"
    report = {
        "schema_version": 1,
        "protocol": LLM_DIAGNOSTIC_DECISION_PROTOCOL,
        "decision": decision,
        "decision_scope": "operational_proposal_utility_only",
        "default_operational_llm_enabled": False,
        "metrics": metrics,
        "minimum_review_requirements": {
            "human_reviews": int(diagnostic["min_human_reviewed"]),
            "human_reviewed_proposals": int(diagnostic["min_human_reviewed_proposals"]),
            "human_reviews_for_keep": int(diagnostic["min_human_reviewed_for_keep"]),
            "human_reviewed_proposals_for_keep": int(
                diagnostic["min_human_reviewed_proposals_for_keep"]
            ),
            "review_protocol": str(diagnostic["review_protocol"]),
        },
        "gates": gates,
        "thresholds": dict(thresholds),
        "promotion_effect": "none_explicit_policy_change_required",
        "training_eligible": False,
        "certification_allowed": False,
        "release_authorized": False,
        "inputs": {
            "diagnostic_manifest_sha256": sha256_file(diagnostic_manifest_path),
            "validated_responses_sha256": sha256_file(validated_responses_path),
            "human_reviews_sha256": sha256_file(human_reviews_path),
            "control_policy_sha256": sha256_file(control_path),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(output_path)
    _write_json(output_path, report)
    return DiagnosticDecisionResult(output_path, decision, len(completed))
