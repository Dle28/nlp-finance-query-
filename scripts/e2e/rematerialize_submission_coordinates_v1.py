#!/usr/bin/env python3
"""Repair competition table coordinates in an already-built submission.

This is a narrow migration tool for a submission produced before the
source-line-map gate existed.  It changes only ``relevant_tables``: answers,
questions, evidence CSVs and pandas queries are copied unchanged.  The input
coordinates must be the builder's ``local_ordinal + 1`` form; every table
coordinate is then re-materialized through the immutable V2 UID map.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any
import zipfile


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = ROOT / "data/ViFinQA/questions/questions.jsonl"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_coordinate_index(
    *,
    tables_path: Path,
    source_line_map_path: Path,
) -> tuple[dict[tuple[str, int], int], dict[str, Any]]:
    """Index ``document_id|local_ordinal+1`` to the canonical OCR line."""

    source_line_map = json.loads(source_line_map_path.read_text(encoding="utf-8"))
    if not isinstance(source_line_map, dict):
        raise ValueError("source line map must be a JSON object")
    index: dict[tuple[str, int], int] = {}
    table_uids: set[str] = set()
    for line_number, table in enumerate(_read_jsonl(tables_path), start=1):
        uid = str(table.get("internal_table_uid") or "")
        document_id = str(table.get("document_id") or "").removesuffix(".txt")
        if not uid or not document_id:
            raise ValueError(f"{tables_path}:{line_number} has incomplete table identity")
        if uid in table_uids:
            raise ValueError(f"duplicate table UID: {uid}")
        table_uids.add(uid)
        if uid not in source_line_map:
            raise ValueError(f"source line map is missing table UID: {uid}")
        try:
            local_position = int(table["local_ordinal"]) + 1
            source_line = int(source_line_map[uid])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid coordinate for table UID {uid}") from error
        if local_position < 1 or source_line < 1:
            raise ValueError(f"non-positive coordinate for table UID {uid}")
        key = (document_id, local_position)
        if key in index:
            raise ValueError(f"duplicate local table position: {document_id}|{local_position}")
        index[key] = source_line
    if table_uids != {str(uid) for uid in source_line_map}:
        raise ValueError(
            "source line map coverage mismatch: "
            f"tables={len(table_uids)} map_entries={len(source_line_map)}"
        )
    return index, {
        "table_count": len(table_uids),
        "map_entry_count": len(source_line_map),
        "map_sha256": sha256_file(source_line_map_path),
        "tables_sha256": sha256_file(tables_path),
    }


def rematerialize_rows(
    rows: list[dict[str, Any]],
    *,
    local_coordinate_index: dict[tuple[str, int], int],
    allow_unmapped_source_overrides: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert every local table position while preserving explicit overrides."""

    output: list[dict[str, Any]] = []
    stats = {
        "references_seen": 0,
        "references_rematerialized": 0,
        "unmapped_source_overrides": 0,
    }
    for row in rows:
        current = dict(row)
        references: list[str] = []
        for reference in row.get("relevant_tables") or []:
            value = str(reference)
            document_id, separator, position_text = value.rpartition("|")
            if not separator or not document_id:
                raise ValueError(f"Q{row.get('id')}: invalid table reference {value!r}")
            try:
                local_position = int(position_text)
            except ValueError as error:
                raise ValueError(f"Q{row.get('id')}: invalid table position {value!r}") from error
            stats["references_seen"] += 1
            source_line = local_coordinate_index.get((document_id, local_position))
            if source_line is None:
                if not allow_unmapped_source_overrides:
                    raise ValueError(
                        f"Q{row.get('id')}: {value} is not a V2 local table position; "
                        "pass --allow-unmapped-source-overrides only for explicit "
                        "legacy line overrides"
                    )
                stats["unmapped_source_overrides"] += 1
                references.append(value)
                continue
            references.append(f"{document_id}|{source_line}")
            stats["references_rematerialized"] += 1
        current["relevant_tables"] = references
        output.append(current)
    return output, stats


