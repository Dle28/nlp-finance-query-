"""Unified submission-first flow and blocked-feedback CLI wiring.

The historical builder remains the compatibility implementation for retrieval,
proposal selection, replay and ZIP creation.  This module gives operators one
stable command while the internal builder is migrated behind the module
contracts in :mod:`finance_query.pipeline`.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from .context.prompt_profiles import DEFAULT_PROMPT_PROFILE, PROMPT_PROFILES
from .feedback.blocked import run_blocked_feedback
from .model_ops import (
    MODEL_OPS_PROTOCOL,
    admit_feedback_records,
    decide_promotion,
    validate_held_out_metrics,
)
from .flow_audit import audit_submission_flow, write_flow_audit


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_ref(path: Path) -> dict[str, Any]:
    """Return a stable reference for a completed flow artifact."""

    resolved = path.resolve()
    reference: dict[str, Any] = {"path": str(resolved), "exists": resolved.is_file()}
    if resolved.is_file():
        reference["sha256"] = _sha256_file(resolved)
        reference["size_bytes"] = resolved.stat().st_size
    return reference


def _write_flow_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Write one immutable orchestration manifest after all stages finish."""

    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


def _model_lifecycle_snapshot(feedback_summary: dict[str, Any]) -> dict[str, Any]:
    """Materialize the feedback -> training -> promotion gates in one record.

    The canonical flow does not train a model inline.  It records the next
    gate explicitly so a valid model response cannot be mistaken for a
    training label or a promoted artifact.
    """

    feedback_manifest_path = feedback_summary.get("manifest_path")
    feedback_records: list[dict[str, Any]] = []
    if feedback_manifest_path:
        manifest_path = Path(str(feedback_manifest_path)).resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outputs = manifest.get("outputs") if isinstance(manifest, dict) else {}
        feedback_descriptor = outputs.get("feedback_records") if isinstance(outputs, dict) else {}
        feedback_path = (
            Path(str(feedback_descriptor.get("path"))).resolve()
            if isinstance(feedback_descriptor, dict) and feedback_descriptor.get("path")
            else manifest_path.parent / "blocked_question_feedback_v1.jsonl"
        )
        if not feedback_path.is_file():
            raise FileNotFoundError(feedback_path)
        feedback_records = _load_jsonl(feedback_path)

    admission = admit_feedback_records(feedback_records)
    metrics_gate = validate_held_out_metrics({})
    promotion = decide_promotion(metrics_gate, training_candidates=admission)
    rejection_counts = Counter(
        str(reason)
        for rejected in admission.rejected
        for reason in rejected.get("reason_codes") or []
    )
    model_id = feedback_summary.get("model_id")
    feedback_status = (
        "PACKETS_PREPARED_NOT_RUN"
        if not model_id
        else "FEEDBACK_COLLECTED_NOT_ADMITTED"
    )
    return {
        "schema_version": 1,
        "protocol": MODEL_OPS_PROTOCOL,
        "status": feedback_status,
        "feedback": {
            "model_id": model_id,
            "model_evaluated_count": int(feedback_summary.get("model_evaluated_count") or 0),
            "valid_feedback_count": int(feedback_summary.get("valid_feedback_count") or 0),
        },
        "training_admission": {
            "status": "NOT_RUN",
            "accepted_count": admission.accepted_count,
            "rejected_count": admission.rejected_count,
            "rejection_reason_counts": dict(sorted(rejection_counts.items())),
            "candidate_protocol": "vifinqa_verified_training_candidate_v1",
            "automatic_weight_update": False,
        },
        "held_out_metrics": {
            "status": "REQUIRED_NOT_RUN",
            "required_metrics": ["recall_at_5", "mrr_at_5"],
            "full_run_required": True,
            "artifact_hashes_required": ["code", "model", "index", "prompt", "input"],
            "gate_reason_codes": list(metrics_gate.reason_codes),
        },
        "promotion": {
            **promotion.to_dict(),
            "status": "INELIGIBLE_METRICS_NOT_RUN",
            "promotion_allowed": False,
        },
        "authority": {
            "feedback_authorizes_training": False,
            "training_candidate_authorizes_model_weights": False,
            "model_load_or_run_authorizes_promotion": False,
            "promotion_authorizes_answer": False,
            "release_authorized": False,
        },
        "next_gate": (
            "attach_source_receipt_or_human_review, run_full_held_out_metrics, "
            "then make an explicit promotion decision"
        ),
    }


