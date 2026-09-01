#!/usr/bin/env python3
"""Package the self-supervised curriculum and Kaggle training notebook."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
CODE_FILES = (
    "configs/training/vifinqa_rag_finetune_v1.json",
    "notebooks/vifinqa_rag_finetune_kaggle_v1.ipynb",
    "scripts/training/build_vifinqa_self_supervised_v1.py",
    "src/finance_query/training/__init__.py",
    "src/finance_query/training/synthetic_curriculum.py",
    "src/finance_query/e2e/core/learned_rag.py",
    "docs/research/RAG_FINETUNE_PIPELINE_V1.md",
)
REQUIREMENTS = """sentence-transformers>=5,<6
datasets>=3,<5
transformers>=4.51,<5
accelerate>=1.6,<2
peft>=0.15,<0.19
trl>=0.18,<0.25
bitsandbytes>=0.45,<0.49
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curriculum-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    curriculum_manifest = json.loads((args.curriculum_dir / "manifest.json").read_text(encoding="utf-8"))
    if curriculum_manifest.get("competition_questions_read") != 0:
        raise ValueError("curriculum is not test-question blind")
    if any(curriculum_manifest.get("split_ticker_overlap", {}).values()):
        raise ValueError("curriculum has ticker leakage")
    args.output_dir.mkdir(parents=True)
    archive = args.output_dir / "vifinqa_rag_finetune_kaggle_v1.zip"
    files: dict[str, dict[str, object]] = {}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for relative in CODE_FILES:
            path = ROOT / relative
            bundle.write(path, f"AI_guru/{relative}")
            files[f"AI_guru/{relative}"] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        for path in sorted(args.curriculum_dir.glob("*.json*")):
            arcname = f"curriculum/{path.name}"
            bundle.write(path, arcname)
            files[arcname] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        bundle.writestr("requirements-kaggle-training.txt", REQUIREMENTS)
    manifest = {
        "protocol": "vifinqa_rag_finetune_kaggle_package_v1",
        "archive": archive.name,
        "archive_sha256": sha256(archive),
        "archive_size_bytes": archive.stat().st_size,
        "curriculum_protocol": curriculum_manifest["protocol"],
        "competition_questions_read": 0,
        "split_ticker_overlap": curriculum_manifest["split_ticker_overlap"],
        "files": files,
        "kaggle_steps": [
            "Upload this ZIP as a private Kaggle Dataset and attach it to a GPU notebook.",
            "Open AI_guru/notebooks/vifinqa_rag_finetune_kaggle_v1.ipynb.",
            "Run once with FAST_DEV_RUN=True, then rerun from a fresh session with False.",
            "Download /kaggle/working/vifinqa_rag_finetuned_v1.zip.",
        ],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
