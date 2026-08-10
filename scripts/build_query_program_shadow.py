#!/usr/bin/env python3
"""Compile allow-listed Formula EvidenceSets into non-executable QueryPrograms."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from analyze_formula_evidence import validate_manifest  # noqa: E402
from finance_query.query_program import (  # noqa: E402
    QUERY_PROGRAM_PROTOCOL,
    QUERY_PROGRAM_SCHEMA_VERSION,
    QueryProgramError,
    compile_query_program,
    shadow_readiness,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
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
    evidence_path = args.formula_evidence.resolve()
    output = args.output.resolve()
    formula_manifest = validate_manifest(bundle, evidence_path)
    rows: list[dict[str, Any]] = []
    compile_counts: Counter[str] = Counter()
    for evidence_set in load_jsonl(evidence_path):
        formula = evidence_set.get("formula") or {}
        try:
            program = compile_query_program(formula)
        except QueryProgramError as error:
            rows.append(
                {
                    "id": int(evidence_set["id"]),
                    "formula_id": str(formula.get("formula_id") or ""),
                    "program": None,
                    "readiness": {
                        "status": "compile_blocked",
                        "reason_codes": ["query_program_compile_contract_failed"],
                        "detail": str(error),
                        "submission_eligible": False,
                    },
                    "execution_mode": "shadow_only",
                }
            )
            compile_counts["compile_blocked"] += 1
            continue
        readiness = shadow_readiness(evidence_set, program)
        rows.append(
            {
                "id": int(evidence_set["id"]),
                "formula_id": str(formula.get("formula_id") or ""),
                "program": None if program is None else program.to_dict(),
                "readiness": readiness,
                "execution_mode": "shadow_only",
            }
        )
        compile_counts[str(readiness["status"])] += 1
    atomic_jsonl(output, rows)
    manifest = {
        "schema_version": QUERY_PROGRAM_SCHEMA_VERSION,
        "protocol": QUERY_PROGRAM_PROTOCOL,
        "bundle_dir": bundle.name,
        "formula_evidence_file": evidence_path.name,
        "formula_evidence_sha256": sha256_file(evidence_path),
        "formula_evidence_manifest_sha256": sha256_file(evidence_path.with_suffix(".manifest.json")),
        "formula_evidence_schema_version": int(formula_manifest["schema_version"]),
        "program_count": sum(row["program"] is not None for row in rows),
        "readiness_counts": dict(sorted(compile_counts.items())),
        "execution_mode": "shadow_only",
        "submission_eligible": False,
        "sidecar_sha256": sha256_file(output),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), "manifest": str(output.with_suffix('.manifest.json')), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
