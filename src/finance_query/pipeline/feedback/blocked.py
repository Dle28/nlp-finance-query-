"""Model-assisted feedback for questions blocked by the E2E policy.

This is deliberately a feedback lane, not a second verifier.  The model sees
the raw Vietnamese question, typed/provenance metadata, candidate labels and
the deterministic E2E receipt.  It may classify the blockage and recommend a
next experiment.  It may not emit a numeric answer, evidence certificate,
training label or promotion decision.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from ..contracts import (
    FEEDBACK_ACTIONS,
    FEEDBACK_CONFIDENCES,
    FEEDBACK_CONTEXT_FIELDS,
    FEEDBACK_DECISIONS,
    FEEDBACK_FAILURE_CLASSES,
    FEEDBACK_MANIFEST_PROTOCOL,
    FEEDBACK_PROTOCOL,
    FEEDBACK_REASON_CODES,
    IMPROVEMENT_RECORD_PROTOCOL,
    assert_no_authority_upgrade,
    canonical_sha256,
    non_authorizing_flags,
)
from ..context.compiler import compile_blocked_question_packets, compile_feedback_prompt
from ..context.prompt_profiles import DEFAULT_PROMPT_PROFILE


FEEDBACK_STATUS_VALID = "VALID_FEEDBACK"
FEEDBACK_STATUS_ABSTAIN = "MODEL_ABSTAIN"
FEEDBACK_STATUS_NOT_RUN = "MODEL_NOT_RUN"
FEEDBACK_STATUS_ERROR = "MODEL_RUNTIME_ERROR"
FEEDBACK_STATUS_INVALID = "MODEL_OUTPUT_INVALID"

ModelInvoker = Callable[[str], object]


# ``assert_no_authority_upgrade`` is the shared contract guard.  These extra
# names cover numeric/evidence-shaped keys which are intentionally not part of
# the shared authority helper because that helper is also used by other
# contracts.  They are checked recursively before the exact feedback schema is
# validated, so a nested attempt cannot be hidden behind an unknown key.
_FEEDBACK_FORBIDDEN_KEYS = frozenset(
    {
        "authority",
        "authority_boundary",
        "certificate_id",
        "evidence_value",
        "is_verified",
        "numeric_cells",
        "numeric_value",
        "raw_numeric_value",
        "source_cell_value",
        "source_numeric_value",
        "value",
        "verified",
    }
)

_MAX_MODEL_OUTPUT_CHARS = 64_000
_FEEDBACK_REQUIRED_KEYS = frozenset(
    {
        "decision",
        "failure_class",
        "reason_codes",
        "observations",
        "missing_context_fields",
        "recommended_actions",
        "confidence",
        "source_ref_sha256",
    }
)


def _reject_non_standard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _remove_markdown_json_fences(text: str) -> str:
    """Remove complete line-based JSON fences while preserving text offsets.

    The model is instructed not to use markdown, but small open-weight models
    often emit `````json`` wrappers.  Removing only complete, line-based JSON
    fences is deliberately narrow: an unclosed fence or an unsupported fence
    language remains an invalid model response instead of being guessed at.
    """

    lines = text.splitlines(keepends=True)
    if not any(line.strip().startswith("```") for line in lines):
        return text

    output: list[str] = []
    fence_count = 0
    for line in lines:
        content = line.rstrip("\r\n")
        stripped = content.strip()
        if not stripped.startswith("```"):
            output.append(line)
            continue
        language = stripped[3:].strip()
        if language.casefold() not in {"", "json"}:
            raise ValueError("unsupported markdown code fence; expected json")
        fence_count += 1
        # Keep line length/newline stable so candidate spans remain easy to
        # diagnose without retaining the wrapper in the parsed value.
        output.append(" " * len(content) + line[len(content) :])

    if fence_count % 2:
        raise ValueError("unclosed markdown code fence")
    return "".join(output)


