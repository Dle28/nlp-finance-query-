#!/usr/bin/env python3
"""Build a hash-bound plan-override sidecar for disclosed report rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.plan_overrides import (  # noqa: E402
    PLAN_OVERRIDE_SCHEMA_VERSION,
    source_ticker_direct_override,
    validate_plan_overrides,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
    bundle = args.bundle_dir.resolve()
    items_path = bundle / "review_items.jsonl"
    items = load_jsonl(items_path)
    tables_path = bundle / "tables.jsonl"
    known_tickers = {
        str(row.get("ticker") or "").strip()
        for row in load_jsonl(tables_path)
        if str(row.get("ticker") or "").strip()
    }
    overrides = [
        override
        for item in items
        if (override := source_ticker_direct_override(item, known_tickers))
    ]
    validate_plan_overrides(items, overrides)
    output = args.output.resolve()
    write_jsonl(output, overrides)
    manifest = {
        "schema_version": PLAN_OVERRIDE_SCHEMA_VERSION,
        "bundle_review_items_sha256": sha256_file(items_path),
        "bundle_tables_sha256": sha256_file(tables_path),
        "known_source_ticker_count": len(known_tickers),
        "ticker_resolution_override_count": sum(
            "exact_query_ticker_token_in_bundle_metadata_v1"
            in str(override.get("reason_code") or "")
            for override in overrides
        ),
        "override_count": len(overrides),
        "sidecar_sha256": sha256_file(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "manifest": str(manifest_path), "count": len(overrides)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
