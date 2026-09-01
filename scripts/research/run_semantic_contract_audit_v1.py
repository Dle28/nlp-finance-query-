#!/usr/bin/env python3
"""Run a memory-bounded semantic-contract audit on an independent Q&A slice.

This audit deliberately hydrates only table UIDs already present in the
independent review packet.  It uses the production builder's semantic cell
ranker, but never loads the complete 146k-table corpus into memory and never
reads research/model answer artifacts.  The result is a candidate audit, not
an answer key or a strict verification certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_ITEMS = (
    ROOT
    / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811"
    / "vifinqa_review_bundle/review_items.jsonl"
)
DEFAULT_INDEPENDENT_REVIEW = (
    ROOT
    / "artifacts/research/independent_qna_review_v1_20260830_r1"
    / "question_reviews.jsonl"
)
DEFAULT_STRUCTURED_TABLES = (
    ROOT
    / "artifacts/research/document_corpus_round2_assets_v1_20260829_r1"
    / "full_table_assets_v1.jsonl"
)

DEFAULT_BUILDER = ROOT / "scripts/e2e/build_competition_submission_v1.py"

PROTOCOL = "vifinqa_semantic_contract_audit_v1"

# These are data-bearing fields, not feedback metadata.  The audit may inspect
# source cells in memory because the builder needs them to select a cell, but
# it must never emit or compare these fields.
FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "candidate_answer",
        "candidate_answer_decimal",
        "gold_answer",
        "gold_value",
        "model_answer",
        "research_answer",
        "predicted_answer",
        "predicted_value",
        "answer_value",
        "evidence_value",
        "evidence_raw_value",
        "raw_value",
        "raw_cell_value",
        "selected_cell",
        "value",
    }
)

# A question_id is a tracking key only.  These fields would turn it into a
# per-question policy/answer exception and are rejected before the audit.
QUESTION_ID_EXCEPTION_KEYS = frozenset(
    {
        "question_id_exception",
        "question_id_exceptions",
        "qid_exception",
        "qid_exceptions",
        "question_overrides",
        "question_id_overrides",
        "qid_overrides",
        "per_question_rules",
        "per_question_overrides",
        "special_question_cases",
        "question_specific_rules",
        "manual_question_fix",
        "manual_question_fixes",
    }
)

REJECTION_CLASS_GUARD = "GUARD_REJECTION"
REJECTION_CLASS_EVIDENCE = "MISSING_EVIDENCE"
REJECTION_CLASS_UPSTREAM = "UPSTREAM_FILTER"
REJECTION_CLASS_AUDIT = "AUDIT_DIAGNOSTIC"

EVIDENCE_REASON_CODES = frozenset(
    {
        "NO_EVIDENCE_WINDOW",
        "EVIDENCE_NOT_HYDRATED",
        "MISSING_EVIDENCE",
        "SOURCE_NOT_HYDRATED",
        "STRUCTURED_TABLE_MISSING",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_builder_snapshot(path: Path | None) -> Path:
    if path is None:
        raise ValueError(
            "an explicit --builder-path is required; run the audit from a "
            "hashed builder snapshot, not the mutable workspace"
        )
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"builder snapshot does not exist: {resolved}")
    return resolved


def _builder_snapshot_metadata(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "sha256": _sha256(path),
        "explicitly_supplied": True,
        "content_hash_recorded": True,
        # A hash pins the input used by this run; it does not prove that the
        # directory is immutable.  Keep that distinction explicit.
        "filesystem_immutability_proven": False,
    }


def _builder_module(builder_path: Path):
    path = builder_path.resolve()
    spec = importlib.util.spec_from_file_location("competition_submission_builder_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record at {path}:{line_number} is not an object")
            records.append(record)
    return records


def _question_id_exception_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).strip().casefold()
            child_path = f"{path}.{key_text}"
            if key_text in QUESTION_ID_EXCEPTION_KEYS:
                found.append(child_path)
            found.extend(_question_id_exception_paths(nested, child_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_question_id_exception_paths(nested, f"{path}[{index}]"))
    return found


def _assert_no_question_id_exceptions(records: Iterable[Mapping[str, Any]], label: str) -> None:
    paths: list[str] = []
    for index, record in enumerate(records):
        paths.extend(_question_id_exception_paths(record, f"{label}[{index}]"))
    if paths:
        raise ValueError(
            "Question-ID-specific exceptions are forbidden in this audit: "
            + ", ".join(paths[:8])
        )


def _assert_value_free_output(payload: Any) -> None:
    """Fail closed if a future edit emits answer-bearing audit metadata."""

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                key_text = str(key)
                if key_text in FORBIDDEN_OUTPUT_KEYS:
                    raise AssertionError(
                        f"value-bearing output key is forbidden at {path}: {key_text}"
                    )
                walk(nested, f"{path}.{key_text}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                walk(nested, f"{path}[{index}]")

    walk(payload)


def _normalized_reason_codes(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    return sorted({str(code).strip().upper() for code in values if str(code).strip()})


def _family_from_item(item: Mapping[str, Any]) -> tuple[str, str]:
    plan = item.get("question_plan")
    if isinstance(plan, Mapping):
        family = str(plan.get("family") or "").strip()
        if family:
            return family, "question_plan"
    family = str(item.get("family") or "").strip()
    if family:
        return family, "review_item"
    return "UNSPECIFIED", "unavailable"


def _audit_item_without_sidecars(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source/navigation inputs while dropping model/research hints.

    ``rank_semantic_cells`` needs the source evidence window to inspect the
    table.  It does not need candidate-validity scores, research coordinates,
    or model/research answer fields.  Removing those fields here prevents the
    feedback harness from silently becoming another answer-selection lane.
    """

    allowed_item_fields = ("id", "question", "effective_metric")
    sanitized: dict[str, Any] = {
        key: item[key] for key in allowed_item_fields if key in item
    }
    plan = item.get("question_plan")
    if isinstance(plan, Mapping):
        allowed_plan_fields = (
            "family",
            "years",
            "scope",
            "requested_unit",
            "operands",
            "operation_ast",
            "filters",
            "warnings",
        )
        sanitized["question_plan"] = {
            key: plan[key] for key in allowed_plan_fields if key in plan
        }
    candidates: list[dict[str, Any]] = []
    allowed_candidate_fields = (
        "rank",
        "internal_table_uid",
        "document_id",
        "ticker",
        "report_year",
        "scope",
        "local_ordinal",
        "page_no",
        "lexical_rank",
        "dense_rank",
        "fused_score",
        "original_retrieval_rank",
        "review_score",
        "ticker_match",
        "year_match",
        "scope_match",
        "candidate_status",
        "evidence_window",
    )
    for candidate in item.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        candidates.append(
            {
                key: candidate[key]
                for key in allowed_candidate_fields
                if key in candidate
            }
        )
    sanitized["candidates"] = candidates
    return sanitized


