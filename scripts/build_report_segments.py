#!/usr/bin/env python3
"""Build a hash-bound, non-numeric report-segment sidecar for a review bundle."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.report_segments import (  # noqa: E402
    NORMALIZATION_POLICY,
    SEGMENT_VERSION,
    build_report_segment,
)
from finance_query.table_structure import sha256_file, validate_structure_sidecar  # noqa: E402


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def rows(path: Path):
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def main() -> None:
    parsed = args()
    bundle = parsed.bundle_dir.resolve()
    output = parsed.output.resolve()
    context_path = (parsed.evidence_context or bundle / "tables_evidence_context_v3.jsonl").resolve()
    if output.parent != bundle or context_path.parent != bundle:
        raise ValueError("segment output and evidence context must reside directly in bundle-dir")
    structure = bundle / "tables_structured_v2.jsonl"
    validate_structure_sidecar(bundle, structure)
    validate_evidence_context_sidecar(bundle, structure, context_path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    heading_counts: Counter[str] = Counter()
    uid_count = 0
    seen: set[str] = set()
    with temporary.open("w", encoding="utf-8") as handle:
        for base, raw_structure, context in zip(
            rows(bundle / "tables.jsonl"), rows(structure), rows(context_path), strict=True
        ):
            uid = str(base.get("internal_table_uid") or "")
            if not uid or uid != str(raw_structure.get("internal_table_uid") or "") or uid != str(context.get("internal_table_uid") or ""):
                raise ValueError("bundle/V2/V3 UID order or identity differs")
            if uid in seen:
                raise ValueError("segment UIDs are not unique")
            seen.add(uid)
            row = build_report_segment({**base, **raw_structure}, context)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            heading_counts[row["source_heading_kind"]] += 1
            uid_count += 1
        handle.flush(); os.fsync(handle.fileno())
    temporary.replace(output)
    manifest = {
        "schema_version": SEGMENT_VERSION,
        "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(structure),
        "evidence_context_file": context_path.name,
        "evidence_context_sha256": sha256_file(context_path),
        "segment_count": uid_count,
        "heading_kind_counts": dict(heading_counts),
        "normalization_policy": NORMALIZATION_POLICY,
        "evidence_eligible": False,
        "training_eligible": False,
        "sidecar_sha256": sha256_file(output),
    }
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
