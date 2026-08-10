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


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    reports_root = args.reports_root.resolve()
    source_audit = args.source_audit.resolve()
    tables_output = (args.tables_output or bundle / "source_completion_tables_v1.jsonl").resolve()
    contexts_output = (args.contexts_output or bundle / "source_completion_context_v1.jsonl").resolve()
    manifest_output = source_completion_manifest_path(tables_output).resolve()
    if tables_output.parent != bundle or contexts_output.parent != bundle or manifest_output.parent != bundle:
        raise ValueError("Source-completion artifacts must reside directly in the review bundle")
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

    tables: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    for uid in sorted(candidates_by_uid):
        table, context = revalidate_raw_source_candidate(
            candidates_by_uid[uid], reports_root=reports_root
        )
        table["source_completion"]["origins"] = sorted(
            origins_by_uid[uid],
            key=lambda row: (row["question_id"], row["formula_id"], row["operand_id"]),
        )
        tables.append(table)
        contexts.append(context)
    atomic_jsonl(tables_output, tables)
    atomic_jsonl(contexts_output, contexts)

    source_reports = {
        str(table["source_provenance"]["source_path"]): str(
            table["source_provenance"]["source_sha256"]
        )
        for table in tables
    }
    manifest = {
        "schema_version": 1,
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
