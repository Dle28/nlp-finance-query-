#!/usr/bin/env python3
"""Materialize controlled-formula EvidenceSets from an immutable review bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.financial_metrics import infer_formula_spec  # noqa: E402
from finance_query.formula_evidence import (  # noqa: E402
    FORMULA_SOURCE_DISCOVERY_POLICY,
    attach_resolved_single_entity,
    formula_evidence_set,
    source_discovery_candidates,
)
from finance_query.plan_overrides import (  # noqa: E402
    EXACT_QUERY_TICKER_TOKEN_POLICY,
    exact_source_ticker_tokens,
)
from finance_query.report_entities import (  # noqa: E402
    FORMULA_ENTITY_RESOLUTION_POLICY,
    REPORT_ENTITY_RESOLUTION_POLICY,
    resolve_question_entity,
    validate_report_entity_alias_sidecar,
)
from finance_query.source_completion import (  # noqa: E402
    source_completion_manifest_path,
    validate_source_completion_sidecar,
)
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--evidence-context",
        type=Path,
        default=None,
        help="Canonical context sidecar; defaults to tables_evidence_context_v3.jsonl in the bundle.",
    )
    parser.add_argument(
        "--report-entity-aliases",
        type=Path,
        default=None,
        help=(
            "Optional source-title-only entity-alias sidecar. It can fill a missing "
            "formula-plan ticker only when one exact source entity maps to one ticker; "
            "it never supplies a scope, row, cell, answer, or label."
        ),
    )
    parser.add_argument(
        "--source-completion-tables",
        type=Path,
        default=None,
        help=(
            "Optional revalidated raw-source completion sidecar. It is used only "
            "for formula EvidenceSet coverage and remains answer/training-ineligible."
        ),
    )
    parser.add_argument(
        "--source-completion-context",
        type=Path,
        default=None,
        help="Canonical V3 context sidecar paired with --source-completion-tables.",
    )
    parser.add_argument("--max-matches-per-operand", type=int, default=12)
    parser.add_argument(
        "--discover-source-operands",
        action="store_true",
        help=(
            "Expand formula EvidenceSet candidates through immutable tables.jsonl "
            "metadata for resolved ticker(s) and operand years. It never produces answers or labels."
        ),
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    if args.max_matches_per_operand < 1:
        raise ValueError("max-matches-per-operand must be positive")
    bundle = args.bundle_dir.resolve()
    structured = bundle / "tables_structured_v2.jsonl"
    contexts_path = (args.evidence_context or bundle / "tables_evidence_context_v3.jsonl").resolve()
    if contexts_path.parent != bundle:
        raise ValueError("Formula context sidecar must reside in the review bundle")
    validate_structure_sidecar(bundle, structured)
    validate_evidence_context_sidecar(bundle, structured, contexts_path)
    if bool(args.source_completion_tables) != bool(args.source_completion_context):
        raise ValueError(
            "--source-completion-tables and --source-completion-context must be supplied together"
        )
    items = load_jsonl(bundle / "review_items.jsonl")
    source_tables = {
        str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables.jsonl")
    }
    known_source_tickers = {
        str(table.get("ticker") or "").strip()
        for table in source_tables.values()
        if str(table.get("ticker") or "").strip()
    }
    structured_tables = {
        str(row["internal_table_uid"]): row for row in load_jsonl(structured)
    }
    if set(source_tables) != set(structured_tables):
        raise ValueError("V2 structured sidecar UID set differs from immutable bundle tables")
    # V2 gives the immutable raw grid/provenance. tables.jsonl contributes only
    # existing document metadata (ticker, scope, report year) for the optional
    # source-discovery join; no OCR content is replaced or inferred.
    tables = {
        uid: {**source_tables[uid], **structured_table}
        for uid, structured_table in structured_tables.items()
    }
    contexts = {str(row["internal_table_uid"]): row for row in load_jsonl(contexts_path)}
    requested_entity_aliases = args.report_entity_aliases
    default_entity_aliases = bundle / "report_entity_aliases_v1.jsonl"
    entity_aliases_path = (
        (requested_entity_aliases or default_entity_aliases).resolve()
        if requested_entity_aliases is not None or default_entity_aliases.is_file()
        else None
    )
    entity_aliases: list[dict[str, Any]] = []
    entity_alias_manifest: dict[str, Any] | None = None
    if entity_aliases_path is not None:
        if entity_aliases_path.parent != bundle:
            raise ValueError("Report-entity aliases must reside directly in the review bundle")
        entity_alias_manifest = validate_report_entity_alias_sidecar(bundle, entity_aliases_path)
        entity_aliases = load_jsonl(entity_aliases_path)
    entity_resolution_enabled = bool(args.discover_source_operands)
    source_completion_manifest: dict[str, Any] | None = None
    if args.source_completion_tables is not None:
        completion_tables_path = args.source_completion_tables.resolve()
        completion_context_path = args.source_completion_context.resolve()
        if completion_tables_path.parent != bundle or completion_context_path.parent != bundle:
            raise ValueError("Source-completion sidecars must reside directly in the review bundle")
        source_completion_manifest = validate_source_completion_sidecar(
            bundle, completion_tables_path, completion_context_path
        )
        completion_tables = {
            str(row["internal_table_uid"]): row
            for row in load_jsonl(completion_tables_path)
        }
        completion_contexts = {
            str(row["internal_table_uid"]): row
            for row in load_jsonl(completion_context_path)
        }
        if set(completion_tables) & set(tables) or set(completion_contexts) & set(contexts):
            raise ValueError("Source-completion UID overlaps immutable V2 context")
        tables.update(completion_tables)
        contexts.update(completion_contexts)
    evidence = []
    source_discovery_candidate_count = 0
    source_title_resolution_count = 0
    source_ticker_resolution_count = 0
    for item in items:
        formula = infer_formula_spec(str(item.get("question") or ""))
        if formula is None:
            continue
        candidate_item = item
        plan = item.get("question_plan") or {}
        entity_resolution = None
        if not (plan.get("tickers") or []) and entity_resolution_enabled:
            entity_resolution = (
                resolve_question_entity(item.get("question"), entity_aliases)
                if entity_aliases
                else None
            )
            if entity_resolution is not None:
                source_title_resolution_count += 1
            else:
                ticker_tokens = exact_source_ticker_tokens(
                    str(item.get("question") or ""), known_source_tickers
                )
                if len(ticker_tokens) == 1:
                    ticker = ticker_tokens[0]
                    entity_resolution = {
                        "policy": EXACT_QUERY_TICKER_TOKEN_POLICY,
                        "ticker": ticker,
                        "matched_source_ticker_tokens": [ticker],
                        "known_source_ticker_count": len(known_source_tickers),
                        "scope_inferred": False,
                    }
                    source_ticker_resolution_count += 1
            if entity_resolution is not None:
                candidate_item = {
                    **item,
                    "question_plan": {**plan, "tickers": [entity_resolution["ticker"]]},
                    "_formula_source_entity_resolution": entity_resolution,
                }
                formula = attach_resolved_single_entity(formula, entity_resolution)
        if args.discover_source_operands:
            discovered = source_discovery_candidates(candidate_item, formula, tables)
            source_discovery_candidate_count += len(discovered)
            candidate_item = {
                # Keep a source-title-resolved ticker when one was safely
                # attached above.  Re-expanding from ``item`` here would
                # silently discard that metadata and make the exact-row gate
                # reject every discovered candidate as entity-unresolved.
                **candidate_item,
                "candidates": [*(candidate_item.get("candidates") or []), *discovered],
                "_formula_source_discovery": {
                    "enabled": True,
                    "policy": FORMULA_SOURCE_DISCOVERY_POLICY,
                    "candidate_count": len(discovered),
                    "entity_resolution": entity_resolution,
                },
            }
        evidence.append(
            formula_evidence_set(
                formula,
                candidate_item,
                tables,
                contexts,
                max_matches_per_operand=args.max_matches_per_operand,
            )
        )
    output = args.output.resolve()
    write_jsonl(output, evidence)
    source_resolution_configured = bool(
        args.discover_source_operands
        and (entity_alias_manifest is not None or source_ticker_resolution_count)
    )
    manifest = {
        "schema_version": (
            6
            if source_resolution_configured
            else 4 if source_completion_manifest is not None else 3 if args.discover_source_operands else 2
        ),
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(structured),
        "evidence_context_sha256": sha256_file(contexts_path),
        "evidence_context_file": contexts_path.name,
        "evidence_set_count": len(evidence),
        "numeric_binding_policy": "one_reliable_raw_v2_number_per_operand",
        "source_discovery": {
            "enabled": bool(args.discover_source_operands),
            "policy": FORMULA_SOURCE_DISCOVERY_POLICY if args.discover_source_operands else None,
            "candidate_count": source_discovery_candidate_count,
            "source_metadata": "immutable_tables_jsonl_uid_join",
        },
        "source_entity_resolution": (
            {
                "enabled": True,
                "policy": FORMULA_ENTITY_RESOLUTION_POLICY,
                "resolution_count": source_title_resolution_count + source_ticker_resolution_count,
                "scope_inference": False,
                "evidence_eligible": False,
                "training_eligible": False,
                "title_aliases": (
                    {
                        "enabled": True,
                        "policy": REPORT_ENTITY_RESOLUTION_POLICY,
                        "aliases_file": entity_aliases_path.name,
                        "aliases_sha256": sha256_file(entity_aliases_path),
                        "alias_manifest_sha256": sha256_file(
                            entity_aliases_path.with_suffix(".manifest.json")
                        ),
                        "resolution_count": source_title_resolution_count,
                    }
                    if entity_alias_manifest is not None
                    else {"enabled": False}
                ),
                "exact_ticker_tokens": {
                    "enabled": True,
                    "policy": EXACT_QUERY_TICKER_TOKEN_POLICY,
                    "known_source_ticker_count": len(known_source_tickers),
                    "resolution_count": source_ticker_resolution_count,
                },
            }
            if source_resolution_configured
            else {"enabled": False}
        ),
        "source_completion": (
            {
                "enabled": True,
                "protocol": str(source_completion_manifest.get("protocol") or ""),
                "tables_file": completion_tables_path.name,
                "contexts_file": completion_context_path.name,
                "tables_sha256": sha256_file(completion_tables_path),
                "contexts_sha256": sha256_file(completion_context_path),
                "manifest_sha256": sha256_file(
                    source_completion_manifest_path(completion_tables_path)
                ),
                "answer_eligible": False,
                "training_eligible": False,
            }
            if source_completion_manifest is not None
            else {"enabled": False}
        ),
        "completeness_counts": dict(Counter(row["evidence_completeness"] for row in evidence)),
        "sidecar_sha256": sha256_file(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "manifest": str(manifest_path),
                **manifest["completeness_counts"],
                "source_discovery_candidates": source_discovery_candidate_count,
                "source_title_entity_resolutions": source_title_resolution_count,
                "source_exact_ticker_resolutions": source_ticker_resolution_count,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