def _copy_submission_payload(source_dir: Path, destination: Path) -> None:
    (destination / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_dir / "submission.json", destination / "submission.json")
    source_data = source_dir / "data"
    for csv_path in sorted(source_data.glob("*.csv")):
        shutil.copy2(csv_path, destination / "data" / csv_path.name)
    for name in (
        "diagnostics.jsonl",
        "prediction_audit_ledger_v1.jsonl",
        "best_surviving_candidates_v1.jsonl",
        "submission_ledger_v1.jsonl",
        "resolved_predictions_v1.jsonl",
        "e2e_receipts_v1.jsonl",
        "submission_compile_report.json",
    ):
        source = source_dir / name
        if source.is_file():
            shutil.copy2(source, destination / name)


def rematerialize_submission(
    *,
    submission_dir: Path,
    tables_path: Path,
    source_line_map_path: Path,
    output_dir: Path,
    questions_path: Path = DEFAULT_QUESTIONS,
    allow_unmapped_source_overrides: bool = False,
) -> dict[str, Any]:
    submission_dir = submission_dir.resolve()
    tables_path = tables_path.resolve()
    source_line_map_path = source_line_map_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    submission_path = submission_dir / "submission.json"
    if not submission_path.is_file() or not (submission_dir / "data").is_dir():
        raise FileNotFoundError(f"submission directory is incomplete: {submission_dir}")

    input_rows = json.loads(submission_path.read_text(encoding="utf-8"))
    if not isinstance(input_rows, list) or any(not isinstance(row, dict) for row in input_rows):
        raise ValueError("submission.json must contain a list of objects")
    expected_ids = [int(row["id"]) for row in _read_jsonl(questions_path.resolve())]
    actual_ids = [int(row["id"]) for row in input_rows]
    if actual_ids != expected_ids:
        raise ValueError(
            "submission question ids/order do not match questions.jsonl: "
            f"actual_count={len(actual_ids)} expected_count={len(expected_ids)}"
        )
    local_index, coordinate_manifest = load_coordinate_index(
        tables_path=tables_path,
        source_line_map_path=source_line_map_path,
    )
    rows, repair_stats = rematerialize_rows(
        input_rows,
        local_coordinate_index=local_index,
        allow_unmapped_source_overrides=allow_unmapped_source_overrides,
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        _copy_submission_payload(submission_dir, staging)
        (staging / "submission.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        original_report_path = submission_dir / "build_report.json"
        original_report = (
            json.loads(original_report_path.read_text(encoding="utf-8"))
            if original_report_path.is_file()
            else {}
        )
        report = dict(original_report)
        report["coordinate_repair"] = {
            "protocol": "vifinqa_submission_coordinate_repair_v1",
            "input_coordinate_kind": "local_ordinal_plus_one",
            "output_coordinate_kind": "canonical_ocr_table_start_line",
            "input_submission_sha256": sha256_file(submission_path),
            "tables_path": str(tables_path),
            "source_line_map_path": str(source_line_map_path),
            "allow_unmapped_source_overrides": allow_unmapped_source_overrides,
            **coordinate_manifest,
            **repair_stats,
        }
        report["source_line_coordinates"] = {
            "map_path": str(source_line_map_path),
            "map_entries": coordinate_manifest["map_entry_count"],
            "map_sha256": coordinate_manifest["map_sha256"],
            "source_char_start": 0,
            "source_line_map": repair_stats["references_rematerialized"],
            "local_ordinal_fallback": 0,
            "local_ordinal_fallback_allowed": False,
            "line_override": repair_stats["unmapped_source_overrides"],
            "status": "PASS",
        }
        (staging / "build_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    archive_path = output_dir / "submission.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(output_dir / "submission.json", "submission.json")
        for csv_path in sorted((output_dir / "data").glob("*.csv")):
            archive.write(csv_path, f"data/{csv_path.name}")
    report = json.loads((output_dir / "build_report.json").read_text(encoding="utf-8"))
    report["zip_path"] = str(archive_path)
    report["zip_size_bytes"] = archive_path.stat().st_size
    (output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission-dir", type=Path, required=True)
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--source-line-map", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument(
        "--allow-unmapped-source-overrides",
        action="store_true",
        help="Preserve explicit legacy line overrides without a V2 table UID.",
    )
    args = parser.parse_args()
    report = rematerialize_submission(
        submission_dir=args.submission_dir,
        tables_path=args.tables,
        source_line_map_path=args.source_line_map,
        output_dir=args.output_dir,
        questions_path=args.questions,
        allow_unmapped_source_overrides=args.allow_unmapped_source_overrides,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
