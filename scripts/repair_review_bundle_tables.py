#!/usr/bin/env python3
"""Create a local V2 table-structure sidecar for an immutable review bundle.

The bundle itself is not modified.  The sidecar is keyed by the original table
UID and rebuilt straight from the report's raw HTML, so it can repair blank
cell positions and HTML spans without rebuilding lexical or dense indexes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.table_structure import parse_html_table  # noqa: E402


TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=ROOT / "data/ViFinQA/financial_statements",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write a sidecar even if one or more bundle tables cannot be verified.",
    )
    return parser.parse_args()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def source_path_for(table: dict[str, Any], reports_root: Path) -> Path:
    document_id = str(table["document_id"])
    expected = reports_root / str(table["ticker"]) / str(table["report_year"])
    expected = expected / document_id / f"{document_id}_extracted.txt"
    if expected.is_file():
        return expected
    matches = sorted(reports_root.rglob(f"{document_id}_extracted.txt"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"No raw report for {document_id}")
    raise RuntimeError(f"Ambiguous raw reports for {document_id}: {matches}")


def table_from_source(table: dict[str, Any], reports_root: Path) -> tuple[str, Path, int]:
    path = source_path_for(table, reports_root)
    text = path.read_text(encoding="utf-8")
    ordinal = int(table["local_ordinal"])
    matches = list(TABLE_RE.finditer(text))
    if ordinal < 1 or ordinal > len(matches):
        raise ValueError(f"Invalid local_ordinal={ordinal} for {path}")
    match = matches[ordinal - 1]
    html = match.group(0)
    table_hash = sha256_bytes(html.encode("utf-8"))
    uid_payload = (
        f"{table['document_id']}\x1f{ordinal}\x1f{match.start()}\x1f{table_hash}"
    ).encode("utf-8")
    observed_uid = sha256_bytes(uid_payload)
    if observed_uid != str(table["internal_table_uid"]):
        raise ValueError(
            "Raw source identity does not match bundle UID: "
            f"expected {table['internal_table_uid']}, observed {observed_uid}"
        )
    return html, path, match.start()


def repair_bundle_tables(
    bundle_dir: Path,
    reports_root: Path,
    output_path: Path,
    *,
    force: bool = False,
    allow_partial: bool = False,
) -> dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    reports_root = reports_root.resolve()
    output_path = output_path.resolve()
    tables_path = bundle_dir / "tables.jsonl"
    if not tables_path.is_file():
        raise FileNotFoundError(tables_path)
    if output_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {output_path}; use --force")
    if not reports_root.is_dir():
        raise NotADirectoryError(reports_root)

    tables = load_jsonl(tables_path)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    source_counts: Counter[str] = Counter()
    for table in tables:
        uid = str(table["internal_table_uid"])
        try:
            table_html, source_path, char_start = table_from_source(table, reports_root)
            structure = parse_html_table(
                table_html,
                context=str(table.get("context_before") or ""),
            )
            rows.append(
                {
                    "internal_table_uid": uid,
                    "document_id": table["document_id"],
                    "local_ordinal": table["local_ordinal"],
                    "source_provenance": {
                        "source_path": str(source_path),
                        "source_sha256": sha256_file(source_path),
                        "char_start": char_start,
                        "table_sha256": sha256_bytes(table_html.encode("utf-8")),
                    },
                    **structure,
                }
            )
            source_counts[str(source_path)] += 1
        except (FileNotFoundError, RuntimeError, UnicodeDecodeError, ValueError) as exc:
            errors.append({"internal_table_uid": uid, "error": str(exc)})

    if errors and not allow_partial:
        sample = "; ".join(error["error"] for error in errors[:3])
        raise RuntimeError(
            f"Refusing partial table repair ({len(errors)}/{len(tables)} failed): {sample}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(output_path)

    manifest_path = output_path.with_name("table_structure_v2.manifest.json")
    manifest = {
        "structure_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_bundle_tables_sha256": sha256_file(tables_path),
        "reports_root": str(reports_root),
        "table_count": len(tables),
        "repaired_table_count": len(rows),
        "error_count": len(errors),
        "unique_source_count": len(source_counts),
        "sidecar_sha256": sha256_file(output_path),
        "errors": errors,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    output = args.output or bundle / "tables_structured_v2.jsonl"
    manifest = repair_bundle_tables(
        bundle,
        args.reports_root,
        output,
        force=args.force,
        allow_partial=args.allow_partial,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("No Kaggle, lexical, or dense index was rebuilt.")


if __name__ == "__main__":
    main()
