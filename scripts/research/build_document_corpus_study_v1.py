#!/usr/bin/env python3
"""Build the frozen ViFinQA document-corpus study and Vietnamese report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.document_corpus_study import (  # noqa: E402
    PROTOCOL,
    document_inventory,
    diagnose_asset_coverage,
    evaluate_hypotheses,
    freeze_ticker_split,
    load_documents,
    question_document_routes,
    render_report_vi,
    scan_raw_table_inventory,
    scan_table_assets,
    sha256_file,
    validate_study,
    verify_offsets,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypotheses", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--table-assets", type=Path, required=True)
    parser.add_argument("--raw-table-inventory", type=Path, required=True)
    parser.add_argument("--question-taxonomy", type=Path, required=True)
    parser.add_argument("--dense-meta", type=Path, required=True)
    parser.add_argument("--lexical-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite: {args.output_dir}")
    hypothesis = json.loads(args.hypotheses.read_text(encoding="utf-8"))
    documents = load_documents(args.documents)
    split = freeze_ticker_split((str(row["ticker"]) for row in documents), hypothesis["seed"])
    document_summary, exact_keys, slots = document_inventory(documents, split)
    raw_table_summary = scan_raw_table_inventory(args.raw_table_inventory, split)
    table_summary, samples, asset_doc_counts = scan_table_assets(
        args.table_assets,
        split,
        seed=hypothesis["seed"],
        sample_per_stage=int(hypothesis["split"]["offset_verification_tables_per_stage"]),
    )
    offset_rows, offset_summary = verify_offsets(samples)
    coverage_diagnosis = diagnose_asset_coverage(
        documents,
        asset_doc_counts,
        dense_meta_path=args.dense_meta,
        lexical_index_path=args.lexical_index,
        asset_count=table_summary["asset_count"],
    )
    route_summary = question_document_routes(args.question_taxonomy, exact_keys, slots)
    audit_table_count = sum(int(row["table_count"]) for row in documents)
    hypothesis_results = evaluate_hypotheses(
        document=document_summary,
        tables=table_summary,
        raw_tables=raw_table_summary,
        coverage_diagnosis=coverage_diagnosis,
        offsets=offset_summary,
        routes=route_summary,
        audit_table_count=audit_table_count,
        asset_document_counts=asset_doc_counts,
        documents=documents,
    )
    report = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "hypothesis_freeze_protocol": hypothesis["protocol"],
        "hypothesis_definitions": hypothesis["hypotheses"],
        "ticker_split": split,
        "document_inventory": document_summary,
        "raw_table_inventory": raw_table_summary,
        "table_asset_inventory": table_summary,
        "asset_coverage_diagnosis": coverage_diagnosis,
        "offset_verification": offset_summary,
        "question_routes": route_summary,
        "hypothesis_results": hypothesis_results,
        "source_contract": hypothesis["source_contract"],
    }
    validate_study(report)
    args.output_dir.mkdir(parents=True)
    split_path = args.output_dir / "ticker_split_v1.json"
    split_path.write_text(json.dumps(split, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    offset_path = args.output_dir / "offset_verification_v1.jsonl"
    offset_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in offset_rows), encoding="utf-8")
    json_path = args.output_dir / "document_corpus_study_v1.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = args.output_dir / "document_corpus_report_vi.md"
    markdown_path.write_text(render_report_vi(report), encoding="utf-8")
    inputs = {
        "hypotheses": args.hypotheses,
        "documents": args.documents,
        "table_assets": args.table_assets,
        "raw_table_inventory": args.raw_table_inventory,
        "question_taxonomy": args.question_taxonomy,
        "dense_meta": args.dense_meta,
        "lexical_index": args.lexical_index,
    }
    outputs = {
        split_path.name: split_path,
        offset_path.name: offset_path,
        json_path.name: json_path,
        markdown_path.name: markdown_path,
    }
    manifest = {
        "protocol": "vifinqa_document_corpus_study_manifest_v1",
        "schema_version": 1,
        "inputs": {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for name, path in inputs.items()
        },
        "outputs": {
            name: {"sha256": sha256_file(path)} for name, path in outputs.items()
        },
        "source_contract": report["source_contract"],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "BUILT",
        "documents": document_summary["document_count"],
        "raw_tables": raw_table_summary["raw_table_count"],
        "table_assets": table_summary["asset_count"],
        "offset_verified": offset_summary["verified_count"],
        "hypotheses_supported": sum(row["supported"] for row in hypothesis_results),
        "hypotheses_not_supported": sum(not row["supported"] for row in hypothesis_results),
        "route_coverage": route_summary["covered_rate_on_evaluable"],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
