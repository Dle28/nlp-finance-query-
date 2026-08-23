"""Phase 0-2 implementation of ViFinQA's Certified Canonical Layer.

The layer consumes four immutable, already hash-bound artifacts: the V10 raw
bundle, Table Structure V2, Evidence Context V3, and a Preprocessing V2
checkout.  It deliberately creates *only* research artifacts.  In particular,
it has no path to set ``training_eligible`` or to materialize a model label.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import zip_longest
import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator, Mapping, TextIO

import yaml

from finance_query.evidence_context import (
    evidence_context_manifest_path,
    validate_evidence_context_sidecar,
)
from finance_query.preprocessing_v2.text_repair import numeric_tokens
from finance_query.table_structure import sha256_file, validate_structure_sidecar

from .evidence_graph import build_packet_evidence_graph


CERTIFIED_CANONICAL_PROTOCOL = "vifinqa_certified_canonical_v1"
CERTIFIED_CANONICAL_SCHEMA_VERSION = 1

OUTPUT_NAMES = (
    "input_inventory.json",
    "source_certificates.jsonl",
    "cell_lineage_certificates.jsonl",
    "semantic_assertions.jsonl",
    "issue_registry.jsonl",
    "certification_results.jsonl",
    "proof_receipts.jsonl",
    "benchmark_packets_v1.jsonl",
    "mutation_report.json",
    "validation_report.json",
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SENTINEL = object()
_ASSERTION_FIELDS = (
    "source_identity",
    "cell_lineage",
    "heading_context",
    "table_semantics",
    "period_context",
    "unit_context",
)


class CertifiedCanonicalError(ValueError):
    """A source or certificate contract failed closed."""


@dataclass(frozen=True, slots=True)
class CertifiedCanonicalInputs:
    config_path: Path
    raw_tables: Path
    structured_v2: Path
    evidence_context_v3: Path
    preprocessing_normalized: Path
    preprocessing_manifest: Path
    benchmark_packets_per_bucket: int
    benchmark_target_min: int
    benchmark_target_max: int
    require_benchmark_target_range: bool
    source_hashes: dict[str, str]
    sidecar_validation: dict[str, Any]

    def required_paths(self) -> dict[str, Path]:
        return {
            "config": self.config_path,
            "raw_tables": self.raw_tables,
            "structured_v2": self.structured_v2,
            "evidence_context_v3": self.evidence_context_v3,
            "preprocessing_normalized": self.preprocessing_normalized,
            "preprocessing_manifest": self.preprocessing_manifest,
        }


@dataclass(frozen=True, slots=True)
class CertifiedCanonicalResult:
    output_dir: Path
    manifest_path: Path
    table_count: int
    lifecycle_counts: dict[str, int]
    benchmark_packet_count: int


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise CertifiedCanonicalError(f"{path}:{line_number} is not a JSON object")
            yield value


def _write_json_line(file: TextIO, value: Mapping[str, Any]) -> None:
    file.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _resolve(repo_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (repo_root / path).resolve()


def _new_output_dir(path: Path, inputs: Iterable[Path]) -> None:
    resolved_inputs = {item.resolve() for item in inputs}
    if path.resolve() in resolved_inputs:
        raise CertifiedCanonicalError("output-dir cannot be an immutable input path")
    if path.exists():
        raise FileExistsError("output-dir must be new; completed artifacts are immutable")
    path.mkdir(parents=True)


def _input_entry(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _require_hash(value: object, *, field: str) -> str:
    candidate = str(value or "").casefold()
    if not _HASH_RE.fullmatch(candidate):
        raise CertifiedCanonicalError(f"{field} must be a SHA-256 hex digest")
    return candidate


def _optional_match(
    left: object,
    right: object,
    *,
    code: str,
    failures: list[str],
) -> None:
    if left is not None and right is not None and left != right:
        failures.append(code)


def _load_preprocessing_manifest(
    manifest_path: Path,
    *,
    raw_tables: Path,
    structured_v2: Path,
    evidence_context_v3: Path,
    preprocessing_normalized: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("protocol") != "vifinqa_preprocessing_v2"
        or int(manifest.get("schema_version") or 0) != 2
        or manifest.get("training_eligible") is not False
    ):
        raise CertifiedCanonicalError("preprocessing manifest is not a non-promotable V2 checkout")

    output = (manifest.get("outputs") or {}).get(preprocessing_normalized.name) or {}
    if output.get("sha256") != sha256_file(preprocessing_normalized):
        raise CertifiedCanonicalError("preprocessing normalized artifact hash does not match its manifest")

    expected_inputs = manifest.get("inputs") or {}
    for name, path in (
        ("raw_tables", raw_tables),
        ("structured_v2", structured_v2),
        ("evidence_context_v3", evidence_context_v3),
    ):
        item = expected_inputs.get(str(path)) or {}
        if item.get("sha256") != sha256_file(path):
            raise CertifiedCanonicalError(
                f"preprocessing manifest is not bound to the configured {name}"
            )
    return manifest


def load_inputs(config_path: Path) -> CertifiedCanonicalInputs:
    """Load explicit CCL paths and prove their upstream sidecar lineage."""
    config_path = config_path.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if (
        config.get("protocol") != CERTIFIED_CANONICAL_PROTOCOL
        or int(config.get("schema_version") or 0) != CERTIFIED_CANONICAL_SCHEMA_VERSION
    ):
        raise CertifiedCanonicalError("unsupported Certified Canonical Layer config")
    repo_root = config_path.parent.parent.resolve()
    configured = config.get("inputs") or {}
    required = (
        "raw_tables",
        "structured_v2",
        "evidence_context_v3",
        "preprocessing_normalized",
        "preprocessing_manifest",
    )
    if any(not configured.get(name) for name in required):
        raise CertifiedCanonicalError("config must declare every immutable input explicitly")
    paths = {name: _resolve(repo_root, configured[name]) for name in required}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)

    sidecar_validation: dict[str, Any] = {"enabled": False}
    if bool(config.get("validate_sidecar_manifests", True)):
        structure_manifest = validate_structure_sidecar(paths["raw_tables"].parent, paths["structured_v2"])
        context_manifest = validate_evidence_context_sidecar(
            paths["raw_tables"].parent,
            paths["structured_v2"],
            paths["evidence_context_v3"],
        )
        sidecar_validation = {
            "enabled": True,
            "structure_manifest_sha256": sha256_file(
                paths["structured_v2"].with_name("table_structure_v2.manifest.json")
            ),
            "evidence_context_manifest_sha256": sha256_file(
                evidence_context_manifest_path(paths["evidence_context_v3"])
            ),
            "structure_version": structure_manifest.get("structure_version"),
            "evidence_context_version": context_manifest.get("evidence_context_version"),
        }

    _load_preprocessing_manifest(
        paths["preprocessing_manifest"],
        raw_tables=paths["raw_tables"],
        structured_v2=paths["structured_v2"],
        evidence_context_v3=paths["evidence_context_v3"],
        preprocessing_normalized=paths["preprocessing_normalized"],
    )
    benchmark = config.get("benchmark") or {}
    packets_per_bucket = int(benchmark.get("packets_per_bucket") or 45)
    if not 1 <= packets_per_bucket <= 100:
        raise CertifiedCanonicalError("benchmark.packets_per_bucket must be between 1 and 100")
    target_min = int(benchmark.get("target_min_packets") or 250)
    target_max = int(benchmark.get("target_max_packets") or 300)
    if target_min < 1 or target_max < target_min:
        raise CertifiedCanonicalError("benchmark target range is invalid")

    all_paths = {"config": config_path, **paths}
    return CertifiedCanonicalInputs(
        config_path=config_path,
        raw_tables=paths["raw_tables"],
        structured_v2=paths["structured_v2"],
        evidence_context_v3=paths["evidence_context_v3"],
        preprocessing_normalized=paths["preprocessing_normalized"],
        preprocessing_manifest=paths["preprocessing_manifest"],
        benchmark_packets_per_bucket=packets_per_bucket,
        benchmark_target_min=target_min,
        benchmark_target_max=target_max,
        require_benchmark_target_range=bool(benchmark.get("require_target_range", False)),
        source_hashes={name: sha256_file(path) for name, path in all_paths.items()},
        sidecar_validation=sidecar_validation,
    )


def _record_uid(record: Mapping[str, Any], *, source_name: str, line_number: int) -> str:
    uid = str(record.get("internal_table_uid") or "")
    if not _HASH_RE.fullmatch(uid):
        raise CertifiedCanonicalError(
            f"{source_name}:{line_number} has no valid internal_table_uid"
        )
    return uid


def _identity_failures(
    raw: Mapping[str, Any],
    structured: Mapping[str, Any],
    context: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> list[str]:
    """Validate source identity without accepting a parser UID as sufficient proof."""
    failures: list[str] = []
    uid = str(raw.get("internal_table_uid") or "")
    for source_name, record in (
        ("structured_v2", structured),
        ("evidence_context_v3", context),
        ("preprocessing_v2", normalized),
    ):
        if str(record.get("internal_table_uid") or "") != uid:
            failures.append(f"uid_mismatch_{source_name}")

    for source_name, record in (
        ("structured_v2", structured),
        ("evidence_context_v3", context),
    ):
        _optional_match(
            raw.get("document_id"),
            record.get("document_id"),
            code=f"document_id_mismatch_{source_name}",
            failures=failures,
        )
        _optional_match(
            raw.get("local_ordinal"),
            record.get("local_ordinal"),
            code=f"local_ordinal_mismatch_{source_name}",
            failures=failures,
        )
    document = normalized.get("document") or {}
    for field in ("document_id", "ticker", "report_year", "scope", "page_no", "local_ordinal"):
        _optional_match(
            raw.get(field),
            document.get(field),
            code=f"preprocessing_{field}_mismatch",
            failures=failures,
        )

    structured_provenance = structured.get("source_provenance") or {}
    context_provenance = context.get("source_provenance") or {}
    normalized_provenance = normalized.get("source_provenance") or {}
    for field in ("source_sha256", "table_sha256", "char_start"):
        _optional_match(
            structured_provenance.get(field),
            context_provenance.get(field),
            code=f"v2_v3_{field}_mismatch",
            failures=failures,
        )
        _optional_match(
            structured_provenance.get(field),
            normalized_provenance.get(field),
            code=f"v2_preprocessing_{field}_mismatch",
            failures=failures,
        )
    for field in ("source_sha256", "table_sha256"):
        try:
            _require_hash(structured_provenance.get(field), field=f"V2 {field}")
        except CertifiedCanonicalError:
            failures.append(f"invalid_{field}")
    if normalized.get("source_record_sha256") != _sha_json(raw):
        failures.append("source_record_sha256_mismatch")
    return sorted(set(failures))


def _rectangular_grid(rows: object) -> tuple[list[list[Any]], int, list[str]]:
    if not isinstance(rows, list) or not rows:
        return [], 0, ["empty_grid"]
    if not all(isinstance(row, list) for row in rows):
        return [], 0, ["non_list_grid_row"]
    width = len(rows[0]) if rows else 0
    if width == 0:
        return rows, width, ["zero_width_grid"]
    if any(len(row) != width for row in rows):
        return rows, width, ["non_rectangular_grid"]
    return rows, width, []


def _valid_coordinate(value: object, *, row_count: int, width: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value < row_count * width
    )


def _cell_lineage_failures(
    structured: Mapping[str, Any], normalized: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """Check coordinate/value/structure invariants without changing a cell."""
    failures: list[str] = []
    source_rows, width, row_failures = _rectangular_grid(structured.get("rows"))
    failures.extend(row_failures)
    grid = normalized.get("canonical_grid") or {}
    canonical_rows, canonical_width, canonical_failures = _rectangular_grid(grid.get("rows"))
    failures.extend(f"canonical_{code}" for code in canonical_failures)
    if grid.get("structure_source") != "tables_structured_v2":
        failures.append("canonical_structure_source_mismatch")
    if source_rows and (
        len(canonical_rows) != len(source_rows) or canonical_width != width or grid.get("width") != width
    ):
        failures.append("canonical_grid_shape_mismatch")

    provenance = structured.get("cell_provenance")
    canonical_provenance = grid.get("cell_provenance")
    if not isinstance(provenance, list) or len(provenance) != len(source_rows):
        failures.append("provenance_shape_mismatch")
        provenance = []
    elif any(not isinstance(row, list) or len(row) != width for row in provenance):
        failures.append("provenance_shape_mismatch")
    elif canonical_provenance != provenance:
        failures.append("canonical_provenance_mismatch")

    # Check the derived mapping as well as equality with V2. Otherwise a
    # malformed derived anchor would be reported only as an opaque mismatch,
    # which makes the mutation suite unable to prove coordinate detection.
    if isinstance(canonical_provenance, list):
        for row in canonical_provenance:
            if not isinstance(row, list) or len(row) != width:
                failures.append("canonical_provenance_shape_mismatch")
                break
        else:
            for row_index, row in enumerate(canonical_provenance):
                for column_index, cell in enumerate(row):
                    if not isinstance(cell, dict):
                        failures.append("invalid_canonical_cell_provenance")
                        continue
                    anchor_row = cell.get("anchor_row")
                    anchor_column = cell.get("anchor_column")
                    if not (
                        isinstance(anchor_row, int)
                        and not isinstance(anchor_row, bool)
                        and isinstance(anchor_column, int)
                        and not isinstance(anchor_column, bool)
                        and 0 <= anchor_row < len(source_rows)
                        and 0 <= anchor_column < width
                    ):
                        failures.append("invalid_anchor_coordinate")
                    if not bool(cell.get("covered_by_span")) and (
                        anchor_row != row_index or anchor_column != column_index
                    ):
                        failures.append("uncovered_cell_anchor_mismatch")

    numeric_cell_count = 0
    if source_rows and canonical_rows and len(source_rows) == len(canonical_rows) and width == canonical_width:
        for row_index, (source_row, canonical_row) in enumerate(zip(source_rows, canonical_rows)):
            for column_index, (source_value, canonical_value) in enumerate(zip(source_row, canonical_row)):
                source_numbers = numeric_tokens(str(source_value or ""))
                if source_numbers:
                    numeric_cell_count += 1
                if source_numbers != numeric_tokens(str(canonical_value or "")):
                    failures.append("numeric_lexeme_changed")
    if provenance and source_rows:
        row_count = len(source_rows)
        for row_index, row in enumerate(provenance):
            for column_index, cell in enumerate(row):
                if not isinstance(cell, dict):
                    failures.append("invalid_cell_provenance")
                    continue
                anchor_row = cell.get("anchor_row")
                anchor_column = cell.get("anchor_column")
                if not (
                    isinstance(anchor_row, int)
                    and not isinstance(anchor_row, bool)
                    and isinstance(anchor_column, int)
                    and not isinstance(anchor_column, bool)
                    and 0 <= anchor_row < row_count
                    and 0 <= anchor_column < width
                ):
                    failures.append("invalid_anchor_coordinate")
                for source_coordinate in (cell.get("source_row"), cell.get("source_cell")):
                    if not isinstance(source_coordinate, int) or isinstance(source_coordinate, bool) or source_coordinate < 0:
                        failures.append("invalid_source_coordinate")
                if not bool(cell.get("covered_by_span")) and (
                    anchor_row != row_index or anchor_column != column_index
                ):
                    failures.append("uncovered_cell_anchor_mismatch")
    metadata = {
        "row_count": len(source_rows),
        "width": width,
        "cell_count": len(source_rows) * width,
        "numeric_cell_count": numeric_cell_count,
        "raw_grid_sha256": _sha_json(source_rows),
        "cell_provenance_sha256": _sha_json(provenance),
    }
    return sorted(set(failures)), metadata


def _source_certificate(
    raw: Mapping[str, Any],
    structured: Mapping[str, Any],
    context: Mapping[str, Any],
    normalized: Mapping[str, Any],
    *,
    identity_failures: list[str],
) -> dict[str, Any]:
    provenance = structured.get("source_provenance") or {}
    document = normalized.get("document") or {}
    source_identity = {
        "document_sha256": provenance.get("source_sha256"),
        "table_html_sha256": provenance.get("table_sha256"),
        "raw_grid_sha256": _sha_json(structured.get("rows") or []),
        "raw_context_sha256": _sha_json(raw.get("context_before") or ""),
    }
    extraction_identity = {
        "document_id": raw.get("document_id"),
        "page_no": document.get("page_no"),
        "local_ordinal": raw.get("local_ordinal"),
        "char_start": provenance.get("char_start"),
        "parser_version": f"table_structure_v{structured.get('structure_version')}",
    }
    payload = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "certificate_type": "source_identity",
        "internal_table_uid": raw["internal_table_uid"],
        "status": "PASS" if not identity_failures else "FAIL",
        "failure_codes": identity_failures,
        "source_identity": source_identity,
        "extraction_identity": extraction_identity,
        "source_record_sha256": _sha_json(raw),
        "v2_record_sha256": _sha_json(structured),
        "v3_record_sha256": _sha_json(context),
        "preprocessing_record_sha256": _sha_json(normalized),
    }
    return {"certificate_id": _sha_json(payload), **payload}


def _cell_lineage_certificate(
    raw: Mapping[str, Any],
    metadata: Mapping[str, Any],
    failures: list[str],
) -> dict[str, Any]:
    payload = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "certificate_type": "cell_lineage",
        "internal_table_uid": raw["internal_table_uid"],
        "status": "PASS" if not failures else "FAIL",
        "failure_codes": failures,
        **metadata,
        "invariants": {
            "value_identity": "PASS" if "numeric_lexeme_changed" not in failures else "FAIL",
            "coordinate_identity": "PASS"
            if not {"canonical_provenance_mismatch", "invalid_source_coordinate"}.intersection(failures)
            else "FAIL",
            "structural_identity": "PASS"
            if not {
                "non_rectangular_grid",
                "canonical_grid_shape_mismatch",
                "provenance_shape_mismatch",
                "invalid_anchor_coordinate",
                "uncovered_cell_anchor_mismatch",
            }.intersection(failures)
            else "FAIL",
            "semantic_binding": "UNRESOLVED_PHASE_2",
        },
    }
    return {"certificate_id": _sha_json(payload), **payload}


def _source_texts(raw: Mapping[str, Any], structured: Mapping[str, Any]) -> str:
    values = [str(raw.get("context_before") or "")]
    for row in structured.get("rows") or []:
        if isinstance(row, list):
            values.extend(str(cell or "") for cell in row)
    return "\n".join(values).casefold()


def _heading_assertion(
    raw: Mapping[str, Any], structured: Mapping[str, Any], normalized: Mapping[str, Any]
) -> tuple[str, str, object, list[dict[str, Any]], list[str]]:
    outside = normalized.get("outside_table_context") or {}
    heading = str(outside.get("source_heading") or outside.get("reader_heading") or "").strip()
    if heading and heading.casefold() in _source_texts(raw, structured):
        return (
            "PASS",
            "E0",
            heading,
            [{"kind": "raw_context_or_grid_text", "text_sha256": _sha_json(heading)}],
            [],
        )
    return "UNRESOLVED", "E0", None, [], ["heading_not_source_anchored"]


def _header_label_sources(
    structured: Mapping[str, Any], normalized: Mapping[str, Any], label_kind: str
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = structured.get("rows") or []
    columns = ((normalized.get("canonical_grid") or {}).get("columns") or [])
    anchors: list[dict[str, Any]] = []
    failures: list[str] = []
    for column in columns:
        labels = column.get(f"{label_kind}_labels") or []
        if not labels:
            continue
        source_cells = column.get("header_source_cells") or []
        if not source_cells:
            failures.append(f"{label_kind}_without_header_source_cell")
            continue
        matched = False
        for source_cell in source_cells:
            row_index = source_cell.get("row_index")
            column_index = source_cell.get("column_index")
            if not (
                isinstance(row_index, int)
                and isinstance(column_index, int)
                and 0 <= row_index < len(rows)
                and isinstance(rows[row_index], list)
                and 0 <= column_index < len(rows[row_index])
            ):
                failures.append(f"invalid_{label_kind}_header_coordinate")
                continue
            raw_header = str(rows[row_index][column_index] or "").casefold()
            if any(str(label).casefold() in raw_header for label in labels):
                anchors.append(
                    {
                        "kind": f"raw_{label_kind}_header",
                        "row_index": row_index,
                        "column_index": column_index,
                        "labels": [str(label) for label in labels],
                    }
                )
                matched = True
        if not matched:
            failures.append(f"{label_kind}_not_anchored_in_raw_header")
    return anchors, sorted(set(failures))


def _field_assertion(
    *,
    uid: str,
    field: str,
    status: str,
    evidence_class: str,
    value: object,
    evidence_anchors: list[dict[str, Any]],
    reason_codes: list[str],
    derivation_rule: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "internal_table_uid": uid,
        "field": field,
        "status": status,
        "evidence_class": evidence_class,
        "value": value,
        "evidence_anchors": evidence_anchors,
        "reason_codes": sorted(set(reason_codes)),
        "derivation_rule": derivation_rule,
        "rule_version": "1.0.0",
        "certification_authority": "deterministic_phase_0_2_only",
    }
    return {"assertion_id": _sha_json(payload), **payload}


def _build_assertions(
    raw: Mapping[str, Any],
    structured: Mapping[str, Any],
    normalized: Mapping[str, Any],
    source_certificate: Mapping[str, Any],
    cell_certificate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    uid = str(raw["internal_table_uid"])
    heading_status, heading_class, heading_value, heading_anchors, heading_reasons = _heading_assertion(
        raw, structured, normalized
    )
    period_anchors, period_reasons = _header_label_sources(structured, normalized, "period")
    unit_anchors, unit_reasons = _header_label_sources(structured, normalized, "unit")
    return [
        _field_assertion(
            uid=uid,
            field="source_identity",
            status=str(source_certificate["status"]),
            evidence_class="E0",
            value=source_certificate["status"] == "PASS",
            evidence_anchors=[
                {
                    "kind": "source_identity_certificate",
                    "certificate_id": source_certificate["certificate_id"],
                }
            ],
            reason_codes=list(source_certificate.get("failure_codes") or []),
            derivation_rule="source_identity_certificate_v1",
        ),
        _field_assertion(
            uid=uid,
            field="cell_lineage",
            status=str(cell_certificate["status"]),
            evidence_class="E1",
            value=cell_certificate["status"] == "PASS",
            evidence_anchors=[
                {
                    "kind": "cell_lineage_certificate",
                    "certificate_id": cell_certificate["certificate_id"],
                }
            ],
            reason_codes=list(cell_certificate.get("failure_codes") or []),
            derivation_rule="cell_lineage_certificate_v1",
        ),
        _field_assertion(
            uid=uid,
            field="heading_context",
            status=heading_status,
            evidence_class=heading_class,
            value=heading_value,
            evidence_anchors=heading_anchors,
            reason_codes=heading_reasons,
            derivation_rule="raw_heading_occurrence_v1",
        ),
        _field_assertion(
            uid=uid,
            field="table_semantics",
            status="UNRESOLVED",
            evidence_class="E3",
            value=None,
            evidence_anchors=[],
            reason_codes=["phase_4_table_semantics_not_yet_run"],
            derivation_rule="none_phase_0_2",
        ),
        _field_assertion(
            uid=uid,
            field="period_context",
            status="PASS" if period_anchors and not period_reasons else "UNRESOLVED",
            evidence_class="E1",
            value=[anchor["labels"] for anchor in period_anchors] if period_anchors else None,
            evidence_anchors=period_anchors,
            reason_codes=period_reasons or (["period_not_declared"] if not period_anchors else []),
            derivation_rule="v3_header_source_cell_replay_v1",
        ),
        _field_assertion(
            uid=uid,
            field="unit_context",
            status="PASS" if unit_anchors and not unit_reasons else "UNRESOLVED",
            evidence_class="E1",
            value=[anchor["labels"] for anchor in unit_anchors] if unit_anchors else None,
            evidence_anchors=unit_anchors,
            reason_codes=unit_reasons or (["unit_not_declared"] if not unit_anchors else []),
            derivation_rule="v3_header_source_cell_replay_v1",
        ),
    ]


def _reason_to_issue(reason: str) -> str:
    mapping = {
        "ambiguous_table_heading": "AMBIGUOUS_HEADING",
        "generic_table_semantics": "GENERIC_TABLE_SEMANTICS",
        "possible_concatenated_words": "TEXT_REPAIR_UNRESOLVED",
        "generic_column_header": "COLUMN_PATH_UNRESOLVED",
        "duplicate_canonical_headers": "COLUMN_PATH_UNRESOLVED",
        "header_width_exceeds_grid": "STRUCTURE_UNRESOLVED",
        "irregular_grid_width": "STRUCTURE_UNRESOLVED",
        "missing_structure_v2": "STRUCTURE_UNRESOLVED",
        "missing_evidence_context_v3": "CONTEXT_UNRESOLVED",
    }
    return mapping.get(reason, "SEMANTIC_CERTIFICATION_PENDING")


def _issue_priority(code: str) -> tuple[int, str]:
    priorities = {
        "IDENTITY_CONFLICT": 0,
        "CELL_LINEAGE_CONFLICT": 1,
        "AMBIGUOUS_HEADING": 10,
        "GENERIC_TABLE_SEMANTICS": 11,
        "HEADING_CONTEXT_UNRESOLVED": 12,
        "COLUMN_PATH_UNRESOLVED": 13,
        "PERIOD_CONTEXT_UNRESOLVED": 14,
        "UNIT_CONTEXT_UNRESOLVED": 15,
        "TEXT_REPAIR_UNRESOLVED": 16,
        "SEMANTIC_CERTIFICATION_PENDING": 99,
    }
    return priorities.get(code, 90), code


def _issue_registry(
    normalized: Mapping[str, Any],
    source_certificate: Mapping[str, Any],
    cell_certificate: Mapping[str, Any],
    assertions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    uid = str(normalized["internal_table_uid"])
    if source_certificate["status"] != "PASS":
        lifecycle = "QUARANTINED"
        codes = ["IDENTITY_CONFLICT"]
    elif cell_certificate["status"] != "PASS":
        lifecycle = "QUARANTINED"
        codes = ["CELL_LINEAGE_CONFLICT"]
    else:
        lifecycle = "UNRESOLVED"
        codes = []
        for reason in (normalized.get("quality") or {}).get("reason_codes") or []:
            codes.append(_reason_to_issue(str(reason)))
        assertion_by_field = {str(item["field"]): item for item in assertions}
        if assertion_by_field["heading_context"]["status"] != "PASS":
            codes.append("HEADING_CONTEXT_UNRESOLVED")
        if assertion_by_field["period_context"]["status"] != "PASS":
            codes.append("PERIOD_CONTEXT_UNRESOLVED")
        if assertion_by_field["unit_context"]["status"] != "PASS":
            codes.append("UNIT_CONTEXT_UNRESOLVED")
        codes.append("SEMANTIC_CERTIFICATION_PENDING")
    codes = sorted(set(codes), key=_issue_priority)
    nodes = [
        {
            "issue_id": _sha_json({"uid": uid, "code": code}),
            "code": code,
            "severity": "hard" if lifecycle == "QUARANTINED" else "blocking",
        }
        for code in codes
    ]
    primary = nodes[0]
    return {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "internal_table_uid": uid,
        "lifecycle_state": lifecycle,
        "primary_issue": primary,
        "secondary_issues": nodes[1:],
        "issue_graph": {
            "nodes": nodes,
            "edges": [
                {"from_issue_id": node["issue_id"], "to_issue_id": primary["issue_id"]}
                for node in nodes[1:]
            ],
        },
        "source_reason_codes": sorted(
            str(value) for value in (normalized.get("quality") or {}).get("reason_codes") or []
        ),
    }


def _certification_result(
    uid: str,
    source_certificate: Mapping[str, Any],
    cell_certificate: Mapping[str, Any],
    assertions: list[Mapping[str, Any]],
    issue_registry: Mapping[str, Any],
) -> dict[str, Any]:
    assertion_statuses = {
        str(item["field"]): str(item["status"])
        for item in assertions
    }
    result = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "internal_table_uid": uid,
        "lifecycle_state": issue_registry["lifecycle_state"],
        "source_identity_status": source_certificate["status"],
        "cell_lineage_status": cell_certificate["status"],
        "assertion_statuses": assertion_statuses,
        "profile_status": "NOT_EVALUATED_PHASE_0_2",
        "training_eligible": False,
        "promotion_status": "BLOCKED_NO_PROMOTED_ALLOWLIST",
        "primary_issue_code": issue_registry["primary_issue"]["code"],
    }
    return {"certification_result_id": _sha_json(result), **result}


def _proof_receipt(
    uid: str,
    source_certificate: Mapping[str, Any],
    cell_certificate: Mapping[str, Any],
    assertions: list[Mapping[str, Any]],
    issue_registry: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "internal_table_uid": uid,
        "source_certificate_id": source_certificate["certificate_id"],
        "source_certificate_sha256": _sha_json(source_certificate),
        "cell_lineage_certificate_id": cell_certificate["certificate_id"],
        "cell_lineage_certificate_sha256": _sha_json(cell_certificate),
        "assertion_ids": [str(item["assertion_id"]) for item in assertions],
        "assertion_set_sha256": _sha_json(assertions),
        "issue_registry_sha256": _sha_json(issue_registry),
        "certification_result_sha256": _sha_json(result),
        "model_proposals_allowed": False,
        "training_eligible": False,
    }
    return {"proof_receipt_id": _sha_json(payload), **payload}


def _benchmark_bucket(normalized: Mapping[str, Any]) -> str:
    reasons = set(str(value) for value in (normalized.get("quality") or {}).get("reason_codes") or [])
    if "ambiguous_table_heading" in reasons:
        return "ambiguous_heading"
    if "possible_concatenated_words" in reasons:
        return "text_repair"
    if {"generic_column_header", "duplicate_canonical_headers"}.intersection(reasons):
        return "column_path"
    columns = ((normalized.get("canonical_grid") or {}).get("columns") or [])
    has_period = any(column.get("period_labels") for column in columns)
    has_unit = any(column.get("unit_labels") for column in columns)
    if not has_period or not has_unit:
        return "period_unit_gap"
    if "generic_table_semantics" in reasons:
        return "generic_semantics"
    if str((normalized.get("quality") or {}).get("status") or "") == "review_ready":
        return "review_ready"
    return "other_unresolved"


def _consider_benchmark_packet(
    candidates: dict[str, list[tuple[str, dict[str, Any]]]],
    raw: Mapping[str, Any],
    structured: Mapping[str, Any],
    normalized: Mapping[str, Any],
    source_certificate: Mapping[str, Any],
    *,
    per_bucket: int,
) -> None:
    uid = str(normalized["internal_table_uid"])
    bucket = _benchmark_bucket(normalized)
    grid = normalized.get("canonical_grid") or {}
    evidence_graph = build_packet_evidence_graph(
        raw=raw,
        structured=structured,
        normalized=normalized,
    )
    packet = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "packet_type": "phase_3_bakeoff_candidate",
        "internal_table_uid": uid,
        "candidate_bucket": bucket,
        "source_certificate_id": source_certificate["certificate_id"],
        "document": normalized.get("document") or {},
        "source_identity": source_certificate.get("source_identity") or {},
        "quality": normalized.get("quality") or {},
        "outside_table_context": {
            key: (normalized.get("outside_table_context") or {}).get(key)
            for key in ("source_heading", "source_parent_heading", "reader_heading", "source_context_sha256")
        },
        "canonical_columns": [
            {
                key: column.get(key)
                for key in ("column_index", "canonical_label", "period_labels", "unit_labels", "header_source_cells")
            }
            for column in (grid.get("columns") or [])
        ],
        "row_preview": (grid.get("rows") or [])[:8],
        "row_preview_cell_provenance": (grid.get("cell_provenance") or [])[:8],
        "evidence_graph": evidence_graph,
        "training_eligible": False,
    }
    priority = hashlib.sha256(uid.encode("utf-8")).hexdigest()
    selected = candidates[bucket]
    if len(selected) < per_bucket:
        selected.append((priority, packet))
    else:
        worst_index, worst = max(enumerate(selected), key=lambda item: item[1][0])
        if priority < worst[0]:
            selected[worst_index] = (priority, packet)


def _run_mutation_suite(
    raw: Mapping[str, Any],
    structured: Mapping[str, Any],
    context: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> dict[str, Any]:
    """Inject in-memory violations and prove the Phase 1-2 gates see them."""
    baseline_source_failures = _identity_failures(raw, structured, context, normalized)
    baseline_cell_failures, _ = _cell_lineage_failures(structured, normalized)
    if baseline_source_failures or baseline_cell_failures:
        raise CertifiedCanonicalError("mutation suite requires one clean source/cell lineage sample")

    checks: list[dict[str, Any]] = []

    raw_uid = copy.deepcopy(raw)
    raw_uid["internal_table_uid"] = "0" * 64
    observed = _identity_failures(raw_uid, structured, context, normalized)
    checks.append(
        {
            "mutation": "source_uid_mismatch",
            "expected_state": "QUARANTINED",
            "expected_code": "uid_mismatch_structured_v2",
            "observed_codes": observed,
            "passed": "uid_mismatch_structured_v2" in observed,
        }
    )

    normalized_numeric = copy.deepcopy(normalized)
    mutated = False
    for row in (normalized_numeric.get("canonical_grid") or {}).get("rows") or []:
        if not isinstance(row, list):
            continue
        for index, value in enumerate(row):
            if numeric_tokens(str(value or "")):
                row[index] = f"{value} 9"
                mutated = True
                break
        if mutated:
            break
    if not mutated:
        raise CertifiedCanonicalError("mutation sample has no numeric lexeme")
    observed, _ = _cell_lineage_failures(structured, normalized_numeric)
    checks.append(
        {
            "mutation": "numeric_lexeme_change",
            "expected_state": "QUARANTINED",
            "expected_code": "numeric_lexeme_changed",
            "observed_codes": observed,
            "passed": "numeric_lexeme_changed" in observed,
        }
    )

    normalized_anchor = copy.deepcopy(normalized)
    provenance = (normalized_anchor.get("canonical_grid") or {}).get("cell_provenance") or []
    if not provenance or not provenance[0] or not isinstance(provenance[0][0], dict):
        raise CertifiedCanonicalError("mutation sample has no canonical cell provenance")
    provenance[0][0]["anchor_row"] = -1
    observed, _ = _cell_lineage_failures(structured, normalized_anchor)
    checks.append(
        {
            "mutation": "invalid_anchor_coordinate",
            "expected_state": "QUARANTINED",
            "expected_code": "invalid_anchor_coordinate",
            "observed_codes": observed,
            "passed": "invalid_anchor_coordinate" in observed,
        }
    )

    source_certificate = _source_certificate(raw, structured, context, normalized, identity_failures=[])
    cell_failures, metadata = _cell_lineage_failures(structured, normalized)
    cell_certificate = _cell_lineage_certificate(raw, metadata, cell_failures)

    normalized_heading = copy.deepcopy(normalized)
    outside = normalized_heading.setdefault("outside_table_context", {})
    outside["source_heading"] = ""
    outside["reader_heading"] = ""
    heading_assertion = next(
        item
        for item in _build_assertions(
            raw, structured, normalized_heading, source_certificate, cell_certificate
        )
        if item["field"] == "heading_context"
    )
    checks.append(
        {
            "mutation": "remove_heading_anchor",
            "expected_state": "UNRESOLVED",
            "expected_code": "heading_not_source_anchored",
            "observed_codes": heading_assertion["reason_codes"],
            "passed": heading_assertion["status"] == "UNRESOLVED"
            and "heading_not_source_anchored" in heading_assertion["reason_codes"],
        }
    )

    normalized_unit = copy.deepcopy(normalized)
    for column in (normalized_unit.get("canonical_grid") or {}).get("columns") or []:
        column["unit_labels"] = []
    unit_assertion = next(
        item
        for item in _build_assertions(
            raw, structured, normalized_unit, source_certificate, cell_certificate
        )
        if item["field"] == "unit_context"
    )
    checks.append(
        {
            "mutation": "remove_unit_anchor",
            "expected_state": "UNRESOLVED",
            "expected_code": "unit_not_declared",
            "observed_codes": unit_assertion["reason_codes"],
            "passed": unit_assertion["status"] == "UNRESOLVED"
            and "unit_not_declared" in unit_assertion["reason_codes"],
        }
    )
    return {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "sample_internal_table_uid": raw["internal_table_uid"],
        "executed_in_memory_only": True,
        "checks": checks,
        "passed": all(bool(check["passed"]) for check in checks),
    }


def _synthetic_mutation_seed() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """A minimal in-memory seed keeps mutation coverage independent of corpus health."""
    uid = "f" * 64
    raw = {
        "internal_table_uid": uid,
        "document_id": "CCL_MUTATION_SEED",
        "ticker": "SEED",
        "report_year": 2022,
        "scope": "separate",
        "page_no": 1,
        "local_ordinal": 1,
        "context_before": "Bảng cân đối. Đơn vị VND.",
        "rows": [["Chỉ tiêu", "2022 VND"], ["Tiền", "1.000"]],
    }
    provenance = {
        "source_path": "/in-memory/ccl-mutation-seed",
        "source_sha256": "3" * 64,
        "table_sha256": "4" * 64,
        "char_start": 0,
    }
    cell_provenance = [
        [
            {"source_row": 0, "source_cell": 0, "anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
            {"source_row": 0, "source_cell": 1, "anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
        ],
        [
            {"source_row": 1, "source_cell": 0, "anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
            {"source_row": 1, "source_cell": 1, "anchor_row": 1, "anchor_column": 1, "covered_by_span": False},
        ],
    ]
    structured = {
        "internal_table_uid": uid,
        "document_id": raw["document_id"],
        "local_ordinal": 1,
        "structure_version": 2,
        "source_provenance": provenance,
        "rows": raw["rows"],
        "cell_provenance": cell_provenance,
    }
    context = {
        "internal_table_uid": uid,
        "document_id": raw["document_id"],
        "local_ordinal": 1,
        "source_provenance": provenance,
    }
    normalized = {
        "internal_table_uid": uid,
        "document": {
            key: raw[key]
            for key in ("document_id", "ticker", "report_year", "scope", "page_no", "local_ordinal")
        },
        "source_provenance": provenance,
        "source_record_sha256": _sha_json(raw),
        "canonical_grid": {
            "structure_source": "tables_structured_v2",
            "width": 2,
            "rows": copy.deepcopy(raw["rows"]),
            "cell_provenance": copy.deepcopy(cell_provenance),
            "columns": [
                {"column_index": 0, "period_labels": [], "unit_labels": [], "header_source_cells": []},
                {
                    "column_index": 1,
                    "period_labels": ["2022"],
                    "unit_labels": ["VND"],
                    "header_source_cells": [{"row_index": 0, "column_index": 1}],
                },
            ],
        },
        "outside_table_context": {"source_heading": "Bảng cân đối", "reader_heading": "Bảng cân đối"},
        "quality": {"status": "review_ready", "reason_codes": []},
    }
    return raw, structured, context, normalized


def _inventory(inputs: CertifiedCanonicalInputs) -> dict[str, Any]:
    return {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "stage": "phase_0_freeze_and_baseline",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {name: _input_entry(path) for name, path in inputs.required_paths().items()},
        "sidecar_validation": inputs.sidecar_validation,
        "policy": {
            "raw_source_mutation_allowed": False,
            "model_proposals_allowed": False,
            "training_eligible_output_count": 0,
            "promotion_policy": "positive_hash_bound_allowlist_required_in_phase_6",
        },
        "benchmark_policy": {
            "packets_per_bucket": inputs.benchmark_packets_per_bucket,
            "selection": "lowest_sha256_of_internal_table_uid_per_disjoint_bucket",
            "purpose": "phase_3_bakeoff_candidate_only",
            "target_packet_range": [inputs.benchmark_target_min, inputs.benchmark_target_max],
            "target_range_required": inputs.require_benchmark_target_range,
        },
    }


def run_certified_canonical(
    config_path: Path, output_dir: Path
) -> CertifiedCanonicalResult:
    """Build Phase 0-2 certificates in a fresh research-only directory."""
    inputs = load_inputs(config_path)
    _new_output_dir(output_dir, inputs.required_paths().values())
    before_hashes = {name: sha256_file(path) for name, path in inputs.required_paths().items()}
    inventory_path = output_dir / "input_inventory.json"
    inventory_path.write_text(
        json.dumps(_inventory(inputs), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    source_path = output_dir / "source_certificates.jsonl"
    cell_path = output_dir / "cell_lineage_certificates.jsonl"
    assertions_path = output_dir / "semantic_assertions.jsonl"
    issues_path = output_dir / "issue_registry.jsonl"
    results_path = output_dir / "certification_results.jsonl"
    receipts_path = output_dir / "proof_receipts.jsonl"
    benchmark_path = output_dir / "benchmark_packets_v1.jsonl"

    lifecycle_counts: Counter[str] = Counter()
    assertion_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    table_count = 0
    candidates: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    mutation_sample: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None = None

    stream_iterators = (
        _json_lines(inputs.raw_tables),
        _json_lines(inputs.structured_v2),
        _json_lines(inputs.evidence_context_v3),
        _json_lines(inputs.preprocessing_normalized),
    )
    with (
        source_path.open("x", encoding="utf-8") as source_file,
        cell_path.open("x", encoding="utf-8") as cell_file,
        assertions_path.open("x", encoding="utf-8") as assertions_file,
        issues_path.open("x", encoding="utf-8") as issues_file,
        results_path.open("x", encoding="utf-8") as results_file,
        receipts_path.open("x", encoding="utf-8") as receipts_file,
    ):
        for line_number, records in enumerate(zip_longest(*stream_iterators, fillvalue=_SENTINEL), start=1):
            if _SENTINEL in records:
                raise CertifiedCanonicalError(
                    "immutable inputs have different non-empty JSONL record counts"
                )
            raw, structured, context, normalized = records
            assert isinstance(raw, dict)
            assert isinstance(structured, dict)
            assert isinstance(context, dict)
            assert isinstance(normalized, dict)
            uids = [
                _record_uid(record, source_name=name, line_number=line_number)
                for name, record in (
                    ("raw_tables", raw),
                    ("structured_v2", structured),
                    ("evidence_context_v3", context),
                    ("preprocessing_normalized", normalized),
                )
            ]
            if len(set(uids)) != 1:
                raise CertifiedCanonicalError(
                    f"immutable input order/UID mismatch at logical record {line_number}"
                )
            identity_failures = _identity_failures(raw, structured, context, normalized)
            cell_failures, cell_metadata = _cell_lineage_failures(structured, normalized)
            source_certificate = _source_certificate(
                raw, structured, context, normalized, identity_failures=identity_failures
            )
            cell_certificate = _cell_lineage_certificate(raw, cell_metadata, cell_failures)
            assertions = _build_assertions(
                raw, structured, normalized, source_certificate, cell_certificate
            )
            issue_registry = _issue_registry(
                normalized, source_certificate, cell_certificate, assertions
            )
            result = _certification_result(
                uids[0], source_certificate, cell_certificate, assertions, issue_registry
            )
            receipt = _proof_receipt(
                uids[0], source_certificate, cell_certificate, assertions, issue_registry, result
            )
            _write_json_line(source_file, source_certificate)
            _write_json_line(cell_file, cell_certificate)
            for assertion in assertions:
                _write_json_line(assertions_file, assertion)
                assertion_counts[f"{assertion['field']}:{assertion['status']}"] += 1
            _write_json_line(issues_file, issue_registry)
            _write_json_line(results_file, result)
            _write_json_line(receipts_file, receipt)
            lifecycle_counts[str(result["lifecycle_state"])] += 1
            issue_counts[str(issue_registry["primary_issue"]["code"])] += 1
            _consider_benchmark_packet(
                candidates,
                raw,
                structured,
                normalized,
                source_certificate,
                per_bucket=inputs.benchmark_packets_per_bucket,
            )
            if mutation_sample is None and not identity_failures and not cell_failures:
                mutation_sample = (raw, structured, context, normalized)
            table_count += 1

    if table_count == 0:
        raise CertifiedCanonicalError("Certified Canonical Layer cannot certify an empty corpus")
    benchmark_count = 0
    with benchmark_path.open("x", encoding="utf-8") as benchmark_file:
        for bucket in sorted(candidates):
            for _, packet in sorted(candidates[bucket], key=lambda item: item[0]):
                payload = {
                    "benchmark_packet_id": _sha_json(packet),
                    **packet,
                }
                _write_json_line(benchmark_file, payload)
                benchmark_count += 1

    if inputs.require_benchmark_target_range and not (
        inputs.benchmark_target_min <= benchmark_count <= inputs.benchmark_target_max
    ):
        raise CertifiedCanonicalError(
            "benchmark packet count is outside the configured Phase 3 target range: "
            f"{benchmark_count} not in [{inputs.benchmark_target_min}, {inputs.benchmark_target_max}]"
        )

    mutation_report = _run_mutation_suite(*(mutation_sample or _synthetic_mutation_seed()))
    mutation_path = output_dir / "mutation_report.json"
    mutation_path.write_text(
        json.dumps(mutation_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not mutation_report["passed"]:
        raise CertifiedCanonicalError("mutation suite did not detect every modeled corruption")

    after_hashes = {name: sha256_file(path) for name, path in inputs.required_paths().items()}
    if before_hashes != after_hashes:
        changed = sorted(name for name in before_hashes if before_hashes[name] != after_hashes[name])
        raise CertifiedCanonicalError(f"CCL changed immutable input artifacts: {changed}")

    validation = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "run_status": "complete_phase_0_to_2_research_only",
        "table_count": table_count,
        "lifecycle_counts": dict(sorted(lifecycle_counts.items())),
        "primary_issue_counts": dict(sorted(issue_counts.items())),
        "assertion_status_counts": dict(sorted(assertion_counts.items())),
        "benchmark_packet_count": benchmark_count,
        "benchmark_target_range": [inputs.benchmark_target_min, inputs.benchmark_target_max],
        "mutation_suite_passed": True,
        "immutable_input_hashes_unchanged": True,
        "training_eligible_output_count": 0,
        "next_gate": "phase_3_llm_bakeoff_route_selection",
        "not_a_training_dataset": True,
    }
    validation_path = output_dir / "validation_report.json"
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_hashes = {
        name: _input_entry(output_dir / name)
        for name in OUTPUT_NAMES
    }
    manifest = {
        "schema_version": CERTIFIED_CANONICAL_SCHEMA_VERSION,
        "protocol": CERTIFIED_CANONICAL_PROTOCOL,
        "run_status": "complete_phase_0_to_2_research_only",
        "inputs": {name: _input_entry(path) for name, path in inputs.required_paths().items()},
        "outputs": output_hashes,
        "table_count": table_count,
        "lifecycle_counts": dict(sorted(lifecycle_counts.items())),
        "training_eligible": False,
        "promotion_allowed": False,
        "next_gate": "phase_3_llm_bakeoff_route_selection",
    }
    manifest_path = output_dir / "release_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return CertifiedCanonicalResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        table_count=table_count,
        lifecycle_counts=dict(lifecycle_counts),
        benchmark_packet_count=benchmark_count,
    )
