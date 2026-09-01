#!/usr/bin/env python3
"""Build the validated full-corpus lexical navigation index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.table_retrieval import (  # noqa: E402
    build_lexical_index,
    sha256_file,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--source-closure", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite output directory: {args.output_dir}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True)
    index_path = args.output_dir / "table_only_metadata_lexical_v1.sqlite"
    try:
        receipt = build_lexical_index(
            asset_path=args.assets,
            source_closure_path=args.source_closure,
            index_path=index_path,
            contract=config["asset_contract"],
        )
        receipt_path = args.output_dir / "lexical_build_receipt_v1.json"
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest = {
            "protocol": "vifinqa_full_corpus_lexical_manifest_v1",
            "inputs": {
                "config": {"path": str(args.config.resolve()), "sha256": sha256_file(args.config)},
                "assets": {"path": str(args.assets.resolve()), "sha256": sha256_file(args.assets)},
                "source_closure": {"path": str(args.source_closure.resolve()), "sha256": sha256_file(args.source_closure)},
            },
            "outputs": {
                index_path.name: {"sha256": sha256_file(index_path), "size_bytes": index_path.stat().st_size},
                receipt_path.name: {"sha256": sha256_file(receipt_path), "size_bytes": receipt_path.stat().st_size},
            },
            "authorization": config["authorization"],
        }
        (args.output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        for path in args.output_dir.iterdir():
            path.unlink(missing_ok=True)
        args.output_dir.rmdir()
        raise
    print(json.dumps({"status": "BUILT", **receipt}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
