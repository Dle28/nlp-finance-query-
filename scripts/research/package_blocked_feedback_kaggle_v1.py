#!/usr/bin/env python3
"""Package immutable ContextPackets and exact prompts for a Kaggle critic job.

The package contains no numeric source cells.  It is an input dataset for a
real Hugging Face model worker; the worker's output remains non-authorizing
feedback and must not be used as a gold label without review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from finance_query.pipeline.context.compiler import compile_feedback_prompt
from finance_query.pipeline.context.prompt_profiles import DEFAULT_PROMPT_PROFILE


MODEL_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
MODEL_REVISION = "2e1fd397ee46e1388853d2af2c993145b0f1098a"
PROMPT_PROFILE = DEFAULT_PROMPT_PROFILE
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-id", default=MODEL_ID)
    parser.add_argument("--model-revision", default=MODEL_REVISION)
    parser.add_argument("--prompt-profile", default=PROMPT_PROFILE)
    parser.add_argument(
        "--dataset-id",
        default="dungle2810/vifinqa-blocked-feedback-input-v1-20260829",
    )
    parser.add_argument(
        "--dataset-title",
        default="ViFinQA Blocked Feedback Input V1 20260829",
    )
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {output_dir}")
    packet_path = source_dir / "blocked_question_context_packets_v1.jsonl"
    if not packet_path.is_file():
        raise FileNotFoundError(packet_path)
    packets = load_jsonl(packet_path)
    if not packets:
        raise ValueError("packet input is empty")

    output_dir.mkdir(parents=True)
    shutil.copy2(packet_path, output_dir / packet_path.name)
    contract_path = REPOSITORY_ROOT / "configs/pipeline/submission_feedback_v1.json"
    shutil.copy2(contract_path, output_dir / "submission_feedback_prompt_contract_v1.json")
    parser_path = Path(__file__).resolve().parents[2] / "src/finance_query/research/llm/json_output.py"
    if not parser_path.is_file():
        raise FileNotFoundError(parser_path)
    shutil.copy2(parser_path, output_dir / "feedback_json_parser.py")
    prompt_rows: list[dict[str, object]] = []
    for packet in packets:
        prompt = compile_feedback_prompt(packet, profile_name=args.prompt_profile)
        prompt_rows.append(
            {
                "schema_version": 1,
                "protocol": "vifinqa_blocked_question_prompt_v1",
                "question_id": packet.get("question_id"),
                "packet_id": packet.get("packet_id"),
                "prompt_profile": args.prompt_profile,
                "prompt": prompt,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
        )
    prompts_path = output_dir / "blocked_question_prompts_v1.jsonl"
    prompts_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in prompt_rows
        ),
        encoding="utf-8",
    )
    input_manifest = {
        "schema_version": 1,
        "protocol": "vifinqa_blocked_feedback_kaggle_input_manifest_v1",
        "source_dir": str(source_dir),
        "question_count": len(packets),
        "prompt_profile": args.prompt_profile,
        "model": {"id": args.model_id, "revision": args.model_revision},
        "authority": {
            "numeric_cells_in_packet": False,
            "model_output_may_authorize": False,
            "model_output_may_train_without_human": False,
        },
        "files": {
            name: {"sha256": sha256_file(output_dir / name), "bytes": (output_dir / name).stat().st_size}
            for name in (
                packet_path.name,
                prompts_path.name,
                "submission_feedback_prompt_contract_v1.json",
                "feedback_json_parser.py",
            )
        },
    }
    write_json(output_dir / "input_manifest.json", input_manifest)
    write_json(
        output_dir / "dataset-metadata.json",
        {
            "title": args.dataset_title,
            "id": args.dataset_id,
            "licenses": [{"name": "other"}],
            "description": "Hash-bound numeric-free Vietnamese ContextPackets and prompts for a non-authorizing real-model feedback run.",
        },
    )
    print(json.dumps({"output_dir": str(output_dir), **input_manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
