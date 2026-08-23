#!/usr/bin/env python3
"""Materialize fail-closed typed operand plans for every review question."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.artifact_registry import sha256_file  # noqa: E402
from finance_query.report_entities import (  # noqa: E402
    validate_report_entity_alias_sidecar,
)
from finance_query.typed_planner import (  # noqa: E402
    TYPED_OPERAND_PLAN_PROTOCOL,
    TYPED_OPERAND_PLAN_SCHEMA_VERSION,
    build_typed_operand_plan,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _atomic_json(path: Path, payload: Any, *, jsonl: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        if jsonl:
            for row in payload:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        else:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def build(
    bundle: Path,
    output: Path,
    *,
    report_entity_aliases: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    review_items = bundle / "review_items.jsonl"
    items = _load_jsonl(review_items)
    ids = [int(item["id"]) for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("review_items.jsonl contains duplicate question ids")
    aliases: list[dict[str, Any]] = []
    aliases_path = report_entity_aliases
    if aliases_path is None:
        candidate = bundle / "report_entity_aliases_v1.jsonl"
        aliases_path = candidate if candidate.is_file() else None
    if aliases_path is not None:
        aliases_path = aliases_path.resolve()
        validate_report_entity_alias_sidecar(bundle, aliases_path)
        aliases = _load_jsonl(aliases_path)
    rows = [
        build_typed_operand_plan(item, report_entity_aliases=aliases)
        for item in items
    ]
    if {int(row["question_id"]) for row in rows} != set(ids):
        raise ValueError("Typed operand plan coverage differs from review_items")
    _atomic_json(output, rows, jsonl=True)
    manifest = {
        "schema_version": TYPED_OPERAND_PLAN_SCHEMA_VERSION,
        "protocol": TYPED_OPERAND_PLAN_PROTOCOL,
        "question_count": len(rows),
        "status_counts": dict(sorted(Counter(row["decomposition_status"] for row in rows).items())),
        "route_counts": dict(sorted(Counter(row["route"] for row in rows).items())),
        "review_items_sha256": sha256_file(review_items),
        "sidecar_sha256": sha256_file(output),
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "provenance_promotion_allowed": False,
        "source_title_entity_resolution": (
            {
                "enabled": True,
                "aliases_file": aliases_path.name,
                "aliases_sha256": sha256_file(aliases_path),
                "alias_manifest_sha256": sha256_file(aliases_path.with_suffix(".manifest.json")),
                "resolution_count": sum(
                    row.get("source_title_entity_resolution") is not None for row in rows
                ),
            }
            if aliases_path is not None
            else {"enabled": False, "resolution_count": 0}
        ),
    }
    _atomic_json(output.with_suffix(".manifest.json"), manifest)
    return rows, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--report-entity-aliases",
        type=Path,
        default=None,
        help="Optional source-title alias sidecar; defaults to the bundle V1 sidecar when present.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, manifest = build(
        args.bundle_dir.resolve(),
        args.output.resolve(),
        report_entity_aliases=(
            args.report_entity_aliases.resolve()
            if args.report_entity_aliases is not None
            else None
        ),
    )
    print(json.dumps({"rows": len(rows), **manifest["status_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
