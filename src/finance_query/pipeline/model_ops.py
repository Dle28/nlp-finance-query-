"""Fail-closed model lifecycle gates for the submission-first pipeline.

This module owns only lifecycle metadata.  It does not train a model, load
weights, run Kaggle, execute a question, or authorize an answer/evidence/
submission/release.  A feedback response from a model is a hypothesis until
an explicit source receipt or human review is attached to the same feedback
record.

The intended lifecycle is::

    model feedback -> verified training candidate -> held-out metrics
    -> PromotionDecision -> navigation-only ModelBundle manifest

The functions are deliberately pure and return JSON-friendly records so a
caller can persist them in an existing run manifest without adding another
runtime service.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import Any, ClassVar

from .contracts import FEEDBACK_PROTOCOL, canonical_sha256


MODEL_OPS_PROTOCOL = "vifinqa_model_lifecycle_gate_v1"
TRAINING_CANDIDATE_PROTOCOL = "vifinqa_verified_training_candidate_v1"
HELD_OUT_METRICS_PROTOCOL = "vifinqa_held_out_metrics_gate_v1"
PROMOTION_DECISION_PROTOCOL = "vifinqa_model_promotion_decision_v1"
MODEL_BUNDLE_PROTOCOL = "vifinqa_model_bundle_manifest_v1"

MODEL_KINDS = frozenset({"retriever", "reranker"})
REQUIRED_METRICS = ("recall_at_5", "mrr_at_5")
REQUIRED_HASHES = ("code", "model", "index", "prompt", "input")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PROMOTION_APPROVE = "APPROVE"
PROMOTION_REJECT = "REJECT"
PROMOTION_INELIGIBLE = "INELIGIBLE"

# These names are checked only inside the model's nested feedback object.  The
# outer feedback record legitimately contains false lifecycle flags.
_MODEL_FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "final_answer",
        "numeric_answer",
        "raw_value",
        "source_value",
        "numeric_value",
        "parsed_value",
        "numeric_cells",
        "evidence_value",
        "human_verified",
        "training_eligible",
        "promotion_allowed",
        "submission_eligible",
        "release_authorized",
        "answer_authorized",
        "evidence_authorized",
        "certificate",
        "answer_certificate",
    }
)

_POSITIVE_REVIEW_DECISIONS = frozenset(
    {"ACCEPT", "APPROVE", "CONFIRM", "CONFIRMED", "VERIFIED"}
)


class PromotionDecisionCode(str, Enum):
    """Machine-readable outcomes of the promotion gate."""

    APPROVE = PROMOTION_APPROVE
    REJECT = PROMOTION_REJECT
    INELIGIBLE = PROMOTION_INELIGIBLE


def _text(value: object) -> str:
    return str(value or "").strip()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value.strip().lower()))


def _normalise_sha256(value: object) -> str:
    return _text(value).lower()


def _finite_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        decimal = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not decimal.is_finite():
        return None
    return decimal


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _lookup(mapping: Mapping[Any, Any] | None, record: Mapping[str, Any], index: int) -> Any:
    """Look up an external verification by feedback id, question id, or index."""

    if not isinstance(mapping, Mapping):
        return None
    keys = (
        record.get("feedback_id"),
        record.get("question_id"),
        str(record.get("question_id")) if record.get("question_id") is not None else None,
        index,
        str(index),
    )
    for key in keys:
        if key is not None and key in mapping:
            return mapping[key]
    return None


def _as_records(records: Iterable[Mapping[str, Any]] | Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if isinstance(records, Mapping):
        return [records]
    return [record for record in records]


def _nested_forbidden(value: object, path: str = "$") -> str | None:
    """Return the first forbidden key in a model-produced feedback object."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).casefold()
            if name in _MODEL_FORBIDDEN_KEYS or name.endswith("_answer") or name.endswith("_value"):
                return f"{path}.{key}"
            nested = _nested_forbidden(child, f"{path}.{key}")
            if nested:
                return nested
    elif isinstance(value, list):
        for index, child in enumerate(value):
            nested = _nested_forbidden(child, f"{path}[{index}]")
            if nested:
                return nested
    return None


def _hash_values(value: object) -> set[str]:
    """Extract valid SHA-256 strings from a scalar, list, or small mapping."""

    if isinstance(value, Mapping):
        values: list[object] = []
        for key in ("sha256", "source_sha256", "table_sha256", "hash", "hashes"):
            if key in value:
                values.append(value[key])
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        values = [value]
    result: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            result.update(_hash_values(item))
        elif _valid_sha256(item):
            result.add(_normalise_sha256(item))
    return result