def _manifest_payload(
    *,
    output_dir: Path,
    proposal_dir: Path,
    resolver_result: Any,
    observer_result: Any | None,
    compile_result: Any,
    feedback_summary: dict[str, Any],
    flow_release_policy: str,
    flow_audit: dict[str, Any],
    flow_audit_path: Path,
    verification_config: Path | None,
    skip_e2e: bool,
) -> dict[str, Any]:
    """Build a truthful stage/status manifest for one canonical operator run."""

    resolver_manifest_path = resolver_result.manifest_path
    resolver_manifest: dict[str, Any] = {}
    if resolver_manifest_path.is_file():
        resolver_manifest = json.loads(resolver_manifest_path.read_text(encoding="utf-8"))
    migration = resolver_manifest.get("migration") if isinstance(resolver_manifest, dict) else {}
    if not isinstance(migration, dict):
        migration = {}
    observer_manifest_path = observer_result.manifest_path if observer_result else None
    compile_manifest_path = compile_result.output_dir / "submission_compile.manifest.json"
    feedback_status = str(feedback_summary.get("status") or "UNKNOWN")
    model_lifecycle = _model_lifecycle_snapshot(feedback_summary)
    return {
        "schema_version": 1,
        "protocol": "vifinqa_submission_flow_run_manifest_v1",
        "run_status": "COMPLETED" if flow_audit.get("gate_passed") else "FAILED_INTEGRITY",
        "flow_order": [
            "proposal_ast",
            "deterministic_resolver",
            "resolved_prediction",
            "e2e_verification" if observer_result else "e2e_skipped_explicitly",
            "submission_compiler",
            "blocked_feedback",
            "model_lifecycle_gate",
            "pipeline_integrity_audit",
        ],
        "delivery_policy": flow_release_policy,
        "inputs": {
            "verification_config": (
                _artifact_ref(verification_config) if verification_config is not None else None
            ),
            "e2e_required": not skip_e2e,
        },
        "stages": {
            "proposal_compatibility": {
                "status": "COMPLETED",
                "artifact": _artifact_ref(proposal_dir / "build_report.json"),
                "directory": str(proposal_dir.resolve()),
            },
            "resolver": {
                "status": "COMPLETED",
                "manifest": _artifact_ref(resolver_manifest_path),
                "predictions": _artifact_ref(resolver_result.predictions_path),
            },
            "e2e_observer": {
                "status": "COMPLETED" if observer_result else "NOT_RUN",
                "manifest": _artifact_ref(observer_manifest_path) if observer_manifest_path else None,
                "receipts": _artifact_ref(observer_result.receipts_path) if observer_result else None,
            },
            "submission_compiler": {
                "status": "COMPLETED",
                "manifest": _artifact_ref(compile_manifest_path),
                "submission": _artifact_ref(compile_result.submission_path),
                "archive": _artifact_ref(compile_result.archive_path),
            },
            "blocked_feedback": {
                "status": feedback_status,
                "manifest": _artifact_ref(Path(feedback_summary["manifest_path"]))
                if feedback_summary.get("manifest_path")
                else None,
                "valid_feedback_count": feedback_summary.get("valid_feedback_count", 0),
                "model_evaluated_count": feedback_summary.get("model_evaluated_count", 0),
            },
            "model_lifecycle_gate": model_lifecycle,
            "pipeline_integrity_audit": {
                "status": "PASS" if flow_audit.get("gate_passed") else "FAIL",
                "artifact": _artifact_ref(flow_audit_path),
            },
        },
        "migration": {
            "duplicate_execution_legacy": migration.get("duplicate_execution_legacy", True),
            "canonical_decimal_reexecution": migration.get(
                "canonical_decimal_reexecution", False
            ),
            "resolver_backend": migration.get("backend"),
        },
        "authority": {
            "model_output_authorizes_answer": False,
            "e2e_receipt_required_for_verified": True,
            "e2e_required_for_this_run": not skip_e2e,
            "release_authorized": False,
            "feedback_authorizes_training": False,
            "feedback_authorizes_promotion": False,
        },
        "pipeline_integrity": flow_audit,
        "model_lifecycle": model_lifecycle,
        "outputs": {
            "flow_manifest": str((output_dir / "submission_flow.manifest.json").resolve()),
        },
    }