def _json_object_spans(text: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Return every syntactically complete JSON-object span in ``text``.

    Scanning every opening brace lets us tolerate harmless prose around the
    object.  Nested objects are expected in a valid response, so callers must
    reduce these spans to outermost roots before enforcing the one-object rule.
    """

    decoder = json.JSONDecoder(parse_constant=_reject_non_standard_json_constant)
    spans: list[tuple[int, int, dict[str, Any]]] = []
    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, relative_end = decoder.raw_decode(text[start:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            spans.append((start, start + relative_end, value))
    return spans


def _outermost_json_object_spans(
    spans: list[tuple[int, int, dict[str, Any]]],
) -> list[tuple[int, int, dict[str, Any]]]:
    outermost: list[tuple[int, int, dict[str, Any]]] = []
    for candidate in spans:
        start, end, _ = candidate
        contained = any(
            other_start <= start
            and end <= other_end
            and (other_start, other_end) != (start, end)
            for other_start, other_end, _ in spans
        )
        if not contained:
            outermost.append(candidate)
    return sorted(outermost, key=lambda item: item[0])


def parse_feedback_response(raw_response: object) -> dict[str, Any]:
    """Safely parse one model feedback object from a raw model response.

    The parser accepts a mapping from test/local adapters and accepts a single
    JSON object surrounded by harmless prose or a complete `````json`` fence.
    It rejects multiple objects, malformed/ambiguous brace-delimited output,
    unsupported fences, non-standard JSON constants and non-object values.
    The parser also rejects authority-shaped and unknown top-level keys. Full
    enum, field-value and source-hash validation is intentionally performed by
    :func:`validate_feedback_response` after parsing.
    """

    if isinstance(raw_response, Mapping):
        response = dict(raw_response)
        assert_no_authority_upgrade(response)
        _assert_feedback_numeric_and_authority_keys(response)
        if set(response) != _FEEDBACK_REQUIRED_KEYS:
            raise ValueError("feedback keys differ from contract")
        return response
    if not isinstance(raw_response, str):
        raise ValueError("model feedback must be a JSON object or JSON text")
    if len(raw_response) > _MAX_MODEL_OUTPUT_CHARS:
        raise ValueError("model feedback output is too long")
    text = raw_response.strip()
    if not text:
        raise ValueError("model feedback output is empty")
    text = _remove_markdown_json_fences(text)
    spans = _outermost_json_object_spans(_json_object_spans(text))
    if not spans:
        raise ValueError("model feedback did not contain a complete JSON object")
    if len(spans) != 1:
        raise ValueError("model feedback must contain exactly one JSON object")

    start, end, response = spans[0]
    outside = text[:start] + text[end:]
    # Curly/square delimiters outside the selected object are not harmless
    # prose: they can hide a malformed or second structured response.  Reject
    # them fail-closed while still allowing ordinary leading/trailing text.
    if any(character in outside for character in "{}[]"):
        raise ValueError("model feedback contains extra structured delimiters")
    assert_no_authority_upgrade(response)
    _assert_feedback_numeric_and_authority_keys(response)
    if set(response) != _FEEDBACK_REQUIRED_KEYS:
        raise ValueError("feedback keys differ from contract")
    return response


def _assert_feedback_numeric_and_authority_keys(value: object, path: str = "$") -> None:
    """Reject extra numeric/evidence/authority-shaped keys recursively."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold()
            if (
                normalized in _FEEDBACK_FORBIDDEN_KEYS
                or normalized.endswith("_value")
                or normalized.endswith("_certificate")
            ):
                raise ValueError(f"feedback output crosses authority boundary at {path}.{key}")
            _assert_feedback_numeric_and_authority_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_feedback_numeric_and_authority_keys(child, f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class BlockedFeedbackResult:
    """Paths and counts emitted by one immutable blocked-feedback run."""

    output_dir: Path
    manifest_path: Path
    packet_count: int
    evaluated_count: int
    valid_count: int


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: object, *, label: str, max_length: int = 800) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{label} is too long")
    return value


def _string_list(
    value: object,
    *,
    label: str,
    allowed: set[str] | frozenset[str] | None = None,
    max_items: int = 8,
    max_length: int = 300,
) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError(f"{label} must be a list with at most {max_items} items")
    result: list[str] = []
    for item in value:
        text = _text(item, label=label, max_length=max_length)
        if allowed is not None and text not in allowed:
            raise ValueError(f"{label} contains unsupported value {text!r}")
        result.append(text)
    return result


def _allowed_source_refs(packet: Mapping[str, Any]) -> set[str]:
    values = packet.get("allowed_source_refs")
    if not isinstance(values, list):
        raise ValueError("context packet lacks allowed_source_refs")
    return {str(value) for value in values}


def validate_feedback_response(
    response: Mapping[str, Any],
    *,
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a model's feedback-only JSON against the packet contract."""

    if not isinstance(response, Mapping):
        raise ValueError("model feedback must be a JSON object")
    # Keep the shared guard and apply feedback-specific numeric/evidence key
    # checks before the exact-key check.  A forbidden field must never become
    # an ordinary "unknown key" that downstream code might accidentally keep.
    assert_no_authority_upgrade(response)
    _assert_feedback_numeric_and_authority_keys(response)
    if set(response) != _FEEDBACK_REQUIRED_KEYS:
        raise ValueError(
            f"feedback keys differ from contract: {sorted(set(response) ^ _FEEDBACK_REQUIRED_KEYS)}"
        )
    decision = _text(response.get("decision"), label="decision", max_length=40)
    if decision not in FEEDBACK_DECISIONS:
        raise ValueError(f"unsupported feedback decision: {decision}")
    failure_class = _text(response.get("failure_class"), label="failure_class", max_length=80)
    if failure_class not in FEEDBACK_FAILURE_CLASSES:
        raise ValueError(f"unsupported feedback failure_class: {failure_class}")
    reason_codes = _string_list(
        response.get("reason_codes"),
        label="reason_codes",
        allowed=FEEDBACK_REASON_CODES,
        max_items=5,
        max_length=80,
    )
    if not reason_codes:
        raise ValueError("feedback requires at least one reason_code")
    observations = _string_list(
        response.get("observations"),
        label="observations",
        max_items=6,
        max_length=500,
    )
    missing_fields = _string_list(
        response.get("missing_context_fields"),
        label="missing_context_fields",
        allowed=FEEDBACK_CONTEXT_FIELDS,
        max_items=8,
        max_length=80,
    )
    actions = response.get("recommended_actions")
    if not isinstance(actions, list) or len(actions) > 4:
        raise ValueError("recommended_actions must contain at most four objects")
    normalized_actions: list[dict[str, str]] = []
    for action in actions:
        if not isinstance(action, Mapping) or set(action) != {"action", "target", "rationale"}:
            raise ValueError("each recommended action must have action, target and rationale")
        action_name = _text(action.get("action"), label="action", max_length=80)
        if action_name not in FEEDBACK_ACTIONS:
            raise ValueError(f"unsupported feedback action: {action_name}")
        normalized_actions.append(
            {
                "action": action_name,
                "target": _text(action.get("target"), label="target", max_length=160),
                "rationale": _text(action.get("rationale"), label="rationale", max_length=500),
            }
        )
    confidence = _text(response.get("confidence"), label="confidence", max_length=20)
    if confidence not in FEEDBACK_CONFIDENCES:
        raise ValueError(f"unsupported feedback confidence: {confidence}")
    source_refs = _string_list(
        response.get("source_ref_sha256"),
        label="source_ref_sha256",
        max_items=12,
        max_length=64,
    )
    allowed_refs = _allowed_source_refs(packet)
    if any(
        len(value) != 64
        or not re.fullmatch(r"[0-9a-f]{64}", value)
        or value not in allowed_refs
        for value in source_refs
    ):
        raise ValueError("feedback cites a source reference outside the context packet")
    if decision == "ABSTAIN" and failure_class != "ABSTAIN":
        raise ValueError("ABSTAIN feedback must use failure_class=ABSTAIN")
    if decision == "FEEDBACK_ONLY" and failure_class == "ABSTAIN":
        raise ValueError("FEEDBACK_ONLY cannot use failure_class=ABSTAIN")
    return {
        "decision": decision,
        "failure_class": failure_class,
        "reason_codes": reason_codes,
        "observations": observations,
        "missing_context_fields": missing_fields,
        "recommended_actions": normalized_actions,
        "confidence": confidence,
        "source_ref_sha256": source_refs,
    }


def _fallback_feedback(*, reason_code: str, observation: str) -> dict[str, Any]:
    return {
        "decision": "ABSTAIN",
        "failure_class": "ABSTAIN",
        "reason_codes": [reason_code],
        "observations": [observation],
        "missing_context_fields": [],
        "recommended_actions": [
            {
                "action": "REQUIRE_HUMAN_REVIEW",
                "target": "feedback.review_queue",
                "rationale": "Model feedback is unavailable or invalid; retain the blocked item.",
            }
        ],
        "confidence": "LOW",
        "source_ref_sha256": [],
    }


def _feedback_record(
    *,
    packet: Mapping[str, Any],
    prompt: str,
    model_id: str | None,
    model_status: str,
    feedback: Mapping[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "protocol": FEEDBACK_PROTOCOL,
        "question_id": int(packet["question_id"]),
        "packet_id": packet["packet_id"],
        "prompt_profile": packet.get("prompt_profile"),
        "prompt_sha256": canonical_sha256(prompt),
        "model_id": model_id,
        "model_status": model_status,
        "feedback": dict(feedback),
        "error": error,
        "authority_boundary": "feedback_only_non_authorizing",
        **non_authorizing_flags(),
    }
    return {**payload, "feedback_id": canonical_sha256(payload)}


def _improvement_record(record: Mapping[str, Any]) -> dict[str, Any]:
    feedback = record.get("feedback") if isinstance(record.get("feedback"), Mapping) else {}
    payload: dict[str, Any] = {
        "schema_version": 1,
        "protocol": IMPROVEMENT_RECORD_PROTOCOL,
        "feedback_id": record.get("feedback_id"),
        "question_id": record.get("question_id"),
        "packet_id": record.get("packet_id"),
        "failure_class": feedback.get("failure_class"),
        "reason_codes": list(feedback.get("reason_codes") or []),
        "recommended_actions": list(feedback.get("recommended_actions") or []),
        "missing_context_fields": list(feedback.get("missing_context_fields") or []),
        "next_consumer": "prompt_retrieval_ast_experiment",
        "requires_human_verification": True,
        "learning_mode": "feedback_candidate_not_weight_update",
        **non_authorizing_flags(),
    }
    return {**payload, "improvement_record_id": canonical_sha256(payload)}


def _input_records(submission_dir: Path) -> dict[str, dict[str, Any]]:
    names = (
        "submission.json",
        "diagnostics.jsonl",
        "prediction_audit_ledger_v1.jsonl",
        "best_surviving_candidates_v1.jsonl",
        "build_report.json",
    )
    records: dict[str, dict[str, Any]] = {}
    for name in names:
        path = submission_dir / name
        if not path.is_file():
            if name == "best_surviving_candidates_v1.jsonl" or name == "build_report.json":
                continue
            raise FileNotFoundError(path)
        records[name] = {"path": str(path.resolve()), "sha256": _sha256_file(path)}
    for name in (
        "resolved_predictions_v1.jsonl",
        "e2e_receipts_v1.jsonl",
        "submission_compile_report.json",
        "submission_compile.manifest.json",
    ):
        path = submission_dir / name
        if path.is_file():
            records[name] = {"path": str(path.resolve()), "sha256": _sha256_file(path)}
    return records


def _summary(
    *,
    packets: list[Mapping[str, Any]],
    records: list[Mapping[str, Any]],
    improvements: list[Mapping[str, Any]],
    model_id: str | None,
) -> dict[str, Any]:
    status_counts = Counter(str(row.get("model_status")) for row in records)
    failure_counts = Counter(
        str((row.get("feedback") or {}).get("failure_class"))
        for row in records
        if isinstance(row.get("feedback"), Mapping)
    )
    action_counts = Counter(
        str(action.get("action"))
        for row in records
        if isinstance(row.get("feedback"), Mapping)
        for action in row["feedback"].get("recommended_actions") or []
        if isinstance(action, Mapping)
    )
    return {
        "schema_version": 1,
        "protocol": FEEDBACK_MANIFEST_PROTOCOL,
        "status": "COMPLETED_MODEL_FEEDBACK_NON_AUTHORIZING" if model_id else "PREPARED_MODEL_FEEDBACK_NOT_RUN",
        "model_id": model_id,
        "blocked_count": len(packets),
        "feedback_record_count": len(records),
        "improvement_record_count": len(improvements),
        "model_evaluated_count": sum(
            row.get("model_status") in {FEEDBACK_STATUS_VALID, FEEDBACK_STATUS_ABSTAIN}
            for row in records
        ),
        "valid_feedback_count": sum(row.get("model_status") == FEEDBACK_STATUS_VALID for row in records),
        "model_status_counts": dict(sorted(status_counts.items())),
        "failure_class_counts": dict(sorted(failure_counts.items())),
        "recommended_action_counts": dict(sorted(action_counts.items())),
        "authority_boundary": "feedback_only_non_authorizing",
        **non_authorizing_flags(),
    }


def _improvement_plan(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate model feedback into proposed, not-yet-run experiments."""

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        feedback = record.get("feedback")
        if not isinstance(feedback, Mapping):
            continue
        failure_class = str(feedback.get("failure_class") or "ABSTAIN")
        for action in feedback.get("recommended_actions") or []:
            if not isinstance(action, Mapping):
                continue
            action_name = str(action.get("action") or "NO_ACTION")
            target = str(action.get("target") or "unknown")
            key = (action_name, target)
            group = grouped.setdefault(
                key,
                {
                    "action": action_name,
                    "target": target,
                    "rationales": [],
                    "failure_classes": set(),
                    "question_ids": set(),
                    "feedback_ids": set(),
                },
            )
            rationale = str(action.get("rationale") or "").strip()
            if rationale and rationale not in group["rationales"]:
                group["rationales"].append(rationale)
            group["failure_classes"].add(failure_class)
            group["question_ids"].add(int(record["question_id"]))
            group["feedback_ids"].add(str(record["feedback_id"]))
    experiments: list[dict[str, Any]] = []
    for group in sorted(grouped.values(), key=lambda value: (value["action"], value["target"])):
        experiments.append(
            {
                "action": group["action"],
                "target": group["target"],
                "rationales": sorted(group["rationales"]),
                "failure_classes": sorted(group["failure_classes"]),
                "question_ids": sorted(group["question_ids"]),
                "feedback_ids": sorted(group["feedback_ids"]),
                "status": "PROPOSED_NOT_RUN",
                "requires_human_verification": True,
                "training_eligible": False,
                "promotion_allowed": False,
            }
        )
    return {
        "schema_version": 1,
        "protocol": "vifinqa_improvement_plan_v1",
        "experiment_count": len(experiments),
        "experiments": experiments,
        "learning_mode": "aggregate_feedback_for_next_version",
        "automatic_weight_update": False,
        "human_verification_required": True,
        "promotion_allowed": False,
    }


def run_blocked_feedback(
    *,
    submission_dir: Path,
    output_dir: Path,
    model: ModelInvoker | None = None,
    model_id: str | None = None,
    prompt_profile: str = DEFAULT_PROMPT_PROFILE,
    max_questions: int | None = None,
    prepare_only: bool = False,
) -> BlockedFeedbackResult:
    """Evaluate all strict-blocked questions with a model, or prepare packets.

    ``model`` is a callable so tests and remote inference workers can use the
    same contract.  The local Qwen adapter is wired by the CLI, not imported at
    package load.  Every runtime or schema failure becomes an explicit ABSTAIN
    feedback record and does not stop the remaining questions.
    """

    submission_dir = submission_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite feedback output: {output_dir}")
    if model is not None and not model_id:
        raise ValueError("model_id is required when model is configured")
    if model is not None and prepare_only:
        raise ValueError("prepare_only cannot be combined with a model")
    packets = compile_blocked_question_packets(
        submission_dir,
        prompt_profile=prompt_profile,
        max_questions=max_questions,
    )
    input_records = _input_records(submission_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        packet_path = staging / "blocked_question_context_packets_v1.jsonl"
        feedback_path = staging / "blocked_question_feedback_v1.jsonl"
        improvement_path = staging / "improvement_feedback_records_v1.jsonl"
        plan_path = staging / "improvement_plan_v1.json"
        _write_jsonl(packet_path, packets)
        records: list[dict[str, Any]] = []
        improvements: list[dict[str, Any]] = []
        for packet in packets:
            prompt = compile_feedback_prompt(packet, profile_name=prompt_profile)
            if model is None:
                feedback = _fallback_feedback(
                    reason_code="MODEL_UNCERTAIN",
                    observation="No feedback model was configured; packet prepared for a model worker.",
                )
                record = _feedback_record(
                    packet=packet,
                    prompt=prompt,
                    model_id=None,
                    model_status=FEEDBACK_STATUS_NOT_RUN,
                    feedback=feedback,
                )
            else:
                try:
                    raw_response = model(prompt)
                    parsed_response = parse_feedback_response(raw_response)
                    feedback = validate_feedback_response(parsed_response, packet=packet)
                except Exception as error:  # keep one bad model response from hiding other blockers
                    status = (
                        FEEDBACK_STATUS_INVALID
                        if isinstance(error, ValueError)
                        else FEEDBACK_STATUS_ERROR
                    )
                    feedback = _fallback_feedback(
                        reason_code=(
                            "MODEL_OUTPUT_INVALID"
                            if status == FEEDBACK_STATUS_INVALID
                            else "MODEL_RUNTIME_ERROR"
                        ),
                        observation=f"Feedback model did not produce usable contract output: {error}",
                    )
                    record = _feedback_record(
                        packet=packet,
                        prompt=prompt,
                        model_id=model_id,
                        model_status=status,
                        feedback=feedback,
                        error=str(error),
                    )
                else:
                    record = _feedback_record(
                        packet=packet,
                        prompt=prompt,
                        model_id=model_id,
                        model_status=(
                            FEEDBACK_STATUS_ABSTAIN
                            if feedback["decision"] == "ABSTAIN"
                            else FEEDBACK_STATUS_VALID
                        ),
                        feedback=feedback,
                    )
            records.append(record)
            improvements.append(_improvement_record(record))
        _write_jsonl(feedback_path, records)
        _write_jsonl(improvement_path, improvements)
        improvement_plan = _improvement_plan(records)
        _write_json(plan_path, improvement_plan)
        summary = _summary(
            packets=packets,
            records=records,
            improvements=improvements,
            model_id=model_id,
        )
        summary_path = staging / "feedback_summary.json"
        _write_json(summary_path, summary)
        manifest = {
            **summary,
            "inputs": input_records,
            "outputs": {
                "context_packets": {
                    "path": str(output_dir / packet_path.name),
                    "sha256": _sha256_file(staging / packet_path.name),
                },
                "feedback_records": {
                    "path": str(output_dir / feedback_path.name),
                    "sha256": _sha256_file(staging / feedback_path.name),
                },
                "improvement_records": {
                    "path": str(output_dir / improvement_path.name),
                    "sha256": _sha256_file(staging / improvement_path.name),
                },
                "improvement_plan": {
                    "path": str(output_dir / plan_path.name),
                    "sha256": _sha256_file(staging / plan_path.name),
                },
                "summary": {
                    "path": str(output_dir / summary_path.name),
                    "sha256": _sha256_file(staging / summary_path.name),
                },
            },
            "contract": {
                "prompt_profile": prompt_profile,
                "blocked_classes": ["PARTIAL", "UNRESOLVED", "REJECTED", "ABSTAIN"],
                "numeric_cells_in_prompt": False,
                "model_output_may_authorize": False,
                "model_output_may_train_without_human": False,
            },
        }
        manifest_path = staging / "blocked_feedback_run.manifest.json"
        _write_json(manifest_path, manifest)
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BlockedFeedbackResult(
        output_dir=output_dir,
        manifest_path=output_dir / manifest_path.name,
        packet_count=len(packets),
        evaluated_count=sum(
            row.get("model_status") in {FEEDBACK_STATUS_VALID, FEEDBACK_STATUS_ABSTAIN}
            for row in records
        ),
        valid_count=sum(row.get("model_status") == FEEDBACK_STATUS_VALID for row in records),
    )


__all__ = [
    "BlockedFeedbackResult",
    "parse_feedback_response",
    "run_blocked_feedback",
    "validate_feedback_response",
]