def _verification_receipt(
    record: Mapping[str, Any],
    *,
    source_verifications: Mapping[Any, Any] | None,
    human_verifications: Mapping[Any, Any] | None,
    index: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Validate explicit source and human receipts without copying raw values."""

    reasons: list[str] = []
    source = (
        record.get("source_verification")
        or record.get("source_verification_receipt")
        or record.get("source_receipt")
    )
    if source is None and record.get("source_verified") is True:
        source = {
            "source_verified": True,
            "verification_id": record.get("source_verification_id")
            or record.get("verification_id"),
            "source_sha256": record.get("source_sha256")
            or record.get("source_ref_sha256"),
            "verified_at": record.get("source_verified_at") or record.get("verified_at"),
        }
    verification = record.get("verification")
    if source is None and isinstance(verification, Mapping):
        source = verification.get("source") or verification.get("source_verification")
    if source is None:
        source = _lookup(source_verifications, record, index)
    human = (
        record.get("human_verification")
        or record.get("human_review")
        or record.get("human_review_receipt")
    )
    if human is None and record.get("human_verified") is True:
        human = {
            "human_verified": True,
            "reviewer_id": record.get("reviewer_id"),
            "reviewed_at": record.get("reviewed_at") or record.get("verified_at"),
            "decision": record.get("review_decision") or record.get("decision"),
        }
    if human is None and isinstance(verification, Mapping):
        human = verification.get("human") or verification.get("human_verification")
    if human is None:
        human = _lookup(human_verifications, record, index)

    source_out: dict[str, Any] | None = None
    if source is not None:
        if not isinstance(source, Mapping):
            reasons.append("SOURCE_VERIFICATION_INVALID")
        else:
            verified = source.get("verified") is True or source.get("source_verified") is True
            receipt_id = _text(
                source.get("verification_id")
                or source.get("receipt_id")
                or source.get("review_id")
            )
            source_hashes = _hash_values(
                source.get("source_ref_sha256")
                or source.get("source_sha256")
                or source.get("hashes")
            )
            feedback = record.get("feedback")
            feedback_hashes = (
                _hash_values(feedback.get("source_ref_sha256"))
                if isinstance(feedback, Mapping)
                else set()
            )
            if not verified:
                reasons.append("SOURCE_VERIFICATION_NOT_CONFIRMED")
            if not receipt_id:
                reasons.append("SOURCE_VERIFICATION_RECEIPT_MISSING")
            if not source_hashes:
                reasons.append("SOURCE_VERIFICATION_HASH_MISSING")
            elif not feedback_hashes or not source_hashes.intersection(feedback_hashes):
                reasons.append("SOURCE_VERIFICATION_HASH_MISMATCH")
            source_errors = [reason for reason in reasons if reason.startswith("SOURCE_")]
            if not source_errors:
                source_out = {
                    "verified": True,
                    "verification_id": receipt_id,
                    "source_ref_sha256": sorted(source_hashes),
                    "verified_at": _text(source.get("verified_at") or source.get("timestamp")),
                }

    human_out: dict[str, Any] | None = None
    if human is not None:
        if not isinstance(human, Mapping):
            reasons.append("HUMAN_VERIFICATION_INVALID")
        else:
            verified = human.get("verified") is True or human.get("human_verified") is True
            reviewer_id = _text(human.get("reviewer_id") or human.get("reviewer"))
            reviewed_at = _text(
                human.get("reviewed_at") or human.get("verified_at") or human.get("timestamp")
            )
            decision = _text(human.get("decision") or human.get("review_decision")).upper()
            if decision and decision not in _POSITIVE_REVIEW_DECISIONS:
                reasons.append("HUMAN_VERIFICATION_NOT_ACCEPTED")
            if not verified:
                reasons.append("HUMAN_VERIFICATION_NOT_CONFIRMED")
            if not reviewer_id:
                reasons.append("HUMAN_REVIEWER_MISSING")
            if not reviewed_at:
                reasons.append("HUMAN_REVIEW_TIMESTAMP_MISSING")
            human_errors = [reason for reason in reasons if reason.startswith("HUMAN_")]
            if not human_errors:
                human_out = {
                    "verified": True,
                    "reviewer_id": reviewer_id,
                    "reviewed_at": reviewed_at,
                    "decision": decision or "VERIFIED",
                }

    if source_out is None and human_out is None and not reasons:
        reasons.append("SOURCE_OR_HUMAN_VERIFICATION_REQUIRED")
    return source_out, human_out, reasons


@dataclass(frozen=True, slots=True)
class TrainingAdmissionResult:
    """Accepted verified candidates and explicit rejection reasons."""

    candidates: tuple[dict[str, Any], ...]
    rejected: tuple[dict[str, Any], ...]

    @property
    def accepted_count(self) -> int:
        return len(self.candidates)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def training_candidates(self) -> tuple[dict[str, Any], ...]:
        return self.candidates

    def __iter__(self):
        return iter(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "protocol": TRAINING_CANDIDATE_PROTOCOL,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "candidates": [dict(candidate) for candidate in self.candidates],
            "rejected": [dict(item) for item in self.rejected],
            "authority_boundary": "training_candidate_only_non_authorizing",
            "answer_authorized": False,
            "evidence_authorized": False,
            "promotion_allowed": False,
            "submission_eligible": False,
            "release_authorized": False,
        }


def admit_feedback_records(
    feedback_records: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    source_verifications: Mapping[Any, Any] | None = None,
    human_verifications: Mapping[Any, Any] | None = None,
) -> TrainingAdmissionResult:
    """Admit only contract-valid feedback with an explicit verification receipt.

    A ``VALID_FEEDBACK`` model response without a source receipt or a human
    review is rejected.  A source receipt must contain a receipt id and a
    SHA-256 reference that intersects the hash cited by the feedback.  A
    human review must contain a positive decision, reviewer id, timestamp and
    an explicit ``verified=true`` marker.
    """

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, record in enumerate(_as_records(feedback_records)):
        if not isinstance(record, Mapping):
            rejected.append({"index": index, "reason_codes": ["FEEDBACK_RECORD_INVALID"]})
            continue
        feedback = record.get("feedback")
        reasons: list[str] = []
        if record.get("protocol") != FEEDBACK_PROTOCOL:
            reasons.append("FEEDBACK_PROTOCOL_INVALID")
        if record.get("model_status") != "VALID_FEEDBACK":
            reasons.append("FEEDBACK_MODEL_OUTPUT_NOT_VALID")
        if not _text(record.get("feedback_id")):
            reasons.append("FEEDBACK_ID_MISSING")
        if not isinstance(feedback, Mapping):
            reasons.append("FEEDBACK_PAYLOAD_MISSING")
        else:
            if feedback.get("decision") != "FEEDBACK_ONLY":
                reasons.append("FEEDBACK_NOT_TRAINING_LABEL")
            if not _text(feedback.get("failure_class")):
                reasons.append("FEEDBACK_FAILURE_CLASS_MISSING")
            forbidden_path = _nested_forbidden(feedback)
            if forbidden_path:
                reasons.append("MODEL_OUTPUT_AUTHORITY_FIELD")
        source_out, human_out, verification_reasons = _verification_receipt(
            record,
            source_verifications=source_verifications,
            human_verifications=human_verifications,
            index=index,
        )
        reasons.extend(verification_reasons)
        reasons = list(_unique(reasons))
        if reasons or (source_out is None and human_out is None):
            if not reasons:
                reasons = ["SOURCE_OR_HUMAN_VERIFICATION_REQUIRED"]
            rejected.append(
                {
                    "index": index,
                    "feedback_id": record.get("feedback_id"),
                    "question_id": record.get("question_id"),
                    "reason_codes": reasons,
                    "training_eligible": False,
                }
            )
            continue

        assert isinstance(feedback, Mapping)  # narrowed above; helps type checkers
        verification_basis = (
            "SOURCE_AND_HUMAN_VERIFIED"
            if source_out and human_out
            else "SOURCE_VERIFIED"
            if source_out
            else "HUMAN_VERIFIED"
        )
        candidate_core = {
            "feedback_id": _text(record.get("feedback_id")),
            "question_id": record.get("question_id"),
            "packet_id": record.get("packet_id"),
            "failure_class": _text(feedback.get("failure_class")),
            "reason_codes": list(feedback.get("reason_codes") or []),
            "recommended_actions": list(feedback.get("recommended_actions") or []),
            "missing_context_fields": list(feedback.get("missing_context_fields") or []),
            "verification_basis": verification_basis,
        }
        candidate = {
            "schema_version": 1,
            "protocol": TRAINING_CANDIDATE_PROTOCOL,
            "candidate_id": canonical_sha256(candidate_core),
            **candidate_core,
            "model_id": record.get("model_id"),
            "model_status": record.get("model_status"),
            "source_verification": source_out,
            "human_verification": human_out,
            "source_verified": source_out is not None,
            "human_verified": human_out is not None,
            "training_eligible": True,
            "requires_human_verification": False,
            "authority_boundary": "verified_training_candidate_non_authorizing",
            "answer_authorized": False,
            "evidence_authorized": False,
            "promotion_allowed": False,
            "submission_eligible": False,
            "release_authorized": False,
        }
        candidates.append(candidate)
    return TrainingAdmissionResult(tuple(candidates), tuple(rejected))


# Descriptive alias for callers that prefer the lifecycle wording.
admit_feedback_for_training = admit_feedback_records


def _metric_value(section: Mapping[str, Any], name: str) -> object:
    aliases = {
        "recall_at_5": {"recall_at_5", "recall5"},
        "mrr_at_5": {"mrr_at_5", "mrr5"},
    }[name]
    for key, value in section.items():
        normalised = str(key).casefold().replace("@", "_at_").replace("-", "_")
        normalised = re.sub(r"[^a-z0-9_]+", "", normalised)
        if normalised in aliases:
            return value
    return None


def _hash_descriptor(hashes: Mapping[str, Any], name: str) -> object:
    aliases = {name, f"{name}_sha256", f"{name}_hash"}
    for key, value in hashes.items():
        normalised = str(key).casefold().replace("-", "_")
        if normalised not in aliases:
            continue
        if isinstance(value, Mapping):
            return value.get("sha256") or value.get("hash")
        return value
    if name == "input":
        value = next(
            (item for key, item in hashes.items() if str(key).casefold() == "inputs"),
            None,
        )
        return value.get("sha256") if isinstance(value, Mapping) else value
    return None


@dataclass(frozen=True, slots=True)
class HeldOutMetricsValidation:
    """Structural and non-decrease result for one retriever/reranker report."""

    model_kind: str | None
    valid: bool
    passed: bool
    full_run: bool | None
    baseline_metrics: dict[str, float]
    candidate_metrics: dict[str, float]
    hashes: dict[str, str]
    reason_codes: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return self.passed

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.reason_codes

    def __bool__(self) -> bool:
        return self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "protocol": HELD_OUT_METRICS_PROTOCOL,
            "model_kind": self.model_kind,
            "valid": self.valid,
            "passed": self.passed,
            "eligible": self.eligible,
            "full_run": self.full_run,
            "baseline": dict(self.baseline_metrics),
            "candidate": dict(self.candidate_metrics),
            "hashes": {f"{key}_sha256": value for key, value in self.hashes.items()},
            "reason_codes": list(self.reason_codes),
        }


def validate_held_out_metrics(
    report: Mapping[str, Any] | None = None,
    *,
    baseline: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
    model_kind: str | None = None,
    full_run: bool | None = None,
    hashes: Mapping[str, Any] | None = None,
) -> HeldOutMetricsValidation:
    """Validate Recall@5/MRR@5, full-run status and artifact hashes.

    ``report`` may use either ``baseline``/``candidate`` sections or the
    explicit keyword arguments.  Metric regressions are structurally valid
    evaluations but fail the gate (``passed=False``); missing metrics, a
    non-boolean ``full_run`` and missing/invalid hashes are ineligible.
    """

    payload = report if isinstance(report, Mapping) else {}
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), Mapping) else {}
    metrics_payload = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    baseline_section = (
        baseline
        or payload.get("baseline")
        or payload.get("baseline_metrics")
        or metrics_payload.get("baseline")
        or evaluation.get("baseline")
        or {}
    )
    candidate_section = (
        candidate
        or payload.get("candidate")
        or payload.get("candidate_metrics")
        or metrics_payload.get("candidate")
        or evaluation.get("candidate")
        or {}
    )
    if not isinstance(baseline_section, Mapping):
        baseline_section = {}
    if not isinstance(candidate_section, Mapping):
        candidate_section = {}
    kind = _text(
        model_kind
        or payload.get("model_kind")
        or payload.get("model_type")
        or payload.get("component")
        or evaluation.get("model_kind")
    ).lower() or None
    run_value = (
        full_run
        if full_run is not None
        else payload.get("full_run", evaluation.get("full_run"))
    )
    hash_section = (
        hashes
        or payload.get("hashes")
        or payload.get("artifact_hashes")
        or metrics_payload.get("hashes")
        or evaluation.get("hashes")
        or {}
    )
    if not isinstance(hash_section, Mapping):
        hash_section = {}

    reasons: list[str] = []
    if kind is None:
        reasons.append("MODEL_KIND_MISSING")
    elif kind not in MODEL_KINDS:
        reasons.append("MODEL_KIND_UNSUPPORTED")
    if run_value is not True:
        reasons.append("FULL_RUN_REQUIRED")

    baseline_values: dict[str, float] = {}
    candidate_values: dict[str, float] = {}
    decimal_baseline: dict[str, Decimal] = {}
    decimal_candidate: dict[str, Decimal] = {}
    for name in REQUIRED_METRICS:
        base_value = _finite_decimal(_metric_value(baseline_section, name))
        candidate_value = _finite_decimal(_metric_value(candidate_section, name))
        if base_value is None:
            reasons.append(f"BASELINE_{name.upper()}_MISSING_OR_INVALID")
        elif not Decimal("0") <= base_value <= Decimal("1"):
            reasons.append(f"BASELINE_{name.upper()}_OUT_OF_RANGE")
        else:
            decimal_baseline[name] = base_value
            baseline_values[name] = float(base_value)
        if candidate_value is None:
            reasons.append(f"CANDIDATE_{name.upper()}_MISSING_OR_INVALID")
        elif not Decimal("0") <= candidate_value <= Decimal("1"):
            reasons.append(f"CANDIDATE_{name.upper()}_OUT_OF_RANGE")
        else:
            decimal_candidate[name] = candidate_value
            candidate_values[name] = float(candidate_value)

    normalised_hashes: dict[str, str] = {}
    for name in REQUIRED_HASHES:
        value = _hash_descriptor(hash_section, name)
        if value is None or not _valid_sha256(value):
            reasons.append(f"{name.upper()}_HASH_MISSING_OR_INVALID")
        else:
            normalised_hashes[name] = _normalise_sha256(value)

    if not any(reason.startswith("BASELINE_") or reason.startswith("CANDIDATE_") for reason in reasons):
        regressions = [
            name
            for name in REQUIRED_METRICS
            if decimal_candidate[name] < decimal_baseline[name]
        ]
        reasons.extend(f"METRIC_REGRESSION_{name.upper()}" for name in regressions)

    structural_codes = {
        "MODEL_KIND_MISSING",
        "MODEL_KIND_UNSUPPORTED",
        "FULL_RUN_REQUIRED",
        *(f"BASELINE_{name.upper()}_MISSING_OR_INVALID" for name in REQUIRED_METRICS),
        *(f"CANDIDATE_{name.upper()}_MISSING_OR_INVALID" for name in REQUIRED_METRICS),
        *(f"BASELINE_{name.upper()}_OUT_OF_RANGE" for name in REQUIRED_METRICS),
        *(f"CANDIDATE_{name.upper()}_OUT_OF_RANGE" for name in REQUIRED_METRICS),
        *(f"{name.upper()}_HASH_MISSING_OR_INVALID" for name in REQUIRED_HASHES),
    }
    unique_reasons = _unique(reasons)
    structural_valid = not any(reason in structural_codes for reason in unique_reasons)
    passed = structural_valid and not any(reason.startswith("METRIC_REGRESSION_") for reason in unique_reasons)
    return HeldOutMetricsValidation(
        model_kind=kind,
        valid=structural_valid,
        passed=passed,
        full_run=run_value if isinstance(run_value, bool) else None,
        baseline_metrics=baseline_values,
        candidate_metrics=candidate_values,
        hashes=normalised_hashes,
        reason_codes=unique_reasons,
    )


def _coerce_metric_result(value: HeldOutMetricsValidation | Mapping[str, Any]) -> HeldOutMetricsValidation:
    if isinstance(value, HeldOutMetricsValidation):
        return value
    return validate_held_out_metrics(value)


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Immutable, machine-readable promotion gate result."""

    APPROVE: ClassVar[str] = PROMOTION_APPROVE
    REJECT: ClassVar[str] = PROMOTION_REJECT
    INELIGIBLE: ClassVar[str] = PROMOTION_INELIGIBLE

    decision: str
    reason_codes: tuple[str, ...]
    metrics_passed: bool
    training_candidate_count: int

    @property
    def status(self) -> str:
        return self.decision

    @property
    def approved(self) -> bool:
        return self.decision == PROMOTION_APPROVE

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.reason_codes

    def __bool__(self) -> bool:
        return self.approved

    def to_dict(self) -> dict[str, Any]:
        promotion_allowed = self.approved
        return {
            "schema_version": 1,
            "protocol": PROMOTION_DECISION_PROTOCOL,
            "decision": self.decision,
            "status": self.decision,
            "reason_codes": list(self.reason_codes),
            "metrics_passed": self.metrics_passed,
            "training_candidate_count": self.training_candidate_count,
            "authority_boundary": "model_promotion_only_non_answer_authorizing",
            "answer_authorized": False,
            "evidence_authorized": False,
            "training_eligible": False,
            "promotion_allowed": promotion_allowed,
            "submission_eligible": False,
            "release_authorized": False,
        }


def _candidate_count(
    training_candidates: TrainingAdmissionResult | Iterable[Mapping[str, Any]] | None,
) -> tuple[int, list[str]]:
    if isinstance(training_candidates, TrainingAdmissionResult):
        rows = list(training_candidates.candidates)
    elif training_candidates is None:
        return 0, ["TRAINING_CANDIDATES_MISSING"]
    else:
        rows = _as_records(training_candidates)
    reasons: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("training_eligible") is not True:
            reasons.append("UNADMITTED_TRAINING_CANDIDATE")
    if not rows:
        reasons.append("NO_ELIGIBLE_TRAINING_CANDIDATES")
    return len(rows), list(_unique(reasons))


def decide_promotion(
    metrics: HeldOutMetricsValidation | Mapping[str, Any],
    *,
    training_candidates: TrainingAdmissionResult | Iterable[Mapping[str, Any]] | None = None,
) -> PromotionDecision:
    """Return APPROVE, REJECT, or INELIGIBLE without granting answer authority."""

    metric_result = _coerce_metric_result(metrics)
    candidate_count, candidate_reasons = _candidate_count(training_candidates)
    reasons: list[str] = []
    if not metric_result.valid:
        reasons.extend(metric_result.reason_codes)
    elif not metric_result.passed:
        reasons.extend(metric_result.reason_codes)
    reasons.extend(candidate_reasons)
    reasons = list(_unique(reasons))
    if not metric_result.valid or candidate_reasons:
        decision = PROMOTION_INELIGIBLE
    elif not metric_result.passed:
        decision = PROMOTION_REJECT
    else:
        decision = PROMOTION_APPROVE
    return PromotionDecision(
        decision=decision,
        reason_codes=tuple(reasons),
        metrics_passed=metric_result.passed,
        training_candidate_count=candidate_count,
    )


make_promotion_decision = decide_promotion


def _bundle_hashes(
    hashes: Mapping[str, Any] | None,
    explicit: Mapping[str, Any],
) -> tuple[dict[str, str], tuple[str, ...]]:
    source: dict[str, Any] = dict(hashes or {})
    source.update({key: value for key, value in explicit.items() if value is not None})
    output: dict[str, str] = {}
    missing: list[str] = []
    for name in REQUIRED_HASHES:
        value = _hash_descriptor(source, name)
        if value is None or not _valid_sha256(value):
            missing.append(f"{name.upper()}_HASH_MISSING_OR_INVALID")
        else:
            output[f"{name}_sha256"] = _normalise_sha256(value)
    return output, tuple(missing)


def build_model_bundle_manifest(
    *,
    model_name: str,
    model_version: str,
    model_kind: str,
    hashes: Mapping[str, Any] | None = None,
    code_sha256: str | None = None,
    model_sha256: str | None = None,
    index_sha256: str | None = None,
    prompt_sha256: str | None = None,
    input_sha256: str | None = None,
    metrics: HeldOutMetricsValidation | Mapping[str, Any] | None = None,
    promotion_decision: PromotionDecision | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a hash-complete candidate/promoted navigation-model manifest.

    Missing hashes are a hard error.  Approval changes only the model
    lifecycle status and ``promotion_allowed`` flag; answer, evidence,
    submission and release authority remain false in every manifest.
    """

    name = _text(model_name)
    version = _text(model_version)
    kind = _text(model_kind).lower()
    if not name or not version:
        raise ValueError("model_name and model_version are required")
    if kind not in MODEL_KINDS:
        raise ValueError(f"unsupported model_kind: {model_kind!r}")
    bundle_hashes, missing_hashes = _bundle_hashes(
        hashes,
        {
            "code": code_sha256,
            "model": model_sha256,
            "index": index_sha256,
            "prompt": prompt_sha256,
            "input": input_sha256,
        },
    )
    if missing_hashes:
        raise ValueError("model bundle hashes are incomplete: " + ", ".join(missing_hashes))

    metric_result: HeldOutMetricsValidation | None = None
    if metrics is not None:
        metric_result = _coerce_metric_result(metrics)
    decision_payload: dict[str, Any] | None = None
    decision_code: str | None = None
    if promotion_decision is not None:
        if isinstance(promotion_decision, PromotionDecision):
            decision_payload = promotion_decision.to_dict()
        elif isinstance(promotion_decision, Mapping):
            decision_payload = dict(promotion_decision)
        else:
            raise ValueError("promotion_decision must be a PromotionDecision or mapping")
        decision_code = _text(
            decision_payload.get("decision") or decision_payload.get("status")
        ).upper()
        if decision_code not in {
            PROMOTION_APPROVE,
            PROMOTION_REJECT,
            PROMOTION_INELIGIBLE,
        }:
            raise ValueError("promotion_decision has an unsupported decision")
        if decision_code == PROMOTION_APPROVE and metric_result is not None and not metric_result.passed:
            raise ValueError("APPROVE cannot be paired with a failed held-out metrics gate")
        if metric_result is not None and metric_result.model_kind != kind:
            raise ValueError("model bundle kind does not match held-out metrics kind")
    if decision_code == PROMOTION_APPROVE and metric_result is None:
        raise ValueError("APPROVE requires a passed held-out metrics gate")
    if metric_result is not None:
        expected_metric_hashes = {
            f"{key}_sha256": value for key, value in metric_result.hashes.items()
        }
        if any(expected_metric_hashes.get(key) != value for key, value in bundle_hashes.items()):
            raise ValueError("model bundle hashes do not match held-out metrics hashes")

    promoted = decision_code == PROMOTION_APPROVE
    lifecycle_status = "PROMOTED_NAVIGATION_ONLY" if promoted else "CANDIDATE"
    core = {
        "model_name": name,
        "model_version": version,
        "model_kind": kind,
        "hashes": bundle_hashes,
        "lifecycle_status": lifecycle_status,
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "protocol": MODEL_BUNDLE_PROTOCOL,
        "bundle_id": canonical_sha256(core),
        **core,
        "candidate_until_gate": not promoted,
        "artifact_role": "promoted_navigation_only" if promoted else "candidate",
        "model_artifact_candidate_only": not promoted,
        "held_out_metrics": metric_result.to_dict() if metric_result is not None else None,
        "promotion_decision": decision_payload,
        "authority_boundary": "navigation_model_only_non_authorizing",
        "authority_flags": {
            "answer_authorized": False,
            "evidence_authorized": False,
            "training_eligible": False,
            "promotion_allowed": promoted,
            "submission_eligible": False,
            "release_authorized": False,
        },
    }
    manifest.update(manifest["authority_flags"])
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest


create_model_bundle_manifest = build_model_bundle_manifest


__all__ = [
    "HELD_OUT_METRICS_PROTOCOL",
    "MODEL_BUNDLE_PROTOCOL",
    "MODEL_KINDS",
    "MODEL_OPS_PROTOCOL",
    "PROMOTION_APPROVE",
    "PROMOTION_INELIGIBLE",
    "PROMOTION_REJECT",
    "PROMOTION_DECISION_PROTOCOL",
    "PromotionDecision",
    "PromotionDecisionCode",
    "TRAINING_CANDIDATE_PROTOCOL",
    "TrainingAdmissionResult",
    "HeldOutMetricsValidation",
    "admit_feedback_for_training",
    "admit_feedback_records",
    "build_model_bundle_manifest",
    "create_model_bundle_manifest",
    "decide_promotion",
    "make_promotion_decision",
    "validate_held_out_metrics",
]
