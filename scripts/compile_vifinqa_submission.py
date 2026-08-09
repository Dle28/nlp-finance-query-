#!/usr/bin/env python3
"""Compile a full ViFinQA submission from a source-grounded execution ledger.

This is deliberately not an answer guesser.  Its input ledger must already
bind every operand to exact V2 raw-HTML rows and carry one of the allowed
provenance states.  The compiler materializes the cited tables as CSVs,
re-executes every deterministic operation, validates all 1,012 records against
the public contest contract, then writes a portable ``submission.zip``.

Ledger schema (one JSON object per line)::

  {
    "id": 1,
    "provenance_status": "machine_calibrated",
    "execution_status": "grounded",
    "grounding_status": "exact_rows_validated",
    "execution_mode": "direct_lookup" | "exact_formula",
    "operation_ast": {"op": "lookup", "args": ["x0"]},
    "normalize_operands_to_vnd": true,
    "operand_bindings": [
      {"operand_id": "x0", "internal_table_uid": "...", "binding": {...}}
    ]
  }

The table UID must occur in ``tables_structured_v2.jsonl``.  This prevents
legacy projected/OCR rows from masquerading as final submission evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.schemas import DirectBinding
from finance_query.submission import (  # noqa: E402
    SubmissionValidationError,
    validate_submission_directory,
    validate_submission_zip,
    write_direct_lookup_record,
    write_execution_record,
)
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


ALLOWED_PROVENANCE = {"human_verified", "machine_calibrated"}
REQUIRED_EXECUTION_STATUS = "grounded"
REQUIRED_GROUNDING_STATUS = "exact_rows_validated"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--execution-ledger", type=Path, required=True)
    parser.add_argument(
        "--questions",
        type=Path,
        default=ROOT / "data/ViFinQA/questions/questions.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New empty directory to receive submission.json and data/*.csv.",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="Defaults to <output-dir>.zip. Refuses to overwrite an existing file.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSONL: {path}:{line_number}") from error
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be an object: {path}:{line_number}")
            rows.append(row)
    return rows


def index_rows(rows: Iterable[dict[str, Any]], *, name: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        qid = row.get("id")
        if not isinstance(qid, int) or isinstance(qid, bool):
            raise ValueError(f"{name} row has non-integer id: {qid!r}")
        if qid in output:
            raise ValueError(f"Duplicate Q{qid} in {name}")
        output[qid] = row
    return output


def load_v2_tables(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    bundle_tables = {
        str(row["internal_table_uid"]): row
        for row in load_jsonl(bundle_dir / "tables.jsonl")
    }
    sidecar_path = bundle_dir / "tables_structured_v2.jsonl"
    if not sidecar_path.is_file():
        raise FileNotFoundError(
            f"Final submission requires V2 exact-row sidecar: {sidecar_path}. "
            "Run repair-tables before autonomous execution."
        )
    validate_structure_sidecar(bundle_dir, sidecar_path)
    output: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(sidecar_path):
        uid = str(row.get("internal_table_uid") or "")
        if uid not in bundle_tables:
            raise ValueError(f"V2 sidecar UID is absent from review bundle: {uid}")
        quality = row.get("structure_quality") or {}
        if str(quality.get("status") or "") != "reconstructed_from_raw_html":
            continue
        output[uid] = {**bundle_tables[uid], **row}
    return output


def validate_ledger_row(row: dict[str, Any]) -> None:
    qid = int(row["id"])
    provenance = str(row.get("provenance_status") or row.get("annotation_status") or "")
    if provenance not in ALLOWED_PROVENANCE:
        raise ValueError(
            f"Q{qid}: submission accepts only {sorted(ALLOWED_PROVENANCE)}, got {provenance!r}"
        )
    if str(row.get("execution_status") or "") != REQUIRED_EXECUTION_STATUS:
        raise ValueError(f"Q{qid}: execution_status must be {REQUIRED_EXECUTION_STATUS!r}")
    if str(row.get("grounding_status") or "") != REQUIRED_GROUNDING_STATUS:
        raise ValueError(f"Q{qid}: grounding_status must be {REQUIRED_GROUNDING_STATUS!r}")
    if str(row.get("formula_definition_status") or "confirmed") == "ambiguous":
        raise ValueError(f"Q{qid}: ambiguous formula cannot enter a submission")


def operand_pairs(
    row: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> dict[str, tuple[DirectBinding, dict[str, Any]]]:
    qid = int(row["id"])
    raw_bindings = row.get("operand_bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise ValueError(f"Q{qid}: operand_bindings must be a non-empty list")
    output: dict[str, tuple[DirectBinding, dict[str, Any]]] = {}
    for item in raw_bindings:
        if not isinstance(item, dict):
            raise ValueError(f"Q{qid}: operand binding must be an object")
        operand_id = item.get("operand_id")
        uid = str(item.get("internal_table_uid") or "")
        binding_payload = item.get("binding")
        if not isinstance(operand_id, str) or not operand_id:
            raise ValueError(f"Q{qid}: operand binding has no operand_id")
        if operand_id in output:
            raise ValueError(f"Q{qid}: duplicate operand binding {operand_id!r}")
        if uid not in tables:
            raise ValueError(f"Q{qid}: operand {operand_id!r} lacks V2 table {uid!r}")
        if not isinstance(binding_payload, dict):
            raise ValueError(f"Q{qid}: operand {operand_id!r} has no binding object")
        try:
            binding = DirectBinding(**binding_payload)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Q{qid}: invalid DirectBinding for {operand_id!r}") from error
        if binding.internal_table_uid != uid:
            raise ValueError(f"Q{qid}: binding/table UID mismatch for {operand_id!r}")
        output[operand_id] = (binding, tables[uid])
    return output


def compile_record(
    question: dict[str, Any], ledger_row: dict[str, Any], output_dir: Path, tables: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    qid = int(question["id"])
    validate_ledger_row(ledger_row)
    bindings = operand_pairs(ledger_row, tables)
    mode = str(ledger_row.get("execution_mode") or "")
    operation = ledger_row.get("operation_ast")
    if not isinstance(operation, dict):
        raise ValueError(f"Q{qid}: operation_ast must be an object")
    if mode == "direct_lookup":
        if len(bindings) != 1 or str(operation.get("op") or "") != "lookup":
            raise ValueError(f"Q{qid}: direct_lookup must have exactly one lookup operand")
        binding, asset = next(iter(bindings.values()))
        return write_direct_lookup_record(
            question_id=qid,
            question=str(question["question"]),
            binding=binding,
            asset=asset,
            package_root=output_dir,
        )
    if mode == "exact_formula":
        if not isinstance(ledger_row.get("normalize_operands_to_vnd"), bool):
            raise ValueError(f"Q{qid}: exact_formula must explicitly set normalize_operands_to_vnd")
        return write_execution_record(
            question_id=qid,
            question=str(question["question"]),
            operand_bindings=bindings,
            operation_ast=operation,
            package_root=output_dir,
            normalize_operands_to_vnd=bool(ledger_row["normalize_operands_to_vnd"]),
        )
    raise ValueError(f"Q{qid}: unsupported execution_mode {mode!r}")


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_archive(source_dir: Path, archive: Path) -> None:
    if archive.exists():
        raise FileExistsError(f"Refusing to overwrite archive: {archive}")
    temporary = archive.with_suffix(archive.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(source_dir).as_posix())
    os.replace(temporary, archive)


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    output_dir = args.output_dir.resolve()
    archive = (args.archive or output_dir.with_suffix(".zip")).resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite output directory: {output_dir}")
    if archive.exists():
        raise FileExistsError(f"Refusing to overwrite archive: {archive}")
    questions = load_jsonl(args.questions.resolve())
    question_ids = index_rows(questions, name="questions")
    ledger = index_rows(load_jsonl(args.execution_ledger.resolve()), name="execution ledger")
    missing = sorted(set(question_ids) - set(ledger))
    unexpected = sorted(set(ledger) - set(question_ids))
    if missing or unexpected:
        raise ValueError(
            f"Execution ledger must cover every question; missing={missing[:10]}, unexpected={unexpected[:10]}"
        )
    tables = load_v2_tables(bundle)
    output_dir.mkdir(parents=True, exist_ok=False)
    records = [compile_record(question, ledger[int(question["id"])], output_dir, tables) for question in questions]
    write_json(output_dir / "submission.json", records)
    directory_result = validate_submission_directory(output_dir, questions)
    write_archive(output_dir, archive)
    zip_result = validate_submission_zip(archive, questions, output_dir.parent / f"{output_dir.name}_validated")
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "archive": str(archive),
                "directory_validation": directory_result.to_dict(),
                "zip_validation": zip_result.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
