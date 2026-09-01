#!/usr/bin/env python3
"""Validate completed lexical or dense full-corpus navigation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.table_retrieval import load_jsonl, sha256_file  # noqa: E402


def validate_manifest(directory: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return ["missing manifest.json"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("navigation_metadata_only") is not True and (
        manifest.get("authorization") or {}
    ).get("navigation_metadata_only") is not True:
        errors.append("manifest is not navigation-only")
    authorization = manifest.get("authorization") or manifest
    if authorization.get("submission_eligible") is not False:
        errors.append("manifest does not disable submission eligibility")
    for name, entry in manifest.get("outputs", {}).items():
        path = directory / name
        if not path.exists():
            errors.append(f"missing output: {name}")
        elif sha256_file(path) != entry.get("sha256"):
            errors.append(f"SHA mismatch: {name}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("lexical", "dense"), required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    errors = validate_manifest(args.artifact_dir)
    checks: dict[str, object] = {"kind": args.kind}
    if args.kind == "lexical":
        receipt = json.loads(
            (args.artifact_dir / "lexical_build_receipt_v1.json").read_text(encoding="utf-8")
        )
        index = args.artifact_dir / "table_only_metadata_lexical_v1.sqlite"
        with sqlite3.connect(index) as connection:
            count = int(connection.execute("SELECT count(*) FROM tables_fts").fetchone()[0])
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        checks.update({"count": count, "sqlite_integrity": integrity})
        if count != 146246 or receipt.get("indexed_count") != 146246:
            errors.append("lexical count is not 146246")
        if integrity != "ok":
            errors.append(f"SQLite integrity failed: {integrity}")
        if receipt.get("context_in_index") is not False:
            errors.append("lexical representation unexpectedly includes context")
    else:
        import numpy as np

        receipt = json.loads(
            (args.artifact_dir / "dense_build_receipt_v1.json").read_text(encoding="utf-8")
        )
        embeddings = np.load(args.artifact_dir / "dense_embeddings_v1.npy", mmap_mode="r")
        metadata_count = sum(1 for _ in load_jsonl(args.artifact_dir / "dense_metadata_v1.jsonl"))
        uid_count = sum(1 for _ in load_jsonl(args.artifact_dir / "dense_uids_v1.jsonl"))
        checks.update(
            {
                "embedding_shape": list(embeddings.shape),
                "metadata_count": metadata_count,
                "uid_count": uid_count,
                "resolved_device": receipt.get("resolved_device"),
            }
        )
        if embeddings.shape != (146246, int(receipt.get("dimension") or 0)):
            errors.append(f"dense shape mismatch: {embeddings.shape}")
        if metadata_count != 146246 or uid_count != 146246 or receipt.get("count") != 146246:
            errors.append("dense population is not 146246")
        if (args.artifact_dir / "dense_build_state_v1.json").exists():
            errors.append("dense checkpoint remains after claimed completion")
    closure = receipt.get("asset_closure") or {}
    if closure.get("document_count") != 1965:
        errors.append("asset-bearing document count is not 1965")
    if closure.get("source_report_count") != 1973:
        errors.append("source report count is not 1973")
    if closure.get("zero_table_report_count") != 8:
        errors.append("zero-table report count is not 8")
    if receipt.get("navigation_metadata_only") is not True:
        errors.append("receipt is not navigation-only")
    if receipt.get("may_authorize_answer") is not False:
        errors.append("receipt does not forbid answer authorization")
    if receipt.get("submission_eligible") is not False:
        errors.append("receipt does not forbid submission eligibility")
    result = {
        "status": "VALID" if not errors else "INVALID",
        "errors": errors,
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
