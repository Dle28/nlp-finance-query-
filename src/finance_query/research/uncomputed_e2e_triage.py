"""Explain why an E2E question is not yet answerable, without answering it.

This module joins four separate observations that must not be confused:

* what the question text states about entities;
* whether a typed calculation plan is complete;
* whether retrieval found source-table candidates; and
* where the locked E2E replay stopped.

The result is a research-only repair queue.  It never copies numeric values,
selects a candidate table, creates an evidence binding, or changes an E2E
route.  Its role is to prevent spending review time on the wrong problem --
for example, treating an unmaterialized route as an ambiguous question.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping


PROTOCOL = "vifinqa_uncomputed_e2e_triage_v1"
SCHEMA_VERSION = 1
SOURCE_CONTRACT = {
    "research_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN_VALUE_KEYS = frozenset({"answer", "raw_value", "cell_value", "pandas_query"})
HANDOFF_CANDIDATE_FIELDS = (
    "internal_table_uid",
    "document_id",
    "observed_scope",
    "retrieval_support",
    "lexical_rank",
    "dense_rank",
    "max_row_label_token_jaccard",
    "exact_table_locator",
    "exact_table_locator_sha256",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain JSON objects")
        rows.append(value)
    return rows


def _index_by_question(path: Path, *, label: str, expected_question_count: int) -> dict[int, dict[str, Any]]:
    index: dict[int, dict[str, Any]] = {}
    for row in _load_jsonl(path):
        value = row.get("question_id")
        if isinstance(value, bool):
            raise ValueError(f"{label} has an invalid question_id")
        try:
            question_id = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} has an invalid question_id") from error
        if question_id in index:
            raise ValueError(f"{label} has duplicate question_id {question_id}")
        index[question_id] = row
    if len(index) != expected_question_count:
        raise ValueError(
            f"{label} question count mismatch: expected {expected_question_count}, observed {len(index)}"
        )
    return index


def _group_by_question(path: Path) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in _load_jsonl(path):
        value = row.get("question_id")
        if isinstance(value, bool):
            continue
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        grouped[question_id].append(row)
    return dict(grouped)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _question_entities(taxonomy: Mapping[str, Any]) -> list[str]:
    values = taxonomy.get("entities_resolved") or []
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def _plan_entities(plan: Mapping[str, Any]) -> list[str]:
    values = plan.get("entities") or []
    if not values:
        values = [operand.get("entity") or operand.get("ticker") for operand in plan.get("operands") or []]
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def _binding_reason_codes(binding_packet: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    for stage in binding_packet.get("stages") or []:
        if not isinstance(stage, Mapping):
            continue
        for operand in stage.get("required_operands") or []:
            if not isinstance(operand, Mapping):
                continue
            values.update(str(code) for code in operand.get("reason_codes") or [] if str(code))
    return sorted(values)


def _source_signal(
    *,
    plan_status: str,
    routing: Mapping[str, Any],
    hybrid_packets: list[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    """Return a candidate-source signal, explicitly not an evidence verdict."""
    if plan_status != "complete":
        return "NOT_ASSESSED_PLAN_INCOMPLETE", []
    if str(routing.get("routing_status") or "") != "ROUTED":
        return "NO_TABLE_CANDIDATE_AFTER_RETRIEVAL", []
    buckets = sorted(
        {
            str(packet.get("priority_bucket") or "")
            for packet in hybrid_packets
            if str(packet.get("priority_bucket") or "")
        }
    )
    if not buckets:
        return "RETRIEVAL_CANDIDATE_METADATA_MISSING", buckets
    if "hard_review" in buckets:
        return "CANDIDATE_AMBIGUITY_SIGNAL", buckets
    if buckets == ["agreement_high_proxy"]:
        return "CANDIDATES_AGREE_PROXY_ONLY", buckets
    return "CANDIDATES_REQUIRE_ROW_COLUMN_CHECK", buckets


def _primary_blocker(
    *,
    plan: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    period_packet: Mapping[str, Any],
    binding_packet: Mapping[str, Any],
    execution: Mapping[str, Any],
    routing: Mapping[str, Any],
) -> tuple[str, str, str]:
    """Return blocker, layer, and a concrete non-authorizing repair action."""
    execution_status = str(execution.get("execution_status") or "")
    plan_status = str(plan.get("decomposition_status") or "")
    reasons = {str(value) for value in plan.get("reason_codes") or []}
    binding_reasons = set(_binding_reason_codes(binding_packet))
    question_entities = _question_entities(taxonomy)

    if execution_status == "execution_replay_ready":
        return (
            "EXACT_VALUE_EXECUTED_BUT_EVIDENCE_FIELDS_UNRESOLVED",
            "semantic_evidence",
            "REEXTRACT_EXACT_CELL_AND_BIND_ENTITY_PERIOD_SCOPE_UNIT",
        )
    if execution_status == "binding_conflict":
        if "PERIOD_CANDIDATE_BLOCKED" in binding_reasons:
            packet_status = str(period_packet.get("packet_status") or "")
            if packet_status == "packet_blocked":
                return (
                    "SOURCE_NAVIGATION_PACKET_MISSING",
                    "document_retrieval",
                    "CREATE_SOURCE_NAVIGATION_PACKET_FOR_ROUTE_COMPLETE",
                )
            if packet_status == "ambiguous_period_columns":
                return (
                    "PERIOD_COLUMN_AMBIGUITY",
                    "exact_cell_metadata",
                    "REPAIR_PERIOD_HEADER_AND_COMPARATIVE_COLUMN_BINDING",
                )
            if packet_status == "no_period_column":
                return (
                    "PERIOD_HEADER_NOT_EXTRACTED",
                    "document_extraction",
                    "REEXTRACT_TABLE_HEADER_AND_PERIOD_COLUMN",
                )
            return (
                "PERIOD_CANDIDATE_NOT_MATERIALIZED",
                "exact_cell_metadata",
                "INSPECT_PERIOD_CANDIDATE_PACKET_AND_SOURCE_HEADER",
            )
        if "PERIOD_COLUMN_MISSING" in binding_reasons:
            return (
                "PERIOD_HEADER_NOT_EXTRACTED",
                "document_extraction",
                "REEXTRACT_TABLE_HEADER_AND_PERIOD_COLUMN",
            )
        if "SOURCE_UNIT_ANCHOR_MISSING_OR_CONFLICTING" in binding_reasons:
            return (
                "SOURCE_UNIT_ANCHOR_MISSING_OR_CONFLICTING",
                "exact_cell_metadata",
                "REEXTRACT_UNIT_HEADER_AND_VALIDATE_CONVERSION",
            )
        return (
            "EXACT_CELL_BINDING_CONFLICT",
            "exact_cell_metadata",
            "RECHECK_EXACT_CELL_PACKET_AND_METADATA",
        )
    if plan_status != "complete":
        if "MISSING_ENTITY" in reasons:
            if question_entities:
                return (
                    "ENTITY_PRESENT_IN_QUESTION_BUT_NOT_IN_PLAN",
                    "question_to_plan",
                    "REPAIR_ENTITY_EXTRACTION_WITH_EXISTING_ALIAS_CATALOG",
                )
            return (
                "ENTITY_NOT_RESOLVED_FROM_QUESTION",
                "question_semantics",
                "KEEP_ABSTAIN_AND_REQUEST_OR_DEFINE_ENTITY_SCOPE",
            )
        if "UNRESOLVED_MULTI_STAGE_SELECTION" in reasons:
            return (
                "MULTI_STAGE_SELECTION_NOT_COMPILED",
                "program_compilation",
                "DEFINE_TYPED_SELECTION_STAGES_BEFORE_RETRIEVAL",
            )
        if "EXECUTOR_COMPILATION_REQUIRED" in reasons:
            return (
                "CALCULATION_PROGRAM_NOT_COMPILED",
                "program_compilation",
                "ADD_FAIL_CLOSED_TYPED_OPERATOR_AND_CANARY",
            )
        if "UNKNOWN_OPERAND_STRUCTURE" in reasons or "MISSING_OPERANDS" in reasons:
            return (
                "QUESTION_TO_OPERAND_PLAN_INCOMPLETE",
                "question_to_plan",
                "DEFINE_METRIC_OPERANDS_AND_TIME_ENTITY_CONTRACTS",
            )
        return (
            "TYPED_PLAN_INCOMPLETE",
            "question_to_plan",
            "REVIEW_PLAN_REQUIREMENTS_WITHOUT_SELECTING_VALUES",
        )
    route_status = str(binding_packet.get("route_status") or "")
    if route_status == "composed_execution_required":
        return (
            "COMPOSITION_GRAPH_NOT_MATERIALIZED",
            "e2e_route_materialization",
            "CREATE_HASH_BOUND_STAGE_GRAPH_AND_EXACT_CELL_PACKETS",
        )
    if route_status == "route_incomplete" and str(routing.get("routing_status") or "") == "ROUTED":
        return (
            "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E",
            "e2e_route_materialization",
            "CREATE_EXACT_CELL_PACKETS_FROM_HYBRID_REVIEW_CANDIDATES",
        )
    if str(routing.get("routing_status") or "") != "ROUTED":
        return (
            "NO_RETRIEVAL_CANDIDATE",
            "document_retrieval",
            "CHECK_CORPUS_ENTITY_YEAR_COVERAGE_THEN_EXPAND_RETRIEVAL",
        )
    return (
        "UNCLASSIFIED_E2E_BLOCKER",
        "investigation",
        "INSPECT_SOURCE_PACKET_AND_REPLAY_RECEIPT",
    )


def _evidence_statuses(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str((row.get("evidence_binding") or {}).get("binding_status") or "MISSING")
        for row in rows
    )
    return dict(sorted(counts.items()))


def _candidate_handoff(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Project a hybrid packet to the source-navigation fields needed next.

    This deliberately retains neither table rows nor numeric cells.  The next
    step must still inspect the original source and bind a row/column exactly.
    """
    candidates = []
    for candidate in packet.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        candidates.append(
            {field: candidate.get(field) for field in HANDOFF_CANDIDATE_FIELDS if field in candidate}
        )
    return {
        "question_id": packet.get("question_id"),
        "route_id": packet.get("route_id"),
        "review_packet_id": packet.get("review_packet_id"),
        "operand_id": packet.get("operand_id"),
        "ticker": packet.get("ticker"),
        "report_year": packet.get("report_year"),
        "requested_scope": packet.get("requested_scope"),
        "metric_core_query": packet.get("metric_core_query"),
        "priority_bucket": packet.get("priority_bucket"),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def _contains_forbidden_value_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in FORBIDDEN_VALUE_KEYS or _contains_forbidden_value_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_value_key(item) for item in value)
    return False