def _add_feedback_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--feedback-model-name",
        default=None,
        help=(
            "Hugging Face model id or local path for blocked-question feedback. "
            "If omitted, only numeric-free feedback packets are prepared."
        ),
    )
    parser.add_argument(
        "--feedback-device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="Device for the optional feedback model.",
    )
    parser.add_argument(
        "--feedback-max-new-tokens",
        type=int,
        default=768,
        help="Maximum tokens generated for one feedback JSON response.",
    )
    parser.add_argument(
        "--feedback-prompt-profile",
        choices=tuple(PROMPT_PROFILES),
        default=DEFAULT_PROMPT_PROFILE,
        help="Bilingual prompt contract used by the feedback critic.",
    )
    parser.add_argument(
        "--feedback-max-questions",
        type=int,
        default=None,
        help="Optional bounded prefix of strict-blocked questions to evaluate.",
    )
    parser.add_argument(
        "--feedback-output-dir",
        type=Path,
        default=None,
        help="Feedback artifact directory; defaults to <submission>/blocked_feedback.",
    )
    parser.add_argument(
        "--feedback-prepare-only",
        action="store_true",
        help="Compile packets/prompts without loading or invoking a model.",
    )
    return parser


def configure_submission_flow_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add canonical flow options on top of the compatibility builder options."""

    from ..e2e.submission_pipeline import configure_parser as configure_submission_parser

    configure_submission_parser(parser)
    parser.add_argument(
        "--flow-release-policy",
        choices=("best_effort", "strict"),
        default="best_effort",
        help=(
            "Delivery policy applied only after the resolved ledger and E2E receipt. "
            "best_effort keeps coverage; strict marks any non-VERIFIED row blocked."
        ),
    )
    parser.add_argument(
        "--skip-e2e",
        action="store_true",
        help=(
            "Explicitly prepare a candidate-only flow without the independent E2E stage. "
            "This is diagnostic/best-effort only and cannot be used with strict policy."
        ),
    )
    return _add_feedback_arguments(parser)


def configure_blocked_feedback_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Configure the standalone feedback command."""

    parser.add_argument(
        "--submission-dir",
        type=Path,
        required=True,
        help="Completed build-submission output containing diagnostics and ledgers.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New immutable blocked-feedback artifact directory.",
    )
    _add_feedback_arguments(parser)
    return parser


def _load_feedback_model(args: argparse.Namespace) -> tuple[Any | None, str | None]:
    model_name = getattr(args, "feedback_model_name", None)
    if not model_name or getattr(args, "feedback_prepare_only", False):
        return None, None
    from ..research.llm.qwen_inference import QwenGenerator

    model = QwenGenerator(
        model_name,
        int(getattr(args, "feedback_max_new_tokens", 768)),
        device=str(getattr(args, "feedback_device", "auto")),
    )
    return model, model_name


