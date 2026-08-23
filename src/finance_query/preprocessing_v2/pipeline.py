"""Preprocessing V2: source-bound canonical tables and review artefacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, TextIO

import yaml

from finance_query.evidence_context import (
    evidence_context_manifest_path,
    validate_evidence_context_sidecar,
)
from finance_query.report_segments import build_report_segment
from finance_query.table_structure import sha256_file, validate_structure_sidecar

from .text_repair import RepairRule, normalize_canonical_text, numeric_tokens


SCHEMA_VERSION = 2
PROTOCOL = "vifinqa_preprocessing_v2"
OUTPUT_NAMES = (
    "normalized_tables_v2.jsonl",
    "repair_ledger_v2.jsonl",
    "quarantine_v2.jsonl",
    "review_samples_v2.jsonl",
    "label_inventory_v1.json",
    "preprocessing_report_v2.json",
    "REVIEW.md",
)


@dataclass(frozen=True, slots=True)
class PreprocessingResult:
    output_dir: Path
    manifest_path: Path
    table_count: int
    status_counts: dict[str, int]
    current_benchmark_label_count: int


def _json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} is not a JSON object")
            yield value


def _write_json_line(file: TextIO, value: Mapping[str, Any]) -> None:
    file.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolve(repo_root: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (repo_root / path).resolve()


def _create_output_dir(path: Path, inputs: Iterable[Path]) -> None:
    resolved = path.resolve()
    if resolved in {item.resolve() for item in inputs}:
        raise ValueError("output-dir cannot be an immutable input path")
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise FileExistsError("output-dir must be new or empty")
    else:
        path.mkdir(parents=True)


class _SidecarIndex:
    """Disk-backed lookup avoids retaining the 400 MB sidecars in memory."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.connection = sqlite3.connect(database)
        self.connection.execute(
            "CREATE TABLE sidecars (kind TEXT, uid TEXT, payload TEXT, PRIMARY KEY(kind, uid))"
        )

    def add_file(self, kind: str, path: Path) -> int:
        count = 0
        batch: list[tuple[str, str, str]] = []
        for value in _json_lines(path):
            uid = str(value.get("internal_table_uid") or "")
            if not uid:
                raise ValueError(f"{path} contains a sidecar without internal_table_uid")
            batch.append((kind, uid, json.dumps(value, ensure_ascii=False)))
            count += 1
            if len(batch) >= 500:
                self.connection.executemany("INSERT INTO sidecars VALUES (?, ?, ?)", batch)
                batch.clear()
        if batch:
            self.connection.executemany("INSERT INTO sidecars VALUES (?, ?, ?)", batch)
        self.connection.commit()
        return count

    def get(self, kind: str, uid: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM sidecars WHERE kind = ? AND uid = ?", (kind, uid)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        self.connection.close()


def _source_conflict(
    raw: Mapping[str, Any], structured: Mapping[str, Any] | None, context: Mapping[str, Any] | None
) -> list[str]:
    reasons: list[str] = []
    raw_uid = str(raw.get("internal_table_uid") or "")
    for name, sidecar in (("v2", structured), ("v3", context)):
        if not sidecar:
            continue
        if str(sidecar.get("internal_table_uid") or "") != raw_uid:
            reasons.append(f"{name}_uid_conflict")
        provenance = sidecar.get("source_provenance") or {}
        for key in ("source_sha256", "table_sha256"):
            raw_value = str(raw.get(key) or "")
            side_value = str(provenance.get(key) or "")
            if raw_value and side_value and raw_value != side_value:
                reasons.append(f"{name}_{key}_conflict")
    return reasons


def _normalize_field(
    value: object,
    *,
    uid: str,
    field_path: str,
    rules: list[RepairRule],
    ledger: list[dict[str, Any]],
    strip_html: bool = False,
    strip_page_markers: bool = False,
) -> tuple[str, list[str]]:
    canonical, events, flags = normalize_canonical_text(
        value,
        rules=rules,
        strip_html=strip_html,
        strip_page_markers=strip_page_markers,
    )
    for event in events:
        ledger.append({"internal_table_uid": uid, "field_path": field_path, **event})
    return canonical, flags


def _fallback_columns(headers: list[str], width: int) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for index in range(width):
        source = headers[index] if index < len(headers) else ""
        label = source or ("Nhãn dòng" if index == 0 else f"Cột nguồn {index + 1}")
        columns.append(
            {
                "column_index": index,
                "source_label": source,
                "canonical_label": label,
                "role": "row_label" if index == 0 else "value_or_text",
                "period_labels": [],
                "unit_labels": [],
                "source": "raw_header_fallback",
            }
        )
    return columns


def _canonical_columns(
    raw: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    width: int,
    rules: list[RepairRule],
    ledger: list[dict[str, Any]],
    uid: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    reasons: list[str] = []
    supplied = ((context or {}).get("canonical_headers") or {}).get("columns") or []
    if supplied:
        columns = [dict(value) for value in supplied]
        source = "evidence_context_v3"
    else:
        columns = _fallback_columns([str(v) for v in raw.get("headers") or []], width)
        source = "raw_header_fallback"
        reasons.extend(("missing_evidence_context_v3", "flatten_unresolved_without_v3"))

    while len(columns) < width:
        index = len(columns)
        columns.append(
            {
                "column_index": index,
                "source_label": "",
                "canonical_label": f"Cột nguồn {index + 1}",
                "role": "value_or_text",
                "period_labels": [],
                "unit_labels": [],
                "source": "safe_width_padding",
            }
        )
        reasons.append("generic_column_header")
    if len(columns) > width:
        reasons.append("header_width_exceeds_grid")
        width = len(columns)

    labels: list[str] = []
    for index, column in enumerate(columns):
        original = str(column.get("source_label") or column.get("canonical_label") or "")
        label, flags = _normalize_field(
            original,
            uid=uid,
            field_path=f"canonical_headers.columns[{index}]",
            rules=rules,
            ledger=ledger,
        )
        reasons.extend(flags)
        if not label:
            if index == 0 or str(column.get("role") or "") == "row_label":
                # A blank top-left source header is the normal accounting-table
                # shape; this display label does not invent financial meaning.
                label = "Nhãn dòng"
            else:
                label = f"Cột nguồn {index + 1}"
                reasons.append("generic_column_header")
        column["column_index"] = index
        column["canonical_label"] = label
        column["source"] = source if "source" not in column else column["source"]
        labels.append(label.casefold())
    nonempty = [label for label in labels if label]
    if len(nonempty) != len(set(nonempty)):
        reasons.append("duplicate_canonical_headers")
    return columns, sorted(set(reasons))


def _normalize_table(
    raw: Mapping[str, Any],
    structured: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    rules: list[RepairRule],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    uid = str(raw.get("internal_table_uid") or "")
    ledger: list[dict[str, Any]] = []
    severe = _source_conflict(raw, structured, context)
    reasons: list[str] = []
    if not uid:
        severe.append("missing_internal_table_uid")

    source_rows = (structured or {}).get("rows") or raw.get("rows") or []
    if not source_rows:
        severe.append("missing_rows")
    if structured is None:
        reasons.extend(("missing_structure_v2", "flatten_unresolved_without_v2"))
    if context is None:
        reasons.append("missing_evidence_context_v3")

    raw_widths = [len(row) for row in source_rows if isinstance(row, list)]
    v3_width = int(((context or {}).get("grid") or {}).get("width") or 0)
    width = max(raw_widths + [len(raw.get("headers") or []), v3_width, 0])
    if len(set(raw_widths)) > 1:
        reasons.append("irregular_grid_width")

    rows: list[list[str]] = []
    unresolved_flags: list[str] = []
    for row_index, source_row in enumerate(source_rows):
        if not isinstance(source_row, list):
            severe.append("non_list_source_row")
            continue
        canonical_row: list[str] = []
        for column_index, cell in enumerate(source_row):
            canonical, flags = _normalize_field(
                cell,
                uid=uid,
                field_path=f"rows[{row_index}][{column_index}]",
                rules=rules,
                ledger=ledger,
            )
            if numeric_tokens(str(cell or "")) != numeric_tokens(canonical):
                severe.append("numeric_token_mutation")
            unresolved_flags.extend(flags)
            canonical_row.append(canonical)
        if len(canonical_row) < width:
            before = list(canonical_row)
            canonical_row.extend([""] * (width - len(canonical_row)))
            ledger.append(
                {
                    "internal_table_uid": uid,
                    "field_path": f"rows[{row_index}]",
                    "repair_type": "safe_trailing_grid_padding",
                    "before_sha256": _sha256_json(before),
                    "after_sha256": _sha256_json(canonical_row),
                    "before_excerpt": json.dumps(before, ensure_ascii=False)[:180],
                    "after_excerpt": json.dumps(canonical_row, ensure_ascii=False)[:180],
                    "confidence": 1.0,
                    "canonical_only": True,
                }
            )
            reasons.append("grid_trailing_padding")
        rows.append(canonical_row)

    columns, column_reasons = _canonical_columns(raw, context, width, rules, ledger, uid)
    reasons.extend(column_reasons)
    if len(columns) > width:
        width = len(columns)
        for row in rows:
            row.extend([""] * (width - len(row)))
        reasons.append("grid_trailing_padding")

    canonical_context, context_flags = _normalize_field(
        raw.get("context_before"),
        uid=uid,
        field_path="context_before",
        rules=rules,
        ledger=ledger,
        strip_html=True,
        strip_page_markers=True,
    )
    reasons.extend(context_flags)
    reasons.extend(unresolved_flags)
    segment = build_report_segment(raw, context or structured or {})
    trace = (context or {}).get("context_trace") or (structured or {}).get("context_trace") or {}
    function = (context or {}).get("table_function") or (structured or {}).get("table_function") or {}
    section = (context or {}).get("table_section") or (structured or {}).get("table_section") or {}
    row_paths = [str(value) for value in raw.get("row_paths") or []]
    canonical_row_paths: list[str] = []
    for index, value in enumerate(row_paths):
        normalized, flags = _normalize_field(
            value,
            uid=uid,
            field_path=f"row_paths[{index}]",
            rules=rules,
            ledger=ledger,
        )
        canonical_row_paths.append(normalized)
        reasons.extend(flags)

    if not segment.get("source_heading") and not trace.get("topic", {}).get("label"):
        reasons.append("ambiguous_table_heading")
    if str(function.get("specificity") or "") == "generic" or str(section.get("kind") or "") == "unknown":
        reasons.append("generic_table_semantics")
    severe = sorted(set(severe))
    reasons = sorted(set(reason for reason in reasons if reason not in severe))
    status = "quarantined" if severe else ("needs_review" if reasons else "review_ready")

    structured_provenance = (structured or {}).get("source_provenance") or {}
    record = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "internal_table_uid": uid,
        "document": {
            "document_id": raw.get("document_id"),
            "ticker": raw.get("ticker"),
            "report_year": raw.get("report_year"),
            "scope": raw.get("scope"),
            "page_no": raw.get("page_no"),
            "local_ordinal": raw.get("local_ordinal"),
        },
        "source_provenance": {
            key: raw.get(key) if raw.get(key) is not None else structured_provenance.get(key)
            for key in (
                "source_path",
                "source_sha256",
                "table_sha256",
                "char_start",
                "char_end",
                "byte_start",
                "byte_end",
            )
        },
        "source_record_sha256": _sha256_json(raw),
        "canonical_grid": {
            "width": width,
            "row_count": len(rows),
            "rows": rows,
            "columns": columns,
            "row_paths": canonical_row_paths,
            "cell_provenance": (structured or {}).get("cell_provenance"),
            "structure_source": "tables_structured_v2" if structured else "raw_fallback",
        },
        "outside_table_context": {
            "document_id": raw.get("document_id"),
            "ticker": raw.get("ticker"),
            "report_year": raw.get("report_year"),
            "scope": raw.get("scope"),
            "source_heading": segment.get("source_heading"),
            "source_parent_heading": segment.get("source_parent_heading"),
            "reader_heading": segment.get("reader_heading"),
            "canonical_context_excerpt": canonical_context[-1200:],
            "source_context_sha256": segment.get("source_context_sha256"),
        },
        "inside_table_context": {
            "table_function": function,
            "table_section": section,
            "topic": trace.get("topic") or {},
            "period_labels": segment.get("period_labels") or trace.get("period_labels") or [],
            "unit_labels": segment.get("unit_labels") or trace.get("unit_labels") or [],
            "compact_descriptor": segment.get("compact_descriptor"),
            "row_path_policy": "source_order_hierarchical_labels",
            "column_path_policy": "v3_source_cells_or_generic_fail_closed",
        },
        "model_view": {
            "semantic_prefix": " · ".join(
                str(value)
                for value in (
                    segment.get("reader_heading") or segment.get("source_heading"),
                    segment.get("compact_descriptor"),
                )
                if value
            ),
            "rendering_policy": "outside_context_then_hierarchical_columns_then_rows",
            "generated_passage": False,
        },
        "quality": {
            "status": status,
            "reason_codes": reasons,
            "quarantine_reason_codes": severe,
            "repair_count": len(ledger),
            "has_structure_v2": structured is not None,
            "has_context_v3": context is not None,
        },
        "evidence_eligible": False,
        "training_eligible": False,
    }
    return record, ledger


def _label_file_stats(path: Path) -> dict[str, Any]:
    rows = list(_json_lines(path))
    ids = [
        str(
            value.get("id")
            or value.get("question_id")
            or value.get("example_id")
            or value.get("curriculum_id")
            or ""
        )
        for value in rows
    ]
    statuses = Counter(str(value.get("annotation_status") or "unspecified") for value in rows)
    eligible = sum(
        bool(value.get("training_eligible"))
        or bool((value.get("machine_self_review") or {}).get("training_eligible"))
        for value in rows
    )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "row_count": len(rows),
        "unique_id_count": len({value for value in ids if value}),
        "ids": sorted({value for value in ids if value}),
        "annotation_status_counts": dict(sorted(statuses.items())),
        "human_verified_record_count": sum(bool(value.get("human_verified")) for value in rows),
        "training_eligible_record_count": eligible,
    }


def _build_label_inventory(repo_root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    active_ids: set[str] = set()
    for name, specification in (config.get("labels") or {}).items():
        path = _resolve(repo_root, specification["path"])
        required = bool(specification.get("required", True))
        if not path.is_file():
            if required:
                raise FileNotFoundError(path)
            groups[name] = {"path": str(path), "missing": True, "role": specification.get("role")}
            continue
        stats = _label_file_stats(path)
        stats["role"] = specification.get("role")
        if specification.get("active_benchmark_supervision"):
            active_ids.update(stats.pop("ids"))
        else:
            stats.pop("ids")
        groups[name] = stats
    return {
        "schema_version": 1,
        "policy": "pin_active_exports_and_never_sum_historical_snapshots",
        "current_benchmark_supervision_unique_id_count": len(active_ids),
        "groups": groups,
    }


def _review_projection(record: Mapping[str, Any], trigger: str) -> dict[str, Any]:
    grid = record["canonical_grid"]
    return {
        "internal_table_uid": record["internal_table_uid"],
        "trigger_reason": trigger,
        "quality": record["quality"],
        "document": record["document"],
        "outside_table_context": record["outside_table_context"],
        "inside_table_context": record["inside_table_context"],
        "column_labels": [column["canonical_label"] for column in grid["columns"]],
        "row_preview": grid["rows"][:8],
        "source_provenance": record["source_provenance"],
        "training_eligible": False,
    }


def _review_markdown(report: Mapping[str, Any], labels: Mapping[str, Any]) -> str:
    counts = report["status_counts"]
    reasons = report["top_reason_codes"][:15]
    lines = [
        "# Preprocessing V2 review checkout",
        "",
        "This folder is a canonical review view. Raw source files remain immutable.",
        "No record produced here is training-eligible until a separate data gate promotes it.",
        "",
        "## Run summary",
        "",
        f"- Tables processed: {report['table_count']}",
        f"- Review ready: {counts.get('review_ready', 0)}",
        f"- Needs review: {counts.get('needs_review', 0)}",
        f"- Quarantined: {counts.get('quarantined', 0)}",
        f"- Current benchmark supervision labels: {labels['current_benchmark_supervision_unique_id_count']}",
        "",
        "## Most common reason codes",
        "",
    ]
    lines.extend(f"- `{item['reason']}`: {item['count']}" for item in reasons)
    lines.extend(
        [
            "",
            "## Review order",
            "",
            "1. Inspect `quarantine_v2.jsonl`; source conflicts or numeric mutation are hard blockers.",
            "2. Inspect `review_samples_v2.jsonl` by reason code; approve new OCR rules only with source evidence.",
            "3. Re-run into a new folder and compare manifests; never edit generated JSONL in place.",
            "4. Start fine-tuning only after the canonical data gate records an approved subset and its hashes.",
            "",
            "## Fine-tune V2 gate plan",
            "",
            "1. Freeze a promoted dataset manifest; separate issuer-held-out train/dev/test splits.",
            "2. Train with duplicate-free batches and balanced hard-negative families.",
            "3. Run embedding-variance, positive-margin and small-canary checks after every checkpoint.",
            "4. Compare the candidate against the unchanged base model through the serving renderer and top-10 pipeline.",
            "5. Promote only if issuer-held-out Recall@10/NDCG improve without grounding regressions.",
            "",
        ]
    )
    return "\n".join(lines)


def run_preprocessing(config_path: Path, output_dir: Path) -> PreprocessingResult:
    config_path = config_path.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if config.get("protocol") != PROTOCOL or int(config.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError("Unsupported preprocessing config protocol/schema")
    repo_root = config_path.parent.parent.resolve()
    inputs_config = config.get("inputs") or {}
    table_assets = _resolve(repo_root, inputs_config["table_assets"])
    structured_path = _resolve(repo_root, inputs_config["structured_v2"])
    context_path = _resolve(repo_root, inputs_config["evidence_context_v3"])
    input_paths = [config_path, table_assets, structured_path, context_path]
    for path in input_paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    validation: dict[str, Any] = {"enabled": False}
    if bool(config.get("validate_sidecar_manifests", True)):
        structure_manifest_path = structured_path.with_name("table_structure_v2.manifest.json")
        context_manifest_path = evidence_context_manifest_path(context_path)
        structure_manifest = validate_structure_sidecar(table_assets.parent, structured_path)
        context_manifest = validate_evidence_context_sidecar(
            table_assets.parent, structured_path, context_path
        )
        input_paths.extend((structure_manifest_path, context_manifest_path))
        validation = {
            "enabled": True,
            "structure_version": structure_manifest.get("structure_version"),
            "evidence_context_version": context_manifest.get("evidence_context_version"),
            "structure_manifest_sha256": sha256_file(structure_manifest_path),
            "evidence_context_manifest_sha256": sha256_file(context_manifest_path),
        }
    _create_output_dir(output_dir, input_paths)

    rules = [RepairRule.from_mapping(value) for value in config.get("text_repairs") or []]
    index_path = output_dir / ".sidecar_index.sqlite3"
    index = _SidecarIndex(index_path)
    sidecar_counts = {
        "structured_v2": index.add_file("v2", structured_path),
        "evidence_context_v3": index.add_file("v3", context_path),
    }
    joined_sidecar_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    table_count = 0
    ledger_count = 0
    repair_type_counts: Counter[str] = Counter()
    ocr_rule_counts: Counter[str] = Counter()
    sample_limit = int(config.get("review_sample_per_reason") or 4)
    sampled: defaultdict[str, int] = defaultdict(int)

    normalized_path = output_dir / "normalized_tables_v2.jsonl"
    ledger_path = output_dir / "repair_ledger_v2.jsonl"
    quarantine_path = output_dir / "quarantine_v2.jsonl"
    sample_path = output_dir / "review_samples_v2.jsonl"
    try:
        with (
            normalized_path.open("x", encoding="utf-8") as normalized_file,
            ledger_path.open("x", encoding="utf-8") as ledger_file,
            quarantine_path.open("x", encoding="utf-8") as quarantine_file,
            sample_path.open("x", encoding="utf-8") as sample_file,
        ):
            for raw in _json_lines(table_assets):
                uid = str(raw.get("internal_table_uid") or "")
                structured = index.get("v2", uid)
                context = index.get("v3", uid)
                if structured is not None:
                    joined_sidecar_counts["structured_v2"] += 1
                if context is not None:
                    joined_sidecar_counts["evidence_context_v3"] += 1
                record, ledger = _normalize_table(raw, structured, context, rules)
                _write_json_line(normalized_file, record)
                for event in ledger:
                    _write_json_line(ledger_file, event)
                    repair_type_counts[str(event.get("repair_type") or "unknown")] += 1
                    if event.get("rule_id"):
                        ocr_rule_counts[str(event["rule_id"])] += 1
                ledger_count += len(ledger)
                table_count += 1
                status = str(record["quality"]["status"])
                status_counts[status] += 1
                all_reasons = record["quality"]["quarantine_reason_codes"] + record["quality"]["reason_codes"]
                for reason in all_reasons:
                    reason_counts[reason] += 1
                    if sampled[reason] < sample_limit:
                        _write_json_line(sample_file, _review_projection(record, reason))
                        sampled[reason] += 1
                if status == "quarantined":
                    _write_json_line(quarantine_file, _review_projection(record, status))
    finally:
        index.close()
        index_path.unlink(missing_ok=True)

    label_inventory = _build_label_inventory(repo_root, config)
    label_path = output_dir / "label_inventory_v1.json"
    label_path.write_text(
        json.dumps(label_inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "table_count": table_count,
        "status_counts": dict(sorted(status_counts.items())),
        "repair_ledger_event_count": ledger_count,
        "repair_type_counts": dict(sorted(repair_type_counts.items())),
        "allowlisted_ocr_rule_counts": dict(sorted(ocr_rule_counts.items())),
        "sidecar_file_record_counts": sidecar_counts,
        "joined_sidecar_counts": dict(sorted(joined_sidecar_counts.items())),
        "joined_sidecar_coverage": {
            key: round(joined_sidecar_counts[key] / table_count, 6) if table_count else 0.0
            for key in sidecar_counts
        },
        "sidecar_manifest_validation": validation,
        "top_reason_codes": [
            {"reason": reason, "count": count}
            for reason, count in reason_counts.most_common()
        ],
        "data_gate": {
            "status": "blocked_pending_review",
            "training_eligible_output_count": 0,
            "policy": "promotion_requires_separate_hash-bound_approval",
        },
    }
    report_path = output_dir / "preprocessing_report_v2.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "REVIEW.md").write_text(
        _review_markdown(report, label_inventory), encoding="utf-8"
    )

    output_hashes = {
        name: {"sha256": sha256_file(output_dir / name), "bytes": (output_dir / name).stat().st_size}
        for name in OUTPUT_NAMES
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
        "inputs": {
            str(path): {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in input_paths
            if path != config_path
        },
        "outputs": output_hashes,
        "table_count": table_count,
        "status_counts": dict(sorted(status_counts.items())),
        "evidence_eligible": False,
        "training_eligible": False,
    }
    manifest_path = output_dir / "preprocessing_manifest_v2.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return PreprocessingResult(
        output_dir=output_dir,
        manifest_path=manifest_path,
        table_count=table_count,
        status_counts=dict(status_counts),
        current_benchmark_label_count=int(
            label_inventory["current_benchmark_supervision_unique_id_count"]
        ),
    )
