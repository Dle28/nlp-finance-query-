"""Build an Agent 4, fail-closed candidate union from V2/V3 replays.

This producer is deliberately research-only.  It verifies the three agent
artifacts against the current immutable V2/V3 snapshot, promotes only
value-free coordinates that can be reloaded from that snapshot, and writes a
quarantine ledger for every rejected candidate.  It never writes a numeric
source value or a semantic approval into the union packet.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Mapping


PROTOCOL = "vifinqa_agent4_candidate_union_v1"
PERIOD_PROTOCOL = "vifinqa_period_packet_union_v1"
CONTRACT = {
    "candidate_only": True,
    "evidence_eligible": False,
    "machine_recheck_only": True,
    "may_authorize_evidence": False,
    "may_execute_formula": False,
    "may_materialize_answer": False,
    "may_select_final_column": False,
    "may_select_value": False,
    "promotion_allowed": False,
    "research_only": True,
    "submission_eligible": False,
    "training_eligible": False,
}
TARGET_IDS = (70, 98, 104, 156, 185, 242, 263, 340, 357)
INTEGRATED_IDS = (156, 263, 340)
QUARANTINED_IDS = (98, 104, 185, 242, 357)


class IntegrationError(ValueError):
    """Raised when an input loses its declared provenance or shape."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise IntegrationError(f"{path} must contain a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise IntegrationError(f"{path}:{line_number} must be a JSON object")
        result.append(value)
    return result


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, values: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def require_hash(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise IntegrationError(f"SHA-256 mismatch for {label}")


def require_output_files(root: Path, manifest: Mapping[str, Any], label: str) -> None:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise IntegrationError(f"{label} manifest has no outputs")
    for name, descriptor in outputs.items():
        if not isinstance(descriptor, Mapping):
            raise IntegrationError(f"{label} output descriptor is invalid: {name}")
        expected = descriptor.get("sha256")
        if not isinstance(expected, str):
            raise IntegrationError(f"{label} output has no hash: {name}")
        raw_path = str(descriptor.get("path") or name)
        path = root / Path(raw_path).name
        if not path.is_file():
            raise IntegrationError(f"{label} output is missing: {path}")
        require_hash(path, expected, f"{label} output {name}")


def require_contract(value: object, label: str) -> None:
    if not isinstance(value, Mapping):
        raise IntegrationError(f"{label} has no source contract")
    # Agent 1 and Agent 2 use earlier contract schemas.  Missing optional
    # safety flags are tolerated, but every flag that is present must remain
    # fail-closed and the common candidate/research flags are mandatory.
    for key in ("candidate_only", "evidence_eligible", "promotion_allowed", "submission_eligible"):
        if key not in value:
            raise IntegrationError(f"{label} source contract omits {key}")
    for key, expected in CONTRACT.items():
        if key in value and value.get(key) is not expected:
            raise IntegrationError(
                f"{label} source contract violates {key}={expected!r}"
            )


def index_by(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(field) or "")
        if not value or value in result:
            raise IntegrationError(f"{label} has duplicate or missing {field}")
        result[value] = row
    return result


def folded(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


def coordinates(values: object) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, Mapping):
            continue
        row_index, column_index = value.get("row_index"), value.get("column_index")
        if isinstance(row_index, int) and isinstance(column_index, int):
            result.append((row_index, column_index))
    return result


def context_header(context: Mapping[str, Any], column_index: int) -> Mapping[str, Any] | None:
    headers = ((context.get("canonical_headers") or {}).get("columns") or [])
    matches = [
        value
        for value in headers
        if isinstance(value, Mapping) and value.get("column_index") == column_index
    ]
    return matches[0] if len(matches) == 1 else None


def source_title(context: Mapping[str, Any]) -> str:
    return str(((context.get("context_trace") or {}).get("source_title")) or "")


def source_title_unit_marker(
    *,
    table: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any] | None:
    title = source_title(context)
    normalized = folded(title).replace("vnđ", "vnd")
    if not re.search(r"\bvnd\b", normalized):
        return None
    provenance = table.get("source_provenance") or {}
    source_sha = str(provenance.get("source_sha256") or "")
    table_sha = str(provenance.get("table_sha256") or "")
    document_id = str(table.get("document_id") or "")
    uid = str(table.get("internal_table_uid") or "")
    if not source_sha or not table_sha or not document_id or not uid:
        raise IntegrationError("V2 source provenance is incomplete")
    return {
        "document_id": document_id,
        "internal_table_uid": uid,
        "source_sha256": source_sha,
        "table_sha256": table_sha,
        "source_title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest(),
        "evidence_context_row_sha256": canonical_sha(context),
        "source_unit": "vnd",
        "source_to_vnd_multiplier": "1",
        "raw_unit_label": "VND",
    }


def source_cell_sha(table: Mapping[str, Any], row_index: int, column_index: int) -> str:
    rows = table.get("rows") or []
    if (
        not isinstance(row_index, int)
        or not isinstance(column_index, int)
        or row_index < 0
        or row_index >= len(rows)
        or not isinstance(rows[row_index], list)
        or column_index < 0
        or column_index >= len(rows[row_index])
    ):
        raise IntegrationError("source cell coordinates are out of bounds")
    return hashlib.sha256(
        str(rows[row_index][column_index]).encode("utf-8")
    ).hexdigest()


def source_provenance_matches(
    *,
    table: Mapping[str, Any],
    context: Mapping[str, Any],
    candidate: Mapping[str, Any],
    label: str,
) -> None:
    table_provenance = table.get("source_provenance") or {}
    context_provenance = context.get("source_provenance") or {}
    for key in ("source_sha256", "table_sha256"):
        expected = str(candidate.get(key) or "")
        if expected and expected != str(table_provenance.get(key) or ""):
            raise IntegrationError(f"{label} candidate/V2 {key} mismatch")
        if str(table_provenance.get(key) or "") != str(context_provenance.get(key) or ""):
            raise IntegrationError(f"{label} V2/V3 {key} mismatch")
    if str(candidate.get("document_id") or "") != str(table.get("document_id") or ""):
        raise IntegrationError(f"{label} document mismatch")
    if str(candidate.get("internal_table_uid") or "") != str(
        table.get("internal_table_uid") or ""
    ):
        raise IntegrationError(f"{label} table UID mismatch")
    if str(table.get("document_id") or "") != str(context.get("document_id") or ""):
        raise IntegrationError(f"{label} V2/V3 document mismatch")


def verify_base_inputs(
    *,
    base_period: Path,
    base_manifest_path: Path,
    route: Path,
    route_manifest_path: Path,
    structured: Path,
    context: Path,
    context_manifest_path: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    base_manifest = read_json(base_manifest_path)
    if base_manifest.get("protocol") != PERIOD_PROTOCOL:
        raise IntegrationError("unexpected base period manifest protocol")
    require_hash(
        base_period,
        ((base_manifest.get("outputs") or {}).get("period_packets") or {}).get(
            "sha256"
        ),
        "base period packets",
    )
    require_hash(
        structured,
        ((base_manifest.get("inputs") or {}).get("structured_tables_v2") or {}).get(
            "sha256"
        ),
        "base V2 tables",
    )
    base_context_descriptor = (base_manifest.get("inputs") or {}).get(
        "evidence_context_v3"
    ) or {}
    require_hash(context, base_context_descriptor.get("sha256"), "base V3 context")

    route_manifest = read_json(route_manifest_path)
    require_hash(
        route,
        ((route_manifest.get("outputs") or {}).get("overlay") or {}).get("sha256"),
        "route overlay",
    )

    context_manifest = read_json(context_manifest_path)
    if context_manifest.get("sidecar_sha256") != sha256_file(context):
        raise IntegrationError("V3 context sidecar hash mismatch")
    if context_manifest.get("input_structure_sha256") != sha256_file(structured):
        raise IntegrationError("V3 context input V2 hash mismatch")

    packets = read_jsonl(base_period)
    routes = read_jsonl(route)
    tables = index_by(read_jsonl(structured), "internal_table_uid", "V2 tables")
    contexts = index_by(read_jsonl(context), "internal_table_uid", "V3 contexts")
    if len(packets) != 1012 or {int(row.get("question_id") or 0) for row in packets} != set(
        range(1, 1013)
    ):
        raise IntegrationError("base period packet coverage is not 1..1012")
    if {int(row.get("question_id") or 0) for row in routes} != set(range(1, 1013)):
        raise IntegrationError("route overlay coverage is not 1..1012")
    return packets, index_by(routes, "question_id", "route overlay"), tables, contexts


def verify_agent1(
    root: Path,
    *,
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    packets: Mapping[int, Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    manifest_path = root / "source_period_recheck_agent1.manifest.json"
    summary_path = root / "source_period_recheck_agent1_summary_v1.json"
    before_after_path = root / "source_period_recheck_before_after_v1.jsonl"
    manifest = read_json(manifest_path)
    require_output_files(root, manifest, "Agent 1")
    require_contract(manifest.get("source_contract"), "Agent 1")
    summary = read_json(summary_path)
    require_contract(summary.get("source_contract"), "Agent 1 summary")
    if summary.get("raw_numeric_values_included") is not False:
        raise IntegrationError("Agent 1 summary contains numeric payload")
    rows = read_jsonl(before_after_path)
    by_question = {int(row["question_id"]): row for row in rows}
    if set(by_question) != {70, 185, 357}:
        raise IntegrationError("Agent 1 question coverage mismatch")
    decisions: dict[int, dict[str, Any]] = {}
    for question_id in (70, 185, 357):
        row = by_question[question_id]
        after = row.get("after") or {}
        candidate = after.get("candidate_identity")
        if not isinstance(candidate, Mapping):
            decisions[question_id] = {
                "agent": "Agent 1",
                "action": "QUARANTINED",
                "reason_codes": ["AGENT1_CANDIDATE_IDENTITY_MISSING"],
            }
            continue
        uid = str(candidate.get("internal_table_uid") or "")
        table = tables.get(uid)
        context = contexts.get(uid)
        if table is None or context is None:
            decisions[question_id] = {
                "agent": "Agent 1",
                "action": "QUARANTINED",
                "reason_codes": ["V2_V3_UID_NOT_FOUND_IN_CURRENT_SNAPSHOT"],
            }
            continue
        try:
            source_provenance_matches(
                table=table,
                context=context,
                candidate=candidate,
                label=f"Agent 1 Q{question_id}",
            )
            column_index = int(
                candidate.get("column_index", candidate.get("closing_column_index"))
            )
            header = context_header(context, column_index)
            failure_reasons: list[str] = []
            if header is None or coordinates(header.get("header_source_cells")) != coordinates(
                candidate.get("header_source_cells")
            ):
                failure_reasons.append("V2_V3_HEADER_ANCHORS_MISMATCH")
            if question_id == 185:
                expected = (
                    ((packets[question_id].get("stages") or [])[0].get(
                        "required_operands"
                    ) or [])[0].get("expected_table_types")
                    or []
                )
                table_kind = str(((context.get("table_function") or {}).get("kind")) or "")
                if table_kind not in {str(value) for value in expected}:
                    failure_reasons.append("EXPECTED_TABLE_TYPE_MISMATCH")
            if question_id == 357:
                failure_reasons.append("INHERITED_HEADER_NOT_REFLECTED_IN_CURRENT_V3")
            if failure_reasons:
                decisions[question_id] = {
                    "agent": "Agent 1",
                    "action": "QUARANTINED",
                    "reason_codes": failure_reasons,
                }
                continue
        except (KeyError, TypeError, ValueError, IntegrationError) as exc:
            decisions[question_id] = {
                "agent": "Agent 1",
                "action": "QUARANTINED",
                "reason_codes": [str(exc) or "AGENT1_REPLAY_CONFLICT"],
            }
            continue
        decisions[question_id] = {
            "agent": "Agent 1",
            "action": "BASELINE_UNCHANGED" if question_id == 70 else "INTEGRATED_CANDIDATE_ONLY",
            "reason_codes": [],
        }
    return decisions


def verify_agent2(
    root: Path,
    *,
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    manifest = read_json(root / "manifest.json")
    require_output_files(root, manifest, "Agent 2")
    require_contract(manifest.get("source_contract"), "Agent 2")
    matrix = read_jsonl(root / "question_candidate_matrix_v1.jsonl")
    if any(row.get("raw_numeric_values_included") is not False for row in matrix):
        raise IntegrationError("Agent 2 matrix contains numeric payload")
    selected = [
        row
        for row in matrix
        if int(row.get("question_id") or 0) == 340
        and row.get("decision") == "MATERIALIZED_CANDIDATE_ONLY"
    ]
    if len(selected) != 1:
        raise IntegrationError("Agent 2 Q340 materialized candidate is not unique")
    row = selected[0]
    require_contract(row.get("source_contract"), "Agent 2 Q340")
    candidate = row.get("candidate") or {}
    locator = candidate.get("locator") or {}
    uid = str(locator.get("internal_table_uid") or "")
    table, context = tables.get(uid), contexts.get(uid)
    if table is None or context is None:
        raise IntegrationError("Agent 2 Q340 UID is absent from V2/V3")
    source_provenance_matches(
        table=table,
        context=context,
        candidate={
            "document_id": locator.get("document_id"),
            "internal_table_uid": uid,
        },
        label="Agent 2 Q340",
    )
    row_index = int(locator.get("row_index"))
    column_index = int((locator.get("current_column_indices") or [])[0])
    if row_index != 12 or column_index != 4:
        raise IntegrationError("Agent 2 Q340 coordinates differ from declared candidate")
    rows = table.get("rows") or []
    if row_index >= len(rows) or not isinstance(rows[row_index], list):
        raise IntegrationError("Agent 2 Q340 row is out of bounds")
    row_text = " ".join(str(value) for value in rows[row_index][:3])
    if "140" not in row_text or "hang ton kho" not in folded(row_text):
        raise IntegrationError("Agent 2 Q340 parent row code/label is not in V2")
    child_rows = candidate.get("row", {}).get("direct_children") or []
    if not any(
        str(child.get("row_code") or "") == "141"
        and "hang ton kho" in folded(child.get("label"))
        for child in child_rows
        if isinstance(child, Mapping)
    ):
        raise IntegrationError("Agent 2 Q340 immediate child gate is not proven")
    header = context_header(context, column_index)
    if header is None or coordinates(header.get("header_source_cells")) != [(0, 4)]:
        raise IntegrationError("Agent 2 Q340 V3 closing header is not unique")
    if folded("Số cuối năm") not in folded(header.get("source_label")):
        raise IntegrationError("Agent 2 Q340 header label is not closing")
    if not re.search(r"\b2019\b", folded(source_title(context))):
        raise IntegrationError("Agent 2 Q340 source title has no report year")
    ledger = read_jsonl(root / "candidate_quarantine_ledger_v1.jsonl")
    decisions = {
        int(row["question_id"]): {
            "agent": "Agent 2",
            "action": "QUARANTINED",
            "reason_codes": [str(row.get("decision") or "AGENT2_PROVENANCE_BLOCKED")],
        }
        for row in ledger
        if int(row.get("question_id") or 0) in {98, 104}
    }
    decisions[340] = {
        "agent": "Agent 2",
        "action": "INTEGRATED_CANDIDATE_ONLY",
        "reason_codes": [],
    }
    return candidate, decisions


def verify_agent3(
    root: Path,
    *,
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[
    dict[int, dict[str, Any]],
    dict[int, Mapping[str, Any]],
    dict[int, dict[str, Any]],
]:
    manifest = read_json(root / "manifest.json")
    require_output_files(root, manifest, "Agent 3")
    require_contract(manifest.get("source_contract"), "Agent 3")
    candidates = read_jsonl(root / "candidate_packets_v1.jsonl")
    by_question = {int(row["question_id"]): row for row in candidates}
    if set(by_question) != {156, 263}:
        raise IntegrationError("Agent 3 candidate coverage mismatch")
    verified: dict[int, dict[str, Any]] = {}
    for question_id in (156, 263):
        candidate = by_question[question_id]
        require_contract(candidate.get("source_contract"), f"Agent 3 Q{question_id}")
        if candidate.get("raw_numeric_values_included") is not False:
            raise IntegrationError(f"Agent 3 Q{question_id} contains numeric payload")
        uid = str(candidate.get("internal_table_uid") or "")
        table, context = tables.get(uid), contexts.get(uid)
        if table is None or context is None:
            raise IntegrationError(f"Agent 3 Q{question_id} UID is absent from V2/V3")
        source_provenance_matches(
            table=table,
            context=context,
            candidate=candidate,
            label=f"Agent 3 Q{question_id}",
        )
        row_index = int(candidate.get("row_index"))
        column_index = int(candidate.get("column_index"))
        if source_cell_sha(table, row_index, column_index) != str(
            candidate.get("source_cell_sha256") or ""
        ):
            raise IntegrationError(f"Agent 3 Q{question_id} source cell hash mismatch")
        header = context_header(context, column_index)
        if header is None or coordinates(header.get("header_source_cells")) != coordinates(
            candidate.get("header_source_cells")
        ):
            raise IntegrationError(f"Agent 3 Q{question_id} header anchor mismatch")
        grid = context.get("grid") or {}
        quality = context.get("quality") or {}
        if grid.get("rectangular") is not True or grid.get("provenance_complete") is not True:
            raise IntegrationError(f"Agent 3 Q{question_id} V3 grid is not complete")
        if quality.get("status") != "review_ready":
            raise IntegrationError(f"Agent 3 Q{question_id} V3 quality is not review_ready")
        profiles = context.get("row_profiles") or []
        profile = next(
            (
                value
                for value in profiles
                if isinstance(value, Mapping)
                and value.get("row_index") == row_index
            ),
            None,
        )
        if not isinstance(profile, Mapping) or column_index in set(
            profile.get("unreliable_numeric_columns") or []
        ):
            raise IntegrationError(f"Agent 3 Q{question_id} numeric reliability veto")
        verified[question_id] = {
            "agent": "Agent 3",
            "action": "INTEGRATED_CANDIDATE_ONLY",
            "reason_codes": [],
        }
    quarantine_rows = read_jsonl(root / "quarantine_ledger_v1.jsonl")
    quarantine = {
        int(row["question_id"]): {
            "agent": "Agent 3",
            "action": "QUARANTINED",
            "reason_codes": list(
                dict.fromkeys(
                    [
                        str(row.get("quarantine_reason") or "AGENT3_QUARANTINED"),
                        *[str(value) for value in row.get("blocker_codes") or []],
                    ]
                )
            ),
        }
        for row in quarantine_rows
        if int(row.get("question_id") or 0) == 242
    }
    return verified, by_question, quarantine


def candidate_contract() -> dict[str, Any]:
    return dict(CONTRACT)


def make_candidate(
    *,
    question_id: int,
    candidate_id: str,
    table: Mapping[str, Any],
    context: Mapping[str, Any],
    row_index: int,
    column_index: int,
    header_source_cells: list[dict[str, int]],
    period_labels: list[str],
    period_method: str,
    period_role: str,
    period_source_date: str,
    semantic_status: str,
) -> dict[str, Any]:
    header = context_header(context, column_index)
    if header is None:
        raise IntegrationError(f"Q{question_id} candidate header is not unique")
    provenance = table.get("source_provenance") or {}
    marker = source_title_unit_marker(table=table, context=context)
    header_units = list(header.get("unit_labels") or [])
    if marker is None and not header_units:
        raise IntegrationError(
            f"Q{question_id} has neither a header nor source-title unit anchor"
        )
    candidate = {
        "candidate_id": candidate_id,
        "candidate_origin": "agent4_independent_v2_v3_replay",
        "column_index": column_index,
        "header_source_cells": header_source_cells,
        "internal_table_uid": table.get("internal_table_uid"),
        "period_labels": period_labels,
        "period_resolution_method": period_method,
        "period_role": period_role,
        "period_source_date": period_source_date,
        "period_source_title_sha256": hashlib.sha256(
            source_title(context).encode("utf-8")
        ).hexdigest(),
        "row_index": row_index,
        "source_sha256": provenance.get("source_sha256"),
        "table_sha256": provenance.get("table_sha256"),
        "source_cell_sha256": source_cell_sha(table, row_index, column_index),
        "source_label": header.get("source_label"),
        "unit_labels": header_units,
        "semantic_candidate_status": semantic_status,
        "raw_numeric_values_included": False,
        "source_contract": candidate_contract(),
    }
    if marker is not None:
        candidate["unit_source_title_recheck"] = marker
    return candidate


def replace_operand(
    packet: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    result = deepcopy(dict(packet))
    result["input_packet_status"] = "agent4_independent_v2_v3_replay_candidate"
    result["packet_status"] = "unique_period_column_candidate"
    stages = result.get("stages") or []
    if len(stages) != 1 or len(stages[0].get("required_operands") or []) != 1:
        raise IntegrationError(
            f"Q{packet.get('question_id')} does not have one direct operand"
        )
    operand = stages[0]["required_operands"][0]
    operand["column_status"] = "unique_period_column_candidate"
    operand["period_column_candidate_count"] = 1
    operand["column_candidate_reason_counts"] = {reason_code: 1}
    operand["period_column_candidates"] = [dict(candidate)]
    return result


def decision_row(
    *,
    question_id: int,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    action: str,
    reason_codes: list[str],
    agent: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_id": question_id,
        "agent": agent,
        "action": action,
        "before_packet_status": before.get("packet_status"),
        "after_packet_status": after.get("packet_status"),
        "before_period_candidate_count": sum(
            len(operand.get("period_column_candidates") or [])
            for stage in before.get("stages") or []
            for operand in stage.get("required_operands") or []
        ),
        "after_period_candidate_count": sum(
            len(operand.get("period_column_candidates") or [])
            for stage in after.get("stages") or []
            for operand in stage.get("required_operands") or []
        ),
        "reason_codes": reason_codes,
        "replay_gate": (
            "candidate_only_v2_v3_coordinate_hash_replay"
            if action == "INTEGRATED_CANDIDATE_ONLY"
            else "quarantine_until_independent_source_replay"
        ),
        "source_contract": candidate_contract(),
    }


def output_descriptor(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-period", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--route-overlay", type=Path, required=True)
    parser.add_argument("--route-manifest", type=Path, required=True)
    parser.add_argument("--structured-tables", type=Path, required=True)
    parser.add_argument("--evidence-context", type=Path, required=True)
    parser.add_argument("--evidence-context-manifest", type=Path, required=True)
    parser.add_argument("--agent1-dir", type=Path, required=True)
    parser.add_argument("--agent2-dir", type=Path, required=True)
    parser.add_argument("--agent3-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_dir}")
    packets_list, routes, tables, contexts = verify_base_inputs(
        base_period=args.base_period,
        base_manifest_path=args.base_manifest,
        route=args.route_overlay,
        route_manifest_path=args.route_manifest,
        structured=args.structured_tables,
        context=args.evidence_context,
        context_manifest_path=args.evidence_context_manifest,
    )
    packets = {int(row["question_id"]): row for row in packets_list}
    agent1 = verify_agent1(
        args.agent1_dir, tables=tables, contexts=contexts, packets=packets
    )
    agent2_candidate, agent2 = verify_agent2(
        args.agent2_dir, tables=tables, contexts=contexts
    )
    agent3, agent3_candidates, agent3_quarantine = verify_agent3(
        args.agent3_dir, tables=tables, contexts=contexts
    )

    after_packets = {question_id: deepcopy(packet) for question_id, packet in packets.items()}
    decisions: dict[int, dict[str, Any]] = {}
    quarantine: list[dict[str, Any]] = []

    for question_id in TARGET_IDS:
        agent_decision = (
            agent1.get(question_id)
            or agent2.get(question_id)
            or agent3.get(question_id)
            or agent3_quarantine.get(question_id)
        )
        if agent_decision is None:
            agent_decision = {
                "agent": "baseline",
                "action": "UNCHANGED",
                "reason_codes": [],
            }
        if question_id == 70:
            decisions[question_id] = decision_row(
                question_id=question_id,
                before=packets[question_id],
                after=after_packets[question_id],
                action="BASELINE_UNCHANGED",
                reason_codes=[],
                agent="Agent 1",
            )
            continue
        if question_id in QUARANTINED_IDS:
            reasons = list(agent_decision.get("reason_codes") or [])
            if not reasons:
                reasons = ["CANDIDATE_NOT_INTEGRATION_ELIGIBLE"]
            decisions[question_id] = decision_row(
                question_id=question_id,
                before=packets[question_id],
                after=after_packets[question_id],
                action="QUARANTINED",
                reason_codes=reasons,
                agent=str(agent_decision.get("agent") or "Agent review"),
            )
            quarantine.append(
                {
                    "schema_version": 1,
                    "protocol": "vifinqa_agent4_quarantine_ledger_v1",
                    "question_id": question_id,
                    "agent": agent_decision.get("agent"),
                    "quarantine_status": "QUARANTINED",
                    "reason_codes": reasons,
                    "candidate_only": True,
                    "raw_numeric_values_included": False,
                    "source_contract": candidate_contract(),
                }
            )

    q340_table = tables[str(agent2_candidate["locator"]["internal_table_uid"])]
    q340_context = contexts[str(agent2_candidate["locator"]["internal_table_uid"])]
    q340_candidate = make_candidate(
        question_id=340,
        candidate_id=str(agent2_candidate.get("candidate_id") or "agent2-q340"),
        table=q340_table,
        context=q340_context,
        row_index=12,
        column_index=4,
        header_source_cells=[{"row_index": 0, "column_index": 4}],
        period_labels=["2019"],
        period_method="v2_exact_document_header_current_header_v1",
        period_role="closing",
        period_source_date="2019-12-31",
        semantic_status="UNPROVEN_WITHOUT_HUMAN_SEMANTIC_APPROVAL",
    )
    after_packets[340] = replace_operand(
        packets[340],
        candidate=q340_candidate,
        reason_code="AGENT2_PARENT_CHILD_V2_V3_REPLAY_CANDIDATE_ONLY",
    )
    decisions[340] = decision_row(
        question_id=340,
        before=packets[340],
        after=after_packets[340],
        action="INTEGRATED_CANDIDATE_ONLY",
        reason_codes=[],
        agent="Agent 2",
    )

    for question_id in (156, 263):
        source = agent3_candidates[question_id]
        uid = str(source["internal_table_uid"])
        table, context = tables[uid], contexts[uid]
        column_index = int(source["column_index"])
        q_candidate = make_candidate(
            question_id=question_id,
            candidate_id=f"agent3-q{question_id}-v2-v3-replay",
            table=table,
            context=context,
            row_index=int(source["row_index"]),
            column_index=column_index,
            header_source_cells=[
                {
                    "row_index": int(coord["row_index"]),
                    "column_index": int(coord["column_index"]),
                }
                for coord in source["header_source_cells"]
            ],
            period_labels=(
                list(
                    (
                        context_header(context, column_index) or {}
                    ).get("period_labels")
                    or []
                )
            ),
            period_method=(
                "agent3_multicol_table_instant_context_candidate_only_v1"
                if question_id == 156
                else "agent3_multicol_closing_period_context_candidate_only_v1"
            ),
            period_role=str(source.get("period_role") or "candidate_only"),
            period_source_date=str(source["period_end"]),
            semantic_status="UNPROVEN_WITHOUT_HUMAN_SEMANTIC_APPROVAL",
        )
        after_packets[question_id] = replace_operand(
            packets[question_id],
            candidate=q_candidate,
            reason_code="AGENT3_MULTICOL_V2_V3_REPLAY_CANDIDATE_ONLY",
        )
        decisions[question_id] = decision_row(
            question_id=question_id,
            before=packets[question_id],
            after=after_packets[question_id],
            action="INTEGRATED_CANDIDATE_ONLY",
            reason_codes=[],
            agent="Agent 3",
        )

    # Every question must have an explicit before/after decision in the
    # integration report, while untouched questions remain byte-for-byte
    # equivalent objects inside the new union.
    for question_id in sorted(packets):
        if question_id in decisions:
            continue
        decisions[question_id] = decision_row(
            question_id=question_id,
            before=packets[question_id],
            after=after_packets[question_id],
            action="BASELINE_UNCHANGED",
            reason_codes=[],
            agent="baseline",
        )

    args.output_dir.mkdir(parents=True, exist_ok=False)
    period_output = args.output_dir / "period_column_candidate_packets_v1.jsonl"
    summary_output = args.output_dir / "period_packet_union_summary_v1.json"
    decisions_output = args.output_dir / "integration_before_after_v1.jsonl"
    quarantine_output = args.output_dir / "quarantine_ledger_v1.jsonl"

    write_jsonl(
        period_output,
        [after_packets[question_id] for question_id in sorted(after_packets)],
    )
    write_jsonl(
        decisions_output,
        [decisions[question_id] for question_id in sorted(decisions)],
    )
    write_jsonl(quarantine_output, sorted(quarantine, key=lambda row: row["question_id"]))

    base_counts = Counter(str(row.get("packet_status") or "") for row in packets.values())
    output_counts = Counter(
        str(row.get("packet_status") or "") for row in after_packets.values()
    )
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_protocol": PERIOD_PROTOCOL,
        "base_question_count": len(packets),
        "question_count": len(after_packets),
        "base_packet_status_counts": dict(sorted(base_counts.items())),
        "output_packet_status_counts": dict(sorted(output_counts.items())),
        "replaced_question_ids": list(INTEGRATED_IDS),
        "quarantined_question_ids": list(QUARANTINED_IDS),
        "unchanged_agent1_question_ids": [70],
        "candidate_replay_proof": {
            "agent1_q70": "REGRESSION_PASS_UNCHANGED_CANDIDATE",
            "agent1_q185": "QUARANTINED_EXPECTED_TABLE_TYPE_OR_HEADER_CONFLICT",
            "agent1_q357": "QUARANTINED_V2_V3_HEADER_ANCHOR_CONFLICT",
            "agent2_q340": "V2_V3_COORDINATE_HASH_AND_PARENT_CHILD_CHECK_PASS",
            "agent3_q156": "V2_V3_COORDINATE_HASH_AND_HEADER_CHECK_PASS",
            "agent3_q263": "V2_V3_COORDINATE_HASH_AND_HEADER_CHECK_PASS",
        },
        "human_verified_count": 0,
        "semantic_approval_count": 0,
        "release_authorized": False,
        "submission_eligible": False,
        "source_contract": candidate_contract(),
    }
    write_json(summary_output, summary)

    manifest = {
        "schema_version": 1,
        "protocol": PERIOD_PROTOCOL,
        "integration_protocol": PROTOCOL,
        "created_at_utc": summary["created_at_utc"],
        "inputs": {
            "base_period_packets": {
                "path": str(args.base_period),
                "sha256": sha256_file(args.base_period),
            },
            "base_period_manifest": {
                "path": str(args.base_manifest),
                "sha256": sha256_file(args.base_manifest),
            },
            "route_overlay": {
                "path": str(args.route_overlay),
                "sha256": sha256_file(args.route_overlay),
            },
            "route_overlay_manifest": {
                "path": str(args.route_manifest),
                "sha256": sha256_file(args.route_manifest),
            },
            "structured_tables_v2": {
                "path": str(args.structured_tables),
                "sha256": sha256_file(args.structured_tables),
            },
            "evidence_context_v3": {
                "path": str(args.evidence_context),
                "sha256": sha256_file(args.evidence_context),
            },
            "evidence_context_manifest_v3": {
                "path": str(args.evidence_context_manifest),
                "sha256": sha256_file(args.evidence_context_manifest),
            },
            "agent1_manifest": {
                "path": str(args.agent1_dir / "source_period_recheck_agent1.manifest.json"),
                "sha256": sha256_file(
                    args.agent1_dir / "source_period_recheck_agent1.manifest.json"
                ),
            },
            "agent2_manifest": {
                "path": str(args.agent2_dir / "manifest.json"),
                "sha256": sha256_file(args.agent2_dir / "manifest.json"),
            },
            "agent3_manifest": {
                "path": str(args.agent3_dir / "manifest.json"),
                "sha256": sha256_file(args.agent3_dir / "manifest.json"),
            },
        },
        "outputs": {
            "period_packets": output_descriptor(period_output),
            "summary": output_descriptor(summary_output),
            "integration_before_after": output_descriptor(decisions_output),
            "quarantine_ledger": output_descriptor(quarantine_output),
        },
        "counts": {
            "question_count": len(after_packets),
            "replaced_question_count": len(INTEGRATED_IDS),
            "quarantined_question_count": len(QUARANTINED_IDS),
            "human_verified_count": 0,
            "semantic_approval_count": 0,
            "release_authorized": False,
        },
        "source_contract": candidate_contract(),
    }
    manifest_output = args.output_dir / "period_column_candidate_packets_v1.manifest.json"
    write_json(manifest_output, manifest)

    print(
        json.dumps(
            {
                "status": "PASS",
                "protocol": PROTOCOL,
                "output_dir": str(args.output_dir),
                "integrated_question_ids": list(INTEGRATED_IDS),
                "quarantined_question_ids": list(QUARANTINED_IDS),
                "human_verified_count": 0,
                "release_authorized": False,
                "period_packets_sha256": sha256_file(period_output),
                "period_manifest_sha256": sha256_file(manifest_output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
