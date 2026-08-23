#!/usr/bin/env python3
"""Materialize a hash-bound, navigation-only corporate/report graph."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.corporate_report_graph import (  # noqa: E402
    CORPORATE_REPORT_GRAPH_PROTOCOL,
    CORPORATE_REPORT_GRAPH_VERSION,
    GRAPH_SOURCE_CONTRACT,
    build_corporate_relationship_edges,
    build_report_families,
    build_report_relationship_edges,
    document_ticker_map,
    validate_corporate_report_graph,
)
from finance_query.report_entities import validate_report_entity_alias_sidecar  # noqa: E402
from finance_query.report_segments import validate_report_segment_sidecar  # noqa: E402
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _validate_document_metadata(bundle: Path, document_path: Path) -> dict[str, Any]:
    catalog_manifest_path = bundle / "table_routing_catalog_v1.manifest.json"
    if not catalog_manifest_path.is_file():
        raise FileNotFoundError("Table-routing catalog manifest is missing")
    manifest = json.loads(catalog_manifest_path.read_text(encoding="utf-8"))
    if manifest.get("document_metadata_file") != document_path.name:
        raise ValueError("Routing catalog manifest references another document metadata file")
    if manifest.get("document_metadata_sha256") != sha256_file(document_path):
        raise ValueError("Document metadata does not match routing catalog manifest")
    return manifest


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not bundle.is_dir():
        raise FileNotFoundError("Bundle directory is missing")
    if output_dir.exists():
        raise FileExistsError("Output directory already exists; choose a new immutable output path")
    tables_path = bundle / "tables.jsonl"
    documents_path = bundle / "document_metadata_v1.jsonl"
    segments_path = bundle / "report_segments_v1.jsonl"
    aliases_path = bundle / "report_entity_aliases_v1.jsonl"
    for path in (tables_path, documents_path, segments_path, aliases_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing graph input: {path.name}")
    catalog_manifest = _validate_document_metadata(bundle, documents_path)
    segment_manifest = validate_report_segment_sidecar(bundle, segments_path)
    alias_manifest = validate_report_entity_alias_sidecar(bundle, aliases_path)
    tables = load_jsonl(tables_path)
    documents = load_jsonl(documents_path)
    segments = load_jsonl(segments_path)
    aliases = load_jsonl(aliases_path)
    if len({str(row.get("internal_table_uid") or "") for row in tables}) != len(tables):
        raise ValueError("Bundle tables must have unique non-empty UIDs")
    if {str(row.get("internal_table_uid") or "") for row in tables} != {
        str(row.get("internal_table_uid") or "") for row in segments
    }:
        raise ValueError("Tables and report segments do not have identical UID coverage")
    document_tickers = document_ticker_map(tables)
    document_ids = {str(row.get("document_id") or "") for row in documents}
    if set(document_tickers) != document_ids:
        raise ValueError("Tables and document metadata do not have identical document coverage")
    families = build_report_families(documents, document_tickers=document_tickers)
    report_edges = build_report_relationship_edges(documents, document_tickers=document_tickers)
    corporate_edges = build_corporate_relationship_edges(tables, segments, aliases)
    output_dir.mkdir(parents=True)
    family_path = output_dir / "report_families_v1.jsonl"
    report_edge_path = output_dir / "report_relationship_edges_v1.jsonl"
    corporate_edge_path = output_dir / "corporate_relationship_edges_v1.jsonl"
    atomic_write_jsonl(family_path, families)
    atomic_write_jsonl(report_edge_path, report_edges)
    atomic_write_jsonl(corporate_edge_path, corporate_edges)
    manifest = {
        "schema_version": CORPORATE_REPORT_GRAPH_VERSION,
        "protocol": CORPORATE_REPORT_GRAPH_PROTOCOL,
        "inputs": {
            "bundle_tables": {"file": tables_path.name, "sha256": sha256_file(tables_path)},
            "document_metadata": {"file": documents_path.name, "sha256": sha256_file(documents_path)},
            "routing_catalog_manifest": {
                "file": "table_routing_catalog_v1.manifest.json",
                "sha256": sha256_file(bundle / "table_routing_catalog_v1.manifest.json"),
            },
            "report_segments": {"file": segments_path.name, "sha256": sha256_file(segments_path)},
            "report_segments_manifest": {
                "file": "report_segments_v1.manifest.json",
                "sha256": sha256_file(segments_path.with_suffix(".manifest.json")),
            },
            "report_entity_aliases": {"file": aliases_path.name, "sha256": sha256_file(aliases_path)},
            "report_entity_aliases_manifest": {
                "file": "report_entity_aliases_v1.manifest.json",
                "sha256": sha256_file(aliases_path.with_suffix(".manifest.json")),
            },
        },
        "upstream_manifest_hashes": {
            "table_routing_catalog_document_metadata_sha256": catalog_manifest.get("document_metadata_sha256"),
            "report_segments_sidecar_sha256": segment_manifest.get("sidecar_sha256"),
            "report_entity_aliases_sidecar_sha256": alias_manifest.get("sidecar_sha256"),
        },
        "report_family_count": len(families),
        "report_relationship_edge_count": len(report_edges),
        "corporate_relationship_edge_count": len(corporate_edges),
        "outputs": {
            "report_families": {"file": family_path.name, "sha256": sha256_file(family_path)},
            "report_relationship_edges": {"file": report_edge_path.name, "sha256": sha256_file(report_edge_path)},
            "corporate_relationship_edges": {"file": corporate_edge_path.name, "sha256": sha256_file(corporate_edge_path)},
        },
        "source_contract": dict(GRAPH_SOURCE_CONTRACT),
    }
    manifest_path = output_dir / "corporate_report_graph_v1.manifest.json"
    atomic_write_json(manifest_path, manifest)
    validate_corporate_report_graph(output_dir)
    print(json.dumps({"output_dir": str(output_dir), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
