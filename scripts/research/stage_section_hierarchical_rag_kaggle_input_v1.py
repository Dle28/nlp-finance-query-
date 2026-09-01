#!/usr/bin/env python3
"""Stage the immutable, value-blind inputs for the Kaggle section-RAG run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.section_rag_evaluation import (
    validate_section_chunk_assets,
    validate_section_rag_table_baseline,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source: Path, target: Path, *, root: Path) -> dict[str, object]:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if _sha256(source) != _sha256(target):
        raise ValueError(f"copy hash mismatch: {source}")
    return {"path": str(target.relative_to(root)), "sha256": _sha256(target), "size_bytes": target.stat().st_size}


def _assert_no_reviewer_flag(path: Path) -> None:
    if path.suffix != ".jsonl":
        return
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            row = json.loads(line)
            if "human_verified" in row:
                raise ValueError(f"sanitized Kaggle input contains human_verified: {path}:{line_number}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section-artifact-dir", type=Path, required=True)
    parser.add_argument("--baseline-artifact-dir", type=Path, required=True)
    parser.add_argument("--code-bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    validate_section_chunk_assets(args.section_artifact_dir)
    validate_section_rag_table_baseline(args.baseline_artifact_dir, expected_route_count=1232)
    if not args.code_bundle.is_file():
        raise SystemExit(f"code bundle is missing: {args.code_bundle}")
    args.output_dir.mkdir(parents=True)
    try:
        copied: dict[str, dict[str, object]] = {}
        section_files = {
            "section_artifact_manifest_v1.json": "manifest.json",
            "section_chunk_assets_v1.jsonl": "section_chunk_assets_v1.jsonl",
            "section_source_closure_v1.jsonl": "section_source_closure_v1.jsonl",
            "section_dense_contract_v1.json": "section_dense_contract_v1.json",
        }
        for target_name, source_name in section_files.items():
            copied[target_name] = _copy(args.section_artifact_dir / source_name, args.output_dir / target_name, root=args.output_dir)
        baseline_files = {
            "section_rag_table_baseline_manifest_v1.json": "manifest.json",
            "route_comparison_v1.jsonl": "route_comparison_v1.jsonl",
            "hybrid_table_candidates_v1.jsonl": "hybrid_table_candidates_v1.jsonl",
        }
        for target_name, source_name in baseline_files.items():
            source = args.baseline_artifact_dir / source_name
            _assert_no_reviewer_flag(source)
            copied[target_name] = _copy(source, args.output_dir / target_name, root=args.output_dir)
        copied[args.code_bundle.name] = _copy(args.code_bundle, args.output_dir / args.code_bundle.name, root=args.output_dir)
        manifest = {
            "schema_version": 1,
            "protocol": "vifinqa_section_hierarchical_rag_kaggle_input_v1",
            "files": copied,
            "section_chunk_count": 28924,
            "route_count": 1232,
            "candidate_count": 12320,
            "contains_human_verified": False,
            "contains_financial_source_values": False,
            "navigation_metadata_only": True,
            "may_authorize_answer": False,
            "submission_eligible": False,
        }
        (args.output_dir / "input_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps({"status": "STAGED", **manifest}, ensure_ascii=False, indent=2, sort_keys=True))
    except Exception:
        shutil.rmtree(args.output_dir, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