def _rejection_class(event: Mapping[str, Any], hydrated_uids: set[str]) -> str:
    stage = str(event.get("stage") or "").strip().casefold()
    uid = str(event.get("internal_table_uid") or "").strip()
    reason_codes = set(_normalized_reason_codes(event.get("reason_codes")))
    if stage in {"semantic_contract", "semantic_guard", "guard"}:
        return REJECTION_CLASS_GUARD
    if (
        stage in {"evidence_window", "evidence", "source_evidence"}
        or reason_codes & EVIDENCE_REASON_CODES
        or not uid
        or uid not in hydrated_uids
    ):
        return REJECTION_CLASS_EVIDENCE
    if stage == "candidate_filter":
        return REJECTION_CLASS_UPSTREAM
    return REJECTION_CLASS_AUDIT


def _project_rejection_event(
    event: Mapping[str, Any], *, hydrated_uids: set[str]
) -> dict[str, Any]:
    rejection_class = _rejection_class(event, hydrated_uids)
    reason_codes = _normalized_reason_codes(event.get("reason_codes"))
    uid = str(event.get("internal_table_uid") or "").strip()
    if rejection_class == REJECTION_CLASS_EVIDENCE and uid and uid not in hydrated_uids:
        reason_codes = sorted(set(reason_codes) | {"EVIDENCE_NOT_HYDRATED"})
    if not reason_codes:
        fallback = {
            REJECTION_CLASS_GUARD: "GUARD_REJECTION_UNCODED",
            REJECTION_CLASS_EVIDENCE: "MISSING_EVIDENCE_UNCODED",
            REJECTION_CLASS_UPSTREAM: "UPSTREAM_FILTER_UNCODED",
            REJECTION_CLASS_AUDIT: "AUDIT_REJECTION_UNCODED",
        }[rejection_class]
        reason_codes = [fallback]
    projected: dict[str, Any] = {
        "stage": str(event.get("stage") or "unknown"),
        "rejection_class": rejection_class,
        "internal_table_uid": uid or None,
        "document_id": event.get("document_id"),
        "candidate_rank": event.get("candidate_rank"),
        "row_index": event.get("row_index"),
        "column_index": event.get("column_index"),
        "reason_codes": reason_codes,
    }
    return projected