def _report(summary: Mapping[str, Any]) -> str:
    primary = summary["primary_blocker_counts"]
    actions = summary["repair_action_counts"]
    lines = [
        "# Triage câu chưa tính được trong E2E",
        "",
        "Báo cáo này phân biệt lỗi ở câu hỏi, kế hoạch tính, truy hồi nguồn,",
        "và bước ghép nguồn vào E2E. Không có giá trị số, đáp án hay quyền nộp bài.",
        "",
        "## Kết quả",
        "",
        f"- Tổng số câu: {summary['question_count']}",
        f"- Đã thực thi số học nhưng chưa đủ chứng cứ: {summary['executed_but_not_authorized_count']}",
        f"- Chưa thực thi: {summary['not_computed_count']}",
        (
            "- Gói chuyển tiếp sang bước xác nhận ô nguồn: "
            f"{summary['route_repair_handoff_count']} tuyến cho "
            f"{summary['route_repair_handoff_question_count']} câu"
        ),
        "",
        "## Lý do chính",
        "",
    ]
    lines.extend(f"- `{name}`: {count}" for name, count in primary.items())
    lines.extend(["", "## Việc khắc phục theo lô", ""])
    lines.extend(f"- `{action}`: {count}" for action, count in actions.items())
    lines.extend([
        "",
        "## Cách đọc",
        "",
        "- `ENTITY_NOT_RESOLVED_FROM_QUESTION` là câu chưa đủ thực thể; giữ abstain.",
        "- `ENTITY_PRESENT_IN_QUESTION_BUT_NOT_IN_PLAN` là lỗi ghép entity của pipeline, không phải thiếu dữ liệu.",
        "- `RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E` nghĩa là đã có bảng ứng viên nhưng chưa được phép chọn ô nguồn; cần tạo packet exact-cell.",
        "- `CANDIDATE_AMBIGUITY_SIGNAL` chỉ là tín hiệu cần kiểm tra bảng/dòng/cột, không kết luận tài liệu mơ hồ.",
    ])
    return "\n".join(lines) + "\n"


