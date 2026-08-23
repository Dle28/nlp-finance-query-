#!/usr/bin/env python3
"""Run one proposal-only CCL component-selection smoke job on a CUDA GPU.

The script writes raw model text only.  It never marks a selection as valid,
never generates a semantic table label, and never substitutes an abstention on
model failure; the separate closed-world validator owns those decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

from finance_query.certified_canonical.phase5_component_selection import render_phase5_component_selection_prompt
from finance_query.certified_canonical.phase5_component_selection_smoke import (
    PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
    PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION,
)
from finance_query.table_structure import sha256_file


_RAW_RESPONSES_NAME = "component_selection_raw_responses_v1.jsonl"
_REPORT_NAME = "component_selection_model_execution_report.json"
_MANIFEST_NAME = "component_selection_model_execution_manifest.json"
_PILOT_PROTOCOL = "vifinqa_ccl_phase5_component_selection_pilot_v1"
_JOB_CONTRACTS = {
    PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL: ("prepared_component_selection_smoke_not_executed", 5, 5),
    _PILOT_PROTOCOL: ("prepared_component_selection_pilot_not_executed", 1, 32),
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected JSON object: {path}:{line_number}")
            rows.append(value)
    return rows


def _load_job(job_manifest: Path, requests_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    job = _json(job_manifest)
    protocol = job.get("protocol")
    contract = _JOB_CONTRACTS.get(protocol)
    if (
        contract is None
        or int(job.get("schema_version") or 0) != PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION
        or job.get("run_status") != contract[0]
        or job.get("model_execution_allowed") is not True
        or job.get("training_eligible") is not False
        or job.get("certification_allowed") is not False
    ):
        raise ValueError("job manifest is not an immutable proposal-only component-selection job")
    expected = ((job.get("outputs") or {}).get(requests_path.name) or {}).get("sha256")
    if expected != sha256_file(requests_path):
        raise ValueError("requests do not match their component-selection smoke job manifest")
    route = job.get("route")
    if not isinstance(route, Mapping):
        raise ValueError("job manifest has no route")
    requests = _jsonl(requests_path)
    expected_packet_ids = route.get("packet_ids")
    if not isinstance(expected_packet_ids, list) or not contract[1] <= len(expected_packet_ids) <= contract[2] or len(requests) != len(expected_packet_ids):
        raise ValueError("component-selection job packet count violates its protocol contract")
    seen_requests: set[str] = set()
    observed_packet_ids: list[str] = []
    for request in requests:
        request_id = str(request.get("component_selection_request_id") or "")
        packet_id = str(request.get("component_selection_packet_id") or "")
        if (
            not request_id
            or request_id in seen_requests
            or not packet_id
            or request.get("protocol") != protocol
            or request.get("route") != route
            or request.get("training_eligible") is not False
            or request.get("certification_allowed") is not False
        ):
            raise ValueError("component-selection smoke request is malformed")
        packet = request.get("packet")
        if not isinstance(packet, Mapping) or packet.get("component_selection_packet_id") != packet_id:
            raise ValueError("component-selection smoke request packet identity is malformed")
        render_phase5_component_selection_prompt(packet)
        seen_requests.add(request_id)
        observed_packet_ids.append(packet_id)
    if observed_packet_ids != expected_packet_ids:
        raise ValueError("component-selection smoke request order does not match the declared exact packet IDs")
    return job, requests


def _chat_text(tokenizer: Any, prompt: str, *, model_id: str) -> str:
    messages = [
        {"role": "system", "content": "You obey closed-world JSON source-selection contracts exactly."},
        {"role": "user", "content": prompt},
    ]
    if model_id.casefold().startswith("qwen/qwen3"):
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-manifest", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--progress-every", type=int, default=1)
    args = parser.parse_args()
    if args.progress_every < 1:
        raise ValueError("progress-every must be positive")
    job_manifest = args.job_manifest.resolve()
    requests_path = args.requests.resolve()
    output_dir = args.output_dir.resolve()
    if not job_manifest.is_file() or not requests_path.is_file():
        raise FileNotFoundError("job-manifest and requests must exist")
    if output_dir.exists() or output_dir in {job_manifest, requests_path}:
        raise FileExistsError("output-dir must be new and distinct from immutable job inputs")
    job, requests = _load_job(job_manifest, requests_path)
    route = dict(job["route"])
    input_hashes = {"job_manifest": sha256_file(job_manifest), "requests": sha256_file(requests_path)}

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the component-selection smoke route")
    vram_gib = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    if vram_gib < 14:
        raise RuntimeError("component-selection smoke route requires at least 14 GiB GPU VRAM")
    tokenizer = AutoTokenizer.from_pretrained(route["model_id"], revision=route["revision"])
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
    model = AutoModelForCausalLM.from_pretrained(
        route["model_id"],
        revision=route["revision"],
        quantization_config=quantization,
        torch_dtype=torch.float16,
        device_map="auto",
    )
    model.eval()
    model_device = next(model.parameters()).device
    prompts = []
    for request in requests:
        prompt = _chat_text(tokenizer, render_phase5_component_selection_prompt(request["packet"]), model_id=str(route["model_id"]))
        tokenized = tokenizer(prompt, add_special_tokens=False).input_ids
        if len(tokenized) > route["max_input_tokens"]:
            raise RuntimeError("component-selection smoke request exceeds its declared input-token budget")
        prompts.append(prompt)
    output_dir.mkdir(parents=True)
    raw_path = output_dir / _RAW_RESPONSES_NAME
    started = time.perf_counter()
    with raw_path.open("x", encoding="utf-8") as file:
        for index, (request, prompt) in enumerate(zip(requests, prompts), start=1):
            encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(model_device)
            with torch.inference_mode():
                generated = model.generate(
                    **encoded,
                    do_sample=False,
                    max_new_tokens=route["max_new_tokens"],
                    pad_token_id=tokenizer.eos_token_id,
                )
            completion = tokenizer.decode(generated[0][encoded.input_ids.shape[1] :], skip_special_tokens=True)
            file.write(
                json.dumps(
                    {
                        "component_selection_packet_id": request["component_selection_packet_id"],
                        "response": completion,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            file.flush()
            if index % args.progress_every == 0 or index == len(requests):
                print(json.dumps({"event": "ccl_phase5_component_selection_progress", "completed_requests": index, "total_requests": len(requests), "elapsed_seconds": round(time.perf_counter() - started, 3)}, ensure_ascii=False), flush=True)
    elapsed = time.perf_counter() - started
    report = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION,
        "protocol": job["protocol"],
        "route": route,
        "run_status": "component_selection_model_execution_complete_responses_unvalidated",
        "response_count": len(requests),
        "generation_contract": {
            "do_sample": False,
            "max_input_tokens": route["max_input_tokens"],
            "max_new_tokens": route["max_new_tokens"],
            "qwen3_thinking_disabled": str(route["model_id"]).casefold().startswith("qwen/qwen3"),
        },
        "runtime": {"cuda_available": True, "gpu_name": torch.cuda.get_device_name(0), "vram_gib": round(vram_gib, 3), "torch_version": torch.__version__},
        "elapsed_seconds": round(elapsed, 3),
        "input_hashes_unchanged": input_hashes == {"job_manifest": sha256_file(job_manifest), "requests": sha256_file(requests_path)},
        "training_eligible_output_count": 0,
        "certification_allowed": False,
    }
    if report["input_hashes_unchanged"] is not True:
        raise RuntimeError("component-selection model execution changed a hash-bound input")
    report_path = output_dir / _REPORT_NAME
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": PHASE5_COMPONENT_SELECTION_SMOKE_SCHEMA_VERSION,
        "protocol": job["protocol"],
        "route": route,
        "run_status": "component_selection_model_execution_complete_responses_unvalidated",
        "inputs": {name: {"sha256": value} for name, value in input_hashes.items()},
        "outputs": {_RAW_RESPONSES_NAME: {"sha256": sha256_file(raw_path)}, _REPORT_NAME: {"sha256": sha256_file(report_path)}},
        "training_eligible": False,
        "certification_allowed": False,
    }
    (output_dir / _MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "response_count": len(requests), "training_eligible": False, "certification_allowed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
