"""Hash-bound diagnostic workbench for exact-cell binding conflicts."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core.exact_cell_bindings_v2 import resolve_source_unit


PROTOCOL = "vifinqa_binding_conflict_workbench_v1"
DECISION_PROTOCOL = "vifinqa_binding_conflict_human_decision_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_contract() -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "eligible_for_materialization": False,
        "may_select_value": False,
        "may_execute_formula": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }


def _rows(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _index(path: Path, label: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in _rows(path):
        question_id = row.get("question_id")
        if not isinstance(question_id, int) or question_id in result:
            raise ValueError(f"{label} requires unique integer question_id")
        result[question_id] = row
    return result


def _uid_index(path: Path, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _rows(path):
        uid = str(row.get("internal_table_uid") or "")
        if not uid or uid in result:
            raise ValueError(f"{label} requires unique internal_table_uid")
        result[uid] = row
    return result


def context_unit_anchor_candidate(
    table: Mapping[str, Any], evidence_context: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return one source-title unit candidate without making it executable."""

    if (
        table.get("internal_table_uid") != evidence_context.get("internal_table_uid")
        or table.get("document_id") != evidence_context.get("document_id")
    ):
        return None
    table_provenance = table.get("source_provenance") or {}
    context_provenance = evidence_context.get("source_provenance") or {}
    for field in ("source_sha256", "table_sha256"):
        value = str(table_provenance.get(field) or "")
        if len(value) != 64 or value != str(context_provenance.get(field) or ""):
            return None
    trace = evidence_context.get("context_trace") or {}
    title = str(trace.get("source_title") or "").strip()
    unit_labels = [str(value).strip() for value in trace.get("unit_labels") or [] if str(value).strip()]
    if not title or len(set(unit_labels)) != 1:
        return None
    source_unit, _, multiplier = resolve_source_unit([{"raw_source_cell": title}])
    label = unit_labels[0]
    if multiplier is None or source_unit is None or label.casefold() not in title.casefold():
        return None
    return {
        "anchor_kind": "evidence_context_source_title",
        "document_id": table.get("document_id"),
        "internal_table_uid": table.get("internal_table_uid"),
        "source_sha256": table_provenance.get("source_sha256"),
        "table_sha256": table_provenance.get("table_sha256"),
        "source_title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest(),
        "evidence_context_row_sha256": canonical_sha256(evidence_context),
        "raw_unit_label": label,
        "source_unit": source_unit,
        "source_to_vnd_multiplier": format(multiplier, "f"),
        "candidate_status": "requires_reviewed_contract_extension",
        "source_contract": source_contract(),
    }


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_binding_conflict_workbench(
    *,
    execution: Path,
    execution_manifest: Path,
    bindings: Path,
    bindings_manifest: Path,
    period_packets: Path,
    period_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite binding workbench: {output_dir}")
    manifests = {
        "execution": json.loads(execution_manifest.read_text()),
        "bindings": json.loads(bindings_manifest.read_text()),
        "period": json.loads(period_manifest.read_text()),
        "context": json.loads(evidence_context_manifest.read_text()),
    }
    checks = (
        (((manifests["execution"].get("outputs") or {}).get("execution") or {}).get("sha256"), execution, "execution"),
        (((manifests["bindings"].get("outputs") or {}).get("bindings") or {}).get("sha256"), bindings, "bindings"),
        (((manifests["period"].get("outputs") or {}).get("period_packets") or {}).get("sha256"), period_packets, "period packets"),
        (((manifests["bindings"].get("inputs") or {}).get("structured_tables") or {}).get("sha256"), structured_tables, "structured tables"),
        (manifests["context"].get("sidecar_sha256"), evidence_context, "evidence context"),
    )
    for expected, path, label in checks:
        if not isinstance(expected, str) or expected != sha256_file(path):
            raise ValueError(f"SHA-256 mismatch for {label}")
    if manifests["context"].get("input_structure_sha256") != sha256_file(structured_tables):
        raise ValueError("evidence context is stale for structured tables")
    execution_rows = _index(execution, "execution")
    binding_rows = _index(bindings, "bindings")
    period_rows = _index(period_packets, "period packets")
    if set(execution_rows) != set(binding_rows) or set(execution_rows) != set(period_rows):
        raise ValueError("binding workbench inputs do not cover the same questions")
    tables = _uid_index(structured_tables, "structured tables")
    contexts = _uid_index(evidence_context, "evidence context")

    workbench: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    classes = Counter()
    unit_candidate_count = 0
    for question_id in sorted(execution_rows):
        if execution_rows[question_id].get("execution_status") != "binding_conflict":
            continue
        binding, period = binding_rows[question_id], period_rows[question_id]
        operands = [
            operand
            for stage in binding.get("stages") or []
            if isinstance(stage, Mapping)
            for operand in stage.get("required_operands") or []
            if isinstance(operand, Mapping)
        ]
        reasons = sorted({str(reason) for operand in operands for reason in operand.get("reason_codes") or []})
        period_status = str(period.get("packet_status") or "")
        classification = {
            "packet_blocked": "upstream_navigation_candidate_required",
            "no_period_column": "exact_period_header_review_required",
            "ambiguous_period_columns": "period_disambiguation_required",
        }.get(period_status, "exact_unit_anchor_review_required")
        classes[classification] += 1
        unit_candidates = []
        for operand in operands:
            if "SOURCE_UNIT_ANCHOR_MISSING_OR_CONFLICTING" not in (operand.get("reason_codes") or []):
                continue
            uid = str(operand.get("internal_table_uid") or "")
            if uid in tables and uid in contexts:
                candidate = context_unit_anchor_candidate(tables[uid], contexts[uid])
                if candidate is not None:
                    unit_candidates.append(candidate)
        unit_candidate_count += len(unit_candidates)
        payload = {
            "question_id": question_id,
            "classification": classification,
            "binding_reason_codes": reasons,
            "period_packet_status": period_status,
            "question_context": binding.get("question_context") or {},
            "operand_coordinates": [
                {
                    "stage_id": operand.get("stage_id"),
                    "role": operand.get("role"),
                    "internal_table_uid": operand.get("internal_table_uid"),
                    "row_index": operand.get("row_index"),
                    "column_index": operand.get("column_index"),
                    "binding_status": operand.get("binding_status"),
                    "reason_codes": list(operand.get("reason_codes") or []),
                }
                for operand in operands
            ],
            "source_title_unit_candidates": unit_candidates,
            "binding_row_sha256": canonical_sha256(binding),
            "period_row_sha256": canonical_sha256(period),
        }
        item = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            **payload,
            "workbench_item_sha256": canonical_sha256(payload),
            "source_contract": source_contract(),
        }
        workbench.append(item)
        decisions.append(
            {
                "schema_version": 1,
                "protocol": DECISION_PROTOCOL,
                "question_id": question_id,
                "workbench_item_sha256": item["workbench_item_sha256"],
                "decision": None,
                "approved_repair": None,
                "source_coordinates_checked": False,
                "decision_provenance": None,
                "reviewer_id": None,
                "reviewed_at": None,
                "notes": "",
                "is_blank_template": True,
                "source_contract": source_contract(),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=False)
    workbench_path = output_dir / "binding_conflict_workbench_v1.jsonl"
    decisions_path = output_dir / "binding_conflict_human_decision_template_v1.jsonl"
    _write_jsonl(workbench_path, workbench)
    _write_jsonl(decisions_path, decisions)
    input_paths = {
        "execution": execution,
        "execution_manifest": execution_manifest,
        "bindings": bindings,
        "bindings_manifest": bindings_manifest,
        "period_packets": period_packets,
        "period_manifest": period_manifest,
        "structured_tables": structured_tables,
        "evidence_context": evidence_context,
        "evidence_context_manifest": evidence_context_manifest,
    }
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "binding_conflicts_classified_not_repaired",
        "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in input_paths.items()},
        "outputs": {
            "workbench": {"path": str(workbench_path), "sha256": sha256_file(workbench_path)},
            "blank_decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)},
        },
        "counts": {
            "binding_conflict_count": len(workbench),
            "classification_counts": dict(sorted(classes.items())),
            "source_title_unit_candidate_count": unit_candidate_count,
            "human_decision_count": 0,
        },
        "source_contract": source_contract(),
    }
    manifest_path = output_dir / "binding_conflict_workbench_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {**result, "manifest_path": str(manifest_path)}
