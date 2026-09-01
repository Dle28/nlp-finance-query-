#!/usr/bin/env python3
"""Package the reproducible Kaggle GPU code for section RAG evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_FILES = (
    "configs/research/section_hierarchical_rag_evaluation_v1.json",
    "notebooks/vifinqa_section_hierarchical_rag_kaggle_v1.ipynb",
    "scripts/research/build_section_dense_index_v1.py",
    "scripts/research/build_section_rag_table_baseline_v1.py",
    "scripts/research/build_section_hierarchical_rag_evaluation_v1.py",
    "scripts/research/validate_section_chunk_assets_v1.py",
    "scripts/research/validate_section_rag_table_baseline_v1.py",
    "scripts/research/validate_section_hierarchical_rag_evaluation_v1.py",
    "src/finance_query/__init__.py",
    "src/finance_query/e2e/__init__.py",
    "src/finance_query/e2e/core/__init__.py",
    "src/finance_query/e2e/core/dense_retrieval.py",
    "src/finance_query/e2e/core/report_segments.py",
    "src/finance_query/e2e/core/schemas.py",
    "src/finance_query/e2e/core/table_retrieval.py",
    "src/finance_query/e2e/core/table_structure.py",
    "src/finance_query/research/__init__.py",
    "src/finance_query/research/section_rag_evaluation.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    archive = args.output_dir / "vifinqa_section_hierarchical_rag_code_v1.zip"
    files: dict[str, dict[str, object]] = {}
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative in PACKAGE_FILES:
            path = ROOT / relative
            if not path.is_file():
                raise FileNotFoundError(path)
            bundle.write(path, arcname=f"AI_guru/{relative}")
            files[relative] = {"sha256": sha256(path), "size_bytes": path.stat().st_size}
        requirements = "beautifulsoup4>=4.12\nlxml>=5\nnumpy>=1.26\nsentence-transformers>=3,<6\ntorch>=2.2\n"
        bundle.writestr("AI_guru/requirements-kaggle-section-rag.txt", requirements)
        files["requirements-kaggle-section-rag.txt"] = {
            "sha256": hashlib.sha256(requirements.encode()).hexdigest(),
            "size_bytes": len(requirements.encode()),
        }
    manifest = {
        "schema_version": 1,
        "protocol": "vifinqa_section_hierarchical_rag_kaggle_code_bundle_v1",
        "archive": archive.name,
        "archive_sha256": sha256(archive),
        "files": files,
        "required_inputs": {
            "section_artifact_manifest_v1.json": "renamed manifest for flat Kaggle Dataset upload",
            "section_chunk_assets_v1.jsonl": "source-bounded, value-blind section chunks",
            "section_source_closure_v1.jsonl": "closure for section dense index",
            "section_dense_contract_v1.json": "hash-bound dense-index contract",
            "section_rag_table_baseline_manifest_v1.json": "renamed manifest for sanitized table-only baseline",
        },
        "does_not_include_financial_source_values": True,
        "navigation_metadata_only": True,
        "submission_eligible": False,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "PACKAGED", **manifest}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
