"""Hash-bound human policy gate for Phase 5 component-selection jobs.

The calibration score measures a small blind review; it never grants authority
to run a model. This module keeps the distinct human decision explicit and
binds it to one prepared job manifest.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from finance_query.certified_canonical.phase5_component_selection import PHASE5_COMPONENT_SELECTION_PROTOCOL
from finance_query.certified_canonical.phase5_component_selection_smoke import PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL
from finance_query.certified_canonical.pipeline import CertifiedCanonicalError


SCHEMA_VERSION = 1
SCORE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_final_review_score_v1"
PILOT_PROTOCOL = "vifinqa_ccl_phase5_component_selection_pilot_v1"
POLICY_REVIEW_PROTOCOL = "vifinqa_ccl_phase5_component_selection_policy_review_v1"
POLICY_RESPONSE_PROTOCOL = "vifinqa_ccl_phase5_component_selection_policy_response_v1"
POLICY_DECISION_PROTOCOL = "vifinqa_ccl_phase5_component_selection_policy_decision_v1"
SCORE_NAME = "component_selection_final_review_calibration_score_v1.json"
SCORE_MANIFEST_NAME = "component_selection_final_review_score_manifest.json"
COMPARISON_NAME = "component_selection_final_review_comparison_v1.jsonl"
POLICY_REVIEW_NAME = "component_selection_policy_review_v1.json"
POLICY_TEMPLATE_NAME = "component_selection_policy_response_template_v1.json"
POLICY_MANIFEST_NAME = "component_selection_policy_review_manifest.json"
POLICY_DECISION_NAME = "component_selection_policy_decision_v1.json"
POLICY_DECISION_MANIFEST_NAME = "component_selection_policy_decision_manifest.json"
_JOB_CONTRACTS = {
    PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL: "prepared_component_selection_smoke_not_executed",
    PILOT_PROTOCOL: "prepared_component_selection_pilot_not_executed",
}
_RESPONSE_KEYS = {
    "schema_version",
    "protocol",
    "immutable_policy_review_sha256",
    "reviewer_id",
    "reviewed_at_utc",
    "policy_decision",
    "acknowledge_calibration_evidence",
    "acknowledge_candidate_lineage",
    "approved_candidate_job_manifest_sha256",
    "rationale",
    "training_eligible",
    "certification_allowed",
}
_KEEP_BLOCKED = "KEEP_DISPATCH_BLOCKED_AND_EXPAND_CALIBRATION"
_AUTHORIZE_SMOKE = "AUTHORIZE_BOUNDED_SMOKE_ONLY"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise CertifiedCanonicalError(f"invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise CertifiedCanonicalError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise CertifiedCanonicalError(f"invalid JSONL input: {path}:{line_number}") from error
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"expected JSONL object: {path}:{line_number}")
            rows.append(value)
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as file:
        file.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _require_hash(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, str) or expected != sha256_file(path):
        raise CertifiedCanonicalError(f"SHA-256 mismatch for {label}")


def _require_non_promotable(value: Mapping[str, Any], *, label: str) -> None:
    if value.get("training_eligible") is not False or value.get("certification_allowed") is not False:
        raise CertifiedCanonicalError(f"{label} is unexpectedly promotable")


def _resolve_path(base: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (base / path).resolve()


def _new_output(output_dir: Path, inputs: Mapping[str, Path], *, label: str) -> None:
    if output_dir.exists() or output_dir.resolve() in {path.resolve() for path in inputs.values()}:
        raise FileExistsError(f"{label} output-dir must be new and distinct from its inputs")


def _metrics(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    completed = len(rows)
    output: dict[str, Any] = {
        "reviewed_item_count": completed,
        "human_abstention_count": sum(
            (row.get("human_review") or {}).get("review_decision") == "ABSTAIN_UNRESOLVED" for row in rows
        ),
    }
    for model in ("qwen", "mistral"):
        comparisons = [row.get(f"{model}_comparison") or {} for row in rows]
        output[f"{model}_primary_match_count"] = sum(item.get("primary_match") is True for item in comparisons)
        output[f"{model}_support_exact_match_count"] = sum(item.get("support_exact_match") is True for item in comparisons)
        output[f"{model}_mean_support_jaccard"] = (
            sum(float(item.get("support_jaccard")) for item in comparisons) / completed if completed else None
        )
    return output


def _require_metrics(expected: Mapping[str, Any], actual: Mapping[str, Any], *, label: str) -> None:
    for key in (
        "reviewed_item_count",
        "human_abstention_count",
        "qwen_primary_match_count",
        "qwen_support_exact_match_count",
        "mistral_primary_match_count",
        "mistral_support_exact_match_count",
    ):
        if expected.get(key) != actual.get(key):
            raise CertifiedCanonicalError(f"{label} does not match its comparison evidence: {key}")
    for key in ("qwen_mean_support_jaccard", "mistral_mean_support_jaccard"):
        observed, declared = actual.get(key), expected.get(key)
        if not isinstance(observed, (float, int)) or not isinstance(declared, (float, int)) or not math.isclose(
            float(observed), float(declared), rel_tol=0.0, abs_tol=1e-12
        ):
            raise CertifiedCanonicalError(f"{label} does not match its comparison evidence: {key}")


def _require_score(
    *, score_path: Path, score_manifest_path: Path, comparison_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    score = _json(score_path)
    score_manifest = _json(score_manifest_path)
    if (
        score.get("protocol") != SCORE_PROTOCOL
        or score.get("run_status") != "final_review_calibration_scored_not_promoted"
        or score.get("training_eligible_output_count") != 0
        or score.get("certification_allowed") is not False
        or score.get("next_gate") != "explicit_policy_review_of_calibration_error_and_sample_size_before_any_bounded_pilot"
    ):
        raise CertifiedCanonicalError("calibration score is unsupported or promotable")
    if (
        score_manifest.get("protocol") != SCORE_PROTOCOL
        or score_manifest.get("run_status") != "final_review_calibration_scored_not_promoted"
    ):
        raise CertifiedCanonicalError("calibration score manifest is unsupported")
    _require_non_promotable(score_manifest, label="calibration score manifest")
    outputs = score_manifest.get("outputs") or {}
    _require_hash(score_path, (outputs.get(SCORE_NAME) or {}).get("sha256"), label="calibration score")
    _require_hash(comparison_path, (outputs.get(COMPARISON_NAME) or {}).get("sha256"), label="calibration comparison")
    rows = _jsonl(comparison_path)
    if not rows:
        raise CertifiedCanonicalError("calibration comparison has no rows")
    for row in rows:
        if row.get("protocol") != SCORE_PROTOCOL:
            raise CertifiedCanonicalError("calibration comparison protocol is unsupported")
        _require_non_promotable(row, label="calibration comparison row")
    global_metrics = score.get("global_metrics")
    stratum_metrics = score.get("stratum_metrics")
    if not isinstance(global_metrics, Mapping) or not isinstance(stratum_metrics, Mapping):
        raise CertifiedCanonicalError("calibration score metrics are malformed")
    _require_metrics(global_metrics, _metrics(rows), label="global calibration metrics")
    rows_by_stratum: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        stratum = row.get("review_stratum")
        if not isinstance(stratum, str) or not stratum:
            raise CertifiedCanonicalError("calibration comparison lacks a review stratum")
        rows_by_stratum[stratum].append(row)
    if set(rows_by_stratum) != set(stratum_metrics):
        raise CertifiedCanonicalError("calibration score strata do not match comparison evidence")
    for stratum, rows_for_stratum in rows_by_stratum.items():
        metrics = stratum_metrics.get(stratum)
        if not isinstance(metrics, Mapping):
            raise CertifiedCanonicalError("calibration score stratum metrics are malformed")
        _require_metrics(metrics, _metrics(rows_for_stratum), label=f"calibration metrics for {stratum}")
    return score, rows


def _require_candidate_job(job_manifest_path: Path) -> dict[str, Any]:
    job = _json(job_manifest_path)
    protocol = job.get("protocol")
    if (
        protocol not in _JOB_CONTRACTS
        or job.get("run_status") != _JOB_CONTRACTS[protocol]
        or job.get("navigation_overlay_required") is not True
        or job.get("model_execution_allowed") is not True
    ):
        raise CertifiedCanonicalError("candidate job is not a navigation-gated prepared component-selection job")
    _require_non_promotable(job, label="candidate job manifest")
    outputs = job.get("outputs") or {}
    if not isinstance(outputs, Mapping):
        raise CertifiedCanonicalError("candidate job manifest outputs are malformed")
    packet_manifest_names = [name for name in outputs if isinstance(name, str) and name.endswith("_packet_manifest.json")]
    request_names = [name for name in outputs if isinstance(name, str) and name.endswith("_requests_v1.jsonl")]
    if len(packet_manifest_names) != 1 or len(request_names) != 1:
        raise CertifiedCanonicalError("candidate job does not bind one selected packet manifest and request file")
    packet_manifest_path = job_manifest_path.parent / packet_manifest_names[0]
    request_path = job_manifest_path.parent / request_names[0]
    if not packet_manifest_path.is_file() or not request_path.is_file():
        raise FileNotFoundError("candidate job is missing selected packets or requests")
    _require_hash(packet_manifest_path, (outputs.get(packet_manifest_names[0]) or {}).get("sha256"), label="selected packet manifest")
    _require_hash(request_path, (outputs.get(request_names[0]) or {}).get("sha256"), label="candidate requests")
    selected_manifest = _json(packet_manifest_path)
    if (
        selected_manifest.get("protocol") != PHASE5_COMPONENT_SELECTION_PROTOCOL
        or selected_manifest.get("run_status") != "phase_5_component_selection_packets_complete_not_dispatched"
        or selected_manifest.get("navigation_overlay_required") is not True
    ):
        raise CertifiedCanonicalError("candidate selected packet manifest is not navigation-gated")
    _require_non_promotable(selected_manifest, label="candidate selected packet manifest")
    inputs = job.get("inputs") or {}
    packet_source = inputs.get("packet_manifest") if isinstance(inputs, Mapping) else None
    if not isinstance(packet_source, Mapping):
        raise CertifiedCanonicalError("candidate job does not bind its source packet manifest")
    source_manifest_path = _resolve_path(job_manifest_path.parent, packet_source.get("path"))
    if not source_manifest_path.is_file():
        raise FileNotFoundError("candidate source packet manifest is missing")
    _require_hash(source_manifest_path, packet_source.get("sha256"), label="candidate source packet manifest")
    source_manifest = _json(source_manifest_path)
    navigation_inputs = source_manifest.get("inputs") or {}
    if (
        source_manifest.get("protocol") != PHASE5_COMPONENT_SELECTION_PROTOCOL
        or source_manifest.get("navigation_overlay_required") is not True
        or not isinstance(navigation_inputs, Mapping)
        or not {"report_navigation_overlay_v1.jsonl", "report_navigation_overlay_manifest.json"}.issubset(navigation_inputs)
    ):
        raise CertifiedCanonicalError("candidate source packet manifest is missing navigation provenance")
    _require_non_promotable(source_manifest, label="candidate source packet manifest")
    request_rows = _jsonl(request_path)
    request_ids = [str(row.get("component_selection_request_id") or "") for row in request_rows]
    if not request_ids or "" in request_ids or len(request_ids) != len(set(request_ids)):
        raise CertifiedCanonicalError("candidate request identities are malformed")
    return {
        "job_protocol": protocol,
        "job_manifest_path": str(job_manifest_path),
        "job_manifest_sha256": sha256_file(job_manifest_path),
        "source_packet_manifest_path": str(source_manifest_path),
        "source_packet_manifest_sha256": sha256_file(source_manifest_path),
        "selected_packet_manifest_sha256": sha256_file(packet_manifest_path),
        "request_sha256": sha256_file(request_path),
        "request_count": len(request_rows),
        "navigation_overlay_required": True,
        "training_eligible": False,
        "certification_allowed": False,
    }


def build_phase5_component_selection_policy_review(
    *, score: Path, score_manifest: Path, comparison: Path, candidate_job_manifest: Path, output_dir: Path
) -> dict[str, Any]:
    """Build a pending human decision package without granting dispatch authority."""
    inputs = {
        "score": score.resolve(),
        "score_manifest": score_manifest.resolve(),
        "comparison": comparison.resolve(),
        "candidate_job_manifest": candidate_job_manifest.resolve(),
    }
    if any(not path.is_file() for path in inputs.values()):
        raise FileNotFoundError("policy-review input is missing")
    _new_output(output_dir, inputs, label="component-selection policy review")
    before_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    score_payload, comparison_rows = _require_score(
        score_path=inputs["score"], score_manifest_path=inputs["score_manifest"], comparison_path=inputs["comparison"]
    )
    candidate = _require_candidate_job(inputs["candidate_job_manifest"])
    metrics = score_payload["global_metrics"]
    reviewed = int(metrics["reviewed_item_count"])
    abstentions = int(metrics["human_abstention_count"])
    reasons: list[str] = []
    if reviewed < 20:
        reasons.append("CALIBRATION_SAMPLE_BELOW_RECOMMENDED_MINIMUM")
    if abstentions:
        reasons.append("HUMAN_ABSTENTIONS_REQUIRE_ERROR_ANALYSIS")
    if len(score_payload["stratum_metrics"]) < 2 or any(
        int(value.get("reviewed_item_count") or 0) < 5 for value in score_payload["stratum_metrics"].values()
    ):
        reasons.append("STRATUM_COVERAGE_BELOW_RECOMMENDED_MINIMUM")
    if not reasons:
        reasons.append("HUMAN_POLICY_DECISION_STILL_REQUIRED")
    after_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("policy-review construction changed a hash-bound input")
    output_dir.mkdir(parents=True)
    review = {
        "schema_version": SCHEMA_VERSION,
        "protocol": POLICY_REVIEW_PROTOCOL,
        "run_status": "policy_decision_pending",
        "calibration_score": {
            "sha256": before_hashes["score"],
            "global_metrics": metrics,
            "stratum_metrics": score_payload["stratum_metrics"],
            "comparison_row_count": len(comparison_rows),
        },
        "candidate_job": candidate,
        "recommendation": {
            "policy_decision": _KEEP_BLOCKED,
            "reason_codes": reasons,
            "explanation": "The score is measurement evidence only; a named human reviewer must make any exception explicit.",
        },
        "permitted_policy_decisions": [_KEEP_BLOCKED, _AUTHORIZE_SMOKE],
        "kaggle_dataset_packaging_allowed": False,
        "model_execution_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    review_path = output_dir / POLICY_REVIEW_NAME
    _write_json(review_path, review)
    template = {
        "schema_version": SCHEMA_VERSION,
        "protocol": POLICY_RESPONSE_PROTOCOL,
        "immutable_policy_review_sha256": _canonical_sha(review),
        "reviewer_id": None,
        "reviewed_at_utc": None,
        "policy_decision": None,
        "acknowledge_calibration_evidence": None,
        "acknowledge_candidate_lineage": None,
        "approved_candidate_job_manifest_sha256": None,
        "rationale": None,
        "training_eligible": False,
        "certification_allowed": False,
    }
    template_path = output_dir / POLICY_TEMPLATE_NAME
    _write_json(template_path, template)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": POLICY_REVIEW_PROTOCOL,
        "run_status": "policy_decision_pending",
        "inputs": {name: {"path": str(path), "sha256": before_hashes[name]} for name, path in inputs.items()},
        "outputs": {
            POLICY_REVIEW_NAME: {"sha256": sha256_file(review_path)},
            POLICY_TEMPLATE_NAME: {"sha256": sha256_file(template_path)},
        },
        "kaggle_dataset_packaging_allowed": False,
        "model_execution_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    _write_json(output_dir / POLICY_MANIFEST_NAME, manifest)
    return review


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _require_policy_review_package(
    *, policy_review: Path, policy_template: Path, policy_manifest: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    review = _json(policy_review)
    template = _json(policy_template)
    manifest = _json(policy_manifest)
    if (
        review.get("protocol") != POLICY_REVIEW_PROTOCOL
        or review.get("run_status") != "policy_decision_pending"
        or review.get("kaggle_dataset_packaging_allowed") is not False
        or review.get("model_execution_allowed") is not False
    ):
        raise CertifiedCanonicalError("policy review is not a pending dispatch gate")
    _require_non_promotable(review, label="policy review")
    if manifest.get("protocol") != POLICY_REVIEW_PROTOCOL or manifest.get("run_status") != "policy_decision_pending":
        raise CertifiedCanonicalError("policy-review manifest is unsupported")
    _require_non_promotable(manifest, label="policy-review manifest")
    outputs = manifest.get("outputs") or {}
    _require_hash(policy_review, (outputs.get(POLICY_REVIEW_NAME) or {}).get("sha256"), label="policy review")
    _require_hash(policy_template, (outputs.get(POLICY_TEMPLATE_NAME) or {}).get("sha256"), label="policy response template")
    if (
        set(template) != _RESPONSE_KEYS
        or template.get("protocol") != POLICY_RESPONSE_PROTOCOL
        or template.get("immutable_policy_review_sha256") != _canonical_sha(review)
        or any(template.get(key) is not None for key in _RESPONSE_KEYS - {"schema_version", "protocol", "immutable_policy_review_sha256", "training_eligible", "certification_allowed"})
    ):
        raise CertifiedCanonicalError("policy response template is not blank or bound to its review")
    _require_non_promotable(template, label="policy response template")
    return review, template


def resolve_phase5_component_selection_policy_review(
    *, policy_review: Path, policy_template: Path, policy_manifest: Path, response: Path, output_dir: Path
) -> dict[str, Any]:
    """Record a human decision; only an explicit five-request smoke exception can permit packaging."""
    inputs = {
        "policy_review": policy_review.resolve(),
        "policy_template": policy_template.resolve(),
        "policy_manifest": policy_manifest.resolve(),
        "response": response.resolve(),
    }
    if any(not path.is_file() for path in inputs.values()):
        raise FileNotFoundError("policy-resolution input is missing")
    _new_output(output_dir, inputs, label="component-selection policy decision")
    before_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    review, _ = _require_policy_review_package(
        policy_review=inputs["policy_review"], policy_template=inputs["policy_template"], policy_manifest=inputs["policy_manifest"]
    )
    response_payload = _json(inputs["response"])
    if set(response_payload) != _RESPONSE_KEYS:
        raise CertifiedCanonicalError("policy response keys are malformed")
    if (
        response_payload.get("schema_version") != SCHEMA_VERSION
        or response_payload.get("protocol") != POLICY_RESPONSE_PROTOCOL
        or response_payload.get("immutable_policy_review_sha256") != _canonical_sha(review)
    ):
        raise CertifiedCanonicalError("policy response is not bound to the immutable policy review")
    _require_non_promotable(response_payload, label="policy response")
    if (
        not isinstance(response_payload.get("reviewer_id"), str)
        or not response_payload["reviewer_id"].strip()
        or not _valid_timestamp(response_payload.get("reviewed_at_utc"))
        or response_payload.get("acknowledge_calibration_evidence") is not True
        or response_payload.get("acknowledge_candidate_lineage") is not True
        or not isinstance(response_payload.get("rationale"), str)
        or len(response_payload["rationale"].strip()) < 20
    ):
        raise CertifiedCanonicalError("policy response lacks reviewer identity, acknowledgements, timestamp, or rationale")
    decision = response_payload.get("policy_decision")
    candidate = review.get("candidate_job") or {}
    candidate_sha = candidate.get("job_manifest_sha256")
    if decision not in {_KEEP_BLOCKED, _AUTHORIZE_SMOKE}:
        raise CertifiedCanonicalError("policy response decision is unsupported")
    if decision == _KEEP_BLOCKED:
        if response_payload.get("approved_candidate_job_manifest_sha256") is not None:
            raise CertifiedCanonicalError("blocked policy decision cannot approve a candidate job")
        run_status = "dispatch_remains_blocked"
        packaging_allowed = False
    else:
        if (
            candidate.get("job_protocol") != PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL
            or candidate.get("request_count") != 5
            or response_payload.get("approved_candidate_job_manifest_sha256") != candidate_sha
        ):
            raise CertifiedCanonicalError("policy exception can authorize only the exact five-request smoke job")
        run_status = "bounded_smoke_explicitly_authorized_not_dispatched"
        packaging_allowed = True
    after_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    if after_hashes != before_hashes:
        raise CertifiedCanonicalError("policy-resolution construction changed a hash-bound input")
    output_dir.mkdir(parents=True)
    decision_payload = {
        "schema_version": SCHEMA_VERSION,
        "protocol": POLICY_DECISION_PROTOCOL,
        "run_status": run_status,
        "policy_review_sha256": before_hashes["policy_review"],
        "policy_response_sha256": before_hashes["response"],
        "reviewer_id": response_payload["reviewer_id"].strip(),
        "reviewed_at_utc": response_payload["reviewed_at_utc"],
        "policy_decision": decision,
        "candidate_job_manifest_sha256": candidate_sha,
        "kaggle_dataset_packaging_allowed": packaging_allowed,
        "model_execution_allowed": packaging_allowed,
        "training_eligible": False,
        "certification_allowed": False,
    }
    decision_path = output_dir / POLICY_DECISION_NAME
    _write_json(decision_path, decision_payload)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": POLICY_DECISION_PROTOCOL,
        "run_status": run_status,
        "inputs": {name: {"path": str(path), "sha256": before_hashes[name]} for name, path in inputs.items()},
        "outputs": {POLICY_DECISION_NAME: {"sha256": sha256_file(decision_path)}},
        "kaggle_dataset_packaging_allowed": packaging_allowed,
        "model_execution_allowed": packaging_allowed,
        "training_eligible": False,
        "certification_allowed": False,
    }
    _write_json(output_dir / POLICY_DECISION_MANIFEST_NAME, manifest)
    return decision_payload
