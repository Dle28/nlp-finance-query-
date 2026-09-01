#!/usr/bin/env python3
"""Stage the answer-optimized runtime and Kaggle kernel without overwriting an old run."""

from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE_RUNTIME = ROOT / "artifacts/kaggle_upload/dungle2810_vifinqa_primary_runtime_full_v1_20260829"
BASE_NOTEBOOK = ROOT / "artifacts/kaggle_upload/dungle2810_vifinqa_full_corpus_submission_v1_20260829.ipynb"
BASE_KERNEL_METADATA = ROOT / "artifacts/kaggle_upload/dungle2810_vifinqa_full_corpus_submission_v1_20260829_kernel/kernel-metadata.json"
DIRECT_REPLAY = ROOT / "artifacts/research/answer_optimization_v1/direct_evidence_replay_all_ready_v1.jsonl"
DIRECT_REPLAY_MANIFEST = ROOT / "artifacts/research/answer_optimization_v1/direct_evidence_replay_all_ready_v1.jsonl.manifest.json"

RUNTIME_SLUG = "dungle2810/vifinqa-primary-answer-optimized-v1-20260830"
KERNEL_SLUG = "dungle2810/vifinqa-answer-optimized-v1-20260830"


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if "__pycache__" in Path(info.name).parts or info.name.endswith((".pyc", ".pyo")):
        return None
    return info


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _patch_notebook(text: str, *, runtime_slug: str) -> str:
    text = text.replace(
        "vifinqa-primary-full-corpus-runtime-v1-20260829",
        runtime_slug.rsplit("/", 1)[-1],
    )
    text = text.replace(
        "vifinqa_full_corpus_submission_20260829",
        "vifinqa_answer_optimized_20260830",
    )
    payload = json.loads(text)
    for cell in payload.get("cells") or []:
        source = cell.get("source")
        if not isinstance(source, list) or not any(
            "--model-answer-candidate" in str(line) for line in source
        ):
            continue
        if any("--direct-evidence-replay" in str(line) for line in source):
            return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        for index, line in enumerate(source):
            if "--model-answer-candidate" not in str(line):
                continue
            source[index + 1:index + 1] = [
                "    '--direct-evidence-replay', str(RUNTIME_INPUT / 'direct_evidence_replay_all_ready_v1.jsonl'),\n",
                "    '--direct-replay-include-provisional',\n",
            ]
            return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    raise ValueError("could not locate model-answer argument in template notebook")


def stage(
    output_root: Path,
    *,
    runtime_slug: str = RUNTIME_SLUG,
    kernel_slug: str = KERNEL_SLUG,
    runtime_title: str = "ViFinQA Answer Optimized Runtime V1 20260830",
    kernel_title: str = "ViFinQA Answer Optimized Submission V1 20260830",
) -> dict[str, str]:
    output_root = output_root.expanduser().resolve()
    runtime_dir = output_root / "runtime"
    kernel_dir = output_root / "kernel"
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite staging directory: {output_root}")
    for path in (BASE_RUNTIME, BASE_NOTEBOOK, BASE_KERNEL_METADATA, DIRECT_REPLAY, DIRECT_REPLAY_MANIFEST):
        if not path.exists():
            raise FileNotFoundError(path)

    shutil.copytree(BASE_RUNTIME, runtime_dir)
    shutil.copy2(DIRECT_REPLAY, runtime_dir / DIRECT_REPLAY.name)
    shutil.copy2(DIRECT_REPLAY_MANIFEST, runtime_dir / DIRECT_REPLAY_MANIFEST.name)

    runtime_tar = runtime_dir / "runtime_code.tar"
    with tarfile.open(runtime_tar, mode="w") as archive:
        archive.add(ROOT / "scripts/e2e/build_competition_submission_v1.py", arcname="scripts/e2e/build_competition_submission_v1.py", filter=_tar_filter)
        archive.add(ROOT / "src/finance_query", arcname="src/finance_query", filter=_tar_filter)

    dataset_metadata = {
        "title": runtime_title,
        "id": runtime_slug,
        "licenses": [{"name": "other"}],
        "description": (
            "Full-corpus ViFinQA submission runtime with direct-source replay "
            "answer candidates. Candidates are re-read from the current table "
            "asset and Decimal-replayed before use; they are best-effort and not "
            "human-verified authority."
        ),
    }
    _write_json(runtime_dir / "dataset-metadata.json", dataset_metadata)

    kernel_dir.mkdir(parents=True)
    notebook = _patch_notebook(
        BASE_NOTEBOOK.read_text(encoding="utf-8"),
        runtime_slug=runtime_slug,
    )
    (kernel_dir / "main.ipynb").write_text(notebook, encoding="utf-8")
    kernel_metadata = json.loads(BASE_KERNEL_METADATA.read_text(encoding="utf-8"))
    kernel_metadata.update(
        {
            "id": kernel_slug,
            "title": kernel_title,
            "code_file": "main.ipynb",
        }
    )
    sources = list(kernel_metadata.get("dataset_sources") or [])
    if not sources:
        raise ValueError("template kernel has no dataset_sources")
    sources[0] = runtime_slug
    kernel_metadata["dataset_sources"] = sources
    _write_json(kernel_dir / "kernel-metadata.json", kernel_metadata)
    return {
        "output_root": str(output_root),
        "runtime_dir": str(runtime_dir),
        "kernel_dir": str(kernel_dir),
        "runtime_slug": runtime_slug,
        "kernel_slug": kernel_slug,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "artifacts/kaggle_upload/dungle2810_vifinqa_answer_optimized_v1_20260830",
    )
    parser.add_argument("--runtime-slug", default=RUNTIME_SLUG)
    parser.add_argument("--kernel-slug", default=KERNEL_SLUG)
    parser.add_argument(
        "--runtime-title",
        default="ViFinQA Answer Optimized Runtime V1 20260830",
    )
    parser.add_argument(
        "--kernel-title",
        default="ViFinQA Answer Optimized Submission V1 20260830",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            stage(
                args.output_root,
                runtime_slug=args.runtime_slug,
                kernel_slug=args.kernel_slug,
                runtime_title=args.runtime_title,
                kernel_title=args.kernel_title,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
