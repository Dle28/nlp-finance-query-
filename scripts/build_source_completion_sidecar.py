#!/usr/bin/env python3
"""Materialize revalidated raw tables omitted from an immutable review bundle.

This creates a supplemental, provenance-bound sidecar for formula coverage
only.  It does not change `tables.jsonl`, the corpus/index, review statuses,
answers, execution ledger, or training labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.source_completion import (  # noqa: E402
    SOURCE_COMPLETION_PROTOCOL,
    revalidate_raw_source_candidate,
    sha256_file,
    source_completion_manifest_path,
    validate_source_completion_sidecar,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=ROOT / "data/ViFinQA/financial_statements",
    )
    parser.add_argument("--tables-output", type=Path, default=None)
    parser.add_argument("--contexts-output", type=Path, default=None)
    parser.add_argument(
        "--base-tables",
        type=Path,
        default=None,
        help=(
            "Existing validated source-completion table sidecar to retain in a "
            "new combined shadow snapshot. Must be paired with --base-contexts."
        ),
    )
    parser.add_argument(
        "--base-contexts",
        type=Path,
        default=None,
        help="Canonical V3 context sidecar paired with --base-tables.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _completion_table_identity(table: dict[str, Any]) -> str:
    """Hash raw table identity while excluding accumulated audit origins."""
    stable = dict(table)
    completion = dict(stable.get("source_completion") or {})
    completion.pop("origins", None)
    stable["source_completion"] = completion
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalized_origins(origins: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, str, str]] = set()
    output: list[dict[str, Any]] = []
    for origin in origins:
        normalized = {
            "question_id": int(origin["question_id"]),
            "formula_id": str(origin.get("formula_id") or ""),
            "operand_id": str(origin.get("operand_id") or ""),
        }
        key = (
            normalized["question_id"],
            normalized["formula_id"],
            normalized["operand_id"],
        )
        if key not in seen:
            seen.add(key)
            output.append(normalized)
    return sorted(
        output,
        key=lambda row: (row["question_id"], row["formula_id"], row["operand_id"]),
    )


def merge_completion_rows(
    base_tables: Iterable[dict[str, Any]],
    base_contexts: Iterable[dict[str, Any]],
    new_tables: Iterable[dict[str, Any]],
    new_contexts: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Union two raw-source snapshots without weakening source provenance.

    A repeated UID is permitted only when its raw grid/provenance and rebuilt
    V3 context are byte-for-byte equivalent apart from the append-only list of
    audit origins.  This makes a second audit pass additive rather than an
    accidental replacement of previously revalidated source tables.
    """
    def maps(
        tables: list[dict[str, Any]], contexts: list[dict[str, Any]], label: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        table_by_uid = {str(row.get("internal_table_uid") or ""): dict(row) for row in tables}
        context_by_uid = {str(row.get("internal_table_uid") or ""): dict(row) for row in contexts}
        if "" in table_by_uid or len(table_by_uid) != len(tables):
            raise ValueError(f"{label} source-completion tables have duplicate or empty UIDs")
        if "" in context_by_uid or len(context_by_uid) != len(contexts):
            raise ValueError(f"{label} source-completion contexts have duplicate or empty UIDs")
        if set(table_by_uid) != set(context_by_uid):
            raise ValueError(f"{label} source-completion table/context UID sets differ")
        return table_by_uid, context_by_uid

    # Convert once so the UID cardinality checks above cannot consume a
    # generator before it is merged.
    base_table_rows, base_context_rows = list(base_tables), list(base_contexts)
    new_table_rows, new_context_rows = list(new_tables), list(new_contexts)
    base_by_uid, base_context_by_uid = maps(base_table_rows, base_context_rows, "base")
    new_by_uid, new_context_by_uid = maps(new_table_rows, new_context_rows, "new")

    merged_tables = dict(base_by_uid)
    merged_contexts = dict(base_context_by_uid)
    for uid, table in new_by_uid.items():
        if uid not in merged_tables:
            merged_tables[uid] = table
            merged_contexts[uid] = new_context_by_uid[uid]
            continue
        previous = merged_tables[uid]
        if _completion_table_identity(previous) != _completion_table_identity(table):
            raise ValueError("Repeated source-completion UID has different raw table provenance")
        if merged_contexts[uid] != new_context_by_uid[uid]:
            raise ValueError("Repeated source-completion UID has different canonical context")
        completion = dict(previous.get("source_completion") or {})
        completion["origins"] = _normalized_origins(
            [
                *(completion.get("origins") or []),
                *((table.get("source_completion") or {}).get("origins") or []),
            ]
        )
        previous["source_completion"] = completion
    ordered_uids = sorted(merged_tables)
    return ([merged_tables[uid] for uid in ordered_uids], [merged_contexts[uid] for uid in ordered_uids])


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    reports_root = args.reports_root.resolve()
    source_audit = args.source_audit.resolve()
    tables_output = (args.tables_output or bundle / "source_completion_tables_v1.jsonl").resolve()
    contexts_output = (args.contexts_output or bundle / "source_completion_context_v1.jsonl").resolve()
    manifest_output = source_completion_manifest_path(tables_output).resolve()
    if bool(args.base_tables) != bool(args.base_contexts):
        raise ValueError("--base-tables and --base-contexts must be supplied together")
    base_tables = args.base_tables.resolve() if args.base_tables is not None else None
    base_contexts = args.base_contexts.resolve() if args.base_contexts is not None else None
    if tables_output.parent != bundle or contexts_output.parent != bundle or manifest_output.parent != bundle:
        raise ValueError("Source-completion artifacts must reside directly in the review bundle")
    if base_tables is not None:
        if base_tables.parent != bundle or base_contexts is None or base_contexts.parent != bundle:
            raise ValueError("Base source-completion artifacts must reside directly in the review bundle")
        if tables_output == base_tables or contexts_output == base_contexts:
            raise ValueError("Combined source-completion outputs must not overwrite the base snapshot")
    if not (bundle / "tables.jsonl").is_file() or not source_audit.is_file():
        raise FileNotFoundError("Immutable bundle tables or source-completion audit is missing")
    if not reports_root.is_dir():
        raise NotADirectoryError(reports_root)

    audit = json.loads(source_audit.read_text(encoding="utf-8"))
    if str(audit.get("protocol") or "") != "raw_source_completion_audit_v1":
        raise ValueError("Source audit does not use raw_source_completion_audit_v1")
    if not bool(audit.get("audit_only")) or bool(audit.get("promotion_allowed")):
        raise ValueError("Source audit must be audit-only and promotion-disabled")
    if str(audit.get("bundle_tables_sha256") or "") != sha256_file(bundle / "tables.jsonl"):
        raise ValueError("Source audit belongs to a different immutable bundle")

    origins_by_uid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidates_by_uid: dict[str, dict[str, Any]] = {}
    for finding in audit.get("findings") or []:
        if str(finding.get("audit_finding") or "") != "raw_source_present_not_in_bundle":
            continue
        origin = {
            "question_id": int(finding["id"]),
            "formula_id": str(finding.get("formula_id") or ""),
            "operand_id": str(finding.get("operand_id") or ""),
        }
        for candidate in finding.get("raw_source_candidates") or []:
            if bool(candidate.get("already_in_immutable_bundle")):
                # A scope-gap finding may intentionally contain a bundled
                # sibling as audit context. Only the omitted raw table may be
                # materialized into the supplemental sidecar.
                continue
            uid = str(candidate.get("raw_table_uid") or "")
            if not uid:
                raise ValueError("Completion audit candidate has no raw table UID")
            previous = candidates_by_uid.setdefault(uid, dict(candidate))
            if (
                str(previous.get("raw_table_sha256") or "")
                != str(candidate.get("raw_table_sha256") or "")
            ):
                raise ValueError("One source completion UID has conflicting raw table hashes")
            origins_by_uid[uid].append(origin)

    new_tables: list[dict[str, Any]] = []
    new_contexts: list[dict[str, Any]] = []
    for uid in sorted(candidates_by_uid):
        table, context = revalidate_raw_source_candidate(
            candidates_by_uid[uid], reports_root=reports_root
        )
        table["source_completion"]["origins"] = sorted(
            origins_by_uid[uid],
            key=lambda row: (row["question_id"], row["formula_id"], row["operand_id"]),
        )
        new_tables.append(table)
        new_contexts.append(context)
    base_manifest: dict[str, Any] | None = None
    if base_tables is not None and base_contexts is not None:
        base_manifest = validate_source_completion_sidecar(bundle, base_tables, base_contexts)
        tables, contexts = merge_completion_rows(
            load_jsonl(base_tables),
            load_jsonl(base_contexts),
            new_tables,
            new_contexts,
        )
    else:
        tables, contexts = new_tables, new_contexts
    atomic_jsonl(tables_output, tables)
    atomic_jsonl(contexts_output, contexts)

    source_reports = {
        str(table["source_provenance"]["source_path"]): str(
            table["source_provenance"]["source_sha256"]
        )
        for table in tables
    }
    manifest = {
        "schema_version": 2 if base_manifest is not None else 1,
        "protocol": SOURCE_COMPLETION_PROTOCOL,
        "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "source_audit_sha256": sha256_file(source_audit),
        "reports_root": str(reports_root),
        "table_count": len(tables),
        "context_count": len(contexts),
        "source_reports": dict(sorted(source_reports.items())),
        "tables_sidecar_sha256": sha256_file(tables_output),
        "contexts_sidecar_sha256": sha256_file(contexts_output),
        "audit_only": False,
        "answer_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
    }
    if base_manifest is not None and base_tables is not None and base_contexts is not None:
        manifest["base_source_completion"] = {
            "tables_file": base_tables.name,
            "contexts_file": base_contexts.name,
            "tables_sha256": sha256_file(base_tables),
            "contexts_sha256": sha256_file(base_contexts),
            "manifest_sha256": sha256_file(source_completion_manifest_path(base_tables)),
            "protocol": str(base_manifest.get("protocol") or ""),
        }
    atomic_json(manifest_output, manifest)
    print(
        json.dumps(
            {
                "tables_output": str(tables_output),
                "contexts_output": str(contexts_output),
                "manifest": str(manifest_output),
                "table_count": len(tables),
                "answer_eligible": False,
                "training_eligible": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