def _empty_family_bucket() -> dict[str, Any]:
    return {
        "question_count": 0,
        "status_counts": Counter(),
        "rejection_class_counts": Counter(),
        "reason_code_counts": Counter(),
        "reason_code_class_counts": defaultdict(Counter),
        "abstain_count": 0,
        "abstain_reason_code_counts": Counter(),
        "abstain_without_explicit_reason_code_count": 0,
    }


def _counter_dict(counter: Mapping[str, int]) -> dict[str, int]:
    return {str(key): int(counter[key]) for key in sorted(counter)}


def _nested_counter_dict(value: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, int]]:
    return {
        str(key): _counter_dict(value[key])
        for key in sorted(value)
    }


def _finalize_family_bucket(bucket: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question_count": int(bucket["question_count"]),
        "status_counts": _counter_dict(bucket["status_counts"]),
        "rejection_class_counts": _counter_dict(bucket["rejection_class_counts"]),
        "reason_code_counts": _counter_dict(bucket["reason_code_counts"]),
        "reason_code_class_counts": _nested_counter_dict(
            bucket["reason_code_class_counts"]
        ),
        "abstain_count": int(bucket["abstain_count"]),
        "abstain_reason_code_counts": _counter_dict(bucket["abstain_reason_code_counts"]),
        "abstain_without_explicit_reason_code_count": int(
            bucket["abstain_without_explicit_reason_code_count"]
        ),
    }


