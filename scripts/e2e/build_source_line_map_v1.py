#!/usr/bin/env python3
"""Build the immutable OCR table-coordinate map used by submission packaging.

The structured V2 bundle stores an internal table ordinal and a character
offset into the original extracted report.  The competition submission uses
the canonical 1-based OCR line at which the table starts.  This small
preprocessing step materializes that coordinate once, verifies the source
hashes recorded by V2, and makes the result available to remote runtimes
where the original reports are not mounted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vifinqa_source_line_map_v1"
PROVENANCE_KEYS = (
    "source_path",
    "source_sha256",
    "char_start",
    "char_end",
    "byte_start",
    "byte_end",
    "table_sha256",
)


def table_provenance(table: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read provenance from both the V2 wrapper and the full flat asset.

    ``tables_structured_v2.jsonl`` nests provenance under
    ``source_provenance``.  The full-corpus ``TableAsset`` keeps the same
    immutable fields at the top level.  The coordinate map must be produced
    from either representation without changing the coordinate contract.
    """

    nested = table.get("source_provenance")
    if isinstance(nested, Mapping) and nested:
        return nested
    return {
        key: table[key]
        for key in PROVENANCE_KEYS
        if key in table
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_line_map(tables_path: Path) -> tuple[dict[str, int], dict[str, Any]]:
    """Return ``internal_table_uid -> 1-based source line`` with hash checks."""

    tables_path = tables_path.resolve()
    mapping: dict[str, int] = {}
    source_cache: dict[Path, tuple[str, str]] = {}
    table_count = 0
    source_file_count = 0

    with tables_path.open("r", encoding="utf-8") as tables_handle:
        for line_number, raw_line in enumerate(tables_handle, start=1):
            if not raw_line.strip():
                continue
            table_count += 1
            table = json.loads(raw_line)
            uid = str(table.get("internal_table_uid") or "")
            if not uid:
                raise ValueError(f"{tables_path}:{line_number} has no internal_table_uid")
            if uid in mapping:
                raise ValueError(f"{tables_path}:{line_number} duplicates internal_table_uid {uid}")

            provenance = table_provenance(table)
            source_path = Path(str(provenance.get("source_path") or ""))
            if not source_path.is_file():
                raise FileNotFoundError(
                    f"{tables_path}:{line_number} source file is unavailable: {source_path}"
                )
            if source_path not in source_cache:
                source_text = source_path.read_text(encoding="utf-8", errors="replace")
                source_cache[source_path] = (source_text, sha256_file(source_path))
                source_file_count += 1
            source_text, source_sha256 = source_cache[source_path]
            expected_sha256 = str(provenance.get("source_sha256") or "")
            if expected_sha256 and source_sha256 != expected_sha256:
                raise ValueError(
                    f"{tables_path}:{line_number} source hash mismatch for {uid}: "
                    f"expected={expected_sha256} actual={source_sha256}"
                )
            try:
                char_start = int(provenance["char_start"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{tables_path}:{line_number} has invalid char_start") from error
            if char_start < 0 or char_start > len(source_text):
                raise ValueError(
                    f"{tables_path}:{line_number} char_start={char_start} is outside "
                    f"source length {len(source_text)}"
                )

            # The scorer's coordinate is 1-based, and the table starts at the
            # character offset recorded by the immutable V2 provenance.
            mapping[uid] = source_text.count("\n", 0, char_start) + 1

    if not mapping:
        raise ValueError(f"{tables_path} contains no structured tables")
    return mapping, {
        "table_count": table_count,
        "map_entry_count": len(mapping),
        "source_file_count": source_file_count,
        "line_base": 1,
        "coordinate_kind": "canonical_ocr_table_start_line",
    }


def write_source_line_map(
    *,
    tables_path: Path,
    output_path: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Build and write the map plus a hash-bound manifest."""

    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite source line map: {output_path}")
    mapping, stats = build_source_line_map(tables_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dict(sorted(mapping.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if manifest_path is None:
        manifest_path = output_path.with_name(f"{output_path.stem}.manifest.json")
    manifest_path = manifest_path.resolve()
    if manifest_path.exists():
        raise FileExistsError(f"refusing to overwrite source line map manifest: {manifest_path}")
    manifest = {
        "protocol": PROTOCOL,
        "tables_path": str(tables_path.resolve()),
        "tables_sha256": sha256_file(tables_path.resolve()),
        "map_path": str(output_path),
        "map_sha256": sha256_file(output_path),
        **stats,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args()
    print(
        json.dumps(
            write_source_line_map(
                tables_path=args.tables,
                output_path=args.output,
                manifest_path=args.manifest,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
