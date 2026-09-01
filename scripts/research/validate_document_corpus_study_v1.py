#!/usr/bin/env python3
"""Validate hashes and population gates of a document corpus study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.document_corpus_study import sha256_file, validate_study  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != "vifinqa_document_corpus_study_manifest_v1":
        raise SystemExit("manifest protocol mismatch")
    for descriptor in manifest["inputs"].values():
        path = Path(descriptor["path"])
        if sha256_file(path) != descriptor["sha256"]:
            raise SystemExit(f"input hash mismatch: {path}")
    for name, descriptor in manifest["outputs"].items():
        path = args.artifact_dir / name
        if sha256_file(path) != descriptor["sha256"]:
            raise SystemExit(f"output hash mismatch: {path}")
    report = json.loads((args.artifact_dir / "document_corpus_study_v1.json").read_text(encoding="utf-8"))
    validate_study(report)
    print(json.dumps({
        "status": "VALIDATION_PASSED",
        "documents": report["document_inventory"]["document_count"],
        "raw_tables": report["raw_table_inventory"]["raw_table_count"],
        "table_assets": report["table_asset_inventory"]["asset_count"],
        "offset_match_rate": report["offset_verification"]["match_rate"],
        "hypotheses": len(report["hypothesis_results"]),
        "submission_eligible": report["source_contract"]["submission_eligible"],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
