#!/usr/bin/env python3
"""Build a source-title-only issuer-alias sidecar for Formula EvidenceSets."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.report_entities import (  # noqa: E402
    REPORT_ENTITY_POLICY,
    REPORT_ENTITY_VERSION,
    build_report_entity_aliases,
)
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    args = parse_args()
    bundle, output = args.bundle_dir.resolve(), args.output.resolve()
    if output.parent != bundle:
        raise ValueError("Report-entity output must reside directly in bundle-dir")
    aliases = build_report_entity_aliases(load_jsonl(bundle / "tables.jsonl"))
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for alias in aliases:
            handle.write(json.dumps(alias, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(output)
    manifest = {
        "schema_version": REPORT_ENTITY_VERSION,
        "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "alias_count": len(aliases),
        "normalization_policy": REPORT_ENTITY_POLICY,
        "evidence_eligible": False,
        "training_eligible": False,
        "sidecar_sha256": sha256_file(output),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
