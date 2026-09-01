#!/usr/bin/env python3
"""Fail-closed validation for the research-only document corpus Round 2 artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.research.document_corpus_study import sha256_file  # noqa: E402


EXPECTED_HYPOTHESES = {
    *{f"A{value:02d}_" for value in range(1, 9)},
    *{f"B{value:02d}_" for value in range(1, 17)},
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc


def validate_manifest(directory: Path) -> list[str]:
    errors: list[str] = []
    manifest = read_json(directory / "manifest.json")
    contract = manifest.get("source_contract") or {}
    expected_contract = {
        "research_only": True,
        "production_index_replacement_allowed": False,
        "submission_eligible": False,
    }
    for key, expected in expected_contract.items():
        if contract.get(key) is not expected:
            errors.append(f"manifest contract mismatch: {key}")
    for section in ("inputs", "outputs"):
        for name, entry in manifest.get(section, {}).items():
            path_value = entry.get("path")
            path = Path(path_value) if path_value else directory / name
            if not path.exists():
                errors.append(f"missing {section} file: {path}")
            elif sha256_file(path) != entry.get("sha256"):
                errors.append(f"SHA mismatch: {path}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    args = parser.parse_args()
    errors = validate_manifest(args.build_dir) + validate_manifest(args.analysis_dir)

    receipt = read_json(args.build_dir / "build_receipt_v1.json")
    build = receipt.get("build") or {}
    if (build.get("report_count"), build.get("table_count"), build.get("failure_count")) != (1973, 146246, 0):
        errors.append("full build population is not 1973 documents / 146246 tables / 0 failures")
    if not receipt.get("deterministic_replay_match"):
        errors.append("deterministic replay did not match")
    if not (receipt.get("old_partial_prefix_detection") or {}).get("pass"):
        errors.append("old prefix truncation was not rejected")
    if not (receipt.get("atomic_interruption_probe") or {}).get("pass"):
        errors.append("atomic interruption probe failed")

    assets = args.build_dir / "full_table_assets_v1.jsonl"
    asset_lines = sum(1 for _ in load_jsonl(assets))
    if asset_lines != 146246:
        errors.append(f"asset JSONL count mismatch: {asset_lines}")

    analysis = read_json(args.analysis_dir / "asset_analysis_v1.json")
    if analysis.get("table_count") != 146246:
        errors.append("analysis table count mismatch")
    if analysis.get("question_materialized") is not False or analysis.get("submission_eligible") is not False:
        errors.append("analysis research-only gates are missing")

    index_path = args.analysis_dir / "full_lexical_ablation_v1.sqlite"
    with sqlite3.connect(index_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        table_count = connection.execute("SELECT count(*) FROM table_only_fts").fetchone()[0]
        combined_count = connection.execute("SELECT count(*) FROM combined_fts").fetchone()[0]
    if integrity != "ok":
        errors.append(f"SQLite integrity check failed: {integrity}")
    if (table_count, combined_count) != (146246, 146246):
        errors.append(f"lexical parity mismatch: {table_count}, {combined_count}")

    fidelity_rows = list(load_jsonl(args.analysis_dir / "numeric_fidelity_samples_v1.jsonl"))
    if len(fidelity_rows) != 3000:
        errors.append(f"numeric fidelity sample count mismatch: {len(fidelity_rows)}")
    for index, row in enumerate(fidelity_rows):
        if not all(row.get(key) is True for key in ("source_sha256_match", "table_sha256_match", "uid_match", "line_locator_match")):
            errors.append(f"fidelity identity failure at sample {index}")
            break
        if "answer" in row or "pandas_query" in row or row.get("question_materialized") is not False:
            errors.append(f"forbidden answer material in fidelity sample {index}")
            break

    query_rows = list(load_jsonl(args.analysis_dir / "retrieval_queries_v1.jsonl"))
    result_rows = list(load_jsonl(args.analysis_dir / "retrieval_results_v1.jsonl"))
    if len(query_rows) != 900 or len(result_rows) != 900:
        errors.append(f"retrieval population mismatch: queries={len(query_rows)}, results={len(result_rows)}")
    if any(row.get("question_materialized") is not False or "answer" in row for row in [*query_rows, *result_rows]):
        errors.append("retrieval artifacts contain materialized questions/answers")

    hypotheses = read_json(args.analysis_dir / "hypothesis_results_v1.json")
    ids = {str(row.get("id")) for row in hypotheses}
    if len(hypotheses) != 24:
        errors.append(f"hypothesis result count mismatch: {len(hypotheses)}")
    for prefix in EXPECTED_HYPOTHESES:
        if not any(value.startswith(prefix) for value in ids):
            errors.append(f"missing hypothesis prefix: {prefix}")
    if any(row.get("status") not in {"SUPPORTED", "REJECTED", "SUPPORTED_WITH_LIMIT", "GUARDRAIL_PASS"} for row in hypotheses):
        errors.append("unknown hypothesis status")

    report = args.analysis_dir / "document_corpus_round2_report_vi.md"
    if not report.exists() or report.stat().st_size < 3000:
        errors.append("Vietnamese report is missing or unexpectedly short")

    result = {
        "status": "VALID" if not errors else "INVALID",
        "error_count": len(errors),
        "errors": errors,
        "checks": {
            "documents": build.get("report_count"),
            "assets": asset_lines,
            "fidelity_samples": len(fidelity_rows),
            "retrieval_queries": len(query_rows),
            "hypotheses": len(hypotheses),
            "sqlite_integrity": integrity,
            "research_only": True,
            "submission_eligible": False,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
