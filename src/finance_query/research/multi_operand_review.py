"""Build a numeric-free, research-only review queue for multi-operand questions.

The queue groups every *required* operand of a question together.  It is a
review aid: a reviewer can identify a candidate table, row, period and unit
for each operand, but this module never reads a cell value, evaluates an
operation, or makes an answer/evidence/submission eligible.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.table_retrieval import load_jsonl, sha256_file
from finance_query.research.exact_cell_research import (
    _period_unit_diagnostic,
    _row_candidates,
)
from finance_query.research.full_corpus_candidate_retrieval import _tokens


PROTOCOL = "vifinqa_multi_operand_review_v1"
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "raw_source_row",
        "raw_source_cell",
        "rows",
    }
)
_YEAR_TOKEN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_NUMBER_TOKEN = re.compile(r"(?<![\w])[-+]?(?:\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)(?![\w])")
_NUMERIC_CELL = re.compile(r"\d")


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _redact_financial_numbers(value: object) -> str:
    """Keep explicit years but hide every other numeric token in source text."""
    text = str(value or "").strip()
    years: list[str] = []

    def stash_year(match: re.Match[str]) -> str:
        years.append(match.group(1))
        return f"__YEAR_{len(years) - 1}__"

    protected = _YEAR_TOKEN.sub(stash_year, text)
    redacted = _NUMBER_TOKEN.sub("[SỐ ĐÃ ẨN]", protected)
    for index, year in enumerate(years):
        redacted = redacted.replace(f"__YEAR_{index}__", year)
    return redacted


def _relative_source_path(path: str, repo_root: Path) -> str:
    source = Path(path)
    try:
        return source.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        # The path is provenance metadata, not an execution path.  Do not leak
        # a reviewer workstation path when it is outside this repository.
        return f"external/{source.name}"


def _operation_description(operation: Mapping[str, Any]) -> str:
    operator = str(operation.get("op") or "")
    args = [str(argument) for argument in operation.get("args") or []]
    if operator == "mean":
        return "Lấy giá trị trung bình của toàn bộ nguồn đã xác nhận."
    if operator == "subtract" and len(args) == 2:
        return f"Lấy nguồn {args[0]} trừ nguồn {args[1]}."
    if operator == "absolute_difference" and len(args) == 2:
        return f"Lấy độ chênh tuyệt đối giữa nguồn {args[0]} và nguồn {args[1]}."
    return "Chỉ kiểm tra đủ các nguồn bắt buộc; không thực thi phép tính ở màn hình này."


def _expected_operand(operand: Mapping[str, Any]) -> dict[str, Any]:
    years = operand.get("years") or []
    if len(years) != 1 or not isinstance(years[0], int):
        raise ValueError("multi-operand review requires exactly one requested year per operand")
    return {
        "operand_id": str(operand["operand_id"]),
        "ticker": str(operand["ticker"]),
        "report_year": int(years[0]),
        "requested_scope": operand.get("scope"),
        "metric_hints": [str(value) for value in operand.get("metric_hints") or []],
        "role": str(operand.get("role") or "unknown"),
        "requested_unit": str(
            (operand.get("unit_contract") or {}).get("requested_unit") or "unknown"
        ),
    }


def _source_table_structure(asset: Mapping[str, Any]) -> dict[str, Any]:
    header_indices = {int(value) for value in asset.get("header_row_indices") or []}
    raw_structure = asset.get("rows") or []
    return {
        "row_count": len(raw_structure),
        "column_count": max((len(row) for row in raw_structure), default=0),
        "header_row_indices": sorted(header_indices),
        "structure": [
            {
                "row_index": row_index,
                "is_header": row_index in header_indices,
                "cells": [_redact_financial_numbers(cell) for cell in raw_row],
            }
            for row_index, raw_row in enumerate(raw_structure)
        ],
        "numeric_financial_values_exposed": False,
    }


def _selectable_cells(asset: Mapping[str, Any]) -> list[dict[str, int]]:
    """Return coordinate-only cells a reviewer may select from one table.

    The source values stay inside the full asset.  A coordinate is offered only
    when the row has a meaningful label and the original cell carries a numeric
    token.  OCR can misclassify data rows as headers, so header-row membership
    is deliberately not used as an exclusion criterion here.
    """
    cells: list[dict[str, int]] = []
    for row_index, raw_row in enumerate(asset.get("rows") or []):
        # The first row is always the table's header anchor.  Later rows may
        # be OCR-misclassified as headers and are handled by their own labels.
        if row_index == 0:
            continue
        row = [str(value) for value in raw_row]
        if not row or not row[0].strip():
            continue
        for column_index, value in enumerate(row[1:], start=1):
            if _NUMERIC_CELL.search(value):
                cells.append({"row_index": row_index, "column_index": column_index})
    return cells


def _recover_misclassified_header_rows(
    asset: Mapping[str, Any], *, query: str, maximum: int
) -> list[dict[str, Any]]:
    """Offer labelled OCR header rows only when no ordinary row is available.

    Some OCR tables mark every row as a header even though rows after the first
    one clearly carry a metric label and a numeric period column.  This recovery
    is navigation-only and visibly tagged for human review; it does not select a
    value or repair the source table.
    """
    header_rows = {int(value) for value in asset.get("header_row_indices") or []}
    if len(header_rows) < 2:
        return []
    query_tokens = _tokens(query)
    scored: list[tuple[float, int, str, list[int]]] = []
    for row_index in sorted(header_rows):
        if row_index == 0:
            continue
        raw_rows = asset.get("rows") or []
        if not 0 <= row_index < len(raw_rows):
            continue
        row = [str(value) for value in raw_rows[row_index]]
        if not row:
            continue
        label = row[0].strip()
        numeric_columns = [
            column_index
            for column_index, value in enumerate(row[1:], start=1)
            # Structured review assets may already replace a financial value
            # with a numeric-free placeholder.  A non-empty, non-dash cell in
            # a non-label column still establishes that this is a data row;
            # it never reveals the value itself.
            if _NUMERIC_CELL.search(value) or value.strip() not in {"", "-"}
        ]
        if not label or not numeric_columns:
            continue
        label_tokens = _tokens(label)
        score = len(query_tokens & label_tokens) / max(1, len(query_tokens | label_tokens))
        scored.append((score, row_index, label, numeric_columns))
    scored.sort(key=lambda value: (-value[0], value[1]))
    return [
        {
            "row_rank": rank,
            "row_index": row_index,
            "row_label": _redact_financial_numbers(label),
            "row_label_token_jaccard": score,
            "numeric_column_indices": numeric_columns,
            "row_label_sha256": hashlib.sha256(label.encode("utf-8")).hexdigest(),
            "row_candidate_origin": "OCR_HEADER_ROW_RECOVERY",
        }
        for rank, (score, row_index, label, numeric_columns) in enumerate(
            scored[:maximum], start=1
        )
    ]


def _route_candidate_from_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    locator = {
        "source_path": str(asset["source_path"]),
        "source_sha256": str(asset["source_sha256"]),
        "table_sha256": str(asset["table_sha256"]),
        "local_ordinal": int(asset["local_ordinal"]),
        "char_start": int(asset["char_start"]),
        "page_no": asset.get("page_no"),
    }
    return {
        "internal_table_uid": str(asset["internal_table_uid"]),
        "retrieval_support": "feedback_targeted_research",
        "exact_table_locator": locator,
        "exact_table_locator_sha256": _canonical_sha(locator),
    }


def _candidate_packet(
    *,
    route_candidate: Mapping[str, Any],
    asset: Mapping[str, Any],
    expected: Mapping[str, Any],
    metric_query: str,
    rank: int,
    repo_root: Path,
    max_rows_per_table: int,
) -> dict[str, Any]:
    locator = route_candidate.get("exact_table_locator") or {}
    if not isinstance(locator, Mapping):
        raise ValueError("hybrid candidate has no exact table locator")
    for key in ("source_sha256", "table_sha256"):
        if str(locator.get(key)) != str(asset.get(key)):
            raise ValueError(f"candidate and full-table asset disagree on {key}")
    diagnostics = _period_unit_diagnostic(
        asset,
        requested_year=int(expected["report_year"]),
        requested_scope=(
            str(expected["requested_scope"])
            if expected.get("requested_scope") is not None
            else None
        ),
    )
    row_candidates = _row_candidates(
        asset, query=metric_query, maximum=max_rows_per_table
    )
    for row in row_candidates:
        row["row_candidate_origin"] = "BODY_ROW"
        row["row_label"] = _redact_financial_numbers(row["row_label"])
    row_candidates = [row for row in row_candidates if str(row["row_label"]).strip()]
    if not row_candidates:
        row_candidates = _recover_misclassified_header_rows(
            asset, query=metric_query, maximum=max_rows_per_table
        )
    column_headers = [
        {
            "column_index": int(record["column_index"]),
            "header_labels": [
                _redact_financial_numbers(label) for label in record["header_labels"]
            ],
            "header_years": [int(year) for year in record["header_years"]],
            "header_sha256": str(record["header_sha256"]),
        }
        for record in diagnostics["column_headers"]
    ]
    # _period_unit_diagnostic only considers numeric columns.  A table with an
    # unusual OCR header can still be reviewed manually, but it must be marked
    # as such instead of being treated as structurally complete.
    structural_candidate_ready = (
        bool(row_candidates)
        and diagnostics["period_status"] == "UNIQUE_YEAR_HEADER_CANDIDATE"
        and diagnostics["unit_status"] == "UNIQUE_HEADER_UNIT_CANDIDATE"
        and diagnostics["scope_status"] in {"SCOPE_MATCH", "SCOPE_NOT_REQUESTED"}
    )
    return {
        "rank": rank,
        "table_uid": str(route_candidate["internal_table_uid"]),
        "document_id": str(asset["document_id"]),
        "observed_scope": str(asset.get("scope") or "unknown"),
        "retrieval_support": str(route_candidate.get("retrieval_support") or "unknown"),
        "table_function": _redact_financial_numbers(
            (asset.get("table_function") or {}).get("label") or "Chưa phân loại"
        ),
        "source": {
            "relative_path": _relative_source_path(str(asset["source_path"]), repo_root),
            "page_no": asset.get("page_no"),
            "table_ordinal": int(asset["local_ordinal"]),
            "char_start": int(asset["char_start"]),
            "source_sha256": str(asset["source_sha256"]),
            "table_sha256": str(asset["table_sha256"]),
            "locator_sha256": str(route_candidate["exact_table_locator_sha256"]),
        },
        "scope_status": diagnostics["scope_status"],
        "period_status": diagnostics["period_status"],
        "fallback_period_status": diagnostics["fallback_period_status"],
        "matching_year_column_indices": diagnostics["matching_year_column_indices"],
        "unit_status": diagnostics["unit_status"],
        "source_unit_candidate": diagnostics["source_unit_candidate"],
        "column_headers": column_headers,
        "row_candidates": row_candidates,
        "selectable_cells": _selectable_cells(asset),
        "source_table": _source_table_structure(asset),
        "structural_candidate_ready": structural_candidate_ready,
        "raw_numeric_values_included": False,
    }


def _load_selected_assets(path: Path, selected_uids: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            asset = json.loads(line)
            uid = str(asset.get("internal_table_uid") or "")
            if uid in selected_uids:
                selected[uid] = asset
    missing = selected_uids - set(selected)
    if missing:
        raise ValueError(f"selected candidate UIDs missing from full tables: {sorted(missing)[:3]}")
    return selected


def _unresolved_questions(receipt_path: Path) -> set[int]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "ACCEPTED_NON_PROMOTING":
        raise ValueError("the preceding review intake was not accepted")
    authorization = receipt.get("authorization") or {}
    if authorization.get("human_verified_count") != 0:
        raise ValueError("preceding review intake unexpectedly grants human verification")
    return {
        int(decision["question_id"])
        for decision in receipt.get("decisions") or []
        if decision.get("decision") in {"REJECT", "UNCERTAIN"}
    }


def _preferred_table_uids(
    overrides: Mapping[str, Any], *, question_id: int, operand_id: str
) -> list[str]:
    by_question = overrides.get(str(question_id), {})
    if not isinstance(by_question, Mapping):
        raise ValueError(f"candidate override Q{question_id} must be an object")
    by_operand = by_question.get(operand_id, {})
    if not isinstance(by_operand, Mapping):
        raise ValueError(f"candidate override Q{question_id} {operand_id} must be an object")
    values = by_operand.get("prepend_table_uids", [])
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"candidate override Q{question_id} {operand_id} has invalid table UIDs")
    if len(values) != len(set(values)):
        raise ValueError(f"candidate override Q{question_id} {operand_id} has duplicate table UIDs")
    return values


def _candidate_sequence(
    *,
    route: Mapping[str, Any],
    preferred_uids: list[str],
    assets: Mapping[str, Mapping[str, Any]],
    maximum: int,
) -> list[Mapping[str, Any]]:
    route_candidates = list(route.get("candidates") or [])
    by_uid = {str(candidate["internal_table_uid"]): candidate for candidate in route_candidates}
    selected: list[Mapping[str, Any]] = []
    for uid in [*preferred_uids, *by_uid]:
        if any(str(candidate["internal_table_uid"]) == uid for candidate in selected):
            continue
        candidate = by_uid.get(uid)
        if candidate is None:
            asset = assets.get(uid)
            if asset is None:
                raise ValueError(f"feedback candidate UID is missing from full tables: {uid}")
            candidate = _route_candidate_from_asset(asset)
        selected.append(candidate)
        if len(selected) == maximum:
            break
    return selected


def build_multi_operand_review(
    *,
    config_path: Path,
    plans_path: Path,
    hybrid_review_queue_path: Path,
    assets_path: Path,
    prior_review_receipt_path: Path,
    output_dir: Path,
    repo_root: Path,
) -> dict[str, Any]:
    """Create review packets that include every required operand of a question."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("protocol") != PROTOCOL:
        raise ValueError("unexpected multi-operand review protocol")
    question_ids = [int(value) for value in config.get("question_ids") or []]
    if not question_ids or len(set(question_ids)) != len(question_ids):
        raise ValueError("config question_ids must be a non-empty unique list")
    unresolved = _unresolved_questions(prior_review_receipt_path)
    unexpected = sorted(set(question_ids) - unresolved)
    if unexpected:
        raise ValueError(
            "multi-operand queue may only continue unresolved prior-review questions: "
            f"{unexpected}"
        )
    max_tables = int(config["review"]["max_tables_per_operand"])
    max_rows = int(config["review"]["max_rows_per_table"])
    if max_tables < 1 or max_rows < 1:
        raise ValueError("review candidate limits must be positive")

    candidate_overrides = config.get("candidate_overrides", {})
    if not isinstance(candidate_overrides, Mapping):
        raise ValueError("candidate_overrides must be an object")
    plans = {
        int(plan["question_id"]): plan
        for plan in load_jsonl(plans_path)
        if int(plan["question_id"]) in set(question_ids)
    }
    missing_plans = sorted(set(question_ids) - set(plans))
    if missing_plans:
        raise ValueError(f"question plans missing: {missing_plans}")
    routes = {
        (int(route["question_id"]), str(route["operand_id"])): route
        for route in load_jsonl(hybrid_review_queue_path)
        if int(route["question_id"]) in set(question_ids)
    }

    expected_by_question: dict[int, list[dict[str, Any]]] = {}
    selected_uids: set[str] = set()
    for question_id in question_ids:
        expected_operands = [
            _expected_operand(operand)
            for operand in plans[question_id].get("operands") or []
            if operand.get("required") is True
        ]
        if len(expected_operands) < 2:
            raise ValueError(f"Q{question_id} is not a multi-operand question")
        expected_by_question[question_id] = expected_operands
        for operand in expected_operands:
            route = routes.get((question_id, operand["operand_id"]))
            if route is None:
                raise ValueError(f"Q{question_id} {operand['operand_id']} has no hybrid route")
            if (
                str(route.get("ticker")) != operand["ticker"]
                or int(route.get("report_year")) != operand["report_year"]
                or route.get("requested_scope") != operand["requested_scope"]
            ):
                raise ValueError(f"Q{question_id} {operand['operand_id']} route does not match plan")
            preferred_uids = _preferred_table_uids(
                candidate_overrides, question_id=question_id, operand_id=operand["operand_id"]
            )
            candidates = (route.get("candidates") or [])[:max_tables]
            if not candidates:
                raise ValueError(f"Q{question_id} {operand['operand_id']} has no candidate table")
            selected_uids.update(str(candidate["internal_table_uid"]) for candidate in candidates)
            selected_uids.update(preferred_uids)

    assets = _load_selected_assets(assets_path, selected_uids)
    packets: list[dict[str, Any]] = []
    structural_candidate_count = 0
    for order, question_id in enumerate(question_ids, start=1):
        plan = plans[question_id]
        operation = plan.get("operation_ast") or {}
        required_operands = expected_by_question[question_id]
        operand_packets: list[dict[str, Any]] = []
        for expected in required_operands:
            route = routes[(question_id, expected["operand_id"])]
            preferred_uids = _preferred_table_uids(
                candidate_overrides, question_id=question_id, operand_id=expected["operand_id"]
            )
            candidates = [
                _candidate_packet(
                    route_candidate=candidate,
                    asset=assets[str(candidate["internal_table_uid"])],
                    expected=expected,
                    metric_query=str(route["metric_core_query"]),
                    rank=rank,
                    repo_root=repo_root,
                    max_rows_per_table=max_rows,
                )
                for rank, candidate in enumerate(
                    _candidate_sequence(
                        route=route,
                        preferred_uids=preferred_uids,
                        assets=assets,
                        maximum=max_tables,
                    ),
                    start=1,
                )
            ]
            structural_candidate_count += sum(
                int(candidate["structural_candidate_ready"]) for candidate in candidates
            )
            operand_packets.append(
                {
                    **expected,
                    "route_id": str(route["route_id"]),
                    "metric_core_query": str(route["metric_core_query"]),
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                    "review_status": "READY_FOR_SOURCE_REVIEW",
                }
            )
        packet = {
            "protocol": PROTOCOL,
            "packet_id": _canonical_sha(
                {
                    "question_id": question_id,
                    "operation": operation,
                    "operands": [operand["route_id"] for operand in operand_packets],
                }
            ),
            "order": order,
            "question_id": question_id,
            "question": str(plan["question"]),
            "operation": operation,
            "operation_description": _operation_description(operation),
            "requested_unit": str(plan.get("requested_unit") or "unknown"),
            "required_operand_count": len(required_operands),
            "operands": operand_packets,
            "review_status": "READY_FOR_MULTI_OPERAND_REVIEW",
            "review_instruction": (
                "Chỉ chọn APPROVE khi mọi toán hạng bắt buộc đều có một bảng, dòng, "
                "cột kỳ và xác nhận scope/unit. Không nhập số tiền và không thực hiện phép tính."
            ),
            "raw_numeric_values_included": False,
            "human_verified": False,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
        }
        if _contains_forbidden_key(packet):
            raise AssertionError("internal multi-operand packet exposes a forbidden field")
        packets.append(packet)

    coverage = {
        "protocol": PROTOCOL,
        "question_count": len(packets),
        "question_ids": question_ids,
        "required_operand_count": sum(packet["required_operand_count"] for packet in packets),
        "candidate_table_count": sum(
            operand["candidate_count"]
            for packet in packets
            for operand in packet["operands"]
        ),
        "structural_candidate_count": structural_candidate_count,
        "operation_counts": dict(
            sorted(Counter(str(packet["operation"].get("op") or "unknown") for packet in packets).items())
        ),
        "prior_review_status": "ACCEPTED_NON_PROMOTING",
        "raw_numeric_values_included": False,
        "human_verified_count": 0,
        "may_authorize_evidence": False,
        "may_authorize_answer": False,
        "training_eligible": False,
        "submission_eligible": False,
    }

    output_dir.mkdir(parents=True)
    queue_path = output_dir / "multi_operand_review_queue_v1.jsonl"
    coverage_path = output_dir / "coverage_report_v1.json"
    _write_jsonl(queue_path, packets)
    coverage_path.write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
            "typed_operand_plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
            "hybrid_review_queue": {
                "path": str(hybrid_review_queue_path),
                "sha256": sha256_file(hybrid_review_queue_path),
            },
            "full_table_assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
            "prior_review_receipt": {
                "path": str(prior_review_receipt_path),
                "sha256": sha256_file(prior_review_receipt_path),
            },
        },
        "outputs": {
            path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in (queue_path, coverage_path)
        },
        "authorization": {
            "research_only": True,
            "human_verified": False,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return coverage


def validate_multi_operand_review(
    artifact_dir: Path, *, expected_question_ids: list[int] | None = None
) -> dict[str, Any]:
    """Verify complete operand coverage and the non-promoting data boundary."""
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected multi-operand review protocol")
    for filename, contract in (manifest.get("outputs") or {}).items():
        if sha256_file(artifact_dir / filename) != contract.get("sha256"):
            raise ValueError(f"output hash mismatch: {filename}")
    queue = list(load_jsonl(artifact_dir / "multi_operand_review_queue_v1.jsonl"))
    coverage = json.loads((artifact_dir / "coverage_report_v1.json").read_text(encoding="utf-8"))
    if expected_question_ids is not None and [int(row["question_id"]) for row in queue] != expected_question_ids:
        raise ValueError("multi-operand review question IDs do not match expectation")
    packet_ids = [str(packet.get("packet_id")) for packet in queue]
    if not queue or len(packet_ids) != len(set(packet_ids)):
        raise ValueError("review queue must have unique non-empty packets")
    operand_count = 0
    for packet in queue:
        if _contains_forbidden_key(packet):
            raise ValueError("review queue contains a forbidden numeric-value field")
        for field in (
            "human_verified",
            "may_authorize_evidence",
            "may_authorize_answer",
            "training_eligible",
            "submission_eligible",
        ):
            if packet.get(field) is not False:
                raise ValueError(f"review packet incorrectly enables {field}")
        operands = packet.get("operands")
        if not isinstance(operands, list) or len(operands) != int(packet["required_operand_count"]):
            raise ValueError("review packet does not contain every required operand")
        operand_ids = [str(operand.get("operand_id")) for operand in operands]
        if len(operand_ids) != len(set(operand_ids)):
            raise ValueError("review packet has duplicate operand IDs")
        for operand in operands:
            candidates = operand.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise ValueError("required operand has no review candidate")
            for candidate in candidates:
                if candidate.get("raw_numeric_values_included") is not False:
                    raise ValueError("candidate exposes raw numeric values")
                selectable_cells = candidate.get("selectable_cells")
                if not isinstance(selectable_cells, list) or not selectable_cells:
                    raise ValueError("candidate has no coordinate-only selectable cells")
                if len({(cell.get("row_index"), cell.get("column_index")) for cell in selectable_cells}) != len(selectable_cells):
                    raise ValueError("candidate has duplicate selectable cells")
                source_table = candidate.get("source_table") or {}
                if source_table.get("numeric_financial_values_exposed") is not False:
                    raise ValueError("candidate source table exposes financial values")
                if "rows" in source_table or not isinstance(source_table.get("structure"), list):
                    raise ValueError("candidate source table has an invalid safe structure")
        operand_count += len(operands)
    if int(coverage.get("question_count")) != len(queue):
        raise ValueError("coverage question count mismatch")
    if int(coverage.get("required_operand_count")) != operand_count:
        raise ValueError("coverage operand count mismatch")
    if coverage.get("human_verified_count") != 0 or coverage.get("submission_eligible") is not False:
        raise ValueError("coverage incorrectly promotes review data")
    return {
        "status": "PASS",
        "question_count": len(queue),
        "required_operand_count": operand_count,
        "human_verified_count": 0,
        "answer_eligible": False,
        "submission_eligible": False,
    }