def evaluate_blocked_submission(args: argparse.Namespace) -> dict[str, Any]:
    """Run or prepare model feedback for strict-blocked submission rows."""

    model, model_id = _load_feedback_model(args)
    output_dir = Path(args.output_dir)
    result = run_blocked_feedback(
        submission_dir=Path(args.submission_dir),
        output_dir=output_dir,
        model=model,
        model_id=model_id,
        prompt_profile=str(args.feedback_prompt_profile),
        max_questions=args.feedback_max_questions,
        prepare_only=bool(args.feedback_prepare_only or model is None),
    )
    summary_path = result.output_dir / "feedback_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["manifest_path"] = str(result.manifest_path)
    return summary


def run_submission_flow(args: argparse.Namespace) -> dict[str, Any]:
    """Run Proposal → Resolve → E2E → Compile → Feedback as one flow.

    The historical builder is invoked in a sibling compatibility directory
    with its internal E2E callback disabled.  This prevents it from being the
    final delivery authority while the resolver/observer/compiler modules are
    migrated into the canonical path.
    """

    from ..e2e.submission_pipeline import build_primary_submission
    from .e2e_observer import run_e2e_observer
    from .resolver import resolve_submission_artifacts
    from .submission_compiler import compile_submission_package

    output_dir = Path(args.output).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite canonical flow output: {output_dir}")
    flow_release_policy = str(getattr(args, "flow_release_policy", "best_effort"))
    verification_config_value = getattr(args, "verification_config", None)
    verification_config = (
        Path(verification_config_value).expanduser().resolve()
        if verification_config_value is not None
        else None
    )
    verification_certificate = getattr(args, "verification_certificate", None)
    skip_e2e = bool(getattr(args, "skip_e2e", False))
    feedback_model_name = getattr(args, "feedback_model_name", None)
    if verification_certificate is not None:
        raise ValueError(
            "run-submission-flow accepts --verification-config only; "
            "use a fresh canonical E2E observer receipt for this flow"
        )
    if flow_release_policy == "strict" and (verification_config is None or skip_e2e):
        raise ValueError(
            "strict canonical flow requires --verification-config so an independent E2E receipt can be produced"
        )
    if feedback_model_name and verification_config is None:
        raise ValueError(
            "model feedback requires --verification-config; prepare-only feedback may run without E2E"
        )
    if verification_config is None and not skip_e2e:
        raise ValueError(
            "run-submission-flow requires --verification-config for independent E2E; "
            "use --skip-e2e only for an explicitly candidate-only preparation run"
        )
    if verification_config is not None and skip_e2e:
        raise ValueError("--skip-e2e cannot be combined with --verification-config")
    if verification_config is not None and not verification_config.is_file():
        raise FileNotFoundError(f"verification config does not exist: {verification_config}")
    if bool(getattr(args, "require_full_population", False)) and getattr(
        args, "expected_question_count", None
    ) is None:
        raise ValueError(
            "--require-full-population requires --expected-question-count on the unified flow"
        )
    proposal_dir = output_dir.with_name(output_dir.name + ".proposal_compatibility")
    builder_args = copy(args)
    builder_args.output = proposal_dir
    # The old builder performs its own in-loop checks.  It must not run the
    # canonical source-closure E2E before the new resolver hand-off.
    builder_args.verification_config = None
    builder_args.verification_certificate = None
    build_primary_submission(builder_args)

    resolver_result = resolve_submission_artifacts(
        proposal_dir,
        output_dir=output_dir / "resolver",
    )
    observer_result = None
    if verification_config is not None:
        observer_result = run_e2e_observer(
            resolved_predictions_path=resolver_result.predictions_path,
            base_config=Path(verification_config),
            output_dir=output_dir / "e2e_observer",
        )
    compile_result = compile_submission_package(
        proposal_dir=proposal_dir,
        resolved_predictions_path=resolver_result.predictions_path,
        e2e_receipts_path=observer_result.receipts_path if observer_result else None,
        output_dir=output_dir / "compiled_submission",
        policy=flow_release_policy,
    )

    feedback_output_dir = getattr(args, "feedback_output_dir", None)
    if feedback_output_dir is None:
        feedback_output_dir = output_dir / "blocked_feedback"
    feedback_output_dir = Path(feedback_output_dir).expanduser().resolve()
    feedback_args = argparse.Namespace(
        submission_dir=compile_result.output_dir,
        output_dir=feedback_output_dir,
        feedback_model_name=getattr(args, "feedback_model_name", None),
        feedback_device=getattr(args, "feedback_device", "auto"),
        feedback_max_new_tokens=getattr(args, "feedback_max_new_tokens", 768),
        feedback_prompt_profile=getattr(
            args,
            "feedback_prompt_profile",
            DEFAULT_PROMPT_PROFILE,
        ),
        feedback_max_questions=getattr(args, "feedback_max_questions", None),
        feedback_prepare_only=getattr(args, "feedback_prepare_only", False),
    )
    feedback_summary = evaluate_blocked_submission(feedback_args)
    model_lifecycle = _model_lifecycle_snapshot(feedback_summary)
    expected_question_count = getattr(args, "expected_question_count", None)
    flow_audit = audit_submission_flow(
        proposal_dir=proposal_dir,
        resolver_dir=resolver_result.output_dir,
        observer_dir=observer_result.output_dir if observer_result else None,
        compile_dir=compile_result.output_dir,
        feedback_dir=Path(feedback_output_dir),
        expected_question_count=(
            int(expected_question_count) if expected_question_count is not None else None
        ),
        require_e2e=not skip_e2e,
    )
    flow_audit_path = write_flow_audit(
        output_dir / "pipeline_integrity_audit_v1.json",
        flow_audit,
    )
    result = {
        "submission_dir": str(compile_result.output_dir.resolve()),
        "proposal_compatibility_dir": str(proposal_dir.resolve()),
        "resolver_dir": str(resolver_result.output_dir.resolve()),
        "e2e_observer_dir": str(observer_result.output_dir.resolve()) if observer_result else None,
        "feedback_dir": str(Path(feedback_output_dir).resolve()),
        "flow_order": [
            "proposal_ast",
            "deterministic_resolver",
            "resolved_prediction",
            "e2e_verification" if observer_result else "e2e_skipped_explicitly",
            "submission_compiler",
            "blocked_feedback",
            "model_lifecycle_gate",
            "pipeline_integrity_audit",
        ],
        "flow_authority": {
            "model_output_authorizes_answer": False,
            "e2e_receipt_required_for_verified": True,
            "release_authorized": False,
        },
        "feedback": feedback_summary,
        "model_lifecycle": model_lifecycle,
        "pipeline_integrity": {
            "status": flow_audit["status"],
            "gate_passed": flow_audit["gate_passed"],
            "report_path": str(flow_audit_path),
            "error_count": len(flow_audit["errors"]),
        },
    }
    flow_manifest_path = output_dir / "submission_flow.manifest.json"
    _write_flow_manifest(
        flow_manifest_path,
        _manifest_payload(
            output_dir=output_dir,
            proposal_dir=proposal_dir,
            resolver_result=resolver_result,
            observer_result=observer_result,
            compile_result=compile_result,
            feedback_summary=feedback_summary,
            flow_release_policy=flow_release_policy,
            flow_audit=flow_audit,
            flow_audit_path=flow_audit_path,
            verification_config=verification_config,
            skip_e2e=skip_e2e,
        ),
    )
    result["flow_manifest_path"] = str(flow_manifest_path.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not flow_audit["gate_passed"]:
        raise RuntimeError(
            "unified submission flow failed pipeline integrity audit; "
            f"see {flow_audit_path}"
        )
    return result


__all__ = [
    "configure_blocked_feedback_parser",
    "configure_submission_flow_parser",
    "evaluate_blocked_submission",
    "run_submission_flow",
]
