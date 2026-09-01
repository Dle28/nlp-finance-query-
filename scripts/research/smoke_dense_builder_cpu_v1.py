#!/usr/bin/env python3
"""Run the real dense builder on a tiny CPU-only sample."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from itertools import islice
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.e2e.core.dense_retrieval import build_dense_index  # noqa: E402
from finance_query.e2e.core.table_retrieval import load_jsonl  # noqa: E402


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=8)
    parser.add_argument("--model", default="intfloat/multilingual-e5-small")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    if args.sample_count < 2:
        raise SystemExit("sample-count must be at least 2")
    args.output_dir.mkdir(parents=True)
    inputs = args.output_dir / "smoke_inputs"
    inputs.mkdir()
    rows = list(islice(load_jsonl(args.assets), args.sample_count))
    if len(rows) != args.sample_count:
        raise SystemExit(f"asset file has fewer than {args.sample_count} rows")
    sample_assets = inputs / "sample_assets.jsonl"
    sample_assets.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    document_counts = Counter(str(row["document_id"]) for row in rows)
    sample_closure = inputs / "sample_source_closure.jsonl"
    sample_closure.write_text(
        "".join(
            json.dumps({"document_id": document_id, "table_count": count}, sort_keys=True) + "\n"
            for document_id, count in sorted(document_counts.items())
        ),
        encoding="utf-8",
    )
    contract = {
        "expected_asset_sha256": file_sha256(sample_assets),
        "expected_source_closure_sha256": file_sha256(sample_closure),
        "expected_table_count": len(rows),
        "expected_document_count": len(document_counts),
        "expected_source_report_count": len(document_counts),
        "expected_zero_table_report_count": 0,
        "expected_ticker_count": len({str(row["ticker"]) for row in rows}),
    }
    receipt = build_dense_index(
        asset_path=sample_assets,
        source_closure_path=sample_closure,
        output_dir=args.output_dir / "dense_smoke_only",
        contract=contract,
        model_name=args.model,
        requested_device="cpu",
        batch_size=4,
        checkpoint_every_batches=1,
    )
    smoke = {
        "protocol": "vifinqa_dense_cpu_real_model_smoke_v1",
        "smoke_only": True,
        "full_corpus_index": False,
        "sample_count": len(rows),
        "model": args.model,
        "resolved_device": receipt["resolved_device"],
        "dimension": receipt["dimension"],
        "completed": receipt["count"] == len(rows),
    }
    (args.output_dir / "SMOKE_ONLY.json").write_text(
        json.dumps(smoke, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(smoke, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
