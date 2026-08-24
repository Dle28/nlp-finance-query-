#!/usr/bin/env python3
"""Run one pinned <14.7B open-source active-learning route on CUDA/Kaggle."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.active_learning_models import (  # noqa: E402
    MODEL_JOB_PROTOCOL,
    MODEL_MAX_NEW_TOKENS,
    MODEL_MAX_SECONDS_PER_REQUEST,
    MODEL_PROGRESS_EVERY,
    RAW_RESPONSE_PROTOCOL,
    VALIDATED_RESPONSE_PROTOCOL,
    render_prompt,
)
from finance_query.evidence_closure import canonical_sha256, load_json, load_jsonl, sha256_file  # noqa: E402


def _output_path(manifest: Mapping[str, Any], name: str, *, base_dir: Path) -> Path:
    record = (manifest.get("outputs") or {}).get(name)
    if not isinstance(record, Mapping):
        raise ValueError(f"model job lacks output {name}")
    path = Path(str(record.get("path") or ""))
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file() or sha256_file(path) != record.get("sha256"):
        raise ValueError(f"model job output hash mismatch: {name}")
    return path


def _fallback(request: Mapping[str, Any], error: Exception) -> str:
    _ = error
    return json.dumps({
        "schema_version": 1,
        "protocol": VALIDATED_RESPONSE_PROTOCOL,
        "review_item_id": request["review_item_id"],
        "model_role": request["model_role"],
        "verdict": "ABSTAIN",
        "policy": None,
        "reason_codes": ["MODEL_RUNTIME_ERROR"],
        "cited_source_ref_sha256": [],
    }, ensure_ascii=False, sort_keys=True)


def _generation_contract(requests: list[dict[str, Any]]) -> tuple[int, float, int, bool]:
    contracts = [row.get("generation_contract") for row in requests]
    if any(not isinstance(contract, Mapping) for contract in contracts):
        raise ValueError("request lacks generation_contract")
    canonical = contracts[0]
    if any(contract != canonical for contract in contracts[1:]):
        raise ValueError("requests do not share one generation_contract")
    max_new_tokens = canonical.get("max_new_tokens")
    max_seconds = canonical.get("max_seconds_per_request")
    progress_every = canonical.get("progress_every")
    if (
        max_new_tokens == MODEL_MAX_NEW_TOKENS
        and max_seconds == MODEL_MAX_SECONDS_PER_REQUEST
        and progress_every == MODEL_PROGRESS_EVERY
    ):
        return int(max_new_tokens), float(max_seconds), int(progress_every), False
    legacy_contract = {
        "do_sample": False,
        "max_new_tokens": 512,
        "qwen3_thinking_enabled": canonical.get("qwen3_thinking_enabled"),
        "temperature": 0,
    }
    if canonical != legacy_contract or canonical.get("qwen3_thinking_enabled") not in {False, None}:
        raise ValueError("unsupported generation_contract")
    return MODEL_MAX_NEW_TOKENS, MODEL_MAX_SECONDS_PER_REQUEST, MODEL_PROGRESS_EVERY, True


def _write_progress(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--role", choices=["open_source_model_proposer", "open_source_model_critic"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    job = load_json(args.job_manifest.resolve())
    if job.get("protocol") != MODEL_JOB_PROTOCOL or job.get("status") != "PREPARED_GPU_EXECUTION_NOT_RUN":
        raise ValueError("invalid active-learning model job")
    output_name = "proposer_requests" if args.role == "open_source_model_proposer" else "critic_requests"
    requests_path = _output_path(job, output_name, base_dir=args.job_manifest.resolve().parent)
    requests = load_jsonl(requests_path)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        requests = requests[: args.limit]
    if not requests or any(row.get("model_role") != args.role for row in requests):
        raise ValueError("request role mismatch")
    max_new_tokens, max_seconds_per_request, progress_every, legacy_contract_hardened = _generation_contract(requests)
    request_generation_contract_sha256 = canonical_sha256(requests[0]["generation_contract"])
    route = (job.get("model_routes") or {}).get(args.role)
    if not isinstance(route, Mapping) or route.get("model_id") != requests[0].get("model_id"):
        raise ValueError("model route mismatch")
    try:
        import bitsandbytes
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as error:
        raise RuntimeError("GPU execution requires torch, transformers, accelerate, huggingface_hub and bitsandbytes") from error
    if not torch.cuda.is_available():
        raise RuntimeError("open-source active-learning model execution requires CUDA; use the Kaggle notebook")
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    shard_names = [str(item["filename"]) for item in route["weight_shards"]]
    snapshot = Path(snapshot_download(
        repo_id=str(route["model_id"]),
        revision=str(route["revision"]),
        allow_patterns=shard_names + ["*.json", "*.model", "*.jinja", "tokenizer*"],
    ))
    for shard in route["weight_shards"]:
        path = snapshot / str(shard["filename"])
        if not path.is_file() or sha256_file(path) != shard["sha256"]:
            raise ValueError(f"weight shard hash mismatch: {shard['filename']}")
    tokenizer = AutoTokenizer.from_pretrained(snapshot)
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16)
    model = AutoModelForCausalLM.from_pretrained(snapshot, device_map="auto", torch_dtype=torch.float16, quantization_config=quantization)
    model.eval()
    staging = Path(tempfile.mkdtemp(prefix=f".{args.output_dir.name}.tmp-", dir=args.output_dir.parent))
    try:
        raw_path = staging / "raw_model_responses_v1.jsonl"
        progress_path = staging / "model_execution_progress.json"
        runtime_errors = 0
        started_at = time.perf_counter()
        with raw_path.open("x", encoding="utf-8") as handle:
            for index, request in enumerate(requests, start=1):
                prompt = render_prompt(request)
                request_started_at = time.perf_counter()
                try:
                    kwargs: dict[str, Any] = {
                        "add_generation_prompt": True,
                        "tokenize": True,
                        "return_tensors": "pt",
                        "return_dict": True,
                    }
                    if str(route["model_id"]).startswith("Qwen/Qwen3-"):
                        kwargs["enable_thinking"] = False
                    model_inputs = {
                        key: value.to(model.device)
                        for key, value in tokenizer.apply_chat_template(
                            [{"role": "user", "content": prompt}], **kwargs
                        ).items()
                    }
                    input_ids = model_inputs["input_ids"]
                    if "attention_mask" not in model_inputs:
                        raise RuntimeError("tokenizer did not return an attention_mask")
                    with torch.inference_mode():
                        generated = model.generate(
                            **model_inputs,
                            do_sample=False,
                            max_new_tokens=max_new_tokens,
                            max_time=max_seconds_per_request,
                            pad_token_id=tokenizer.eos_token_id,
                        )
                    raw = tokenizer.decode(generated[0][input_ids.shape[-1] :], skip_special_tokens=True).strip()
                except Exception as error:  # retain a reason-coded abstention per packet
                    runtime_errors += 1
                    raw = _fallback(request, error)
                envelope = {
                    "schema_version": 1,
                    "protocol": RAW_RESPONSE_PROTOCOL,
                    "request_id": request["request_id"],
                    "review_item_id": request["review_item_id"],
                    "model_role": args.role,
                    "model_id": route["model_id"],
                    "model_revision": route["revision"],
                    "prompt_sha256": canonical_sha256(prompt),
                    "raw_response": raw,
                    "elapsed_seconds": round(time.perf_counter() - request_started_at, 3),
                }
                handle.write(json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                progress = {
                    "schema_version": 1,
                    "protocol": RAW_RESPONSE_PROTOCOL,
                    "status": "RAW_MODEL_EXECUTION_IN_PROGRESS",
                    "model_role": args.role,
                    "completed_requests": index,
                    "total_requests": len(requests),
                    "last_request_id": request["request_id"],
                    "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                    "max_new_tokens": max_new_tokens,
                    "max_seconds_per_request": max_seconds_per_request,
                    "legacy_generation_contract_hardened": legacy_contract_hardened,
                    "request_generation_contract_sha256": request_generation_contract_sha256,
                    "training_eligible": False,
                    "certification_allowed": False,
                    "submission_eligible": False,
                }
                _write_progress(progress_path, progress)
                if index == 1 or index == len(requests) or index % progress_every == 0:
                    print(json.dumps(progress, ensure_ascii=False, sort_keys=True), flush=True)
        runtime = {
            "schema_version": 1,
            "protocol": RAW_RESPONSE_PROTOCOL,
            "status": "RAW_MODEL_EXECUTION_COMPLETE_UNVALIDATED",
            "model_role": args.role,
            "model_id": route["model_id"],
            "model_revision": route["revision"],
            "parameter_count_billions": route["parameter_count_billions"],
            "weight_shards_verified": True,
            "request_count": len(requests),
            "runtime_error_abstention_count": runtime_errors,
            "max_new_tokens": max_new_tokens,
            "max_seconds_per_request": max_seconds_per_request,
            "legacy_generation_contract_hardened": legacy_contract_hardened,
            "request_generation_contract_sha256": request_generation_contract_sha256,
            "elapsed_seconds": round(time.perf_counter() - started_at, 3),
            "gpu": torch.cuda.get_device_name(0),
            "torch_version": torch.__version__,
            "bitsandbytes_version": bitsandbytes.__version__,
            "training_eligible": False,
            "certification_allowed": False,
            "submission_eligible": False,
        }
        runtime_path = staging / "model_runtime_receipt.json"
        runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            **runtime,
            "inputs": {"job_manifest": {"path": str(args.job_manifest.resolve()), "sha256": sha256_file(args.job_manifest)}, "requests": {"path": str(requests_path.resolve()), "sha256": sha256_file(requests_path)}},
            "outputs": {"raw_responses": {"path": str(args.output_dir / raw_path.name), "sha256": sha256_file(raw_path)}, "runtime_receipt": {"path": str(args.output_dir / runtime_path.name), "sha256": sha256_file(runtime_path)}, "progress": {"path": str(args.output_dir / progress_path.name), "sha256": sha256_file(progress_path)}},
        }
        manifest_path = staging / "raw_model_execution.manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staging.rename(args.output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(args.output_dir / "raw_model_execution.manifest.json")


if __name__ == "__main__":
    main()
