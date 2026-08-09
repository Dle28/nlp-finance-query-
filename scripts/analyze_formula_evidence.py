#!/usr/bin/env python3
"""Audit a Formula EvidenceSet sidecar without calculating answers or labels.

The audit validates that every selected operand still points to the same raw
V2 cell, canonical source header and parseable numeric value. It then emits
coverage/bottleneck statistics for deciding what preprocessing or planner work
is warranted next.
"""

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

from finance_query.execution import parse_decimal  # noqa: E402
from finance_query.formula_evidence import FORMULA_SOURCE_DISCOVERY_POLICY  # noqa: E402
from finance_query.source_completion import (  # noqa: E402
    SOURCE_COMPLETION_PROTOCOL,
    validate_source_completion_sidecar,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def evidence_context_path(bundle: Path, manifest: dict[str, Any]) -> Path:
    name = str(manifest.get("evidence_context_file") or "tables_evidence_context_v1.jsonl")
    if Path(name).name != name:
        raise ValueError("Formula evidence context filename must be local to the bundle")
    return bundle / name


def validate_manifest(bundle: Path, sidecar: Path) -> dict[str, Any]:
    manifest_path = sidecar.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Formula evidence manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = int(manifest.get("schema_version") or 0)
    if version not in {2, 3, 4}:
        raise ValueError("Formula evidence audit requires schema_version=2, 3, or 4")
    expected = {
        "bundle_review_items_sha256": bundle / "review_items.jsonl",
        "structured_tables_sha256": bundle / "tables_structured_v2.jsonl",
        "evidence_context_sha256": evidence_context_path(bundle, manifest),
    }
    for key, path in expected.items():
        if str(manifest.get(key) or "") != sha256_file(path):
            raise ValueError(f"Formula evidence manifest does not match {path.name}")
    if version >= 3:
        if str(manifest.get("bundle_tables_sha256") or "") != sha256_file(bundle / "tables.jsonl"):
            raise ValueError("Formula source-discovery manifest does not match tables.jsonl")
        discovery = manifest.get("source_discovery") or {}
        if not bool(discovery.get("enabled")) or str(discovery.get("policy") or "") != FORMULA_SOURCE_DISCOVERY_POLICY:
            raise ValueError("Formula source-discovery manifest lacks the required policy")
    if version >= 4:
        source_completion_paths(bundle, manifest)
    if str(manifest.get("sidecar_sha256") or "") != sha256_file(sidecar):
        raise ValueError("Formula evidence sidecar hash does not match its manifest")
    if str(manifest.get("numeric_binding_policy") or "") != "one_reliable_raw_v2_number_per_operand":
        raise ValueError("Formula evidence sidecar does not declare the strict numeric binding policy")
    return manifest


def _bundle_local_path(bundle: Path, value: object, label: str) -> Path:
    name = str(value or "")
    if not name or Path(name).name != name:
        raise ValueError(f"Formula source-completion {label} must be a local bundle filename")
    return bundle / name


def source_completion_paths(bundle: Path, manifest: dict[str, Any]) -> tuple[Path, Path]:
    """Validate and return optional V4 raw-source supplemental sidecars."""
    completion = manifest.get("source_completion") or {}
    if not bool(completion.get("enabled")):
        raise ValueError("Formula evidence V4 manifest must enable source completion")
    if str(completion.get("protocol") or "") != SOURCE_COMPLETION_PROTOCOL:
        raise ValueError("Formula source-completion protocol is invalid")
    if bool(completion.get("answer_eligible")) or bool(completion.get("training_eligible")):
        raise ValueError("Formula source-completion cannot be answer/training eligible")
    tables_path = _bundle_local_path(bundle, completion.get("tables_file"), "tables file")
    contexts_path = _bundle_local_path(bundle, completion.get("contexts_file"), "contexts file")
    for key, path in {
        "tables_sha256": tables_path,
        "contexts_sha256": contexts_path,
    }.items():
        if str(completion.get(key) or "") != sha256_file(path):
            raise ValueError(f"Formula source-completion {key} does not match {path.name}")
    manifest_path = tables_path.with_name("source_completion_v1.manifest.json")
    if str(completion.get("manifest_sha256") or "") != sha256_file(manifest_path):
        raise ValueError("Formula source-completion manifest hash mismatch")
    validate_source_completion_sidecar(bundle, tables_path, contexts_path)
    return tables_path, contexts_path


def operand_matches(rows: Iterable[dict[str, Any]]) -> Iterable[tuple[int, str, dict[str, Any]]]:
    for row in rows:
        qid = int(row["id"])
        for operand_id, matches in (row.get("operand_matches") or {}).items():
            for match in matches or []:
                if isinstance(match, dict):
                    yield qid, str(operand_id), match


def _needed_uids(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(match.get("internal_table_uid") or "")
        for _qid, _operand_id, match in operand_matches(rows)
        if str(match.get("internal_table_uid") or "")
    }


def load_referenced_jsonl(path: Path, uids: set[str]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            uid = str(row.get("internal_table_uid") or "")
            if uid in uids:
                output[uid] = row
    missing = uids - set(output)
    if missing:
        raise ValueError(f"Referenced formula table missing from {path.name}: {sorted(missing)[:3]}")
    return output


def load_referenced_from_paths(paths: Iterable[Path], uids: set[str]) -> dict[str, dict[str, Any]]:
    """Load exact referenced rows from V2 plus optional source-completion files."""
    output: dict[str, dict[str, Any]] = {}
    for path in paths:
        with path.open(encoding="utf-8-sig") as file:
            for line in file:
                if not line.strip():
                    continue
                row = json.loads(line)
                uid = str(row.get("internal_table_uid") or "")
                if uid in uids:
                    if uid in output:
                        raise ValueError(f"Referenced formula table UID appears twice: {uid}")
                    output[uid] = row
    missing = uids - set(output)
    if missing:
        raise ValueError(f"Referenced formula table missing from sidecars: {sorted(missing)[:3]}")
    return output


def canonical_column_label(context: dict[str, Any], column_index: int) -> str:
    return str(
        next(
            (
                column.get("source_label")
                for column in (context.get("canonical_headers") or {}).get("columns") or []
                if int(column.get("column_index") or -1) == column_index
            ),
            "",
        )
        or ""
    )


def validate_operand_matches(
    rows: list[dict[str, Any]],
    bundle: Path,
    context_path: Path | None = None,
    source_completion_tables: Path | None = None,
    source_completion_context: Path | None = None,
) -> int:
    uids = _needed_uids(rows)
    if not uids:
        return 0
    if bool(source_completion_tables) != bool(source_completion_context):
        raise ValueError("Source-completion tables/context must be supplied together")
    table_paths = [bundle / "tables_structured_v2.jsonl"]
    context_paths = [context_path or bundle / "tables_evidence_context_v1.jsonl"]
    if source_completion_tables is not None:
        table_paths.append(source_completion_tables)
        context_paths.append(source_completion_context)  # type: ignore[arg-type]
    tables = load_referenced_from_paths(table_paths, uids)
    contexts = load_referenced_from_paths(
        context_paths, uids
    )
    checked = 0
    for qid, operand_id, match in operand_matches(rows):
        uid = str(match.get("internal_table_uid") or "")
        table, context = tables[uid], contexts[uid]
        binding = match.get("binding") or {}
        if str(binding.get("status") or "") != "cell_bound":
            raise ValueError(f"Q{qid}/{operand_id}: stored operand match is not cell_bound")
        row_index, column_index = binding.get("row_index"), binding.get("column_index")
        source_rows = table.get("rows") or []
        if not isinstance(row_index, int) or not isinstance(column_index, int):
            raise ValueError(f"Q{qid}/{operand_id}: source coordinates are invalid")
        if not 0 <= row_index < len(source_rows) or not 0 <= column_index < len(source_rows[row_index]):
            raise ValueError(f"Q{qid}/{operand_id}: source coordinates are out of bounds")
        raw_value = str(source_rows[row_index][column_index])
        if raw_value != str(binding.get("raw_value") or ""):
            raise ValueError(f"Q{qid}/{operand_id}: raw value differs from V2 source")
        if [str(value) for value in source_rows[row_index]] != [str(value) for value in match.get("source_row") or []]:
            raise ValueError(f"Q{qid}/{operand_id}: source row differs from V2 source")
        if canonical_column_label(context, column_index) != str(binding.get("column_label") or ""):
            raise ValueError(f"Q{qid}/{operand_id}: column label differs from canonical source")
        parsed = parse_decimal(raw_value)
        if parsed.value is None or any(warning != "percent_value_not_scaled" for warning in parsed.warnings):
            raise ValueError(f"Q{qid}/{operand_id}: stored source is not one reliable number")
        if str(binding.get("parsed_value") or "") != parsed.value:
            raise ValueError(f"Q{qid}/{operand_id}: parsed value differs from source parser")
        checked += 1
    return checked


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    candidate_gate_rejections: Counter[str] = Counter()
    for row in rows:
        candidate_gate_rejections.update(
            {
                str(reason): int(count)
                for reason, count in (row.get("candidate_gate_rejections") or {}).items()
            }
        )
    return {
        "evidence_set_count": len(rows),
        "completeness_counts": dict(Counter(str(row.get("evidence_completeness") or "") for row in rows)),
        "operand_coverage_counts": dict(Counter(str(row.get("operand_coverage_status") or "") for row in rows)),
        "formula_counts": dict(Counter(str((row.get("formula") or {}).get("formula_id") or "") for row in rows)),
        "reason_code_counts": dict(Counter(code for row in rows for code in (row.get("reason_codes") or []))),
        "candidate_gate_rejection_counts": dict(candidate_gate_rejections),
        "missing_operand_counts": dict(
            Counter(operand for row in rows for operand in (row.get("missing_operand_ids") or []))
        ),
        "fully_covered_but_not_complete_ids": [
            int(row["id"])
            for row in rows
            if not row.get("missing_operand_ids") and row.get("evidence_completeness") != "complete"
        ],
    }


def main() -> None:
    args = parse_args()
    bundle, sidecar = args.bundle_dir.resolve(), args.formula_evidence.resolve()
    manifest = validate_manifest(bundle, sidecar)
    rows = load_jsonl(sidecar)
    completion_tables, completion_context = (
        source_completion_paths(bundle, manifest)
        if int(manifest.get("schema_version") or 0) >= 4
        else (None, None)
    )
    checked = validate_operand_matches(
        rows,
        bundle,
        evidence_context_path(bundle, manifest),
        completion_tables,
        completion_context,
    )
    output = {
        "formula_evidence_manifest": manifest,
        "operand_binding_count_checked": checked,
        "summary": summarize(rows),
        "note": (
            "Audit only: it validates source coverage and numeric safety; it does not execute a formula, "
            "change a review status, or create a label."
        ),
    }
    write_json(args.output.resolve(), output)
    print(json.dumps({"output": str(args.output.resolve()), **output["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
