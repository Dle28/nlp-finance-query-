#!/usr/bin/env python3
"""Run Qwen2.5-14B 4-bit only on hash-bound grounded critic V2 packets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from finance_query.grounded_critic_protocol import validate_critic_response
from finance_query.grounded_critic_qwen_v2 import critic_prompt, fallback_abstention
from finance_query.qwen_inference import QwenGenerator


DEFAULT_MODEL = "Qwen/Qwen2.5-14B-Instruct"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packets-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-output", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-new-tokens", type=int, default=768)
    return parser.parse_args()


def preflight() -> tuple[Any, str]:
    try:
        import bitsandbytes  # noqa: F401
        import torch
    except ImportError as error:
        raise RuntimeError("Qwen critic requires torch and bitsandbytes in the Kaggle GPU runtime") from error
    if not torch.cuda.is_available():
        raise RuntimeError("Qwen critic requires a Kaggle CUDA GPU runtime")
    properties = torch.cuda.get_device_properties(0)
    if properties.total_memory < 14 * 1024**3:
        raise RuntimeError("Qwen2.5-14B 4-bit critic requires at least 14 GiB GPU memory")
    return torch, torch.cuda.get_device_name(0)


def main() -> None:
    args = parse_args()
    packet_manifest = json.loads(args.packets_manifest.read_text(encoding="utf-8"))
    expected = ((packet_manifest.get("outputs") or {}).get("packets") or {}).get("sha256")
    if not isinstance(expected, str) or sha256_file(args.packets) != expected:
        raise ValueError("SHA-256 mismatch for grounded critic packets")
    packets = [json.loads(line) for line in args.packets.read_text(encoding="utf-8").splitlines() if line]
    if not packets:
        raise ValueError("No execution-ready grounded critic packets; refusing Qwen model download")
    ids = [int(packet["question_id"]) for packet in packets]
    if len(ids) != len(set(ids)):
        raise ValueError("Grounded critic packet question IDs must be unique")
    torch, gpu_name = preflight()
    generator = QwenGenerator(args.model, args.max_new_tokens)
    results = []
    for packet in packets:
        question_id = int(packet["question_id"])
        try:
            response = generator(critic_prompt(packet))
            result = validate_critic_response(packet, response)
        except Exception as error:  # fail closed per packet; retain run diagnostics
            result = validate_critic_response(packet, fallback_abstention(question_id, str(error)))
        results.append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in results),
        encoding="utf-8",
    )
    revision = getattr(getattr(generator.model, "config", None), "_commit_hash", None) or "unknown"
    runtime = {
        "run_mode": "qwen14b_4bit",
        "runtime": f"torch={torch.__version__}",
        "gpu": gpu_name,
        "gpu_memory_gib": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2),
        "model": args.model,
        "model_revision": revision,
        "packets_sha256": sha256_file(args.packets),
        "results_sha256": sha256_file(args.output),
    }
    args.runtime_output.write_text(json.dumps(runtime, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(args.runtime_output)


if __name__ == "__main__":
    main()
