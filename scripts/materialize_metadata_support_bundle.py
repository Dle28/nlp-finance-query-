#!/usr/bin/env python3
"""Create a new review bundle with verified raw metadata-support tables.

This is a local migration path for a bundle exported before metadata support
existed.  It never mutates the input bundle, corpus, lexical index or dense
index.  It copies the immutable review records and adds only raw-report tables
whose inclusion is controlled by a direct/formula plan.  The V2/V3 sidecars
are materialized together, so every new UID remains subject to the ordinary
exact-row and period-cell gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.binding import row_label  # noqa: E402
from finance_query.corpus import extract_assets_from_report  # noqa: E402
from finance_query.evidence_context import (  # noqa: E402
    EVIDENCE_CONTEXT_VERSION,
    build_evidence_context,
    evidence_context_manifest_path,
    recover_continuation_headers,
    validate_evidence_context_sidecar,
)
from finance_query.financial_metrics import fold_text, infer_formula_spec  # noqa: E402
from finance_query.source_completion import source_report_index  # noqa: E402
from finance_query.table_structure import (  # noqa: E402
    parse_html_table,
    sha256_file,
    validate_structure_sidecar,
)


FORMULA_POLICY = "resolved_operand_entity_year_or_following_statement_function_v1"
DIRECT_POLICY = "resolved_direct_entity_year_exact_row_phrase_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--reports-root", type=Path, default=ROOT / "data/ViFinQA/financial_statements"
    )
    parser.add_argument(
        "--evidence-context",
        type=Path,
        default=None,
        help="Validated V3 context sidecar in the input bundle; required when its historical default is stale.",
    )
    parser.add_argument("--max-formula-support-tables", type=int, default=128)
    parser.add_argument("--max-direct-support-tables", type=int, default=24)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def jsonl_uid_set(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig") as handle:
        return {str(json.loads(line).get("internal_table_uid") or "") for line in handle if line.strip()}


def jsonl_count(path: Path) -> int:
    with path.open(encoding="utf-8-sig") as handle:
        return sum(1 for line in handle if line.strip())


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def direct_tokens(value: object) -> list[str]:
    return [token for token in fold_text(str(value or "")).split() if not token.isdecimal()]


def direct_row_matches(metric: object, row: list[Any]) -> bool:
    metric_tokens = direct_tokens(metric)
    return len(metric_tokens) >= 2 and direct_tokens(row_label([str(value) for value in row])) == metric_tokens


def formula_requests(formula: dict[str, Any]) -> list[dict[str, Any]]:
    requests = []
    for operand in formula.get("operands") or []:
        entity = str(operand.get("entity") or "").strip()
        functions = {str(value) for value in operand.get("allowed_table_functions") or [] if str(value)}
        years = {int(value) for value in operand.get("years") or [] if str(value).isdigit()}
        if entity and functions and years:
            requests.append(
                {
                    "entity": entity,
                    "functions": functions,
                    "years": years,
                    "operand_id": str(operand.get("operand_id") or ""),
                }
            )
    return requests


def materialize(asset: Any, path: Path, inclusion: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    table_html = text[int(asset.char_start) : int(asset.char_end)]
    structure = parse_html_table(table_html, context=str(asset.context_before))
    if structure.get("rows") != asset.rows:
        raise ValueError("raw support grid differs from canonical raw extractor")
    if dict(structure.get("table_function") or {}) != dict(asset.table_function):
        raise ValueError("raw support table function differs from canonical raw extractor")
    table = {
        "internal_table_uid": asset.internal_table_uid,
        "document_id": asset.document_id,
        "ticker": asset.ticker,
        "report_year": asset.report_year,
        "scope": asset.scope,
        "local_ordinal": asset.local_ordinal,
        "page_no": asset.page_no,
        "unit_hint": asset.unit_hint,
        "context_before": asset.context_before,
        "source_provenance": {
            "source_path": str(path),
            "source_sha256": asset.source_sha256,
            "char_start": asset.char_start,
            "table_sha256": asset.table_sha256,
        },
        "bundle_inclusion": inclusion,
        **structure,
    }
    context = build_evidence_context(table)
    if str((context.get("quality") or {}).get("status") or "") != "review_ready":
        raise ValueError("raw support table is not V2/V3 review_ready")
    return table, context


def main() -> None:
    args = parse_args()
    if args.max_formula_support_tables < 1 or args.max_direct_support_tables < 1:
        raise ValueError("metadata support limits must be positive")
    source = args.bundle_dir.resolve()
    output = args.output_dir.resolve()
    reports_root = args.reports_root.resolve()
    if source == output or output.exists():
        raise FileExistsError("output-dir must be a new directory distinct from the immutable input bundle")
    if not reports_root.is_dir():
        raise NotADirectoryError(reports_root)

    structure_path = source / "tables_structured_v2.jsonl"
    context_path = (
        args.evidence_context.resolve()
        if args.evidence_context is not None
        else source / f"tables_evidence_context_v{EVIDENCE_CONTEXT_VERSION}.jsonl"
    )
    if context_path.parent != source:
        raise ValueError("evidence-context must reside directly in the input bundle")
    validate_structure_sidecar(source, structure_path)
    validate_evidence_context_sidecar(source, structure_path, context_path)
    source_items = load_jsonl(source / "review_items.jsonl")
    source_tables_path = source / "tables.jsonl"
    known_uids = jsonl_uid_set(source_tables_path)
    source_table_count = jsonl_count(source_tables_path)
    if "" in known_uids or len(known_uids) != source_table_count:
        raise ValueError("input bundle has duplicate table UIDs")
    source_structure_count = jsonl_count(structure_path)
    if source_structure_count != source_table_count or jsonl_uid_set(structure_path) != known_uids:
        raise ValueError("input V2 structure UID set differs from input bundle")
    source_context_count = jsonl_count(context_path)
    if source_context_count != source_table_count or jsonl_uid_set(context_path) != known_uids:
        raise ValueError("input V3 context UID set differs from input bundle")

    report_paths = source_report_index(reports_root)
    # A full report expands into many long OCR strings.  This is deliberately
    # a tiny LRU cache: support discovery is source-verification work, not a
    # corpus rebuild and must not retain the entire corpus in RAM.
    assets_cache: OrderedDict[Path, list[Any]] = OrderedDict()
    candidate_inclusions: dict[str, dict[str, Any]] = {}
    candidate_locators: dict[str, tuple[Path, int]] = {}
    skipped: Counter[str] = Counter()

    def assets(path: Path) -> list[Any]:
        cached = assets_cache.pop(path, None)
        if cached is None:
            cached = extract_assets_from_report(path, reports_root)
        assets_cache[path] = cached
        while len(assets_cache) > 2:
            assets_cache.popitem(last=False)
        return cached

    def collect(asset: Any, path: Path, kind: str, payload: dict[str, Any]) -> None:
        uid = str(asset.internal_table_uid)
        if uid in known_uids:
            skipped["already_in_bundle"] += 1
            return
        entry = candidate_inclusions.setdefault(uid, {})
        values = entry.setdefault(kind, {"policy": payload["policy"], "question_ids": [], **payload.get("fields", {})})
        values["question_ids"] = sorted(set(values.get("question_ids") or []) | {payload["question_id"]})
        for key, value in (payload.get("append") or {}).items():
            values[key] = sorted(set(values.get(key) or []) | set(value))
        candidate_locators[uid] = (path, int(asset.local_ordinal))

    for position, item in enumerate(source_items, start=1):
        plan = item.get("question_plan") or {}
        qid = int(item["id"])
        ticker_values = {str(value) for value in plan.get("tickers") or [] if str(value)}
        years = {int(value) for value in plan.get("years") or [] if str(value).isdigit()}
        expected_scope = str(plan.get("scope") or "")

        if str(plan.get("family") or "") == "direct_lookup" and len(ticker_values) == 1 and years:
            ticker = next(iter(ticker_values))
            matches = []
            for year in sorted(years):
                for path in report_paths.get((ticker.casefold(), year), []):
                    for asset in assets(path):
                        if expected_scope and asset.scope != expected_scope:
                            continue
                        row_indices = [index for index, row in enumerate(asset.rows) if direct_row_matches(item.get("effective_metric"), row)]
                        if row_indices:
                            matches.append((asset, path, row_indices))
            matches.sort(key=lambda value: (value[0].scope, value[0].document_id, value[0].local_ordinal, value[0].internal_table_uid))
            for asset, path, row_indices in matches[: args.max_direct_support_tables]:
                collect(asset, path, "direct_metadata_support", {"policy": DIRECT_POLICY, "question_id": qid, "fields": {"effective_metrics": [str(item.get("effective_metric") or "")], "matching_row_indices": list(row_indices)}})
            if len(matches) > args.max_direct_support_tables:
                skipped["direct_per_question_cap"] += len(matches) - args.max_direct_support_tables

        formula = infer_formula_spec(str(item.get("question") or ""))
        requests = formula_requests(formula) if formula else []
        formula_matches: dict[str, tuple[Any, Path, set[str]]] = {}
        for request in requests:
            for operand_year in request["years"]:
                for report_year in (operand_year, operand_year + 1):
                    for path in report_paths.get((request["entity"].casefold(), report_year), []):
                        for asset in assets(path):
                            if expected_scope and asset.scope != expected_scope:
                                continue
                            if str((asset.table_function or {}).get("kind") or "") not in request["functions"]:
                                continue
                            if str((asset.table_function or {}).get("specificity") or "") != "structural":
                                continue
                            existing = formula_matches.get(asset.internal_table_uid)
                            ids = set() if existing is None else set(existing[2])
                            ids.add(request["operand_id"])
                            formula_matches[asset.internal_table_uid] = (asset, path, ids)
        ordered_formula = sorted(formula_matches.values(), key=lambda value: (value[0].ticker, value[0].scope, int(value[0].report_year or 0), value[0].document_id, value[0].local_ordinal, value[0].internal_table_uid))
        for asset, path, operand_ids in ordered_formula[: args.max_formula_support_tables]:
            collect(asset, path, "formula_metadata_support", {"policy": FORMULA_POLICY, "question_id": qid, "append": {"formula_ids": [str(formula.get("formula_id") or "")], "operand_ids": sorted(operand_ids)}})
        if len(ordered_formula) > args.max_formula_support_tables:
            skipped["formula_per_question_cap"] += len(ordered_formula) - args.max_formula_support_tables
        if position % 10 == 0:
            print(f"Scanned {position}/{len(source_items)} questions; raw support UIDs={len(candidate_locators)}", flush=True)

    new_tables: list[dict[str, Any]] = []
    new_contexts: list[dict[str, Any]] = []
    for uid in sorted(candidate_locators):
        path, ordinal = candidate_locators[uid]
        asset = next(
            (value for value in assets(path) if value.internal_table_uid == uid and int(value.local_ordinal) == ordinal),
            None,
        )
        if asset is None:
            raise ValueError(f"raw support UID/ordinal disappeared during materialization: {uid}")
        try:
            table, context = materialize(asset, path, candidate_inclusions[uid])
        except ValueError as exc:
            skipped[str(exc)] += 1
            continue
        new_tables.append(table)
        new_contexts.append(context)
    recover_continuation_headers(new_contexts)

    output.mkdir(parents=True)
    for name in ("review_items.jsonl", "errors.jsonl"):
        shutil.copy2(source / name, output / name)
    shutil.copy2(source_tables_path, output / "tables.jsonl")
    append_jsonl(output / "tables.jsonl", new_tables)
    shutil.copy2(structure_path, output / "tables_structured_v2.jsonl")
    append_jsonl(output / "tables_structured_v2.jsonl", new_tables)
    new_context_path = output / f"tables_evidence_context_v{EVIDENCE_CONTEXT_VERSION}.jsonl"
    shutil.copy2(context_path, new_context_path)
    append_jsonl(new_context_path, new_contexts)

    tables_hash = sha256_file(output / "tables.jsonl")
    structure_hash = sha256_file(output / "tables_structured_v2.jsonl")
    context_hash = sha256_file(new_context_path)
    structure_manifest = {
        "structure_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_bundle_tables_sha256": tables_hash,
        "table_count": source_table_count + len(new_tables),
        "repaired_table_count": source_table_count + len(new_tables),
        "error_count": 0,
        "sidecar_sha256": structure_hash,
        "materialized_from": {"bundle_tables_sha256": sha256_file(source / "tables.jsonl"), "new_raw_table_count": len(new_tables)},
    }
    write_json(output / "table_structure_v2.manifest.json", structure_manifest)
    context_manifest = {
        "evidence_context_version": EVIDENCE_CONTEXT_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_bundle_tables_sha256": tables_hash,
        "input_structure_path": str(output / "tables_structured_v2.jsonl"),
        "input_structure_sha256": structure_hash,
        "table_count": source_table_count + len(new_tables),
        "context_count": source_context_count + len(new_contexts),
        "error_count": 0,
        "numeric_binding_policy": "one_reliable_raw_v2_number_per_cell",
        "continuation_header_recovery": {"materialized_support_only": len(new_contexts)},
        "sidecar_sha256": context_hash,
        "materialized_from": {"bundle_context_file": context_path.name, "bundle_context_sha256": sha256_file(context_path), "new_raw_context_count": len(new_contexts)},
    }
    write_json(evidence_context_manifest_path(new_context_path), context_manifest)

    parent_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    output_manifest = {
        **parent_manifest,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "unique_table_count": source_table_count + len(new_tables),
        "metadata_support_materialization": {
            "protocol": "raw_metadata_support_bundle_v1",
            "parent_bundle_tables_sha256": sha256_file(source / "tables.jsonl"),
            "reports_root": str(reports_root),
            "formula_policy": FORMULA_POLICY,
            "direct_policy": DIRECT_POLICY,
            "new_raw_table_count": len(new_tables),
            "skipped": dict(sorted(skipped.items())),
            "answer_eligible": False,
            "training_eligible": False,
            "ui_candidate_effect": "not_added_to_review_candidates",
        },
    }
    write_json(output / "manifest.json", output_manifest)
    checksum_names = ["manifest.json", "review_items.jsonl", "tables.jsonl", "errors.jsonl", "tables_structured_v2.jsonl", new_context_path.name]
    (output / "SHA256SUMS").write_text("".join(f"{sha256_file(output / name)}  {name}\n" for name in checksum_names), encoding="utf-8")
    materialization_manifest = {
        "protocol": "raw_metadata_support_bundle_v1",
        "input_bundle": str(source),
        "output_bundle": str(output),
        "new_raw_table_count": len(new_tables),
        "new_raw_context_count": len(new_contexts),
        "bundle_tables_sha256": tables_hash,
        "structure_sha256": structure_hash,
        "context_sha256": context_hash,
        "skipped": dict(sorted(skipped.items())),
    }
    write_json(output / "metadata_support_materialization.manifest.json", materialization_manifest)
    validate_structure_sidecar(output, output / "tables_structured_v2.jsonl")
    validate_evidence_context_sidecar(output, output / "tables_structured_v2.jsonl", new_context_path)
    print(json.dumps(materialization_manifest, ensure_ascii=False, indent=2))
    print("No corpus, lexical index, dense index, review labels, or answers were rebuilt/changed.")


if __name__ == "__main__":
    main()