def build_uncomputed_e2e_triage(
    *,
    plans_path: Path,
    taxonomy_path: Path,
    routing_status_path: Path,
    hybrid_review_queue_path: Path,
    period_packets_path: Path,
    bindings_path: Path,
    execution_path: Path,
    evidence_bindings_path: Path,
    certificates_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Build an immutable research-only root-cause and repair queue."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    plans = _index_by_question(plans_path, label="typed plans", expected_question_count=expected_question_count)
    taxonomy = _index_by_question(taxonomy_path, label="taxonomy", expected_question_count=expected_question_count)
    routing = _index_by_question(routing_status_path, label="routing status", expected_question_count=expected_question_count)
    period_packets = _index_by_question(period_packets_path, label="period packets", expected_question_count=expected_question_count)
    bindings = _index_by_question(bindings_path, label="E2E bindings", expected_question_count=expected_question_count)
    execution = _index_by_question(execution_path, label="E2E execution", expected_question_count=expected_question_count)
    certificates = _index_by_question(certificates_path, label="answer certificates", expected_question_count=expected_question_count)
    question_ids = set(plans)
    if any(set(index) != question_ids for index in (taxonomy, routing, period_packets, bindings, execution, certificates)):
        raise ValueError("input question ID universes do not match")
    hybrid_by_question = _group_by_question(hybrid_review_queue_path)
    evidence_by_question = _group_by_question(evidence_bindings_path)

    triage_rows: list[dict[str, Any]] = []
    handoff_rows: list[dict[str, Any]] = []
    blocker_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    source_signal_counts: Counter[str] = Counter()
    for question_id in sorted(question_ids):
        plan = plans[question_id]
        taxonomy_row = taxonomy[question_id].get("taxonomy") or taxonomy[question_id]
        binding_packet = bindings[question_id]
        execution_row = execution[question_id]
        certificate = certificates[question_id].get("answer_certificate") or certificates[question_id]
        hybrid_packets = hybrid_by_question.get(question_id, [])
        plan_status = str(plan.get("decomposition_status") or "")
        source_signal, review_buckets = _source_signal(
            plan_status=plan_status,
            routing=routing[question_id],
            hybrid_packets=hybrid_packets,
        )
        blocker, layer, action = _primary_blocker(
            plan=plan,
            taxonomy=taxonomy_row,
            period_packet=period_packets[question_id],
            binding_packet=binding_packet,
            execution=execution_row,
            routing=routing[question_id],
        )
        question_entities = _question_entities(taxonomy_row)
        plan_entities = _plan_entities(plan)
        row = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "execution_status": execution_row.get("execution_status"),
            "certificate_status": certificate.get("status"),
            "typed_plan_status": plan_status,
            "typed_plan_reason_codes": sorted(str(value) for value in plan.get("reason_codes") or []),
            "question_entity_status": (
                "ENTITY_STATED_OR_RESOLVED_FROM_QUESTION"
                if question_entities
                else "ENTITY_NOT_RESOLVED_FROM_QUESTION"
            ),
            "question_entities": question_entities,
            "plan_entity_status": (
                "ENTITY_PRESENT_IN_PLAN" if plan_entities else "ENTITY_MISSING_FROM_PLAN"
            ),
            "plan_entities": plan_entities,
            "retrieval_status": routing[question_id].get("routing_status"),
            "table_candidate_count": routing[question_id].get("table_candidate_count", 0),
            "source_candidate_signal": source_signal,
            "source_review_buckets": review_buckets,
            "e2e_route_status": binding_packet.get("route_status"),
            "binding_packet_status": binding_packet.get("binding_packet_status"),
            "binding_reason_codes": _binding_reason_codes(binding_packet),
            "period_packet_status": period_packets[question_id].get("packet_status"),
            "evidence_binding_status_counts": _evidence_statuses(evidence_by_question.get(question_id, [])),
            "primary_blocker": blocker,
            "blocker_layer": layer,
            "repair_action": action,
            "source_contract": dict(SOURCE_CONTRACT),
        }
        if _contains_forbidden_value_key(row):
            raise ValueError("triage row unexpectedly contains a numeric-answer field")
        triage_rows.append(row)
        if plan_status == "complete" and row["execution_status"] != "execution_replay_ready":
            for packet in hybrid_packets:
                handoff = {
                    "schema_version": SCHEMA_VERSION,
                    "protocol": PROTOCOL,
                    "repair_action": action,
                    "primary_blocker": blocker,
                    "blocker_layer": layer,
                    "exact_cell_binding_required": True,
                    "exact_row_and_column_unresolved": True,
                    "source_navigation": _candidate_handoff(packet),
                    "source_contract": dict(SOURCE_CONTRACT),
                }
                if _contains_forbidden_value_key(handoff):
                    raise ValueError("repair handoff unexpectedly contains a numeric-answer field")
                handoff_rows.append(handoff)
        blocker_counts[blocker] += 1
        action_counts[action] += 1
        layer_counts[layer] += 1
        source_signal_counts[source_signal] += 1

    executed_but_not_authorized = sum(
        row["execution_status"] == "execution_replay_ready" and row["certificate_status"] != "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
        for row in triage_rows
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "question_count": len(triage_rows),
        "not_computed_count": sum(row["execution_status"] != "execution_replay_ready" for row in triage_rows),
        "executed_but_not_authorized_count": executed_but_not_authorized,
        "route_repair_handoff_count": len(handoff_rows),
        "route_repair_handoff_question_count": len(
            {row["source_navigation"]["question_id"] for row in handoff_rows}
        ),
        "primary_blocker_counts": dict(sorted(blocker_counts.items())),
        "blocker_layer_counts": dict(sorted(layer_counts.items())),
        "repair_action_counts": dict(sorted(action_counts.items())),
        "source_candidate_signal_counts": dict(sorted(source_signal_counts.items())),
        "source_contract": dict(SOURCE_CONTRACT),
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        triage_path = temporary_dir / "question_triage_v1.jsonl"
        handoff_path = temporary_dir / "route_repair_handoff_v1.jsonl"
        summary_path = temporary_dir / "uncomputed_e2e_triage_summary_v1.json"
        report_path = temporary_dir / "uncomputed_e2e_triage_report_v1.md"
        _write_jsonl(triage_path, triage_rows)
        _write_jsonl(handoff_path, handoff_rows)
        _write_json(summary_path, summary)
        report_path.write_text(_report(summary), encoding="utf-8")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "inputs": {
                name: {"path": str(path), "sha256": sha256_file(path)}
                for name, path in {
                    "plans": plans_path,
                    "taxonomy": taxonomy_path,
                    "routing_status": routing_status_path,
                    "hybrid_review_queue": hybrid_review_queue_path,
                    "period_packets": period_packets_path,
                    "bindings": bindings_path,
                    "execution": execution_path,
                    "evidence_bindings": evidence_bindings_path,
                    "certificates": certificates_path,
                }.items()
            },
            "outputs": {
                "question_triage": {"path": triage_path.name, "sha256": sha256_file(triage_path)},
                "route_repair_handoff": {"path": handoff_path.name, "sha256": sha256_file(handoff_path)},
                "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                "report": {"path": report_path.name, "sha256": sha256_file(report_path)},
            },
            "source_contract": dict(SOURCE_CONTRACT),
        }
        _write_json(temporary_dir / "manifest.json", manifest)
        temporary_dir.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return summary


