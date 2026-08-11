"""Evidence-bounded LLM review packets for normalized table-routing stages.

The LLM is a source selector, not an answer authority.  It may choose only a
row and a value cell that are already present in a metric-router packet.  The
deterministic verifier rejects uncited answers, candidate escapes, row/header
mismatches and missing operands before any later replay/critic gate sees the
record.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .execution import parse_decimal


LLM_TABLE_REVIEW_PROTOCOL = "qwen_evidence_bounded_table_review_v1"
LLM_TABLE_REVIEW_SCHEMA_VERSION = 1
YEAR_BOUNDARY_TEMPLATE = r"(?<!\d){year}(?!\d)"
TARGET_PERIOD_END_RE = re.compile(
    r"(?<!\d)(?:31\s*[/.-]\s*12|31\s+(?:tháng|thang)\s+12)(?!\d)", re.IGNORECASE
)
TARGET_PERIOD_START_RE = re.compile(r"(?<!\d)(?:0?1\s*[/.-]\s*0?1|0?1\s+0?1)(?!\d)")
EXPLICIT_DATE_RE = re.compile(
    r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])\s*(?:[/.-]|(?:tháng|thang)\s*)\s*"
    r"(?:0?[1-9]|1[0-2])(?!\d)",
    re.IGNORECASE,
)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_payload(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _candidate_cell_rows(
    table: Mapping[str, Any],
    context: Mapping[str, Any],
    row_index: int,
    *,
    target_year: int | None,
    reporting_period_end: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    rows = table.get("rows") or []
    if not isinstance(row_index, int) or not 0 <= row_index < len(rows):
        return []
    row = rows[row_index]
    columns = (context.get("canonical_headers") or {}).get("columns") or []
    cells: list[dict[str, Any]] = []
    for column_index, raw_value in enumerate(row):
        if column_index >= len(columns):
            continue
        column = columns[column_index] or {}
        header = str(column.get("source_label") or "")
        role = str(column.get("role") or "")
        parsed = parse_decimal(raw_value)
        # ``Mã số``/``Thuyết minh`` are numeric-looking identifiers, never
        # financial values.  A period header must be explicit when the stage
        # has a requested year; "năm nay" is not enough to bind a year.
        if role not in {"value_or_text", "percent_or_rate"} or not header or parsed.value is None:
            continue
        period_labels = list(column.get("period_labels") or [])
        cell = {
            "column_index": column_index,
            "canonical_header": header,
            "raw_value": str(raw_value),
            "period_labels": period_labels,
            "unit_labels": list(column.get("unit_labels") or []),
        }
        if not _cell_matches_target_year(cell, target_year, reporting_period_end):
            continue
        cells.append(cell)
    if target_year is None or not cells:
        return cells
    # Prefer the explicit year-end column where a table has both an annual
    # label and a 31/12 label. Opening/interim dates were already excluded by
    # the year-period contract above.
    best_priority = min(_target_period_priority(cell) for cell in cells)
    return [cell for cell in cells if _target_period_priority(cell) == best_priority]


def _fiscal_period_end_for_year(
    reporting_period_end: Mapping[str, Any] | None,
    target_year: int | None,
) -> tuple[int, int, int] | None:
    if target_year is None or not isinstance(reporting_period_end, Mapping):
        return None
    try:
        day = int(reporting_period_end["day"])
        month = int(reporting_period_end["month"])
        year = int(reporting_period_end["year"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 1 <= day <= 31 or not 1 <= month <= 12 or year != int(target_year):
        return None
    return day, month, year


def _cell_matches_reporting_period_end(
    cell: Mapping[str, Any],
    fiscal_period_end: tuple[int, int, int],
) -> bool:
    day, month, year = fiscal_period_end
    day_pattern = str(day) if day >= 10 else rf"0?{day}"
    month_pattern = str(month) if month >= 10 else rf"0?{month}"
    pattern = re.compile(
        rf"(?<!\d){day_pattern}\s*(?:[/.-]|\s+(?:tháng|thang)\s+)"
        rf"{month_pattern}\s*(?:[/.-]|\s+(?:năm|nam)\s*[^\d]*){year}(?!\d)",
        re.IGNORECASE,
    )
    text = " ".join(
        [str(cell.get("canonical_header") or ""), *[str(value) for value in cell.get("period_labels") or []]]
    )
    return bool(pattern.search(text))


def _cell_matches_target_year(
    cell: Mapping[str, Any],
    target_year: int | None,
    reporting_period_end: Mapping[str, Any] | None = None,
) -> bool:
    if target_year is None:
        return True
    text = " ".join(
        [str(cell.get("canonical_header") or ""), *[str(value) for value in cell.get("period_labels") or []]]
    )
    if not re.search(YEAR_BOUNDARY_TEMPLATE.format(year=int(target_year)), text):
        return False
    fiscal_period_end = _fiscal_period_end_for_year(reporting_period_end, target_year)
    if fiscal_period_end is not None:
        if _cell_matches_reporting_period_end(cell, fiscal_period_end):
            return True
        # An annual column can be labeled only by its year (for example
        # ``2022 Nghìn VND``). It is admissible only because the document's
        # fiscal end was itself extracted from literal source context above.
        # A competing explicit date is still rejected, so an interim/opening
        # balance cannot inherit the document-level period by accident.
        return not EXPLICIT_DATE_RE.search(text)
    # A year-specific statement value must not silently bind an opening
    # balance or a quarterly/interim date. A question that explicitly asks for
    # either needs a separate metric/period contract before it can reach this
    # reviewer. A plain source header such as ``Năm 2022`` remains valid.
    if TARGET_PERIOD_START_RE.search(text):
        return False
    return not EXPLICIT_DATE_RE.search(text) or bool(TARGET_PERIOD_END_RE.search(text))


def _target_period_priority(cell: Mapping[str, Any]) -> int:
    """Rank explicit target periods without interpreting their numeric values."""
    text = " ".join(
        [str(cell.get("canonical_header") or ""), *[str(value) for value in cell.get("period_labels") or []]]
    )
    if TARGET_PERIOD_END_RE.search(text):
        return 0
    return 1


def build_review_packet(
    *,
    question_id: int,
    question: str,
    stage: Mapping[str, Any],
    candidate_result: Mapping[str, Any],
    catalog_by_uid: Mapping[str, Mapping[str, Any]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    contexts_by_uid: Mapping[str, Mapping[str, Any]],
    max_tables_per_binding: int = 2,
) -> dict[str, Any]:
    """Build a compact, citation-only packet from routing candidates.

    No raw table is invented here.  Values are emitted only as raw source
    cells paired with their canonical V3 header.  The optional cap is a prompt
    size bound, never a ranking or source-quality promotion.
    """
    if max_tables_per_binding < 1:
        raise ValueError("max_tables_per_binding must be positive")
    if str(candidate_result.get("status") or "") != "candidate_tables_found":
        raise ValueError("LLM packet requires candidate_tables_found")
    by_binding = candidate_result.get("candidate_table_uids_by_entity_variable") or {}
    if not isinstance(by_binding, Mapping) or not by_binding:
        raise ValueError("Candidate result lacks entity/variable bindings")
    candidates: list[dict[str, Any]] = []
    required_bindings: list[dict[str, str]] = []
    emitted_bindings: set[tuple[str, str]] = set()
    emitted_bindings_by_scope: dict[str, set[tuple[str, str]]] = {}
    seen: set[str] = set()
    for entity in sorted(str(value) for value in by_binding):
        variables = by_binding[entity]
        if not isinstance(variables, Mapping):
            raise ValueError("Candidate bindings must map entity to variables")
        for variable_id in sorted(str(value) for value in variables):
            required_bindings.append({"company": entity, "variable_id": variable_id})
            for uid in (str(value) for value in variables[variable_id]):
                catalog = catalog_by_uid.get(uid)
                table = tables_by_uid.get(uid)
                context = contexts_by_uid.get(uid)
                if catalog is None or table is None or context is None:
                    raise ValueError(f"Candidate {uid} is missing catalog/table/context")
                matching_rows = [
                    value
                    for value in catalog.get("canonical_variables") or []
                    if str(value.get("variable_id") or "") == variable_id
                ]
                for binding in matching_rows:
                    row_index = binding.get("row_index")
                    cells = _candidate_cell_rows(
                        table,
                        context,
                        row_index,
                        target_year=stage.get("year"),
                        reporting_period_end=catalog.get("reporting_period_end"),
                    )
                    if not cells:
                        continue
                    candidate_id = f"{entity}|{variable_id}|{uid}|{row_index}"
                    if candidate_id in seen:
                        continue
                    seen.add(candidate_id)
                    candidates.append(
                        {
                            "candidate_id": candidate_id,
                            "company": entity,
                            "variable_id": variable_id,
                            "internal_table_uid": uid,
                            "document_id": str(table.get("document_id") or ""),
                            "report_year": catalog.get("report_year"),
                            "report_scope": str(catalog.get("report_scope") or "unknown"),
                            "reporting_period_end": catalog.get("reporting_period_end"),
                            "table_type": str(catalog.get("table_type") or "other"),
                            "row_index": row_index,
                            "raw_row_label": str(binding.get("raw_row_label") or ""),
                            "available_value_cells": cells,
                        }
                    )
                    emitted_bindings.add((entity, variable_id))
                    scope = str(catalog.get("report_scope") or "unknown")
                    emitted_bindings_by_scope.setdefault(scope, set()).add((entity, variable_id))
    required_binding_set = {(binding["company"], binding["variable_id"]) for binding in required_bindings}
    viable_report_scopes = sorted(
        scope
        for scope, bindings in emitted_bindings_by_scope.items()
        if required_binding_set.issubset(bindings)
    )
    scope_status = (
        "resolved" if len(viable_report_scopes) == 1
        else "ambiguous" if len(viable_report_scopes) > 1
        else "unresolved"
    )
    if scope_status == "resolved":
        # Keep the prompt compact and remove an otherwise tempting alternate
        # separate/consolidated source. The verifier repeats this constraint,
        # so this is an input reduction rather than a trust boundary.
        candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("report_scope") or "unknown") == viable_report_scopes[0]
        ]
    required_variables_by_entity = {
        entity: {variable for company, variable in required_binding_set if company == entity}
        for entity, _variable in required_binding_set
    }
    document_contract: dict[str, Any] = {
        "status": "unresolved",
        "selected_document_ids": {},
        "viable_document_ids_by_company": {},
        "must_use_one_document_per_company": True,
    }
    if scope_status == "resolved":
        selected_document_ids: dict[str, str] = {}
        viable_document_ids_by_company: dict[str, list[str]] = {}
        document_status = "resolved"
        for entity, required_variables in sorted(required_variables_by_entity.items()):
            variables_by_document: dict[str, set[str]] = {}
            candidates_by_document: dict[str, list[Mapping[str, Any]]] = {}
            for candidate in candidates:
                if str(candidate.get("company") or "") != entity:
                    continue
                document_id = str(candidate.get("document_id") or "")
                if not document_id:
                    continue
                variables_by_document.setdefault(document_id, set()).add(
                    str(candidate.get("variable_id") or "")
                )
                candidates_by_document.setdefault(document_id, []).append(candidate)
            viable = sorted(
                document_id
                for document_id, variables in variables_by_document.items()
                if required_variables.issubset(variables)
            )
            viable_document_ids_by_company[entity] = viable
            target_year = stage.get("year")
            target_year_viable = [
                document_id
                for document_id in viable
                if any(candidate.get("report_year") == target_year for candidate in candidates_by_document[document_id])
            ]
            if len(target_year_viable) == 1:
                selected_document_ids[entity] = target_year_viable[0]
            elif not target_year_viable and len(viable) == 1:
                # A comparative source is permitted only when it is the sole
                # complete source document and its V3 header already proved
                # the requested year.
                selected_document_ids[entity] = viable[0]
            elif len(target_year_viable) > 1:
                document_status = "ambiguous"
            else:
                document_status = "unresolved"
        if len(selected_document_ids) != len(required_variables_by_entity) and document_status == "resolved":
            document_status = "unresolved"
        document_contract = {
            "status": document_status,
            "selected_document_ids": selected_document_ids,
            "viable_document_ids_by_company": viable_document_ids_by_company,
            "must_use_one_document_per_company": True,
        }
        if document_status == "resolved":
            candidates = [
                candidate
                for candidate in candidates
                if selected_document_ids.get(str(candidate.get("company") or ""))
                == str(candidate.get("document_id") or "")
            ]
    # Apply the prompt-size cap only after source-period, scope and document
    # contracts selected admissible inputs. Capping earlier could hide the
    # only valid fiscal-year source behind an interim/comparative table.
    limited_candidates: list[dict[str, Any]] = []
    selected_table_uids_by_binding: dict[tuple[str, str], set[str]] = {}
    for candidate in candidates:
        key = (str(candidate["company"]), str(candidate["variable_id"]))
        chosen_uids = selected_table_uids_by_binding.setdefault(key, set())
        uid = str(candidate["internal_table_uid"])
        if uid not in chosen_uids and len(chosen_uids) >= max_tables_per_binding:
            continue
        chosen_uids.add(uid)
        limited_candidates.append(candidate)
    candidates = limited_candidates
    missing_source_cell_bindings = [
        binding
        for binding in required_bindings
        if (binding["company"], binding["variable_id"])
        not in {(str(candidate["company"]), str(candidate["variable_id"])) for candidate in candidates}
    ]
    packet = {
        "schema_version": LLM_TABLE_REVIEW_SCHEMA_VERSION,
        "protocol": LLM_TABLE_REVIEW_PROTOCOL,
        "question_id": int(question_id),
        "question": question,
        "stage": {
            "stage_id": str(stage.get("stage_id") or ""),
            "metric_id": str(stage.get("metric_id") or ""),
            "year": stage.get("year"),
            "calculation": str(stage.get("calculation") or ""),
            "required_variables": list(stage.get("required_variables") or []),
        },
        "required_bindings": required_bindings,
        "missing_source_cell_bindings": missing_source_cell_bindings,
        "scope_contract": {
            "status": scope_status,
            "viable_report_scopes": viable_report_scopes,
            "must_use_one_uniform_scope": True,
        },
        "document_contract": document_contract,
        "candidates": candidates,
        "source_contract": {
            "may_choose_only_listed_candidate_id": True,
            "must_copy_raw_value_and_canonical_header_exactly": True,
            "may_not_compute_or_return_a_final_answer": True,
            "may_not_infer_a_missing_operand": True,
        },
    }
    packet["packet_sha256"] = _sha256_payload(packet)
    return packet


def review_prompt(packet: Mapping[str, Any]) -> str:
    """Instruct Qwen to act as a cited-binding reviewer, not a QA model."""
    return """Bạn là reviewer evidence cho bảng tài chính. Không trả lời câu hỏi cuối cùng và không làm phép tính.
