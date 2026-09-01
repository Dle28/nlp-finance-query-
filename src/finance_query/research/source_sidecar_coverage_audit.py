"""Audit whether full-corpus retrieval tables can be reconstructed as V2/V3 sources.

The existing review bundle intentionally contains only a subset of the full
corpus.  This research-only audit distinguishes a genuinely malformed OCR
table from one that is merely absent from that subset.  It emits no table
cells, numeric values, answers, or candidate bindings.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.table_structure import parse_html_table
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_source_sidecar_coverage_audit_v1"
CONTRACT = {
    "research_only": True,
    "source_coverage_diagnostic_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "human_verified",
        "raw_source_cell",
        "raw_source_row",
        "rows",
        "cell_provenance",
        "context_before",
    }
)


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            yield value


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_asset_manifest(assets_path: Path, manifest_path: Path) -> None:
    manifest = _json(manifest_path)
    descriptor = (manifest.get("outputs") or {}).get(assets_path.name) or {}
    if sha256_file(assets_path) != descriptor.get("sha256"):
        raise ValueError("full-corpus assets do not match their manifest")


def _source_identity(asset: Mapping[str, Any]) -> dict[str, object]:
    return {
        "document_id": str(asset.get("document_id") or ""),
        "internal_table_uid": str(asset.get("internal_table_uid") or ""),
        "source_sha256": str(asset.get("source_sha256") or ""),
        "table_sha256": str(asset.get("table_sha256") or ""),
        "local_ordinal": asset.get("local_ordinal"),
        "char_start": asset.get("char_start"),
    }


def _valid_grid(parsed: Mapping[str, Any]) -> bool:
    rows = parsed.get("rows") or []
    provenance = parsed.get("cell_provenance") or []
    if len(rows) != len(provenance):
        return False
    return all(isinstance(row, list) and isinstance(prov, list) and len(row) == len(prov) for row, prov in zip(rows, provenance))


def _reconstruction_status(asset: Mapping[str, Any], *, source_cache: dict[Path, tuple[str, str]]) -> tuple[str, list[str]]:
    source_path = Path(str(asset.get("source_path") or ""))
    if not source_path.is_file():
        return "SOURCE_FILE_MISSING", ["source_file_missing"]
    if source_path not in source_cache:
        raw = source_path.read_bytes()
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return "SOURCE_UTF8_INVALID", ["source_utf8_invalid"]
        source_cache[source_path] = (text, hashlib.sha256(raw).hexdigest())
    text, source_sha256 = source_cache[source_path]
    if source_sha256 != str(asset.get("source_sha256") or ""):
        return "SOURCE_HASH_MISMATCH", ["source_hash_mismatch"]
    try:
        char_start, char_end = int(asset["char_start"]), int(asset["char_end"])
    except (KeyError, TypeError, ValueError):
        return "SOURCE_LOCATOR_INVALID", ["source_locator_invalid"]
    if char_start < 0 or char_end <= char_start or char_end > len(text):
        return "SOURCE_LOCATOR_INVALID", ["source_locator_invalid"]
    table_html = text[char_start:char_end]
    if hashlib.sha256(table_html.encode("utf-8")).hexdigest() != str(asset.get("table_sha256") or ""):
        return "TABLE_SLICE_HASH_MISMATCH", ["table_slice_hash_mismatch"]
    parsed = parse_html_table(table_html, context=str(asset.get("context_before") or ""))
    if not _valid_grid(parsed):
        return "PARSED_GRID_INVALID", ["parsed_grid_invalid"]
    if parsed.get("rows") != asset.get("rows"):
        return "PARSED_ROWS_DRIFT", ["parsed_rows_drift"]
    return "SOURCE_RECONSTRUCTABLE", []


def build_source_sidecar_coverage_audit(
    *,
    row_review_queue_path: Path,
    full_assets_path: Path,
    full_assets_manifest_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit V2/V3 sidecar coverage for all tables appearing in review routes."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_asset_manifest(full_assets_path, full_assets_manifest_path)
    review_rows = list(_rows(row_review_queue_path))
    requested_ids = {str(row.get("internal_table_uid") or "") for row in review_rows}
    requested_ids.discard("")
    if not requested_ids:
        raise ValueError("row review queue contains no table IDs")
    v2_ids = {str(row.get("internal_table_uid") or "") for row in _rows(structured_tables_path)}
    v3_ids = {str(row.get("internal_table_uid") or "") for row in _rows(evidence_context_path)}
    existing_both = requested_ids & v2_ids & v3_ids
    missing_ids = requested_ids - existing_both
    selected_assets: dict[str, dict[str, Any]] = {}
    for asset in _rows(full_assets_path):
        uid = str(asset.get("internal_table_uid") or "")
        if uid in missing_ids:
            selected_assets[uid] = asset
    missing_assets = sorted(missing_ids - set(selected_assets))
    if missing_assets:
        raise ValueError("review queue contains IDs absent from full-corpus assets")
    source_cache: dict[Path, tuple[str, str]] = {}
    audit_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for uid in sorted(missing_ids):
        asset = selected_assets[uid]
        status, reasons = _reconstruction_status(asset, source_cache=source_cache)
        row = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "source_identity": _source_identity(asset),
            "existing_v2": uid in v2_ids,
            "existing_v3": uid in v3_ids,
            "reconstruction_status": status,
            "reason_codes": reasons,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(row):
            raise ValueError("coverage audit leaked a reviewer field or source value")
        audit_rows.append(row)
        status_counts[status] += 1
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "review_row_count": len(review_rows),
        "review_table_count": len(requested_ids),
        "v2_v3_existing_table_count": len(existing_both),
        "v2_v3_missing_table_count": len(missing_ids),
        "reconstruction_status_counts": dict(sorted(status_counts.items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "source_sidecar_coverage_audit_v1.jsonl"
        summary_path = temporary / "source_sidecar_coverage_summary_v1.json"
        _write_jsonl(audit_path, audit_rows)
        _write_json(summary_path, summary)
        inputs = {
            "row_review_queue": row_review_queue_path,
            "full_assets": full_assets_path,
            "full_assets_manifest": full_assets_manifest_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    audit_path.name: {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                    summary_path.name: {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"status": "BUILT", **summary, "answer_eligible": False, "submission_eligible": False}


def validate_source_sidecar_coverage_audit(artifact_dir: Path) -> dict[str, Any]:
    manifest = _json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected source-sidecar coverage audit contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("source-sidecar coverage audit input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("source-sidecar coverage audit output hash mismatch")
    rows = list(_rows(artifact_dir / "source_sidecar_coverage_audit_v1.jsonl"))
    summary = _json(artifact_dir / "source_sidecar_coverage_summary_v1.json")
    if any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT for row in rows):
        raise ValueError("source-sidecar coverage audit leaked unsafe content")
    if int(summary.get("v2_v3_missing_table_count") or 0) != len(rows):
        raise ValueError("source-sidecar coverage audit summary mismatch")
    if sum(int(value) for value in (summary.get("reconstruction_status_counts") or {}).values()) != len(rows):
        raise ValueError("source-sidecar coverage audit status count mismatch")
    return {
        "status": "PASS",
        "review_table_count": int(summary["review_table_count"]),
        "v2_v3_missing_table_count": len(rows),
        "answer_eligible": False,
        "submission_eligible": False,
    }