def _source_locator(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    source = row.get("source") or {}
    return (
        source.get("internal_table_uid"),
        source.get("row_index"),
        source.get("column_index"),
    )


def _load_candidate_tables(
    path: Path,
    required_uids: set[str],
    builder: Any,
) -> tuple[dict[str, dict[str, Any]], int]:
    tables: dict[str, dict[str, Any]] = {}
    total_lines = 0
    with path.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            total_lines += 1
            raw = json.loads(line)
            uid = str(raw.get("internal_table_uid") or "")
            if uid and uid in required_uids:
                tables[uid] = builder.normalize_structured_table(raw)
    return tables, total_lines


def audit(args: argparse.Namespace) -> dict[str, Any]:
    builder_path = _require_builder_snapshot(getattr(args, "builder_path", None))
    builder_snapshot = _builder_snapshot_metadata(builder_path)
    builder = _builder_module(builder_path)
    review_rows = _read_jsonl(args.independent_review)
    review_items_rows = _read_jsonl(args.review_items)
    _assert_no_question_id_exceptions(review_rows, "independent_review")
    _assert_no_question_id_exceptions(review_items_rows, "review_items")
    independent = {
        int(row["question_id"]): row
        for row in review_rows
        if not row.get("registry_excluded")
    }
    review_items_raw = {int(row["id"]): row for row in review_items_rows}
    selected_ids = sorted(set(independent) & set(review_items_raw))
    required_uids = {
        str(candidate.get("internal_table_uid") or "")
        for question_id in selected_ids
        for candidate in review_items_raw[question_id].get("candidates") or []
        if isinstance(candidate, Mapping) and candidate.get("internal_table_uid")
    }
    tables, corpus_line_count = _load_candidate_tables(
        args.structured_tables,
        required_uids,
        builder,
    )
    hydrated_uids = set(tables)
    review_items, registry_stats = builder.enrich_review_items_with_registry_identity(
        {question_id: review_items_raw[question_id] for question_id in selected_ids}
    )

    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    rejection_counts: Counter[str] = Counter()
    rejection_class_counts: Counter[str] = Counter()
    reason_class_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    family_buckets: defaultdict[str, dict[str, Any]] = defaultdict(_empty_family_bucket)
    for question_id in selected_ids:
        baseline = independent[question_id]
        original_item = review_items[question_id]
        family, family_source = _family_from_item(original_item)
        item = _audit_item_without_sidecars(original_item)
        rejection_log: list[dict[str, Any]] = []
        ranked = builder.rank_semantic_cells(
            item,
            tables_by_uid=tables,
            allow_uncertain=True,
            rejection_log=rejection_log,
        )
        top = ranked[0] if ranked else None
        baseline_locator = _source_locator(baseline)
        if top is None:
            status = "SEMANTIC_ABSTAIN"
            new_locator = (None, None, None)
        else:
            new_locator = (
                top.get("internal_table_uid"),
                top.get("row_index"),
                top.get("column_index"),
            )
            status = (
                "SEMANTIC_CANDIDATE_CHANGED"
                if new_locator != baseline_locator
                else "SEMANTIC_CANDIDATE_RETAINED"
            )
        projected_rejections = [
            _project_rejection_event(event, hydrated_uids=hydrated_uids)
            for event in rejection_log
            if isinstance(event, Mapping)
        ]
        # If the selector returns no cell without emitting a rejection event,
        # make the absence visible without inventing an answer-level reason.
        if not ranked and not projected_rejections:
            candidate_uids = {
                str(candidate.get("internal_table_uid") or "").strip()
                for candidate in item.get("candidates") or []
                if isinstance(candidate, Mapping)
            }
            if candidate_uids and candidate_uids.isdisjoint(hydrated_uids):
                projected_rejections.append(
                    {
                        "stage": "audit_evidence_check",
                        "rejection_class": REJECTION_CLASS_EVIDENCE,
                        "internal_table_uid": None,
                        "document_id": None,
                        "candidate_rank": None,
                        "row_index": None,
                        "column_index": None,
                        "reason_codes": ["EVIDENCE_NOT_HYDRATED"],
                    }
                )
            else:
                projected_rejections.append(
                    {
                        "stage": "audit_diagnostic",
                        "rejection_class": REJECTION_CLASS_AUDIT,
                        "internal_table_uid": None,
                        "document_id": None,
                        "candidate_rank": None,
                        "row_index": None,
                        "column_index": None,
                        "reason_codes": ["AUDIT_NO_SURVIVING_SEMANTIC_CANDIDATE"],
                    }
                )
        question_reasons = sorted(
            {
                code
                for rejection in projected_rejections
                for code in rejection["reason_codes"]
            }
        )
        question_class_counts: Counter[str] = Counter(
            rejection["rejection_class"] for rejection in projected_rejections
        )
        question_reason_class_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for rejection in projected_rejections:
            rejection_class = rejection["rejection_class"]
            rejection_class_counts[rejection_class] += 1
            for reason in rejection["reason_codes"]:
                rejection_counts[reason] += 1
                reason_class_counts[reason][rejection_class] += 1
                question_reason_class_counts[reason][rejection_class] += 1
        if top is not None:
            counts["semantic_candidate_survived_count"] += 1
        else:
            counts["semantic_abstain_count"] += 1
        counts[status] += 1
        for rejection_class, class_count in question_class_counts.items():
            counts[f"{rejection_class.lower()}_event_count"] += class_count
        abstain_reason_codes = question_reasons if status == "SEMANTIC_ABSTAIN" else []
        family_bucket = family_buckets[family]
        family_bucket["question_count"] += 1
        family_bucket["status_counts"][status] += 1
        family_bucket["rejection_class_counts"].update(question_class_counts)
        family_bucket["reason_code_counts"].update(question_reasons)
        for reason, classes in question_reason_class_counts.items():
            for rejection_class, class_count in classes.items():
                family_bucket["reason_code_class_counts"][reason][rejection_class] += class_count
        if status == "SEMANTIC_ABSTAIN":
            family_bucket["abstain_count"] += 1
            family_bucket["abstain_reason_code_counts"].update(abstain_reason_codes)
            if not abstain_reason_codes:
                family_bucket["abstain_without_explicit_reason_code_count"] += 1
        record = {
            "question_id": question_id,
            "family": family,
            "family_source": family_source,
            "baseline_review_status": baseline.get("review_status"),
            "baseline_locator": {
                "internal_table_uid": baseline_locator[0],
                "row_index": baseline_locator[1],
                "column_index": baseline_locator[2],
            },
            "semantic_contract_status": status,
            "semantic_candidate_count": len(ranked),
            "semantic_rejection_count": len(projected_rejections),
            "semantic_rejection_class_counts": _counter_dict(question_class_counts),
            "semantic_rejection_reason_counts_by_class": _nested_counter_dict(
                question_reason_class_counts
            ),
            "semantic_rejection_reason_codes": question_reasons,
            "abstain_reason_codes": abstain_reason_codes,
            "semantic_rejections": projected_rejections,
            "new_locator": {
                "internal_table_uid": new_locator[0],
                "row_index": new_locator[1],
                "column_index": new_locator[2],
            },
            "source_first_independent": True,
            "research_or_model_inputs_consumed": False,
            "gold_answer_available_locally": False,
            "conclusion": (
                "Candidate semantic đã vượt qua guard; đây là feedback/navigation "
                "metadata, không phải accuracy hay VERIFIED."
                if top is not None
                else "Không còn ô semantic đủ điều kiện; giữ fail-closed và ghi rõ "
                "abstain_reason_codes."
            ),
        }
        records.append(record)

    abstain_without_reason_ids = [
        row["question_id"]
        for row in records
        if row["semantic_contract_status"] == "SEMANTIC_ABSTAIN"
        and not row["abstain_reason_codes"]
    ]
    if abstain_without_reason_ids:
        raise AssertionError(
            "every SEMANTIC_ABSTAIN must carry abstain_reason_codes: "
            + ", ".join(str(question_id) for question_id in abstain_without_reason_ids)
        )

    family_level = {
        family: _finalize_family_bucket(bucket)
        for family, bucket in sorted(family_buckets.items())
    }
    summary = {
        "protocol": PROTOCOL,
        "question_count": len(records),
        "registry_excluded_questions_not_audited": sum(
            1 for row in review_rows if row.get("registry_excluded")
        ),
        "candidate_table_uid_count": len(required_uids),
        "hydrated_candidate_table_uid_count": len(tables),
        "structured_corpus_line_count_scanned": corpus_line_count,
        "counts": {
            **dict(sorted(counts.items())),
            "status_counts": dict(sorted(Counter(
                row["semantic_contract_status"] for row in records
            ).items())),
            "rejection_class_counts": _counter_dict(rejection_class_counts),
            "rejection_reason_counts": _counter_dict(rejection_counts),
            "reason_code_class_counts": _nested_counter_dict(reason_class_counts),
            "abstain_count": sum(
                row["semantic_contract_status"] == "SEMANTIC_ABSTAIN"
                for row in records
            ),
            "abstain_without_explicit_reason_code_count": len(abstain_without_reason_ids),
        },
        "family_level": family_level,
        "family_status_counts": {
            family: bucket["status_counts"] for family, bucket in family_level.items()
        },
        "registry_identity": registry_stats,
        "builder_snapshot": builder_snapshot,
        "input_paths": {
            "builder_path": str(builder_path),
            "review_items": str(args.review_items),
            "independent_review": str(args.independent_review),
            "structured_tables": str(args.structured_tables),
        },
        "policy": {
            "family_level_contracts_only": True,
            "question_id_exceptions": False,
            "question_id_used_for_tracking_only": True,
            "research_or_model_values_used": False,
            "research_or_model_sidecars_passed_to_selector": False,
            "gold_answer_available_locally": False,
            "candidate_status_is_not_verified": True,
            "accuracy_measured": False,
            "value_free_output": True,
            "abstain_reason_codes_required": True,
            "abstain_reason_codes_complete": not abstain_without_reason_ids,
            "strict_verification_count": 0,
        },
        "records": records,
    }
    _assert_value_free_output(summary)
    args.output.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.output / "semantic_contract_reviews.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records),
        encoding="utf-8",
    )
    tsv_path = args.output / "semantic_contract_reviews.tsv"
    lines = [
        "question_id\tfamily\tsemantic_contract_status\tsemantic_candidate_count\tsemantic_rejection_count\tsemantic_rejection_class_counts\tabstain_reason_codes\tsemantic_rejection_reason_codes\tbaseline_table_uid\tbaseline_row_index\tbaseline_column_index\tnew_table_uid\tnew_row_index\tnew_column_index"
    ]
    for row in records:
        baseline_locator = row.get("baseline_locator") or {}
        new_locator = row.get("new_locator") or {}
        lines.append(
            "\t".join(
                str(value or "")
                for value in (
                    row["question_id"],
                    row["family"],
                    row["semantic_contract_status"],
                    row["semantic_candidate_count"],
                    row["semantic_rejection_count"],
                    json.dumps(row["semantic_rejection_class_counts"], sort_keys=True),
                    ",".join(row["abstain_reason_codes"]),
                    ",".join(row["semantic_rejection_reason_codes"]),
                    baseline_locator.get("internal_table_uid"),
                    baseline_locator.get("row_index"),
                    baseline_locator.get("column_index"),
                    new_locator.get("internal_table_uid"),
                    new_locator.get("row_index"),
                    new_locator.get("column_index"),
                )
            )
        )
    tsv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    readme = f"""# Semantic contract feedback audit v1

Đây là audit độc lập, memory-bounded cho {len(records)} câu registry-clean từ review packet. Harness chỉ ghi feedback metadata: family, status, locator và reason code. Nó không xuất/so sánh answer, value, gold, model hoặc research result; các số liệu dưới đây không phải accuracy.

- Corpus đã quét: {corpus_line_count:,} dòng; hydrate: {len(tables):,}/{len(required_uids):,} UID ứng viên.
- Builder snapshot: `{builder_path}`; SHA-256 được ghi trong `summary.json`.
- `GUARD_REJECTION`: candidate có evidence nhưng bị semantic contract loại.
- `MISSING_EVIDENCE`: evidence window hoặc structured table chưa hydrate được.
- `UPSTREAM_FILTER`: candidate bị upstream filter loại, không tự gán là semantic guard.
- `AUDIT_DIAGNOSTIC`: diagnostic do harness phát hiện, không phải lỗi answer-selection.
- Mọi `SEMANTIC_ABSTAIN` bắt buộc có `abstain_reason_codes`; nếu không audit sẽ fail.
- `SEMANTIC_CANDIDATE_*` chỉ là feedback/navigation metadata, không phải accuracy hay VERIFIED.

Files:

- `semantic_contract_reviews.jsonl`: value-free metadata từng câu.
- `semantic_contract_reviews.tsv`: bảng đọc nhanh không có answer/value columns.
- `summary.json`: counts theo family/status/class/reason và policy.
    """
    (args.output / "README_VI.md").write_text(readme, encoding="utf-8")
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-items", type=Path, default=DEFAULT_REVIEW_ITEMS)
    parser.add_argument("--independent-review", type=Path, default=DEFAULT_INDEPENDENT_REVIEW)
    parser.add_argument("--structured-tables", type=Path, default=DEFAULT_STRUCTURED_TABLES)
    parser.add_argument(
        "--builder-path",
        type=Path,
        required=True,
        help="Explicit builder snapshot used for reproducible audit lineage",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args)


if __name__ == "__main__":
    main()
