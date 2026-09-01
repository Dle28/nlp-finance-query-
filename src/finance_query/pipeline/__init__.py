"""Canonical Proposal → Resolve → E2E → Compile → Feedback orchestration."""

from .contracts import (
    BLOCKED_VERIFICATION_CLASSES,
    CONTEXT_PACKET_PROTOCOL,
    FEEDBACK_PROTOCOL,
    IMPROVEMENT_RECORD_PROTOCOL,
)
from .resolver import (
    RESOLVED_PREDICTION_PROTOCOL,
    ResolverResult,
    resolve_submission_artifacts,
)
from .submission_compiler import SubmissionCompileResult, compile_submission_package
from .flow_audit import FLOW_AUDIT_PROTOCOL, audit_submission_flow, write_flow_audit
from .model_ops import (
    HeldOutMetricsValidation,
    PromotionDecision,
    TrainingAdmissionResult,
    admit_feedback_for_training,
    admit_feedback_records,
    build_model_bundle_manifest,
    decide_promotion,
    validate_held_out_metrics,
)

__all__ = [
    "BLOCKED_VERIFICATION_CLASSES",
    "CONTEXT_PACKET_PROTOCOL",
    "FEEDBACK_PROTOCOL",
    "IMPROVEMENT_RECORD_PROTOCOL",
    "RESOLVED_PREDICTION_PROTOCOL",
    "ResolverResult",
    "SubmissionCompileResult",
    "compile_submission_package",
    "FLOW_AUDIT_PROTOCOL",
    "audit_submission_flow",
    "write_flow_audit",
    "resolve_submission_artifacts",
    "HeldOutMetricsValidation",
    "PromotionDecision",
    "TrainingAdmissionResult",
    "admit_feedback_for_training",
    "admit_feedback_records",
    "build_model_bundle_manifest",
    "decide_promotion",
    "validate_held_out_metrics",
]
