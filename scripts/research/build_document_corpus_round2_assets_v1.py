#!/usr/bin/env python3
"""Build and replay the complete research-only table asset closure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.document_corpus_round2 import (  # noqa: E402
    build_full_assets_atomic,
    detect_prefix_truncation,
    interruption_safety_probe,
    replay_asset_digest,
)
from finance_query.research.document_corpus_study import sha256_file  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--old-partial-assets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    asset_path = args.output_dir / "full_table_assets_v1.jsonl"
    closure_path = args.output_dir / "source_closure_v1.jsonl"
    build = build_full_assets_atomic(
        reports_root=args.reports_root,
        document_csv=args.documents,
        output_path=asset_path,
        closure_path=closure_path,
    )
    replay = replay_asset_digest(reports_root=args.reports_root, document_csv=args.documents)
    prefix = detect_prefix_truncation(args.old_partial_assets, args.documents)
    interruption = interruption_safety_probe(args.output_dir)
    receipt = {
        "protocol": "vifinqa_document_corpus_round2_build_receipt_v1",
        "build": build,
        "replay": replay,
        "deterministic_replay_match": (
            build["table_count"] == replay["table_count"]
            and build["output_sha256"] == replay["canonical_sha256"]
        ),
        "old_partial_prefix_detection": prefix,
        "atomic_interruption_probe": interruption,
        "source_contract": {
            "research_only": True,
            "production_index_replacement_allowed": False,
            "submission_eligible": False,
        },
    }
    receipt_path = args.output_dir / "build_receipt_v1.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "protocol": "vifinqa_document_corpus_round2_assets_manifest_v1",
        "inputs": {
            "protocol": {"path": str(args.protocol.resolve()), "sha256": sha256_file(args.protocol)},
            "documents": {"path": str(args.documents.resolve()), "sha256": sha256_file(args.documents)},
            "old_partial_assets": {"path": str(args.old_partial_assets.resolve()), "sha256": sha256_file(args.old_partial_assets)},
        },
        "outputs": {
            asset_path.name: {"sha256": sha256_file(asset_path)},
            closure_path.name: {"sha256": sha256_file(closure_path)},
            receipt_path.name: {"sha256": sha256_file(receipt_path)},
        },
        "source_contract": receipt["source_contract"],
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "BUILT", "reports": build["report_count"], "tables": build["table_count"], "deterministic": receipt["deterministic_replay_match"], "elapsed_seconds": build["elapsed_seconds"] + replay["elapsed_seconds"], "output_size_bytes": build["output_size_bytes"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
