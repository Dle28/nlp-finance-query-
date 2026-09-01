#!/usr/bin/env python3
"""Build an immutable full-dataset baseline/candidate comparison artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_query.research.full_dataset_compare import (
    compare_full_dataset,
    load_jsonl,
    sha256_file,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {args.output_dir}")
    rows, report = compare_full_dataset(
        baseline_rows=load_jsonl(args.baseline),
        candidate_rows=load_jsonl(args.candidate),
        taxonomy_rows=load_jsonl(args.taxonomy),
    )
    args.output_dir.mkdir(parents=True)
    rows_path = args.output_dir / "comparison_rows_v1.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = args.output_dir / "comparison_report_v1.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "protocol": "vifinqa_full_dataset_candidate_comparison_manifest_v1",
        "schema_version": 1,
        "inputs": {
            "baseline": {"path": str(args.baseline), "sha256": sha256_file(args.baseline)},
            "candidate": {"path": str(args.candidate), "sha256": sha256_file(args.candidate)},
            "taxonomy": {"path": str(args.taxonomy), "sha256": sha256_file(args.taxonomy)},
        },
        "outputs": {
            rows_path.name: {"sha256": sha256_file(rows_path)},
            report_path.name: {"sha256": sha256_file(report_path)},
        },
        "source_contract": report["source_contract"],
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