Chỉ chọn cell nguồn cho từng required binding từ candidate_id đã liệt kê. Không được suy luận số, tự tạo bảng,
đổi header, đổi raw_value, hay chọn candidate ngoài packet. Nếu một binding không có bằng chứng chắc chắn, hãy chọn
verdict=no_candidate hoặc abstain và mô tả feedback ngắn.

Trả về DUY NHẤT một JSON object theo schema:
{
  \"verdict\": \"supported\" | \"no_candidate\" | \"abstain\",
  \"selected_bindings\": [
    {\"candidate_id\": \"...\", \"column_index\": 0, \"canonical_header\": \"...\", \"raw_value\": \"...\"}
  ],
  \"feedback\": {\"reason_code\": \"...\", \"detail\": \"...\"},
  \"final_answer\": null
}
Nếu verdict=supported, phải có đúng một binding cho mọi company/variable cần thiết. final_answer luôn null.

PACKET:\n""" + _canonical_json(packet)


def self_critique_prompt(packet: Mapping[str, Any], decision: Mapping[str, Any]) -> str:
    """A second role pass catches unsupported claims; it is not an independent critic."""
    return """Bạn là self-critique fail-closed cho một review evidence. So sánh DECISION với PACKET.
Reject nếu decision có final_answer khác null, thiếu binding, candidate_id ngoài packet, header/raw_value không literal,
hoặc có bất kỳ suy luận không được trích dẫn. Không được sửa decision.
Trả về duy nhất JSON: {\"verdict\": \"approve\" | \"reject\", \"violations\": [\"...\"]}.
PACKET:\n""" + _canonical_json(packet) + "\nDECISION:\n" + _canonical_json(decision)


def _candidate_by_id(packet: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    candidates = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in packet.get("candidates") or []
    }
    if "" in candidates or len(candidates) != len(packet.get("candidates") or []):
        raise ValueError("Packet contains duplicate or empty candidate IDs")
    return candidates


def verify_llm_decision(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    self_critique: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed on any non-literal LLM claim.

    A successful output remains ``machine_provisional``.  It can become
    ``machine_calibrated`` only through the existing direct replay and
    reviewer-independent critic gates, and it never becomes human_verified.
    """
    if str(packet.get("protocol") or "") != LLM_TABLE_REVIEW_PROTOCOL:
        raise ValueError("Unsupported LLM review packet protocol")
    expected_hash = dict(packet)
    provided_hash = str(expected_hash.pop("packet_sha256", ""))
    if provided_hash != _sha256_payload(expected_hash):
        raise ValueError("Packet hash mismatch")
    verdict = str(decision.get("verdict") or "")
    if verdict not in {"supported", "no_candidate", "abstain"}:
        return _blocked(packet, "invalid_llm_verdict")
    if decision.get("final_answer") not in (None, ""):
        return _blocked(packet, "llm_self_inference_detected")
    if self_critique is not None:
        if str(self_critique.get("verdict") or "") != "approve":
            return _blocked(packet, "llm_self_critique_rejected", self_critique)
    if verdict != "supported":
        return {
            "id": int(packet["question_id"]),
            "reviewer_type": "qwen_evidence_bounded",
            "annotation_status": "needs_human",
            "provenance_status": "machine_abstained",
            "reason_codes": ["llm_abstained", str((decision.get("feedback") or {}).get("reason_code") or "unspecified")],
            "feedback": dict(decision.get("feedback") or {}),
            "requires_independent_replay": False,
            "human_verified": False,
            "submission_eligible": False,
        }
    candidates = _candidate_by_id(packet)
    required = {
        (str(binding.get("company") or ""), str(binding.get("variable_id") or ""))
        for binding in packet.get("required_bindings") or []
    }
    selected: list[dict[str, Any]] = []
    seen_required: set[tuple[str, str]] = set()
    for binding in decision.get("selected_bindings") or []:
        candidate = candidates.get(str(binding.get("candidate_id") or ""))
        if candidate is None:
            return _blocked(packet, "llm_candidate_escape")
        key = (str(candidate["company"]), str(candidate["variable_id"]))
        if key not in required or key in seen_required:
            return _blocked(packet, "llm_duplicate_or_unrequired_binding")
        expected_cell = next(
            (
                cell
                for cell in candidate.get("available_value_cells") or []
                if int(cell.get("column_index", -1)) == binding.get("column_index")
            ),
            None,
        )
        if expected_cell is None:
            return _blocked(packet, "llm_invalid_value_cell")
        if not _cell_matches_target_year(
            expected_cell,
            packet.get("stage", {}).get("year"),
            candidate.get("reporting_period_end"),
        ):
            return _blocked(packet, "llm_non_target_period_cell")
        if (
            str(binding.get("canonical_header") or "") != str(expected_cell["canonical_header"])
            or str(binding.get("raw_value") or "") != str(expected_cell["raw_value"])
        ):
            return _blocked(packet, "llm_citation_not_literal")
        seen_required.add(key)
        selected.append(
            {
                "company": key[0],
                "variable_id": key[1],
                "internal_table_uid": candidate["internal_table_uid"],
                "row_index": candidate["row_index"],
                "raw_row_label": candidate["raw_row_label"],
                **expected_cell,
            }
        )
    if seen_required != required:
        return _blocked(packet, "llm_missing_required_binding")
    scope_contract = packet.get("scope_contract") or {}
    viable_scopes = [str(value) for value in scope_contract.get("viable_report_scopes") or []]
    if scope_contract:
        if str(scope_contract.get("status") or "") != "resolved" or len(viable_scopes) != 1:
            return _blocked(packet, "llm_report_scope_not_resolved")
        expected_scope = viable_scopes[0]
        selected_scopes = {
            str(candidates[str(binding["candidate_id"])].get("report_scope") or "unknown")
            for binding in decision.get("selected_bindings") or []
        }
        if selected_scopes != {expected_scope}:
            return _blocked(packet, "llm_inconsistent_report_scope")
    document_contract = packet.get("document_contract") or {}
    if document_contract:
        selected_document_ids = {
            str(company): str(document_id)
            for company, document_id in (document_contract.get("selected_document_ids") or {}).items()
        }
        if (
            str(document_contract.get("status") or "") != "resolved"
            or not selected_document_ids
            or any(
                str(candidates[str(binding["candidate_id"])].get("document_id") or "")
                != selected_document_ids.get(str(candidates[str(binding["candidate_id"])].get("company") or ""))
                for binding in decision.get("selected_bindings") or []
            )
        ):
            return _blocked(packet, "llm_inconsistent_source_document")
    return {
        "id": int(packet["question_id"]),
        "reviewer_type": "qwen_evidence_bounded",
        "annotation_status": "machine_provisional",
        "provenance_status": "machine_provisional",
        "reason_codes": ["llm_citations_passed_literal_verifier"],
        "selected_bindings": selected,
        "packet_sha256": provided_hash,
        "self_critique": None if self_critique is None else dict(self_critique),
        "self_critique_is_independent": False,
        "requires_independent_replay": True,
        "requires_human_confirmation": False,
        "human_verified": False,
        "submission_eligible": False,
        "promotion_path": "direct_replay_and_independent_critic_then_machine_calibrated_only",
    }


def _blocked(
    packet: Mapping[str, Any],
    reason: str,
    self_critique: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": int(packet["question_id"]),
        "reviewer_type": "qwen_evidence_bounded",
        "annotation_status": "needs_human",
        "provenance_status": "machine_abstained",
        "reason_codes": [reason],
        "self_critique": None if self_critique is None else dict(self_critique),
        "requires_independent_replay": False,
        "human_verified": False,
        "submission_eligible": False,
    }