def validate_uncomputed_e2e_triage(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Validate hashes, coverage, and the research-only output contract."""
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("triage manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected triage protocol")
    for descriptor in (manifest.get("outputs") or {}).values():
        if not isinstance(descriptor, Mapping):
            raise ValueError("triage manifest output is invalid")
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("triage output hash mismatch")
    rows = _load_jsonl(artifact_dir / "question_triage_v1.jsonl")
    if len(rows) != expected_question_count:
        raise ValueError("triage question count mismatch")
    ids = {row.get("question_id") for row in rows}
    if len(ids) != expected_question_count or any(not isinstance(value, int) for value in ids):
        raise ValueError("triage question IDs are incomplete")
    if any(
        row.get("source_contract") != SOURCE_CONTRACT or _contains_forbidden_value_key(row)
        for row in rows
    ):
        raise ValueError("triage research-only contract was violated")
    handoff_rows = _load_jsonl(artifact_dir / "route_repair_handoff_v1.jsonl")
    if any(
        row.get("source_contract") != SOURCE_CONTRACT
        or row.get("exact_cell_binding_required") is not True
        or row.get("exact_row_and_column_unresolved") is not True
        or _contains_forbidden_value_key(row)
        for row in handoff_rows
    ):
        raise ValueError("triage repair handoff contract was violated")
    summary = json.loads((artifact_dir / "uncomputed_e2e_triage_summary_v1.json").read_text(encoding="utf-8"))
    if summary.get("question_count") != expected_question_count:
        raise ValueError("triage summary question count mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "not_computed_count": summary.get("not_computed_count"),
        "executed_but_not_authorized_count": summary.get("executed_but_not_authorized_count"),
        "route_repair_handoff_count": summary.get("route_repair_handoff_count"),
    }
