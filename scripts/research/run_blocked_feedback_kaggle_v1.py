#!/usr/bin/env python3
"""Run the real Qwen feedback critic on Kaggle.

This worker intentionally contains its own small validator so the Kaggle
artifact is portable.  It never accepts a numeric answer, evidence value,
certificate, training flag, promotion flag, or release decision from the
model.  Invalid output becomes an explicit ABSTAIN feedback record.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
import zipfile

try:
    # The package script copies this dependency beside the worker so that the
    # Kaggle input is runnable without installing this repository.
    from feedback_json_parser import parse_one_json_object
except ImportError:  # local source checkout / unit-test fallback
    from finance_query.research.llm.json_output import parse_one_json_object


MODEL_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
MODEL_REVISION = "2e1fd397ee46e1388853d2af2c993145b0f1098a"
DEFAULT_BATCH_SIZE = 1
DEFAULT_MAX_INPUT_TOKENS = 4096
DEFAULT_MAX_NEW_TOKENS = 192

FEEDBACK_DECISIONS = {"FEEDBACK_ONLY", "ABSTAIN"}
FAILURE_CLASSES = {
    "RETRIEVAL_MISS",
    "RERANK_MISS",
    "CONTEXT_INCOMPLETE",
    "SEMANTIC_AMBIGUITY",
    "TEMPORAL_OR_SCOPE_AMBIGUITY",
    "UNIT_AMBIGUITY",
    "FORMULA_OR_OPERAND",
    "PROVENANCE_OR_SOURCE_CONFLICT",
    "AST_OR_FORMAT",
    "DETERMINISTIC_REPLAY",
    "INSUFFICIENT_EVIDENCE",
    "ABSTAIN",
    "OTHER",
}
CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
REASON_CODES = {
    "MODEL_UNCERTAIN",
    "MODEL_RUNTIME_ERROR",
    "MODEL_OUTPUT_INVALID",
    "INSUFFICIENT_PACKET_EVIDENCE",
    "BLOCKED_BY_SOURCE_BINDING",
    "BLOCKED_BY_SEMANTIC_BINDING",
    "BLOCKED_BY_TEMPORAL_BINDING",
    "BLOCKED_BY_SCOPE_OR_ENTITY",
    "BLOCKED_BY_UNIT",
    "BLOCKED_BY_FORMULA",
    "BLOCKED_BY_OPERAND",
    "BLOCKED_BY_REPLAY",
    "BLOCKED_BY_PROVENANCE",
    "SUPPORTED_BY_RECEIPT",
}
CONTEXT_FIELDS = {
    "question_raw_vi",
    "entity",
    "metric",
    "period",
    "scope",
    "unit",
    "source_document",
    "source_table",
    "table_header",
    "row_label",
    "column_label",
    "operand_set",
    "operation_ast",
    "source_hash",
    "retrieval_candidate",
    "rerank_score",
    "formula_definition",
    "evidence_binding",
}
ACTIONS = {
    "ADD_VI_QUERY_ALIAS",
    "ADD_EN_CANONICAL_ALIAS",
    "INCREASE_RETRIEVAL_K",
    "ADD_RERANK_HARD_NEGATIVE",
    "HYDRATE_CONTEXT_FIELD",
    "UPDATE_PROMPT_PROFILE",
    "ADD_AST_TRAINING_EXAMPLE",
    "REPAIR_SOURCE_BINDING",
    "REQUIRE_HUMAN_REVIEW",
    "NO_ACTION",
}
FORBIDDEN_KEYS = {
    "answer",
    "answer_decimal",
    "numeric_answer",
    "final_answer",
    "raw_value",
    "source_value",
    "parsed_value",
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


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def find_input(filename: str) -> Path:
    root = Path(os.environ.get("FEEDBACK_INPUT_DIR", "/kaggle/input"))
    matches = sorted(root.rglob(filename)) if root.exists() else []
    if not matches:
        raise FileNotFoundError(f"cannot find {filename} below {root}")
    return matches[0]


def validate_input_manifest(path: Path) -> dict[str, Any]:
    """Validate the hash-bound package before loading any prompt or packet.

    The worker must not combine a packet from one Kaggle dataset with prompts
    or a parser from another dataset merely because ``/kaggle/input`` contains
    multiple versions.  All declared files are therefore resolved relative
    to the manifest that selected the input package and checked byte-for-byte.
    """

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input_manifest.json must contain an object")
    if value.get("protocol") != "vifinqa_blocked_feedback_kaggle_input_manifest_v1":
        raise ValueError("unsupported blocked-feedback input manifest protocol")
    files = value.get("files")
    if not isinstance(files, Mapping) or not files:
        raise ValueError("input manifest has no declared files")
    package_root = path.parent.resolve()
    for filename, descriptor in files.items():
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("input manifest contains an unsafe file name")
        if not isinstance(descriptor, Mapping):
            raise ValueError(f"input manifest descriptor is invalid: {filename}")
        file_path = package_root / filename
        if not file_path.is_file():
            raise FileNotFoundError(file_path)
        expected_hash = descriptor.get("sha256")
        expected_bytes = descriptor.get("bytes")
        if not isinstance(expected_hash, str) or sha256_file(file_path) != expected_hash:
            raise ValueError(f"input manifest hash mismatch: {filename}")
        if not isinstance(expected_bytes, int) or file_path.stat().st_size != expected_bytes:
            raise ValueError(f"input manifest size mismatch: {filename}")
    required = {
        "blocked_question_context_packets_v1.jsonl",
        "blocked_question_prompts_v1.jsonl",
        "submission_feedback_prompt_contract_v1.json",
        "feedback_json_parser.py",
    }
    if not required.issubset(files):
        raise ValueError(
            "input manifest is incomplete; repack the feedback worker input with the strict parser"
        )
    return value


def validate_prompt_rows(
    packets: list[dict[str, Any]],
    prompts: list[dict[str, Any]],
) -> None:
    """Check prompt/packet identity and prompt hashes before inference."""

    packets_by_id = {int(row["question_id"]): row for row in packets}
    for row in prompts:
        question_id = int(row["question_id"])
        packet = packets_by_id.get(question_id)
        if packet is None or row.get("packet_id") != packet.get("packet_id"):
            raise ValueError(f"prompt is not bound to packet for question {question_id}")
        prompt = row.get("prompt")
        expected_hash = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
        if row.get("prompt_sha256") != expected_hash:
            raise ValueError(f"prompt hash mismatch for question {question_id}")


def validate_checkpoint_records(
    completed: Mapping[int, Mapping[str, Any]],
    *,
    packets_by_id: Mapping[int, Mapping[str, Any]],
    prompt_rows: Mapping[int, Mapping[str, Any]],
    model_id: str,
    model_revision: str,
) -> None:
    """Reject stale or cross-input checkpoints instead of silently reusing them."""

    for question_id, record in completed.items():
        if question_id not in packets_by_id or question_id not in prompt_rows:
            raise ValueError(f"checkpoint contains an unknown question id: {question_id}")
        if record.get("protocol") != "vifinqa_blocked_question_feedback_v1":
            raise ValueError("checkpoint contains an unsupported feedback protocol")
        prompt = str(prompt_rows[question_id].get("prompt") or "")
        if record.get("prompt_sha256") != hashlib.sha256(prompt.encode("utf-8")).hexdigest():
            raise ValueError(f"checkpoint prompt hash mismatch for question {question_id}")
        if record.get("model_id") != model_id or record.get("model_revision") != model_revision:
            raise ValueError(
                "checkpoint model identity differs; use a new output directory or the same model revision"
            )


def walk_forbidden(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold()
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_answer"):
                raise ValueError(f"forbidden feedback key at {path}.{key}")
            walk_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_forbidden(child, f"{path}[{index}]")


def _text(value: object, label: str, maximum: int = 800) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{label} is too long")
    return value


def _list(value: object, label: str, allowed: set[str] | None = None, maximum: int = 8) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list of at most {maximum} items")
    output: list[str] = []
    for item in value:
        item_text = _text(item, label, 500)
        if allowed is not None and item_text not in allowed:
            raise ValueError(f"unsupported {label}: {item_text}")
        output.append(item_text)
    return output


def validate_feedback(response: object, packet: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        raise ValueError("feedback is not an object")
    walk_forbidden(response)
    required = {
        "decision",
        "failure_class",
        "reason_codes",
        "observations",
        "missing_context_fields",
        "recommended_actions",
        "confidence",
        "source_ref_sha256",
    }
    if set(response) != required:
        raise ValueError("feedback keys do not match contract")
    decision = _text(response.get("decision"), "decision", 40)
    failure_class = _text(response.get("failure_class"), "failure_class", 80)
    if decision not in FEEDBACK_DECISIONS or failure_class not in FAILURE_CLASSES:
        raise ValueError("unsupported decision or failure class")
    reasons = _list(response.get("reason_codes"), "reason_codes", REASON_CODES, 5)
    if not reasons:
        raise ValueError("reason_codes cannot be empty")
    observations = _list(response.get("observations"), "observations", None, 6)
    missing = _list(response.get("missing_context_fields"), "missing_context_fields", CONTEXT_FIELDS, 8)
    actions = response.get("recommended_actions")
    if not isinstance(actions, list) or len(actions) > 4:
        raise ValueError("recommended_actions is invalid")
    normalized_actions: list[dict[str, str]] = []
    for action in actions:
        if not isinstance(action, Mapping) or set(action) != {"action", "target", "rationale"}:
            raise ValueError("invalid action object")
        action_name = _text(action.get("action"), "action", 80)
        if action_name not in ACTIONS:
            raise ValueError(f"unsupported action: {action_name}")
        normalized_actions.append(
            {
                "action": action_name,
                "target": _text(action.get("target"), "target", 160),
                "rationale": _text(action.get("rationale"), "rationale", 500),
            }
        )
    confidence = _text(response.get("confidence"), "confidence", 20)
    if confidence not in CONFIDENCES:
        raise ValueError("unsupported confidence")
    refs = _list(response.get("source_ref_sha256"), "source_ref_sha256", None, 12)
    allowed_refs = {str(value) for value in packet.get("allowed_source_refs") or []}
    if any(len(value) != 64 or value not in allowed_refs for value in refs):
        raise ValueError("feedback cites an unavailable source reference")
    if decision == "ABSTAIN" and failure_class != "ABSTAIN":
        raise ValueError("ABSTAIN must use failure_class=ABSTAIN")
    if decision == "FEEDBACK_ONLY" and failure_class == "ABSTAIN":
        raise ValueError("FEEDBACK_ONLY cannot use failure_class=ABSTAIN")
    return {
        "decision": decision,
        "failure_class": failure_class,
        "reason_codes": reasons,
        "observations": observations,
        "missing_context_fields": missing,
        "recommended_actions": normalized_actions,
        "confidence": confidence,
        "source_ref_sha256": refs,
    }


def fallback(reason_code: str, observation: str) -> dict[str, Any]:
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
                "rationale": "The real model did not provide contract-valid feedback; retain the blocked item.",
            }
        ],
        "confidence": "LOW",
        "source_ref_sha256": [],
    }


def extract_json(text: str) -> Mapping[str, Any]:
    """Parse exactly one model object; never select the first of many."""

    return parse_one_json_object(text)


def safe_model_preview(text: str, maximum: int = 320) -> str:
    """Return only a redacted diagnostic preview; never persist raw output."""

    return re.sub(r"\d+(?:[.,]\d+)*", "<number>", text[:maximum]).replace("\n", " ").strip()


def feedback_record(
    *, packet: Mapping[str, Any], prompt: str, model_id: str, status: str,
    feedback: Mapping[str, Any], error_code: str | None = None,
    model_revision: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "protocol": "vifinqa_blocked_question_feedback_v1",
        "question_id": int(packet["question_id"]),
        "packet_id": packet["packet_id"],
        "prompt_profile": packet.get("prompt_profile"),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "model_id": model_id,
        "model_revision": model_revision,
        "model_status": status,
        "feedback": dict(feedback),
        "error_code": error_code,
        "authority_boundary": "feedback_only_non_authorizing",
        "answer_authorized": False,
        "evidence_authorized": False,
        "training_eligible": False,
        "promotion_allowed": False,
        "submission_eligible": False,
        "release_authorized": False,
    }
    return {**payload, "feedback_id": canonical_sha256(payload)}


def improvement_record(record: Mapping[str, Any]) -> dict[str, Any]:
    feedback = record.get("feedback") if isinstance(record.get("feedback"), Mapping) else {}
    payload = {
        "schema_version": 1,
        "protocol": "vifinqa_improvement_feedback_record_v1",
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
        "answer_authorized": False,
        "evidence_authorized": False,
        "training_eligible": False,
        "promotion_allowed": False,
        "submission_eligible": False,
        "release_authorized": False,
    }
    return {**payload, "improvement_record_id": canonical_sha256(payload)}


def improvement_plan(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        feedback = record.get("feedback")
        if not isinstance(feedback, Mapping):
            continue
        for action in feedback.get("recommended_actions") or []:
            if not isinstance(action, Mapping):
                continue
            key = (str(action.get("action")), str(action.get("target")))
            group = grouped.setdefault(key, {"action": key[0], "target": key[1], "rationales": set(), "questions": set(), "failure_classes": set()})
            group["rationales"].add(str(action.get("rationale") or ""))
            group["questions"].add(int(record["question_id"]))
            group["failure_classes"].add(str(feedback.get("failure_class") or "ABSTAIN"))
    experiments = [
        {
            "action": group["action"],
            "target": group["target"],
            "rationales": sorted(value for value in group["rationales"] if value),
            "question_ids": sorted(group["questions"]),
            "failure_classes": sorted(group["failure_classes"]),
            "status": "PROPOSED_NOT_RUN",
            "requires_human_verification": True,
            "training_eligible": False,
            "promotion_allowed": False,
        }
        for group in sorted(grouped.values(), key=lambda item: (item["action"], item["target"]))
    ]
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


def load_model(model_id: str, revision: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    use_cuda = torch.cuda.is_available()
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=True)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    kwargs: dict[str, Any] = {"revision": revision, "trust_remote_code": True, "low_cpu_mem_usage": True}
    load_mode = "cpu_fp32"
    if use_cuda:
        # Prefer 4-bit NF4 on a Kaggle T4. The first real-model run loaded
        # the checkpoint but was cancelled during the first generation pass;
        # keeping a full fp16 copy leaves too little headroom for KV cache.
        # Fall back to fp16 if the runtime does not expose bitsandbytes.
        try:
            from transformers import BitsAndBytesConfig

            kwargs.update(
                {
                    "quantization_config": BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=torch.float16,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True,
                    ),
                    "device_map": "auto",
                }
            )
            load_mode = "cuda_4bit_nf4"
        except Exception as error:
            print(
                json.dumps(
                    {"warning": "4-bit setup unavailable; falling back to fp16", "error": type(error).__name__}
                ),
                flush=True,
            )
            kwargs.update({"torch_dtype": torch.float16, "device_map": "auto"})
            load_mode = "cuda_fp16"
    else:
        kwargs.update({"torch_dtype": torch.float32})
    try:
        model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
    except Exception as error:
        if load_mode != "cuda_4bit_nf4":
            raise
        # A broken bitsandbytes build should not make the real-model lane
        # silently become a mock. Retry with an explicit fp16 model instead.
        print(
            json.dumps(
                {
                    "warning": "4-bit load failed; retrying real model in fp16",
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:240],
                }
            ),
            flush=True,
        )
        kwargs.pop("quantization_config", None)
        kwargs.update({"torch_dtype": torch.float16, "device_map": "auto"})
        load_mode = "cuda_fp16"
        model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
    
    model.eval()
    return tokenizer, model, use_cuda, load_mode


def main() -> None:
    input_manifest_path = find_input("input_manifest.json")
    input_manifest = validate_input_manifest(input_manifest_path)
    input_root = input_manifest_path.parent
    packets_path = input_root / "blocked_question_context_packets_v1.jsonl"
    prompts_path = input_root / "blocked_question_prompts_v1.jsonl"
    packets = load_jsonl(packets_path)
    prompts = load_jsonl(prompts_path)
    if len(packets) != len(prompts):
        raise ValueError("packet and prompt counts differ")
    if input_manifest.get("question_count") != len(packets):
        raise ValueError("input manifest question count differs from packet count")
    validate_prompt_rows(packets, prompts)
    packets_by_id = {int(row["question_id"]): row for row in packets}
    prompt_rows = {int(row["question_id"]): row for row in prompts}
    if len(packets_by_id) != len(packets) or len(prompt_rows) != len(prompts):
        raise ValueError("duplicate question id in input")
    model_id = os.environ.get("FEEDBACK_MODEL_ID", MODEL_ID)
    revision = os.environ.get("FEEDBACK_MODEL_REVISION", MODEL_REVISION)
    batch_size = max(1, int(os.environ.get("FEEDBACK_BATCH_SIZE", DEFAULT_BATCH_SIZE)))
    max_input_tokens = max(512, int(os.environ.get("FEEDBACK_MAX_INPUT_TOKENS", DEFAULT_MAX_INPUT_TOKENS)))
    max_new_tokens = max(32, int(os.environ.get("FEEDBACK_MAX_NEW_TOKENS", DEFAULT_MAX_NEW_TOKENS)))
    output_dir = Path(os.environ.get("FEEDBACK_OUTPUT_DIR", "/kaggle/working/vifinqa_blocked_feedback_qwen_v1"))
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "feedback_checkpoint_v1.jsonl"
    completed: dict[int, dict[str, Any]] = {}
    if checkpoint_path.is_file():
        for row in load_jsonl(checkpoint_path):
            completed[int(row["question_id"])] = row
    validate_checkpoint_records(
        completed,
        packets_by_id=packets_by_id,
        prompt_rows=prompt_rows,
        model_id=model_id,
        model_revision=revision,
    )

    # This is the only model construction path: a real Hugging Face checkpoint
    # is downloaded/loaded in the Kaggle worker.  There is no mock invoker.
    tokenizer, model, use_cuda, load_mode = load_model(model_id, revision)
    print(
        json.dumps(
            {
                "event": "real_model_loaded",
                "model_id": model_id,
                "revision": revision,
                "device": "cuda" if use_cuda else "cpu",
                "load_mode": load_mode,
                "batch_size": batch_size,
                "max_input_tokens": max_input_tokens,
                "max_new_tokens": max_new_tokens,
                "total_packets": len(packets),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    pending_ids = [question_id for question_id in sorted(packets_by_id) if question_id not in completed]
    start_time = time.time()
    runtime_error_count = 0
    invalid_count = 0
    with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
        for offset in range(0, len(pending_ids), batch_size):
            batch_ids = pending_ids[offset : offset + batch_size]
            batch_prompts = [str(prompt_rows[question_id]["prompt"]) for question_id in batch_ids]
            batch_errors: list[Exception] = []
            generated_texts: list[str] = []
            try:
                import torch

                chat_messages = [[{"role": "user", "content": prompt}] for prompt in batch_prompts]
                chat_kwargs: dict[str, Any] = {
                    "add_generation_prompt": True,
                    "tokenize": True,
                    "return_dict": True,
                    "return_tensors": "pt",
                    "padding": True,
                    "truncation": True,
                    "max_length": max_input_tokens,
                }
                # Qwen3 exposes a thinking switch; Qwen2.5 simply ignores
                # this branch. Keeping it conditional makes the worker
                # reusable for a later non-thinking feedback critic.
                if str(model_id).startswith("Qwen/Qwen3-"):
                    chat_kwargs["enable_thinking"] = False
                encoded = tokenizer.apply_chat_template(
                    chat_messages,
                    **chat_kwargs,
                )
                if use_cuda:
                    encoded = {key: value.to(model.device) for key, value in encoded.items()}
                with torch.inference_mode():
                    generated = model.generate(
                        **encoded,
                        max_new_tokens=max_new_tokens,
                        do_sample=False,
                        num_beams=1,
                        pad_token_id=tokenizer.pad_token_id,
                        eos_token_id=tokenizer.eos_token_id,
                    )
                input_width = encoded["input_ids"].shape[1]
                generated_texts = [
                    tokenizer.decode(row[input_width:], skip_special_tokens=True)
                    for row in generated
                ]
            except Exception as error:  # explicit runtime fallback per batch
                if use_cuda:
                    try:
                        import torch

                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                batch_errors.append(error)
                runtime_error_count += len(batch_ids)
                generated_texts = [""] * len(batch_ids)
            errors_for_rows: list[Exception | None] = (
                [batch_errors[0]] * len(batch_ids) if batch_errors else [None] * len(batch_ids)
            )
            for question_id, prompt, generated_text, batch_error in zip(batch_ids, batch_prompts, generated_texts, errors_for_rows, strict=True):
                packet = packets_by_id[question_id]
                status = "VALID_FEEDBACK"
                error_code = None
                if batch_error is not None:
                    feedback = fallback("MODEL_RUNTIME_ERROR", "The real model worker failed for this batch; no authority was granted.")
                    status = "MODEL_RUNTIME_ERROR"
                    error_code = type(batch_error).__name__
                else:
                    parsed_response: Mapping[str, Any] | None = None
                    try:
                        parsed_response = extract_json(generated_text)
                        feedback = validate_feedback(parsed_response, packet)
                        if feedback["decision"] == "ABSTAIN":
                            status = "MODEL_ABSTAIN"
                    except Exception as error:
                        feedback = fallback("MODEL_OUTPUT_INVALID", "The real model output was not contract-valid; retain the blocked item.")
                        status = "MODEL_OUTPUT_INVALID"
                        error_code = f"CONTRACT_VALIDATION_FAILED:{type(error).__name__}"
                        print(
                            json.dumps(
                                {
                                    "event": "model_output_invalid",
                                    "question_id": question_id,
                                    "error_type": type(error).__name__,
                                    "error_message": str(error)[:160],
                                    "observed_keys": sorted(parsed_response) if parsed_response is not None else [],
                                    "raw_chars": len(generated_text),
                                    "preview_redacted": safe_model_preview(generated_text),
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        invalid_count += 1
                record = feedback_record(
                    packet=packet,
                    prompt=prompt,
                    model_id=model_id,
                    model_revision=revision,
                    status=status,
                    feedback=feedback,
                    error_code=error_code,
                )
                completed[question_id] = record
                checkpoint.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                checkpoint.flush()
            processed = min(offset + len(batch_ids), len(pending_ids))
            if processed == len(pending_ids) or processed % max(batch_size * 4, 16) == 0:
                elapsed = max(time.time() - start_time, 0.001)
                print(
                    json.dumps(
                        {
                            "heartbeat": True,
                            "processed": len(completed),
                            "total": len(packets),
                            "pending_this_run": len(pending_ids),
                            "elapsed_seconds": round(elapsed, 2),
                            "rows_per_minute": round((processed / elapsed) * 60, 2),
                            "runtime_error_count": runtime_error_count,
                            "invalid_count": invalid_count,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    records = [completed[question_id] for question_id in sorted(completed)]
    if len(records) != len(packets):
        raise RuntimeError(f"model worker completed {len(records)} of {len(packets)} rows")
    feedback_path = output_dir / "blocked_question_feedback_v1.jsonl"
    improvement_path = output_dir / "improvement_feedback_records_v1.jsonl"
    plan_path = output_dir / "improvement_plan_v1.json"
    summary_path = output_dir / "feedback_summary.json"
    write_jsonl(feedback_path, records)
    improvements = [improvement_record(row) for row in records]
    write_jsonl(improvement_path, improvements)
    write_json(plan_path, improvement_plan(records))
    status_counts = Counter(str(row["model_status"]) for row in records)
    failure_counts = Counter(str((row.get("feedback") or {}).get("failure_class")) for row in records)
    action_counts = Counter(
        str(action.get("action"))
        for row in records
        for action in (row.get("feedback") or {}).get("recommended_actions") or []
        if isinstance(action, Mapping)
    )
    summary = {
        "schema_version": 1,
        "protocol": "vifinqa_blocked_feedback_run_manifest_v1",
        "status": "COMPLETED_MODEL_FEEDBACK_NON_AUTHORIZING",
        "model_id": model_id,
        "model_revision": revision,
        "device": "cuda" if use_cuda else "cpu",
        "blocked_count": len(packets),
        "feedback_record_count": len(records),
        "improvement_record_count": len(improvements),
        "model_evaluated_count": sum(row["model_status"] in {"VALID_FEEDBACK", "MODEL_ABSTAIN"} for row in records),
        "valid_feedback_count": sum(row["model_status"] == "VALID_FEEDBACK" for row in records),
        "model_status_counts": dict(sorted(status_counts.items())),
        "failure_class_counts": dict(sorted(failure_counts.items())),
        "recommended_action_counts": dict(sorted(action_counts.items())),
        "runtime_error_count": runtime_error_count,
        "model_output_invalid_count": invalid_count,
        "authority_boundary": "feedback_only_non_authorizing",
        "answer_authorized": False,
        "evidence_authorized": False,
        "training_eligible": False,
        "promotion_allowed": False,
        "submission_eligible": False,
        "release_authorized": False,
    }
    write_json(summary_path, summary)
    output_files = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in (feedback_path, improvement_path, plan_path, summary_path)
    }
    manifest = {
        "schema_version": 1,
        "protocol": "vifinqa_kaggle_blocked_feedback_run_manifest_v1",
        "input_manifest": input_manifest,
        "model": {
            "id": model_id,
            "revision": revision,
            "device": "cuda" if use_cuda else "cpu",
            "load_mode": load_mode,
            "batch_size": batch_size,
            "max_input_tokens": max_input_tokens,
            "max_new_tokens": max_new_tokens,
        },
        "counts": summary,
        "outputs": output_files,
        "errors": [],
        "authority": {
            "real_model_invoked": True,
            "numeric_cells_in_input": False,
            "model_output_may_authorize": False,
            "model_output_may_train_without_human": False,
        },
    }
    manifest_path = output_dir / "model_run_manifest.json"
    write_json(manifest_path, manifest)
    archive_path = output_dir / "blocked_feedback_kaggle_output.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output_dir.iterdir()):
            if path.is_file() and path.name != archive_path.name:
                archive.write(path, path.name)
    print(json.dumps({"output_dir": str(output_dir), "manifest": str(manifest_path), **summary}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
