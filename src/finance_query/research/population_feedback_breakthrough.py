"""Build a population-level feedback ledger and breakthrough queue.

This module is intentionally research-only.  It joins one full-population
feedback preparation with a prior model run and an independent semantic audit,
then compresses the evidence into reusable failure families.  It never emits
answer values, source cell values, or a question-specific prediction rule.

The central hypothesis is deliberately narrow:
``SOURCE_BOUND_PLAN_COMPLETENESS_BEFORE_ROUTE_PRIORITY``.  A whole-question
plan (AST, operands, semantic dimensions, source coordinates, and replay) must
be complete before route/tier priority is allowed to choose it.  The ledger
does not claim that this hypothesis improves answer accuracy; an independent
gold scorer is still required.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


PROTOCOL = "vifinqa_population_feedback_breakthrough_v1"
PRIMARY_FAMILY = "SOURCE_BOUND_PLAN_COMPLETENESS"
FULL_POPULATION_LANE = "FULL_POPULATION"
SEMANTIC_AUDIT_LANE = "DISCOVERY_DIAGNOSTIC_SLICE"
FEEDBACK_TRANSPORT_LANE = "FEEDBACK_TRANSPORT"

# These keys are data-bearing or authority-bearing.  The output is a feedback
# ledger, so it may contain counts and tracking IDs but must not copy any
# answer/source value or authority decision from an input artifact.
FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "candidate_answer",
        "candidate_answer_decimal",
        "gold_answer",
        "gold_value",
        "model_answer",
        "model_value",
        "predicted_answer",
        "predicted_value",
        "raw_value",
        "raw_cell_value",
        "source_value",
        "cell_value",
        "numeric_cells",
        "pandas_query",
        "evidence_value",
        "answer_authorized",
        "evidence_authorized",
        "training_eligible",
        "promotion_allowed",
        "submission_eligible",
        "release_authorized",
    }
)

_SEMANTIC_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "TEMPORAL_BINDING",
        ("PERIOD", "YEAR", "ACQUISITION_DATE", "CLOSING_BALANCE"),
    ),
    (
        "UNIT_SCALE",
        ("CURRENCY", "PER_SHARE", "PERCENTAGE"),
    ),
    (
        "FORMULA_TOTAL_OPERAND",
        (
            "TOTAL",
            "COMPONENT",
            "BOND_",
            "DERIVATIVE",
            "SEGMENT",
            "LENDING_TOTAL",
            "SUBSIDIARY_INVESTMENT_TOTAL",
        ),
    ),
    (
        "ENTITY_SCOPE_PROVENANCE",
        (
            "ENTITY",
            "COUNTERPARTY",
            "RELATED_PARTY",
            "STOCK_",
            "DOMESTIC_FOREIGN",
        ),
    ),
    (
        "METRIC_ROW_BINDING",
        ("METRIC_", "ROW_", "PROVISION", "REPORT_NAVIGATION"),
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain an object")
            rows.append(value)
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _descriptor(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _int_question_id(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if result < 1:
        raise ValueError(f"{label} must be positive")
    return result


def _by_question(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
    expected_question_count: int | None = None,
) -> dict[int, Mapping[str, Any]]:
    indexed: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        question_id = _int_question_id(row.get("question_id"), label=f"{label} question_id")
        if question_id in indexed:
            raise ValueError(f"duplicate {label} question_id: {question_id}")
        indexed[question_id] = row
    if expected_question_count is not None:
        expected = set(range(1, expected_question_count + 1))
        if set(indexed) != expected:
            missing = sorted(expected - set(indexed))[:8]
            extra = sorted(set(indexed) - expected)[:8]
            raise ValueError(
                f"{label} population mismatch: expected={expected_question_count} "
                f"actual={len(indexed)} missing={missing} extra={extra}"
            )
    return indexed


def _text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _token(value: object) -> str:
    return "_".join(_text(value).upper().split())


def semantic_reason_family(reason_code: object) -> str:
    """Map one guard code to a reusable family, never to a question ID."""

    code = _token(reason_code)
    for family, fragments in _SEMANTIC_FAMILY_RULES:
        if any(fragment in code for fragment in fragments):
            return family
    return "OTHER_SEMANTIC_GUARD"


def _sequence_strings(value: object) -> list[str]:
    if isinstance(value, str):
        values: Sequence[object] = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
    else:
        values = []
    return sorted({_text(item) for item in values if _text(item)})


def _normalize_e2e_reason(value: object) -> str:
    """Remove question-ID segments from diagnostic reason telemetry."""

    code = _text(value)
    if not code:
        return ""
    parts = [part for part in code.split(":") if not re.fullmatch(r"q\d+", part, re.IGNORECASE)]
    normalized = ":".join(parts)
    # Formula diagnostics also carry stage/role names.  Those are useful in
    # a per-run trace but would fragment a population family into hundreds of
    # pseudo-categories, so retain the reusable contract failure only.
    if normalized.startswith("FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION"):
        return "FORMULA_COMPATIBILITY_CONSTRAINT_VIOLATION"
    if parts and parts[0].casefold() == "stage":
        for part in reversed(parts[1:]):
            if part.startswith(
                (
                    "BINDING_",
                    "PERIOD_",
                    "VARIABLE_",
                    "UNIT_",
                    "SCOPE_",
                    "ENTITY_",
                    "SOURCE_",
                )
            ):
                return part
        return "STAGE_BINDING_CONTRACT_FAILURE"
    if normalized.startswith("COUNTERFACTUAL_") and len(parts) > 2:
        return ":".join(parts[:2])
    return normalized


def _route_state(packet: Mapping[str, Any]) -> str:
    typed = packet.get("typed_question")
    typed = typed if isinstance(typed, Mapping) else {}
    route_status = _token(typed.get("research_route_status"))
    if route_status == "COMPOSED_EXECUTION_REQUIRED":
        return PRIMARY_FAMILY
    if route_status == "ROUTE_INCOMPLETE":
        return "ROUTE_MATERIALIZATION"
    if route_status == "ROUTE_COMPLETE":
        return "ROUTE_COMPLETE_UNVERIFIED"
    return "ROUTE_STATE_UNCLASSIFIED"


def _route_family(packet: Mapping[str, Any]) -> str:
    proposal = packet.get("proposal")
    proposal = proposal if isinstance(proposal, Mapping) else {}
    route = _token(proposal.get("answer_route"))
    if "SEMANTIC_CELL" in route:
        return "SEMANTIC_CELL_HEURISTIC"
    if "ARG_EXTREME" in route or any(
        marker in route for marker in ("RATIO", "GROWTH", "SUBTRACT", "AGGREGATE", "COMPOSED", "FORMULA")
    ):
        return "FORMULA_OR_OPERATION"
    if "MULTI_ENTITY" in route or "MULTI_ENTITY_PLAN" in route:
        return "MULTI_ENTITY"
    if "EXACT_EXECUTION" in route or "MODEL_STAGED" in route:
        return "DETERMINISTIC_OR_MODEL_REPLAY"
    if "SOURCE_FIRST" in route or "DIRECT_SOURCE" in route:
        return "SOURCE_FIRST"
    if "RAW_CORPUS" in route:
        return "SOURCE_RECOVERY"
    if "FALLBACK" in route:
        return "FALLBACK"
    return "OTHER_ROUTE"


def _feedback_view(record: Mapping[str, Any]) -> dict[str, Any]:
    feedback = record.get("feedback")
    feedback = feedback if isinstance(feedback, Mapping) else {}
    return {
        "model_status": _text(record.get("model_status")) or "UNKNOWN",
        "failure_class": _text(feedback.get("failure_class")) or "UNKNOWN",
        "decision": _text(feedback.get("decision")) or "UNKNOWN",
        "reason_codes": _sequence_strings(feedback.get("reason_codes")),
        "prompt_profile": _text(record.get("prompt_profile")) or "UNKNOWN",
        "contract_error": _text(record.get("error_code")) or None,
    }


def _feedback_summary_view(summary: Mapping[str, Any], *, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    authority = {
        key: summary.get(key) is True
        for key in (
            "answer_authorized",
            "evidence_authorized",
            "training_eligible",
            "promotion_allowed",
            "submission_eligible",
            "release_authorized",
        )
    }
    if any(authority.values()):
        raise ValueError("feedback input contains an unexpected authority=true state")
    view = {
        "status": _text(summary.get("status")) or "UNKNOWN",
        "blocked_count": summary.get("blocked_count"),
        "feedback_record_count": summary.get("feedback_record_count"),
        "model_evaluated_count": summary.get("model_evaluated_count"),
        "valid_feedback_count": summary.get("valid_feedback_count"),
        "model_status_counts": dict(summary.get("model_status_counts") or {}),
        "failure_class_counts": dict(summary.get("failure_class_counts") or {}),
        "authority_all_false": not any(authority.values()),
    }
    if "model_output_invalid_count" in summary:
        view["model_output_invalid_count"] = summary.get("model_output_invalid_count")
    if "runtime_error_count" in summary:
        view["runtime_error_count"] = summary.get("runtime_error_count")
    if isinstance(manifest, Mapping):
        authority_manifest = manifest.get("authority")
        view["real_model_invoked"] = bool(
            isinstance(authority_manifest, Mapping)
            and authority_manifest.get("real_model_invoked") is True
        )
        model = manifest.get("model")
        if isinstance(model, Mapping):
            view["model_id"] = _text(model.get("id")) or None
            view["model_revision"] = _text(model.get("revision")) or None
    return view


def _semantic_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    counts = summary.get("counts")
    counts = counts if isinstance(counts, Mapping) else {}
    records = summary.get("records")
    records = records if isinstance(records, list) else []
    by_family_events: Counter[str] = Counter()
    by_family_questions: defaultdict[str, set[int]] = defaultdict(set)
    by_family_codes: defaultdict[str, Counter[str]] = defaultdict(Counter)
    raw_events = counts.get("rejection_reason_counts")
    raw_events = raw_events if isinstance(raw_events, Mapping) else {}
    for raw_code, raw_count in raw_events.items():
        code = _text(raw_code)
        try:
            event_count = int(raw_count)
        except (TypeError, ValueError):
            continue
        family = semantic_reason_family(code)
        by_family_events[family] += event_count
        by_family_codes[family][code] += event_count
    for record in records:
        if not isinstance(record, Mapping):
            continue
        question_id = _int_question_id(record.get("question_id"), label="semantic audit question_id")
        raw_codes = record.get("semantic_rejection_reason_codes")
        if not isinstance(raw_codes, list):
            raw_codes = []
            for rejection in record.get("semantic_rejections") or []:
                if isinstance(rejection, Mapping):
                    raw_codes.extend(rejection.get("reason_codes") or [])
        for raw_code in set(_sequence_strings(raw_codes)):
            by_family_questions[semantic_reason_family(raw_code)].add(question_id)
    family_rows: list[dict[str, Any]] = []
    family_names = sorted(set(by_family_events) | set(by_family_questions))
    for family in family_names:
        top_codes = [
            {"reason_code": code, "guard_event_count": count}
            for code, count in by_family_codes[family].most_common(8)
        ]
        family_rows.append(
            {
                "failure_family": family,
                "lane": SEMANTIC_AUDIT_LANE,
                "question_count": len(by_family_questions[family]),
                "guard_event_count": by_family_events[family],
                "top_reason_codes": top_codes,
                "event_counts_are_guard_rejections": True,
                "accuracy_measured": False,
            }
        )
    family_rows.sort(key=lambda row: (-int(row["guard_event_count"]), row["failure_family"]))
    return {
        "protocol": _text(summary.get("protocol")) or "UNKNOWN",
        "question_count": len(records),
        "status_counts": dict(counts.get("status_counts") or {}),
        "semantic_abstain_count": counts.get("semantic_abstain_count"),
        "semantic_candidate_survived_count": counts.get("semantic_candidate_survived_count"),
        "guard_rejection_event_count": counts.get("guard_rejection_event_count"),
        "accuracy_measured": bool((summary.get("policy") or {}).get("accuracy_measured"))
        if isinstance(summary.get("policy"), Mapping)
        else False,
        "semantic_accuracy": "NOT_MEASURED",
        "family_rows": family_rows,
        "question_ids": sorted(
            _int_question_id(record.get("question_id"), label="semantic audit question_id")
            for record in records
            if isinstance(record, Mapping)
        ),
    }


def _question_semantic_view(record: Mapping[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {
            "in_audit_slice": False,
            "semantic_contract_status": "NOT_IN_AUDIT_SLICE",
            "reason_codes": [],
            "failure_families": [],
            "guard_rejection_count": 0,
        }
    codes = _sequence_strings(record.get("semantic_rejection_reason_codes"))
    if not codes:
        for rejection in record.get("semantic_rejections") or []:
            if isinstance(rejection, Mapping):
                codes.extend(_sequence_strings(rejection.get("reason_codes")))
        codes = sorted(set(codes))
    return {
        "in_audit_slice": True,
        "semantic_contract_status": _text(record.get("semantic_contract_status")) or "UNKNOWN",
        "reason_codes": codes,
        "failure_families": sorted({semantic_reason_family(code) for code in codes}),
        "guard_rejection_count": int(record.get("semantic_rejection_count") or 0),
    }


def _e2e_metrics(
    path: Path | None,
    *,
    expected_question_count: int,
) -> dict[str, Any]:
    """Summarize independent E2E receipts without copying answer-bearing data."""

    if path is None:
        return {
            "present": False,
            "status": "NOT_SUPPLIED",
            "question_count": 0,
            "e2e_status_counts": {},
            "failure_reason_counts": {},
            "top_failure_reasons": [],
            "accuracy_measured": False,
        }
    rows = _read_jsonl(path)
    _by_question(rows, label="e2e receipts", expected_question_count=expected_question_count)
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for row in rows:
        status = _text(row.get("e2e_status")) or _text(row.get("status")) or "UNKNOWN"
        status_counts[status] += 1
        for reason in _sequence_strings(row.get("failure_reason_codes")):
            normalized_reason = _normalize_e2e_reason(reason)
            if normalized_reason:
                reason_counts[normalized_reason] += 1
    return {
        "present": True,
        "status": "PASS_INPUT_CLOSURE",
        "question_count": len(rows),
        "e2e_status_counts": dict(sorted(status_counts.items())),
        "failure_reason_counts": dict(reason_counts),
        "top_failure_reasons": [
            {"reason_code": reason, "receipt_count": count}
            for reason, count in reason_counts.most_common(12)
        ],
        "accuracy_measured": False,
    }


def _assert_value_free(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text in FORBIDDEN_OUTPUT_KEYS:
                raise AssertionError(f"forbidden output key at {path}: {key_text}")
            _assert_value_free(nested, path=f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_value_free(nested, path=f"{path}[{index}]")


def _research_descriptors(
    research_docs: Sequence[Path],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in research_docs:
        descriptor = _descriptor(path)
        rows.append(
            {
                "path": descriptor["path"],
                "sha256": descriptor["sha256"],
                "bytes": descriptor["bytes"],
            }
        )
    return rows


def _default_research_findings() -> list[dict[str, Any]]:
    return [
        {
            "study": "CANDIDATE_PLAN_SELECTOR_REVIEW_V1",
            "finding": "Whole-question AST, operand graph, semantic dimensions, source coordinates and replay must be complete before route priority; Question-ID exceptions are forbidden.",
            "role": "system_bottleneck_hypothesis",
        },
        {
            "study": "RESEARCH_INTEGRATION_LOG_V1",
            "finding": "The answer-level selector shadow A/B previously produced zero JSON-value and answer diffs, so selector integration alone is not evidence of an accuracy lift.",
            "role": "negative_control",
        },
        {
            "study": "FEEDBACK_REMEDIATION_REVIEW_V1",
            "finding": "The prepared full-population feedback lane has 1,012 packets but no model-evaluated records; the earlier real-model run had 1,012 invalid outputs.",
            "role": "feedback_transport_blocker",
        },
        {
            "study": "independent_qna_review_v1_20260831_r9",
            "finding": "The independent semantic audit covers 172 diagnostic direct-lookup questions, records 29,405 guard rejection events and has no local gold accuracy measurement.",
            "role": "diagnostic_failure_family_evidence",
        },
    ]


def _family_queue(
    *,
    route_state_counts: Mapping[str, int],
    route_family_counts: Mapping[str, int],
    source_scope_not_materialized_count: int,
    current_feedback: Mapping[str, Any],
    prior_feedback: Mapping[str, Any],
    semantic_metrics: Mapping[str, Any],
    e2e_metrics: Mapping[str, Any],
    population_count: int,
    evidence_descriptors: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    missing_complete = int(route_state_counts.get(PRIMARY_FAMILY, 0)) + int(
        route_state_counts.get("ROUTE_MATERIALIZATION", 0)
    )
    queue: list[dict[str, Any]] = [
        {
            "queue_rank": 1,
            "failure_family": PRIMARY_FAMILY,
            "lane": FULL_POPULATION_LANE,
            "affected_question_count": missing_complete,
            "affected_question_share": round(missing_complete / population_count, 6),
            "evidence": {
                "route_state_counts": dict(route_state_counts),
                "source_scope_not_materialized_count": source_scope_not_materialized_count,
                "e2e_status_counts": dict(e2e_metrics.get("e2e_status_counts") or {}),
                "e2e_top_failure_reasons": list(e2e_metrics.get("top_failure_reasons") or [])[:8],
            },
            "hypothesis": "The submission boundary can carry a route answer without a complete source-bound whole-question plan; completing the plan and replay before route priority should reduce partial-plan selection across families.",
            "candidate_change": "Keep all route-family plans in CandidatePlanSet; require operation AST, complete operand set, period/entity/scope/unit consistency, source coordinates, source hash when available, and deterministic replay before route/tier priority.",
            "population_acceptance": "Run control and candidate on all 1,012 questions with the same input closure; report answer and execution accuracy from an independent scorer, plus non-target regressions by family.",
            "status": "READY_FOR_FULL_POPULATION_AB",
            "authority": "none",
            "scorer_required": True,
            "evidence_files": [
                evidence_descriptors["context_packets"],
                evidence_descriptors["semantic_summary"],
            ],
        },
        {
            "queue_rank": 2,
            "failure_family": "FEEDBACK_OUTPUT_CONTRACT",
            "lane": FEEDBACK_TRANSPORT_LANE,
            "affected_question_count": population_count,
            "affected_question_share": 1.0,
            "evidence": {
                "current_status": current_feedback.get("status"),
                "current_model_evaluated_count": current_feedback.get("model_evaluated_count"),
                "current_valid_feedback_count": current_feedback.get("valid_feedback_count"),
                "prior_status": prior_feedback.get("status"),
                "prior_model_output_invalid_count": prior_feedback.get("model_output_invalid_count"),
            },
            "hypothesis": "The feedback loop is bottlenecked by transport/schema validity rather than by the absence of blocked questions; a compact prompt plus a strict one-object output contract should make model feedback observable.",
            "candidate_change": "Run the immutable numeric-free packet package with en_system_vi_context_compact_v2 and a bounded generation budget, then validate every row before any family synthesis.",
            "population_acceptance": "Model-evaluated_count > 0 and valid_feedback_count > 0 with contract-invalid count reported; no output may authorize answers or training.",
            "status": "WAITING_FOR_REMOTE_MODEL_RUN",
            "authority": "none",
            "scorer_required": False,
            "evidence_files": [evidence_descriptors["feedback_summary"]],
        },
    ]
    semantic_rows = list(semantic_metrics.get("family_rows") or [])
    next_rank = 3
    for row in semantic_rows:
        queue.append(
            {
                "queue_rank": next_rank,
                "failure_family": row["failure_family"],
                "lane": SEMANTIC_AUDIT_LANE,
                "affected_question_count": row["question_count"],
                "affected_question_share": round(
                    int(row["question_count"]) / int(semantic_metrics["question_count"] or 1),
                    6,
                ),
                "evidence": {
                    "guard_rejection_event_count": row["guard_event_count"],
                    "top_reason_codes": row["top_reason_codes"],
                    "route_family_counts_full_population": dict(route_family_counts),
                },
                "hypothesis": "The family-level semantic contract is rejecting or disambiguating a recurring phenomenon that can be tested as one reusable rule.",
                "candidate_change": "Design one family-level contract/ablation for this phenomenon and keep it shadow-only until independent gold evaluation proves its effect.",
                "population_acceptance": "Preserve an untouched evaluation subset, then run the frozen candidate on the complete population with answer/execution accuracy and regressions.",
                "status": "DISCOVERY_ONLY_UNTIL_GOLD",
                "authority": "none",
                "scorer_required": True,
                "evidence_files": [evidence_descriptors["semantic_summary"]],
            }
        )
        next_rank += 1
    return queue


def _report(summary: Mapping[str, Any], output_dir: Path) -> str:
    counts = summary["population_counts"]
    feedback = summary["feedback_transport"]
    semantic = summary["semantic_audit"]
    lines = [
        "# Population feedback breakthrough handoff",
        "",
        "This is a research/diagnostic artifact. It is value-free, non-authorizing, and does not claim an accuracy gain.",
        "",
        "## Hypothesis/family",
        "",
        f"- `{summary['hypothesis_family']}`",
        f"- Rule: `{summary['hypothesis_rule']}`",
        "- Question IDs are retained only for traceability in the per-question ledger; no Question-ID rule, allowlist, or exception is generated.",
        "",
        "## Control and candidate",
        "",
        f"- Control fingerprint: `{summary['control_fingerprint']}`",
        f"- Candidate fingerprint: `{summary['candidate_fingerprint']}`",
        f"- Population: `{counts['total_population']}` full-population records; semantic audit slice `{semantic['question_count']}` is diagnostic only.",
        "- Split policy: no tuning result is promoted; the next frozen A/B must preserve an untouched evaluation subset and then rerun the complete population.",
        "",
        "## Feedback expansion evidence",
        "",
        f"- Current prepared feedback: `{feedback['current']['status']}`, packets `{feedback['current']['blocked_count']}`, model-evaluated `{feedback['current']['model_evaluated_count']}`, valid `{feedback['current']['valid_feedback_count']}`.",
        f"- Prior real-model run: `{feedback['prior']['status']}`, model-evaluated `{feedback['prior']['model_evaluated_count']}`, contract-invalid `{feedback['prior'].get('model_output_invalid_count', 'UNKNOWN')}`.",
        f"- Current full-pop route state: `{counts['route_state_counts']}`.",
        f"- Current packets with source scope not materialized: `{counts['source_scope_not_materialized_count']}/{counts['total_population']}`.",
        f"- Independent semantic audit: guard events `{semantic.get('guard_rejection_event_count')}`, status counts `{semantic.get('status_counts')}`, semantic accuracy `{semantic.get('semantic_accuracy', 'NOT_MEASURED')}`.",
        f"- Independent E2E observer: `{summary['e2e_observer']['status']}`, receipts `{summary['e2e_observer']['question_count']}`, statuses `{summary['e2e_observer'].get('e2e_status_counts')}`, accuracy `{summary['e2e_observer'].get('accuracy', 'NOT_MEASURED')}`.",
        "",
        "## Family-level feedback queue",
        "",
        "| rank | family | lane | affected records | status |",
        "| ---: | --- | --- | ---: | --- |",
    ]
    for row in summary["family_feedback_queue"]:
        lines.append(
            f"| {row['queue_rank']} | `{row['failure_family']}` | `{row['lane']}` | {row['affected_question_count']} | `{row['status']}` |"
        )
    lines.extend(
        [
            "",
            "## Mandatory final A/B report",
            "",
            "- Scorer/gold identity: `NOT_AVAILABLE`; command: `NOT_RUN`; exit status: `NOT_RUN`.",
            "- ANSWER_ACCURACY: `NOT_MEASURED` (no independent gold/scorer in this run).",
            "- EXECUTION_ACCURACY: `NOT_MEASURED` (feedback ledger is not an answer evaluator).",
            "- Improved / regressed / unchanged / changed-answer: `NOT_MEASURED`.",
            f"- Processed records: `{counts['processed_records']}`; missing records: `{counts['missing_records']}`; errors: `{counts['error_count']}`.",
            f"- Feedback-unresolved records: `{feedback['current']['blocked_count']}`; this is transport status, not answer accuracy.",
            "- Family-level wins/losses: `NOT_MEASURED`; diagnostic family counts are reported above and are guard events, not wrong-answer counts.",
            "",
            "## Decision and authority",
            "",
            f"- Decision: `{summary['decision']}` — the system bottleneck and feedback transport retry are specified, but independent correctness evidence is still missing.",
            f"- Authority status: `{summary['authority_status']}`; answer/evidence/training/promotion/release authority are all false.",
            "",
            "## Artifact paths and hashes",
            "",
        ]
    )
    for name, descriptor in summary["artifact_outputs"].items():
        lines.append(f"- `{name}`: `{descriptor['path']}` SHA-256 `{descriptor['sha256']}`")
    if summary.get("feedback_package"):
        package = summary["feedback_package"]
        lines.append(
            f"- `feedback_package_zip`: `{package['path']}` SHA-256 `{package['sha256']}`"
        )
    lines.extend(
        [
            "",
            "## Next bounded action",
            "",
            "1. Run the new immutable compact-prompt feedback package on a real model worker and return its model manifest plus contract-validity counts.",
            "2. Use the family queue to implement one source-bound plan-completeness candidate in a fresh output directory.",
            "3. Run control/candidate over all 1,012 records with an independent scorer; keep or reject only from that A/B report.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_population_feedback_breakthrough(
    *,
    feedback_dir: Path,
    prior_model_dir: Path,
    semantic_summary_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    research_docs: Sequence[Path] = (),
    feedback_package_path: Path | None = None,
    e2e_receipts_path: Path | None = None,
    pipeline_integrity_path: Path | None = None,
) -> dict[str, Any]:
    """Create an immutable full-population feedback ledger and queue."""

    feedback_dir = feedback_dir.resolve()
    prior_model_dir = prior_model_dir.resolve()
    semantic_summary_path = semantic_summary_path.resolve()
    output_dir = output_dir.resolve()
    feedback_package = (
        _descriptor(feedback_package_path.resolve())
        if feedback_package_path is not None
        else None
    )
    e2e_receipts = e2e_receipts_path.resolve() if e2e_receipts_path is not None else None
    pipeline_integrity = (
        pipeline_integrity_path.resolve() if pipeline_integrity_path is not None else None
    )
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if expected_question_count < 1:
        raise ValueError("expected_question_count must be positive")

    packet_path = feedback_dir / "blocked_question_context_packets_v1.jsonl"
    current_feedback_path = feedback_dir / "blocked_question_feedback_v1.jsonl"
    current_summary_path = feedback_dir / "feedback_summary.json"
    current_manifest_path = feedback_dir / "blocked_feedback_run.manifest.json"
    prior_feedback_path = prior_model_dir / "blocked_question_feedback_v1.jsonl"
    prior_summary_path = prior_model_dir / "feedback_summary.json"
    prior_manifest_path = prior_model_dir / "model_run_manifest.json"
    for path in (
        packet_path,
        current_feedback_path,
        current_summary_path,
        prior_feedback_path,
        prior_summary_path,
        semantic_summary_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    packets = _read_jsonl(packet_path)
    current_feedback_rows = _read_jsonl(current_feedback_path)
    prior_feedback_rows = _read_jsonl(prior_feedback_path)
    packets_by_qid = _by_question(
        packets,
        label="context packets",
        expected_question_count=expected_question_count,
    )
    current_by_qid = _by_question(
        current_feedback_rows,
        label="current feedback",
        expected_question_count=expected_question_count,
    )
    prior_by_qid = _by_question(
        prior_feedback_rows,
        label="prior model feedback",
        expected_question_count=expected_question_count,
    )
    packet_ids = {
        _text(row.get("packet_id"))
        for row in packets
        if _text(row.get("packet_id"))
    }
    # The current feedback must be hash-linked to the current packet stream.
    # The historical model run deliberately used a different packet
    # generation, so its packet IDs must not be mistaken for current packet
    # provenance; it is joined by question ID only as telemetry.
    missing_packet_ids = [
        _text(row.get("packet_id"))
        for row in current_feedback_rows
        if _text(row.get("packet_id")) not in packet_ids
    ]
    if missing_packet_ids:
        raise ValueError("current feedback contains packet IDs absent from context packets")

    semantic_summary = _read_json(semantic_summary_path)
    semantic_records = [
        row for row in (semantic_summary.get("records") or []) if isinstance(row, Mapping)
    ]
    semantic_by_qid = _by_question(semantic_records, label="semantic audit")
    semantic_metrics = _semantic_metrics(semantic_summary)
    e2e_metrics = _e2e_metrics(
        e2e_receipts,
        expected_question_count=expected_question_count,
    )
    pipeline_integrity_view: dict[str, Any] = {
        "present": False,
        "status": "NOT_SUPPLIED",
        "error_count": None,
    }
    if pipeline_integrity is not None:
        integrity = _read_json(pipeline_integrity)
        errors = integrity.get("errors")
        pipeline_integrity_view = {
            "present": True,
            "status": _text(integrity.get("status")) or "UNKNOWN",
            "error_count": len(errors) if isinstance(errors, list) else None,
            "gate_passed": integrity.get("gate_passed") is True,
        }

    current_summary = _read_json(current_summary_path)
    prior_summary = _read_json(prior_summary_path)
    current_manifest = _read_json(current_manifest_path) if current_manifest_path.is_file() else None
    prior_manifest = _read_json(prior_manifest_path) if prior_manifest_path.is_file() else None
    current_feedback_view = _feedback_summary_view(current_summary, manifest=current_manifest)
    prior_feedback_view = _feedback_summary_view(prior_summary, manifest=prior_manifest)

    route_state_counts: Counter[str] = Counter()
    route_family_counts: Counter[str] = Counter()
    plan_shape_counts: Counter[str] = Counter()
    source_scope_status_counts: Counter[str] = Counter()
    source_scope_not_materialized_count = 0
    ledger_rows: list[dict[str, Any]] = []
    for question_id in sorted(packets_by_qid):
        packet = packets_by_qid[question_id]
        current = current_by_qid[question_id]
        prior = prior_by_qid[question_id]
        typed = packet.get("typed_question")
        typed = typed if isinstance(typed, Mapping) else {}
        submission_status = packet.get("submission_status")
        submission_status = submission_status if isinstance(submission_status, Mapping) else {}
        source_scope = typed.get("source_scope")
        source_scope = source_scope if isinstance(source_scope, Mapping) else {}
        route_state = _route_state(packet)
        route_family = _route_family(packet)
        plan_shape = _text(typed.get("plan_shape")) or "UNKNOWN"
        scope_status = _text(source_scope.get("source_scope_status")) or "UNKNOWN"
        route_state_counts[route_state] += 1
        route_family_counts[route_family] += 1
        plan_shape_counts[plan_shape] += 1
        source_scope_status_counts[scope_status] += 1
        if scope_status == "NOT_MATERIALIZED_IN_PACKET_INPUT":
            source_scope_not_materialized_count += 1
        blocked_codes = _sequence_strings(packet.get("blocked_reason_codes"))
        semantic_view = _question_semantic_view(semantic_by_qid.get(question_id))
        ledger_rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "question_id": question_id,
                "question_id_role": "tracking_only",
                "population_lane": FULL_POPULATION_LANE,
                "plan_shape": plan_shape,
                "plan_status": _text(typed.get("plan_status")) or "UNKNOWN",
                "research_route_status": _text(typed.get("research_route_status")) or "UNKNOWN",
                "route_state_family": route_state,
                "proposal_route_family": route_family,
                "proposal_route": _text((packet.get("proposal") or {}).get("answer_route")) or "UNKNOWN"
                if isinstance(packet.get("proposal"), Mapping)
                else "UNKNOWN",
                "reporting_scope": _text(typed.get("reporting_scope")) or "UNSPECIFIED",
                "source_scope_status": scope_status,
                "submission_verification_class": _text(submission_status.get("verification_class")) or "UNKNOWN",
                "blocked_reason_codes": blocked_codes,
                "blocked_reason_code_count": len(blocked_codes),
                "current_feedback": _feedback_view(current),
                "prior_model_feedback": _feedback_view(prior),
                "semantic_audit": semantic_view,
                "authority": {
                    "answer_permitted": False,
                    "evidence_permitted": False,
                    "training_permitted": False,
                    "promotion_permitted": False,
                    "release_permitted": False,
                },
            }
        )

    route_state_counts_dict = dict(sorted(route_state_counts.items()))
    route_family_counts_dict = dict(sorted(route_family_counts.items()))
    plan_shape_counts_dict = dict(sorted(plan_shape_counts.items()))
    source_scope_status_counts_dict = dict(sorted(source_scope_status_counts.items()))

    input_descriptors: dict[str, dict[str, Any]] = {
        "context_packets": _descriptor(packet_path),
        "feedback_summary": _descriptor(current_summary_path),
        "feedback_manifest": _descriptor(current_manifest_path)
        if current_manifest_path.is_file()
        else {"path": None},
        "current_feedback_records": _descriptor(current_feedback_path),
        "prior_feedback_summary": _descriptor(prior_summary_path),
        "prior_model_manifest": _descriptor(prior_manifest_path)
        if prior_manifest_path.is_file()
        else {"path": None},
        "prior_feedback_records": _descriptor(prior_feedback_path),
        "semantic_summary": _descriptor(semantic_summary_path),
    }
    if feedback_package is not None:
        input_descriptors["feedback_package_zip"] = feedback_package
    if e2e_receipts is not None:
        input_descriptors["e2e_receipts"] = _descriptor(e2e_receipts)
    if pipeline_integrity is not None:
        input_descriptors["pipeline_integrity"] = _descriptor(pipeline_integrity)
    research_descriptors = _research_descriptors(list(research_docs))
    control_material = {
        "protocol": "vifinqa_population_feedback_control_v1",
        "full_population": expected_question_count,
        "inputs": input_descriptors,
        "research_inputs": research_descriptors,
        "control_lane": "q76_prepared_feedback_plus_prior_real_model_run",
    }
    control_fingerprint = _sha256_json(control_material)
    module_path = Path(__file__).resolve()
    candidate_material = {
        "protocol": PROTOCOL,
        "module_sha256": sha256_file(module_path),
        "primary_family": PRIMARY_FAMILY,
        "rule": "complete_whole_question_plan_before_route_priority",
        "question_id_policy": "tracking_only_no_exceptions",
        "full_population": expected_question_count,
        "feedback_prompt_retry": "en_system_vi_context_compact_v2",
        "feedback_package_sha256": (
            feedback_package.get("sha256") if feedback_package is not None else None
        ),
        "authority": "non_authorizing",
    }
    candidate_fingerprint = _sha256_json(candidate_material)

    evidence_descriptors = {
        "context_packets": input_descriptors["context_packets"],
        "feedback_summary": input_descriptors["feedback_summary"],
        "semantic_summary": input_descriptors["semantic_summary"],
    }
    family_queue = _family_queue(
        route_state_counts=route_state_counts_dict,
        route_family_counts=route_family_counts_dict,
        source_scope_not_materialized_count=source_scope_not_materialized_count,
        current_feedback=current_feedback_view,
        prior_feedback=prior_feedback_view,
        semantic_metrics=semantic_metrics,
        e2e_metrics=e2e_metrics,
        population_count=expected_question_count,
        evidence_descriptors=evidence_descriptors,
    )

    summary: dict[str, Any] = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "COMPLETE_DIAGNOSTIC",
        "hypothesis_family": PRIMARY_FAMILY,
        "hypothesis_rule": "complete_whole_question_plan_before_route_priority",
        "control_fingerprint": control_fingerprint,
        "candidate_fingerprint": candidate_fingerprint,
        "population_counts": {
            "total_population": expected_question_count,
            "processed_records": len(ledger_rows),
            "missing_records": expected_question_count - len(ledger_rows),
            "error_count": 0,
            "route_state_counts": route_state_counts_dict,
            "route_family_counts": route_family_counts_dict,
            "plan_shape_counts": plan_shape_counts_dict,
            "source_scope_status_counts": source_scope_status_counts_dict,
            "source_scope_not_materialized_count": source_scope_not_materialized_count,
        },
        "feedback_transport": {
            "current": current_feedback_view,
            "prior": prior_feedback_view,
            "current_record_count": len(current_feedback_rows),
            "prior_record_count": len(prior_feedback_rows),
            "model_feedback_is_accuracy_evidence": False,
        },
        "feedback_package": feedback_package,
        "semantic_audit": semantic_metrics,
        "e2e_observer": {
            **e2e_metrics,
            "accuracy": "NOT_MEASURED",
            "input_role": "independent_contract_and_source_closure_diagnostic",
        },
        "pipeline_integrity": pipeline_integrity_view,
        "research_review": {
            "documents": research_descriptors,
            "findings": _default_research_findings(),
        },
        "split_policy": {
            "full_population_required": True,
            "tuning_slice_may_discover_hypothesis": True,
            "semantic_audit_slice_is_not_full_population": True,
            "question_id_exceptions": False,
        },
        "scorer_gold": {
            "identity": "NOT_AVAILABLE",
            "command": "NOT_RUN",
            "exit_status": "NOT_RUN",
            "answer_accuracy": "NOT_MEASURED",
            "execution_accuracy": "NOT_MEASURED",
        },
        "ab_report": {
            "improved": "NOT_MEASURED",
            "regressed": "NOT_MEASURED",
            "unchanged": "NOT_MEASURED",
            "changed_answer": "NOT_MEASURED",
            "unresolved": expected_question_count,
            "family_wins_losses": "NOT_MEASURED",
        },
        "family_feedback_queue": family_queue,
        "decision": "INVESTIGATE_FURTHER",
        "authority_status": "CANDIDATE_ONLY",
        "authority": {
            "answer_permitted": False,
            "evidence_permitted": False,
            "training_permitted": False,
            "promotion_permitted": False,
            "submission_permitted": False,
            "release_permitted": False,
        },
        "input_descriptors": input_descriptors,
    }
    _assert_value_free(summary)
    _assert_value_free(ledger_rows)

    output_dir.mkdir(parents=True)
    ledger_path = output_dir / "population_feedback_ledger_v1.jsonl"
    queue_path = output_dir / "family_feedback_queue_v1.json"
    experiment_path = output_dir / "breakthrough_experiment_contract_v1.json"
    summary_path = output_dir / "population_feedback_breakthrough_summary.json"
    _write_jsonl(ledger_path, ledger_rows)
    _write_json(queue_path, {"schema_version": 1, "protocol": PROTOCOL, "queue": family_queue})
    _write_json(
        experiment_path,
        {
            "schema_version": 1,
            "protocol": "vifinqa_breakthrough_experiment_contract_v1",
            "hypothesis_family": PRIMARY_FAMILY,
            "control_fingerprint": control_fingerprint,
            "candidate_fingerprint": candidate_fingerprint,
            "population": {
                "total": expected_question_count,
                "split_policy": summary["split_policy"],
            },
            "control": {
                "lane": "existing_submission_candidate_and_feedback_transport",
                "inputs": input_descriptors,
            },
            "candidate": {
                "lane": "shadow_candidate_plan_completeness_and_feedback_retry",
                "changed_rule": candidate_material["rule"],
                "prompt_profile": candidate_material["feedback_prompt_retry"],
                "question_id_exceptions": False,
            },
            "primary_metrics": {
                "answer_accuracy": "REQUIRE_INDEPENDENT_SCORER",
                "execution_accuracy": "REQUIRE_INDEPENDENT_SCORER",
                "regression_tolerance": "report_all_non_target_regressions; no silent tradeoff",
            },
            "current_result": {
                "answer_accuracy": "NOT_MEASURED",
                "execution_accuracy": "NOT_MEASURED",
                "decision": "INVESTIGATE_FURTHER",
            },
            "authority": summary["authority"],
        },
    )
    _write_json(summary_path, summary)

    output_descriptors: dict[str, dict[str, Any]] = {
        "population_feedback_ledger": _descriptor(ledger_path),
        "family_feedback_queue": _descriptor(queue_path),
        "breakthrough_experiment_contract": _descriptor(experiment_path),
    }
    summary["artifact_outputs"] = output_descriptors
    # Rewrite summary once to include the exact non-self-referential output
    # descriptors.  The summary's own descriptor is recorded in the manifest
    # below, avoiding a circular hash.
    _write_json(summary_path, summary)

    handoff_path = output_dir / "population_feedback_breakthrough_handoff_v1.md"
    handoff_path.write_text(_report(summary, output_dir), encoding="utf-8")
    output_descriptors["handoff"] = _descriptor(handoff_path)
    summary["artifact_outputs"] = output_descriptors
    _write_json(summary_path, summary)

    manifest_path = output_dir / "manifest.json"
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": summary["status"],
        "control_fingerprint": control_fingerprint,
        "candidate_fingerprint": candidate_fingerprint,
        "population_count": expected_question_count,
        "authority": summary["authority"],
        "outputs": {
            **{
                name: _descriptor(Path(descriptor["path"]))
                for name, descriptor in output_descriptors.items()
            },
            "summary": _descriptor(summary_path),
        },
    }
    _write_json(manifest_path, manifest)
    return summary
