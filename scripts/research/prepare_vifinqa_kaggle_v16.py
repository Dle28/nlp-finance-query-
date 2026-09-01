#!/usr/bin/env python3
"""Prepare a reproducible Kaggle full-run notebook and submission runtime.

This script only stages artifacts.  It does not claim that Kaggle was run or
that a fine-tuned checkpoint is promoted; those facts must come from the
Kaggle output and the promotion gates in the notebook.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"pattern not found for {label}")
    return text.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-notebook", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()

    source_nb = args.source_notebook
    out_dir = args.output_dir
    runtime_dir = args.runtime_dir
    if out_dir.exists():
        raise SystemExit(f"refusing to overwrite: {out_dir}")
    out_dir.mkdir(parents=True)
    staged_runtime = out_dir / "vifinqa_submission_runtime_v2"
    shutil.copytree(runtime_dir, staged_runtime)
    source_line_map_path = staged_runtime / "source_line_map.json"
    if not source_line_map_path.is_file():
        raise FileNotFoundError(
            "runtime is not submission-ready: missing source_line_map.json "
            f"under {staged_runtime}"
        )
    shutil.copy2(ROOT / "scripts/e2e/build_competition_submission_v1.py", staged_runtime / "build_competition_submission_v1.py")

    notebook = json.loads(source_nb.read_text(encoding="utf-8"))
    cells = notebook["cells"]
    for cell in cells:
        source = "".join(cell.get("source", []))
        if "FAST_DEV_RUN = True" in source:
            source = source.replace("FAST_DEV_RUN = True", "FAST_DEV_RUN = False", 1)
            source = source.replace(
                "Notebook mặc định chạy nhanh để kiểm tra (`FAST_DEV_RUN=True`). Chuyển sang\n`False` sau khi toàn bộ cell chạy qua một lần.",
                "Notebook này chạy full curriculum (`FAST_DEV_RUN=False`) trên GPU Kaggle; chỉ dùng smoke notebook để chẩn đoán.",
            )
        if "--model-candidate-limit', '12'" in source:
            source = source.replace("'--model-candidate-limit', '12'", "'--model-candidate-limit', '50'", 1)
            source = source.replace(
                "cmd = [sys.executable, str(runtime_root / 'build_competition_submission_v1.py'), '--questions', str(runtime_root / 'questions.jsonl'), '--bundle', str(review_root), '--replay', str(runtime_root / 'replay.jsonl'), '--output', str(output_root), '--source-line-map', str(runtime_root / 'source_line_map.json'), '--finetuned-model-root', str(WORK_ROOT), '--model-device', 'cuda', '--model-candidate-limit', '50', '--model-batch-size', '64']",
                "cmd = [sys.executable, str(runtime_root / 'build_competition_submission_v1.py'), '--questions', str(runtime_root / 'questions.jsonl'), '--bundle', str(review_root), '--replay', str(runtime_root / 'replay.jsonl'), '--output', str(output_root), '--source-line-map', str(runtime_root / 'source_line_map.json'), '--finetuned-model-root', str(WORK_ROOT), '--model-device', 'cuda', '--model-candidate-limit', '50', '--model-batch-size', '64']\n\ndense_files = sorted(INPUT_ROOT.rglob('dense_embeddings_v1.npy'))\nif dense_files:\n    dense_dir = dense_files[0].parent\n    cmd += ['--dense-index-dir', str(dense_dir), '--dense-candidate-limit', '50', '--dense-device', 'cuda', '--dense-batch-size', '128']\n    print({'dense_index': str(dense_dir), 'dense_navigation_only': True})\nelse:\n    print({'dense_index': None, 'dense_navigation_only': False, 'note': 'No dense index attached; review shortlist remains the navigation source.'})",
                1,
            )
            source = source.replace(
                "'--source-line-map', str(runtime_root / 'source_line_map.json'), '--finetuned-model-root'",
                "'--source-line-map', str(runtime_root / 'source_line_map.json'), '--require-source-line-map', '--finetuned-model-root'",
                1,
            )
            source = source.replace("submission_vifinqa_finetuned_v15", "submission_vifinqa_finetuned_v16", 1)
        if "'--finetuned-model-root', str(WORK_ROOT)" in source and "'--reranker-policy'" not in source:
            source = source.replace(
                "'--finetuned-model-root', str(WORK_ROOT)",
                "'--reranker-policy', 'use_finetuned_reranker', '--finetuned-model-root', str(WORK_ROOT)",
                1,
            )
        cell["source"] = source.splitlines(keepends=True)

    notebook["metadata"]["vifinqa_run_protocol"] = {
        "version": "v16",
        "fast_dev_run": False,
        "candidate_limit": 50,
        "dense_expansion": "auto-if-attached",
        "coordinate_contract": {
            "source_line_map": "required",
            "fallback": "forbidden",
            "map_filename": "source_line_map.json",
        },
        "numeric_authority": "v2_table_replay_for_cells_and_staged_model_results",
        "primary_model": "integrated_submission_pipeline_v1",
    }
    notebook_path = out_dir / "vifinqa-rag-finetune-gpu-v1-20260828-v16.ipynb"
    notebook_path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    metadata = {
        "id": "dungle2810/vifinqa-rag-finetune-gpu-v1-20260828-v16",
        "title": "ViFinQA RAG Finetune GPU V1 20260828 V16 Full",
        "code_file": notebook_path.name,
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": True,
        "enable_tpu": False,
        "enable_internet": True,
        "dataset_sources": [
            "dungle2810/vifinqa-rag-finetune-v1-20260828",
            "dungle2810/vifinqa-qwen-review-bundle-20260811",
            "dungle2810/vifinqa-submission-runtime-v1-20260828",
        ],
        "kernel_sources": [],
        "competition_sources": [],
        "model_sources": [],
        "docker_image": "gcr.io/kaggle-private-byod/python@sha256:37c64f7dd9c54116ecd1bcc88817c5469b88387388fade02bfa8bf3fc647d461",
        "machine_shape": "NvidiaTeslaT4",
    }
    (out_dir / "kernel-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"notebook": str(notebook_path), "runtime": str(staged_runtime), "metadata": str(out_dir / "kernel-metadata.json")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
