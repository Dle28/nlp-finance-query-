#!/usr/bin/env python3
"""Create a small Kaggle code bundle for the full dense-index notebook."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_FILES = (
    "configs/retrieval/full_corpus_v1.json",
    "notebooks/vifinqa_full_dense_index_kaggle_v1.ipynb",
    "scripts/research/build_full_corpus_dense_v1.py",
    "scripts/research/search_full_corpus_dense_v1.py",
    "scripts/e2e/validate_full_corpus_retrieval_v1.py",
    "src/finance_query/__init__.py",
    "src/finance_query/e2e/__init__.py",
    "src/finance_query/e2e/core/__init__.py",
    "src/finance_query/e2e/core/dense_retrieval.py",
    "src/finance_query/e2e/core/table_retrieval.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    archive = args.output_dir / "vifinqa_full_dense_code_v1.zip"
    file_manifest = {}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in PACKAGE_FILES:
            path = ROOT / relative
            if not path.exists():
                raise FileNotFoundError(path)
            bundle.write(path, arcname=f"AI_guru/{relative}")
            file_manifest[relative] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        requirements = "sentence-transformers>=3,<6\nnumpy>=1.26\ntorch>=2.2\n"
        bundle.writestr("AI_guru/requirements-kaggle-retrieval.txt", requirements)
        file_manifest["requirements-kaggle-retrieval.txt"] = {
            "sha256": hashlib.sha256(requirements.encode()).hexdigest(),
            "size_bytes": len(requirements.encode()),
        }
    manifest = {
        "protocol": "vifinqa_full_dense_kaggle_code_bundle_v1",
        "archive": archive.name,
        "archive_sha256": sha256(archive),
        "files": file_manifest,
        "does_not_include_assets": True,
        "required_asset_filename": "full_table_assets_v1.jsonl",
        "required_source_closure_filename": "source_closure_v1.jsonl",
        "required_source_closure_sha256": "59f27c643bd1c4a640e7670dd94789e3222c27a6022be3f7319947b65b6dd9fd",
        "required_asset_sha256": "617ae044499907cf302519b7ff6fb96fb86ab70ff08e6fdcb0e6a3f8df5251f7",
        "navigation_metadata_only": True,
        "submission_eligible": False,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PACKAGED", **manifest}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
