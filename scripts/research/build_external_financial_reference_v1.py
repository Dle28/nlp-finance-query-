#!/usr/bin/env python3
"""Build a provenance-bound FinQA/TAT-QA external evaluation index."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.external_financial_reference import build_external_reference


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finqa-root", type=Path, required=True)
    parser.add_argument("--tatqa-root", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {args.output_dir}")
    source_config = json.loads(args.source_config.read_text(encoding="utf-8"))
    rows, report = build_external_reference(
        finqa_root=args.finqa_root,
        tatqa_root=args.tatqa_root,
        source_config=source_config,
    )
    args.output_dir.mkdir(parents=True)
    index_path = args.output_dir / "external_financial_reference_v1.jsonl"
    with index_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report_path = args.output_dir / "validation_report_v1.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "protocol": "external_financial_reference_manifest_v1",
        "source_config": {"path": str(args.source_config), "sha256": _sha256(args.source_config)},
        "index": {"path": str(index_path), "sha256": _sha256(index_path)},
        "validation_report": {"path": str(report_path), "sha256": _sha256(report_path)},
        "counts": report,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
