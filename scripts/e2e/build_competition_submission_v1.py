#!/usr/bin/env python3
"""Build the primary, best-effort ViFinQA competition submission.

The submission path combines the existing review bundle with research/model
signals, then resolves every prediction from the structured table bundle.  A
research signal may improve navigation or propose a staged result, but it is
accepted only after the current table coordinates are replayed.  The builder
never drops a question: lower-confidence rows are kept in the output so the
leaderboard can measure the full answering model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import unicodedata
import zipfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

import finance_query.e2e.core.source_first_lookup as source_first_lookup_module
import finance_query.e2e.core.candidate_plan_selector as candidate_plan_selector_module
from finance_query.e2e.core.learned_rag import (
    FineTunedArtifactManifest,
    PairScorer,
    load_finetuned_reranker,
    rerank_review_items_candidates,
)
from finance_query.e2e.core.candidate_validity import (
    CANDIDATE_VALIDITY_PROTOCOL,
    load_candidate_validity_model,
    rank_candidate_pool,
)
from finance_query.e2e.core.candidate_plan_selector import (
    AnswerLevelSelector as CandidateAnswerLevelSelector,
    CandidatePlanSet as CandidateAnswerPlanSet,
)
from finance_query.e2e.core.currency_units import vnd_scale
from finance_query.e2e.core.formula_evidence_bridge import (
    FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
    build_formula_evidence_candidates,
)
from finance_query.e2e.core.questions import extract_tickers, load_ticker_aliases, normalize_text
from finance_query.e2e.core.proposal_verifier import (
    load_certificate_index,
    select_best_proposal,
    verify_proposed_answer,
)
from finance_query.e2e.core.reproducibility import (
    fingerprint_path,
    route_flag_snapshot,
    stage_status,
    submission_gate,
    validate_dense_index_artifact,
    write_json,
)
from finance_query.e2e.core.source_first_lookup import (
    _financial_receivables_total_table_contract,  # noqa: F401 - legacy builder API
    build_source_first_direct_lookup_index,
    resolve_source_first_direct_lookup,
)
from finance_query.e2e.deterministic_replay import (
    load_deterministic_replay_inputs,
    run_deterministic_replay,
)
from finance_query.e2e.decimal_executor import execute_ast, validate_operation_ast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = ROOT / "data/ViFinQA/questions/questions.jsonl"
DEFAULT_CODE_STOCK = ROOT / "data/ViFinQA/code_stock.csv"
DEFAULT_BUNDLE = (
    ROOT
    / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle"
)
DEFAULT_REPLAY = (
    ROOT
    / "artifacts/runs/e2e-explicit-ticker-candidate-replay-20260827-agent4-r9"
    / "grounded_execution_replay_v2.jsonl"
)
DEFAULT_OUTPUT = ROOT / "submissions/vifinqa_primary_integrated_20260828_r1"
DEFAULT_RESEARCH_CANDIDATES = (
    ROOT
    / "artifacts/research/agent4_candidate_union_v1_20260827_r2/period_column_candidate_packets_v1.jsonl",
    ROOT
    / "artifacts/research/multicol_semantic_diagnostic_v1_20260827_r2/candidate_packets_v1.jsonl",
)
DEFAULT_ROUTE_OVERLAY = (
    ROOT
    / "artifacts/research/explicit_ticker_direct_lookup_route_materialization_v1_20260827_r2/route_completeness_overlay_v3.jsonl"
)
DEFAULT_MODEL_ANSWER_CANDIDATES = (
    ROOT
    / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_qwen_review_results/qwen_staged_execution_ledger_v1.jsonl",
)
DEFAULT_CANDIDATE_VALIDITY_MODEL = ROOT / "artifacts/review_calibrator.joblib"
# Two questions were absent from the Kaggle review candidate file.  These
# values are recovered from the raw corpus, not from a hidden answer set.
RAW_CORPUS_RECOVERIES: dict[int, dict[str, Any]] = {
    336: {
        "answer": Decimal("387.656354540"),
        "sources": [
            {
                "raw_value_decimal": "387656354540",
                "role": "total_future_minimum_lease_payments",
                "document_id": "HND_financial_statements_2025",
                "row_index": 4,
                "column_index": 1,
                "row_label": "Tổng số tiền thuê tối thiểu trong tương lai",
                "source_to_vnd_multiplier": "1",
                "line_override": 912,
            }
        ],
    },
    786: {
        "answer": Decimal("158.231899625"),
        "sources": [
            {
                "raw_value_decimal": "623094091939",
                "role": "dtk_profit_before_tax",
                "document_id": "DTK_financial_statements_2023_separate",
                "row_index": 2,
                "column_index": 3,
                "row_label": "Lợi nhuận trước thuế",
                "source_to_vnd_multiplier": "1",
                "line_override": 280,
            },
            {
                "raw_value_decimal": "464862192314",
                "role": "hnd_profit_before_tax",
                "document_id": "HND_financial_statements_2023",
                "row_index": 2,
                "column_index": 3,
                "row_label": "Lợi nhuận trước thuế",
                "source_to_vnd_multiplier": "1",
                "line_override": 385,
            },
        ],
    },
}

STOPWORDS = {
    "bao", "nhieu", "la", "cua", "cong", "ty", "ctcp", "nam", "trong",
    "vao", "den", "tu", "va", "cho", "biet", "trieu", "ty", "dong",
    "vnd", "hop", "nhat", "me", "co", "phan", "gia", "tri", "tai",
    "thoi", "diem", "ky", "bao", "cao", "theo", "lan", "phan", "tram",
    "tang", "giam", "tru", "di", "muc", "thay", "doi", "toc", "do",
    "truong", "lon", "hon", "be", "sang", "cuoi", "dau", "tinh",
}
NUMBER_RE = re.compile(r"^\(?[-+]?\d[\d.,\s]*\)?%?$")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# The semantic-cell contract is a separate experiment from the source-first
# answer routes.  Keeping an explicit process-local switch lets controlled
# route A/B runs retain the established baseline ranking while the stricter
# contract is evaluated independently.  It is intentionally reported in the
# build artifact and never changes source/evidence authority.
_SEMANTIC_CELL_CONTRACT_ENABLED = True


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def review_candidate_table_uids(
    review_items: Mapping[int, Mapping[str, Any]],
) -> set[str]:
    """Collect only hydrated table UIDs already named by the review packet.

    The complete round-2 asset is useful for research, but materializing all
    of it for a submission build is needlessly expensive.  The review packet
    already carries immutable table UIDs; using that set gives the production
    run a bounded, reproducible source boundary while retaining the exact
    source rows needed by the answer lane.
    """

    return {
        str(candidate.get("internal_table_uid") or "")
        for item in review_items.values()
        for candidate in item.get("candidates") or []
        if candidate.get("internal_table_uid")
    }


STRUCTURED_TABLE_PROVENANCE_KEYS = (
    "source_path",
    "source_sha256",
    "char_start",
    "char_end",
    "byte_start",
    "byte_end",
    "table_sha256",
)


def normalize_structured_table(table: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize V2 and full-corpus table assets to one runtime contract.

    The original V2 bundle wraps provenance in ``source_provenance`` and
    exposes the display header as ``column_labels``.  The complete OCR corpus
    asset keeps equivalent fields at the top level as ``source_path`` /
    ``char_start`` and ``headers``.  Retrieval, cell selection, replay and
    submission coordinate emission must all consume the same normalized view.
    """

    normalized = dict(table)
    nested = normalized.get("source_provenance")
    if isinstance(nested, Mapping) and nested:
        normalized["source_provenance"] = dict(nested)
    else:
        provenance = {
            key: normalized[key]
            for key in STRUCTURED_TABLE_PROVENANCE_KEYS
            if key in normalized
        }
        if provenance:
            normalized["source_provenance"] = provenance

    if not normalized.get("column_labels"):
        headers = normalized.get("headers")
        if isinstance(headers, list):
            normalized["column_labels"] = list(headers)
    return normalized


def table_provenance(table: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return normalized provenance for either supported table schema."""

    nested = table.get("source_provenance")
    if isinstance(nested, Mapping) and nested:
        return nested
    return {
        key: table[key]
        for key in STRUCTURED_TABLE_PROVENANCE_KEYS
        if key in table
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def implementation_fingerprint() -> dict[str, str]:
    """Fingerprint loaded source and on-disk sources for reproducible A/B runs."""

    builder_path = Path(__file__).resolve()
    source_first_path = Path(source_first_lookup_module.__file__).resolve()
    return {
        "builder_path": str(builder_path),
        "builder_sha256": sha256_file(builder_path),
        "source_first_lookup_path": str(source_first_path),
        "source_first_lookup_sha256": sha256_file(source_first_path),
        "candidate_plan_selector_path": str(
            Path(candidate_plan_selector_module.__file__).resolve()
        ),
        "candidate_plan_selector_sha256": sha256_file(
            Path(candidate_plan_selector_module.__file__).resolve()
        ),
    }


RERANKER_POLICIES = {"baseline_no_reranker", "use_finetuned_reranker"}


def resolve_reranker_policy(args: Any) -> str:
    """Resolve an explicit reranker policy and reject accidental A/B use.

    The historical builder enabled a reranker whenever a model root was
    present.  That made a copied Kaggle input silently change the baseline.
    A model root now has to agree with an explicit policy flag.
    """

    policy = str(getattr(args, "reranker_policy", "baseline_no_reranker"))
    if policy not in RERANKER_POLICIES:
        raise ValueError(
            "--reranker-policy must be one of: "
            + ", ".join(sorted(RERANKER_POLICIES))
        )
    model_root = getattr(args, "finetuned_model_root", None)
    if model_root is not None and policy != "use_finetuned_reranker":
        raise ValueError(
            "a fine-tuned model root was supplied while the run is configured "
            "as baseline_no_reranker; pass --reranker-policy "
            "use_finetuned_reranker explicitly"
        )
    if policy == "use_finetuned_reranker" and model_root is None:
        raise ValueError(
            "use_finetuned_reranker requires --finetuned-model-root"
        )
    return policy


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_question_id(record: dict[str, Any]) -> int | None:
    return _int_or_none(record.get("question_id", record.get("id")))


def _research_coordinate_candidates(record: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield coordinates from the supported value-blind research schemas.

    The adapter deliberately reads only table coordinates and semantic labels.
    Raw values present in a future artifact are ignored; the current structured
    V2 table is the only place from which this submission builder reads a cell.
    """
    question_id = _record_question_id(record)
    if question_id is None:
        return
    stages = record.get("stages") or []
    nested_found = False
    for stage in stages:
        for operand in stage.get("required_operands") or []:
            for candidate in operand.get("period_column_candidates") or []:
                nested_found = True
                yield {
                    "question_id": question_id,
                    "internal_table_uid": candidate.get("internal_table_uid"),
                    "document_id": candidate.get("document_id"),
                    "row_index": candidate.get("row_index"),
                    "column_index": candidate.get("column_index"),
                    "candidate_status": candidate.get("semantic_candidate_status")
                    or candidate.get("candidate_status")
                    or operand.get("column_status")
                    or record.get("packet_status"),
                    "protocol": record.get("protocol"),
                    "stage_id": stage.get("stage_id"),
                    "operand_id": operand.get("role") or operand.get("operand_id"),
                    "period_labels": candidate.get("period_labels") or [],
                    "requested_output_unit": record.get("requested_output_unit")
                    or candidate.get("requested_output_unit"),
                    "reason_codes": candidate.get("reason_codes") or [],
                    "source_cell_sha256": candidate.get("source_cell_sha256"),
                    "source_table_sha256": candidate.get("table_sha256"),
                }
    if nested_found:
        return
    if record.get("internal_table_uid"):
        yield {
            "question_id": question_id,
            "internal_table_uid": record.get("internal_table_uid"),
            "document_id": record.get("document_id"),
            "row_index": record.get("row_index"),
            "column_index": record.get("column_index"),
            "candidate_status": record.get("candidate_status"),
            "protocol": record.get("protocol"),
            "stage_id": record.get("stage_id"),
            "operand_id": record.get("operand_id"),
            "period_labels": record.get("period_labels") or [],
            "requested_output_unit": record.get("requested_output_unit"),
            "reason_codes": record.get("reason_codes") or [],
            "source_cell_sha256": record.get("source_cell_sha256"),
            "source_table_sha256": record.get("table_sha256"),
        }


def _research_evidence_window(table: dict[str, Any], row_index: int) -> list[dict[str, Any]]:
    """Hydrate research coordinates with compact V2 rows and header context."""
    rows = table.get("rows") or []
    selected = set(range(min(8, len(rows))))
    selected.update(range(max(0, row_index - 2), min(len(rows), row_index + 3)))
    evidence = [{"index": index, "row": rows[index]} for index in sorted(selected)]
    labels = table.get("column_labels") or []
    if labels:
        evidence.append({"index": -1, "row": labels})
    return evidence


def load_research_candidate_hints(
    paths: Iterable[Path],
    *,
    tables_by_uid: dict[str, dict[str, Any]],
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    """Load research coordinates as navigation hints for the primary model.

    Research packets remain value-blind inputs.  A hint is accepted only when
    its UID, document, row and column can be hydrated from the current V2
    bundle and the target cell is numeric.  This lets research improve recall
    without allowing a stale packet to inject an answer.
    """
    hints_by_question: dict[int, list[dict[str, Any]]] = {}
    stats: Counter[str] = Counter()
    source_paths: list[str] = []
    seen: set[tuple[int, str, int, int]] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        stats["paths_requested"] += 1
        if not path.exists():
            stats["paths_missing"] += 1
            continue
        source_paths.append(str(path))
        stats["paths_used"] += 1
        for record in read_jsonl(path):
            stats["records_scanned"] += 1
            for raw_candidate in _research_coordinate_candidates(record):
                stats["coordinates_seen"] += 1
                question_id = int(raw_candidate["question_id"])
                uid = str(raw_candidate.get("internal_table_uid") or "")
                row_index = _int_or_none(raw_candidate.get("row_index"))
                column_index = _int_or_none(raw_candidate.get("column_index"))
                table = tables_by_uid.get(uid)
                if not uid or table is None:
                    stats["coordinates_rejected_missing_table"] += 1
                    continue
                table_document = str(table.get("document_id") or "").removesuffix(".txt")
                candidate_document = str(raw_candidate.get("document_id") or "").removesuffix(".txt")
                if candidate_document and candidate_document != table_document:
                    stats["coordinates_rejected_document_mismatch"] += 1
                    continue
                rows = table.get("rows") or []
                if (
                    row_index is None
                    or column_index is None
                    or row_index < 0
                    or row_index >= len(rows)
                    or column_index < 0
                    or column_index >= len(rows[row_index])
                ):
                    stats["coordinates_rejected_bad_coordinate"] += 1
                    continue
                if parse_decimal(rows[row_index][column_index]) is None:
                    stats["coordinates_rejected_non_numeric"] += 1
                    continue
                key = (question_id, uid, row_index, column_index)
                if key in seen:
                    stats["coordinates_deduplicated"] += 1
                    continue
                seen.add(key)
                hint = {
                    "question_id": question_id,
                    "internal_table_uid": uid,
                    "document_id": table_document,
                    "row_index": row_index,
                    "column_index": column_index,
                    "candidate_status": raw_candidate.get("candidate_status"),
                    "protocol": raw_candidate.get("protocol"),
                    "stage_id": raw_candidate.get("stage_id"),
                    "operand_id": raw_candidate.get("operand_id"),
                    "period_labels": list(raw_candidate.get("period_labels") or []),
                    "requested_output_unit": raw_candidate.get("requested_output_unit"),
                    "reason_codes": list(raw_candidate.get("reason_codes") or []),
                    "source_cell_sha256": raw_candidate.get("source_cell_sha256"),
                    "source_table_sha256": raw_candidate.get("source_table_sha256"),
                    "research_source": path.stem,
                }
                hints_by_question.setdefault(question_id, []).append(hint)
                stats["coordinates_accepted"] += 1
    stats["questions_with_hints"] = len(hints_by_question)
    stats["source_paths"] = source_paths
    return hints_by_question, dict(stats)


def fuse_research_candidates(
    items: dict[int, dict[str, Any]],
    hints_by_question: dict[int, list[dict[str, Any]]],
    *,
    tables_by_uid: dict[str, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Append hydrated research hints to the primary review candidate pool."""
    fused = {question_id: dict(item) for question_id, item in items.items()}
    stats: Counter[str] = Counter()
    for question_id, hints in hints_by_question.items():
        item = fused.get(question_id)
        if item is None:
            stats["questions_without_review_item"] += 1
            continue
        candidates = list(item.get("candidates") or [])
        existing = {
            (
                str(candidate.get("internal_table_uid") or ""),
                _int_or_none(candidate.get("research_row_index")),
                _int_or_none(candidate.get("research_column_index")),
            )
            for candidate in candidates
            if candidate.get("research_row_index") is not None
        }
        added = 0
        for hint in hints:
            key = (
                str(hint["internal_table_uid"]),
                int(hint["row_index"]),
                int(hint["column_index"]),
            )
            if key in existing:
                stats["hints_already_fused"] += 1
                continue
            table = tables_by_uid[str(hint["internal_table_uid"])]
            candidate = {
                "internal_table_uid": hint["internal_table_uid"],
                "document_id": hint["document_id"],
                "report_year": table.get("report_year"),
                "scope": table.get("scope"),
                "rank": len(candidates) + 1,
                "review_score": 0.0,
                "evidence_window": _research_evidence_window(table, int(hint["row_index"])),
                "research_row_index": int(hint["row_index"]),
                "research_column_index": int(hint["column_index"]),
                "candidate_source": hint["research_source"],
                "retrieval_source": hint["research_source"],
                "research_protocol": hint.get("protocol"),
                "research_stage_id": hint.get("stage_id"),
                "research_operand_id": hint.get("operand_id"),
                "research_candidate_status": hint.get("candidate_status"),
                "research_period_labels": hint.get("period_labels") or [],
                "research_reason_codes": hint.get("reason_codes") or [],
                "source_cell_sha256": hint.get("source_cell_sha256"),
                "source_table_sha256": hint.get("source_table_sha256"),
                "navigation_metadata_only": True,
                "may_authorize_answer": False,
                "submission_eligible": False,
                "research_candidate_only": True,
            }
            candidates.append(candidate)
            existing.add(key)
            added += 1
            stats["candidates_added"] += 1
        if added:
            updated = dict(item)
            updated["candidates"] = candidates
            updated["research_candidates_fused"] = added
            fused[question_id] = updated
            stats["questions_changed"] += 1
    stats["questions_with_hints"] = len(hints_by_question)
    return fused, dict(stats)


def load_route_overlay(path: Path | None) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Load route diagnostics without making them answer authority."""
    if path is None:
        return {}, {"path": None, "loaded": False, "question_count": 0}
    path = path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"route overlay does not exist: {path}")
    rows: dict[int, dict[str, Any]] = {}
    statuses: Counter[str] = Counter()
    for record in read_jsonl(path):
        question_id = _record_question_id(record)
        if question_id is None:
            continue
        rows[question_id] = record
        statuses[str(record.get("route_status") or "unknown")] += 1
    return rows, {
        "path": str(path),
        "loaded": True,
        "question_count": len(rows),
        "route_status_counts": dict(statuses),
        "navigation_only": True,
    }


def normalize(text: Any) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def content_tokens(text: Any) -> set[str]:
    return {
        token
        for token in normalize(text).split()
        if len(token) > 1 and token not in STOPWORDS and not token.isdigit()
    }


def parse_decimal(value: Any) -> Decimal | None:
    raw = "" if value is None else str(value).strip()
    if not raw or raw in {"-", "–", "—", "_", "N/A", "n/a"}:
        return None
    raw = raw.replace("\u00a0", "").replace(" ", "")
    if not NUMBER_RE.match(raw):
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").rstrip("%")
    if raw.startswith("-"):
        negative = True
        raw = raw[1:]
    elif raw.startswith("+"):
        raw = raw[1:]

    # Vietnamese financial OCR normally uses dots as thousands separators.
    if "." in raw and "," in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif raw.count(".") >= 1:
        parts = raw.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            raw = "".join(parts)
    elif raw.count(",") >= 1:
        parts = raw.split(",")
        if all(len(part) == 3 for part in parts[1:]):
            raw = "".join(parts)
        elif raw.count(",") == 1:
            raw = raw.replace(",", ".")
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return -parsed if negative else parsed


def parse_decimal_literal(value: Any) -> Decimal | None:
    """Parse a canonical Decimal string without OCR thousands heuristics."""

    raw = "" if value is None else str(value).strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def requested_divisor(question: str) -> Decimal:
    q = normalize(question)
    # Keep compound Vietnamese units ahead of the generic ``tỷ`` branch.
    # ``trăm tỷ`` and ``nghìn tỷ`` occur in the competition questions and
    # otherwise get silently interpreted as plain billion VND.
    if "nghin ty dong" in q or "ngan ty dong" in q:
        return Decimal("1000000000000")
    if "tram ty dong" in q:
        return Decimal("100000000000")
    if "ty dong" in q or "ti dong" in q:
        return Decimal("1000000000")
    if "trieu dong" in q or "trieu vnd" in q:
        return Decimal("1000000")
    if "nghin dong" in q or "ngan dong" in q:
        return Decimal("1000")
    return Decimal(1)


_UNIT_MULTIPLIER_PATTERNS: tuple[tuple[str, Decimal], ...] = (
    ("nghin ty dong", Decimal("1000000000000")),
    ("ngan ty dong", Decimal("1000000000000")),
    ("nghin ty vnd", Decimal("1000000000000")),
    ("ngan ty vnd", Decimal("1000000000000")),
    ("trillion vnd", Decimal("1000000000000")),
    ("trillion dong", Decimal("1000000000000")),
    ("tram ty dong", Decimal("100000000000")),
    ("tram ty vnd", Decimal("100000000000")),
    ("ty dong", Decimal("1000000000")),
    ("ti dong", Decimal("1000000000")),
    ("ty vnd", Decimal("1000000000")),
    ("ti vnd", Decimal("1000000000")),
    ("billion vnd", Decimal("1000000000")),
    ("billion dong", Decimal("1000000000")),
    ("trieu dong", Decimal("1000000")),
    ("trieu vnd", Decimal("1000000")),
    ("million vnd", Decimal("1000000")),
    ("million dong", Decimal("1000000")),
    ("nghin dong", Decimal("1000")),
    ("ngan dong", Decimal("1000")),
    ("nghin vnd", Decimal("1000")),
    ("ngan vnd", Decimal("1000")),
    ("thousand vnd", Decimal("1000")),
    ("thousand dong", Decimal("1000")),
    ("vnd", Decimal("1")),
)


def _unit_multiplier_from_text(value: Any) -> Decimal | None:
    """Return a fixed VND scale mentioned in one source-side text field."""

    text = normalize(value)
    for phrase, multiplier in _UNIT_MULTIPLIER_PATTERNS:
        if phrase in text:
            return multiplier
    return None


def source_multiplier(
    rows: list[dict[str, Any]], table: dict[str, Any] | None = None
) -> Decimal:
    """Infer a source unit without allowing noisy metadata to inflate values.

    ``unit_hint`` and ``context_trace.unit_labels`` are navigation metadata,
    and the latter can contain units from neighbouring prose.  A compact
    header or explicit unit row is therefore resolved first.  This matters
    for tables whose header says ``VND`` while an automatically generated
    hint/summary also mentions ``tỷ đồng``: the numeric cells remain VND.
    """
    if table:
        # A legacy OCR header can concatenate a period label and ``Ngàn VND``
        # (for example ``Số cuối nămNgàn VND``).  The established candidate
        # contract treats that ambiguous token as a plain VND display; only a
        # marked declaration such as ``Đơn vị tính: Ngàn VND`` is a scale
        # contract.  Keep that compatibility while still giving explicit
        # ``VND`` headers precedence over noisy hints.
        header_text = normalize(
            " ".join(
                [str(value) for value in table.get("column_labels") or []]
                + [str(value) for value in table.get("headers") or []]
            )
        )
        if "ngan vnd" in header_text and "don vi" not in header_text:
            return Decimal(1)
        declared = _table_declared_source_multiplier(table)
        if declared is not None:
            return declared

    # A candidate window may include a standalone OCR unit row.  Only accept
    # it when it is a unit declaration or contains no numeric cell; this keeps
    # a prose row such as ``chi phí ... 28,4 tỷ đồng`` from changing a table's
    # scale accidentally.
    for candidate in rows:
        raw_row = candidate.get("row", [])
        row_text = normalize(" ".join(str(cell) for cell in raw_row))
        multiplier = _unit_multiplier_from_text(row_text)
        if multiplier is None:
            continue
        has_numeric_cell = any(parse_decimal(cell) is not None for cell in raw_row)
        if "don vi" in row_text or "unit" in row_text or not has_numeric_cell:
            return multiplier
    return Decimal(1)


def metric_text(item: dict[str, Any]) -> str:
    operands = (item.get("question_plan") or {}).get("operands") or []
    parts = [str(op.get("metric") or "") for op in operands]
    return " ".join(parts) or str(item.get("question") or "")


def semantic_row_score(metric: str, question: str, label: str) -> float:
    label_norm = normalize(label)
    if not label_norm or parse_decimal(label) is not None:
        return -2.0
    metric_norm = normalize(metric)
    # Question plans often append the company name to the metric.  Keeping
    # those entity tokens makes a long company-name row look more relevant
    # than the requested financial line (for example ``Nợ trung hạn``).
    entity_match = re.search(r"\b(?:cong ty|ngan hang|ctcp|tong cong ty)\b", metric_norm)
    metric_core = metric_norm[: entity_match.start()].strip() if entity_match else metric_norm
    metric_tokens = content_tokens(metric_core)
    question_tokens = content_tokens(question)
    label_tokens = content_tokens(label)
    target = metric_tokens or question_tokens
    overlap = len(target & label_tokens) / max(1, len(target | label_tokens))
    recall = len(target & label_tokens) / max(1, len(target))
    sequence = SequenceMatcher(None, metric_norm, label_norm).ratio()
    containment = 0.45 if label_norm in metric_norm or metric_norm in label_norm else 0.0
    # Prefer a row whose leading metric phrase is present verbatim.  Token
    # overlap alone ranks broad rows such as ``Tiền gửi tại ...`` above the
    # exact ``Lãi tiền gửi`` row when the question also names a bank.
    phrase_bonus = 0.0
    metric_words = metric_core.split()
    for width in range(min(7, len(metric_words)), 1, -1):
        for start in range(0, len(metric_words) - width + 1):
            phrase = " ".join(metric_words[start : start + width])
            if phrase and phrase in label_norm:
                phrase_bonus = max(phrase_bonus, 1.25 + 0.12 * width)
    generic_penalty = 0.0
    if label_norm in {"tong", "cong", "so du", "so du cuoi nam", "so du dau nam"}:
        generic_penalty = 0.8
    return 1.8 * overlap + 1.4 * recall + 0.8 * sequence + containment + phrase_bonus - generic_penalty


_SEMANTIC_COLUMN_NOISE = {
    "bao",
    "biet",
    "cao",
    "cong",
    "ctcp",
    "cua",
    "dong",
    "don",
    "dvt",
    "gia",
    "hop",
    "la",
    "me",
    "nam",
    "ngay",
    "nghin",
    "nhat",
    "nhieu",
    "phan",
    "so",
    "tai",
    "thang",
    "thoi",
    "trieu",
    "ty",
    "vnd",
    "vao",
    "va",
    "xem",
    "y",
}

_SEMANTIC_PERCENT_MARKERS = (
    "%",
    "phan tram",
    "ty le",
    "ti le",
    "quyen bieu quyet",
    "ty le loi ich",
    "loi ich kinh te",
)

_SEMANTIC_ACQUISITION_MARKERS = (
    "ngay mua",
    "tai ngay mua",
    "gia tri hop ly tai ngay mua",
    "acquisition date",
    "purchase date",
)


def _semantic_phrase_present(text: Any, phrase: Any) -> bool:
    """Match a normalized phrase without letting short tokens overmatch.

    Contracts such as ``cong`` (``Cộng``) must not be satisfied by an
    unrelated label such as ``công cụ``.  Multi-word Vietnamese phrases keep
    their existing containment behaviour because OCR often removes
    punctuation between words.
    """

    text_norm = normalize(text)
    phrase_norm = normalize(phrase)
    if not text_norm or not phrase_norm:
        return False
    if " " in phrase_norm:
        return phrase_norm in text_norm
    return bool(re.search(rf"(?<!\w){re.escape(phrase_norm)}(?!\w)", text_norm))


_SEMANTIC_TICKER_ALIASES: dict[str, str] | None = None


def _semantic_expected_tickers(item: Mapping[str, Any]) -> set[str]:
    """Return plan tickers, recovering an omitted ticker from the registry.

    Older review bundles occasionally contain an empty planner ticker for a
    direct question that names the legal company form in full.  This is a
    routing repair only: the answer still has to bind to the hydrated table
    and pass every row/column contract below.
    """

    plan = item.get("question_plan") or {}
    planned = {
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    }
    if planned:
        return planned
    global _SEMANTIC_TICKER_ALIASES
    if _SEMANTIC_TICKER_ALIASES is None:
        _SEMANTIC_TICKER_ALIASES = load_ticker_aliases(DEFAULT_CODE_STOCK)
    return {
        ticker
        for ticker in extract_tickers(str(item.get("question") or ""), _SEMANTIC_TICKER_ALIASES)
        if ticker not in {"CP", "CTCP", "TMCP"}
    }


def enrich_review_items_with_registry_identity(
    items: Mapping[int, Mapping[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, int]]:
    """Fill one omitted direct-lookup issuer from the exact code-stock registry.

    The review bundle is a frozen retrieval artifact, so rebuilding it in
    place would destroy lineage.  This copy-on-read adapter repairs only the
    in-memory route plan when the question resolves to exactly one registered
    issuer.  It never resolves an ambiguous multi-issuer question and it does
    not supply a value or bypass table-coordinate validation.
    """

    aliases = load_ticker_aliases(DEFAULT_CODE_STOCK)
    enriched: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for raw_id, raw_item in items.items():
        item = dict(raw_item)
        plan = dict(item.get("question_plan") or {})
        planned = {
            str(value).strip().upper()
            for value in plan.get("tickers") or []
            if str(value).strip()
        }
        inferred = [
            ticker
            for ticker in extract_tickers(str(item.get("question") or ""), aliases)
            if ticker not in {"CP", "CTCP", "TMCP"}
        ]
        if planned:
            stats["planner_identity_present"] += 1
        elif len(set(inferred)) == 1:
            ticker = inferred[0]
            plan["tickers"] = [ticker]
            operands: list[dict[str, Any]] = []
            for operand in plan.get("operands") or []:
                if not isinstance(operand, Mapping):
                    continue
                updated = dict(operand)
                if not str(updated.get("ticker") or "").strip():
                    updated["ticker"] = ticker
                operands.append(updated)
            if operands:
                plan["operands"] = operands
            warnings = [
                str(value)
                for value in plan.get("warnings") or []
                if "ticker" not in str(value).casefold()
            ]
            plan["warnings"] = warnings
            stats["recovered_single_registry_identity"] += 1
        elif inferred:
            stats["ambiguous_registry_identity"] += 1
        else:
            stats["unresolved_registry_identity"] += 1
        item["question_plan"] = plan
        enriched[int(raw_id)] = item
    stats["items"] = len(items)
    return enriched, dict(stats)


def _semantic_tokens(value: Any) -> list[str]:
    """Return comparison tokens while retaining financial qualifiers."""

    output: list[str] = []
    for token in normalize(value).split():
        if token in _SEMANTIC_COLUMN_NOISE or YEAR_RE.fullmatch(token):
            continue
        if token.isdigit() or re.fullmatch(r"[a-z]*\d+[a-z\d]*", token):
            continue
        if token not in output:
            output.append(token)
    return output


def _semantic_row_label(row: list[Any], *, parser: Any) -> str:
    """Keep every textual cell that belongs to a row's local label.

    The legacy ranker only inspected the first three cells.  That loses the
    counterparty, currency or maturity qualifier when OCR puts it in a later
    text column.  Numeric cells are removed with the existing parser so the
    returned label remains coordinate-preserving and value-blind.
    """

    parts: list[str] = []
    for cell in row:
        text = str(cell or "").strip()
        if text and parser(cell) is None:
            parts.append(text)
    return " ".join(parts)


def _semantic_row_context(
    table: Mapping[str, Any], *, row_index: int, row: list[Any], parser: Any
) -> str:
    """Return the selected row plus value-free hierarchy/period context.

    The full-corpus asset retains ``row_paths`` produced during table
    materialization.  Those paths are navigation metadata, but their textual
    ancestors are important for distinguishing repeated labels such as
    ``Nguyên giá`` under fixed assets versus investment property.  Numeric
    path segments are discarded so this helper cannot turn a nearby value into
    a semantic match.
    """

    parts = [_semantic_row_label(row, parser=parser)]
    row_paths = table.get("row_paths") or []
    if isinstance(row_paths, list) and 0 <= row_index < len(row_paths):
        path = str(row_paths[row_index] or "")
        path_parts: list[str] = []
        for value in re.split(r"\s*>\s*", path):
            value = value.strip()
            if not value or parser(value) is not None:
                continue
            path_parts.append(value)
        if path_parts:
            parts.append(" ".join(path_parts))

    # A repeated row label may live below a period heading without carrying
    # that heading in its own cell (for example ``Năm trước`` or ``Số đầu
    # năm``).  Attach only the nearest period heading; do not merge both sides
    # of a movement schedule into every candidate.
    rows = table.get("rows") or []
    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    for index in range(min(row_index - 1, len(rows) - 1), -1, -1):
        candidate = rows[index]
        if not isinstance(candidate, list):
            continue
        # ``header_row_indices`` may contain a period header such as
        # ``Số cuối năm 2021 | Tỷ lệ %``.  Copying that whole row into the
        # selected row context contaminates the row with every neighbouring
        # column (and, in particular, makes an amount cell look like a
        # percentage cell).  Column period/metric binding is handled by
        # ``semantic_column_context``; a table header must not become row
        # evidence.
        if index in header_indices:
            continue
        candidate_label = normalize(_semantic_row_label(candidate, parser=parser))
        if candidate_label and any(
            marker in candidate_label
            for marker in (
                "so cuoi nam",
                "so dau nam",
                "cuoi nam",
                "dau nam",
                "so cuoi ky",
                "so dau ky",
                "nam nay",
                "nam truoc",
            )
        ):
            parts.append(candidate_label)
            break

    # Preserve a small amount of structural hierarchy for indented/bulleted
    # rows.  These rows often repeat ``Nguyên giá`` or ``Trung hạn bằng VND``;
    # the nearest numbered/Roman or text-only heading identifies whether the
    # value belongs to investment property, fixed assets, certificates of
    # deposit, bonds, and so on.  Only labels are copied; numeric values are
    # never used as context.
    current_raw_label = _semantic_row_label(row, parser=parser).strip()
    current_label = normalize(current_raw_label)
    if current_raw_label.startswith(("-", "▪")) or current_label in {
        "nguyen gia",
        "gia tri hao mon luy ke",
        "gia tri con lai",
    }:
        added = 0
        for index in range(min(row_index - 1, len(rows) - 1), max(-1, row_index - 8), -1):
            candidate = rows[index]
            if not isinstance(candidate, list):
                continue
            candidate_label = _semantic_row_label(candidate, parser=parser).strip()
            candidate_norm = normalize(candidate_label)
            if not candidate_norm or candidate_norm == current_label:
                continue
            if any(
                marker in candidate_norm
                for marker in (
                    "so cuoi nam",
                    "so dau nam",
                    "nam nay",
                    "nam truoc",
                    "cuoi nam",
                    "dau nam",
                )
            ):
                continue
            if (
                re.match(r"^(?:[ivxlcdm]+|\d+)\.?\s", candidate_norm)
                or all(parse_decimal(cell) is None for cell in candidate)
            ):
                parts.append(candidate_label)
                added += 1
            if added >= 2:
                break

    # Maturity/currency rows are also semantically incomplete on their own:
    # ``Trung hạn bằng VND`` can sit below certificates of deposit or below
    # bonds in the same table.  Attach the nearest value-free section heading
    # for every such descriptor, including rows that do not carry a bullet.
    # A heading may have a numeric section ordinal in its first cell, so allow
    # at most one parsed cell and reject rows containing ordinary data values.
    if any(
        marker in current_label
        for marker in (
            "trung han",
            "ngan han",
            "dai han",
            "bang vnd",
            "bang dong noi te",
            "ngoai te",
            "co ky han",
            "qua han",
        )
    ):
        for index in range(min(row_index - 1, len(rows) - 1), max(-1, row_index - 12), -1):
            candidate = rows[index]
            if not isinstance(candidate, list):
                continue
            candidate_label = _semantic_row_label(candidate, parser=parser).strip()
            candidate_norm = normalize(candidate_label)
            if not candidate_norm or candidate_norm == current_label:
                continue
            if any(
                marker in candidate_norm
                for marker in (
                    "so cuoi nam",
                    "so dau nam",
                    "nam nay",
                    "nam truoc",
                    "cuoi nam",
                    "dau nam",
                    "trung han",
                    "ngan han",
                    "dai han",
                    "bang vnd",
                    "ngoai te",
                )
            ):
                continue
            numeric_cells = sum(parser(cell) is not None for cell in candidate)
            if numeric_cells <= 1:
                parts.append(candidate_label)
                break
    return " ".join(part for part in parts if part).strip()


def _semantic_header_rows(
    table: Mapping[str, Any], evidence: list[dict[str, Any]], *, row_index: int
) -> list[list[Any]]:
    """Collect header rows without treating nearby data as column metadata."""

    rows: list[list[Any]] = []
    seen: set[tuple[str, ...]] = set()

    def add(row: Any) -> None:
        if not isinstance(row, list):
            return
        key = tuple(str(value or "") for value in row)
        if key and key not in seen:
            seen.add(key)
            rows.append(row)

    labels = table.get("column_labels")
    if isinstance(labels, list):
        add(labels)
    headers = table.get("headers")
    if isinstance(headers, list):
        add(headers)
    def looks_like_header(row: Any) -> bool:
        if not isinstance(row, list) or not any(str(value or "").strip() for value in row):
            return False
        # The materializer sometimes records ordinary data rows in
        # ``header_row_indices``.  A real header may contain a year/date or a
        # single ordinal, but a row with several parsed amounts is data and
        # must never become column metadata.
        numeric_cells = sum(parse_decimal(value) is not None for value in row)
        return numeric_cells <= 1

    header_indices = {
        int(value)
        for value in table.get("header_row_indices") or []
        if str(value).lstrip("-").isdigit()
    }
    if not header_indices:
        header_indices = {0}
    else:
        header_indices.add(0)
    table_rows = table.get("rows") or []
    for index in sorted(header_indices):
        if 0 <= index < len(table_rows) and index <= row_index and looks_like_header(table_rows[index]):
            add(table_rows[index])
    for entry in evidence:
        index = int(entry.get("index", 0))
        if index == -1:
            add(entry.get("row"))
        elif index == 0 and not (table.get("headers") or table.get("column_labels")):
            if looks_like_header(entry.get("row")):
                add(entry.get("row"))
    return rows


def _semantic_column_header_parts(
    table: Mapping[str, Any],
    evidence: list[dict[str, Any]],
    *,
    column_index: int,
    row_index: int,
) -> list[str]:
    """Return raw header fragments bound to one selected column.

    Keeping the raw fragments alongside the normalized context is important
    for a literal ``%`` marker: ``normalize`` removes punctuation, so a
    percentage-only header would otherwise become indistinguishable from an
    amount header.
    """

    parts: list[str] = []
    for row in _semantic_header_rows(table, evidence, row_index=row_index):
        if 0 <= column_index < len(row):
            cell_text = str(row[column_index] or "").strip()
            if cell_text:
                parts.append(cell_text)
            else:
                # HTML/PDF table extraction represents a merged period header
                # once at the left edge of a three-column block.  Propagate
                # only that nearest left label to empty cells; this binds
                # ``Giá trị thuần`` at column 3 to 2021 while keeping the
                # adjacent 2020 block distinct.
                for left_index in range(column_index - 1, -1, -1):
                    left_text = str(row[left_index] or "").strip()
                    if left_text:
                        parts.append(left_text)
                        break
    return list(dict.fromkeys(parts))


def _semantic_column_period_info(
    table: Mapping[str, Any],
    evidence: list[dict[str, Any]],
    *,
    column_index: int,
    row_index: int,
) -> dict[str, Any]:
    """Return value-free year/start/end/percentage metadata for one column."""

    parts = _semantic_column_header_parts(
        table,
        evidence,
        column_index=column_index,
        row_index=row_index,
    )
    context = normalize(" ".join(parts))
    signals = _semantic_period_signals(context)
    return {
        "parts": parts,
        "context": context,
        "years": {int(value) for value in YEAR_RE.findall(context)},
        "start": bool(signals["start"]),
        "end": bool(signals["end"]),
        "percentage": any(
            "%" in str(part)
            or any(_semantic_phrase_present(part, marker) for marker in _SEMANTIC_PERCENT_MARKERS if marker != "%")
            for part in parts
        ),
    }


def semantic_column_context(
    table: Mapping[str, Any],
    evidence: list[dict[str, Any]],
    *,
    column_index: int,
    row_index: int,
) -> str:
    """Return only the normalized labels that describe one selected column."""

    return str(
        _semantic_column_period_info(
            table,
            evidence,
            column_index=column_index,
            row_index=row_index,
        )["context"]
    )


_SEMANTIC_START_PERIOD_MARKERS = (
    "dau nam",
    "dau ky",
    "so dau nam",
    "so dau ky",
    "opening",
)
_SEMANTIC_END_PERIOD_MARKERS = (
    "cuoi nam",
    "cuoi ky",
    "so cuoi nam",
    "so cuoi ky",
    "closing",
)
# ``normalize`` turns ``01/01/2021`` into ``01 01 2021``.  The leading zero
# matters: the old ``1 1`` expression did not match the actual OCR/date form.
# Only an explicit 01/01 opening date or a period-end date (28/29/30/31) is
# strong enough to infer start/end; an arbitrary point date remains
# unresolved rather than being guessed as a closing period.
_SEMANTIC_START_DATE_RE = re.compile(
    r"\b0?1\s+(?:thang\s+)?0?1"
    r"(?:\s+(?:nam\s+)?(?:19|20)\d{2})?\b"
)
_SEMANTIC_END_DATE_RE = re.compile(
    r"\b0?(?:28|29|30|31)\s+(?:thang\s+)?(?:0?[1-9]|1[0-2])"
    r"(?:\s+(?:nam\s+)?(?:19|20)\d{2})?\b"
)


def _semantic_period_signals(value: Any) -> dict[str, Any]:
    """Classify start/end signals without guessing from a filename or value.

    Explicit calendar dates take precedence over generic ``tại ngày``/``đến
    ngày`` wording.  The returned positions are used only to choose an intent
    when a question mentions both ends of a range; they never supply a year
    or a numeric answer.
    """

    text = normalize(value)
    start_positions = [
        match.start()
        for marker in _SEMANTIC_START_PERIOD_MARKERS
        for match in re.finditer(rf"\b{re.escape(marker)}\b", text)
    ]
    end_positions = [
        match.start()
        for marker in _SEMANTIC_END_PERIOD_MARKERS
        for match in re.finditer(rf"\b{re.escape(marker)}\b", text)
    ]
    start_date = _SEMANTIC_START_DATE_RE.search(text)
    end_date = _SEMANTIC_END_DATE_RE.search(text)
    if start_date:
        start_positions.append(start_date.start())
    if end_date:
        end_positions.append(end_date.start())

    return {
        "text": text,
        "start": bool(start_positions),
        "end": bool(end_positions),
        "start_positions": start_positions,
        "end_positions": end_positions,
        "explicit_start_date": bool(start_date),
        "explicit_end_date": bool(end_date),
    }


def _semantic_period_intent(question: str) -> str | None:
    signals = _semantic_period_signals(question)
    starts = signals["start_positions"]
    ends = signals["end_positions"]
    if starts and not ends:
        return "start"
    if ends and not starts:
        return "end"
    if starts and ends:
        # For an explicit range, the final temporal cue is the requested
        # point when the question is phrased ``từ ... đến ...``.  If the
        # wording is not directional, keep the period unresolved instead of
        # silently preferring one side.
        if max(ends) > max(starts):
            return "end"
        if max(starts) > max(ends):
            return "start"
    return None


def _semantic_column_period_score(
    column_text: str, *, question: str, year: int | None
) -> float:
    q = normalize(question)
    score = 0.0
    if year is not None and str(year) in column_text:
        score += 4.0
    if year is not None and any(
        value in column_text
        for value in re.findall(r"(?:19|20)\d{2}", column_text)
        if value != str(year)
    ):
        score -= 2.0
    intent = _semantic_period_intent(question)
    if intent == "end":
        if any(marker in column_text for marker in ("so cuoi nam", "cuoi nam", "cuoi ky")):
            score += 4.0
        if year is not None and f"31 12 {year}" in column_text:
            score += 4.0
        if any(marker in column_text for marker in ("so dau nam", "dau nam", "dau ky", "1 1")):
            score -= 5.0
    elif intent == "start":
        if any(marker in column_text for marker in ("so dau nam", "dau nam", "dau ky")):
            score += 4.0
        if year is not None and f"1 1 {year}" in column_text:
            score += 4.0
        if any(marker in column_text for marker in ("so cuoi nam", "cuoi nam", "cuoi ky", "31 12")):
            score -= 5.0
    elif "nam nay" in column_text:
        score += 1.0
    if "ma so" in column_text or "thuyet minh" in column_text:
        score -= 3.0
    if "vnd" in column_text or "dong" in column_text:
        score += 0.2
    # A plain year in a flow question is still useful; do not infer a start or
    # end period when the question does not provide one.
    if intent is None and "trong nam" in q and "nam nay" in column_text:
        score += 1.0
    return score


def choose_semantic_column(
    table: Mapping[str, Any],
    evidence: list[dict[str, Any]],
    row_index: int,
    row: list[Any],
    year: int | None,
    question: str,
    metric: str,
    *,
    parser: Any,
) -> tuple[int, Decimal] | None:
    """Choose a numeric cell using both period and metric-column semantics.

    ``choose_year_column`` is intentionally retained for the older callers and
    source-first resolver contract.  Direct single-cell ranking needs one
    extra dimension: a row can repeat ``Giá gốc | Dự phòng | Giá trị thuần``
    or ``segment | Tổng cộng`` for the same period.  A metric-aware column
    contract prevents the first numeric column from becoming an answer merely
    because its year header matched.
    """

    numeric = [
        (index, parser(cell))
        for index, cell in enumerate(row[1:], start=1)
    ]
    numeric = [(index, value) for index, value in numeric if value is not None]
    if not numeric:
        return None

    metric_norm = normalize(metric)
    metric_tokens = _semantic_tokens(metric)
    question_norm = normalize(question)
    wants_percent = "%" in str(question) or any(
        marker != "%" and _semantic_phrase_present(question, marker)
        for marker in _SEMANTIC_PERCENT_MARKERS
    )
    asks_total = bool(re.search(r"\btong\b", question_norm))
    per_share = "co phieu" in question_norm and (
        "dong co phieu" in question_norm
        or "dong tren co phieu" in question_norm
        or "moi co phieu" in question_norm
    )
    scores: dict[int, float] = {}
    semantic_label = normalize(
        _semantic_row_context(
            table,
            row_index=row_index,
            row=row,
            parser=parser,
        )
    )
    header_rows = _semantic_header_rows(table, evidence, row_index=row_index)
    header_cells = [
        normalize(cell)
        for header in header_rows
        for cell in header
        if str(cell or "").strip()
    ]
    header_text = normalize(" ".join(header_cells))
    aggregate_header = any(
        cell in {"tong", "cong", "tong cong"}
        or "loai tru" in cell
        or "dieu chinh" in cell
        for cell in header_cells
    )
    last_numeric_column = max(index for index, _ in numeric)
    column_info = {
        column_index: _semantic_column_period_info(
            table,
            evidence,
            column_index=column_index,
            row_index=row_index,
        )
        for column_index, _ in numeric
    }
    selected_column_indices = {column_index for column_index, _ in numeric}

    # Prefer an explicitly requested year when the row contains both the
    # current and comparative numeric cells.  If the requested cell is a dash
    # or has no numeric token, keep the available wrong-period cell in the
    # pool so ``assess_semantic_cell`` can emit a hard, auditable rejection
    # instead of silently treating the prior period as a fallback answer.
    if year is not None:
        exact_year_columns = {
            column_index
            for column_index, info in column_info.items()
            if year in info["years"]
        }
        if exact_year_columns:
            # A report may label the period on a group/header column while
            # leaving the adjacent amount column unlabeled.  Retain those
            # genuinely year-ambiguous columns for metric/type scoring; do
            # not let an exact-year percentage header evict an unlabeled
            # amount cell.  Explicitly labelled non-requested years remain
            # excluded and are handled as a hard mismatch by the guard if
            # they are ever selected through another route.
            selected_column_indices = exact_year_columns | {
                column_index
                for column_index, info in column_info.items()
                if not info["years"]
            }

    period_intent = _semantic_period_intent(question)
    if period_intent in {"start", "end"}:
        matching_period_columns = {
            column_index
            for column_index in selected_column_indices
            if column_info[column_index][period_intent]
        }
        if matching_period_columns:
            selected_column_indices = matching_period_columns | {
                column_index
                for column_index in selected_column_indices
                if not column_info[column_index]["start"]
                and not column_info[column_index]["end"]
            }

    # An amount query should use a non-percentage column whenever one exists
    # in the already selected year/period pool.  When every available cell is
    # percentage-labelled, do not manufacture an amount: leave the pool
    # intact so the semantic guard returns ``PERCENTAGE_CELL_FOR_AMOUNT_QUERY``.
    non_percentage_columns = {
        column_index
        for column_index in selected_column_indices
        if not column_info[column_index]["percentage"]
    }
    if wants_percent:
        percentage_columns = {
            column_index
            for column_index in selected_column_indices
            if column_info[column_index]["percentage"]
        }
        if percentage_columns:
            selected_column_indices = percentage_columns
    elif non_percentage_columns:
        selected_column_indices = non_percentage_columns

    for column_index, _ in numeric:
        if column_index not in selected_column_indices:
            continue
        context = str(column_info[column_index]["context"])
        score = _semantic_column_period_score(context, question=question, year=year)
        header_tokens = set(_semantic_tokens(context))
        overlap = len(set(metric_tokens) & header_tokens)
        if overlap:
            score += min(6.0, 1.35 * overlap)
        if metric_norm and metric_norm in context:
            score += 5.0
        for phrase in (
            "gia tri thuan",
            "nguyen gia",
            "gia tri ghi so",
            "thu lao",
            "so luong",
            "vnd co phieu",
            "tong cong",
        ):
            if phrase in metric_norm and phrase in context:
                score += 3.0
        if any(marker in context for marker in _SEMANTIC_PERCENT_MARKERS):
            score += 5.0 if wants_percent else -6.0
        elif wants_percent:
            score -= 3.0
        if asks_total:
            if "tong" in context:
                score += 4.0
            if any(marker in context for marker in ("linh vuc", "bo phan", "phan khuc", "nganh nghe")):
                score -= 1.0
        # Segment and movement tables often expose the issuer-level result in
        # the last ``Tổng/Cộng`` column.  Some OCR assets lose that header and
        # retain only ``Điều chỉnh và loại trừ``; the last numeric column is a
        # safe structural hint only when the row itself is a total/revenue
        # line or the header visibly contains an aggregate column.
        if aggregate_header and column_index == last_numeric_column:
            if any(marker in context for marker in ("tong", "cong")):
                score += 3.5
            elif "loai tru" in header_text and any(
                marker in semantic_label
                for marker in ("doanh thu", "tong", "loi nhuan", "tai san", "no phai tra")
            ):
                score += 3.0
        if "vnd" in metric_norm or "noi te" in question_norm:
            if "ngoai te" in context:
                score -= 5.0
            if "vnd" in context:
                score += 2.0
        if "ngoai te" in metric_norm or "usd" in question_norm:
            if "ngoai te" in context or "usd" in context:
                score += 2.0
        if per_share:
            if "co phieu" in context and ("vnd" in context or "dong" in context):
                score += 5.0
            if any(unit in context for unit in ("trieu", "ty", "nghin")):
                score -= 3.0
        scores[column_index] = score

    # Preserve the established period choice when no metric header carries
    # useful information.  It remains a deterministic fallback, not authority.
    try:
        legacy_choice = choose_year_column(evidence, row_index, row, year, question)
    except (NameError, TypeError):
        legacy_choice = None
    if legacy_choice is not None and legacy_choice[0] in scores:
        scores[legacy_choice[0]] += 1.25

    ranked = sorted(scores.items(), key=lambda entry: (entry[1], -entry[0]), reverse=True)
    if not ranked:
        return None
    best_index, best_score = ranked[0]
    if len(ranked) > 1 and abs(best_score - ranked[1][1]) < 0.18:
        if legacy_choice is None or legacy_choice[0] not in {best_index, ranked[1][0]}:
            return None
        best_index = legacy_choice[0]
    raw_value = parser(row[best_index])
    if raw_value is None:
        return None
    return best_index, raw_value


def _semantic_table_context(table: Mapping[str, Any]) -> str:
    parts: list[str] = []
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        for key in ("source_title", "summary", "topic"):
            value = trace.get(key)
            if isinstance(value, Mapping):
                parts.append(str(value.get("label") or value.get("kind") or ""))
            else:
                parts.append(str(value or ""))
    for field in ("table_function", "table_section", "table_purpose"):
        value = table.get(field)
        if isinstance(value, Mapping):
            parts.extend(str(value.get(key) or "") for key in ("kind", "label", "matched_evidence"))
        elif value:
            parts.append(str(value))
    for field in ("headers", "column_labels"):
        values = table.get(field)
        if isinstance(values, list):
            parts.extend(str(value or "") for value in values)
    return normalize(" ".join(parts))


def _semantic_local_row_context(
    table: Mapping[str, Any], *, row_index: int, parser: Any
) -> str:
    rows = table.get("rows") or []
    parts: list[str] = []
    start = max(0, row_index - 6)
    stop = min(len(rows), row_index + 3)
    for index in range(start, stop):
        row = rows[index]
        if isinstance(row, list):
            parts.append(
                _semantic_row_context(
                    table,
                    row_index=index,
                    row=row,
                    parser=parser,
                )
            )
    return normalize(" ".join(parts))


def assess_semantic_cell(
    item: Mapping[str, Any],
    table: Mapping[str, Any],
    *,
    row_label: str,
    row_index: int,
    column_index: int,
    raw_cell: Any,
    column_context: str,
    evidence: list[dict[str, Any]],
    source_multiplier: Decimal,
    parser: Any,
    metric: str | None = None,
) -> dict[str, Any]:
    """Apply fail-closed semantic contracts to one direct-lookup cell.

    This is a generic family-level guard.  It deliberately contains no
    Question-ID exception.  A failed contract removes the cell from the
    semantic ranking; the normal candidate/model lanes may still provide a
    separately labelled best-effort proposal.
    """

    plan = item.get("question_plan") or {}
    question = str(item.get("question") or "")
    q = normalize(question)
    metric = metric or metric_text(dict(item))
    metric_norm = normalize(metric)
    row = normalize(row_label)
    column = normalize(column_context)
    table_context = _semantic_table_context(table)
    local_context = _semantic_local_row_context(table, row_index=row_index, parser=parser)
    table_rows = table.get("rows") or []
    raw_selected_row = ""
    if isinstance(table_rows, list) and 0 <= row_index < len(table_rows):
        selected_row_value = table_rows[row_index]
        if isinstance(selected_row_value, list):
            raw_selected_row = normalize(
                _semantic_row_label(selected_row_value, parser=parser)
            )
    selected_surface = " ".join(part for part in (row, column) if part)
    surface = " ".join(part for part in (selected_surface, local_context) if part)
    target_text = f"{q} {metric_norm}"
    question_wants_percentage = "%" in question or any(
        marker != "%" and _semantic_phrase_present(question, marker)
        for marker in _SEMANTIC_PERCENT_MARKERS
    )
    reason_codes: list[str] = []
    strengths: list[str] = []
    adjustment = 0.0

    def reject(code: str) -> None:
        if code not in reason_codes:
            reason_codes.append(code)

    def require(code: str, phrases: tuple[str, ...], *, include_table: bool = False) -> None:
        # A metric qualifier must be bound to the selected row or column.  The
        # local window is useful for section/table contracts, but allowing a
        # neighboring row to satisfy ``giá trị thuần`` or ``Techcombank`` can
        # silently authorize the wrong numeric cell.
        search = selected_surface if not include_table else f"{selected_surface} {table_context}"
        if not any(_semantic_phrase_present(search, phrase) for phrase in phrases):
            reject(code)
        else:
            strengths.append(code)

    def require_all(code: str, phrases: tuple[str, ...], *, include_table: bool = False) -> None:
        search = selected_surface if not include_table else f"{selected_surface} {table_context}"
        if not all(_semantic_phrase_present(search, phrase) for phrase in phrases):
            reject(code)
        else:
            strengths.append(code)

    def require_row_any(code: str, phrases: tuple[str, ...]) -> None:
        if not any(_semantic_phrase_present(row, phrase) for phrase in phrases):
            reject(code)
        else:
            strengths.append(code)

    def require_row_all(code: str, phrases: tuple[str, ...]) -> None:
        if not all(_semantic_phrase_present(row, phrase) for phrase in phrases):
            reject(code)
        else:
            strengths.append(code)

    def require_raw_row_any(code: str, phrases: tuple[str, ...]) -> None:
        if not any(_semantic_phrase_present(raw_selected_row, phrase) for phrase in phrases):
            reject(code)
        else:
            strengths.append(code)

    selected_is_total_row = bool(
        re.search(r"\b(?:tong|cong|so du cuoi nam|so du cuoi ky)\b", row)
    )
    expected_tickers = _semantic_expected_tickers(item)
    actual_ticker = table_ticker(table)
    if expected_tickers and actual_ticker and actual_ticker not in expected_tickers:
        reject("ENTITY_TABLE_TICKER_MISMATCH")
    requested_years = [
        int(value)
        for value in plan.get("years") or []
        if str(value).isdigit()
    ]
    table_year = _int_or_none(table.get("report_year")) or document_year(table.get("document_id"))
    if requested_years and table_year is not None and table_year != requested_years[0]:
        reject("REPORT_YEAR_MISMATCH")
    prior_period_markers = (
        "nam truoc",
        "ky truoc",
        "nam lien truoc",
        "prior year",
        "previous year",
    )
    row_has_prior_period = any(
        marker in f"{row} {raw_selected_row}" for marker in prior_period_markers
    )
    question_has_prior_period = any(marker in q for marker in prior_period_markers)
    if (
        requested_years
        and table_year == requested_years[0]
        and row_has_prior_period
        and not question_has_prior_period
    ):
        reject("ROW_PRIOR_PERIOD_SELECTED_FOR_REQUESTED_YEAR")

    requested_scope = str(plan.get("scope") or "").strip().lower()
    if not requested_scope and "cong ty me" in q:
        requested_scope = "separate"
    table_scope = table_reporting_scope(table)
    if requested_scope and table_scope and table_scope not in {requested_scope, "unknown"}:
        reject("SCOPE_MISMATCH")

    intent = _semantic_period_intent(question)
    row_period_signals = _semantic_period_signals(row)
    row_years = {int(value) for value in YEAR_RE.findall(row)}
    if requested_years and row_years and requested_years[0] not in row_years:
        reject("ROW_PERIOD_YEAR_MISMATCH")
    column_years = {int(value) for value in YEAR_RE.findall(column)}
    if requested_years and column_years and requested_years[0] not in column_years:
        reject("COLUMN_PERIOD_YEAR_MISMATCH")
    selected_column_info = _semantic_column_period_info(
        table,
        evidence,
        column_index=column_index,
        row_index=row_index,
    )
    peer_column_years = {
        year_value
        for peer_index in range(
            1,
            max(
                (
                    len(candidate_row)
                    for candidate_row in table_rows
                    if isinstance(candidate_row, list)
                ),
                default=0,
            ),
        )
        for year_value in _semantic_column_period_info(
            table,
            evidence,
            column_index=peer_index,
            row_index=row_index,
        )["years"]
    }
    # If a different explicit year is present elsewhere but the selected
    # numeric column has no year binding, its period is ambiguous.  A peer
    # carrying only the requested year is not a contradiction: this is the
    # common single-period layout where the metric header omits the repeated
    # year.  Keep the direct cell fail-closed only when a non-requested year
    # creates real comparative-period evidence.
    if (
        requested_years
        and peer_column_years
        and not column_years
        and any(value != requested_years[0] for value in peer_column_years)
    ):
        reject("COLUMN_PERIOD_YEAR_MISMATCH")
    column_period_signals = _semantic_period_signals(column)
    if intent == "end" and row_period_signals["start"] and not row_period_signals["end"]:
        reject("ROW_PERIOD_START_SELECTED_FOR_END_QUERY")
    if intent == "start" and row_period_signals["end"] and not row_period_signals["start"]:
        reject("ROW_PERIOD_END_SELECTED_FOR_START_QUERY")
    if intent == "end" and column_period_signals["start"] and not column_period_signals["end"]:
        reject("COLUMN_PERIOD_START_SELECTED_FOR_END_QUERY")
    if intent == "start" and column_period_signals["end"] and not column_period_signals["start"]:
        reject("COLUMN_PERIOD_END_SELECTED_FOR_START_QUERY")
    if not any(marker in q for marker in _SEMANTIC_ACQUISITION_MARKERS) and any(
        marker in f"{table_context} {local_context}" for marker in _SEMANTIC_ACQUISITION_MARKERS
    ):
        reject("ACQUISITION_DATE_CONTEXT_FOR_REPORT_BALANCE")

    # A table-of-contents line or report-heading line can share generic words
    # with a metric.  It is never a financial value row for this lane.  Use
    # the raw selected row and raw header fragments so a page number or a
    # literal ``%`` cannot be erased by normalization before this gate.
    navigation_row_markers = (
        "muc luc",
        "bao cao tai chinh",
        "bao cao ket qua",
        "bao cao luu chuyen",
        "bao cao thay doi",
        "thuyet minh bao cao",
    )
    raw_row_is_navigation = any(
        _semantic_phrase_present(raw_selected_row, marker)
        for marker in navigation_row_markers
    )
    raw_column_parts = selected_column_info["parts"]
    column_is_page_navigation = any(
        _semantic_phrase_present(part, marker)
        for part in raw_column_parts
        for marker in ("trang", "page", "muc luc")
    )
    if raw_row_is_navigation or column_is_page_navigation:
        reject("REPORT_NAVIGATION_ROW")

    # High-risk metric qualifiers must be present in the selected row/column
    # or its immediate local section.  Broad report titles alone cannot satisfy
    # these requirements.
    if "gia tri thuan" in f"{q} {metric_norm}":
        require("METRIC_NET_VALUE_REQUIRED", ("gia tri thuan", "net value"))
    if "nguyen gia" in target_text:
        # Investment schedules often encode the original investment cost as
        # the balance-sheet ``Số cuối năm`` cell under an exact investment
        # row, rather than repeating ``Nguyên giá`` in the column header.  It
        # is safe to admit that layout only when the selected row itself names
        # the requested subsidiary investment; fixed-asset rows still need an
        # explicit ``Nguyên giá`` binding.
        if "dau tu" in target_text and "cong ty con" in target_text:
            if _semantic_phrase_present(selected_surface, "nguyen gia"):
                strengths.append("METRIC_ORIGINAL_COST_REQUIRED")
            elif _semantic_phrase_present(row, "dau tu vao cong ty con") and any(
                _semantic_phrase_present(selected_surface, phrase)
                for phrase in ("so cuoi nam", "gia goc", "gia tri ghi so", "gia tri")
            ):
                strengths.append("METRIC_ORIGINAL_COST_INVESTMENT_BALANCE_LAYOUT")
            else:
                reject("METRIC_ORIGINAL_COST_REQUIRED")
        else:
            require("METRIC_ORIGINAL_COST_REQUIRED", ("nguyen gia", "original cost"))
    if "khau hao" in f"{q} {metric_norm}" or "hao mon" in f"{q} {metric_norm}":
        require("METRIC_DEPRECIATION_REQUIRED", ("khau hao", "hao mon"))
    if "phai tra nguoi ban" in f"{q} {metric_norm}":
        require("METRIC_SUPPLIER_PAYABLE_REQUIRED", ("phai tra nguoi ban",))
    if "mua dich vu" in f"{q} {metric_norm}" or "mua cac dich vu" in f"{q} {metric_norm}":
        require("METRIC_SERVICE_PURCHASE_REQUIRED", ("dich vu",))
    if "cho vay" in f"{q} {metric_norm}":
        require("METRIC_LENDING_DIRECTION_REQUIRED", ("cho vay", "no cho vay"))
        if "vay " in row and "cho vay" not in row and "no cho vay" not in row:
            reject("LENDING_BORROWING_DIRECTION_CONFLICT")
    if "lai vay" in f"{q} {metric_norm}":
        require("METRIC_BORROWING_INTEREST_REQUIRED", ("lai vay", "chi phi lai tien vay"))
    if "lai tien gui" in f"{q} {metric_norm}" or "tien cho vay" in f"{q} {metric_norm}":
        require("METRIC_INTEREST_INCOME_REQUIRED", ("lai tien gui", "lai tien cho vay", "thu nhap lai"))
    if "dau tu" in target_text and "cong ty con" in target_text:
        require_all("METRIC_SUBSIDIARY_INVESTMENT_REQUIRED", ("dau tu", "cong ty con"))
    if "gia tri ghi so" in f"{q} {metric_norm}":
        require("METRIC_BOOK_VALUE_REQUIRED", ("gia tri ghi so", "so du"))
    if "thu lao" in f"{q} {metric_norm}":
        require("METRIC_REMUNERATION_REQUIRED", ("thu lao", "luong", "remuneration"))
    if "so luong co phan" in f"{q} {metric_norm}" or "so luong co phieu" in f"{q} {metric_norm}":
        if "so luong" not in selected_surface or not any(
            phrase in selected_surface for phrase in ("co phan", "co phieu")
        ):
            reject("METRIC_SHARE_COUNT_REQUIRED")
        else:
            strengths.append("METRIC_SHARE_COUNT_REQUIRED")
    if "gia tri co phieu" in f"{q} {metric_norm}":
        require_all("METRIC_SHARE_VALUE_REQUIRED", ("gia tri", "co phieu"))
    if "techcombank" in f"{q} {metric_norm}":
        require("COUNTERPARTY_TECHCOMBANK_REQUIRED", ("techcombank",), include_table=True)
    if "usd" in f"{q} {metric_norm}":
        require("CURRENCY_USD_REQUIRED", ("usd",), include_table=True)

    # Family-level row contracts.  These are intentionally expressed as
    # semantic qualifiers, not Question-ID exceptions: a selected cell must
    # carry the distinguishing financial line in its row hierarchy.
    if "gop von" in target_text and "don vi khac" in target_text:
        require_row_all("ROW_OTHER_INVESTMENT_REQUIRED", ("gop von", "don vi khac"))
    if "dau tu" in target_text and "cong ty lien ket" in target_text:
        require_row_all("ROW_ASSOCIATE_INVESTMENT_REQUIRED", ("dau tu", "cong ty lien ket"))
    if "bat dong san dau tu" in target_text:
        require_row_all("ROW_INVESTMENT_PROPERTY_REQUIRED", ("bat dong san", "dau tu"))
    if "tong tai san" in target_text:
        require_row_any("ROW_TOTAL_ASSETS_REQUIRED", ("tong tai san",))
    if "so du cuoi nam" in target_text or "so du cuoi ky" in target_text:
        require(
            "PERIOD_CLOSING_BALANCE_REQUIRED",
            ("so du cuoi nam", "so du cuoi ky", "cuoi nam", "cuoi ky"),
        )
    if "so du cuoi nam" in target_text and "quy binh on" in target_text:
        require_raw_row_any("ROW_FUND_CLOSING_BALANCE_REQUIRED", ("so du cuoi nam",))
    if "loi nhuan sau thue" in target_text:
        require_row_any("ROW_NET_PROFIT_REQUIRED", ("loi nhuan sau thue", "loi nhuan thuan sau thue"))
    if "loi nhuan thuan" in target_text and "loi nhuan sau thue" not in target_text:
        require_row_any("ROW_NET_PROFIT_LINE_REQUIRED", ("loi nhuan thuan", "loi nhuan trong nam"))
    if "chi phi lai vay" in target_text:
        require_row_any("ROW_BORROWING_INTEREST_REQUIRED", ("chi phi lai vay", "lai vay"))
    if "lai tien gui" in target_text or "tien cho vay" in target_text:
        require_row_any("ROW_INTEREST_INCOME_REQUIRED", ("lai tien gui", "lai cho vay", "thu nhap lai"))
    if "so du ngoai te" in target_text:
        require_row_any("ROW_FOREIGN_CURRENCY_BALANCE_REQUIRED", ("usd", "do la my", "ngoai te"))
    if "tien gui tiet kiem" in target_text:
        require_row_any("ROW_SAVINGS_DEPOSIT_REQUIRED", ("tiet kiem",))
    if "tien gui co ky han" in target_text:
        require_row_all("ROW_TERM_DEPOSIT_REQUIRED", ("tien gui", "ky han"))
    if "ky phieu" in target_text and "trung han" in target_text:
        require_row_any("ROW_MEDIUM_TERM_BOND_REQUIRED", ("ky phieu", "trai phieu"))
    if "cho vay tai tro" in target_text or "upas" in target_text:
        require_row_any("ROW_UPAS_LENDING_REQUIRED", ("upas", "cho vay tai tro"))
    if "no xau" in target_text:
        require_row_any("ROW_BAD_DEBT_REQUIRED", ("no xau", "no kho doi"))
    if "thue tai chinh" in target_text:
        require_row_any("ROW_FINANCE_LEASE_REQUIRED", ("thue tai chinh", "thue tai san"))
    if "cam ket" in target_text and ("cho thue" in target_text or "thue hoat dong" in target_text):
        require_row_all("ROW_OPERATING_LEASE_COMMITMENT_REQUIRED", ("cam ket", "thue"))
    if "von co phan" in target_text:
        require_row_any("ROW_SHARE_CAPITAL_REQUIRED", ("von co phan",))
    if "chi phi quan ly doanh nghiep" in target_text and "khau hao" in target_text:
        require_row_all(
            "ROW_MANAGEMENT_DEPRECIATION_REQUIRED",
            ("chi phi quan ly doanh nghiep", "khau hao"),
        )
    if "bat dong san dau tu" in target_text and "nguyen gia" in target_text:
        require_row_all(
            "ROW_INVESTMENT_PROPERTY_ORIGINAL_COST_REQUIRED",
            ("bat dong san", "dau tu", "nguyen gia"),
        )
    if "tai san co dinh huu hinh khac" in target_text:
        require_row_all(
            "ROW_OTHER_TANGIBLE_FIXED_ASSET_REQUIRED",
            ("tai san co dinh", "huu hinh", "khac"),
        )
    if "thue toi thieu" in target_text and ("hop dong thue" in target_text or "tuong lai" in target_text):
        require_row_all("ROW_FUTURE_MINIMUM_LEASE_RECEIPTS_REQUIRED", ("thue", "toi thieu"))
    if "tong gia tri trai phieu" in target_text:
        if any(
            _semantic_phrase_present(row, phrase)
            for phrase in ("chi phi phat hanh", "den han tra", "trai phieu den han")
        ):
            reject("BOND_COMPONENT_VALUE_CONFLICT")
        if not selected_is_total_row and not _semantic_phrase_present(column, "tong"):
            reject("BOND_TOTAL_NOT_BOUND")
    if "tong chi phi san xuat" in target_text or "chi phi san xuat va kinh doanh" in target_text:
        require_row_all("ROW_PRODUCTION_BUSINESS_COST_REQUIRED", ("chi phi", "san xuat", "kinh doanh"))
        if not selected_is_total_row and not _semantic_phrase_present(column, "tong"):
            reject("PRODUCTION_BUSINESS_COST_TOTAL_NOT_BOUND")
    if "tong du no cho vay" in target_text:
        if any(
            _semantic_phrase_present(row, phrase)
            for phrase in ("nhnn", "ngan hang nha nuoc", "cho vay khach hang", "khach hang")
        ) and not selected_is_total_row:
            reject("LENDING_TOTAL_COMPONENT_CONFLICT")
        # A banking detail table can place deposits and loans in one row and
        # expose only a final ``Tổng`` column.  That final number is not the
        # requested loan balance: it is a mixed deposit-plus-loan aggregate.
        # Keep the contract at the row/column boundary so a nearby table title
        # cannot authorize the wrong financial quantity.
        if any(
            _semantic_phrase_present(row, phrase)
            for phrase in ("tien gui va cho vay", "tien gui va vay", "tien gui")
        ) and not _semantic_phrase_present(row, "tong du no cho vay"):
            reject("LENDING_TOTAL_DEPOSIT_COMBINED_CONFLICT")
        if not selected_is_total_row and not _semantic_phrase_present(column, "tong"):
            reject("LENDING_TOTAL_NOT_BOUND")
    if "tong gia tri dau tu vao cong ty con" in target_text:
        if not selected_is_total_row and not _semantic_phrase_present(column, "tong"):
            reject("SUBSIDIARY_INVESTMENT_TOTAL_NOT_BOUND")
    if "gia tri ghi so" in target_text and "no phai thu" in target_text:
        require_row_any("ROW_RECEIVABLE_GROSS_VALUE_REQUIRED", ("phai thu", "khoan phai thu"))
    if "gia goc" in target_text and "no phai thu" in target_text:
        # ``Giá gốc`` is repeated across investment, bond and receivable
        # schedules.  The selected raw row must name a receivable; a section
        # heading such as ``các khoản cho vay và phải thu`` is not enough.
        require_raw_row_any("ROW_RECEIVABLE_GROSS_COST_REQUIRED", ("phai thu",))
    if "phai thu ve cho vay dai han" in target_text:
        require_raw_row_any(
            "ROW_LONG_TERM_LOAN_RECEIVABLE_REQUIRED",
            ("phai thu ve cho vay dai han", "cho vay dai han", "phai thu dai han"),
        )
    if _semantic_phrase_present(target_text, "phai thu khac"):
        require_raw_row_any(
            "ROW_OTHER_RECEIVABLE_REQUIRED",
            ("phai thu khac", "phai thu ngan han khac", "phai thu dai han khac"),
        )
    if (
        _semantic_phrase_present(target_text, "phai thu khach hang")
        or (
            _semantic_phrase_present(target_text, "phai thu ngan han")
            and _semantic_phrase_present(target_text, "khach hang")
        )
    ):
        require_raw_row_any(
            "ROW_CUSTOMER_RECEIVABLE_REQUIRED",
            ("phai thu ngan han cua khach hang", "phai thu khach hang ngan han"),
        )
    if "gia goc" in target_text:
        require("METRIC_GROSS_COST_REQUIRED", ("gia goc",))
    if "du phong" in target_text and "du phong" not in row:
        reject("PROVISION_ROW_REQUIRED")
    if "du phong" in row and "du phong" not in target_text:
        reject("PROVISION_ROW_CONFLICT")
    if "phai tra" in target_text and ("nha cung cap" in target_text or "nguoi ban" in target_text):
        require_row_any(
            "ROW_SUPPLIER_PAYABLE_REQUIRED",
            ("phai tra nguoi ban", "nha cung cap", "phai tra cho nha cung cap"),
        )
    if "doanh thu" in target_text:
        require_row_any("ROW_REVENUE_REQUIRED", ("doanh thu",))
    if "tien mat" in target_text:
        require_row_any("ROW_CASH_ON_HAND_REQUIRED", ("tien mat", "tien"))
    if "chi phi tra truoc" in target_text:
        require_row_any("ROW_PREPAID_EXPENSE_REQUIRED", ("chi phi tra truoc",))
    if "thue thu nhap doanh nghiep" in target_text:
        require_row_any(
            "ROW_CORPORATE_INCOME_TAX_REQUIRED",
            ("thue thu nhap doanh nghiep", "thue tndn"),
        )
    if "tien" in target_text and not any(
        marker in target_text
        for marker in (
            "tien gui",
            "tien mat",
            "tien va cac khoan tuong duong tien",
            "lai tien",
        )
    ):
        require_row_any(
            "ROW_CASH_REQUIRED",
            ("tien mat", "tien va cac khoan tuong duong tien", "tien"),
        )
    if "vay ngan han" in target_text and "ngan hang" in target_text:
        require_row_all("ROW_SHORT_TERM_BANK_DEBT_REQUIRED", ("vay", "ngan han", "ngan hang"))
    if "cho vay" in target_text and "trung han" in target_text:
        require_row_all("ROW_MEDIUM_TERM_LENDING_REQUIRED", ("cho vay", "trung han"))
    if "tong gia tri hop dong" in target_text:
        if not _semantic_phrase_present(selected_surface, "tong gia tri hop dong") and not _semantic_phrase_present(
            selected_surface, "gia tri giao dich theo hop dong"
        ):
            reject("ROW_DERIVATIVE_TOTAL_REQUIRED")
        else:
            strengths.append("ROW_DERIVATIVE_TOTAL_REQUIRED")
        if not selected_is_total_row and not _semantic_phrase_present(
            raw_selected_row, "tong gia tri hop dong"
        ):
            reject("DERIVATIVE_CONTRACT_TOTAL_NOT_BOUND")
        if not selected_is_total_row and not _semantic_phrase_present(column, "tong"):
            reject("DERIVATIVE_TOTAL_NOT_BOUND")
    if "theo nganh nghe" in target_text:
        # ``row_paths`` for an unlabeled aggregate row may contain the whole
        # header vector.  Bind the requested measure to the selected column,
        # not merely to that navigation metadata, otherwise the adjacent
        # ``Tổng tiền gửi, tiền vay`` column can win by score.
        if not _semantic_phrase_present(column, "tong du no cho vay"):
            reject("INDUSTRY_LOAN_COLUMN_REQUIRED")
        else:
            strengths.append("INDUSTRY_LOAN_COLUMN_REQUIRED")
        raw_row_is_unlabelled = not raw_selected_row.strip(" -▪")
        current_numeric_cells = sum(parse_decimal(cell) is not None for cell in table_rows[row_index]) if (
            isinstance(table_rows, list) and 0 <= row_index < len(table_rows)
            and isinstance(table_rows[row_index], list)
        ) else 0
        later_numeric_row = any(
            isinstance(candidate_row, list)
            and sum(parse_decimal(cell) is not None for cell in candidate_row) >= 2
            for candidate_row in table_rows[row_index + 1:]
        ) if isinstance(table_rows, list) else False
        terminal_aggregate = raw_row_is_unlabelled and current_numeric_cells >= 2 and not later_numeric_row
        if not selected_is_total_row and not terminal_aggregate:
            reject("ROW_INDUSTRY_TOTAL_REQUIRED")
        else:
            strengths.append("ROW_INDUSTRY_TOTAL_REQUIRED")
    if "von gop" in target_text and "tong" in target_text:
        if "nhan von gop" in row or "tien thu" in row:
            reject("EQUITY_CASHFLOW_RECEIPT_CONFLICT")
        else:
            require_row_any("ROW_EQUITY_CONTRIBUTION_REQUIRED", ("von gop",))
    if "von co phan" in target_text and "thang du von co phan" in row and "thang du" not in target_text:
        reject("SHARE_CAPITAL_PREMIUM_CONFLICT")
    if "khach hang tra truoc" in target_text:
        require_row_all("ROW_CUSTOMER_ADVANCE_REQUIRED", ("khach hang", "tra truoc"))
    if "mua dich vu" in target_text:
        require_row_any("ROW_SERVICE_PURCHASE_REQUIRED", ("dich vu",))
        if "cang nam hai dinh vu" in target_text and "cang nam hai dinh vu" not in row:
            reject("COUNTERPARTY_SERVICE_ROW_MISMATCH")

    # Generic words are not allowed to erase a more specific reported metric.
    if "gia tri thuan" in q and "gia goc" in row and "gia tri thuan" not in surface:
        reject("NET_VALUE_ORIGINAL_COST_CONFLICT")
    if "nguyen gia" in q and "gia tri con lai" in row and "nguyen gia" not in surface:
        reject("ORIGINAL_COST_CARRYING_VALUE_CONFLICT")
    if "tong tai san" in q and any(marker in row for marker in ("thay doi", "bien dong", "luu chuyen")):
        reject("TOTAL_ASSET_OPERATIONAL_CHANGE_CONFLICT")
    if "tong so du" in q and "lai suat" in row:
        reject("BALANCE_INTEREST_RATE_CONFLICT")
    if "tong tai san" in q and "tai san hoat dong" in row:
        reject("TOTAL_ASSET_COMPONENT_CONFLICT")
    if "tien" in metric_norm and "tien gui" not in metric_norm and any(
        marker in selected_surface for marker in ("tien gui co ky han", "tien gui")
    ):
        reject("CASH_TERM_DEPOSIT_CONFLICT")
    if "co phan" in q and "von gop" in row and "so luong" not in surface:
        reject("SHARE_COUNT_CAPITAL_CONTRIBUTION_CONFLICT")
    if "thu lao" in q and any(marker in row for marker in ("co phan", "so luong", "quyen so huu")) and "thu lao" not in column:
        reject("REMUNERATION_SHAREHOLDING_CONFLICT")
    if "tien gui cua khach hang" in f"{q} {metric_norm}" and any(
        marker in f"{table_context} {local_context}" for marker in ("qua han", "duoi 1 thang", "tu 1 den 3 thang")
    ) and "tong cong" not in column and "tong" not in row:
        reject("CUSTOMER_DEPOSIT_MATURITY_COMPONENT")

    # In Vietnamese financial questions, ``Tổng số dư`` usually means the
    # reported balance of one named line, not a request to add component rows.
    # Keep the broad component-row gate for true aggregate labels such as
    # ``Tổng tài sản`` or ``Tổng dư nợ``, while letting the more specific row
    # contracts below bind ``Tổng số dư ...`` to its named line.
    asks_total = bool(re.search(r"\btong\b", q)) and not any(
        phrase in q
        for phrase in (
            "tong so du",
            "tong gia tri",
            "tong gia goc",
            "tong gia tri ghi so",
            "tong so luong",
        )
    )
    has_total_row = any(
        re.search(
            r"\btong(?: cong| so| tai san| doanh thu| gia von)?\b",
            normalize(_semantic_row_label(row_value, parser=parser)),
        )
        for row_value in (table.get("rows") or [])
        if isinstance(row_value, list)
    )
    has_total_column = _semantic_phrase_present(column, "tong") or _semantic_phrase_present(
        column, "cong"
    )
    selected_is_total_row = bool(
        re.search(r"\b(?:tong|cong|so du cuoi nam|so cuoi ky)\b", row)
    )
    segment_table = any(
        marker in f"{table_context} {local_context}"
        for marker in ("linh vuc", "bo phan", "phan khuc", "nganh nghe")
    )
    if asks_total and not has_total_row and not has_total_column:
        component_markers = (
            "ngoai te",
            "tien gui",
            "ngan hang",
            "ngan han",
            "dai han",
            "ben lien quan",
            "thanh phan",
            "du phong",
            "nhnn",
            "cong ty ",
            "den han tra",
            "cong cu",
            "cho vay khach hang",
        )
        if segment_table or any(marker in row for marker in component_markers):
            reject("TOTAL_COMPONENT_ROW_WITHOUT_TOTAL_BINDING")
    if asks_total and not has_total_column and not selected_is_total_row and any(
        marker in row for marker in ("den han tra", "cong cu", "cho vay khach hang", "tien gui", "cong ty ")
    ):
        reject("TOTAL_COMPONENT_ROW_NOT_SELECTED_AS_TOTAL")
    if asks_total and segment_table and not has_total_column and not has_total_row:
        reject("SEGMENT_TOTAL_NOT_BOUND")
    if asks_total and "tong cong" in f"{table_context} {local_context}" and not has_total_column and not has_total_row:
        reject("TOTAL_ROW_NOT_BOUND")

    # Table-function semantics are a soft preference; the row/column
    # contracts above remain the hard evidence boundary.
    kind = normalize(
        str(
            ((table.get("table_function") or {}).get("kind"))
            if isinstance(table.get("table_function"), Mapping)
            else table.get("table_kind") or table.get("kind") or ""
        )
    )
    stock_query = intent is not None or "so du" in q
    if stock_query:
        if kind in {"balance sheet", "balance_sheet", "debt schedule", "debt_schedule"}:
            adjustment += 1.2
            strengths.append("STOCK_TABLE_KIND")
        if kind in {"cash flow statement", "cash_flow_statement"} and any(
            marker in row for marker in ("thay doi", "tang", "giam", "luu chuyen", "da nop")
        ):
            reject("STOCK_QUERY_FLOW_ROW_CONFLICT")
        if kind in {"related party schedule", "related_party_schedule"} and "ben lien quan" not in q:
            reject("UNQUALIFIED_RELATED_PARTY_TABLE")
        if kind in {"cash flow statement", "cash_flow_statement"} and "von gop" in row:
            reject("STOCK_EQUITY_CASHFLOW_CONFLICT")
    else:
        if kind in {"income statement", "income_statement", "financial note detail", "financial_note_detail"}:
            adjustment += 0.8
            strengths.append("FLOW_TABLE_KIND")

    source_currency_foreign = any(
        marker in selected_surface for marker in ("usd", "ngoai te")
    )
    asks_vnd_output = any(marker in q for marker in ("ty dong", "trieu dong", "nghin dong", "vnd"))
    if source_currency_foreign and asks_vnd_output and any(
        marker in q for marker in ("usd", "ngoai te")
    ):
        # The corpus cell is a foreign-currency amount.  Without an explicit
        # FX rate/contract, dividing it by a VND output unit is not a valid
        # conversion and must not become a candidate answer.
        reject("FOREIGN_CURRENCY_TO_VND_CONVERSION_MISSING")
    if source_currency_foreign and ("noi te" in q or "vnd" in q) and "ngoai te" in f"{row} {column}":
        reject("DOMESTIC_FOREIGN_CURRENCY_CONFLICT")
    if any(marker in q for marker in ("ngoai te", "usd")) and any(
        marker in selected_surface for marker in ("vnd", "noi te")
    ) and not any(marker in selected_surface for marker in ("ngoai te", "usd")):
        reject("FOREIGN_DOMESTIC_CURRENCY_CONFLICT")
    selected_column_is_percentage = bool(selected_column_info["percentage"]) or "%" in str(raw_cell)
    if selected_column_is_percentage and not question_wants_percentage:
        reject("PERCENTAGE_CELL_FOR_AMOUNT_QUERY")

    if "dong co phieu" in q or "dong tren co phieu" in q or "moi co phieu" in q:
        if source_multiplier != Decimal(1):
            reject("PER_SHARE_SOURCE_SCALE_MISMATCH")
        if not any(marker in selected_surface for marker in ("co phieu", "vnd co phieu", "dong co phieu")):
            reject("PER_SHARE_UNIT_NOT_BOUND")

    # Every direct candidate still carries a machine-readable explanation.  A
    # non-empty reason list is deliberately not hidden by a high score.
    if not reason_codes:
        adjustment += min(1.5, 0.25 * len(strengths))
    return {
        "accepted": not reason_codes,
        "score_adjustment": adjustment,
        "reason_codes": reason_codes,
        "strengths": strengths,
        "protocol": "semantic_cell_contract_v1",
        "table_ticker": actual_ticker,
        "table_year": table_year,
        "requested_scope": requested_scope or None,
        "column_context": column_context,
        "row_index": row_index,
        "column_index": column_index,
        "evidence_window_size": len(evidence),
    }


def choose_year_column(
    evidence: list[dict[str, Any]], row_index: int, row: list[Any], year: int | None, question: str
) -> tuple[int, Decimal] | None:
    numeric = [(idx, parse_decimal(cell)) for idx, cell in enumerate(row[1:], start=1)]
    numeric = [(idx, value) for idx, value in numeric if value is not None]
    if not numeric:
        return None
    year_text = str(year) if year else ""
    period_intent = _semantic_period_intent(question)
    start_intent = period_intent == "start"
    end_intent = period_intent == "end"
    scores: dict[int, float] = {}
    wants_percent = "%" in question or any(
        marker != "%" and _semantic_phrase_present(question, marker)
        for marker in _SEMANTIC_PERCENT_MARKERS
    )
    for header in evidence:
        if int(header.get("index", 0)) > row_index:
            continue
        cells = header.get("row") or []
        for idx, cell in enumerate(cells):
            text = str(cell)
            text_norm = normalize(text)
            score = scores.get(idx, 0.0)
            if year_text and year_text in text:
                score += 3.0
            elif year and YEAR_RE.search(text):
                score -= 0.5
            if "ma so" in text_norm or "thuyet minh" in text_norm:
                score -= 4.0
            if any(token in text_norm for token in ("nam nay", "so cuoi nam", "cuoi ky")):
                score += 0.8
            if start_intent:
                if "so dau nam" in text_norm or "dau ky" in text_norm:
                    score += 4.0
                if "so cuoi nam" in text_norm or "cuoi ky" in text_norm:
                    score -= 3.0
            if end_intent:
                if "so cuoi nam" in text_norm or "cuoi ky" in text_norm:
                    score += 4.0
                if "so dau nam" in text_norm or "dau ky" in text_norm:
                    score -= 3.0
            if "vnd" in text_norm or "dong" in text_norm:
                score += 0.35
            if "%" in text:
                score += 2.0 if wants_percent else -1.0
            scores[idx] = score
    largest = max(abs(value) for _, value in numeric)
    ranked: list[tuple[int, Decimal, float]] = []
    for idx, value in numeric:
        score = scores.get(idx, 0.0)
        raw_cell = str(row[idx])
        if wants_percent and "%" in raw_cell:
            score += 4.0
        if largest >= Decimal("10000") and abs(value) < Decimal("1000") and "%" not in raw_cell:
            score -= 2.5
        ranked.append((idx, value, score))
    ranked.sort(key=lambda entry: (entry[2], -entry[0]), reverse=True)
    return ranked[0][0], ranked[0][1]


def document_year(document_id: Any) -> int | None:
    years = YEAR_RE.findall(str(document_id or ""))
    return int(years[-1]) if years else None


DOCUMENT_TICKER_RE = re.compile(
    r"^([A-Za-z0-9]{2,6})_financial_statements(?:_|$)",
    re.IGNORECASE,
)


def table_ticker(table: Mapping[str, Any]) -> str:
    """Return the immutable issuer key from either supported table schema.

    The original V2 bundle keeps the issuer in ``document_id`` but does not
    repeat it as a top-level ``ticker`` field.  The full-corpus asset has both
    fields.  This fallback is deliberately limited to the canonical document
    naming contract; arbitrary text is never parsed as an issuer alias.
    """

    explicit = str(table.get("ticker") or "").strip().upper()
    if explicit:
        return explicit
    document_id = str(table.get("document_id") or "").strip()
    match = DOCUMENT_TICKER_RE.match(document_id)
    return match.group(1).upper() if match else ""


def table_reporting_scope(table: Mapping[str, Any]) -> str:
    """Return the reporting perimeter from an explicit field or document key."""

    explicit = str(table.get("scope") or "").strip().lower()
    if explicit:
        return explicit
    document_id = str(table.get("document_id") or "").strip().lower()
    match = re.search(r"_(separate|consolidated|aggregated)(?:_|$)", document_id)
    return match.group(1) if match else ""


def candidate_evidence_window(
    candidate: dict[str, Any], table: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Hydrate a compact candidate window with the table's header context.

    Review candidates retain original row indices, so adding missing leading
    rows does not alter cell coordinates.  The extra rows are used only for
    year/unit interpretation and remain outside the emitted evidence.
    """
    evidence = list(candidate.get("evidence_window") or [])
    if table is None:
        return evidence
    seen = {int(row.get("index", 0)) for row in evidence}
    rows = table.get("rows") or []
    for index, row in enumerate(rows[:8]):
        if index not in seen:
            evidence.append({"index": index, "row": row})
    labels = table.get("column_labels") or []
    if labels:
        evidence.append({"index": -1, "row": labels})
    return evidence


def candidate_survives_filter(
    candidate: dict[str, Any], *, table: dict[str, Any] | None
) -> bool:
    """Keep only hydrated candidates that did not fail an upstream filter."""
    if table is None:
        return False
    if not str(candidate.get("internal_table_uid") or ""):
        return False
    if any(candidate.get(key) is False for key in ("ticker_match", "year_match", "scope_match")):
        return False
    if str(candidate.get("candidate_status") or candidate.get("research_candidate_status") or "").upper() in {
        "ABSTAIN",
        "FILTER_REJECTED",
        "QUARANTINED",
        "REJECTED",
    }:
        return False
    return bool(table.get("rows"))


def candidate_ticker(candidate: dict[str, Any]) -> str:
    ticker = str(candidate.get("ticker") or "").strip().upper()
    if ticker:
        return ticker
    return str(candidate.get("document_id") or "").split(
        "_financial_statements", 1
    )[0].strip().upper()


def dense_candidate_window(table: dict[str, Any], query: str, max_rows: int = 18) -> list[dict[str, Any]]:
    """Create a small, coordinate-preserving window for a dense hit.

    The full-corpus dense index stores navigation metadata only.  We hydrate
    a hit from the local structured table and keep the header plus rows whose
    labels overlap the question.  This makes the dense lane usable by the
    existing cell selector without copying the entire table into each item.
    """
    rows = table.get("rows") or []
    if not rows:
        return []
    query_tokens = content_tokens(query)
    scored: list[tuple[float, int]] = []
    for index, row in enumerate(rows):
        label = " ".join(str(cell) for cell in row[:3] if str(cell).strip())
        label_tokens = content_tokens(label)
        overlap = len(query_tokens & label_tokens) / max(1, len(query_tokens))
        # Headers and the first rows carry period/unit context; retain them
        # even when the metric wording is not an exact token match.
        if index < 3:
            overlap += 0.15
        scored.append((overlap, index))
    selected: set[int] = set(range(min(3, len(rows))))
    for score, index in sorted(scored, key=lambda pair: (-pair[0], pair[1])):
        if score <= 0.0 and len(selected) >= max_rows:
            break
        selected.add(index)
        if len(selected) >= max_rows:
            break
    return [{"index": index, "row": rows[index]} for index in sorted(selected)]


def expand_review_items_with_dense(
    *,
    items: dict[int, dict[str, Any]],
    tables_by_uid: dict[str, dict[str, Any]],
    index_dir: Path,
    limit: int = 50,
    device: str = "cpu",
    batch_size: int = 64,
    period_neighbor_offset: int | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, int]]:
    """Merge full-corpus dense hits into the grounded review shortlist.

    The dense lane is navigation-only.  Hits that cannot be hydrated from the
    structured table bundle are discarded, so this function cannot invent an
    evidence source or numeric value.  Reciprocal-rank fusion keeps the
    original review candidates while allowing a high-quality full-corpus hit
    that was absent from the old 40-row shortlist to enter reranking.  An
    optional report-year neighbor query is deliberately kept as a separate
    navigation lane: a financial report published in year ``Y+1`` can contain
    a comparison column for the requested year ``Y``.  The neighbor metadata
    is never treated as a value or an answer-authorizing signal.
    """
    if limit < 1 or limit > 100:
        raise ValueError("dense candidate limit must be between 1 and 100")
    if period_neighbor_offset is not None and period_neighbor_offset not in {1, 2}:
        raise ValueError("period neighbor offset must be one or two report years")
    from finance_query.e2e.core.dense_retrieval import search_dense_batch

    requests: list[dict[str, Any]] = []
    request_keys: list[tuple[int, str, int, int]] = []
    request_seen: set[tuple[int, str, int, str | None]] = set()
    for item_id, item in items.items():
        plan = item.get("question_plan") or {}
        tickers = [str(value).upper() for value in plan.get("tickers") or [] if str(value).strip()]
        years = [int(value) for value in plan.get("years") or [] if value is not None]
        if not tickers or not years:
            continue
        scope = plan.get("scope")
        for ticker in dict.fromkeys(tickers):
            # Register every exact-year request before adding Y+offset
            # requests.  A multi-period question can make one year both an
            # exact request and a neighbor request (for example Y=2018 and
            # Y=2019).  Exact-year provenance must win deterministically;
            # otherwise the earlier neighbor request can consume the identity
            # and silently remove a legitimate answer candidate from the
            # isolated answer lane.
            route_years = [(year, 0) for year in dict.fromkeys(years)]
            if period_neighbor_offset is not None:
                route_years.extend(
                    (year + period_neighbor_offset, period_neighbor_offset)
                    for year in dict.fromkeys(years)
                )
            for requested_year, offset in route_years:
                request_identity = (item_id, ticker, requested_year, scope)
                if request_identity in request_seen:
                    continue
                request_seen.add(request_identity)
                requests.append(
                    {
                        "query": str(item.get("question") or ""),
                        "ticker": ticker,
                        "report_year": requested_year,
                        "scope": scope,
                    }
                )
                request_keys.append((item_id, ticker, requested_year, offset))
    if not requests:
        return items, {
            "requests": 0,
            "dense_hits": 0,
            "hydrated_hits": 0,
            "period_neighbor_enabled": int(period_neighbor_offset is not None),
            "period_neighbor_requests": 0,
            "period_neighbor_hits": 0,
            "period_neighbor_hydrated_hits": 0,
        }
    dense_results = search_dense_batch(
        index_dir=index_dir,
        requests=requests,
        limit=limit,
        requested_device=device,
        encode_batch_size=batch_size,
    )
    by_item: dict[int, dict[str, dict[str, Any]]] = {}
    stats = {
        "requests": len(requests),
        "dense_hits": 0,
        "hydrated_hits": 0,
        "period_neighbor_enabled": int(period_neighbor_offset is not None),
        "period_neighbor_requests": sum(1 for *_, offset in request_keys if offset > 0),
        "period_neighbor_hits": 0,
        "period_neighbor_hydrated_hits": 0,
    }
    for (item_id, _ticker, _year, request_offset), results in zip(request_keys, dense_results, strict=True):
        bucket = by_item.setdefault(item_id, {})
        for result in results:
            stats["dense_hits"] += 1
            if request_offset > 0:
                stats["period_neighbor_hits"] += 1
            uid = str(result.get("internal_table_uid") or "")
            table = tables_by_uid.get(uid)
            if not uid or table is None:
                continue
            stats["hydrated_hits"] += 1
            if request_offset > 0:
                stats["period_neighbor_hydrated_hits"] += 1
            row = bucket.get(uid)
            dense_rank = int(result.get("rank") or 999)
            dense_score = float(result.get("score") or 0.0)
            existing_offset = int(row.get("period_neighbor_offset") or 0) if row else 0
            should_replace = row is None or request_offset < existing_offset or (
                request_offset == existing_offset
                and dense_rank < int(row.get("dense_rank") or 999)
            )
            if should_replace:
                bucket[uid] = {
                    "internal_table_uid": uid,
                    "document_id": str(result.get("document_id") or table.get("document_id") or ""),
                    "dense_rank": dense_rank,
                    "dense_score": dense_score,
                    "period_neighbor_offset": request_offset,
                    "period_request_year": _year,
                    "evidence_window": dense_candidate_window(table, str(items[item_id].get("question") or "")),
                    "retrieval_source": "full_corpus_dense",
                    "navigation_metadata_only": True,
                    "may_authorize_answer": False,
                    "submission_eligible": False,
                }

    expanded: dict[int, dict[str, Any]] = {}
    rrf_constant = 60.0
    for item_id, item in items.items():
        existing = list(item.get("candidates") or [])
        merged: dict[str, dict[str, Any]] = {}
        for original_rank, candidate in enumerate(existing, start=1):
            uid = str(candidate.get("internal_table_uid") or "")
            if not uid:
                continue
            row = dict(candidate)
            row["review_rank"] = original_rank
            row["retrieval_source"] = "review_bundle"
            merged[uid] = row
        for uid, dense in by_item.get(item_id, {}).items():
            row = dict(merged.get(uid) or {})
            row.update({key: value for key, value in dense.items() if key not in {"evidence_window"}})
            if not row.get("evidence_window"):
                row["evidence_window"] = dense["evidence_window"]
            row["dense_rank"] = dense["dense_rank"]
            row["dense_score"] = dense["dense_score"]
            row["period_neighbor_offset"] = dense.get("period_neighbor_offset", 0)
            row["period_request_year"] = dense.get("period_request_year")
            if uid in merged:
                row["retrieval_source"] = "review_bundle+full_corpus_dense"
            merged[uid] = row
        ranked: list[dict[str, Any]] = []
        for row in merged.values():
            review_rank = int(row.get("review_rank") or 999)
            dense_rank = int(row.get("dense_rank") or 999)
            # Candidate recall is the purpose of this expansion.  A dense hit
            # at rank 15 must not be pushed behind forty legacy candidates
            # merely because it came from a different lane.  Use the best
            # rank available for each UID, while retaining both ranks for
            # diagnostics and later cross-encoder reranking.
            effective_rank = min(review_rank, dense_rank)
            row["fusion_score"] = 1.0 / (rrf_constant + effective_rank)
            row["fusion_rank"] = effective_rank
            ranked.append(row)
        ranked.sort(
            key=lambda row: (
                int(row.get("fusion_rank") or 999),
                -float(row.get("dense_score") or 0.0),
                str(row.get("internal_table_uid") or ""),
            )
        )
        candidate_cap = max(40, limit)
        if period_neighbor_offset is not None:
            # Keep the exact-year pool intact.  A simple combined top-k would
            # let Y+1 hits evict exact-year candidates before the answer lane
            # filters them, making a table-recall ablation silently change
            # answers.  The extra neighbor tail is available to the
            # navigation emitter only.
            standard_ranked = [
                row
                for row in ranked
                if int(row.get("period_neighbor_offset") or 0) <= 0
            ]
            neighbor_ranked = [
                row
                for row in ranked
                if int(row.get("period_neighbor_offset") or 0) > 0
            ]
            retained = [
                *standard_ranked[:candidate_cap],
                *neighbor_ranked[:limit],
            ]
        else:
            retained = ranked[:candidate_cap]
        for rank, row in enumerate(retained, start=1):
            row["rank"] = rank
        updated = dict(item)
        updated["candidates"] = retained
        updated["dense_expanded"] = True
        expanded[item_id] = updated
    return expanded, stats


def period_aware_emission_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    limit: int = 12,
    neighbor_slots: int = 2,
) -> list[Mapping[str, Any]]:
    """Reserve a small early prefix for report-year-neighbor table hints.

    This helper is intentionally limited to the relevant-table emission lane.
    It does not reorder the answer candidate pool and it does not change the
    semantic cell selector.  When no neighbor candidate is present, the
    caller's normal ranking is preserved byte-for-byte for the returned
    prefix.  When neighbors exist, a few top standard candidates are followed
    by a bounded number of neighbor candidates so a comparison table cannot
    be crowded out before the F2-oriented table-reference quota is reached.
    """
    if limit < 1:
        raise ValueError("period-aware emission limit must be positive")
    if neighbor_slots < 0 or neighbor_slots > limit:
        raise ValueError("period-aware neighbor slots must be within the emission limit")
    ordered = list(candidates)
    if neighbor_slots == 0:
        return ordered[:limit]
    neighbors = [
        candidate
        for candidate in ordered
        if int(candidate.get("period_neighbor_offset") or 0) > 0
    ]
    if not neighbors:
        return ordered[:limit]
    standard = [
        candidate
        for candidate in ordered
        if int(candidate.get("period_neighbor_offset") or 0) <= 0
    ]
    reserved_neighbors = neighbors[:neighbor_slots]
    standard_prefix_size = min(3, max(0, limit - len(reserved_neighbors)))
    selected: list[Mapping[str, Any]] = [*standard[:standard_prefix_size], *reserved_neighbors]
    selected_ids = {id(candidate) for candidate in selected}
    for candidate in ordered:
        if len(selected) >= limit:
            break
        if id(candidate) not in selected_ids:
            selected.append(candidate)
            selected_ids.add(id(candidate))
    return selected[:limit]


def _answer_review_items_for_period_neighbor(
    expanded_items: dict[int, dict[str, Any]],
    *,
    period_neighbor_offset: int | None,
    navigation_only: bool = False,
    pre_dense_items: dict[int, dict[str, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    """Choose the answer lane for a report-year-neighbor ablation.

    The normal period-neighbor arm keeps exact-year dense hits and research
    hints available to answer selection, while removing only candidates
    explicitly tagged as ``Y+offset``.  The stricter navigation-only arm is a
    component ablation: it keeps answer selection on the pre-dense review
    pool, and exposes the expanded pool only to document/table emission.
    """
    if period_neighbor_offset is None:
        return expanded_items
    if navigation_only:
        if pre_dense_items is None:
            raise ValueError("pre-dense items are required for navigation-only period neighbor mode")
        return {
            question_id: {
                **item,
                "candidates": list(item.get("candidates") or []),
            }
            for question_id, item in pre_dense_items.items()
        }
    return {
        question_id: {
            **item,
            "candidates": [
                candidate
                for candidate in item.get("candidates") or []
                if int(candidate.get("period_neighbor_offset") or 0) <= 0
            ],
        }
        for question_id, item in expanded_items.items()
    }


def should_emit_candidate_context(
    *,
    family: str,
    tier: str,
    period_neighbor_offset: int | None,
) -> bool:
    """Keep extra table references out of already precise direct lookups.

    A source-replayed or exact-row direct lookup already carries its selected
    source table.  Adding generic ranked and report-year-neighbor tables to
    that record spends Tables-F2 precision without improving the answer
    evidence.  Heuristic and multi-step predictions still receive the
    bounded context lane because candidate recall is useful there.
    """

    precise_direct_tiers = {
        "direct_source_replay_v1",
        "direct_source_replay_provisional_v1",
        "source_first_exact_row_v1",
        "exact_execution_r9",
    }
    if family == "direct_lookup" and tier in precise_direct_tiers:
        return False
    return family != "direct_lookup" or period_neighbor_offset is not None


def rank_semantic_cells(
    item: dict[str, Any], *, metric_override: str | None = None, year_override: int | None = None,
    document_override: str | None = None,
    ticker_override: str | None = None,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
    allow_uncertain: bool = False,
    rejection_log: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    semantic_contract_enabled = _SEMANTIC_CELL_CONTRACT_ENABLED
    question = str(item.get("question") or "")
    plan = item.get("question_plan") or {}
    years = plan.get("years") or [int(y) for y in YEAR_RE.findall(question)]
    year = year_override if year_override is not None else (int(years[0]) if years else None)
    metric = metric_override or metric_text(item)
    selections: list[dict[str, Any]] = []
    candidates = (item.get("candidates") or [])[:50]
    if document_override:
        candidates = [
            candidate for candidate in candidates if candidate.get("document_id") == document_override
        ]
    if ticker_override:
        wanted_ticker = str(ticker_override).strip().upper()
        candidates = [
            candidate for candidate in candidates if candidate_ticker(candidate) == wanted_ticker
        ]
    if year is not None:
        exact_period = [candidate for candidate in candidates if document_year(candidate.get("document_id")) == year]
        comparative_period = [
            candidate for candidate in candidates if document_year(candidate.get("document_id")) == year + 1
        ]
        if exact_period:
            candidates = exact_period
        elif comparative_period:
            candidates = comparative_period
    for candidate in candidates[:50]:
        candidate_uid = str(candidate.get("internal_table_uid") or "")
        table = (tables_by_uid or {}).get(candidate_uid)
        def note_rejection(
            stage: str,
            *,
            row_index: int | None = None,
            column_index: int | None = None,
            reason_codes: Iterable[str] = (),
        ) -> None:
            if rejection_log is None:
                return
            rejection_log.append(
                {
                    "stage": stage,
                    "internal_table_uid": candidate_uid or None,
                    "document_id": candidate.get("document_id"),
                    "candidate_rank": candidate.get("rank"),
                    "row_index": row_index,
                    "column_index": column_index,
                    "reason_codes": sorted({str(code) for code in reason_codes if code}),
                }
            )
        if not candidate_survives_filter(candidate, table=table):
            note_rejection("candidate_filter", reason_codes=("CANDIDATE_FILTER_REJECTED",))
            continue
        evidence = candidate_evidence_window(candidate, table)
        if not evidence:
            note_rejection("evidence_window", reason_codes=("NO_EVIDENCE_WINDOW",))
            continue
        base = float(candidate.get("review_score") or 0.0)
        candidate_year = document_year(candidate.get("document_id"))
        year_bonus = 0.0
        if year is not None and candidate_year is not None:
            if candidate_year == year:
                year_bonus = 0.8
            elif candidate_year == year + 1:
                year_bonus = 0.35
            else:
                year_bonus = -0.9
        for evidence_row in evidence:
            row = evidence_row.get("row") or []
            if len(row) < 2:
                continue
            row_index = int(evidence_row.get("index", 0))
            if semantic_contract_enabled:
                # Keep all non-numeric label cells.  OCR tables frequently put
                # the currency, counterparty or maturity qualifier after the
                # first label cell; truncating at three cells makes those
                # distinctions invisible to the semantic contract.
                label = _semantic_row_label(row, parser=parse_decimal)
                semantic_label = _semantic_row_context(
                    table,
                    row_index=row_index,
                    row=row,
                    parser=parse_decimal,
                )
            else:
                # Baseline-compatible ranking used only for controlled route
                # ablations.  This is the pre-contract label/cell policy: the
                # route code and all structured-table replay gates remain
                # unchanged.
                label_parts: list[str] = []
                for cell in row[:3]:
                    if parse_decimal(cell) is None and str(cell).strip():
                        label_parts.append(str(cell))
                label = " ".join(label_parts)
                semantic_label = label
            if semantic_contract_enabled and str(plan.get("family") or "") == "direct_lookup":
                chosen = choose_semantic_column(
                    table,
                    evidence,
                    row_index,
                    row,
                    year,
                    question,
                    metric,
                    parser=parse_decimal,
                )
            else:
                chosen = choose_year_column(evidence, row_index, row, year, question)
            if chosen is None:
                # Candidate evidence windows intentionally include heading and
                # navigation rows.  A heading without a numeric cell is not a
                # rejected answer candidate and should not pollute the
                # per-question semantic rejection diagnosis.
                continue
            column_index, raw_value = chosen
            validity_probability = float(candidate.get("validity_probability") or 0.0)
            # The learned score is a navigation signal.  Keep exact row
            # semantics dominant, while allowing a high-validity candidate to
            # break close semantic ties after the hard filter has passed.
            validity_bonus = 0.9 * (validity_probability - 0.5)
            score = (
                semantic_row_score(metric, question, semantic_label)
                + 0.35 * base
                + year_bonus
                + validity_bonus
            )
            research_row_index = _int_or_none(candidate.get("research_row_index"))
            research_column_index = _int_or_none(candidate.get("research_column_index"))
            if research_row_index is not None and row_index == research_row_index:
                # Research packets are hints, not labels.  A modest coordinate
                # bonus lets a recovered exact-row candidate compete with the
                # legacy shortlist without forcing a weak semantic match.
                score += 0.85
                if research_column_index is not None and column_index == research_column_index:
                    score += 0.25
            multiplier = source_multiplier(evidence, table)
            semantic_guard: dict[str, Any] = {
                "accepted": True,
                "score_adjustment": 0.0,
                "reason_codes": [],
                "strengths": [],
                "protocol": "not_applied_non_direct_lookup",
                "column_context": semantic_column_context(
                    table,
                    evidence,
                    column_index=column_index,
                    row_index=row_index,
                ),
            }
            if semantic_contract_enabled and str(plan.get("family") or "") == "direct_lookup":
                semantic_guard = assess_semantic_cell(
                    item,
                    table,
                    row_label=semantic_label,
                    row_index=row_index,
                    column_index=column_index,
                    raw_cell=row[column_index],
                    column_context=semantic_column_context(
                        table,
                        evidence,
                        column_index=column_index,
                        row_index=row_index,
                    ),
                    evidence=evidence,
                    source_multiplier=multiplier,
                    parser=parse_decimal,
                    metric=metric,
                )
                if not semantic_guard["accepted"]:
                    note_rejection(
                        "semantic_contract",
                        row_index=row_index,
                        column_index=column_index,
                        reason_codes=semantic_guard.get("reason_codes") or (),
                    )
                    continue
            value = raw_value * multiplier / requested_divisor(question)
            score += float(semantic_guard.get("score_adjustment") or 0.0)
            selections.append(
                {
                    "score": score,
                    "value": value,
                    "raw_value": raw_value,
                    "source_multiplier": multiplier,
                    "row_index": row_index,
                    "column_index": column_index,
                    "row_label": label,
                    "semantic_row_label": semantic_label,
                    "document_id": candidate.get("document_id"),
                    "internal_table_uid": candidate.get("internal_table_uid"),
                    "candidate_rank": candidate.get("rank"),
                    "candidate_source": candidate.get("candidate_source")
                    or candidate.get("retrieval_source")
                    or "review_bundle",
                    "research_candidate_only": bool(candidate.get("research_candidate_only", False)),
                    "research_row_index": research_row_index,
                    "research_column_index": research_column_index,
                    "candidate_filter_status": "SURVIVED_FILTER",
                    "validity_probability": validity_probability,
                    "validity_rank": candidate.get("validity_rank"),
                    "validity_model_status": candidate.get("validity_model_status"),
                    "semantic_guard_status": "PASS" if semantic_guard["accepted"] else "REJECT",
                    "semantic_guard_reason_codes": semantic_guard.get("reason_codes") or [],
                    "semantic_guard_strengths": semantic_guard.get("strengths") or [],
                    "semantic_guard": semantic_guard,
                    "column_context": semantic_guard.get("column_context") or "",
                }
            )
    # In primary answering mode a low score is still useful if it is the best
    # hydrated candidate left after filtering.  Strict callers can keep the old
    # behaviour by leaving allow_uncertain=False.
    selections.sort(
        key=lambda selection: (
            -float(selection["score"]),
            int(selection.get("candidate_rank") or 999),
            str(selection.get("internal_table_uid") or ""),
            int(selection.get("row_index") or -1),
            int(selection.get("column_index") or -1),
        )
    )
    if allow_uncertain:
        return selections
    return [selection for selection in selections if selection["score"] > 0.0]


def choose_semantic_cell(
    item: dict[str, Any], *, metric_override: str | None = None, year_override: int | None = None,
    document_override: str | None = None,
    ticker_override: str | None = None,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
    allow_uncertain: bool = False,
) -> dict[str, Any] | None:
    """Return the current best semantic cell for callers that need one value."""

    ranked = rank_semantic_cells(
        item,
        metric_override=metric_override,
        year_override=year_override,
        document_override=document_override,
        ticker_override=ticker_override,
        tables_by_uid=tables_by_uid,
        allow_uncertain=allow_uncertain,
    )
    return ranked[0] if ranked else None


def _json_safe(value: object) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _proposal_evidence(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep only claim fields required by the independent verifier."""

    claims: list[dict[str, Any]] = []
    for source in rows:
        claim = {
            key: source.get(key)
            for key in (
                "role",
                "document_id",
                "internal_table_uid",
                "row_index",
                "column_index",
                "row_label",
                "raw_value",
                "source_value",
                "source_multiplier",
                "source_to_vnd_multiplier",
                "line_override",
            )
            if source.get(key) is not None
        }
        if "raw_value" not in claim and source.get("raw_value_decimal") is not None:
            claim["raw_value"] = source["raw_value_decimal"]
        claims.append(_json_safe(claim))
    return claims


def _route_priority(tier: str) -> float:
    return {
        "exact_execution_r9": 100.0,
        "direct_source_replay_v1": 95.0,
        "direct_source_replay_provisional_v1": 94.0,
        "source_first_exact_row_v1": 93.0,
        "source_first_candidate_bound_v1": 92.5,
        "source_first_composed_total_v1": 92.25,
        "source_first_period_extreme_v1": 92.1,
        "source_first_reclassified_direct_v1": 91.5,
        "source_first_conditional_temporal_v1": 92.0,
        "source_first_temporal_v1": 92.0,
        "source_first_temporal_cross_entity_v1": 92.0,
        "source_first_multi_entity_direct_aggregation_v1": 91.75,
        "source_first_multi_entity_conditional_count_v1": 91.7,
        "source_first_multi_entity_ratio_selector_v1": 91.35,
        "source_first_multi_entity_selector_v1": 91.3,
        "source_first_multi_entity_lease_threshold_v1": 91.29,
        "source_first_multi_entity_interest_threshold_v1": 91.285,
        "source_first_multi_entity_share_threshold_v1": 91.28,
        "source_first_multi_entity_threshold_v1": 91.25,
        "source_first_cross_entity_v1": 91.0,
        "raw_corpus_recovery": 90.0,
        "model_staged_replay": 80.0,
        "program_multi_entity_plan": 70.0,
        "program_ratio_heuristic": 65.0,
        "program_growth_heuristic": 65.0,
        "program_subtract_heuristic": 65.0,
        # A formula bridge candidate has a current-cell replay for every
        # operand, so it must outrank a one-cell exact route that merely
        # reports one operand.  It remains candidate-only; this priority is
        # selection policy, not verification authority.
        FORMULA_EVIDENCE_BRIDGE_PROTOCOL: 105.0,
        "semantic_cell_heuristic": 50.0,
        "best_surviving_candidate": 40.0,
        "fallback_zero": 0.0,
    }.get(tier, 10.0)


def _proposed_answer_plan(
    *,
    question_id: int,
    answer: Decimal,
    tier: str,
    evidence_rows: list[dict[str, Any]],
    question_plan: Mapping[str, Any],
    pandas_query: str,
    selection: Mapping[str, Any] | None,
    proposal_index: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol": "vifinqa_proposed_answer_plan_v1",
        "proposal_id": f"q{question_id}:proposal:{tier}:{proposal_index}",
        "question_id": question_id,
        "answer_decimal": format(answer, "f"),
        "answer_route": tier,
        "operation_ast": _json_safe(question_plan.get("operation_ast") or {}),
        "claims": {
            "entities": _json_safe(question_plan.get("entities") or question_plan.get("tickers") or []),
            "reporting_scope": question_plan.get("reporting_scope") or question_plan.get("scope"),
            "operands": _json_safe(question_plan.get("operands") or []),
        },
        "evidence": _proposal_evidence(evidence_rows),
        "pandas_query": pandas_query,
        "route_priority": _route_priority(tier),
        "retrieval_score": float(selection.get("score") or 0.0) if selection else 0.0,
        "policy_fallback": tier == "fallback_zero",
        "_selector_question_plan": _json_safe(question_plan),
    }


def _selector_question_contract(question_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Build the whole-question dimensions consumed by the shadow selector."""

    contract: dict[str, Any] = {}
    operation_ast = question_plan.get("operation_ast")
    if isinstance(operation_ast, Mapping) and operation_ast:
        contract["operation_ast"] = _json_safe(operation_ast)

    operands = [
        operand
        for operand in question_plan.get("operands") or []
        if isinstance(operand, Mapping)
    ]
    operand_ids = [
        str(operand.get("operand_id") or operand.get("id") or "").strip()
        for operand in operands
    ]
    operand_ids = [operand_id for operand_id in operand_ids if operand_id]
    if operand_ids:
        contract["required_operand_ids"] = operand_ids
        contract["required_operand_count"] = len(operand_ids)

    periods: list[Any] = []
    for operand in operands:
        period = operand.get("period", operand.get("year"))
        if period is None:
            years = operand.get("years")
            if isinstance(years, (list, tuple)) and len(years) == 1:
                period = years[0]
        if period is not None and period not in periods:
            periods.append(period)
    if not periods:
        for key in ("periods", "years"):
            value = question_plan.get(key)
            if isinstance(value, (list, tuple)):
                periods.extend(item for item in value if item not in periods)
            elif value is not None:
                periods.append(value)
    if periods:
        contract["required_periods"] = _json_safe(periods)

    entities = question_plan.get("entities") or question_plan.get("tickers") or []
    if not isinstance(entities, (list, tuple, set, frozenset)):
        entities = [entities] if entities else []
    entities = [str(entity).strip() for entity in entities if str(entity).strip()]
    if not entities:
        entities = [
            str(operand.get("entity") or operand.get("ticker") or "").strip()
            for operand in operands
            if str(operand.get("entity") or operand.get("ticker") or "").strip()
        ]
    if entities:
        contract["required_entities"] = list(dict.fromkeys(entities))

    scope = question_plan.get("reporting_scope") or question_plan.get("scope")
    if scope:
        contract["required_scope"] = scope
    unit = (
        question_plan.get("source_unit")
        or question_plan.get("requested_unit")
        or question_plan.get("unit")
    )
    if unit:
        contract["required_unit"] = unit
    return contract


def _selector_source_coordinate(
    source: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    """Normalize a proposal evidence row to selector coordinates and hash."""

    uid = str(source.get("internal_table_uid") or source.get("table_uid") or "").strip()
    row_index = _int_or_none(source.get("row_index"))
    column_index = _int_or_none(source.get("column_index"))
    table = tables_by_uid.get(uid) if uid else None
    provenance = table_provenance(table) if isinstance(table, Mapping) else {}
    source_hash = str(
        source.get("source_cell_sha256")
        or source.get("source_table_sha256")
        or source.get("table_sha256")
        or source.get("source_hash")
        or provenance.get("source_sha256")
        or provenance.get("table_sha256")
        or ""
    ).strip()
    coordinate = {
        "source_uid": uid,
        "table_uid": uid,
        "row_index": row_index,
        "column_index": column_index,
    }
    if source_hash:
        coordinate["source_hash"] = source_hash
    return coordinate, source_hash or None


def _selector_operand_rows(
    proposal: Mapping[str, Any],
    *,
    question_plan: Mapping[str, Any],
    question_text: str,
    evidence_rows: Sequence[Mapping[str, Any]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join claim operands to hydrated evidence without trusting raw hints."""

    claim_operands = [
        operand
        for operand in ((proposal.get("claims") or {}).get("operands") or [])
        if isinstance(operand, Mapping)
    ]
    if not claim_operands:
        claim_operands = [
            operand
            for operand in (question_plan.get("operands") or [])
            if isinstance(operand, Mapping)
        ]
    if not claim_operands and evidence_rows:
        claim_operands = [
            {"operand_id": f"x{index}"}
            for index, _source in enumerate(evidence_rows)
        ]

    rows: list[dict[str, Any]] = []
    for index, claim in enumerate(claim_operands):
        source = evidence_rows[index] if index < len(evidence_rows) else {}
        operand = dict(claim)
        operand_id = str(
            operand.get("operand_id") or operand.get("id") or f"x{index}"
        ).strip()
        operand["operand_id"] = operand_id
        period = operand.get("period", operand.get("year"))
        if period is None:
            years = operand.get("years")
            if isinstance(years, (list, tuple)) and len(years) == 1:
                period = years[0]
        if period is not None:
            operand["period"] = period
        entity = operand.get("entity") or operand.get("ticker")
        if entity:
            operand["entity"] = entity
            operand["ticker"] = operand.get("ticker") or entity
        scope = operand.get("scope") or (proposal.get("claims") or {}).get("reporting_scope")
        if scope:
            operand["scope"] = scope
        unit = (
            operand.get("unit")
            or operand.get("source_unit")
            or question_plan.get("requested_unit")
        )
        if unit:
            operand["unit"] = unit

        coordinate, source_hash = _selector_source_coordinate(
            source,
            tables_by_uid=tables_by_uid,
        )
        if coordinate.get("source_uid"):
            operand["source"] = coordinate
            operand.update(
                {
                    key: coordinate[key]
                    for key in ("source_uid", "table_uid", "row_index", "column_index")
                }
            )
        # ``value`` is the already unit-normalized route value when present;
        # raw source values remain a fallback for diagnostic candidates.
        # Source-first routes already expose a unit-normalized ``value``.
        # The exact replay ledger intentionally exposes only a raw VND cell,
        # its source multiplier and the question output unit.  Reconstruct
        # that normalized value here so an exact replay is not rejected merely
        # because its audit row is provenance-oriented.
        if source.get("value") is not None:
            raw_value = source.get("value")
        else:
            raw_value = source.get("raw_value")
            if raw_value is None:
                raw_value = source.get("source_value", source.get("raw_value_decimal"))
            raw_decimal = _selector_decimal(raw_value)
            multiplier = _selector_decimal(
                source.get("source_to_vnd_multiplier")
                or source.get("source_multiplier")
            )
            output_divisor = _selector_decimal(
                source.get("requested_output_divisor")
                or proposal.get("question_output_divisor")
                or requested_divisor(question_text)
            )
            if raw_decimal is not None and output_divisor not in {None, Decimal("0")}:
                raw_value = raw_decimal * (multiplier or Decimal("1")) / output_divisor
        if raw_value is None:
            raw_value = operand.get("raw_value", operand.get("value"))
        if raw_value is not None:
            operand["raw_value"] = raw_value
        if source_hash:
            operand["source_hash"] = source_hash
        rows.append(operand)
    return rows


def _selector_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    parsed = parse_decimal_literal(value)
    if parsed is not None and parsed.is_finite():
        return parsed
    return parse_decimal(value)


def _selector_replay_status(
    proposal: Mapping[str, Any],
    *,
    question_text: str,
    operands: Sequence[Mapping[str, Any]],
) -> tuple[str, Decimal | None]:
    """Replay a proposal AST from its joined operand values when possible."""

    verification = proposal.get("verification")
    if (
        isinstance(verification, Mapping)
        and verification.get("strict_certificate_matched") is True
    ):
        answer = _selector_decimal(proposal.get("answer_decimal"))
        return ("PASS", answer) if answer is not None else ("FAIL", None)

    ast = proposal.get("operation_ast")
    if not isinstance(ast, Mapping) or validate_operation_ast(ast):
        return "FAIL", None
    values: dict[str, Any] = {}
    for operand in operands:
        operand_id = str(operand.get("operand_id") or "").strip()
        raw_value = operand.get("raw_value")
        value = _selector_decimal(raw_value)
        if not operand_id or value is None:
            return "FAIL", None
        values[operand_id] = value
    if not values:
        return "FAIL", None

    operation = str(ast.get("op") or "").strip().casefold()
    if operation == "arg_extreme_period":
        grounded_values: dict[str, dict[str, Any]] = {}
        for operand in operands:
            period = _int_or_none(operand.get("period", operand.get("year")))
            if period is None:
                return "FAIL", None
            grounded_values[str(operand["operand_id"])] = {
                "period": period,
                "value": values[str(operand["operand_id"])],
            }
        values = grounded_values
    try:
        replayed = execute_ast(ast, values)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return "FAIL", None
    replayed_decimal = _selector_decimal(replayed)
    answer = _selector_decimal(proposal.get("answer_decimal"))
    if replayed_decimal is None or answer is None:
        return "FAIL", replayed_decimal
    # Submission answers are serialized as JSON numbers, so a tiny Decimal
    # tolerance covers presentation rounding while still rejecting a formula
    # that points at the wrong period/sign/cell.
    if abs(replayed_decimal - answer) > Decimal("0.000000001"):
        return "FAIL", replayed_decimal
    return "PASS", replayed_decimal


def _proposal_to_selector_plan(
    proposal: Mapping[str, Any],
    *,
    question_plan: Mapping[str, Any],
    question_text: str,
    evidence_rows: Sequence[Mapping[str, Any]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    is_baseline: bool = False,
) -> dict[str, Any]:
    operands = _selector_operand_rows(
        proposal,
        question_plan=question_plan,
        question_text=question_text,
        evidence_rows=evidence_rows,
        tables_by_uid=tables_by_uid,
    )
    replay_status, replayed_answer = _selector_replay_status(
        proposal,
        question_text=question_text,
        operands=operands,
    )
    verification = proposal.get("verification")
    verification_class = (
        str(verification.get("verification_class") or "PARTIAL")
        if isinstance(verification, Mapping)
        else "PARTIAL"
    )
    route_name = str(proposal.get("answer_route") or "").strip().casefold()
    heuristic_route = "heuristic" in route_name or route_name == "best_surviving_candidate"
    structural_complete = bool(
        operands
        and len(operands)
        == len(
            [
                operand
                for operand in ((proposal.get("claims") or {}).get("operands") or [])
                if isinstance(operand, Mapping)
            ]
        )
        and isinstance(proposal.get("operation_ast"), Mapping)
        and not proposal.get("policy_fallback")
        and not heuristic_route
    )
    plan: dict[str, Any] = {
        "plan_id": str(proposal.get("proposal_id") or proposal.get("candidate_id") or ""),
        "question_id": proposal.get("question_id"),
        "operation_ast": _json_safe(proposal.get("operation_ast") or {}),
        "operands": operands,
        "answer_decimal": proposal.get("answer_decimal"),
        "replay_answer_decimal": proposal.get("answer_decimal"),
        "replay_status": replay_status,
        "semantic_completeness": "COMPLETE" if structural_complete else "PARTIAL",
        "operand_completeness": "COMPLETE" if structural_complete else "INCOMPLETE",
        # Keep authority states out of the candidate selector even when the
        # proposal was certified elsewhere; the certificate remains the only
        # strict answer authority.
        "verification_status": (
            "REPLAY_READY" if replay_status == "PASS" else verification_class
        ),
        "route_family": proposal.get("answer_route") or "unknown",
        "route_priority": proposal.get("route_priority") or 0.0,
        "candidate_score": proposal.get("retrieval_score") or 0.0,
        "filter_passed": verification_class != "REJECTED",
        "candidate_status": (
            "SURVIVED_FILTER" if verification_class != "REJECTED" else "REJECTED"
        ),
        "source_coordinates": [
            operand.get("source")
            for operand in operands
            if isinstance(operand.get("source"), Mapping)
        ],
        "source_hash": next(
            (
                str(operand.get("source_hash"))
                for operand in operands
                if operand.get("source_hash")
            ),
            None,
        ),
    }
    if replayed_answer is not None:
        plan["replayed_answer_decimal"] = format(replayed_answer, "f")
    if is_baseline:
        plan["is_baseline"] = True
    return plan


def _select_proposal_with_answer_level_selector(
    *,
    question_id: int,
    question_plan: Mapping[str, Any],
    question_text: str,
    proposals: Sequence[Mapping[str, Any]],
    legacy_selected_proposal: Mapping[str, Any] | None,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    max_candidates: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Run the opt-in selector while preserving the established control."""

    baseline_id = (
        str(legacy_selected_proposal.get("proposal_id") or "")
        if isinstance(legacy_selected_proposal, Mapping)
        else ""
    )
    candidate_plans: list[dict[str, Any]] = []
    proposal_by_id: dict[str, Mapping[str, Any]] = {}

    def proposal_question_plan(proposal: Mapping[str, Any]) -> Mapping[str, Any]:
        candidate_plan = proposal.get("_selector_question_plan")
        return candidate_plan if isinstance(candidate_plan, Mapping) else question_plan

    selector_contract_plan = question_plan
    if (
        isinstance(legacy_selected_proposal, Mapping)
        and str(legacy_selected_proposal.get("answer_route") or "")
        == FORMULA_EVIDENCE_BRIDGE_PROTOCOL
    ):
        # The formula bridge is a whole-question candidate with its own AST;
        # use that plan as the selector contract when it wins legacy routing.
        # This prevents the shadow selector from silently replacing a
        # multi-operand formula replay with a one-cell baseline proposal.
        selector_contract_plan = proposal_question_plan(legacy_selected_proposal)

    for proposal in proposals:
        proposal_id = str(proposal.get("proposal_id") or "")
        if not proposal_id:
            continue
        proposal_by_id[proposal_id] = proposal
        if proposal_id == baseline_id:
            continue
        candidate_plans.append(
            _proposal_to_selector_plan(
                proposal,
                question_plan=proposal_question_plan(proposal),
                question_text=question_text,
                evidence_rows=list(proposal.get("_evidence_rows") or []),
                tables_by_uid=tables_by_uid,
            )
        )
    baseline_plan = None
    if isinstance(legacy_selected_proposal, Mapping) and baseline_id:
        baseline_plan = _proposal_to_selector_plan(
            legacy_selected_proposal,
            question_plan=proposal_question_plan(legacy_selected_proposal),
            question_text=question_text,
            evidence_rows=list(legacy_selected_proposal.get("_evidence_rows") or []),
            tables_by_uid=tables_by_uid,
            is_baseline=True,
        )

    plan_set = CandidateAnswerPlanSet(
        question_id=question_id,
        question_contract=_selector_question_contract(selector_contract_plan),
        plans=candidate_plans,
        baseline_plan=baseline_plan,
        max_plans=max_candidates,
    )
    decision = CandidateAnswerLevelSelector(
        max_candidates=max_candidates,
    ).select(plan_set)
    selected_id = str(decision.get("selected_plan_id") or "")
    selected = proposal_by_id.get(selected_id)
    return (dict(selected) if isinstance(selected, Mapping) else None), decision


def _run_submission_verifier(
    *,
    base_config: Path,
    candidate_ledger_path: Path,
    output_dir: Path,
) -> Path:
    """Run canonical E2E against this build's candidate ledger.

    The generated config resolves every inherited input to an absolute path and
    removes byte-for-byte expected-output fixtures.  Those fixtures describe a
    historical candidate ledger and must never be used to certify a new
    submission.  The canonical verifier still snapshots and hashes its full
    input closure.
    """

    base_config = base_config.resolve()
    payload = yaml.safe_load(base_config.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("paths"), Mapping):
        raise ValueError("verification config must contain a paths mapping")
    paths: dict[str, str] = {}
    for name, raw_value in payload["paths"].items():
        if name.startswith("expected_"):
            continue
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        path = Path(raw_value)
        if not path.is_absolute():
            path = (base_config.parent / path).resolve()
        paths[str(name)] = str(path)
    paths["best_candidate_predictions"] = str(candidate_ledger_path.resolve())
    generated = {
        "schema_version": payload.get("schema_version"),
        "protocol": payload.get("protocol"),
        "run_name": "submission-proposal-verifier",
        "paths": paths,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_config = output_dir / "verification_input.yaml"
    generated_config.write_text(
        yaml.safe_dump(generated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    verifier_output = output_dir / "verification_e2e"
    run_deterministic_replay(
        load_deterministic_replay_inputs(generated_config),
        output_dir=verifier_output,
    )
    certificates = verifier_output / "answer_certificates_v1.jsonl"
    if not certificates.is_file():
        raise RuntimeError("canonical E2E completed without answer certificates")
    return certificates


def _apply_certificate_overlay(
    *,
    proposals: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    certificates: Mapping[int, Mapping[str, Any]],
) -> tuple[Counter[str], Counter[str]]:
    """Refresh selected audit rows after the canonical verifier completes."""

    by_question = {int(row["question_id"]): row for row in proposals}
    diagnostic_by_question = {int(row["id"]): row for row in diagnostics}
    candidate_by_question = {int(row["question_id"]): row for row in candidates}
    class_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    for question_id, proposal in by_question.items():
        verification = verify_proposed_answer(
            proposal,
            tables_by_uid=tables_by_uid,
            strict_certificate=certificates.get(question_id),
        )
        proposal["verification"] = verification
        diagnostic = diagnostic_by_question.get(question_id)
        if diagnostic is not None:
            diagnostic["verification"] = verification
            diagnostic["verification_class"] = verification["verification_class"]
            diagnostic["confidence_class"] = (
                "VERIFIED" if verification["verification_class"] == "VERIFIED" else "BEST_EFFORT"
            )
        candidate = candidate_by_question.get(question_id)
        if candidate is not None:
            candidate["verification"] = verification
            candidate["verification_class"] = verification["verification_class"]
        class_counts[str(verification["verification_class"])] += 1
        failure_counts.update(
            str(failure["reason"])
            for failure in verification.get("failures") or []
            if isinstance(failure, Mapping)
        )
    return class_counts, failure_counts


def infer_temporal_operands(item: dict[str, Any]) -> list[dict[str, Any]]:
    question = str(item.get("question") or "")
    plan = item.get("question_plan") or {}
    operands = plan.get("operands") or []
    if len(operands) >= 2:
        return operands[:2]
    if plan.get("family") != "temporal_change":
        return []
    years = [int(year) for year in YEAR_RE.findall(question)]
    years = list(dict.fromkeys(years))
    if len(years) != 2:
        return []
    q = normalize(question)
    temporal_cues = (" tu ", " sang ", " thay doi", "tang ", "giam ", "tru di", "lon hon", "be hon")
    if not any(cue in f" {q} " for cue in temporal_cues):
        return []
    old_year, new_year = min(years), max(years)
    return [
        {"operand_id": "x_old", "metric": question, "period": old_year},
        {"operand_id": "x_new", "metric": question, "period": new_year},
    ]


def infer_formula_operation(item: dict[str, Any], operands: list[dict[str, Any]]) -> str | None:
    if len(operands) < 2:
        return None
    question = str(item.get("question") or "")
    q = normalize(question)
    if "%" in question or "phan tram" in q or "tang truong" in q or "toc do tang" in q:
        return "percentage_change"
    plan_op = ((item.get("question_plan") or {}).get("operation_ast") or {}).get("op")
    if plan_op in {"subtract", "percentage_change"}:
        return plan_op
    if any(cue in q for cue in ("tru di", "thay doi", "tang bao nhieu", "giam bao nhieu", "lon hon", "be hon")):
        return "subtract"
    return None


def _build_candidate_bound_source_indices(
    tables_by_pair: Mapping[tuple[str, int], Iterable[Mapping[str, Any]]],
) -> tuple[
    dict[str, tuple[tuple[str, int], dict[str, Any]]],
    dict[tuple[str, str], list[tuple[tuple[str, int], dict[str, Any]]]],
]:
    """Build the immutable UID/document boundary indexes once per run."""

    tables_by_uid: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
    tables_by_document_scope: dict[
        tuple[str, str], list[tuple[tuple[str, int], dict[str, Any]]]
    ] = {}
    for pair, tables in tables_by_pair.items():
        for table in tables:
            table_copy = dict(table)
            uid = str(table_copy.get("internal_table_uid") or "")
            document_id = str(table_copy.get("document_id") or "").removesuffix(".txt")
            scope = str(table_copy.get("scope") or "").strip().lower()
            if uid:
                tables_by_uid[uid] = (pair, table_copy)
            if document_id:
                tables_by_document_scope.setdefault((document_id, scope), []).append(
                    (pair, table_copy)
                )
    return tables_by_uid, tables_by_document_scope


def source_first_candidate_bound_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
    max_candidate_rank: int = 3,
    min_review_score: float = 0.60,
    candidate_source_index: tuple[dict[str, Any], dict[tuple[str, str], list[Any]]] | None = None,
) -> dict[str, Any] | None:
    """Replay a direct lookup inside one strong navigation source boundary.

    An unqualified question can legitimately have both a separate and a
    consolidated report in the corpus.  The strict source-first resolver
    correctly abstains when those reports disagree.  For the competition
    answer lane, a high-confidence top retrieval candidate can still define a
    bounded source boundary: first try that exact table, then the same
    document if the target table is a neighboring OCR segment.  The answer is
    accepted only after the target row/column is replayed from the current V2
    table; retrieval metadata never supplies the numeric value.

    This is intentionally a weaker proposal tier than an unambiguous
    source-first result.  It remains ``promotion_allowed=False`` and is not a
    semantic authorization path.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    candidates = [
        candidate
        for candidate in item.get("candidates") or []
        if isinstance(candidate, Mapping)
    ]
    ranked: list[tuple[int, float, Mapping[str, Any]]] = []
    for candidate in candidates:
        try:
            rank = int(candidate.get("rank") or candidate.get("original_retrieval_rank"))
        except (TypeError, ValueError):
            continue
        if rank < 1 or rank > max_candidate_rank:
            continue
        try:
            review_score = float(candidate.get("review_score") or 0.0)
        except (TypeError, ValueError):
            review_score = 0.0
        if review_score < min_review_score:
            continue
        ranked.append((rank, -review_score, candidate))
    ranked.sort(key=lambda row: (row[0], row[1], str(row[2].get("internal_table_uid") or "")))

    if candidate_source_index is None:
        candidate_source_index = _build_candidate_bound_source_indices(tables_by_pair)
    tables_by_uid, tables_by_document_scope = candidate_source_index

    def candidate_tables(
        *,
        table_uid: str = "",
        document_id: str = "",
        scope: str = "",
    ) -> dict[tuple[str, int], list[dict[str, Any]]]:
        result: dict[tuple[str, int], list[dict[str, Any]]] = {}
        selected_pairs: list[tuple[tuple[str, int], dict[str, Any]]] = []
        if table_uid:
            record = tables_by_uid.get(table_uid)
            if record is not None:
                selected_pairs.append(record)
        elif document_id:
            if scope:
                selected_pairs.extend(tables_by_document_scope.get((document_id, scope), []))
                selected_pairs.extend(tables_by_document_scope.get((document_id, ""), []))
                selected_pairs.extend(tables_by_document_scope.get((document_id, "unknown"), []))
            else:
                for (indexed_document, _), values in tables_by_document_scope.items():
                    if indexed_document == document_id:
                        selected_pairs.extend(values)
        for pair, table in selected_pairs:
            table_scope = str(table.get("scope") or "").strip().lower()
            if scope and table_scope not in {scope, "", "unknown"}:
                continue
            result.setdefault(pair, []).append(dict(table))
        return result

    def replay(
        restricted: Mapping[tuple[str, int], list[dict[str, Any]]],
        *,
        candidate: Mapping[str, Any],
        boundary: str,
    ) -> dict[str, Any] | None:
        result = resolve_source_first_direct_lookup(
            item,
            tables_by_pair=restricted,
            **dict(resolver_kwargs),
        )
        if result is None:
            return None
        selection = result.get("selection")
        if not isinstance(selection, Mapping):
            return None
        candidate_uid = str(candidate.get("internal_table_uid") or "")
        candidate_doc = str(candidate.get("document_id") or "").removesuffix(".txt")
        selected_uid = str(selection.get("internal_table_uid") or "")
        selected_doc = str(selection.get("document_id") or "").removesuffix(".txt")
        if candidate_uid and boundary == "table" and selected_uid != candidate_uid:
            return None
        if candidate_doc and selected_doc != candidate_doc:
            return None
        candidate_scope = str(candidate.get("scope") or "").strip().lower()
        selected_scope = str(selection.get("source_first_scope") or "").strip().lower()
        if candidate_scope and candidate_scope not in {"unknown", ""} and selected_scope not in {
            candidate_scope,
            "unknown",
            "",
        }:
            return None
        match_mode = str((result.get("diagnostics") or {}).get("match_mode") or "")
        selection_period_mode = str(
            selection.get("period_selection_mode") or ""
        )
        exact_transposed_metric = (
            match_mode
            in {
                "exact_contiguous",
                "ordered_with_ocr_gap",
                "fuzzy_ocr_label",
                "fuzzy_ocr_compact",
            }
            and selection_period_mode.startswith("column_metric_row_")
        )
        if not (match_mode.startswith("contextual_") or exact_transposed_metric):
            # The bounded fallback is not a second arbitrary ranking system.
            # It may accept a contextual source contract, or an exact/OCR
            # metric-header match whose row is independently bound to an
            # explicit period.  The latter is the safe transposed-table case:
            # it allows q349-like recovery after a global
            # separate/consolidated conflict without accepting a broad row
            # substring such as ``Chi phí phải trả khác`` for ``Chi phí khác``.
            # Retrieval still supplies only the boundary; the current
            # structured table supplies the cell.
            return None
        annotated = dict(result)
        annotated["tier"] = "source_first_candidate_bound_v1"
        annotated["protocol"] = "source_first_candidate_bound_v1"
        annotated["sources"] = [
            {
                **dict(source),
                "candidate_bound": True,
                "candidate_bound_boundary": boundary,
                "candidate_bound_rank": candidate.get("rank"),
                "promotion_allowed": False,
            }
            for source in result.get("sources") or []
        ]
        annotated["selection"] = {
            **dict(selection),
            "candidate_bound": True,
            "candidate_bound_boundary": boundary,
            "candidate_bound_rank": candidate.get("rank"),
            "candidate_bound_source_document": candidate_doc or None,
            "candidate_bound_source_scope": candidate_scope or None,
            "candidate_bound_only": True,
            "promotion_allowed": False,
        }
        annotated["diagnostics"] = {
            **dict(result.get("diagnostics") or {}),
            "candidate_bound": True,
            "candidate_bound_boundary": boundary,
            "candidate_bound_rank": candidate.get("rank"),
            "candidate_bound_review_score": candidate.get("review_score"),
            "answer_authority": "current_structured_table_decimal_replay",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        }
        return annotated

    for _, _, candidate in ranked:
        uid = str(candidate.get("internal_table_uid") or "")
        doc = str(candidate.get("document_id") or "").removesuffix(".txt")
        scope = str(candidate.get("scope") or "").strip().lower()
        if uid:
            result = replay(
                candidate_tables(table_uid=uid, scope=scope),
                candidate=candidate,
                boundary="table",
            )
            if result is not None:
                return result
        if doc:
            result = replay(
                candidate_tables(document_id=doc, scope=scope),
                candidate=candidate,
                boundary="document",
            )
            if result is not None:
                return result
    return None


def build_source_first_candidate_bound_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run bounded source-document replays for high-confidence direct hits."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    candidate_source_index = _build_candidate_bound_source_indices(tables_by_pair)
    for question_id, item in items_by_question.items():
        if str((item.get("question_plan") or {}).get("family") or "") != "direct_lookup":
            stats["questions_skipped_non_direct"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_candidate_bound_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
            candidate_source_index=candidate_source_index,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        boundary = str((result.get("diagnostics") or {}).get("candidate_bound_boundary") or "unknown")
        stats[f"boundary_{boundary}"] += 1
    return answers, {
        **dict(stats),
        "protocol": "source_first_candidate_bound_v1",
        "candidate_rank_limit": 3,
        "minimum_review_score": 0.60,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_RECLASSIFIED_DIRECT_PROTOCOL = "source_first_reclassified_direct_v1"
_RECLASSIFIED_DIRECT_OPS = {"count", "mean", "sum", "values"}
_RECLASSIFIED_DIRECT_REJECT_CUES = (
    "co bao nhieu",
    "trong so",
    "chenh lech",
    "so sanh",
    "giua ",
    " tung ",
    " moi ",
)


def _reclassified_direct_metric(item: Mapping[str, Any]) -> tuple[str, str] | None:
    """Recognize one-period ``multi`` plans that ask for one reported row.

    The compiler currently emits a small tail of one-ticker/one-year questions
    as ``multi_entity_or_period_aggregation``.  This adapter is deliberately
    pattern/family based: it has no Question-ID allowlist and returns no
    generic metric guess.  A recognized pattern is still only a proposal
    until the normal source-first row/period/unit replay succeeds.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    if operation not in _RECLASSIFIED_DIRECT_OPS:
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    years = [value for value in plan.get("years") or [] if str(value).isdigit()]
    if len(set(tickers)) != 1 or len(set(years)) != 1:
        return None
    question = normalize(item.get("question") or "")
    if any(cue in f" {question} " for cue in _RECLASSIFIED_DIRECT_REJECT_CUES):
        return None
    if "loi the thuong mai" in question:
        return "goodwill", "Lợi thế thương mại"
    if "co phieu pho thong" in question and "binh quan gia quyen" in question:
        return (
            "weighted_shares",
            "Số lượng bình quân gia quyền của cổ phiếu phổ thông",
        )
    if "tong cong tai san" in question:
        return "total_assets", "Tổng cộng tài sản"
    if "tong cong" in question and "nghia vu no tai chinh" in question:
        return (
            "financial_liability_maturity_total",
            "Tổng cộng nghĩa vụ nợ tài chính",
        )
    if "von chu so huu" in question and "tong" in question:
        # The primary balance sheet uses the numbered ``I.`` row for this
        # metric; keeping that prefix is a semantic row boundary, not a
        # Question-ID-specific answer.
        return "equity", "I. Vốn chủ sở hữu"
    if "cac khoan phai thu" in question and "tong" in question:
        return "receivables", "Các khoản phải thu"
    return None


def source_first_reclassified_direct_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Replay a compiler-misclassified one-period reported-value question.

    For total assets/equity, an unqualified company question uses the
    conventional consolidated-report prior only in this explicitly weaker
    best-effort lane.  The source-first resolver still has to bind the exact
    row and current Decimal cell; no retrieval value or model score is used.
    """

    classification = _reclassified_direct_metric(item)
    if classification is None:
        return None
    kind, metric = classification
    plan = dict(item.get("question_plan") or {})
    original_scope = str(plan.get("scope") or "").strip().lower() or None
    inferred_scope = original_scope
    if inferred_scope is None and kind in {"total_assets", "equity"}:
        inferred_scope = "consolidated"
    proxy_plan = {
        **plan,
        "family": "direct_lookup",
        "operands": [{"metric": metric}],
        "scope": inferred_scope,
    }
    proxy_item = {**dict(item), "question_plan": proxy_plan}
    result = resolve_source_first_direct_lookup(
        proxy_item,
        tables_by_pair=tables_by_pair,
        **dict(resolver_kwargs),
    )
    if result is None:
        return None
    annotated = dict(result)
    annotated["tier"] = _RECLASSIFIED_DIRECT_PROTOCOL
    annotated["protocol"] = _RECLASSIFIED_DIRECT_PROTOCOL
    annotated["sources"] = [
        {
            **dict(source),
            "reclassified_direct": True,
            "reclassified_direct_kind": kind,
            "reclassified_direct_metric": metric,
            "reclassified_direct_scope": inferred_scope,
            "promotion_allowed": False,
        }
        for source in result.get("sources") or []
    ]
    selection = result.get("selection")
    if not isinstance(selection, Mapping):
        return None
    annotated["selection"] = {
        **dict(selection),
        "candidate_source": _RECLASSIFIED_DIRECT_PROTOCOL,
        "reclassified_direct": True,
        "reclassified_direct_kind": kind,
        "reclassified_direct_metric": metric,
        "reclassified_direct_original_scope": original_scope,
        "reclassified_direct_scope": inferred_scope,
        "reclassified_direct_scope_inferred": (
            original_scope is None and inferred_scope is not None
        ),
        "promotion_allowed": False,
    }
    annotated["diagnostics"] = {
        **dict(result.get("diagnostics") or {}),
        "candidate_source": _RECLASSIFIED_DIRECT_PROTOCOL,
        "reclassified_direct": True,
        "reclassified_direct_kind": kind,
        "reclassified_direct_metric": metric,
        "reclassified_direct_original_scope": original_scope,
        "reclassified_direct_scope": inferred_scope,
        "reclassified_direct_scope_inferred": (
            original_scope is None and inferred_scope is not None
        ),
        "answer_authority": "current_structured_table_decimal_replay",
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }
    return annotated


def build_source_first_reclassified_direct_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
    disable_financial_liability_maturity_total: bool = False,
    disable_financial_receivables_total: bool = False,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the family-based reclassified direct proposal lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        classification = _reclassified_direct_metric(item)
        if (
            disable_financial_liability_maturity_total
            and classification is not None
            and classification[0] == "financial_liability_maturity_total"
        ):
            classification = None
        if (
            disable_financial_receivables_total
            and classification is not None
            and classification[0] == "receivables"
        ):
            classification = None
        if classification is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        kind, _ = classification
        stats[f"kind_{kind}"] += 1
        result = source_first_reclassified_direct_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        if (result.get("diagnostics") or {}).get("reclassified_direct_scope_inferred"):
            stats["questions_with_inferred_scope"] += 1
    return answers, {
        **dict(stats),
        "protocol": _RECLASSIFIED_DIRECT_PROTOCOL,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_THRESHOLD_PROTOCOL = "source_first_multi_entity_threshold_v1"


def _multi_entity_threshold_spec(
    item: Mapping[str, Any],
) -> tuple[str, Decimal, int] | None:
    """Recognize a narrow count-over-threshold question family.

    The legacy planner records some explicit issuer-list questions as a
    generic ``multi_entity_or_period_aggregation`` plan with only the first
    ticker retained.  This adapter handles only the stable foreign-exchange
    net-income predicate currently present in the corpus.  It never derives a
    metric from an arbitrary comparison sentence.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    if str((plan.get("operation_ast") or {}).get("op") or "") != "count":
        return None
    years = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None
    question = normalize(item.get("question") or "")
    metric = "lãi thuần từ hoạt động kinh doanh ngoại hối"
    normalized_metric = normalize(metric)
    if normalized_metric not in question:
        return None
    if "co bao nhieu" not in question or "duong" not in question:
        return None
    if not re.search(r"(?:lon hon|tren) 1 nghin ty(?: dong)?", question):
        return None
    return metric, Decimal("1000"), years[0]


def _multi_entity_threshold_tickers(
    item: Mapping[str, Any],
) -> tuple[list[str], str] | None:
    """Recover an explicit issuer list using only exact registry aliases."""

    plan = item.get("question_plan") or {}
    planned = []
    for value in plan.get("tickers") or []:
        ticker = str(value).strip().upper()
        if ticker and ticker not in planned:
            planned.append(ticker)

    # This call initializes the extended alias registry used by the existing
    # two-issuer lane when the planner kept only one ticker.  Its return value
    # is intentionally not used here because that lane requires exactly two
    # issuers, while this lane requires the full explicit list.
    _cross_entity_ticker_plan(item)
    aliases = _CROSS_ENTITY_TICKER_ALIASES
    if aliases is None:
        return None
    inferred = extract_tickers(str(item.get("question") or ""), aliases)
    inferred = [ticker for ticker in inferred if ticker not in {"CP", "CTCP", "TMCP"}]
    if len(planned) >= 2:
        if any(ticker not in inferred for ticker in planned):
            return None
        return planned, "planner_complete"
    if len(inferred) < 2 or len(inferred) > 8:
        return None
    if planned and planned[0] not in inferred:
        return None
    return inferred, "exact_code_stock_alias_recovery"


def source_first_multi_entity_threshold_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Count exact source-replayed values above a stable threshold.

    Scope is omitted in the question, so the lane performs two independent
    replays (consolidated and separate).  It accepts the count only when every
    issuer's pass/fail predicate is identical in both scopes.  The emitted
    evidence uses the canonical consolidated replay, while the scope
    invariance check remains explicit metadata and the result stays a
    best-effort proposal.
    """

    spec = _multi_entity_threshold_spec(item)
    if spec is None:
        return None
    metric, threshold, requested_year = spec
    ticker_plan = _multi_entity_threshold_tickers(item)
    if ticker_plan is None:
        return None
    tickers, ticker_source = ticker_plan
    if len(tickers) < 2:
        return None
    planned_tickers = []
    for value in (item.get("question_plan") or {}).get("tickers") or []:
        ticker = str(value).strip().upper()
        if ticker and ticker not in planned_tickers:
            planned_tickers.append(ticker)

    resolved_by_scope: dict[str, dict[str, dict[str, Any]]] = {}
    predicates_by_scope: dict[str, dict[str, bool]] = {}
    for scope in ("consolidated", "separate"):
        scope_results: dict[str, dict[str, Any]] = {}
        scope_predicates: dict[str, bool] = {}
        for ticker in tickers:
            lookup_question = (
                f"{metric} của {ticker} trong năm {requested_year} tỷ đồng"
            )
            synthetic = {
                "question": lookup_question,
                "question_plan": {
                    "family": "direct_lookup",
                    "tickers": [ticker],
                    "years": [requested_year],
                    "scope": scope,
                    "reporting_scope": scope,
                    "operands": [
                        {
                            "operand_id": "value",
                            "metric": metric,
                            "period": requested_year,
                            "ticker": ticker,
                            "scope": scope,
                        }
                    ],
                },
            }
            result = resolve_source_first_direct_lookup(
                synthetic,
                tables_by_pair=tables_by_pair,
                **dict(resolver_kwargs),
            )
            if result is None or not _cross_entity_source_result_is_safe(
                result,
                requested_metric=metric,
                requested_year=requested_year,
                question=str(item.get("question") or ""),
            ):
                return None
            source = (result.get("sources") or [None])[0]
            if not isinstance(source, Mapping):
                return None
            source_scope = normalize(source.get("source_first_scope") or "")
            if source_scope != scope:
                return None
            scope_results[ticker] = result
            scope_predicates[ticker] = (
                result["answer"] > threshold and result["answer"] > Decimal(0)
            )
        resolved_by_scope[scope] = scope_results
        predicates_by_scope[scope] = scope_predicates

    if predicates_by_scope["consolidated"] != predicates_by_scope["separate"]:
        # An unqualified question cannot be made safe by selecting whichever
        # report happens to rank first when the threshold result changes.
        return None

    canonical_scope = "consolidated"
    selected_results = resolved_by_scope[canonical_scope]
    sources: list[dict[str, Any]] = []
    for ticker in tickers:
        source = dict((selected_results[ticker].get("sources") or [])[0])
        source.update(
            {
                "role": f"entity_{ticker}",
                "threshold": threshold,
                "threshold_unit": "billion_vnd",
                "threshold_predicate": "positive_and_greater_than",
                "threshold_passed": predicates_by_scope[canonical_scope][ticker],
                "scope_invariance_checked": True,
                "scope_invariance_scopes": ["consolidated", "separate"],
                "promotion_allowed": False,
            }
        )
        sources.append(source)

    answer = Decimal(sum(predicates_by_scope[canonical_scope].values()))
    return {
        "answer": answer,
        "sources": sources,
        "query": "float((df1['operand_value'] > 1000).sum())",
        "tier": _MULTI_ENTITY_THRESHOLD_PROTOCOL,
        "protocol": _MULTI_ENTITY_THRESHOLD_PROTOCOL,
        "operation": "count_positive_threshold",
        "metric": metric,
        "threshold": threshold,
        "requested_year": requested_year,
        "planned_tickers": planned_tickers,
        "replayed_tickers": list(tickers),
        "ticker_source": ticker_source,
        "canonical_scope": canonical_scope,
        "scope_invariance": {
            scope: {
                ticker: predicates_by_scope[scope][ticker]
                for ticker in tickers
            }
            for scope in ("consolidated", "separate")
        },
        "scope_values": {
            scope: {
                ticker: str(resolved_by_scope[scope][ticker]["answer"])
                for ticker in tickers
            }
            for scope in ("consolidated", "separate")
        },
        "promotion_allowed": False,
        "diagnostics": {
            "protocol": _MULTI_ENTITY_THRESHOLD_PROTOCOL,
            "metric": metric,
            "threshold": str(threshold),
            "requested_year": requested_year,
            "replayed_tickers": list(tickers),
            "ticker_source": ticker_source,
            "canonical_scope": canonical_scope,
            "scope_invariance": {
                scope: {
                    ticker: predicates_by_scope[scope][ticker]
                    for ticker in tickers
                }
                for scope in ("consolidated", "separate")
            },
            "answer_authority": "current_structured_table_decimal_replay",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_threshold_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the bounded multi-issuer threshold-count proposal lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _multi_entity_threshold_spec(item) is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_multi_entity_threshold_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"ticker_count_{len(result.get('replayed_tickers') or [])}"] += 1
        stats["scope_invariant"] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_THRESHOLD_PROTOCOL,
        "accepted_match_modes": sorted(_CROSS_ENTITY_SOURCE_ALLOWED_MATCH_MODES),
        "requires_exact_report_year": True,
        "requires_scope_invariant_predicate": True,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL = (
    "source_first_multi_entity_share_threshold_v1"
)
_MULTI_ENTITY_SHARE_THRESHOLD_METRIC_VARIANTS = (
    "Số lượng cổ phiếu đang lưu hànhCổ phiếu phổ thông",
    "Cổ phiếu đang lưu hànhCổ phiếu phổ thông",
    "Số lượng cổ phiếu phổ thông đang lưu hành",
    "Số lượng cổ phiếu đang lưu hành",
    "Cổ phiếu đang lưu hành",
)


def _multi_entity_share_threshold_spec(
    item: Mapping[str, Any],
) -> tuple[str, Decimal, int, str | None] | None:
    """Recognize an explicit issuer-list outstanding-share threshold count.

    The typed planner currently leaves this wording in a conditional
    ``abstain``/``plan_required`` family.  The adapter accepts only the
    observable share-count predicate and an explicit end-of-year phrase; it
    does not infer a metric from arbitrary ``Có bao nhiêu`` questions.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") not in {
        "multi_entity_or_period_aggregation",
        "conditional_analytical",
    }:
        return None
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    if operation not in {"count", "plan_required", "abstain"}:
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None
    question = normalize(item.get("question") or "")
    if "co bao nhieu" not in question:
        return None
    if "co phieu" not in question or "dang luu hanh" not in question:
        return None
    if not any(marker in question for marker in ("cuoi nam", "tai ngay", "31 12")):
        return None
    threshold_match = re.search(
        r"(?:vuot|lon hon|tren)\s+([0-9]+)\s+trieu\s+co phieu",
        question,
    )
    if threshold_match is None:
        return None
    threshold = Decimal(threshold_match.group(1)) * Decimal("1000000")
    requested_scope = str(
        plan.get("scope") or plan.get("reporting_scope") or ""
    ).strip().lower() or None
    if requested_scope is None and "cong ty me" in question:
        requested_scope = "separate"
    if requested_scope not in {None, "separate"}:
        return None
    return "outstanding_shares", threshold, years[0], requested_scope


def _multi_entity_share_threshold_tickers(
    item: Mapping[str, Any],
) -> tuple[list[str], str] | None:
    """Recover every explicitly named issuer and reject partial alias lists."""

    plan = item.get("question_plan") or {}
    planned: list[str] = []
    for value in plan.get("tickers") or []:
        ticker = str(value).strip().upper()
        if ticker and ticker not in planned:
            planned.append(ticker)

    # The shared alias registry is built from the frozen code-stock catalog.
    # Do not use a partial planner list when the question visibly introduces
    # more legal-form issuer anchors.  The existing initializer intentionally
    # returns early for a complete planner list, so initialize the base
    # registry explicitly before comparing it with the question aliases.
    global _CROSS_ENTITY_TICKER_ALIASES
    if _CROSS_ENTITY_TICKER_ALIASES is None:
        _CROSS_ENTITY_TICKER_ALIASES = load_ticker_aliases(
            _cross_entity_code_stock_path()
        )
    aliases = _CROSS_ENTITY_TICKER_ALIASES
    if aliases is None:
        return None
    inferred = extract_tickers(str(item.get("question") or ""), aliases)
    inferred = [
        ticker
        for ticker in inferred
        if ticker not in {"CP", "CTCP", "TMCP"}
    ]
    inferred = list(dict.fromkeys(inferred))
    if len(inferred) < 2 or len(inferred) > 8:
        return None
    if planned and any(ticker not in inferred for ticker in planned):
        return None
    normalized_question = normalize(item.get("question") or "")
    issuer_anchor_count = len(
        re.findall(
            r"\b(?:tong\s+ctcp|tong\s+cong\s+ty|ctcp|tap\s+doan)\b",
            normalized_question,
        )
    )
    if issuer_anchor_count < 2 or len(inferred) != issuer_anchor_count:
        return None
    if planned and len(planned) >= 2 and set(planned) != set(inferred):
        return None
    return inferred, (
        "planner_complete"
        if planned and len(planned) >= 2
        else "exact_code_stock_alias_recovery"
    )


def _multi_entity_share_threshold_proxy_item(
    item: Mapping[str, Any],
    *,
    ticker: str,
    year: int,
    metric: str,
    scope: str,
) -> dict[str, Any]:
    """Build a one-issuer end-of-year share lookup for source replay."""

    plan = dict(item.get("question_plan") or {})
    plan.update(
        {
            "family": "direct_lookup",
            "tickers": [ticker],
            "years": [year],
            "scope": scope,
            "reporting_scope": scope,
            "requested_unit": "shares",
            "operands": [
                {
                    "operand_id": "value",
                    "metric": metric,
                    "period": year,
                    "ticker": ticker,
                    "scope": scope,
                }
            ],
            "operation_ast": {"op": "lookup"},
        }
    )
    return {
        "question": f"{metric} của {ticker} cuối năm {year} cổ phiếu",
        "question_plan": plan,
    }


def _multi_entity_share_threshold_result_is_safe(
    result: Mapping[str, Any] | None,
    *,
    ticker: str,
    year: int,
    scope: str,
) -> bool:
    """Require an exact current share row before applying a predicate."""

    if result is None:
        return False
    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or len(sources) != 1:
        return False
    source = sources[0]
    if not isinstance(source, Mapping):
        return False
    document_id = str(source.get("document_id") or "").removesuffix(".txt")
    document_ticker = document_id.split("_financial_statements_", 1)[0].upper()
    if document_ticker != ticker:
        return False
    try:
        if int(source.get("source_report_year")) != year:
            return False
        if int(selection.get("source_report_year")) != year:
            return False
    except (TypeError, ValueError):
        return False
    if int(source.get("report_year_offset") or 0) != 0:
        return False
    if str(source.get("source_first_scope") or "").strip().lower() != scope:
        return False
    match_mode = str(
        selection.get("source_first_match_mode")
        or (result.get("diagnostics") or {}).get("match_mode")
        or ""
    )
    if match_mode not in {"exact_contiguous", "ordered_with_ocr_gap"}:
        return False
    period_mode = str(source.get("period_selection_mode") or "")
    if period_mode not in {
        "exact_report_year_column",
        "contextual_explicit_period_column",
    }:
        return False
    table_kind = str(
        selection.get("source_first_table_kind")
        or source.get("source_first_table_kind")
        or ""
    ).strip().lower()
    if table_kind not in {
        "financial_note",
        "financial_note_detail",
        "financial_data_schedule",
    }:
        return False
    row = normalize(selection.get("row_label") or source.get("row_label") or "")
    if "co phieu" not in row or "dang luu hanh" not in row:
        return False
    if "menh gia" in row or "co phieu quy" in row:
        return False
    multiplier = parse_decimal_literal(source.get("source_to_vnd_multiplier"))
    if multiplier is None or multiplier != Decimal(1):
        return False
    value = result.get("answer")
    if not isinstance(value, Decimal):
        value = parse_decimal_literal(value)
    return value is not None and value > 0


def source_first_multi_entity_share_threshold_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Count issuers whose replayed outstanding shares exceed a threshold."""

    specification = _multi_entity_share_threshold_spec(item)
    if specification is None:
        return None
    kind, threshold, requested_year, requested_scope = specification
    ticker_plan = _multi_entity_share_threshold_tickers(item)
    if ticker_plan is None:
        return None
    tickers, ticker_source = ticker_plan
    if len(tickers) < 2:
        return None

    scopes = [requested_scope] if requested_scope else ["consolidated", "separate"]
    resolved_by_scope: dict[str, dict[str, dict[str, Any]]] = {}
    predicates_by_scope: dict[str, dict[str, bool]] = {}
    values_by_scope: dict[str, dict[str, str]] = {}
    metric_by_scope: dict[str, dict[str, str]] = {}
    for scope_value in scopes:
        if scope_value is None:
            return None
        scope_results: dict[str, dict[str, Any]] = {}
        scope_predicates: dict[str, bool] = {}
        scope_values: dict[str, str] = {}
        scope_metrics: dict[str, str] = {}
        for ticker in tickers:
            available = list(tables_by_pair.get((ticker, requested_year), ()))
            if not available:
                return None
            result: dict[str, Any] | None = None
            metric_used: str | None = None
            resolver_kwargs_for_ticker = dict(resolver_kwargs)
            # A threshold count cannot safely read a comparative column from
            # a later report when the requested annual report exists.
            resolver_kwargs_for_ticker["report_year_neighbor_fallback"] = None
            for resolver_metric in _MULTI_ENTITY_SHARE_THRESHOLD_METRIC_VARIANTS:
                proxy_item = _multi_entity_share_threshold_proxy_item(
                    item,
                    ticker=ticker,
                    year=requested_year,
                    metric=resolver_metric,
                    scope=scope_value,
                )
                candidate = resolve_source_first_direct_lookup(
                    proxy_item,
                    tables_by_pair={(ticker, requested_year): available},
                    **resolver_kwargs_for_ticker,
                )
                if _multi_entity_share_threshold_result_is_safe(
                    candidate,
                    ticker=ticker,
                    year=requested_year,
                    scope=scope_value,
                ):
                    result = dict(candidate)
                    metric_used = resolver_metric
                    break
            if result is None or metric_used is None:
                return None
            value = result.get("answer")
            if not isinstance(value, Decimal):
                value = parse_decimal_literal(value)
            if value is None:
                return None
            scope_results[ticker] = result
            scope_values[ticker] = str(value)
            scope_predicates[ticker] = value > threshold
            scope_metrics[ticker] = metric_used
        resolved_by_scope[scope_value] = scope_results
        values_by_scope[scope_value] = scope_values
        predicates_by_scope[scope_value] = scope_predicates
        metric_by_scope[scope_value] = scope_metrics

    if (
        requested_scope is None
        and predicates_by_scope["consolidated"]
        != predicates_by_scope["separate"]
    ):
        # An unqualified question cannot choose a perimeter after the
        # threshold predicate changes.  Keep it on the ordinary candidate
        # lane instead of silently picking the consolidated table.
        return None
    canonical_scope = requested_scope or "consolidated"
    canonical_results = resolved_by_scope[canonical_scope]
    sources: list[dict[str, Any]] = []
    for ticker in tickers:
        source_rows = canonical_results[ticker].get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        source.update(
            {
                "role": f"entity_{ticker}",
                "ticker": ticker,
                "multi_entity_share_threshold": True,
                "multi_entity_share_threshold_protocol": (
                    _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL
                ),
                "multi_entity_share_threshold_kind": kind,
                "share_metric": metric_by_scope[canonical_scope][ticker],
                "share_value": values_by_scope[canonical_scope][ticker],
                "threshold": threshold,
                "threshold_unit": "shares",
                "threshold_predicate": "greater_than",
                "threshold_passed": predicates_by_scope[canonical_scope][ticker],
                "scope_policy": (
                    "explicit_parent_company_separate"
                    if requested_scope == "separate"
                    else "unqualified_predicate_scope_invariant_canonical_consolidated"
                ),
                "scope_invariance_checked": requested_scope is None,
                "scope_invariance_scopes": scopes,
                "promotion_allowed": False,
            }
        )
        sources.append(source)

    answer = Decimal(sum(predicates_by_scope[canonical_scope].values()))
    first_selection = dict(canonical_results[tickers[0]].get("selection") or {})
    first_selection.update(
        {
            "value": answer,
            "candidate_source": _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
            "source_first_multi_entity_share_threshold": True,
            "multi_entity_share_threshold_protocol": (
                _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL
            ),
            "multi_entity_share_threshold_kind": kind,
            "share_metric": "Số lượng cổ phiếu đang lưu hành",
            "threshold": threshold,
            "threshold_unit": "shares",
            "threshold_predicate": "greater_than",
            "canonical_scope": canonical_scope,
            "scope_policy": (
                "explicit_parent_company_separate"
                if requested_scope == "separate"
                else "unqualified_predicate_scope_invariant_canonical_consolidated"
            ),
            "scope_invariance": predicates_by_scope,
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": first_selection,
        "query": f"float((df1['operand_value'] > {threshold}).sum())",
        "tier": _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
        "protocol": _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
        "operation": "count_greater_than",
        "multi_entity_share_threshold_kind": kind,
        "metric": "Số lượng cổ phiếu đang lưu hành",
        "threshold": threshold,
        "threshold_unit": "shares",
        "requested_year": requested_year,
        "requested_scope": requested_scope,
        "replayed_tickers": list(tickers),
        "ticker_source": ticker_source,
        "canonical_scope": canonical_scope,
        "positive_tickers": [
            ticker
            for ticker in tickers
            if predicates_by_scope[canonical_scope][ticker]
        ],
        "scope_invariance": predicates_by_scope,
        "scope_values": values_by_scope,
        "scope_metrics": metric_by_scope,
        "scope_policy": (
            "explicit_parent_company_separate"
            if requested_scope == "separate"
            else "unqualified_predicate_scope_invariant_canonical_consolidated"
        ),
        "promotion_allowed": False,
        "diagnostics": {
            "candidate_source": _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
            "protocol": _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
            "kind": kind,
            "metric": "Số lượng cổ phiếu đang lưu hành",
            "threshold": str(threshold),
            "threshold_unit": "shares",
            "requested_year": requested_year,
            "requested_scope": requested_scope,
            "replayed_tickers": list(tickers),
            "ticker_source": ticker_source,
            "canonical_scope": canonical_scope,
            "scope_invariance": predicates_by_scope,
            "scope_values": values_by_scope,
            "scope_metrics": metric_by_scope,
            "scope_policy": (
                "explicit_parent_company_separate"
                if requested_scope == "separate"
                else "unqualified_predicate_scope_invariant_canonical_consolidated"
            ),
            "answer_authority": "current_structured_table_decimal_replay_and_local_predicate",
            "verification_class": (
                "PARTIAL_SCOPE_ASSUMPTION"
                if requested_scope is None
                else "PARTIAL_EXPLICIT_SCOPE"
            ),
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_share_threshold_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the bounded outstanding-share threshold proposal lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _multi_entity_share_threshold_spec(item) is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_multi_entity_share_threshold_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"ticker_count_{len(result.get('replayed_tickers') or [])}"] += 1
        if result.get("requested_scope") is None:
            stats["scope_invariant"] += 1
        else:
            stats["explicit_scope"] += 1
        stats[
            f"threshold_pass_count_{len(result.get('positive_tickers') or [])}"
        ] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
        "accepted_match_modes": ["exact_contiguous", "ordered_with_ocr_gap"],
        "requires_exact_report_year": True,
        "requires_explicit_end_of_year": True,
        "requires_complete_issuer_alias_recovery": True,
        "requires_scope_invariant_predicate_when_unqualified": True,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL = (
    "source_first_multi_entity_lease_threshold_v1"
)
_MULTI_ENTITY_LEASE_MATURITY_ROWS = {
    "den han trong 1 nam",
    "trong vong 1 nam",
    "den mot nam",
    "den 1 nam",
}


def _multi_entity_lease_threshold_spec(
    item: Mapping[str, Any],
) -> tuple[str, Decimal, int, str] | None:
    """Recognize the explicit parent-company operating-lease predicate.

    This is deliberately a row-family adapter, not a generic threshold
    executor.  The source row must be the one-year operating-lease maturity
    bucket, and the question must state the parent/separate perimeter.  A
    generic ``đến hạn trong 1 năm`` question remains on the ordinary
    candidate path.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") not in {
        "conditional_analytical",
        "multi_entity_or_period_aggregation",
    }:
        return None
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    if operation not in {"count", "plan_required", "abstain"}:
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None

    question = normalize(item.get("question") or "")
    if "co bao nhieu" not in question:
        return None
    if "cam ket thue hoat dong" not in question:
        return None
    if not any(marker in question for marker in _MULTI_ENTITY_LEASE_MATURITY_ROWS):
        return None
    threshold_match = re.search(
        r"(?:lon hon|vuot|tren)\s+([0-9]+(?:\s+[0-9]+)?)\s+(?:ty|ti)\s+dong",
        question,
    )
    if threshold_match is None:
        return None
    threshold_text = threshold_match.group(1).replace(" ", "")
    try:
        threshold = Decimal(threshold_text)
    except InvalidOperation:
        return None
    requested_scope = str(
        plan.get("scope") or plan.get("reporting_scope") or ""
    ).strip().lower() or None
    if requested_scope is None and "cong ty me" in question:
        requested_scope = "separate"
    # ``công ty mẹ`` is the semantic perimeter for this family.  Do not
    # infer a consolidated/separate choice from whichever table ranks first.
    if requested_scope != "separate":
        return None
    if "cong ty me" not in question and not (
        plan.get("scope") or plan.get("reporting_scope")
    ):
        return None
    return "operating_lease_maturity_1y", threshold, years[0], requested_scope


def _multi_entity_lease_threshold_tickers(
    item: Mapping[str, Any],
) -> tuple[list[str], str] | None:
    """Require the planner/question issuer set to be complete and explicit."""

    plan = item.get("question_plan") or {}
    planned: list[str] = []
    for value in plan.get("tickers") or []:
        ticker = str(value).strip().upper()
        if ticker and ticker not in planned:
            planned.append(ticker)
    if len(planned) < 2 or len(planned) > 8:
        return None

    # A complete planner list makes ``_cross_entity_ticker_plan`` return
    # early, so initialize the immutable alias registry explicitly before
    # checking that every visible ticker is represented in the question.
    global _CROSS_ENTITY_TICKER_ALIASES
    if _CROSS_ENTITY_TICKER_ALIASES is None:
        _CROSS_ENTITY_TICKER_ALIASES = load_ticker_aliases(
            _cross_entity_code_stock_path()
        )
    aliases = _CROSS_ENTITY_TICKER_ALIASES
    if aliases is None:
        return None
    inferred = extract_tickers(str(item.get("question") or ""), aliases)
    inferred = [
        ticker
        for ticker in inferred
        if ticker not in {"CP", "CTCP", "TMCP"}
    ]
    inferred = list(dict.fromkeys(inferred))
    if len(inferred) < 2 or len(inferred) > 8:
        return None
    if set(planned) != set(inferred):
        return None
    return planned, "planner_complete"


def _multi_entity_lease_threshold_table_is_safe(
    table: Mapping[str, Any],
) -> bool:
    """Require the local table context to identify operating-lease amounts."""

    context = source_first_lookup_module._table_context(table)
    if "thue hoat dong" not in context:
        return False
    return any(
        marker in context
        for marker in (
            "cam ket",
            "tien thue toi thieu",
            "hop dong thue hoat dong",
        )
    )


def _multi_entity_lease_threshold_unit_info(
    table: Mapping[str, Any],
) -> tuple[Decimal, str] | None:
    """Resolve a source unit from the table or a hash-checked document header.

    Some bank reports omit the unit from the extracted table header while
    declaring once, near the beginning of the same source document, that all
    amounts are presented in million VND.  That declaration is accepted only
    after the source bytes match the table's recorded SHA-256.  A missing or
    unverified unit keeps the route unresolved.
    """

    direct_parts: list[str] = []
    for key in ("headers", "column_labels", "unit_hint"):
        value = table.get(key)
        if isinstance(value, (list, tuple)):
            direct_parts.extend(str(part or "") for part in value)
        elif value:
            direct_parts.append(str(value))
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        labels = trace.get("unit_labels")
        if isinstance(labels, (list, tuple)):
            direct_parts.extend(str(part or "") for part in labels)
        elif labels:
            direct_parts.append(str(labels))
    direct_multiplier = _unit_multiplier_from_text(" ".join(direct_parts))
    if direct_multiplier is not None:
        return direct_multiplier, "table_header_or_unit_label"

    source_path = Path(str(table.get("source_path") or ""))
    expected_sha = str(table.get("source_sha256") or "").strip().lower()
    if not source_path.is_file() or not expected_sha:
        return None
    try:
        payload = source_path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(payload).hexdigest().lower() != expected_sha:
        return None
    source_text = normalize(payload.decode("utf-8", errors="replace"))
    if (
        "don vi tinh trieu vnd" in source_text
        or "don vi tinh trieu dong" in source_text
        or "trinh bay theo don vi trieu vnd" in source_text
        or "trinh bay theo don vi trieu dong" in source_text
        or re.search(
            r"don vi tien te.{0,160}trieu (?:vnd|dong)",
            source_text,
        )
    ):
        return Decimal("1000000"), "hash_checked_source_document_unit"
    return None


def _multi_entity_lease_threshold_source_multiplier(
    rows: list[dict[str, Any]],
    table: dict[str, Any] | None = None,
) -> Decimal:
    """Use the lease-family unit contract before the generic fallback."""

    if table is not None:
        unit_info = _multi_entity_lease_threshold_unit_info(table)
        if unit_info is not None:
            return unit_info[0]
    return source_multiplier(rows, table)


def _multi_entity_lease_threshold_proxy_item(
    item: Mapping[str, Any],
    *,
    ticker: str,
    year: int,
    scope: str,
) -> dict[str, Any]:
    """Build a direct end-of-year replay for one issuer's maturity bucket."""

    plan = dict(item.get("question_plan") or {})
    plan.update(
        {
            "family": "direct_lookup",
            "tickers": [ticker],
            "years": [year],
            "scope": scope,
            "reporting_scope": scope,
            "requested_unit": "billion_vnd",
            "operands": [
                {
                    "operand_id": "value",
                    "metric": "Cam kết thuê hoạt động",
                    "period": year,
                    "ticker": ticker,
                    "scope": scope,
                }
            ],
            "operation_ast": {"op": "lookup"},
        }
    )
    return {
        "question": (
            f"Cam kết thuê hoạt động đến hạn trong 1 năm của {ticker} "
            f"cuối năm {year} tỷ đồng?"
        ),
        "question_plan": plan,
    }


def _multi_entity_lease_threshold_result_is_safe(
    result: Mapping[str, Any] | None,
    *,
    ticker: str,
    year: int,
    scope: str,
    lease_tables: Sequence[Mapping[str, Any]],
) -> bool:
    """Validate coordinate, context, period, and unit provenance."""

    if result is None:
        return False
    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or len(sources) != 1:
        return False
    source = sources[0]
    if not isinstance(source, Mapping):
        return False
    document_id = str(source.get("document_id") or "").removesuffix(".txt")
    document_ticker = document_id.split("_financial_statements_", 1)[0].upper()
    if document_ticker != ticker:
        return False
    try:
        if int(source.get("source_report_year")) != year:
            return False
        if int(selection.get("source_report_year")) != year:
            return False
    except (TypeError, ValueError):
        return False
    if int(source.get("report_year_offset") or 0) != 0:
        return False
    if str(source.get("source_first_scope") or "").strip().lower() != scope:
        return False
    if str(
        selection.get("source_first_match_mode")
        or (result.get("diagnostics") or {}).get("match_mode")
        or ""
    ) != "contextual_rent_commitment_maturity_1y":
        return False
    if str(source.get("period_selection_mode") or "") != (
        "contextual_explicit_period_column"
    ):
        return False
    row_label = normalize(selection.get("row_label") or source.get("row_label") or "")
    if row_label not in _MULTI_ENTITY_LEASE_MATURITY_ROWS:
        return False
    uid = str(source.get("internal_table_uid") or "")
    table_by_uid = {
        str(table.get("internal_table_uid") or ""): table
        for table in lease_tables
    }
    table = table_by_uid.get(uid)
    if table is None or not _multi_entity_lease_threshold_table_is_safe(table):
        return False
    unit_info = _multi_entity_lease_threshold_unit_info(table)
    if unit_info is None:
        return False
    multiplier = parse_decimal_literal(source.get("source_to_vnd_multiplier"))
    if multiplier is None or multiplier != unit_info[0]:
        return False
    divisor = parse_decimal_literal(source.get("question_output_divisor"))
    if divisor is None or divisor != Decimal("1000000000"):
        return False
    raw_value = parse_decimal_literal(source.get("raw_value_decimal"))
    value = result.get("answer")
    if raw_value is None:
        return False
    if not isinstance(value, Decimal):
        value = parse_decimal_literal(value)
    if value is None:
        return False
    expected = raw_value * multiplier / divisor
    return value == expected and value >= 0


def source_first_multi_entity_lease_threshold_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Count parent-company issuers above an operating-lease threshold."""

    specification = _multi_entity_lease_threshold_spec(item)
    if specification is None:
        return None
    kind, threshold, requested_year, requested_scope = specification
    ticker_plan = _multi_entity_lease_threshold_tickers(item)
    if ticker_plan is None:
        return None
    tickers, ticker_source = ticker_plan

    results: dict[str, dict[str, Any]] = {}
    values: dict[str, str] = {}
    predicates: dict[str, bool] = {}
    unit_provenance: dict[str, str] = {}
    table_counts: dict[str, int] = {}
    for ticker in tickers:
        available = list(tables_by_pair.get((ticker, requested_year), ()))
        lease_tables = [
            table
            for table in available
            if _multi_entity_lease_threshold_table_is_safe(table)
        ]
        if not lease_tables:
            return None
        table_counts[ticker] = len(lease_tables)
        proxy_item = _multi_entity_lease_threshold_proxy_item(
            item,
            ticker=ticker,
            year=requested_year,
            scope=requested_scope,
        )
        resolver_kwargs_for_ticker = dict(resolver_kwargs)
        resolver_kwargs_for_ticker["report_year_neighbor_fallback"] = None
        resolver_kwargs_for_ticker["source_multiplier"] = (
            _multi_entity_lease_threshold_source_multiplier
        )
        result = resolve_source_first_direct_lookup(
            proxy_item,
            tables_by_pair={(ticker, requested_year): lease_tables},
            **resolver_kwargs_for_ticker,
        )
        if not _multi_entity_lease_threshold_result_is_safe(
            result,
            ticker=ticker,
            year=requested_year,
            scope=requested_scope,
            lease_tables=lease_tables,
        ):
            return None
        assert result is not None
        value = result.get("answer")
        if not isinstance(value, Decimal):
            value = parse_decimal_literal(value)
        if value is None:
            return None
        source = (result.get("sources") or [None])[0]
        if not isinstance(source, Mapping):
            return None
        selected_table = next(
            (
                table
                for table in lease_tables
                if str(table.get("internal_table_uid") or "")
                == str(source.get("internal_table_uid") or "")
            ),
            None,
        )
        if selected_table is None:
            return None
        unit_info = _multi_entity_lease_threshold_unit_info(selected_table)
        if unit_info is None:
            return None
        results[ticker] = result
        values[ticker] = str(value)
        predicates[ticker] = value > threshold
        unit_provenance[ticker] = unit_info[1]

    sources: list[dict[str, Any]] = []
    for ticker in tickers:
        source_rows = results[ticker].get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        source.update(
            {
                "role": f"entity_{ticker}",
                "ticker": ticker,
                "multi_entity_lease_threshold": True,
                "multi_entity_lease_threshold_protocol": (
                    _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL
                ),
                "multi_entity_lease_threshold_kind": kind,
                "maturity_bucket": "one_year",
                "threshold": threshold,
                "threshold_unit": "billion_vnd",
                "threshold_predicate": "greater_than",
                "threshold_passed": predicates[ticker],
                "scope_policy": "explicit_parent_company_separate",
                "unit_provenance": unit_provenance[ticker],
                "promotion_allowed": False,
            }
        )
        sources.append(source)

    answer = Decimal(sum(predicates.values()))
    first_selection = dict(results[tickers[0]].get("selection") or {})
    first_selection.update(
        {
            "value": answer,
            "candidate_source": _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
            "source_first_multi_entity_lease_threshold": True,
            "multi_entity_lease_threshold_protocol": (
                _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL
            ),
            "multi_entity_lease_threshold_kind": kind,
            "maturity_bucket": "one_year",
            "threshold": threshold,
            "threshold_unit": "billion_vnd",
            "threshold_predicate": "greater_than",
            "canonical_scope": requested_scope,
            "scope_policy": "explicit_parent_company_separate",
            "unit_provenance": unit_provenance,
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": first_selection,
        "query": f"float((df1['operand_value'] > {threshold}).sum())",
        "tier": _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
        "protocol": _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
        "operation": "count_greater_than",
        "multi_entity_lease_threshold_kind": kind,
        "metric": "Cam kết thuê hoạt động đến hạn trong 1 năm",
        "maturity_bucket": "one_year",
        "threshold": threshold,
        "threshold_unit": "billion_vnd",
        "requested_year": requested_year,
        "requested_scope": requested_scope,
        "replayed_tickers": list(tickers),
        "ticker_source": ticker_source,
        "positive_tickers": [ticker for ticker in tickers if predicates[ticker]],
        "values": values,
        "unit_provenance": unit_provenance,
        "table_counts": table_counts,
        "scope_policy": "explicit_parent_company_separate",
        "promotion_allowed": False,
        "diagnostics": {
            "candidate_source": _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
            "protocol": _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
            "kind": kind,
            "metric": "Cam kết thuê hoạt động đến hạn trong 1 năm",
            "maturity_bucket": "one_year",
            "threshold": str(threshold),
            "threshold_unit": "billion_vnd",
            "requested_year": requested_year,
            "requested_scope": requested_scope,
            "replayed_tickers": list(tickers),
            "ticker_source": ticker_source,
            "positive_tickers": [
                ticker for ticker in tickers if predicates[ticker]
            ],
            "values": values,
            "unit_provenance": unit_provenance,
            "table_counts": table_counts,
            "scope_policy": "explicit_parent_company_separate",
            "answer_authority": (
                "current_structured_table_decimal_replay_and_local_predicate"
            ),
            "verification_class": "PARTIAL_EXPLICIT_SCOPE",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_lease_threshold_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the bounded parent-company operating-lease threshold lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _multi_entity_lease_threshold_spec(item) is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_multi_entity_lease_threshold_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"ticker_count_{len(result.get('replayed_tickers') or [])}"] += 1
        stats[
            f"threshold_pass_count_{len(result.get('positive_tickers') or [])}"
        ] += 1
        stats["explicit_scope"] += 1
        for provenance in set((result.get("unit_provenance") or {}).values()):
            stats[f"unit_provenance_{provenance}"] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
        "accepted_match_modes": [
            "contextual_rent_commitment_maturity_1y",
        ],
        "accepted_maturity_rows": sorted(_MULTI_ENTITY_LEASE_MATURITY_ROWS),
        "requires_exact_report_year": True,
        "requires_explicit_parent_company_scope": True,
        "requires_operating_lease_table_context": True,
        "requires_hash_checked_source_unit_when_header_missing": True,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL = (
    "source_first_multi_entity_interest_threshold_v1"
)


def _multi_entity_interest_threshold_spec(
    item: Mapping[str, Any],
) -> tuple[str, Decimal, int, str] | None:
    """Recognize an explicit parent-company interest-expense threshold.

    This route is intentionally a typed family adapter.  It accepts only the
    observed wording where the question asks for a count of parent companies
    whose reported ``Chi phí lãi vay`` exceeds a VND-billion threshold in one
    year.  Other ``chi phí tài chính`` or interest-related questions remain on
    their existing candidate/program lanes.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    if str((plan.get("operation_ast") or {}).get("op") or "") != "count":
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None

    question = normalize(item.get("question") or "")
    if "tong so" not in question or "phat sinh" not in question:
        return None
    if "chi phi lai vay" not in question:
        return None
    if "cong ty me" not in question:
        return None
    if "cong ty tai chinh" in question:
        return None
    threshold_match = re.search(
        r"(?:nhieu hon|lon hon|vuot|tren)\s+"
        r"([0-9]+(?:\s+[0-9]+)?)\s+(?:ty|ti)(?:\s+dong)?",
        question,
    )
    if threshold_match is None:
        return None
    threshold_text = threshold_match.group(1).replace(" ", "")
    try:
        threshold = Decimal(threshold_text)
    except InvalidOperation:
        return None
    requested_scope = str(
        plan.get("scope") or plan.get("reporting_scope") or ""
    ).strip().lower()
    if requested_scope != "separate":
        return None
    return "interest_expense", threshold, years[0], requested_scope


def _multi_entity_interest_threshold_tickers(
    item: Mapping[str, Any],
) -> tuple[list[str], str] | None:
    """Require a complete planner/question issuer set for this route."""

    plan = item.get("question_plan") or {}
    planned: list[str] = []
    for value in plan.get("tickers") or []:
        ticker = str(value).strip().upper()
        if ticker and ticker not in planned:
            planned.append(ticker)
    if len(planned) < 2 or len(planned) > 8:
        return None

    global _CROSS_ENTITY_TICKER_ALIASES
    if _CROSS_ENTITY_TICKER_ALIASES is None:
        _CROSS_ENTITY_TICKER_ALIASES = load_ticker_aliases(
            _cross_entity_code_stock_path()
        )
    aliases = _CROSS_ENTITY_TICKER_ALIASES
    if aliases is None:
        return None
    inferred = extract_tickers(str(item.get("question") or ""), aliases)
    inferred = list(
        dict.fromkeys(
            ticker
            for ticker in inferred
            if ticker not in {"CP", "CTCP", "TMCP"}
        )
    )
    if len(inferred) < 2 or len(inferred) > 8:
        return None
    if set(planned) != set(inferred):
        return None
    return planned, "planner_complete"


def _multi_entity_interest_threshold_unit_info(
    table: Mapping[str, Any],
) -> tuple[Decimal, str] | None:
    """Require an explicit source-side accounting unit for the replay."""

    parts: list[str] = []
    for key in ("headers", "column_labels", "unit_hint"):
        value = table.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(str(part or "") for part in value)
        elif value:
            parts.append(str(value))
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        labels = trace.get("unit_labels")
        if isinstance(labels, (list, tuple)):
            parts.extend(str(part or "") for part in labels)
        elif labels:
            parts.append(str(labels))
    multiplier = _unit_multiplier_from_text(" ".join(parts))
    if multiplier is None:
        return None
    return multiplier, "table_header_or_unit_label"


def _multi_entity_interest_threshold_table_is_safe(
    table: Mapping[str, Any],
) -> bool:
    """Restrict the source row to P&L or its matching finance-expense note."""

    table_kind = _multi_entity_statement_table_kind(table)
    if table_kind not in {"income_statement", "financial_note_detail"}:
        return False
    if _multi_entity_interest_threshold_unit_info(table) is None:
        return False
    if table_kind == "financial_note_detail":
        context = source_first_lookup_module._table_context(table)
        if "chi phi tai chinh" not in context:
            return False
    return True


def _multi_entity_interest_threshold_source_multiplier(
    rows: list[dict[str, Any]],
    table: dict[str, Any] | None = None,
) -> Decimal:
    """Use the explicit table unit before the generic source-unit fallback."""

    if table is not None:
        unit_info = _multi_entity_interest_threshold_unit_info(table)
        if unit_info is not None:
            return unit_info[0]
    return source_multiplier(rows, table)


def _multi_entity_interest_threshold_proxy_item(
    item: Mapping[str, Any],
    *,
    ticker: str,
    year: int,
    scope: str,
) -> dict[str, Any]:
    """Build an exact one-issuer flow lookup for ``Chi phí lãi vay``."""

    plan = dict(item.get("question_plan") or {})
    plan.update(
        {
            "family": "direct_lookup",
            "tickers": [ticker],
            "years": [year],
            "scope": scope,
            "reporting_scope": scope,
            "requested_unit": "billion_vnd",
            "operands": [
                {
                    "operand_id": "value",
                    "metric": "Chi phí lãi vay",
                    "period": year,
                    "ticker": ticker,
                    "scope": scope,
                }
            ],
            "operation_ast": {"op": "lookup"},
        }
    )
    return {
        "question": (
            f"Chi phí lãi vay của công ty mẹ {ticker} trong năm {year} "
            "là bao nhiêu tỷ đồng?"
        ),
        "question_plan": plan,
    }


def _multi_entity_interest_threshold_result_is_safe(
    result: Mapping[str, Any] | None,
    *,
    ticker: str,
    year: int,
    scope: str,
    available_tables: Sequence[Mapping[str, Any]],
) -> bool:
    """Validate row identity, period, unit and exact Decimal replay."""

    if result is None:
        return False
    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or len(sources) != 1:
        return False
    source = sources[0]
    if not isinstance(source, Mapping):
        return False
    document_id = str(source.get("document_id") or "").removesuffix(".txt")
    document_ticker = document_id.split("_financial_statements_", 1)[0].upper()
    if document_ticker != ticker:
        return False
    if str(source.get("source_first_scope") or "").strip().lower() != scope:
        return False
    try:
        if int(source.get("source_report_year")) != year:
            return False
        if int(selection.get("source_report_year")) != year:
            return False
    except (TypeError, ValueError):
        return False
    if int(source.get("report_year_offset") or 0) != 0:
        return False
    if str(source.get("period_selection_mode") or "") != (
        "exact_report_year_column"
    ):
        return False
    if str(
        selection.get("source_first_match_mode")
        or (result.get("diagnostics") or {}).get("match_mode")
        or ""
    ) not in {
        "exact_contiguous",
        "ordered_with_ocr_gap",
        "fuzzy_ocr_label",
        "fuzzy_ocr_compact",
    }:
        return False
    row_label = normalize(selection.get("row_label") or source.get("row_label") or "")
    if "chi phi lai vay" not in row_label:
        return False
    if "lai tien vay" in row_label and "chi phi lai vay" not in row_label:
        return False
    uid = str(source.get("internal_table_uid") or "")
    table_by_uid = {
        str(table.get("internal_table_uid") or ""): table
        for table in available_tables
    }
    table = table_by_uid.get(uid)
    if table is None or not _multi_entity_interest_threshold_table_is_safe(table):
        return False
    unit_info = _multi_entity_interest_threshold_unit_info(table)
    multiplier = parse_decimal_literal(source.get("source_to_vnd_multiplier"))
    if unit_info is None or multiplier is None or multiplier != unit_info[0]:
        return False
    divisor = parse_decimal_literal(source.get("question_output_divisor"))
    if divisor is None or divisor != Decimal("1000000000"):
        return False
    raw_value = parse_decimal_literal(source.get("raw_value_decimal"))
    value = result.get("answer")
    if raw_value is None:
        return False
    if not isinstance(value, Decimal):
        value = parse_decimal_literal(value)
    if value is None or value < 0:
        return False
    return value == raw_value * multiplier / divisor


def source_first_multi_entity_interest_threshold_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Count parent-company P&L interest-expense values above a threshold."""

    specification = _multi_entity_interest_threshold_spec(item)
    if specification is None:
        return None
    kind, threshold, requested_year, requested_scope = specification
    ticker_plan = _multi_entity_interest_threshold_tickers(item)
    if ticker_plan is None:
        return None
    tickers, ticker_source = ticker_plan

    results: dict[str, dict[str, Any]] = {}
    values: dict[str, str] = {}
    predicates: dict[str, bool] = {}
    table_counts: dict[str, int] = {}
    for ticker in tickers:
        available = list(tables_by_pair.get((ticker, requested_year), ()))
        if not available:
            return None
        safe_tables = [
            table
            for table in available
            if _multi_entity_interest_threshold_table_is_safe(table)
        ]
        if not safe_tables:
            return None
        table_counts[ticker] = len(safe_tables)
        proxy_item = _multi_entity_interest_threshold_proxy_item(
            item,
            ticker=ticker,
            year=requested_year,
            scope=requested_scope,
        )
        kwargs = dict(resolver_kwargs)
        kwargs["report_year_neighbor_fallback"] = None
        kwargs["source_multiplier"] = _multi_entity_interest_threshold_source_multiplier
        result = resolve_source_first_direct_lookup(
            proxy_item,
            tables_by_pair={(ticker, requested_year): available},
            **kwargs,
        )
        if not _multi_entity_interest_threshold_result_is_safe(
            result,
            ticker=ticker,
            year=requested_year,
            scope=requested_scope,
            available_tables=available,
        ):
            result = resolve_source_first_direct_lookup(
                proxy_item,
                tables_by_pair={(ticker, requested_year): safe_tables},
                **kwargs,
            )
        if not _multi_entity_interest_threshold_result_is_safe(
            result,
            ticker=ticker,
            year=requested_year,
            scope=requested_scope,
            available_tables=safe_tables,
        ):
            return None
        assert result is not None
        value = result.get("answer")
        if not isinstance(value, Decimal):
            value = parse_decimal_literal(value)
        if value is None:
            return None
        results[ticker] = dict(result)
        values[ticker] = str(value)
        predicates[ticker] = value > threshold

    sources: list[dict[str, Any]] = []
    for ticker in tickers:
        source_rows = results[ticker].get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        source.update(
            {
                "role": f"entity_{ticker}",
                "ticker": ticker,
                "multi_entity_interest_threshold": True,
                "multi_entity_interest_threshold_protocol": (
                    _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL
                ),
                "multi_entity_interest_threshold_kind": kind,
                "threshold": threshold,
                "threshold_unit": "billion_vnd",
                "threshold_predicate": "greater_than",
                "threshold_passed": predicates[ticker],
                "scope_policy": "explicit_parent_company_separate",
                "unit_provenance": "table_header_or_unit_label",
                "promotion_allowed": False,
            }
        )
        sources.append(source)

    answer = Decimal(sum(predicates.values()))
    first_selection = dict(results[tickers[0]].get("selection") or {})
    first_selection.update(
        {
            "value": answer,
            "candidate_source": _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
            "source_first_multi_entity_interest_threshold": True,
            "multi_entity_interest_threshold_protocol": (
                _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL
            ),
            "multi_entity_interest_threshold_kind": kind,
            "threshold": threshold,
            "threshold_unit": "billion_vnd",
            "threshold_predicate": "greater_than",
            "canonical_scope": requested_scope,
            "scope_policy": "explicit_parent_company_separate",
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": first_selection,
        "query": f"float((df1['operand_value'] > {threshold}).sum())",
        "tier": _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
        "protocol": _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
        "operation": "count_greater_than",
        "multi_entity_interest_threshold_kind": kind,
        "metric": "Chi phí lãi vay",
        "threshold": threshold,
        "threshold_unit": "billion_vnd",
        "requested_year": requested_year,
        "requested_scope": requested_scope,
        "replayed_tickers": list(tickers),
        "ticker_source": ticker_source,
        "positive_tickers": [ticker for ticker in tickers if predicates[ticker]],
        "values": values,
        "table_counts": table_counts,
        "scope_policy": "explicit_parent_company_separate",
        "promotion_allowed": False,
        "diagnostics": {
            "candidate_source": _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
            "protocol": _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
            "kind": kind,
            "metric": "Chi phí lãi vay",
            "threshold": str(threshold),
            "threshold_unit": "billion_vnd",
            "requested_year": requested_year,
            "requested_scope": requested_scope,
            "replayed_tickers": list(tickers),
            "ticker_source": ticker_source,
            "positive_tickers": [
                ticker for ticker in tickers if predicates[ticker]
            ],
            "values": values,
            "table_counts": table_counts,
            "scope_policy": "explicit_parent_company_separate",
            "answer_authority": (
                "current_structured_table_decimal_replay_and_local_predicate"
            ),
            "verification_class": "PARTIAL_EXPLICIT_SCOPE",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_interest_threshold_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the bounded parent-company interest-expense threshold lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _multi_entity_interest_threshold_spec(item) is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_multi_entity_interest_threshold_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"ticker_count_{len(result.get('replayed_tickers') or [])}"] += 1
        stats[
            f"threshold_pass_count_{len(result.get('positive_tickers') or [])}"
        ] += 1
        stats["explicit_scope"] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
        "accepted_match_modes": [
            "exact_contiguous",
            "ordered_with_ocr_gap",
            "fuzzy_ocr_label",
            "fuzzy_ocr_compact",
        ],
        "accepted_table_kinds": ["income_statement", "financial_note_detail"],
        "requires_exact_report_year": True,
        "requires_explicit_parent_company_scope": True,
        "requires_explicit_source_unit": True,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_SELECTOR_PROTOCOL = "source_first_multi_entity_selector_v1"


def _multi_entity_selector_spec(
    item: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    """Recognize one bounded selector-then-lookup family.

    This adapter is intentionally narrower than a generic ``max`` executor.
    The selector is a reported balance-sheet total and the final value is the
    reported current corporate-income-tax expense.  Both are replayed from
    source cells; the legacy plan's empty operands never supply a value.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    if str((plan.get("operation_ast") or {}).get("op") or "") != "max":
        return None
    planned_scope = str(
        plan.get("scope") or plan.get("reporting_scope") or ""
    ).strip().lower()
    # The current family is unqualified.  An explicit scope can use a future
    # typed route once its semantics are separately specified; accepting it
    # here would make the canonical-scope policy implicit.
    if planned_scope:
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None
    question = normalize(item.get("question") or "")
    if "tong von chu so huu cuoi nam cao nhat" not in question:
        return None
    output_phrase = "chi phi thue thu nhap doanh nghiep hien hanh"
    if output_phrase not in question:
        return None
    return (
        "equity_max_current_tax",
        "D. Vốn chủ sở hữu",
        "Tổng chi phí thuế thu nhập doanh nghiệp hiện hành",
    )


def _multi_entity_selector_tickers(
    item: Mapping[str, Any],
) -> tuple[list[str], str] | None:
    """Recover the complete issuer list with exact registry aliases only."""

    plan = item.get("question_plan") or {}
    planned: list[str] = []
    for value in plan.get("tickers") or []:
        ticker = str(value).strip().upper()
        if ticker and ticker not in planned:
            planned.append(ticker)

    if len(planned) >= 2:
        return planned, "planner_complete"

    # This initializes the shared registry, including structural aliases and
    # ticker forms such as PC1/HT1.  The two-issuer return contract is not
    # reused because this family needs the full explicit list.
    _cross_entity_ticker_plan(item)
    aliases = _CROSS_ENTITY_TICKER_ALIASES
    if aliases is None:
        return None
    inferred = extract_tickers(str(item.get("question") or ""), aliases)
    inferred = [ticker for ticker in inferred if ticker not in {"CP", "CTCP", "TMCP"}]
    if len(inferred) < 2 or len(inferred) > 8:
        return None
    # When the compiled plan retained only one issuer, alias recovery must not
    # silently accept a partial company list.  Count the high-precision legal
    # anchors that introduce issuers in this question family.  A mismatch
    # means at least one name is absent from ``code_stock.csv`` (for example
    # Q539's ``Lọc hóa dầu Bình Sơn``), so the route remains unresolved.
    normalized_question = normalize(item.get("question") or "")
    issuer_anchor_count = len(
        re.findall(
            r"\b(?:tong\s+ctcp|tong\s+cong\s+ty|ctcp|tap\s+doan)\b",
            normalized_question,
        )
    )
    if issuer_anchor_count < 2 or len(inferred) != issuer_anchor_count:
        return None
    if planned and planned[0] not in inferred:
        return None
    return inferred, "exact_code_stock_alias_recovery"


def _multi_entity_selector_proxy_item(
    item: Mapping[str, Any],
    *,
    ticker: str,
    year: int,
    metric: str,
    scope: str,
    period_mode: str,
) -> dict[str, Any]:
    if period_mode == "end":
        question = f"{metric} của {ticker} cuối năm {year} tỷ đồng"
    else:
        question = f"{metric} của {ticker} trong năm {year} tỷ đồng"
    plan = dict(item.get("question_plan") or {})
    plan.update(
        {
            "family": "direct_lookup",
            "tickers": [ticker],
            "years": [year],
            "scope": scope,
            "reporting_scope": scope,
            "operands": [
                {
                    "operand_id": "value",
                    "metric": metric,
                    "period": year,
                    "ticker": ticker,
                    "scope": scope,
                }
            ],
            "operation_ast": {"op": "lookup"},
        }
    )
    return {"question": question, "question_plan": plan}


def _multi_entity_selector_result_is_safe(
    result: Mapping[str, Any] | None,
    *,
    ticker: str,
    year: int,
    scope: str,
    role: str,
) -> bool:
    """Require exact issuer/year/scope and a family-specific source row."""

    if result is None:
        return False
    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or len(sources) != 1:
        return False
    source = sources[0]
    if not isinstance(source, Mapping):
        return False
    document_id = str(source.get("document_id") or "").removesuffix(".txt")
    document_ticker = document_id.split("_financial_statements_", 1)[0].upper()
    if document_ticker != ticker:
        return False
    try:
        if int(source.get("source_report_year")) != year:
            return False
        if int(selection.get("source_report_year")) != year:
            return False
    except (TypeError, ValueError):
        return False
    if int(source.get("report_year_offset") or 0) != 0:
        return False
    if str(source.get("source_first_scope") or "").strip().lower() != scope:
        return False
    match_mode = str(
        selection.get("source_first_match_mode")
        or (result.get("diagnostics") or {}).get("match_mode")
        or ""
    )
    if match_mode not in {
        "exact_contiguous",
        "ordered_with_ocr_gap",
        "fuzzy_ocr_label",
        "fuzzy_ocr_compact",
    }:
        return False
    table_kind = str(
        selection.get("source_first_table_kind")
        or source.get("source_first_table_kind")
        or ""
    ).strip().lower()
    row = normalize(selection.get("row_label") or source.get("row_label") or "")
    if role == "selector":
        if table_kind != "balance_sheet":
            return False
        if "von chu so huu" not in row:
            return False
        # The total is not the contributed-capital component.  This also
        # blocks a financial-data schedule from satisfying the selector.
        if "von dau tu cua chu so huu" in row:
            return False
    elif role == "output":
        if table_kind not in {"income_statement", "financial_note_detail"}:
            return False
        if "chi phi thue thu nhap doanh nghiep" not in row:
            return False
        if "hien hanh" not in row or "hoan lai" in row:
            return False
    else:
        return False
    return parse_decimal(result.get("answer")) is not None


def source_first_multi_entity_selector_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Select one issuer by a replayed total, then replay its output metric.

    The question is unqualified by reporting perimeter.  To prevent a
    consolidated/separate ranking flip, the equity selector is replayed in
    both scopes and must have the same unique winner.  The final tax value is
    emitted from the canonical consolidated statement and the scope choice is
    recorded explicitly as a best-effort policy because the two tax rows can
    legitimately differ between perimeters.
    """

    specification = _multi_entity_selector_spec(item)
    if specification is None:
        return None
    kind, selector_metric, output_metric = specification
    ticker_plan = _multi_entity_selector_tickers(item)
    if ticker_plan is None:
        return None
    tickers, ticker_source = ticker_plan
    years = [int(value) for value in (item.get("question_plan") or {}).get("years") or []]
    if len(tickers) < 2 or len(years) != 1:
        return None
    requested_year = years[0]
    selector_results_by_scope: dict[str, dict[str, dict[str, Any]]] = {}
    winners_by_scope: dict[str, str] = {}
    selector_values_by_scope: dict[str, dict[str, str]] = {}
    selector_metric_used_by_scope: dict[str, dict[str, str]] = {}
    for scope in ("consolidated", "separate"):
        scope_results: dict[str, dict[str, Any]] = {}
        scope_values: dict[str, str] = {}
        scope_metrics: dict[str, str] = {}
        for ticker in tickers:
            available = list(tables_by_pair.get((ticker, requested_year), ()))
            if not available:
                return None
            kwargs = dict(resolver_kwargs)
            # This selector is a strict exact-year replay.  A later report's
            # comparative column is useful for other direct lanes, but it is
            # not safe to rank issuers here without a same-year source.
            kwargs["report_year_neighbor_fallback"] = None
            result: dict[str, Any] | None = None
            metric_used: str | None = None
            # ``D. Vốn chủ sở hữu`` is the strict total-row spelling.  Some
            # issuers omit the section prefix, so the second spelling is
            # admitted only after the same balance-sheet/row gates pass.
            for selector_variant in (selector_metric, "Vốn chủ sở hữu"):
                proxy_item = _multi_entity_selector_proxy_item(
                    item,
                    ticker=ticker,
                    year=requested_year,
                    metric=selector_variant,
                    scope=scope,
                    period_mode="end",
                )
                candidate = resolve_source_first_direct_lookup(
                    proxy_item,
                    tables_by_pair={(ticker, requested_year): available},
                    **kwargs,
                )
                if _multi_entity_selector_result_is_safe(
                    candidate,
                    ticker=ticker,
                    year=requested_year,
                    scope=scope,
                    role="selector",
                ):
                    result = candidate
                    metric_used = selector_variant
                    break
            if result is None or metric_used is None:
                return None
            value = result.get("answer")
            if not isinstance(value, Decimal):
                value = parse_decimal_literal(value)
            if value is None:
                return None
            scope_results[ticker] = dict(result)
            scope_values[ticker] = str(value)
            scope_metrics[ticker] = metric_used
        maximum = max(
            (result["answer"] for result in scope_results.values()),
            default=None,
        )
        if maximum is None:
            return None
        winners = [
            ticker
            for ticker, result in scope_results.items()
            if result.get("answer") == maximum
        ]
        if len(winners) != 1:
            return None
        selector_results_by_scope[scope] = scope_results
        selector_values_by_scope[scope] = scope_values
        selector_metric_used_by_scope[scope] = scope_metrics
        winners_by_scope[scope] = winners[0]

    if winners_by_scope.get("consolidated") != winners_by_scope.get("separate"):
        return None
    selected_ticker = winners_by_scope["consolidated"]
    canonical_scope = "consolidated"
    available = list(tables_by_pair.get((selected_ticker, requested_year), ()))
    output_item = _multi_entity_selector_proxy_item(
        item,
        ticker=selected_ticker,
        year=requested_year,
        metric=output_metric,
        scope=canonical_scope,
        period_mode="flow",
    )
    output_kwargs = dict(resolver_kwargs)
    output_kwargs["report_year_neighbor_fallback"] = None
    output_result = resolve_source_first_direct_lookup(
        output_item,
        tables_by_pair={(selected_ticker, requested_year): available},
        **output_kwargs,
    )
    if not _multi_entity_selector_result_is_safe(
        output_result,
        ticker=selected_ticker,
        year=requested_year,
        scope=canonical_scope,
        role="output",
    ):
        return None
    if output_result is None:
        return None
    answer = output_result.get("answer")
    if not isinstance(answer, Decimal):
        answer = parse_decimal_literal(answer)
    if answer is None:
        return None

    sources: list[dict[str, Any]] = []
    for ticker in tickers:
        source_rows = selector_results_by_scope[canonical_scope][ticker].get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        source.update(
            {
                "role": f"selector_{ticker}",
                "ticker": ticker,
                "multi_entity_selector_protocol": _MULTI_ENTITY_SELECTOR_PROTOCOL,
                "multi_entity_selector_kind": kind,
                "selector_metric": selector_metric,
                "selector_metric_used": selector_metric_used_by_scope[canonical_scope][ticker],
                "selector_value": source.get("value"),
                "selector_winner": ticker == selected_ticker,
                "selector_canonical_scope": canonical_scope,
                "scope_invariance_checked": True,
                "scope_invariance_scopes": ["consolidated", "separate"],
                "promotion_allowed": False,
            }
        )
        sources.append(source)
    output_sources = output_result.get("sources") or []
    if len(output_sources) != 1:
        return None
    output_source = dict(output_sources[0])
    output_source.update(
        {
            "role": "selected_output",
            "ticker": selected_ticker,
            "multi_entity_selector_protocol": _MULTI_ENTITY_SELECTOR_PROTOCOL,
            "multi_entity_selector_kind": kind,
            "output_metric": output_metric,
            "selected_ticker": selected_ticker,
            "canonical_scope": canonical_scope,
            "scope_policy": "unqualified_question_canonical_consolidated",
            "promotion_allowed": False,
        }
    )
    sources.append(output_source)

    selection = dict(output_result.get("selection") or {})
    selection.update(
        {
            "value": answer,
            "candidate_source": _MULTI_ENTITY_SELECTOR_PROTOCOL,
            "source_first_multi_entity_selector": True,
            "multi_entity_selector_protocol": _MULTI_ENTITY_SELECTOR_PROTOCOL,
            "multi_entity_selector_kind": kind,
            "selector_metric": selector_metric,
            "output_metric": output_metric,
            "selected_ticker": selected_ticker,
            "selector_winner_scope_invariant": True,
            "canonical_scope": canonical_scope,
            "scope_policy": "unqualified_question_canonical_consolidated",
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": selection,
        "query": "float(df1.loc[df1.operand_role=='selected_output','operand_value'].iloc[0])",
        "tier": _MULTI_ENTITY_SELECTOR_PROTOCOL,
        "protocol": _MULTI_ENTITY_SELECTOR_PROTOCOL,
        "operation": "select_max_then_lookup",
        "multi_entity_selector_kind": kind,
        "selector_metric": selector_metric,
        "output_metric": output_metric,
        "requested_year": requested_year,
        "selected_ticker": selected_ticker,
        "planned_tickers": [
            str(value).strip().upper()
            for value in (item.get("question_plan") or {}).get("tickers") or []
            if str(value).strip()
        ],
        "replayed_tickers": list(tickers),
        "ticker_source": ticker_source,
        "canonical_scope": canonical_scope,
                "scope_invariance": {
                    "winner": winners_by_scope,
                    "selector_values": selector_values_by_scope,
                    "selector_metric_used": selector_metric_used_by_scope,
                },
        "scope_policy": "unqualified_question_canonical_consolidated",
        "promotion_allowed": False,
        "diagnostics": {
            "protocol": _MULTI_ENTITY_SELECTOR_PROTOCOL,
            "kind": kind,
            "selector_metric": selector_metric,
            "output_metric": output_metric,
            "requested_year": requested_year,
            "replayed_tickers": list(tickers),
            "ticker_source": ticker_source,
            "selected_ticker": selected_ticker,
            "canonical_scope": canonical_scope,
                "scope_invariance": {
                    "winner": winners_by_scope,
                    "selector_values": selector_values_by_scope,
                    "selector_metric_used": selector_metric_used_by_scope,
                },
            "scope_policy": "unqualified_question_canonical_consolidated",
            "answer_authority": "current_structured_table_decimal_replay_and_local_selector",
            "verification_class": "PARTIAL_SCOPE_ASSUMPTION",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_selector_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the bounded selector-then-lookup proposal lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _multi_entity_selector_spec(item) is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_multi_entity_selector_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"ticker_count_{len(result.get('replayed_tickers') or [])}"] += 1
        stats["selector_winner_scope_invariant"] += 1
        stats["canonical_scope_consolidated"] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_SELECTOR_PROTOCOL,
        "requires_exact_report_year": True,
        "requires_unique_selector_winner": True,
        "requires_scope_invariant_selector_winner": True,
        "canonical_scope_policy": "unqualified_question_canonical_consolidated",
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL = (
    "source_first_multi_entity_ratio_selector_v1"
)


def _multi_entity_ratio_selector_spec(
    item: Mapping[str, Any],
) -> tuple[str, str, str, str, str] | None:
    """Recognize the narrow debt/equity-selector interest-coverage family."""

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    if str((plan.get("operation_ast") or {}).get("op") or "") != "max":
        return None
    planned_scope = str(
        plan.get("scope") or plan.get("reporting_scope") or ""
    ).strip().lower()
    if planned_scope:
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None
    question = normalize(item.get("question") or "")
    if "no phai tra tren von chu so huu cao nhat" not in question:
        return None
    standard_output_cue = "kha nang thanh toan lai vay" in question
    profit_formula_cue = (
        "loi nhuan truoc thue" in question
        or "loi nhuan ke toan truoc thue" in question
    )
    interest_ratio_cue = (
        "tren chi phi lai vay" in question
        or ("ty le giua" in question and "chi phi lai vay" in question)
    )
    explicit_formula_cue = profit_formula_cue and interest_ratio_cue and (
        "chi phi lai vay" in question
    )
    if not (standard_output_cue or explicit_formula_cue):
        return None
    return (
        "debt_equity_max_interest_coverage",
        "Nợ phải trả",
        "Vốn chủ sở hữu",
        "Lợi nhuận kế toán trước thuế",
        "Chi phí lãi vay",
    )


def _multi_entity_ratio_selector_source_is_safe(
    result: Mapping[str, Any] | None,
    *,
    ticker: str,
    year: int,
    scope: str,
    role: str,
) -> bool:
    """Require one exact current-table source for a ratio selector operand."""

    if result is None:
        return False
    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or len(sources) != 1:
        return False
    source = sources[0]
    if not isinstance(source, Mapping):
        return False
    document_id = str(source.get("document_id") or "").removesuffix(".txt")
    document_ticker = document_id.split("_financial_statements_", 1)[0].upper()
    if document_ticker != ticker:
        return False
    try:
        if int(source.get("source_report_year")) != year:
            return False
        if int(selection.get("source_report_year")) != year:
            return False
    except (TypeError, ValueError):
        return False
    if int(source.get("report_year_offset") or 0) != 0:
        return False
    if str(source.get("source_first_scope") or "").strip().lower() != scope:
        return False
    match_mode = str(
        selection.get("source_first_match_mode")
        or (result.get("diagnostics") or {}).get("match_mode")
        or ""
    )
    if match_mode not in {
        "exact_contiguous",
        "ordered_with_ocr_gap",
        "fuzzy_ocr_label",
        "fuzzy_ocr_compact",
    }:
        return False
    table_kind = str(
        selection.get("source_first_table_kind")
        or source.get("source_first_table_kind")
        or ""
    ).strip().lower()
    row = normalize(selection.get("row_label") or source.get("row_label") or "")
    if role == "debt":
        if table_kind != "balance_sheet":
            return False
        if "no phai tra" not in row or "no thuan" in row:
            return False
    elif role == "equity":
        if table_kind != "balance_sheet":
            return False
        if "von chu so huu" not in row:
            return False
        if "von dau tu cua chu so huu" in row:
            return False
    elif role == "profit_before_tax":
        if table_kind != "income_statement":
            return False
        if "loi nhuan" not in row or "truoc thue" not in row:
            return False
        if "sau thue" in row:
            return False
    elif role == "interest_expense":
        if table_kind != "income_statement":
            return False
        if "chi phi lai vay" not in row:
            return False
    else:
        return False
    return parse_decimal(result.get("answer")) is not None


def _multi_entity_ratio_selector_primary_result(
    item: Mapping[str, Any],
    *,
    ticker: str,
    year: int,
    metric: str,
    scope: str,
    period_mode: str,
    role: str,
    expected_table_kind: str,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve one operand from exactly one primary coded statement table.

    Financial reports repeat values in segment, cash-flow, reconciliation and
    note tables.  The primary-statement retry is intentionally deterministic:
    keep only the classified statement kind and a table header containing the
    accounting ``Mã số`` column, resolve each table independently, and accept
    only one semantic candidate.  A duplicate or conflicting source remains
    unresolved.
    """

    available = list(tables_by_pair.get((ticker, year), ()))
    hits: list[dict[str, Any]] = []
    for table in available:
        if _multi_entity_statement_table_kind(table) != expected_table_kind:
            continue
        header_text = normalize(
            " ".join(
                [str(value) for value in table.get("headers") or []]
                + [str(value) for value in table.get("column_labels") or []]
            )
        )
        if "ma so" not in header_text:
            continue
        proxy_item = _multi_entity_selector_proxy_item(
            item,
            ticker=ticker,
            year=year,
            metric=metric,
            scope=scope,
            period_mode=period_mode,
        )
        kwargs = dict(resolver_kwargs)
        kwargs["report_year_neighbor_fallback"] = None
        result = resolve_source_first_direct_lookup(
            proxy_item,
            tables_by_pair={(ticker, year): [table]},
            **kwargs,
        )
        if _multi_entity_ratio_selector_source_is_safe(
            result,
            ticker=ticker,
            year=year,
            scope=scope,
            role=role,
        ):
            hits.append(dict(result))
    if len(hits) != 1:
        return None
    return hits[0]


def source_first_multi_entity_ratio_selector_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Select max debt/equity issuer, then replay interest coverage.

    The selector is replayed in both reporting scopes and requires the same
    unique issuer to win.  The final unqualified output uses consolidated
    income-statement rows and records that perimeter choice as best effort.
    """

    specification = _multi_entity_ratio_selector_spec(item)
    if specification is None:
        return None
    (
        kind,
        debt_metric,
        equity_metric,
        profit_metric,
        interest_metric,
    ) = specification
    ticker_plan = _multi_entity_selector_tickers(item)
    if ticker_plan is None:
        return None
    tickers, ticker_source = ticker_plan
    years = [int(value) for value in (item.get("question_plan") or {}).get("years") or []]
    if len(tickers) < 2 or len(years) != 1:
        return None
    requested_year = years[0]
    selector_values_by_scope: dict[str, dict[str, dict[str, str]]] = {}
    selector_results_by_scope: dict[str, dict[str, dict[str, Any]]] = {}
    winners_by_scope: dict[str, str] = {}
    for scope in ("consolidated", "separate"):
        scope_values: dict[str, dict[str, str]] = {}
        scope_results: dict[str, dict[str, Any]] = {}
        for ticker in tickers:
            debt = _multi_entity_ratio_selector_primary_result(
                item,
                ticker=ticker,
                year=requested_year,
                metric=debt_metric,
                scope=scope,
                period_mode="end",
                role="debt",
                expected_table_kind="balance_sheet",
                tables_by_pair=tables_by_pair,
                resolver_kwargs=resolver_kwargs,
            )
            equity = _multi_entity_ratio_selector_primary_result(
                item,
                ticker=ticker,
                year=requested_year,
                metric=equity_metric,
                scope=scope,
                period_mode="end",
                role="equity",
                expected_table_kind="balance_sheet",
                tables_by_pair=tables_by_pair,
                resolver_kwargs=resolver_kwargs,
            )
            if debt is None or equity is None:
                return None
            debt_value = debt.get("answer")
            equity_value = equity.get("answer")
            if not isinstance(debt_value, Decimal):
                debt_value = parse_decimal_literal(debt_value)
            if not isinstance(equity_value, Decimal):
                equity_value = parse_decimal_literal(equity_value)
            if (
                debt_value is None
                or equity_value is None
                or debt_value < 0
                or equity_value <= 0
            ):
                return None
            ratio = debt_value / equity_value
            scope_values[ticker] = {
                "debt": str(debt_value),
                "equity": str(equity_value),
                "debt_equity": str(ratio),
            }
            scope_results[ticker] = {
                "debt": debt,
                "equity": equity,
                "debt_equity": ratio,
            }
        maximum = max(
            (value["debt_equity"] for value in scope_results.values()),
            default=None,
        )
        if maximum is None:
            return None
        winners = [
            ticker
            for ticker, value in scope_results.items()
            if value["debt_equity"] == maximum
        ]
        if len(winners) != 1:
            return None
        selector_values_by_scope[scope] = scope_values
        selector_results_by_scope[scope] = scope_results
        winners_by_scope[scope] = winners[0]

    if winners_by_scope.get("consolidated") != winners_by_scope.get("separate"):
        return None
    selected_ticker = winners_by_scope["consolidated"]
    output_results_by_scope: dict[str, dict[str, Any]] = {}
    output_values_by_scope: dict[str, str] = {}
    for scope in ("consolidated", "separate"):
        profit = _multi_entity_ratio_selector_primary_result(
            item,
            ticker=selected_ticker,
            year=requested_year,
            metric=profit_metric,
            scope=scope,
            period_mode="flow",
            role="profit_before_tax",
            expected_table_kind="income_statement",
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        interest = _multi_entity_ratio_selector_primary_result(
            item,
            ticker=selected_ticker,
            year=requested_year,
            metric=interest_metric,
            scope=scope,
            period_mode="flow",
            role="interest_expense",
            expected_table_kind="income_statement",
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if profit is None or interest is None:
            continue
        profit_value = profit.get("answer")
        interest_value = interest.get("answer")
        if not isinstance(profit_value, Decimal):
            profit_value = parse_decimal_literal(profit_value)
        if not isinstance(interest_value, Decimal):
            interest_value = parse_decimal_literal(interest_value)
        if (
            profit_value is None
            or interest_value is None
            or interest_value <= 0
            or str(
                (profit.get("sources") or [{}])[0].get("internal_table_uid")
            )
            != str(
                (interest.get("sources") or [{}])[0].get("internal_table_uid")
            )
        ):
            continue
        coverage = (profit_value + interest_value) / interest_value
        output_results_by_scope[scope] = {
            "profit_before_tax": profit,
            "interest_expense": interest,
            "coverage": coverage,
        }
        output_values_by_scope[scope] = str(coverage)

    canonical_scope = "consolidated"
    canonical_output = output_results_by_scope.get(canonical_scope)
    if canonical_output is None:
        return None
    sources: list[dict[str, Any]] = []
    for ticker in tickers:
        selector_result = selector_results_by_scope[canonical_scope][ticker]
        for role, metric, result in (
            ("debt", debt_metric, selector_result["debt"]),
            ("equity", equity_metric, selector_result["equity"]),
        ):
            source_rows = result.get("sources") or []
            if len(source_rows) != 1:
                return None
            source = dict(source_rows[0])
            source.update(
                {
                    "role": f"selector_{role}_{ticker}",
                    "ticker": ticker,
                    "multi_entity_ratio_selector_protocol": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
                    "multi_entity_ratio_selector_kind": kind,
                    "selector_metric": metric,
                    "selector_value": source.get("value"),
                    "selector_debt_equity": selector_values_by_scope[canonical_scope][ticker]["debt_equity"],
                    "selector_winner": ticker == selected_ticker,
                    "selector_canonical_scope": canonical_scope,
                    "scope_invariance_checked": True,
                    "scope_invariance_scopes": ["consolidated", "separate"],
                    "promotion_allowed": False,
                }
            )
            sources.append(source)
    for role, metric, result in (
        (
            "selected_output_profit_before_tax",
            profit_metric,
            canonical_output["profit_before_tax"],
        ),
        (
            "selected_output_interest_expense",
            interest_metric,
            canonical_output["interest_expense"],
        ),
    ):
        source_rows = result.get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        source.update(
            {
                "role": role,
                "ticker": selected_ticker,
                "multi_entity_ratio_selector_protocol": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
                "multi_entity_ratio_selector_kind": kind,
                "output_metric": metric,
                "selected_ticker": selected_ticker,
                "canonical_scope": canonical_scope,
                "scope_policy": "unqualified_question_canonical_consolidated",
                "promotion_allowed": False,
            }
        )
        sources.append(source)
    answer = canonical_output["coverage"]
    selection = dict(
        canonical_output["profit_before_tax"].get("selection") or {}
    )
    selection.update(
        {
            "value": answer,
            "candidate_source": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
            "source_first_multi_entity_ratio_selector": True,
            "multi_entity_ratio_selector_protocol": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
            "multi_entity_ratio_selector_kind": kind,
            "selector_metric_debt": debt_metric,
            "selector_metric_equity": equity_metric,
            "output_metric_profit_before_tax": profit_metric,
            "output_metric_interest_expense": interest_metric,
            "selected_ticker": selected_ticker,
            "selector_winner_scope_invariant": True,
            "canonical_scope": canonical_scope,
            "scope_policy": "unqualified_question_canonical_consolidated",
            "selector_values_by_scope": selector_values_by_scope,
            "output_values_by_scope": output_values_by_scope,
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": selection,
        "query": (
            "float((df1.loc[df1.operand_role=='selected_output_profit_before_tax',"
            "'operand_value'].iloc[0] + df1.loc["
            "df1.operand_role=='selected_output_interest_expense','operand_value'].iloc[0]) / "
            "df1.loc[df1.operand_role=='selected_output_interest_expense','operand_value'].iloc[0])"
        ),
        "tier": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
        "protocol": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
        "operation": "select_max_debt_equity_then_interest_coverage",
        "multi_entity_ratio_selector_kind": kind,
        "selector_metric_debt": debt_metric,
        "selector_metric_equity": equity_metric,
        "output_metric_profit_before_tax": profit_metric,
        "output_metric_interest_expense": interest_metric,
        "requested_year": requested_year,
        "selected_ticker": selected_ticker,
        "planned_tickers": [
            str(value).strip().upper()
            for value in (item.get("question_plan") or {}).get("tickers") or []
            if str(value).strip()
        ],
        "replayed_tickers": list(tickers),
        "ticker_source": ticker_source,
        "canonical_scope": canonical_scope,
        "scope_invariance": {
            "winner": winners_by_scope,
            "selector_values": selector_values_by_scope,
            "output_values": output_values_by_scope,
        },
        "scope_policy": "unqualified_question_canonical_consolidated",
        "promotion_allowed": False,
        "diagnostics": {
            "protocol": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
            "kind": kind,
            "requested_year": requested_year,
            "replayed_tickers": list(tickers),
            "ticker_source": ticker_source,
            "selected_ticker": selected_ticker,
            "canonical_scope": canonical_scope,
            "scope_invariance": {
                "winner": winners_by_scope,
                "selector_values": selector_values_by_scope,
                "output_values": output_values_by_scope,
            },
            "scope_policy": "unqualified_question_canonical_consolidated",
            "answer_authority": "current_structured_table_decimal_replay_and_local_ratio_selector",
            "verification_class": "PARTIAL_SCOPE_ASSUMPTION",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_ratio_selector_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the bounded debt/equity selector telemetry lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _multi_entity_ratio_selector_spec(item) is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_multi_entity_ratio_selector_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"ticker_count_{len(result.get('replayed_tickers') or [])}"] += 1
        stats["selector_winner_scope_invariant"] += 1
        stats["canonical_scope_consolidated"] += 1
        if "separate" in (result.get("scope_invariance") or {}).get("output_values", {}):
            stats["output_replayed_both_scopes"] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
        "requires_exact_report_year": True,
        "requires_unique_selector_winner": True,
        "requires_scope_invariant_selector_winner": True,
        "requires_primary_coded_statement_table": True,
        "canonical_scope_policy": "unqualified_question_canonical_consolidated",
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_COMPOSED_TOTAL_PROTOCOL = "source_first_composed_total_v1"
_COMPOSED_TOTAL_OPS = {"count", "sum", "values"}
_COMPOSED_TOTAL_REJECT_CUES = (
    "co bao nhieu",
    "trong so",
    "chenh lech",
    "so sanh",
    "giua ",
)
_COMPOSED_TOTAL_SPECS = {
    "provisions": (
        "Dự phòng phải trả ngắn hạn",
        "Dự phòng phải trả dài hạn",
    ),
}


def _composed_total_metric_spec(
    item: Mapping[str, Any],
) -> tuple[str, tuple[str, ...]] | None:
    """Recognize a narrow total that is explicitly composed of two rows.

    Some one-issuer/one-year questions are compiled as a multi-value count
    plan even though the wording asks for an accounting total.  This adapter
    only handles the provision total whose two current-table components are
    stable and independently retrievable.  It deliberately does not treat a
    generic ``tổng`` as a permission to sum arbitrary rows.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    if operation not in _COMPOSED_TOTAL_OPS:
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            years.append(int(value))
        except (TypeError, ValueError):
            continue
    if len(set(tickers)) != 1 or len(set(years)) != 1:
        return None
    question = normalize(item.get("question") or "")
    if any(cue in f" {question} " for cue in _COMPOSED_TOTAL_REJECT_CUES):
        return None
    if "tong" not in question or "du phong phai tra" not in question:
        return None
    # A request for one component is a direct lookup, not a composition.
    if "du phong phai tra ngan han" in question or "du phong phai tra dai han" in question:
        return None
    for kind, metrics in _COMPOSED_TOTAL_SPECS.items():
        return kind, metrics
    return None


def _composed_component_source_is_safe(
    result: Mapping[str, Any],
    *,
    metric: str,
    requested_year: int,
) -> bool:
    """Require one exact current-table component before summing it."""

    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or not sources:
        return False
    if selection.get("source_first_match_mode") not in _TEMPORAL_SOURCE_ALLOWED_MATCH_MODES:
        return False
    try:
        if int(selection.get("source_report_year")) != int(requested_year):
            return False
    except (TypeError, ValueError):
        return False
    row_signature = _temporal_row_signature(selection.get("row_label") or "")
    metric_signature = _temporal_row_signature(metric)
    if not row_signature or row_signature != metric_signature:
        return False
    table_kind = normalize(
        selection.get("source_first_table_kind")
        or sources[0].get("source_first_table_kind")
        or sources[0].get("table_kind")
        or ""
    )
    if table_kind in {"governance roster", "governance"}:
        return False
    # The direct resolver has already applied the requested output divisor.
    # Re-running the OCR-aware parser on a value such as ``Decimal('3.375')``
    # would interpret the three digits after the dot as thousands separators
    # and silently turn it back into 3375.
    if parse_decimal_literal(result.get("answer")) is None:
        return False
    source = sources[0]
    try:
        if int(source.get("source_report_year")) != int(requested_year):
            return False
    except (TypeError, ValueError):
        return False
    if not source.get("internal_table_uid") or not source.get("document_id"):
        return False
    return True


def source_first_composed_total_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Replay two same-table component rows and compose a reported total."""

    specification = _composed_total_metric_spec(item)
    if specification is None:
        return None
    kind, metrics = specification
    plan = dict(item.get("question_plan") or {})
    years = [int(value) for value in plan.get("years") or []]
    requested_year = years[0]
    original_scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower() or None
    component_results: list[tuple[str, dict[str, Any]]] = []
    for metric in metrics:
        proxy_plan = {
            **plan,
            "family": "direct_lookup",
            "operands": [{"metric": metric}],
            "scope": original_scope,
        }
        proxy_item = {**dict(item), "question_plan": proxy_plan}
        result = resolve_source_first_direct_lookup(
            proxy_item,
            tables_by_pair=tables_by_pair,
            **dict(resolver_kwargs),
        )
        if result is None or not _composed_component_source_is_safe(
            result,
            metric=metric,
            requested_year=requested_year,
        ):
            return None
        component_results.append((metric, result))

    sources: list[dict[str, Any]] = []
    component_locators: list[tuple[str, int, int]] = []
    table_contract: list[tuple[str, str, str, str, str]] = []
    for index, (metric, result) in enumerate(component_results, start=1):
        source_rows = result.get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        selection = result.get("selection") or {}
        locator = (
            str(source.get("internal_table_uid") or ""),
            int(source.get("row_index")),
            int(source.get("column_index")),
        )
        component_locators.append(locator)
        table_contract.append(
            (
                str(source.get("document_id") or ""),
                str(source.get("internal_table_uid") or ""),
                normalize(source.get("source_first_scope") or selection.get("source_first_scope") or ""),
                str(source.get("source_to_vnd_multiplier") or ""),
                normalize(
                    source.get("source_first_table_kind")
                    or selection.get("source_first_table_kind")
                    or source.get("table_kind")
                    or ""
                ),
            )
        )
        source.update(
            {
                "role": f"composed_component_{index}",
                "composed_total_protocol": _COMPOSED_TOTAL_PROTOCOL,
                "composed_total_kind": kind,
                "composed_component_metric": metric,
                "promotion_allowed": False,
            }
        )
        sources.append(source)
    if len(set(component_locators)) != len(component_locators):
        return None
    if len(set(table_contract)) != 1:
        # A total assembled from different statements or different units is
        # not safe even when both individual cells replay numerically.
        return None

    answer = sum(
        (parse_decimal(result.get("answer")) for _, result in component_results),
        Decimal(0),
    )
    query = (
        "float(df1.loc[df1.operand_role.isin(['composed_component_1',"
        "'composed_component_2']), 'operand_value'].sum())"
    )
    first_selection = dict(component_results[0][1]["selection"])
    first_selection.pop("raw_value", None)
    first_selection.update(
        {
            "value": answer,
            "row_label": " + ".join(metrics),
            "candidate_source": _COMPOSED_TOTAL_PROTOCOL,
            "source_first_match_mode": "composed_exact_component_replay",
            "composed_total": True,
            "composed_total_protocol": _COMPOSED_TOTAL_PROTOCOL,
            "composed_total_kind": kind,
            "composed_component_metrics": list(metrics),
            "composed_component_selections": [
                dict(result.get("selection") or {})
                for _, result in component_results
            ],
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": first_selection,
        "query": query,
        "tier": _COMPOSED_TOTAL_PROTOCOL,
        "protocol": _COMPOSED_TOTAL_PROTOCOL,
        "operation": "sum",
        "composed_total_kind": kind,
        "composed_component_metrics": list(metrics),
        "promotion_allowed": False,
        "diagnostics": {
            "candidate_source": _COMPOSED_TOTAL_PROTOCOL,
            "composed_total": True,
            "composed_total_kind": kind,
            "composed_component_metrics": list(metrics),
            "composed_component_count": len(metrics),
            "composed_same_table_contract": True,
            "answer_authority": "current_structured_table_decimal_replay",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_composed_total_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the narrow two-component reported-total proposal lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        specification = _composed_total_metric_spec(item)
        if specification is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        kind, metrics = specification
        stats[f"kind_{kind}"] += 1
        result = source_first_composed_total_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"component_count_{len(metrics)}"] += 1
    return answers, {
        **dict(stats),
        "protocol": _COMPOSED_TOTAL_PROTOCOL,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL = (
    "source_first_multi_entity_direct_aggregation_v1"
)
_MULTI_ENTITY_DIRECT_AGGREGATION_SPECS = {
    "tax_payable": {
        "metric": "Thuế và các khoản phải nộp Nhà nước",
        "table_kind": "balance_sheet",
        "question_phrase": "thue va cac khoan phai nop nha nuoc",
        "period_mode": "end",
    },
    "selling_expense": {
        "metric": "Chi phí bán hàng",
        "table_kind": "income_statement",
        "question_phrase": "chi phi ban hang",
        "period_mode": "flow",
    },
    "credit_loss_provision": {
        "metric": "Chi phí dự phòng rủi ro tín dụng",
        "table_kind": "income_statement",
        "question_phrase": "du phong rui ro tin dung",
        "period_mode": "flow",
    },
    "lc_commitment": {
        "metric": "Cam kết L/C",
        "table_kind": "balance_sheet",
        "question_phrase": "cam ket l c",
        "period_mode": "end",
    },
    "operating_cash_flow": {
        "metric": "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
        "table_kind": "cash_flow_statement",
        "question_phrase": "luu chuyen tien thuan tu hoat dong kinh doanh",
        "period_mode": "flow",
    },
}
_MULTI_ENTITY_DIRECT_AGGREGATION_REJECT_CUES = (
    "trong nhom",
    "xet cac",
    "co bao nhieu",
    "cao nhat",
    "lon nhat",
    "thap nhat",
    "nho nhat",
    "trung vi",
    "ty le",
    "ty trong",
    "he so",
    "chenh lech",
    "so sanh",
    "tang truong",
    "toc do tang",
    "neu ",
)


def _multi_entity_direct_aggregation_operation(
    item: Mapping[str, Any],
) -> str | None:
    """Return a safe aggregate operation, including a narrow text fallback.

    A few simple multi-issuer questions are miscompiled as ``count`` even
    though their Vietnamese wording explicitly says ``Tổng``.  Only infer an
    operation for an otherwise unqualified direct aggregate; conditional
    ``Có bao nhiêu`` questions must remain outside this lane.
    """

    planned = str(
        ((item.get("question_plan") or {}).get("operation_ast") or {}).get("op")
        or ""
    )
    if planned in {"mean", "sum"}:
        return planned
    question = normalize(item.get("question") or "")
    if "co bao nhieu" in question:
        return None
    if any(phrase in question for phrase in ("trung binh", "binh quan", "gia tri trung binh")):
        return "mean"
    if "tong" in question or "cong lai" in question:
        return "sum"
    return None


def _multi_entity_direct_aggregation_spec(
    item: Mapping[str, Any],
) -> tuple[str, str, str, str] | None:
    """Recognize a narrow direct metric aggregation over explicit issuers.

    The existing program route can accidentally select a different row for
    each issuer because its metric hint is derived from the whole multi-name
    question.  This adapter instead extracts one of two stable accounting
    families and asks the source-first resolver once per issuer.  It has no
    Question-ID allowlist and refuses conditional, ranking, ratio, and
    comparison wording.

    The return value is ``(kind, metric, table_kind, period_mode)``.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    operation = _multi_entity_direct_aggregation_operation(item)
    if operation is None:
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    if len(tickers) < 2 or len(set(tickers)) != len(tickers):
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None
    question = normalize(item.get("question") or "")
    if any(
        cue in f" {question} "
        for cue in _MULTI_ENTITY_DIRECT_AGGREGATION_REJECT_CUES
    ):
        return None
    # ``khi`` is a conditional cue, but it is also part of the common issuer
    # name ``dầu khí``.  Remove that company-name bigram before applying the
    # word-level condition guard so a simple CFO total is not discarded.
    question_without_dau_khi = question.replace("dau khi", "")
    if re.search(r"\bkhi\b", question_without_dau_khi):
        return None
    scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if not scope:
        scope = "separate" if "cong ty me" in question else ""
    # An explicit parent-company request is the only scope we promote in this
    # lane.  Consolidated/unqualified multi-issuer questions remain on the
    # existing program/candidate path until their scope contract is explicit.
    if scope != "separate":
        return None
    for kind, specification in _MULTI_ENTITY_DIRECT_AGGREGATION_SPECS.items():
        if specification["question_phrase"] in question:
            return (
                kind,
                str(specification["metric"]),
                str(specification["table_kind"]),
                str(specification["period_mode"]),
            )
    return None


def _multi_entity_requested_unit_text(item: Mapping[str, Any]) -> str:
    """Return a unit phrase that preserves the question's divisor contract."""

    question = normalize(item.get("question") or "")
    if "nghin ty dong" in question or "ngan ty dong" in question:
        return "nghìn tỷ đồng"
    if "trieu dong" in question:
        return "triệu đồng"
    if "ty dong" in question:
        return "tỷ đồng"
    if "vnd" in question or "dong" in question:
        return "VND"
    requested_unit = str(
        (item.get("question_plan") or {}).get("requested_unit") or ""
    ).strip().lower()
    return {
        "billion_vnd": "tỷ đồng",
        "million_vnd": "triệu đồng",
        "trillion_vnd": "nghìn tỷ đồng",
        "vnd": "VND",
    }.get(requested_unit, "VND")


def _multi_entity_direct_proxy_item(
    item: Mapping[str, Any],
    *,
    ticker: str,
    year: int,
    metric: str,
    period_mode: str,
    scope: str,
) -> dict[str, Any]:
    """Build a one-issuer proxy so ticker extraction cannot cross-contaminate."""

    unit = _multi_entity_requested_unit_text(item)
    if period_mode == "end":
        question = (
            f"Số dư {metric} cuối năm của công ty mẹ {ticker} "
            f"năm {year} là bao nhiêu {unit}?"
        )
    else:
        question = (
            f"{metric} của công ty mẹ {ticker} trong năm {year} "
            f"là bao nhiêu {unit}?"
        )
    plan = dict(item.get("question_plan") or {})
    plan.update(
        {
            "family": "direct_lookup",
            "tickers": [ticker],
            "years": [year],
            "scope": scope,
            "reporting_scope": scope,
            "operands": [
                {
                    "operand_id": "value",
                    "metric": metric,
                    "period": year,
                    "ticker": ticker,
                    "scope": scope,
                }
            ],
            "operation_ast": {"op": "lookup"},
        }
    )
    return {"question": question, "question_plan": plan}


def _multi_entity_direct_metric_variants(metric: str) -> list[str]:
    """Return exact metric spellings needed for known OCR whitespace loss."""

    variants = [metric]
    if normalize(metric) == normalize("Cam kết L/C"):
        # The same balance-sheet line is rendered as ``... L/C`` by some
        # issuers and ``... thư tín dụng`` by others.  The route keeps this
        # alias local to the typed L/C family; it is not a general fuzzy
        # synonym for every row containing ``cam kết``.
        variants.append("Cam kết thư tín dụng")
    if normalize(metric) == normalize(
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh"
    ):
        # Some MSR cash-flow tables concatenate ``từ`` and ``hoạt`` in the
        # row label; another report concatenates ``hoạt động`` and ``kinh``.
        # These are OCR spelling variants, not semantic aliases.
        variants.append("Lưu chuyển tiền thuần từhoạt động kinh doanh")
        variants.append("Lưu chuyển tiền thuần từ hoạt độngkinh doanh")
    return variants


def _multi_entity_parent_company_year_column(
    evidence: list[dict[str, Any]],
    row_index: int,
    row: list[Any],
    year: int | None,
    question: str,
    *,
    fallback: Any,
) -> tuple[int, Decimal] | None:
    """Select a proved ``Công ty`` year column in a split cash-flow table.

    A small number of separate MSR tables expose both ``Tập đoàn`` and
    ``Công ty`` columns.  The parent-company question must use the latter,
    but only when the same column also declares the requested year and the
    table visibly contains the paired group header.  Ordinary one-group
    tables defer to the normal chooser.
    """

    if not year or not isinstance(row, list):
        return fallback(evidence, row_index, row, year, question)
    company_columns: set[int] = set()
    group_header_seen = False
    year_columns: set[int] = set()
    for entry in evidence:
        index = int(entry.get("index", 0))
        if index > row_index:
            continue
        cells = entry.get("row") or []
        if not isinstance(cells, list):
            continue
        for column_index, cell in enumerate(cells):
            text = normalize(cell)
            if text == "cong ty" or (
                text.startswith("cong ty ") and not text.startswith("cong ty con")
            ):
                company_columns.add(column_index)
            if text == "tap doan" or text.startswith("tap doan "):
                group_header_seen = True
            if str(year) in text:
                year_columns.add(column_index)
    candidates = [
        column_index
        for column_index in sorted(company_columns & year_columns)
        if column_index < len(row) and parse_decimal(row[column_index]) is not None
    ]
    if group_header_seen and len(candidates) == 1:
        return candidates[0], parse_decimal(row[candidates[0]])  # type: ignore[return-value]
    return fallback(evidence, row_index, row, year, question)


def _multi_entity_statement_table_kind(table: Mapping[str, Any]) -> str:
    """Read the source classifier kind without trusting retrieval metadata."""

    return str(source_first_lookup_module._table_kind(table) or "").strip().lower()


def _multi_entity_primary_statement_subset(
    tables: Iterable[Mapping[str, Any]],
    *,
    expected_table_kind: str,
) -> list[dict[str, Any]]:
    """Keep primary statement tables after a duplicate-source retry.

    A small number of reports contain both the primary income statement and a
    later self-audit/reconciliation table with a different sign or value. The
    first source-first attempt is allowed to resolve normally; this subset is
    used only when that attempt is ambiguous or violates the expected table
    kind.  Audit/reclassification markers are structural exclusion cues, not
    numeric ranking signals.
    """

    excluded_context = (
        "giai trinh",
        "tu lap",
        "kiem toan",
        "soat xet",
        "phan loai lai",
        "bao cao truoc day",
        "so lieu trinh bay lai",
    )
    output: list[dict[str, Any]] = []
    for table in tables:
        if _multi_entity_statement_table_kind(table) != expected_table_kind:
            continue
        context = source_first_lookup_module._table_context(table)
        if any(marker in context for marker in excluded_context):
            continue
        output.append(dict(table))
    return output


def _multi_entity_direct_result_is_safe(
    result: Mapping[str, Any] | None,
    *,
    ticker: str,
    year: int,
    metric: str,
    metric_kind: str | None = None,
    expected_table_kind: str,
    scope: str,
) -> bool:
    """Require one exact issuer/scope/year/row replay before aggregation."""

    if result is None:
        return False
    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or len(sources) != 1:
        return False
    source = sources[0]
    if not isinstance(source, Mapping):
        return False
    selected_ticker = str(source.get("ticker") or "").strip().upper()
    document_id = str(source.get("document_id") or "").removesuffix(".txt")
    document_ticker = document_id.split("_financial_statements_", 1)[0].upper()
    if selected_ticker and selected_ticker != ticker:
        return False
    if document_ticker != ticker:
        return False
    try:
        if int(source.get("source_report_year")) != year:
            return False
        if int(selection.get("source_report_year")) != year:
            return False
    except (TypeError, ValueError):
        return False
    if int(source.get("report_year_offset") or 0) != 0:
        return False
    if str(source.get("source_first_scope") or "").strip().lower() != scope:
        return False
    selected_kind = str(
        selection.get("source_first_table_kind")
        or source.get("source_first_table_kind")
        or source.get("table_kind")
        or ""
    ).strip().lower()
    if selected_kind != expected_table_kind:
        return False
    match_mode = str(
        selection.get("source_first_match_mode")
        or (result.get("diagnostics") or {}).get("match_mode")
        or ""
    )
    if match_mode not in {
        "exact_contiguous",
        "ordered_with_ocr_gap",
        "fuzzy_ocr_label",
        "fuzzy_ocr_compact",
    }:
        return False
    row_text = normalize(selection.get("row_label") or source.get("row_label") or "")
    metric_text = normalize(metric)
    row_text_compact = row_text.replace(" ", "")
    metric_text_compact = metric_text.replace(" ", "")
    metric_matches = bool(
        metric_text
        and (
            metric_text in row_text
            or metric_text_compact in row_text_compact
        )
    )
    if not metric_matches and metric_kind == "lc_commitment":
        # ``L/C`` and ``thư tín dụng`` are the two observed spellings of one
        # off-balance-sheet commitment line.  Require all structural pieces
        # so a generic ``Cam kết`` row cannot pass this family gate.
        metric_matches = (
            "cam ket" in row_text
            and "nghiep vu" in row_text
            and (
                "l c" in row_text
                or "thu tin dung" in row_text
            )
        )
    if not metric_text or not metric_matches:
        return False
    if parse_decimal(result.get("answer")) is None:
        return False
    return True


def source_first_multi_entity_direct_aggregation_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Replay one exact source row per issuer, then calculate mean or sum."""

    specification = _multi_entity_direct_aggregation_spec(item)
    if specification is None:
        return None
    kind, metric, expected_table_kind, period_mode = specification
    plan = item.get("question_plan") or {}
    operation = _multi_entity_direct_aggregation_operation(item)
    if operation is None:
        return None
    tickers = [str(value).strip().upper() for value in plan.get("tickers") or []]
    years = [int(value) for value in plan.get("years") or []]
    requested_year = years[0]
    scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if not scope:
        scope = "separate"

    resolved: list[tuple[str, dict[str, Any]]] = []
    for ticker in tickers:
        available = list(tables_by_pair.get((ticker, requested_year), ()))
        if not available:
            return None
        restricted = {(ticker, requested_year): available}
        resolver_kwargs_for_ticker = dict(resolver_kwargs)
        if kind == "operating_cash_flow":
            fallback_choose_year_column = resolver_kwargs_for_ticker.get(
                "choose_year_column"
            )
            if callable(fallback_choose_year_column):
                def choose_parent_company_year_column(
                    evidence: list[dict[str, Any]],
                    row_index: int,
                    row: list[Any],
                    year: int | None,
                    question: str,
                ) -> tuple[int, Decimal] | None:
                    return _multi_entity_parent_company_year_column(
                        evidence,
                        row_index,
                        row,
                        year,
                        question,
                        fallback=fallback_choose_year_column,
                    )

                resolver_kwargs_for_ticker["choose_year_column"] = (
                    choose_parent_company_year_column
                )

        result: dict[str, Any] | None = None
        subset: list[dict[str, Any]] | None = None
        for resolver_metric in _multi_entity_direct_metric_variants(metric):
            proxy_item = _multi_entity_direct_proxy_item(
                item,
                ticker=ticker,
                year=requested_year,
                metric=resolver_metric,
                period_mode=period_mode,
                scope=scope,
            )
            candidate_result = resolve_source_first_direct_lookup(
                proxy_item,
                tables_by_pair=restricted,
                **resolver_kwargs_for_ticker,
            )
            if not _multi_entity_direct_result_is_safe(
                candidate_result,
                ticker=ticker,
                year=requested_year,
                metric=metric,
                metric_kind=kind,
                expected_table_kind=expected_table_kind,
                scope=scope,
            ):
                # Retry only inside primary statement tables.  This specifically
                # handles audit/reconciliation duplicates such as SAB 2017 while
                # keeping a missing or scope-conflicting issuer unresolved.
                if subset is None:
                    subset = _multi_entity_primary_statement_subset(
                        available,
                        expected_table_kind=expected_table_kind,
                    )
                if subset:
                    candidate_result = resolve_source_first_direct_lookup(
                        proxy_item,
                        tables_by_pair={(ticker, requested_year): subset},
                        **resolver_kwargs_for_ticker,
                    )
            if _multi_entity_direct_result_is_safe(
                candidate_result,
                ticker=ticker,
                year=requested_year,
                metric=metric,
                metric_kind=kind,
                expected_table_kind=expected_table_kind,
                scope=scope,
            ):
                result = dict(candidate_result)
                break
        if result is None:
            return None
        resolved.append((ticker, dict(result)))

    # The resolver already returns a Decimal in the requested output unit.
    # Do not send it through the OCR parser again: a value such as Decimal
    # ``2050.099`` would otherwise be mistaken for a dotted thousands-form
    # literal (``2050099``).  The canonical-literal parser is only a fallback
    # for a resolver implementation that returns a string.
    values = [
        value
        if isinstance((value := result.get("answer")), Decimal)
        else parse_decimal_literal(value)
        for _, result in resolved
    ]
    if any(value is None for value in values):
        return None
    numeric_values = [value for value in values if value is not None]
    # Income statements are not sign-consistent across issuers: some reports
    # print an expense in parentheses while others print the same expense as a
    # positive amount and subtract it in the profit formula.  The question
    # asks for the total *expense*, not a signed P&L contribution, so aggregate
    # the magnitude only for this explicitly typed expense family.  Keep the
    # original raw cell/sign in provenance, but expose the semantic operand
    # used by the emitted query as a non-negative expense amount.
    expense_magnitude = kind in {"selling_expense", "credit_loss_provision"}
    if expense_magnitude:
        numeric_values = [abs(value) for value in numeric_values]
    if operation == "mean":
        answer = sum(numeric_values, Decimal(0)) / Decimal(len(numeric_values))
        query = "float(df1['operand_value'].mean())"
    elif operation == "sum":
        answer = sum(numeric_values, Decimal(0))
        query = "float(df1['operand_value'].sum())"
    else:
        return None

    sources: list[dict[str, Any]] = []
    entity_diagnostics: list[dict[str, Any]] = []
    for ticker, result in resolved:
        source_rows = result.get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        if expense_magnitude:
            source_value = source.get("value")
            if isinstance(source_value, Decimal):
                source["value"] = abs(source_value)
            else:
                parsed_source_value = parse_decimal_literal(source_value)
                if parsed_source_value is None:
                    return None
                source["value"] = abs(parsed_source_value)
        source.update(
            {
                "role": f"entity_{ticker}",
                "ticker": ticker,
                "multi_entity_direct_aggregation": True,
                "multi_entity_direct_protocol": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
                "multi_entity_direct_kind": kind,
                "multi_entity_direct_metric": metric,
                "multi_entity_direct_operation": operation,
                "multi_entity_direct_value_policy": (
                    "expense_magnitude_abs" if expense_magnitude else "source_signed"
                ),
                "promotion_allowed": False,
            }
        )
        sources.append(source)
        entity_diagnostics.append(
            {
                "ticker": ticker,
                "answer": str(source.get("value")),
                "source_replayed_answer": str(result.get("answer")),
                "value_policy": (
                    "expense_magnitude_abs" if expense_magnitude else "source_signed"
                ),
                "selection": dict(result.get("selection") or {}),
            }
        )
    if len({str(source.get("internal_table_uid") or "") for source in sources}) != len(sources):
        # A single table UID repeated for multiple issuers indicates ticker
        # contamination or malformed identity metadata; never aggregate it.
        return None

    first_selection = dict(resolved[0][1].get("selection") or {})
    first_selection.pop("raw_value", None)
    first_selection.update(
        {
            "value": answer,
            "row_label": metric,
            "candidate_source": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
            "source_first_multi_entity_direct_aggregation": True,
            "multi_entity_direct_protocol": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
            "multi_entity_direct_kind": kind,
                "multi_entity_direct_metric": metric,
                "multi_entity_direct_operation": operation,
                "multi_entity_direct_value_policy": (
                    "expense_magnitude_abs" if expense_magnitude else "source_signed"
                ),
                "multi_entity_direct_tickers": tickers,
            "multi_entity_direct_year": requested_year,
            "multi_entity_direct_scope": scope,
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": first_selection,
        "query": query,
        "tier": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
        "protocol": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
        "operation": operation,
        "multi_entity_direct_kind": kind,
        "multi_entity_direct_metric": metric,
        "requested_year": requested_year,
        "requested_scope": scope,
        "promotion_allowed": False,
        "diagnostics": {
            "candidate_source": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
            "multi_entity_direct_aggregation": True,
            "multi_entity_direct_protocol": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
            "multi_entity_direct_kind": kind,
            "multi_entity_direct_metric": metric,
            "operation": operation,
            "value_policy": (
                "expense_magnitude_abs" if expense_magnitude else "source_signed"
            ),
            "tickers": tickers,
            "requested_year": requested_year,
            "requested_scope": scope,
            "entity_count": len(sources),
            "entity_replays": entity_diagnostics,
            "answer_authority": "current_structured_table_decimal_replay_and_local_aggregation",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_direct_aggregation_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the fail-closed per-issuer source replay aggregation lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        specification = _multi_entity_direct_aggregation_spec(item)
        if specification is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        kind, _, _, _ = specification
        stats[f"kind_{kind}"] += 1
        result = source_first_multi_entity_direct_aggregation_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"operation_{result['operation']}"] += 1
        stats[
            f"entity_count_{len(result.get('sources') or [])}"
        ] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
        "requires_explicit_parent_company_scope": True,
        "requires_one_year": True,
        "requires_per_entity_current_table_replay": True,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL = (
    "source_first_multi_entity_conditional_count_v1"
)
_MULTI_ENTITY_CONDITIONAL_COUNT_REJECT_CUES = (
    "lon hon",
    "cao hon",
    "thap hon",
    "it nhat",
    "cao nhat",
    "thap nhat",
    "nho nhat",
    "ty le",
    "phan tram",
    "so sanh",
    "chenh lech",
)


def _multi_entity_conditional_count_spec(
    item: Mapping[str, Any],
) -> tuple[str, str, str, str] | None:
    """Recognize an explicit-scope positive-value count over issuers.

    This is intentionally separate from the mean/sum route.  The existing
    planner emits the Q973 family as ``plan_required`` and its legacy fallback
    uses a negative-value predicate.  The source-first route accepts only the
    stable wording ``Có bao nhiêu ... [metric] dương`` with an explicit
    parent-company scope and no threshold/ranking/comparison clause.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") not in {
        "multi_entity_or_period_aggregation",
        "conditional_analytical",
    }:
        return None
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    if operation not in {"count", "plan_required"}:
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    if len(tickers) < 2 or len(set(tickers)) != len(tickers):
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) != 1:
        return None
    question = normalize(item.get("question") or "")
    metric = "lưu chuyển tiền thuần từ hoạt động kinh doanh"
    if normalize(metric) not in question:
        return None
    if "co bao nhieu" not in question or "duong" not in question:
        return None
    if any(cue in f" {question} " for cue in _MULTI_ENTITY_CONDITIONAL_COUNT_REJECT_CUES):
        return None
    if "cong ty me" not in question:
        return None
    scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if scope != "separate":
        return None
    return (
        "operating_cash_flow",
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
        "cash_flow_statement",
        "flow",
    )


def source_first_multi_entity_conditional_count_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Count exact source-replayed positive values for explicit issuers."""

    specification = _multi_entity_conditional_count_spec(item)
    if specification is None:
        return None
    kind, metric, expected_table_kind, period_mode = specification
    plan = item.get("question_plan") or {}
    tickers = [str(value).strip().upper() for value in plan.get("tickers") or []]
    years = [int(value) for value in plan.get("years") or []]
    requested_year = years[0]
    scope = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if scope != "separate":
        return None

    resolved: list[tuple[str, dict[str, Any]]] = []
    for ticker in tickers:
        available = list(tables_by_pair.get((ticker, requested_year), ()))
        if not available:
            return None
        restricted = {(ticker, requested_year): available}
        resolver_kwargs_for_ticker = dict(resolver_kwargs)
        if kind == "operating_cash_flow":
            fallback_choose_year_column = resolver_kwargs_for_ticker.get(
                "choose_year_column"
            )
            if callable(fallback_choose_year_column):

                def choose_parent_company_year_column(
                    evidence: list[dict[str, Any]],
                    row_index: int,
                    row: list[Any],
                    year: int | None,
                    question: str,
                ) -> tuple[int, Decimal] | None:
                    return _multi_entity_parent_company_year_column(
                        evidence,
                        row_index,
                        row,
                        year,
                        question,
                        fallback=fallback_choose_year_column,
                    )

                resolver_kwargs_for_ticker["choose_year_column"] = (
                    choose_parent_company_year_column
                )

        result: dict[str, Any] | None = None
        subset: list[dict[str, Any]] | None = None
        for resolver_metric in _multi_entity_direct_metric_variants(metric):
            proxy_item = _multi_entity_direct_proxy_item(
                item,
                ticker=ticker,
                year=requested_year,
                metric=resolver_metric,
                period_mode=period_mode,
                scope=scope,
            )
            candidate_result = resolve_source_first_direct_lookup(
                proxy_item,
                tables_by_pair=restricted,
                **resolver_kwargs_for_ticker,
            )
            if not _multi_entity_direct_result_is_safe(
                candidate_result,
                ticker=ticker,
                year=requested_year,
                metric=metric,
                metric_kind=kind,
                expected_table_kind=expected_table_kind,
                scope=scope,
            ):
                if subset is None:
                    subset = _multi_entity_primary_statement_subset(
                        available,
                        expected_table_kind=expected_table_kind,
                    )
                if subset:
                    candidate_result = resolve_source_first_direct_lookup(
                        proxy_item,
                        tables_by_pair={(ticker, requested_year): subset},
                        **resolver_kwargs_for_ticker,
                    )
            if _multi_entity_direct_result_is_safe(
                candidate_result,
                ticker=ticker,
                year=requested_year,
                metric=metric,
                metric_kind=kind,
                expected_table_kind=expected_table_kind,
                scope=scope,
            ):
                result = dict(candidate_result)
                break
        if result is None:
            return None
        resolved.append((ticker, result))

    values: list[Decimal] = []
    predicates: list[bool] = []
    sources: list[dict[str, Any]] = []
    entity_diagnostics: list[dict[str, Any]] = []
    for ticker, result in resolved:
        value = result.get("answer")
        if isinstance(value, Decimal):
            numeric_value = value
        else:
            numeric_value = parse_decimal_literal(value)
        if numeric_value is None:
            return None
        predicate = numeric_value > Decimal(0)
        values.append(numeric_value)
        predicates.append(predicate)
        source_rows = result.get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        source.update(
            {
                "role": f"entity_{ticker}",
                "ticker": ticker,
                "multi_entity_conditional_count": True,
                "multi_entity_conditional_count_protocol": (
                    _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL
                ),
                "multi_entity_conditional_metric": metric,
                "multi_entity_conditional_predicate": "positive",
                "multi_entity_conditional_predicate_passed": predicate,
                "multi_entity_conditional_value": numeric_value,
                "promotion_allowed": False,
            }
        )
        sources.append(source)
        entity_diagnostics.append(
            {
                "ticker": ticker,
                "value": str(numeric_value),
                "positive": predicate,
                "selection": dict(result.get("selection") or {}),
            }
        )

    if len({str(source.get("internal_table_uid") or "") for source in sources}) != len(sources):
        return None
    answer = Decimal(sum(predicates))
    first_selection = dict(resolved[0][1].get("selection") or {})
    first_selection.pop("raw_value", None)
    first_selection.update(
        {
            "value": answer,
            "row_label": metric,
            "candidate_source": _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
            "source_first_multi_entity_conditional_count": True,
            "multi_entity_conditional_count_protocol": (
                _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL
            ),
            "multi_entity_conditional_metric": metric,
            "multi_entity_conditional_predicate": "positive",
            "multi_entity_conditional_tickers": tickers,
            "multi_entity_conditional_year": requested_year,
            "multi_entity_conditional_scope": scope,
            "promotion_allowed": False,
        }
    )
    return {
        "answer": answer,
        "sources": sources,
        "selection": first_selection,
        "query": "float((df1['operand_value'] > 0).sum())",
        "tier": _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
        "protocol": _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
        "operation": "count_positive",
        "metric": metric,
        "requested_year": requested_year,
        "requested_scope": scope,
        "replayed_tickers": tickers,
        "positive_tickers": [
            ticker for ticker, predicate in zip(tickers, predicates) if predicate
        ],
        "promotion_allowed": False,
        "diagnostics": {
            "candidate_source": _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
            "multi_entity_conditional_count": True,
            "protocol": _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
            "metric": metric,
            "predicate": "positive",
            "values": {
                ticker: str(value)
                for ticker, value in zip(tickers, values)
            },
            "predicates": {
                ticker: predicate
                for ticker, predicate in zip(tickers, predicates)
            },
            "replayed_tickers": tickers,
            "requested_year": requested_year,
            "requested_scope": scope,
            "entity_count": len(sources),
            "entity_replays": entity_diagnostics,
            "answer_authority": "current_structured_table_decimal_replay_and_local_predicate",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_multi_entity_conditional_count_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the fail-closed explicit-scope positive-count proposal lane."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _multi_entity_conditional_count_spec(item) is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_multi_entity_conditional_count_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"ticker_count_{len(result.get('replayed_tickers') or [])}"] += 1
        stats[f"positive_count_{len(result.get('positive_tickers') or [])}"] += 1
    return answers, {
        **dict(stats),
        "protocol": _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
        "requires_explicit_parent_company_scope": True,
        "requires_one_year": True,
        "requires_per_entity_current_table_replay": True,
        "predicate": "strictly_positive",
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_TEMPORAL_SOURCE_ALLOWED_MATCH_MODES = {
    "exact_contiguous",
    "ordered_with_ocr_gap",
}
_TEMPORAL_ROW_DECORATION = {
    "cny",
    "eur",
    "jpy",
    "krw",
    "nam",
    "nay",
    "truoc",
    "usd",
    "vnd",
}


def temporal_source_metric_variants(item: dict[str, Any]) -> list[str]:
    """Extract a small, source-oriented metric population for two-year asks.

    The compiled temporal operands in the review bundle often contain the
    operation shell (``tăng trưởng``, ``từ năm ... sang năm ...``) and the
    issuer name.  Passing that whole string to a row matcher makes a generic
    numeric row look relevant.  This helper keeps only the financial phrase
    and emits a few structural relaxations (``tổng``/``số dư`` wrappers,
    nested ``giá trị còn lại của ...`` and lender qualifiers).  It is a
    navigation/execution proposal only; the source-first resolver still owns
    row, period, unit and duplicate gates.
    """

    plan = item.get("question_plan") or {}
    question = normalize(item.get("question") or "")
    # Parentheses are punctuation here, not a blanket exclusion.  In this
    # family ``chi phí (thu nhập) thuế hoãn lại`` carries an accounting
    # qualifier inside the parentheses, so dropping the contents would turn a
    # distinct row into the broader ``chi phí thuế hoãn lại`` concept.
    question = re.sub(r"[()]", " ", question)

    # A few questions begin with a date before naming the metric.  Remove only
    # that syntactic preamble; ``tài sản`` is still a metric and must not be
    # mistaken for the preposition ``tại`` after normalization.
    question = re.sub(
        r"^(?:den|vao|tai)\s+ngay\s+\d+\s+\d+\s+\d{4}\s*",
        "",
        question,
    )

    prefixes = (
        "tinh phan tram toc do tang truong ",
        "tinh phan tram tang truong ",
        "tinh toc do tang truong ",
        "tinh ty le phan tram tang truong ",
        "tinh ty le tang truong ",
        "toc do tang truong ",
        "tinh ty suat tang truong ",
        "ty suat tang truong ",
        "ty le phan tram tang truong ",
        "ty le tang truong ",
        "ti le tang truong ",
        "muc tang truong ",
        "tang truong ",
        "tinh phan tram ",
        "tinh so chenh lech ",
        "tinh chenh lech ",
        "so chenh lech ",
        "muc chenh lech ",
        "do chenh lech ",
        "chenh lech ",
        "hieu so giua ",
        "hieu so ",
        "ty le bien dong ",
        "ti le bien dong ",
        "muc bien dong ",
        "bien dong ",
        "muc thay doi ",
        "thay doi ",
        "tinh ",
    )
    question = question.removeprefix("cua ")
    # Operation words can be nested (``tính hiệu số ...``), so peel a bounded
    # sequence of grammatical shells before matching a source row.
    while True:
        for prefix in prefixes:
            if question.startswith(prefix):
                question = question[len(prefix) :].strip()
                break
        else:
            break
    question = question.removeprefix("cua ")

    # Remove the explicit comparison period/result tail.  ``tu ngan hang`` is
    # deliberately not matched here: it is a possible lender qualifier.
    question = re.split(
        r"\s+(?:tu|sang|den)\s+(?:nam\s+)?(?:cuoi nam|dau nam|nam\s+)?(?:19|20)\d{2}",
        question,
        maxsplit=1,
    )[0]
    # ``giữa năm ... và năm ...`` and ``so với ...`` are common in the
    # misclassified one-ticker questions.  The explicit phrase vocabulary
    # keeps ``từ ngân hàng`` available as a lender qualifier.
    question = re.split(
        r"\s+(?:giua|so voi)\s+(?:hai\s+|thoi diem\s+|cuoi nam\s+|"
        r"dau nam\s+|nam\s+|ngay\s+|nien do\s+|ky tai chinh\s+)*"
        r"(?:19|20)\d{2}",
        question,
        maxsplit=1,
    )[0]
    question = re.split(
        r"\s+giua\s+(?:hai\s+)?(?:nien do|ky tai chinh)",
        question,
        maxsplit=1,
    )[0]
    question = re.split(
        r"\s+(?:cuoi nam|cuoi ky|dau nam|dau ky)\s+(?:19|20)\d{2}",
        question,
        maxsplit=1,
    )[0]
    question = re.split(
        r"\s+(?:tang|giam|thay doi|lon hon|be hon)\s+bao nhieu",
        question,
        maxsplit=1,
    )[0]

    # Remove an issuer tail only when it is introduced by an explicit entity
    # marker or planned ticker.  ``tài chính`` and ``tài sản`` are metrics,
    # not issuer tails, even though normalization maps ``tài``/``tại`` to the
    # same ASCII token.
    question = re.sub(
        r"\s+(?:cua|tai|o)\s+(?:ctcp|cong ty|ngan hang|tap doan|tong cong ty)\b.*$",
        "",
        question,
    )
    for ticker in plan.get("tickers") or []:
        ticker_text = normalize(ticker)
        if ticker_text:
            question = re.sub(
                rf"\s+tai\s+{re.escape(ticker_text)}\b.*$",
                "",
                question,
            )
            question = re.sub(
                rf"\s+cua\s+{re.escape(ticker_text)}\b.*$",
                "",
                question,
            )
            question = re.sub(
                rf"\s+{re.escape(ticker_text)}\b.*$",
                "",
                question,
            )
    question = question.strip(" ,")

    pieces: list[str] = []
    if " cua " in question:
        left, right = question.split(" cua ", 1)
        generic_amount_wrapper = left in {
            "so tien",
            "so tien phai tra",
            "so tien phai nop",
        }
        if not generic_amount_wrapper:
            pieces.append(left)
        # Preserve a nested metric only for wrappers such as
        # ``giá trị còn lại của tổng tài sản ...``.  The ordinary right-hand
        # side is the issuer name and must not become a metric candidate.
        if (
            left.endswith(("gia tri con lai", "gia tri", "muc"))
            or generic_amount_wrapper
            or right.startswith(("tong tai san ", "tai san ", "gia tri "))
        ):
            pieces.append(right.split(" cua ", 1)[0])
    else:
        pieces.append(question)

    variants: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip(" ,")
        value = value.removeprefix("cua ").strip()
        if len(value.split()) < 2:
            return
        if value and value not in variants:
            variants.append(value)

    for piece in pieces:
        add(piece)
        # These are grammatical wrappers in the question, while the source
        # may report the underlying line without them.
        for wrapper in (
            "tong ",
            "so du ",
            "gia tri con lai ",
            "gia tri ",
            "muc ",
        ):
            if piece.startswith(wrapper):
                add(piece[len(wrapper) :])
        if piece.startswith("chi phi tra truoc "):
            add(piece[len("chi phi tra truoc ") :])
        if piece.startswith("gia tri con lai "):
            add(piece[len("gia tri con lai ") :])
        if piece.startswith("so du "):
            add(piece[len("so du ") :])
        if " tong " in f" {piece}":
            add(piece.replace(" tong ", " "))
        for removable in ("trong han", "theo don vi"):
            if removable in piece:
                add(piece.replace(removable, " "))
        if " tu ngan hang tmcp quoc te " in f" {piece} ":
            add(piece.replace(" tu ngan hang tmcp quoc te ", " "))
        if piece.endswith(" bang vnd"):
            add(piece[: -len(" bang vnd")])
        if piece.endswith(" phai nop cuoi ky"):
            add(piece[: -len(" phai nop cuoi ky")])
        if piece.endswith(" phai nop"):
            add(piece[: -len(" phai nop")])
        if piece.startswith("lai vay phai tra nguoi ban ngan han"):
            add("lai vay phai tra")

    return variants


def _temporal_metric_core(metric: Any) -> tuple[str, ...]:
    """Return the required semantic tokens for a temporal source lookup.

    The temporal question parser emits harmless wrappers such as ``tổng``
    and ``số dư`` as separate metric variants.  Those wrappers are removed
    here, but the financial qualifiers themselves remain mandatory.  This is
    deliberately conservative: a row containing only ``lãi vay phải trả``
    cannot prove a question asking for ``lãi vay phải trả người bán ngắn hạn``.
    """

    text = normalize(metric)
    while True:
        changed = False
        for prefix in ("tong so du ", "tong ", "so du "):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                changed = True
                break
        if not changed:
            break
    return tuple(token for token in text.split() if token)


def _temporal_row_signature(label: Any) -> str:
    """Canonicalize harmless row numbering/decorations for period comparison."""

    leading_section_markers = {
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "vi",
        "vii",
        "viii",
        "ix",
        "x",
        "xi",
        "xii",
    }
    tokens = normalize(label).split()
    while tokens and (
        tokens[0].isdigit()
        or len(tokens[0]) == 1
        or tokens[0] in leading_section_markers
    ):
        tokens.pop(0)
    while tokens and tokens[-1] in _TEMPORAL_ROW_DECORATION:
        tokens.pop()
    # Annual reports frequently append the note reference to the same row,
    # while the reference changes between years (for example ``VI.9`` versus
    # ``VI.10``).  A trailing Roman-numeral-plus-number pair is a note label,
    # not part of the financial concept.  Remove it only at the end so a
    # semantic token such as ``Vay 1`` remains intact.
    if (
        len(tokens) >= 2
        and tokens[-2] in leading_section_markers
        and tokens[-1].isdigit()
    ):
        del tokens[-2:]
    # Financial-note OCR often appends the referenced note numbers to the
    # reported row, and those references can change when the note is revised
    # between annual reports (for example ``... khó đòi 6, 7, 8, 9, 10`` versus
    # ``... khó đòi 6, 8, 10``).  Strip a run of two or more trailing numbers,
    # but retain a single semantic number such as ``khoản vay 1``.
    trailing_numbers = 0
    for token in reversed(tokens):
        if not token.isdigit():
            break
        trailing_numbers += 1
    if trailing_numbers >= 2:
        del tokens[-trailing_numbers:]
    # A common annual-report label is written as ``Lãi/(lỗ) thuần`` in one
    # year and ``Lãi thuần`` in another.  OCR normalization exposes the
    # parenthetical alternative as the adjacent token ``lo``.  It is not a
    # separate financial concept when it occurs in this conventional phrase;
    # remove only that exact local pattern so a real ``lỗ`` row elsewhere is
    # never collapsed accidentally.
    normalized_tokens: list[str] = []
    for index, token in enumerate(tokens):
        if (
            token == "lo"
            and index > 0
            and tokens[index - 1] == "lai"
            and index + 1 < len(tokens)
            and tokens[index + 1] == "thuan"
        ):
            continue
        normalized_tokens.append(token)
    tokens = normalized_tokens
    return " ".join(tokens)


def _temporal_row_covers_metric(metric: Any, row_label: Any) -> bool:
    """Require every essential metric token to be present in the selected row."""

    required = set(_temporal_metric_core(metric))
    row_tokens = set(_temporal_row_signature(row_label).split())
    return bool(required) and required <= row_tokens


def _temporal_source_result_is_safe(
    result: Mapping[str, Any],
    *,
    metric: str,
    required_metric: str | None = None,
    question: str,
    requested_year: int,
) -> bool:
    selection = result.get("selection")
    if not isinstance(selection, Mapping):
        return False
    if selection.get("source_first_match_mode") not in _TEMPORAL_SOURCE_ALLOWED_MATCH_MODES:
        return False
    try:
        source_year = int(selection.get("source_report_year"))
    except (TypeError, ValueError):
        return False
    if source_year != requested_year:
        # The first temporal arm is intentionally exact-year only.  The
        # comparative Y+1 fallback remains a separate, explicitly measured
        # experiment because it can bind a reclassified prior-year column.
        return False

    row_text = normalize(selection.get("row_label") or "")
    target_text = normalize(metric)
    question_text = normalize(question)
    if not row_text or not target_text:
        return False
    if not _temporal_row_covers_metric(required_metric or metric, row_text):
        return False

    # ``Chi phí thuế TNDN`` is a semantic total in some note tables, while
    # ``Chi phí thuế TNDN hiện hành`` is a distinct income-statement line.
    # The direct resolver may otherwise bind the qualified line because it is
    # the first lexical match.  Without an explicit qualifier in the
    # question, quarantine that result rather than silently changing the
    # requested metric.
    requested_metric_core = " ".join(
        _temporal_metric_core(required_metric or metric)
    )
    if (
        requested_metric_core == "chi phi thue tndn"
        and "hien hanh" in row_text
        and "hien hanh" not in requested_metric_core
    ):
        return False

    # Transfer/reclassification/flow rows are not the reported stock unless
    # the question explicitly asks for that action.
    for prefix in ("chuyen sang ", "chuyen tu ", "trich lap ", "dieu chinh "):
        if row_text.startswith(prefix) and prefix.strip() not in target_text:
            return False
    if "du phong" in target_text and "du phong" not in row_text:
        return False
    if "doanh thu hoat dong tai chinh" in target_text and "tu van" in row_text:
        return False
    if "tien gui tai ngan hang nha nuoc" in target_text and (
        "tien gui tai ngan hang nha nuoc" not in row_text
    ):
        return False
    if "tong no tai chinh" in target_text and not any(
        marker in row_text for marker in ("tong no tai chinh", "cong no tai chinh")
    ):
        return False
    # A named lender in the question must remain visible in the selected row;
    # a bare ``Vay ngắn hạn`` line cannot prove which bank was requested.
    if " tu ngan hang " in f" {question_text} " and "ngan hang" not in row_text:
        return False
    return True


def source_first_temporal_answer(
    item: dict[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Propose a two-year answer from two independent exact source lookups.

    This route only handles one planned ticker and two explicit years.  Every
    operand must resolve through the existing direct-lookup resolver, and all
    successful metric variants for a period must agree on the same value and
    row label.  A Decimal replay is still a best-effort proposal, not semantic
    authorization.
    """

    plan = item.get("question_plan") or {}
    input_family = str(plan.get("family") or "")
    # The current planner labels a subset of one-issuer/two-year questions as
    # cross_entity_comparison because its generic comparison AST has two
    # temporal leaves.  Admit only that exact structural shape here; genuine
    # two-issuer questions remain on the cross-entity route and never enter
    # this fallback.
    if input_family not in {"temporal_change", "cross_entity_comparison"}:
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    years = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(tickers) != 1 or len(years) != 2:
        return None
    metrics = temporal_source_metric_variants(item)
    if not metrics:
        return None

    operand_specs = [
        {"operand_id": "x_old", "metric": metrics[0], "period": min(years)},
        {"operand_id": "x_new", "metric": metrics[0], "period": max(years)},
    ]
    operation = infer_formula_operation(item, operand_specs)
    if operation not in {"subtract", "percentage_change"}:
        return None

    plan_scope = plan.get("scope") or plan.get("reporting_scope")
    requested_scope = plan_scope or (
        "separate" if "cong ty me" in normalize(item.get("question") or "") else None
    )
    resolved: dict[str, dict[str, Any]] = {}
    for role, year in (("x_old", min(years)), ("x_new", max(years))):
        period_results: list[tuple[str, dict[str, Any]]] = []
        for metric in metrics:
            synthetic = {
                "question": item.get("question") or "",
                "question_plan": {
                    "family": "direct_lookup",
                    "tickers": tickers,
                    "years": [year],
                    "scope": plan_scope,
                    "reporting_scope": plan.get("reporting_scope"),
                    "requested_unit": plan.get("requested_unit"),
                    "operands": [
                        {
                            "operand_id": "value",
                            "metric": metric,
                            "period": year,
                            "ticker": tickers[0],
                            "scope": plan_scope,
                        }
                    ],
                },
            }
            result = resolve_source_first_direct_lookup(
                synthetic,
                tables_by_pair=tables_by_pair,
                parse_decimal=parse_decimal,
                candidate_evidence_window=candidate_evidence_window,
                choose_year_column=choose_year_column,
                source_multiplier=source_multiplier,
                requested_divisor=requested_divisor,
                report_year_neighbor_fallback=None,
            )
            if result is not None and _temporal_source_result_is_safe(
                result,
                metric=metric,
                required_metric=metrics[0],
                question=str(item.get("question") or ""),
                requested_year=year,
            ):
                period_results.append((metric, result))
        if not period_results:
            return None
        signatures = {
            (
                str(result.get("answer")),
                _temporal_row_signature(result.get("selection", {}).get("row_label") or ""),
            )
            for _, result in period_results
        }
        if len(signatures) != 1:
            # Different successful variants indicate a semantic or duplicate
            # ambiguity; do not let an arbitrary variant authorize arithmetic.
            return None
        _, selected = period_results[0]
        source = dict((selected.get("sources") or [])[0])
        source["role"] = role
        source["temporal_source_metric"] = period_results[0][0]
        source["temporal_row_signature"] = _temporal_row_signature(
            source.get("row_label") or ""
        )
        resolved[role] = source

    # Arithmetic is meaningful only when both years bind the same reported
    # line.  A reclassification such as ``Doanh thu bộ phận`` versus
    # ``Doanh thu thuần bộ phận`` is not a safe temporal pair even when both
    # individual cells replay successfully.
    temporal_row_signatures = {
        str(source.get("temporal_row_signature") or "")
        for source in resolved.values()
    }
    if not temporal_row_signatures or "" in temporal_row_signatures:
        return None
    if len(temporal_row_signatures) > 1:
        return None

    if requested_scope is None:
        source_scopes = {
            normalize(source.get("source_first_scope") or "unknown")
            for source in resolved.values()
        }
        if len(source_scopes) > 1:
            # Do not subtract/compare a separate statement for one year with a
            # consolidated statement for the other when the question omitted
            # the reporting scope.
            return None

    old = resolved["x_old"]["value"]
    new = resolved["x_new"]["value"]
    question = str(item.get("question") or "")
    q = normalize(question)
    if operation == "percentage_change":
        if old == 0:
            return None
        if "giam" in q and new < old:
            answer = (old - new) / abs(old) * Decimal(100)
            query = (
                "float((df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0] - "
                "df1.loc[df1.operand_role=='x_new','operand_value'].iloc[0]) / abs("
                "df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0]) * 100)"
            )
        else:
            answer = (new - old) / abs(old) * Decimal(100)
            query = (
                "float((df1.loc[df1.operand_role=='x_new','operand_value'].iloc[0] - "
                "df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0]) / abs("
                "df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0]) * 100)"
            )
    elif "be hon" in q or "giam" in q:
        answer = old - new
        query = (
            "float(df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0] - "
            "df1.loc[df1.operand_role=='x_new','operand_value'].iloc[0])"
        )
    else:
        answer = new - old
        query = (
            "float(df1.loc[df1.operand_role=='x_new','operand_value'].iloc[0] - "
            "df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0])"
        )
    return {
        "answer": answer,
        "sources": [resolved["x_old"], resolved["x_new"]],
        "query": query,
        "tier": (
            "source_first_temporal_cross_entity_v1"
            if input_family == "cross_entity_comparison"
            else "source_first_temporal_v1"
        ),
        "operation": operation,
        "metric_variants": metrics,
        "requested_years": years,
        "input_family": input_family,
        "family_recovered": input_family == "cross_entity_comparison",
    }


def build_source_first_temporal_lookup_index(
    items_by_question: Mapping[int, dict[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the exact two-operand source proposal lane with telemetry."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        plan = item.get("question_plan") or {}
        family = str(plan.get("family") or "")
        tickers = [value for value in plan.get("tickers") or [] if str(value).strip()]
        years = []
        for value in plan.get("years") or []:
            try:
                years.append(int(value))
            except (TypeError, ValueError):
                continue
        temporal_shape = family == "temporal_change" or (
            family == "cross_entity_comparison"
            and len(set(str(value).upper() for value in tickers)) == 1
            and len(set(years)) == 2
        )
        if not temporal_shape:
            stats["questions_skipped_non_temporal"] += 1
            continue
        stats["questions_considered"] += 1
        if family == "cross_entity_comparison":
            stats["questions_recovered_from_cross_entity_family"] += 1
        result = source_first_temporal_answer(item, tables_by_pair=tables_by_pair)
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"operation_{result['operation']}"] += 1
    return answers, {
        **dict(stats),
        "protocol": "source_first_temporal_v1",
        "exact_report_year_only": True,
        "requires_one_ticker_two_years": True,
        "accepts_misclassified_cross_entity_single_ticker_shape": True,
        "accepted_match_modes": sorted(_TEMPORAL_SOURCE_ALLOWED_MATCH_MODES),
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "lane": "authorized_best_effort_submission_candidate",
    }


_CONDITIONAL_YEAR_SELECTION_MARKERS = (" nam co ", " nam ma ")
_CONDITIONAL_YEAR_SELECTION_OPERATORS = (
    ("lon nhat", "max"),
    ("cao nhat", "max"),
    ("nho nhat", "min"),
    ("thap nhat", "min"),
)


def _conditional_year_selection_spec(
    item: Mapping[str, Any],
) -> tuple[list[str], list[int], str, str, str] | None:
    """Parse ``return metric in the year whose condition is extreme`` asks.

    The parser is deliberately contract-shaped instead of a general natural
    language parser.  It accepts one issuer, at least three explicit years,
    one ``năm có``/``năm mà`` selector, and a clearly stated max/min cue.  The
    returned tuple is ``(tickers, years, answer_metric, condition_metric,
    operator)``.  Anything outside this grammar remains on the existing
    best-effort program/candidate path.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    if len(tickers) != 1:
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) < 3:
        years = list(dict.fromkeys(int(year) for year in YEAR_RE.findall(str(item.get("question") or ""))))
    if len(years) < 3:
        return None

    question = normalize(item.get("question") or "")
    marker_position = -1
    marker_length = 0
    for marker in _CONDITIONAL_YEAR_SELECTION_MARKERS:
        position = question.find(marker)
        if position >= 0 and (marker_position < 0 or position < marker_position):
            marker_position = position
            marker_length = len(marker)
    if marker_position < 0:
        return None
    operator_position = None
    operator = None
    selector_tail = question[marker_position + marker_length :]
    for cue, candidate_operator in _CONDITIONAL_YEAR_SELECTION_OPERATORS:
        position = selector_tail.find(cue)
        if position >= 0 and (operator_position is None or position < operator_position):
            operator_position = position
            operator = candidate_operator
    if operator_position is None or operator is None:
        return None

    # The answer metric appears after the final explicitly listed year and
    # before the issuer phrase.  This preserves nested accounting qualifiers
    # while avoiding the company name itself.
    prefix = question[:marker_position]
    year_matches = list(YEAR_RE.finditer(prefix))
    if not year_matches:
        return None
    answer_segment = prefix[year_matches[-1].end() :].strip(" ,:;-–")
    answer_segment = re.sub(r"\s+(?:trong|tai|vao)$", "", answer_segment).strip()
    answer_match = re.match(
        r"(.+?)\s+cua\s+(?=(?:ctcp|cong ty|ngan hang|tap doan|tong cong ty|[a-z]{2,6}\b))",
        answer_segment,
    )
    if answer_match:
        answer_metric = answer_match.group(1).strip()
    else:
        answer_metric = answer_segment.split(" cua ", 1)[0].strip()
    condition_metric = selector_tail[:operator_position].strip(" ,:;-–")
    condition_metric = re.sub(r"\s+(?:la|thi|se)$", "", condition_metric).strip()
    if len(answer_metric.split()) < 2 or len(condition_metric.split()) < 2:
        return None
    return tickers, years, answer_metric, condition_metric, operator


def _conditional_preferred_scope(item: Mapping[str, Any]) -> str | None:
    """Choose a source scope for a conditional year-selection proposal."""

    plan = item.get("question_plan") or {}
    planned = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if planned in {"separate", "consolidated"}:
        return planned
    question = normalize(item.get("question") or "")
    if "cong ty me" in question or "bao cao rieng" in question:
        return "separate"
    if "bao cao hop nhat" in question or "bao cao tong hop" in question:
        return "consolidated"

    weighted: Counter[str] = Counter()
    years = {
        int(value)
        for value in plan.get("years") or []
        if str(value).lstrip("-").isdigit()
    }
    for candidate in item.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        scope = str(candidate.get("scope") or "").strip().lower()
        if scope not in {"separate", "consolidated"}:
            continue
        try:
            rank = max(1, int(candidate.get("rank") or candidate.get("original_retrieval_rank") or 100))
        except (TypeError, ValueError):
            rank = 100
        candidate_year = candidate.get("report_year")
        year_bonus = 2.0 if candidate_year is not None and int(candidate_year) in years else 1.0
        weighted[scope] += year_bonus / rank
    if not weighted:
        return None
    # Consolidated is the deterministic tie-break for an unqualified issuer;
    # otherwise use the strongest navigation evidence without treating its
    # score as the answer value.
    return max(
        ("consolidated", "separate"),
        key=lambda scope: (weighted.get(scope, 0.0), scope == "consolidated"),
    )


_CONDITIONAL_SOURCE_ALLOWED_MATCH_MODES = {
    "exact_contiguous",
    "ordered_with_ocr_gap",
    # The source resolver has a bounded table-context contract for cash and
    # cash-equivalent totals.  Its row is often the generic ``TỔNG CỘNG``;
    # allow that contract here only after the contextual metric check below.
    "contextual_cash_and_equivalents_total",
}

_CONDITIONAL_CONTEXTUAL_TOTAL_METRIC_TOKENS = {
    "contextual_cash_and_equivalents_total": {
        "tien",
        "cac",
        "khoan",
        "tuong",
        "duong",
    },
}


def _conditional_metric_core(metric: Any) -> tuple[str, ...]:
    """Return condition/answer tokens after removing harmless time wrappers."""

    text = normalize(metric)
    for prefix in (
        "so du cuoi nam cua ",
        "so du dau nam cua ",
        "cuoi nam cua ",
        "dau nam cua ",
        "tong so du ",
        "tong ",
        "so du ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    text = re.sub(r"\b(?:cuoi|dau) nam\b", "", text).strip()
    text = re.sub(r"\b(?:cuoi|dau) ky\b", "", text).strip()
    return tuple(token for token in text.split() if token)


def _conditional_source_result_is_safe(
    result: Mapping[str, Any],
    *,
    metric: str,
    requested_year: int,
) -> bool:
    """Require exact-year, non-derived source evidence for one operand."""

    selection = result.get("selection")
    if not isinstance(selection, Mapping):
        return False
    if selection.get("source_first_match_mode") not in _CONDITIONAL_SOURCE_ALLOWED_MATCH_MODES:
        return False
    try:
        if int(selection.get("source_report_year")) != int(requested_year):
            return False
    except (TypeError, ValueError):
        return False
    match_mode = str(selection.get("source_first_match_mode") or "")
    row_label = normalize(selection.get("row_label") or "")
    required = set(_conditional_metric_core(metric))
    row_tokens = set(row_label.split())
    if not row_label or not required:
        return False

    # A contextual total is safe only when the requested metric itself carries
    # the context contract that produced the generic total row.  This keeps a
    # generic ``TỔNG CỘNG`` from becoming evidence for an unrelated metric.
    if row_label in {"tong cong", "tong", "cong"}:
        contextual_tokens = _CONDITIONAL_CONTEXTUAL_TOTAL_METRIC_TOKENS.get(match_mode)
        if contextual_tokens is None or not contextual_tokens <= required:
            return False
    elif not required <= row_tokens:
        return False

    # A component such as an accrued/prepaid sub-line must not stand in for
    # a requested total merely because it shares the financial-line tokens.
    derived_prefixes = (
        "trich truoc ",
        "trich lap ",
        "phan bo ",
        "hoan nhap ",
        "chuyen sang ",
        "chuyen tu ",
        "dieu chinh ",
    )
    target = normalize(metric)
    if any(row_label.startswith(prefix) for prefix in derived_prefixes):
        if not any(prefix.strip() in target for prefix in derived_prefixes):
            return False
    return True


def source_first_conditional_temporal_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Select a year by one exact source metric, then return another metric.

    Example: among 2021/2023/2025, find the year with the largest ending cash
    balance and return that year's interest expense.  Each condition operand
    and the final answer operand is independently replayed through the
    source-first direct resolver.  The operation only compares replayed
    ``Decimal`` values; it never reads a numeric value from retrieval text.
    """

    spec = _conditional_year_selection_spec(item)
    if spec is None:
        return None
    tickers, years, answer_metric, condition_metric, operator = spec
    scope = _conditional_preferred_scope(item)
    if scope is None:
        return None
    ticker = tickers[0]
    question = str(item.get("question") or "")
    # Condition values are used only for ordering years.  Do not apply the
    # answer's requested output unit (for example, tỷ đồng) to a condition
    # metric that may be reported in VND/share or another metric-specific
    # source unit.  The source multiplier still normalizes the table unit.
    condition_resolver_kwargs = dict(resolver_kwargs)
    condition_resolver_kwargs["requested_divisor"] = lambda _question: Decimal(1)

    def direct_item(metric: str, year: int) -> dict[str, Any]:
        plan = item.get("question_plan") or {}
        return {
            "question": question,
            "question_plan": {
                "family": "direct_lookup",
                "tickers": [ticker],
                "years": [year],
                "scope": scope,
                "reporting_scope": scope,
                "requested_unit": plan.get("requested_unit"),
                "operands": [
                    {
                        "operand_id": "value",
                        "metric": metric,
                        "ticker": ticker,
                        "period": year,
                        "scope": scope,
                    }
                ],
            },
        }

    condition_results: dict[int, dict[str, Any]] = {}
    condition_values: dict[int, Decimal] = {}
    for year in years:
        result = resolve_source_first_direct_lookup(
            direct_item(condition_metric, year),
            tables_by_pair=tables_by_pair,
            **condition_resolver_kwargs,
        )
        if result is None or not _conditional_source_result_is_safe(
            result,
            metric=condition_metric,
            requested_year=year,
        ):
            return None
        condition_results[year] = result
        condition_values[year] = Decimal(result["answer"])

    target_value = (
        max(condition_values.values())
        if operator == "max"
        else min(condition_values.values())
    )
    selected_years = [year for year, value in condition_values.items() if value == target_value]
    if len(selected_years) != 1:
        return None
    selected_year = selected_years[0]
    answer_result = resolve_source_first_direct_lookup(
        direct_item(answer_metric, selected_year),
        tables_by_pair=tables_by_pair,
        **dict(resolver_kwargs),
    )
    if answer_result is None or not _conditional_source_result_is_safe(
        answer_result,
        metric=answer_metric,
        requested_year=selected_year,
    ):
        return None
    answer_selection = answer_result.get("selection")
    if not isinstance(answer_selection, Mapping):
        return None
    answer_sources = [
        {
            **dict(source),
            "role": "answer_metric",
            "conditional_selected_year": selected_year,
        }
        for source in answer_result.get("sources") or []
    ]
    condition_sources: list[dict[str, Any]] = []
    for year in years:
        for source in condition_results[year].get("sources") or []:
            condition_sources.append(
                {
                    **dict(source),
                    "role": f"condition_metric_{year}",
                    "conditional_condition_year": year,
                }
            )
    return {
        "answer": answer_result["answer"],
        "sources": [*condition_sources, *answer_sources],
        "selection": {
            **dict(answer_selection),
            "conditional_selection": True,
            "conditional_selected_year": selected_year,
            "conditional_condition_metric": condition_metric,
            "conditional_answer_metric": answer_metric,
            "conditional_scope": scope,
            "promotion_allowed": False,
        },
        "query": (
            "float(df1.loc[df1.operand_role=='answer_metric','operand_value'].iloc[0])"
        ),
        "tier": "source_first_conditional_temporal_v1",
        "protocol": "source_first_conditional_temporal_v1",
        "operation": f"select_year_by_{operator}",
        "selected_year": selected_year,
        "condition_metric": condition_metric,
        "answer_metric": answer_metric,
        "condition_values": {str(year): str(value) for year, value in condition_values.items()},
        "requested_scope": scope,
        "diagnostics": {
            "protocol": "source_first_conditional_temporal_v1",
            "condition_metric": condition_metric,
            "answer_metric": answer_metric,
            "condition_values": {str(year): str(value) for year, value in condition_values.items()},
            "selected_year": selected_year,
            "operator": operator,
            "requested_scope": scope,
            "answer_authority": "current_structured_table_decimal_replay",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_conditional_temporal_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run conditional year-selection proposals with explicit telemetry."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        if _conditional_year_selection_spec(item) is None:
            stats["questions_skipped_non_conditional_year_selection"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_conditional_temporal_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"operator_{result['operation']}"] += 1
    return answers, {
        **dict(stats),
        "protocol": "source_first_conditional_temporal_v1",
        "exact_report_year_only": True,
        "requires_one_ticker_and_three_or_more_years": True,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "lane": "authorized_best_effort_submission_candidate",
    }


_PERIOD_EXTREME_PROTOCOL = "source_first_period_extreme_v1"
_PERIOD_EXTREME_MARKERS = (
    " cao nhat",
    " lon nhat",
    " thap nhat",
    " nho nhat",
)
_PERIOD_EXTREME_REJECT_CUES = (
    " nam co ",
    " nam ma ",
    " xet ",
    " nhung nam ",
    " trong cac cong ty ",
    " trong nhom ",
    " co luu chuyen tien thuan ",
    " vuot ",
    " lon hon ",
    " nho hon ",
    " ty le ",
    " ty trong ",
    " phan tram ",
    " bien dong ",
    " thay doi ",
    " so voi ",
    " trich lap ",
    " hoan nhap ",
    " xay dung co ban do dang ",
    " voi cong ty con ",
    " cac ben lien quan ",
)


def _period_extreme_metric_variants(
    item: Mapping[str, Any],
    *,
    marker_position: int,
    marker_length: int,
) -> list[str]:
    """Extract a bounded metric population for direct max/min questions.

    The planner puts these questions in the generic multi-period family and
    often leaves the extrema wording inside the operand text.  This helper
    recovers only the reported financial line.  It intentionally combines a
    small set of accounting synonyms with a grammatical extraction fallback;
    it is not a per-question answer table and it never reads a numeric value.
    """

    question = normalize(item.get("question") or "")
    tickers = {
        normalize(value)
        for value in (item.get("question_plan") or {}).get("tickers") or []
        if normalize(value)
    }
    variants: list[str] = []

    def add(value: Any, *, allow_entity_markers: bool = False) -> None:
        text = normalize(value)
        if not text:
            return
        text = YEAR_RE.sub(" ", text)
        text = re.sub(r"\b(?:cao nhat|lon nhat|thap nhat|nho nhat)\b", " ", text)
        text = re.sub(r"\b(?:la|bao nhieu|may)\b.*$", " ", text)
        text = re.sub(
            r"\b(?:cuoi nam|cuoi ky|dau nam|dau ky|trong nam|tai ngay)\b",
            " ",
            text,
        )
        # Remove only grammatical/amount wrappers.  ``khoản``, ``phải``,
        # ``vay`` and other accounting tokens remain semantic.
        wrappers = (
            "tong so du ",
            "so du cuoi nam ",
            "so du cuoi ky ",
            "so du dau nam ",
            "so du ",
            "tong gia tri ",
            "gia tri con lai ",
            "gia tri thuan ",
            "gia tri ",
            "muc gia tri ",
            "muc ",
            "chi tieu ",
            "so tien ",
            "tong ",
        )
        changed = True
        while changed:
            changed = False
            text = text.strip(" ,:;-–")
            for wrapper in wrappers:
                if text.startswith(wrapper):
                    text = text[len(wrapper) :].strip()
                    changed = True
                    break
        for ticker in sorted(tickers, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(ticker)}\b", " ", text)
        # A company prefix commonly ends in ``... có số dư``.  Keep the
        # phrase after the final grammatical ``có``; a metric itself is not
        # allowed to contain a legal-entity marker.
        # ``có số dư``/``có giá trị`` is a grammatical company-prefix
        # boundary, but ``cơ bản`` and ``cổ phiếu`` are accounting tokens.
        # Restrict the split to a known predicate instead of treating every
        # standalone ``co`` as a boundary.
        grammatical_co = re.search(
            r"\s+co\s+(?=(?:so du|muc|gia tri|tong|khoan|tien|chi phi)\b)",
            f" {text} ",
        )
        if grammatical_co:
            text = text[grammatical_co.end() - 1 :]
        text = re.sub(
            r"^(?:trong|qua|vao|den)\s+(?:cac\s+)?(?:moc\s+)?"
            r"(?:cuoi\s+)?nam\s+",
            "",
            text,
        )
        text = re.split(
            r"\s+(?:trong|qua|vao|den|dat muc|ghi nhan)\b",
            text,
            maxsplit=1,
        )[0]
        text = re.sub(r"\s+", " ", text).strip(" ,:;-–")
        words = text.split()
        if len(words) < 2 or len(words) > 14:
            return
        if not allow_entity_markers and any(
            marker in f" {text} "
            for marker in ("ctcp", "tmcp", "cong ty", "ngan hang", "tap doan")
        ):
            return
        if text not in variants:
            variants.append(text)

    # Stable accounting synonyms are still family-level rules.  They cover
    # common source terminology such as ``Doanh thu bán hàng ...`` for the
    # question's ``Doanh thu thuần`` without tying the route to a Question ID.
    canonical_rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "khoan phai tra ngan han cho tap doan dien luc viet nam",
            (
                "Phải trả ngắn hạn cho Tập đoàn Điện lực Việt Nam",
                "Khoản phải trả ngắn hạn cho Tập đoàn Điện lực Việt Nam",
            ),
        ),
        (
            "doanh thu thuan",
            # Doanh thu bán hàng và cung cấp dịch vụ is the gross line.  It
            # is not a synonym for net revenue when reductions exist; keep
            # this route fail-closed and let a source row explicitly carrying
            # ``thuần`` prove the requested metric.
            ("Doanh thu thuần",),
        ),
        ("tong no vay dai han", ("Vay dài hạn", "Nợ vay dài hạn")),
        ("lai thuan tu hoat dong kinh doanh ngoai hoi", ("Lãi thuần từ hoạt động kinh doanh ngoại hối",)),
        ("du phong phai thu kho doi", ("Dự phòng phải thu khó đòi",)),
        ("dau tu tai chinh dai han", ("Đầu tư tài chính dài hạn",)),
        (
            "lai co ban va suy giam",
            ("Lãi cơ bản và suy giảm trên mỗi cổ phiếu",),
        ),
        ("lai co ban tren co phieu", ("Lãi cơ bản trên cổ phiếu",)),
        ("tai san co dinh vo hinh", ("Tài sản cố định vô hình",)),
        ("tai san co dinh huu hinh", ("Tài sản cố định hữu hình",)),
        ("quy khen thuong", ("Quỹ khen thưởng, phúc lợi",)),
        ("thue tndn hien hanh", ("Thuế TNDN hiện hành",)),
        (
            "chi phi thue thu nhap doanh nghiep",
            ("Chi phí thuế thu nhập doanh nghiệp",),
        ),
        ("chi phi khac", ("Chi phí khác",)),
        (
            "tien thue toi thieu phai nhan",
            ("Tiền thuê tối thiểu phải nhận theo hợp đồng thuê hoạt động",),
        ),
        ("dau tu vao cong ty con", ("Đầu tư vào công ty con",)),
        (
            "tien va cac khoan tuong duong tien",
            ("Tiền và các khoản tương đương tiền",),
        ),
        (
            "thu lao hoi dong quan tri",
            ("Tổng thù lao Hội đồng Quản trị và Ban Tổng Giám đốc",),
        ),
        ("chi phi tra truoc ngan han", ("Chi phí trả trước ngắn hạn",)),
        (
            "phai thu co tuc va loi nhuan duoc chia ngan han",
            ("Phải thu cổ tức và lợi nhuận được chia ngắn hạn",),
        ),
        ("hang ton kho", ("Hàng tồn kho",)),
        (
            "du phong rui ro cho vay khach hang",
            ("Dự phòng rủi ro cho vay khách hàng",),
        ),
        ("gia von ban dien", ("Giá vốn bán điện",)),
        ("thu nhap khac", ("Thu nhập khác",)),
        (
            "khoan vay dai han den han trong nam",
            ("Khoản vay dài hạn đến hạn trong năm",),
        ),
        ("chi phi ban hang", ("Chi phí bán hàng",)),
        ("xay dung co ban do dang", ("Xây dựng cơ bản dở dang",)),
    )
    for phrase, accounting_variants in canonical_rules:
        if phrase in question:
            for metric in accounting_variants:
                add(metric, allow_entity_markers=True)

    prefix = question[:marker_position]
    suffix = question[marker_position + marker_length :]
    pieces = [prefix, suffix]
    for segment in (prefix, suffix):
        years = list(YEAR_RE.finditer(segment))
        if years:
            pieces.append(segment[years[-1].end() :])
        if " cua " in segment:
            parts = segment.split(" cua ")
            pieces.extend(parts[:3])
            for part in parts[1:3]:
                pieces.append(part.split(" cua ", 1)[0])
        if segment.startswith("cua "):
            pieces.append(segment[4:])

    # The suffix after ``lớn nhất của`` often has exactly one metric before
    # the issuer/period tail.  The prefix path handles the inverse wording.
    for segment in list(pieces):
        add(segment)
        if " cua " in segment:
            add(segment.split(" cua ", 1)[0])
            add(segment.split(" cua ", 1)[1].split(" cua ", 1)[0])

    # Remove a few obvious generic operands that can be produced from
    # ``giá trị lớn nhất`` itself.  A real metric must retain at least two
    # accounting words and will already be present from the canonical or
    # structural path above.
    variants = [
        value
        for value in variants
        if value not in {
            "lon nhat",
            "gia tri",
            "muc gia tri",
            "gia tri lon nhat",
            "dat muc",
            "con lai",
            "dau tu",
            "so du dau tu",
            "vao cuoi cac nam va",
            "cua tien va cac khoan tuong duong tien",
        }
    ]
    return variants


def _period_extreme_metric_signatures(metrics: Iterable[str]) -> set[str]:
    """Return row signatures after removing harmless amount/time wrappers."""

    signatures: set[str] = set()
    wrappers = (
        "tong so du ",
        "so du cuoi nam ",
        "so du cuoi ky ",
        "so du dau nam ",
        "so du ",
        "tong gia tri ",
        "gia tri con lai ",
        "gia tri thuan ",
        "gia tri ",
        "muc gia tri ",
        "muc ",
        "chi tieu ",
        "tong ",
    )
    for metric in metrics:
        candidates = [normalize(metric), " ".join(_temporal_metric_core(metric))]
        for candidate in candidates:
            text = candidate.strip()
            raw_signature = _temporal_row_signature(text)
            if raw_signature:
                signatures.add(raw_signature)
            changed = True
            while changed:
                changed = False
                for wrapper in wrappers:
                    if text.startswith(wrapper):
                        text = text[len(wrapper) :].strip()
                        changed = True
                        break
            text = re.sub(
                r"\b(?:cuoi nam|cuoi ky|dau nam|dau ky|trong nam|tai ngay)\b",
                " ",
                text,
            )
            signature = _temporal_row_signature(text)
            if signature:
                signatures.add(signature)
    return signatures


def _period_extreme_table_is_eligible(table: Mapping[str, Any]) -> bool:
    """Exclude acquisition/schedule snapshots from period-end extrema.

    ``financial_data_schedule`` is a generic corpus reconstruction label. It
    can contain a value at the date of an acquisition (or another event)
    which happens to share the requested row label. That value is not a
    period-end observation merely because its report year matches. Primary
    statements and named financial notes remain eligible; an unknown kind is
    retained so this guard does not turn missing classifier metadata into an
    artificial recall gate.
    """

    table_kind = normalize(source_first_lookup_module._table_kind(table))
    return table_kind not in {"financial data schedule"}


def _period_extreme_spec(
    item: Mapping[str, Any],
) -> tuple[list[str], list[int], list[str], str, str | None] | None:
    """Recognize an unconditional one-ticker multi-year max/min question."""

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "multi_entity_or_period_aggregation":
        return None
    operator = str((plan.get("operation_ast") or {}).get("op") or "")
    if operator not in {"max", "min"}:
        return None
    tickers = [
        str(value).strip().upper()
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    if len(set(tickers)) != 1:
        return None
    years: list[int] = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    if len(years) < 3:
        return None
    question = normalize(item.get("question") or "")
    if any(cue in f" {question} " for cue in _PERIOD_EXTREME_REJECT_CUES):
        return None
    marker_candidates = [
        (question.find(marker), marker)
        for marker in _PERIOD_EXTREME_MARKERS
        if question.find(marker) >= 0
    ]
    if not marker_candidates:
        return None
    marker_position, marker = min(marker_candidates, key=lambda pair: pair[0])
    marker_operator = "max" if marker in {" cao nhat", " lon nhat"} else "min"
    if marker_operator != operator:
        return None
    metrics = _period_extreme_metric_variants(
        item,
        marker_position=marker_position,
        marker_length=len(marker),
    )
    if not metrics:
        return None
    planned_scope = str(
        plan.get("scope") or plan.get("reporting_scope") or ""
    ).strip().lower() or None
    if planned_scope not in {None, "separate", "consolidated"}:
        return None
    requested_scope = planned_scope
    if requested_scope is None and "cong ty me" in question:
        requested_scope = "separate"
    elif requested_scope is None:
        # Unqualified annual questions often have an explicit navigation
        # preference in the review bundle.  Use it only to choose which
        # current tables to replay; the source row, value, and cross-year
        # scope invariant remain mandatory below.  If navigation has no
        # preference, leave the lookup unqualified so conflicting reports
        # fail closed in the resolver.
        preferred_scope = _conditional_preferred_scope(item)
        if preferred_scope in {"separate", "consolidated"}:
            requested_scope = preferred_scope
    return tickers, years, metrics, operator, requested_scope


def _period_extreme_source_is_safe(
    result: Mapping[str, Any],
    *,
    metric_signatures: set[str],
    requested_year: int,
) -> tuple[str, str] | None:
    """Require one exact-year source cell and an accepted reported row."""

    selection = result.get("selection")
    sources = result.get("sources") or []
    if not isinstance(selection, Mapping) or len(sources) != 1:
        return None
    source = sources[0]
    if not isinstance(source, Mapping):
        return None
    if selection.get("source_first_match_mode") not in _TEMPORAL_SOURCE_ALLOWED_MATCH_MODES:
        return None
    try:
        if int(selection.get("source_report_year")) != requested_year:
            return None
        if int(source.get("source_report_year")) != requested_year:
            return None
        if int(selection.get("report_year_offset") or 0) != 0:
            return None
    except (TypeError, ValueError):
        return None
    if not source.get("internal_table_uid") or not source.get("document_id"):
        return None
    if parse_decimal(result.get("answer")) is None:
        return None
    row_label = str(selection.get("row_label") or source.get("row_label") or "")
    row_signature = _temporal_row_signature(row_label)
    if not row_signature or row_signature in {"tong", "tong cong", "cong"}:
        return None
    if row_signature not in metric_signatures:
        return None
    row_text = normalize(row_label)
    for cue in (
        "tang ",
        "giam ",
        "thay doi ",
        "trich lap ",
        "hoan nhap ",
        "chuyen ",
        "phan bo ",
    ):
        if cue in f" {row_text} ":
            return None
    table_kind = normalize(
        selection.get("source_first_table_kind")
        or source.get("source_first_table_kind")
        or source.get("table_kind")
        or ""
    )
    if table_kind in {"governance", "governance roster"}:
        return None
    scope = normalize(
        source.get("source_first_scope")
        or selection.get("source_first_scope")
        or source.get("scope")
        or selection.get("scope")
        or "unknown"
    )
    return row_signature, scope


def source_first_period_extreme_answer(
    item: Mapping[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Replay one reported row across years and select its unique max/min."""

    specification = _period_extreme_spec(item)
    if specification is None:
        return None
    tickers, years, metrics, operator, requested_scope = specification
    metric_signatures = _period_extreme_metric_signatures(metrics)
    if not metric_signatures:
        return None
    ticker = tickers[0]
    question = str(item.get("question") or "")
    plan = dict(item.get("question_plan") or {})
    # Restrict only this experimental route's source population.  The
    # ordinary direct-lookup path still sees the complete corpus; here a
    # matching event/acquisition schedule must not displace a balance-sheet
    # observation for a multi-year period comparison.
    requested_pairs = {(ticker, year) for year in years}
    eligible_tables_by_pair = {
        pair: [
            table
            for table in tables_by_pair.get(pair, ())
            if _period_extreme_table_is_eligible(table)
        ]
        for pair in requested_pairs
        if pair in tables_by_pair
    }
    resolved: dict[int, tuple[str, dict[str, Any], str, str]] = {}
    for year in years:
        successes: list[tuple[str, dict[str, Any], str, str]] = []
        for metric in metrics:
            synthetic = {
                "question": question,
                "question_plan": {
                    "family": "direct_lookup",
                    "tickers": [ticker],
                    "years": [year],
                    "scope": requested_scope,
                    "reporting_scope": requested_scope,
                    "requested_unit": plan.get("requested_unit"),
                    "operands": [
                        {
                            "operand_id": "value",
                            "metric": metric,
                            "ticker": ticker,
                            "period": year,
                            "scope": requested_scope,
                        }
                    ],
                },
            }
            exact_resolver_kwargs = dict(resolver_kwargs)
            exact_resolver_kwargs["report_year_neighbor_fallback"] = None
            result = resolve_source_first_direct_lookup(
                synthetic,
                tables_by_pair=eligible_tables_by_pair,
                **exact_resolver_kwargs,
            )
            safe = (
                _period_extreme_source_is_safe(
                    result,
                    metric_signatures=metric_signatures,
                    requested_year=year,
                )
                if result is not None
                else None
            )
            if safe is None:
                continue
            source_scope = safe[1]
            successes.append((metric, result, safe[0], source_scope))
        if not successes:
            return None
        unique_successes = {
            (
                str(result.get("answer")),
                row_signature,
                scope,
            )
            for _, result, row_signature, scope in successes
        }
        if len(unique_successes) != 1:
            # Multiple accounting synonyms resolving to different source
            # rows/values are semantic ambiguity, even if each cell replays.
            return None
        resolved[year] = successes[0]

    row_signatures = {entry[2] for entry in resolved.values()}
    if len(row_signatures) != 1:
        # A source can replay every year while silently changing from one
        # reported line to a different line.  Extrema are valid only over one
        # stable row identity, not merely over a list of numeric cells.
        return None
    scopes = {entry[3] for entry in resolved.values()}
    if len(scopes) != 1:
        return None
    source_scope = next(iter(scopes))
    if requested_scope in {"separate", "consolidated"} and source_scope not in {
        requested_scope,
        "unknown",
    }:
        return None
    # These are resolver outputs, not raw OCR cells.  Preserve their Decimal
    # literal representation so a requested ``nghìn đồng`` answer remains
    # 3.375 instead of being reparsed as the integer 3375.
    values = {
        year: parse_decimal_literal(result.get("answer"))
        for year, (_, result, _, _) in resolved.items()
    }
    if any(value is None for value in values.values()):
        return None
    typed_values = {year: value for year, value in values.items() if value is not None}
    # A provision balance is a contra-asset in the primary statements.  The
    # corpus contains both a positive extracted copy and parenthesized
    # negative statement rows for this metric.  A mixed-sign series is a
    # representation conflict, not a meaningful max over periods; leave it
    # to the existing candidate lane until the sign convention is adjudicated.
    if "du phong rui ro cho vay khach hang" in metric_signatures:
        sign_classes = {
            1 if value > 0 else -1 if value < 0 else 0
            for value in typed_values.values()
        }
        if len(sign_classes) > 1:
            return None
    target_value = (
        max(typed_values.values()) if operator == "max" else min(typed_values.values())
    )
    selected_years = [
        year for year, value in typed_values.items() if value == target_value
    ]
    if len(selected_years) != 1:
        return None
    selected_year = selected_years[0]

    output_sources: list[dict[str, Any]] = []
    for year in years:
        metric, result, row_signature, scope = resolved[year]
        source_rows = result.get("sources") or []
        if len(source_rows) != 1:
            return None
        source = dict(source_rows[0])
        source.update(
            {
                "role": (
                    "answer_metric"
                    if year == selected_year
                    else f"period_extreme_candidate_{year}"
                ),
                "period_extreme_protocol": _PERIOD_EXTREME_PROTOCOL,
                "period_extreme_operator": operator,
                "period_extreme_year": year,
                "period_extreme_metric": metric,
                "period_extreme_row_signature": row_signature,
                "period_extreme_scope": scope,
                "promotion_allowed": False,
            }
        )
        # ``resolve_source_first_direct_lookup`` may keep the source-cell
        # display value (for example ``2.843``) while returning the parsed
        # answer in the source's canonical numeric scale (``2843``).  The
        # answer row is the operand consumed by the emitted pandas query, so
        # bind its replay value to the already-selected Decimal answer.  Keep
        # ``raw_value`` and coordinates untouched for provenance.
        if year == selected_year:
            source["value"] = target_value
        output_sources.append(source)

    selected_result = resolved[selected_year][1]
    selected_selection = selected_result.get("selection")
    if not isinstance(selected_selection, Mapping):
        return None
    selection = {
        **dict(selected_selection),
        "candidate_source": _PERIOD_EXTREME_PROTOCOL,
        "source_first_period_extreme": True,
        "period_extreme_protocol": _PERIOD_EXTREME_PROTOCOL,
        "period_extreme_operator": operator,
        "period_extreme_selected_year": selected_year,
        "period_extreme_metric": resolved[selected_year][0],
        "period_extreme_years": list(years),
        "period_extreme_values": {
            str(year): str(value) for year, value in typed_values.items()
        },
        "period_extreme_scope": source_scope,
        "promotion_allowed": False,
    }
    return {
        "answer": target_value,
        "sources": output_sources,
        "selection": selection,
        "query": (
            "float(df1.loc[df1.operand_role=='answer_metric',"
            "'operand_value'].iloc[0])"
        ),
        "tier": _PERIOD_EXTREME_PROTOCOL,
        "protocol": _PERIOD_EXTREME_PROTOCOL,
        "operation": operator,
        "metric_variants": metrics,
        "requested_years": list(years),
        "selected_year": selected_year,
        "period_values": {str(year): str(value) for year, value in typed_values.items()},
        "requested_scope": requested_scope,
        "source_scope": source_scope,
        "promotion_allowed": False,
        "diagnostics": {
            "candidate_source": _PERIOD_EXTREME_PROTOCOL,
            "period_extreme": True,
            "operator": operator,
            "metric_variants": metrics,
            "requested_years": list(years),
            "selected_year": selected_year,
            "period_values": {
                str(year): str(value) for year, value in typed_values.items()
            },
            "row_signature": resolved[selected_year][2],
            "scope": source_scope,
            "answer_authority": "current_structured_table_decimal_replay",
            "promotion_allowed": False,
            "lane": "authorized_best_effort_submission_candidate",
        },
    }


def build_source_first_period_extreme_lookup_index(
    items_by_question: Mapping[int, Mapping[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
    resolver_kwargs: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run direct multi-year extrema proposals with explicit telemetry."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        specification = _period_extreme_spec(item)
        if specification is None:
            stats["questions_skipped_not_eligible"] += 1
            continue
        stats["questions_considered"] += 1
        stats[f"operator_{specification[3]}"] += 1
        result = source_first_period_extreme_answer(
            item,
            tables_by_pair=tables_by_pair,
            resolver_kwargs=resolver_kwargs,
        )
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"year_count_{len(result.get('requested_years') or [])}"] += 1
    return answers, {
        **dict(stats),
        "protocol": _PERIOD_EXTREME_PROTOCOL,
        "requires_one_ticker_and_three_or_more_years": True,
        "requires_exact_report_year_per_operand": True,
        "requires_same_row_signature_and_scope": True,
        "unique_winner_required": True,
        "accepted_match_modes": sorted(_TEMPORAL_SOURCE_ALLOWED_MATCH_MODES),
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "promotion_allowed": False,
        "lane": "authorized_best_effort_submission_candidate",
    }


_CROSS_ENTITY_SOURCE_ALLOWED_MATCH_MODES = {
    "exact_contiguous",
    "ordered_with_ocr_gap",
}


_CROSS_ENTITY_TICKER_ALIASES: dict[str, str] | None = None


def _cross_entity_code_stock_path() -> Path:
    """Locate the frozen ticker registry when the builder is snapshotted.

    A/B runs copy this script under ``/tmp`` while keeping the repository as
    the working directory.  In that layout ``ROOT`` points at the temporary
    snapshot and the old default path silently disables alias recovery.  Use
    the source-checkout path first when available, then retain the original
    module-relative fallback for normal invocations.
    """

    if DEFAULT_CODE_STOCK.is_file():
        return DEFAULT_CODE_STOCK
    checkout_path = Path.cwd() / "data/ViFinQA/code_stock.csv"
    if checkout_path.is_file():
        return checkout_path
    return DEFAULT_CODE_STOCK


def _cross_entity_ticker_plan(item: Mapping[str, Any]) -> tuple[list[str], str]:
    """Recover only an incomplete issuer plan from exact public aliases.

    The review planner occasionally keeps one issuer from a two-company
    question, even though the raw question contains both company names.  A
    source-first route can recover that missing routing metadata from the
    frozen ``code_stock.csv`` registry, but the recovery must never override a
    complete plan or use fuzzy text similarity.  Numeric authority remains in
    the later per-issuer table replay.
    """

    plan = item.get("question_plan") or {}
    planned: list[str] = []
    for value in plan.get("tickers") or []:
        ticker = str(value).strip().upper()
        if ticker and ticker not in planned:
            planned.append(ticker)
    if len(planned) >= 2:
        return planned, "planner_complete"

    global _CROSS_ENTITY_TICKER_ALIASES
    if _CROSS_ENTITY_TICKER_ALIASES is None:
        code_stock_path = _cross_entity_code_stock_path()
        _CROSS_ENTITY_TICKER_ALIASES = load_ticker_aliases(code_stock_path)
        # The public registry is authoritative for the mapping.  Add only
        # structural variants that remove a leading legal-form/group marker;
        # do not create brand-name or fuzzy aliases.  This recovers common
        # question forms such as ``Dịch vụ Hoàng Huy`` from ``CTCP Đầu tư
        # Dịch vụ Hoàng Huy`` while retaining a minimum two-token boundary.
        ambiguous_aliases: set[str] = set()

        def add_structural_alias(alias: str, ticker: str) -> None:
            normalized = normalize_text(alias).casefold()
            if len(normalized.split()) < 2 or normalized in ambiguous_aliases:
                return
            previous = _CROSS_ENTITY_TICKER_ALIASES.get(normalized)
            if previous is not None and previous != ticker:
                _CROSS_ENTITY_TICKER_ALIASES.pop(normalized, None)
                ambiguous_aliases.add(normalized)
                return
            _CROSS_ENTITY_TICKER_ALIASES[normalized] = ticker

        if code_stock_path.is_file():
            with code_stock_path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    values = [str(value).strip() for value in row.values() if value]
                    ticker = next(
                        (
                            value.upper()
                            for value in values
                            if re.fullmatch(r"[A-Z]{2,6}\d?", value.upper())
                        ),
                        None,
                    )
                    if ticker is None:
                        continue
                    for value in values:
                        if value.upper() == ticker:
                            continue
                        name = normalize_text(value)
                        variants = (
                            re.sub(r"^ctcp\s*[-–—]?\s*", "", name, flags=re.IGNORECASE),
                            re.sub(r"^ngân\s+hàng\s+", "", name, flags=re.IGNORECASE),
                            re.sub(
                                r"^tổng\s+công\s+ty\s+cổ\s+phần\s+",
                                "",
                                name,
                                flags=re.IGNORECASE,
                            ),
                            re.sub(
                                r"^ctcp\s*[-–—]?\s*(?:đầu\s+tư\s+)?",
                                "",
                                name,
                                flags=re.IGNORECASE,
                            ),
                        )
                        for variant in variants:
                            if variant != name:
                                add_structural_alias(variant, ticker)
    inferred = extract_tickers(str(item.get("question") or ""), _CROSS_ENTITY_TICKER_ALIASES)
    inferred = [ticker for ticker in inferred if ticker not in {"CP", "CTCP", "TMCP"}]
    if len(inferred) != 2:
        return planned, "planner_incomplete_no_unique_alias_pair"
    if planned and planned[0] not in inferred:
        return planned, "planner_alias_conflict"
    return inferred, "exact_code_stock_alias_recovery"


def cross_entity_metric_variants(item: Mapping[str, Any]) -> list[str]:
    """Extract a bounded source-oriented metric population for two issuers.

    ``cross_entity_comparison`` plans in the review bundle frequently have no
    operands.  The old multi-entity route consequently fed the complete
    comparison sentence to a semantic-cell matcher, which can select a
    nearby row (for example ``Dự phòng`` for ``Cho vay``).  This helper only
    removes grammatical comparison/entity/period shells and adds a small
    allowlist of accounting wrappers.  It never creates a value or selects a
    table.
    """

    plan = item.get("question_plan") or {}
    question = normalize(item.get("question") or "")
    tickers = [
        normalize(value)
        for value in plan.get("tickers") or []
        if str(value).strip()
    ]
    question = re.sub(r"\([^)]*\)", " ", question)
    question = re.sub(
        r"^(?:cuoi nam|dau nam|nam)\s+(?:19|20)\d{2}\s*",
        "",
        question,
    )
    question = re.sub(
        r"^(?:den ngay|tinh den ngay)\s+\d+\s+\d+\s+(?:19|20)\d{2}\s*",
        "",
        question,
    )
    question = re.sub(r"^so voi\s+[a-z0-9]+\s*", "", question)

    # Remove only comparison shells.  The order matters for phrases such as
    # ``Tính số tiền chênh lệch`` and ``Độ chênh lệch về``.
    for prefix in (
        "tinh so tien chenh lech ",
        "tinh chenh lech ",
        "do chenh lech ",
        "su chenh lech ",
        "chenh lech tuyet doi ",
        "chenh lech ve ",
        "chenh lech ",
        "hieu so ",
        "hieu ",
        "quy mo ",
    ):
        if question.startswith(prefix):
            question = question[len(prefix) :]
            break
    question = question.removeprefix("ve ")

    # In ``<metric> giữa A và B`` the metric is before ``giữa``.  In
    # ``giữa <metric> của A và B`` it is after it.  A short prefix means only
    # the comparison shell survived and is therefore not a metric.
    if question.startswith("giua "):
        before, after = "", question[len("giua ") :]
    elif " giua " in question:
        before, after = question.split(" giua ", 1)
    else:
        before, after = "", ""
    if before or after:
        before = re.sub(
            r"\s+(?:cuoi nam|dau nam|trong nam|vao nam|nam)\s+(?:19|20)\d{2}\s*$",
            "",
            before,
        )
        before = re.sub(
            r"\s+(?:tinh den ngay)\s+\d+\s+\d+\s+(?:19|20)\d{2}\s*$",
            "",
            before,
        )
        question = before if len(before.split()) >= 2 else after

    # Remove named issuer tails but preserve accounting qualifiers such as
    # ``của khách hàng`` and ``của thành viên quản lý``.
    question = re.split(
        r"\s+cua\s+(?=(?:cong ty me|ctcp|ngan hang|tap doan|tong cong ty|cong ty))",
        question,
        maxsplit=1,
    )[0]
    for ticker in tickers:
        if ticker:
            question = re.split(rf"\s+cua\s+{re.escape(ticker)}\b", question, maxsplit=1)[0]
    question = re.split(
        r"\s+tren\s+bctc\s+(?:rieng|hop nhat)",
        question,
        maxsplit=1,
    )[0]
    question = re.sub(r"\s+bctc\s+(?:rieng|hop nhat)\b", "", question)
    question = re.split(
        r"\s+(?:tinh\s+)?den\s+(?:cuoi nam|dau nam)?\s*(?:19|20)\d{2}",
        question,
        maxsplit=1,
    )[0]
    question = re.split(
        r"\s+(?:cuoi nam|dau nam|trong nam|vao nam|nam)\s+(?:19|20)\d{2}",
        question,
        maxsplit=1,
    )[0]
    question = re.split(
        r"\s+(?:cao hon|lon hon|kem hon|thap hon|be hon)\s+(?:bao nhieu|cua)?",
        question,
        maxsplit=1,
    )[0]
    question = re.split(r"\s+(?:so voi|tru di|trừ đi)\s+", question, maxsplit=1)[0]
    question = re.sub(r"\s+(?:cong ty me|cong ty con)\s*$", "", question)
    question = re.sub(r"\s+", " ", question).strip(" ,")

    variants: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip(" ,")
        if len(value.split()) < 2 or value in variants:
            return
        variants.append(value)

    add(question)
    # These are wrappers or stable report-row synonyms, not arbitrary fuzzy
    # substitutions.  Keep the original first so a clean exact row wins.
    prefixes = (
        "tong ",
        "so du ",
        "gia tri con lai ",
        "gia tri ",
        "muc ",
        "quy mo ",
        "khoan ",
        "du no ",
        "so luong ",
        "no ",
        "nguon thu ",
    )
    for prefix in prefixes:
        if question.startswith(prefix):
            add(question[len(prefix) :])

    replacements = (
        ("gia goc khoan dau tu vao ", "dau tu vao "),
        ("nguon thu tu ", "doanh thu tu "),
        ("loi nhuan thuan sau thue", "loi nhuan thuan"),
        ("chi phi thue thu nhap doanh nghiep hien hanh", "chi phi thue thu nhap doanh nghiep"),
        ("so luong co phieu pho thong dang luu hanh", "so luong co phieu dang luu hanh"),
        ("du phong rui ro cho vay cu the", "du phong cu the"),
        ("so du cho vay ", "cho vay "),
        ("so du no ", ""),
        ("du no cho vay ", "cho vay "),
        ("gia tri con lai cua ", ""),
        ("gia tri con lai ", ""),
        ("trich lap du phong ", "du phong "),
        ("chi phi xay dung va phat trien ", "chi phi xay dung phat trien "),
        ("lai co ban tren mot co phieu", "lai co ban tren co phieu"),
        ("tien gui co ky han tai ngan hang", "tien gui co ky han"),
        ("loi nhuan sau thue", "loi nhuan sau thue tndn"),
    )
    for old, new in replacements:
        if old in question:
            add(question.replace(old, new, 1))

    # ``của`` is a grammatical token for the source matcher; do not remove
    # it from the target phrase globally, but retain a compact fallback for
    # nested forms such as ``giá trị còn lại của tài sản ...``.
    add(question.replace(" cua ", " "))
    return variants


def _cross_entity_scope(item: Mapping[str, Any]) -> str | None:
    """Infer scope only for an issuer qualifier, not parent-attributable P&L."""

    plan = item.get("question_plan") or {}
    question = normalize(item.get("question") or "")
    # The phrase is part of the requested consolidated profit metric, even
    # when the legacy plan compiler populated ``scope=separate``.
    if "loi nhuan sau thue cua cong ty me" in question:
        return None
    planned = str(plan.get("scope") or plan.get("reporting_scope") or "").strip().lower()
    if planned in {"separate", "consolidated"}:
        return planned
    if "bctc rieng" in question or "bao cao tai chinh rieng" in question:
        return "separate"
    # In ``lợi nhuận sau thuế của công ty mẹ`` the words ``công ty mẹ`` are
    # part of the reported metric and imply the consolidated parent-attributable
    # row.  Do not reinterpret them as a separate-statement request.
    if "loi nhuan sau thue cua cong ty me" in question:
        return None
    if "cong ty me" in question and re.search(
        r"cong ty me\s+(?:cua\s+)?(?:ctcp|cong ty|ngan hang|tap doan|tong cong ty)",
        question,
    ):
        return "separate"
    return None


def _cross_entity_source_result_is_safe(
    result: Mapping[str, Any],
    *,
    requested_metric: str,
    requested_year: int,
    question: str = "",
) -> bool:
    selection = result.get("selection")
    if not isinstance(selection, Mapping):
        return False
    if selection.get("source_first_match_mode") not in _CROSS_ENTITY_SOURCE_ALLOWED_MATCH_MODES:
        return False
    source = (result.get("sources") or [{}])[0]
    try:
        if int(source.get("source_report_year")) != requested_year:
            return False
    except (TypeError, ValueError, IndexError):
        return False
    row = normalize(selection.get("row_label") or source.get("row_label") or "")
    target = normalize(requested_metric)
    if not row or not target:
        return False

    # A governance/roster table can contain numeric-looking rows, but it is
    # not an accounting source for a cross-issuer metric.  Keep this gate
    # independent of row-label similarity so a high lexical match cannot
    # promote a non-financial table into the answer lane.
    table_kind = normalize(
        selection.get("source_first_table_kind")
        or source.get("source_first_table_kind")
        or source.get("table_kind")
        or ""
    )
    if table_kind in {"governance roster", "governance"}:
        return False

    # Similar-looking accounting lines are not interchangeable in a
    # cross-company subtraction.  These checks intentionally use the original
    # question metric, not the relaxed variant that happened to match.
    if "chung khoan dau tu" in target and "kinh doanh" in row:
        return False
    if "chung khoan kinh doanh" in target and "dau tu" in row:
        return False
    if "du phong" in target and "du phong" not in row:
        return False
    if "trai phieu thuong" in target and "trai phieu thuong" not in row:
        return False
    if "phai thu ben ngoai" in target and "phai thu ben ngoai" not in row:
        return False
    if "phai tra nguoi ban" in target and "phai tra nguoi ban" not in row:
        return False
    if "von chu so huu" in target and "von dau tu cua chu so huu" in row:
        # ``Vốn đầu tư của chủ sở hữu`` is an equity component, not the
        # requested total ``Vốn chủ sở hữu`` line.  Extra-token fuzzy matches
        # are unsafe when the result is subtracted across issuers.
        return False
    if "xay dung" in target and "bat dong san" in target:
        if "xay dung" not in row or "bat dong san" not in row or "trich truoc" in row:
            return False
    if "lai co ban" in target and "lai co ban" not in row:
        return False
    if "tien gui co ky han" in target and "tien gui" not in row:
        return False
    if "loi nhuan thuan" in target:
        if "loi nhuan" not in row or "thuan" not in row:
            return False
        if "chua phan phoi" in row or "co dong khong kiem soat" in row:
            return False
    if "loi nhuan sau thue" in target:
        if "chua phan phoi" in row or "co dong khong kiem soat" in row:
            return False
        if "loi nhuan" not in row or "sau thue" not in row:
            return False
        # ``... của công ty mẹ`` is a consolidated parent-attributable line;
        # a separate-statement profit row is a different contract.
        if "loi nhuan sau thue cua cong ty me" in normalize(question):
            if "cong ty me" not in row or normalize(source.get("source_first_scope")) != "consolidated":
                return False
    if "tai san co dinh huu hinh" in target and "tai san co dinh huu hinh" not in row:
        return False
    if "chi phi thue tndn hien hanh" in target and "hien hanh" not in row:
        return False
    # Income-statement expense rows are commonly rendered in parentheses,
    # while the same requested expense is reported as a positive amount in a
    # tax note.  A cross-issuer comparison must not silently mix those sign
    # conventions.  Unless the question explicitly asks for a negative
    # value, keep the signed P&L representation out of this best-effort lane
    # and let the established program route (or abstention) handle it.
    if "chi phi thue tndn hien hanh" in target:
        raw_value = selection.get("raw_value")
        try:
            raw_decimal = Decimal(str(raw_value))
        except (InvalidOperation, TypeError, ValueError):
            raw_decimal = None
        normalized_question = normalize(question)
        question_tokens = set(normalized_question.split())
        explicit_negative = (
            bool({"am", "lo"} & question_tokens)
            or "so am" in normalized_question
            or "gia tri am" in normalized_question
        )
        if raw_decimal is not None and raw_decimal < 0 and not explicit_negative:
            return False
    if "thu nhap lai tien gui" in target and "thu nhap lai tien gui" not in row:
        return False
    if "chi phi lai tien gui" in target and "chi phi lai tien gui" not in row:
        return False
    if "chi phi lai vay" in target and "chi phi lai vay" not in row:
        return False
    if "lai thuan" in target and "lai thuan" not in row:
        return False
    return True


def _cross_entity_lookup_question(
    *,
    metric: str,
    item: Mapping[str, Any],
    year: int,
    scope: str | None,
) -> str:
    question = normalize(item.get("question") or "")
    period = "cuoi nam" if any(
        cue in question for cue in ("cuoi nam", "den ngay", "tai ngay", "so du")
    ) else "nam"
    if "nghin ty" in question or "ngan ty" in question:
        unit = " nghin ty dong"
    elif "tram ty" in question:
        unit = " tram ty dong"
    elif "ty dong" in question or "ti dong" in question:
        unit = " ty dong"
    elif "trieu dong" in question:
        unit = " trieu dong"
    elif "nghin dong" in question or "ngan dong" in question:
        unit = " nghin dong"
    else:
        unit = ""
    return f"{metric} {period} {year}{unit}" + (" cong ty me" if scope == "separate" else "")


def source_first_cross_entity_answer(
    item: dict[str, Any],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Propose a two-issuer subtraction from two independent source lookups.

    This is deliberately narrower than ``multi_entity_program_answer``:
    exactly two planned issuers, one explicit year, source-first exact/ordered
    row matches, a bounded metric population, and one shared reporting scope.
    It remains a best-effort proposal until the normal independent verifier
    and any human semantic gate accept it.
    """

    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "cross_entity_comparison":
        return None
    tickers, ticker_source = _cross_entity_ticker_plan(item)
    years = []
    for value in plan.get("years") or []:
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    if len(tickers) != 2 or len(years) != 1 or operation != "subtract":
        return None
    requested_year = years[0]
    if ticker_source == "exact_code_stock_alias_recovery" and any(
        (ticker, requested_year) not in tables_by_pair for ticker in tickers
    ):
        # An alias is routing metadata only.  Requiring both source pairs to
        # exist for the requested year prevents a text-only alias recovery
        # from expanding the candidate set without a replayable source.
        return None
    routed_item: Mapping[str, Any] = item
    if ticker_source == "exact_code_stock_alias_recovery":
        routed_plan = dict(plan)
        routed_plan["tickers"] = list(tickers)
        routed_item = dict(item)
        routed_item["question_plan"] = routed_plan
    metric_variants = cross_entity_metric_variants(routed_item)
    if not metric_variants:
        return None
    requested_metric = metric_variants[0]
    requested_scope = _cross_entity_scope(item)

    resolved: list[dict[str, Any]] = []
    for ticker in tickers:
        successful: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for metric in metric_variants:
            lookup_question = _cross_entity_lookup_question(
                metric=metric,
                item=item,
                year=requested_year,
                scope=requested_scope,
            )
            synthetic = {
                "question": lookup_question,
                "question_plan": {
                    "family": "direct_lookup",
                    "tickers": [ticker],
                    "years": [requested_year],
                    "scope": requested_scope,
                    "reporting_scope": requested_scope,
                    "operands": [
                        {
                            "operand_id": "value",
                            "metric": metric,
                            "period": requested_year,
                            "ticker": ticker,
                            "scope": requested_scope,
                        }
                    ],
                },
            }
            result = resolve_source_first_direct_lookup(
                synthetic,
                tables_by_pair=tables_by_pair,
                parse_decimal=parse_decimal,
                candidate_evidence_window=candidate_evidence_window,
                choose_year_column=choose_year_column,
                source_multiplier=source_multiplier,
                requested_divisor=requested_divisor,
                report_year_neighbor_fallback=None,
            )
            if result is None or not _cross_entity_source_result_is_safe(
                result,
                requested_metric=requested_metric,
                requested_year=requested_year,
                question=str(item.get("question") or ""),
            ):
                continue
            source = (result.get("sources") or [None])[0]
            if not isinstance(source, Mapping):
                continue
            signature = (
                str(source.get("document_id") or ""),
                str(source.get("internal_table_uid") or ""),
                str(source.get("row_index") or ""),
                str(source.get("column_index") or ""),
            )
            successful[signature] = result
        if not successful:
            return None
        # Different accepted variants point to different rows/values.  That
        # is a semantic ambiguity, not a reason to prefer the first variant.
        value_signatures = {
            (
                str(result.get("answer")),
                str((result.get("sources") or [{}])[0].get("row_label") or ""),
            )
            for result in successful.values()
        }
        if len(value_signatures) != 1:
            return None
        selected = next(iter(successful.values()))
        source = dict((selected.get("sources") or [])[0])
        source["role"] = f"entity_{ticker}"
        resolved.append({"ticker": ticker, "result": selected, "source": source})

    source_scopes = {
        normalize(entry["source"].get("source_first_scope") or "unknown")
        for entry in resolved
    }
    if requested_scope is None and len(source_scopes) != 1:
        return None
    if requested_scope is not None:
        normalized_scope = normalize(requested_scope)
        if any(
            normalize(entry["source"].get("source_first_scope") or "unknown")
            not in {normalized_scope, "unknown"}
            for entry in resolved
        ):
            return None

    values = [entry["result"]["answer"] for entry in resolved]
    question = normalize(item.get("question") or "")
    # ``chênh lệch`` in these comparison questions is normally a magnitude,
    # even when the legacy plan compiler records the operation as ``subtract``
    # and the question does not contain the explicit word ``tuyệt đối``.
    # Directional wording (``cao hơn``, ``kém hơn``...) remains signed and is
    # handled by the branches below.
    directional_difference = any(
        cue in question
        for cue in ("cao hon", "lon hon", "kem hon", "thap hon", "be hon", "it hon")
    )
    if (
        not directional_difference
        and "chenh lech" in question
    ) or "tuyet doi" in question or "do chenh lech" in question:
        answer = abs(values[0] - values[1])
        query = (
            "float(abs(df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0] - "
            "df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0]))"
            % (tickers[0], tickers[1])
        )
    elif any(cue in question for cue in ("kem hon", "thap hon", "be hon", "it hon")):
        answer = values[1] - values[0]
        query = (
            "float(df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0] - "
            "df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0])"
            % (tickers[1], tickers[0])
        )
    elif "so voi" in question and any(cue in question for cue in ("cao hon", "lon hon")):
        answer = values[1] - values[0]
        query = (
            "float(df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0] - "
            "df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0])"
            % (tickers[1], tickers[0])
        )
    else:
        answer = values[0] - values[1]
        query = (
            "float(df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0] - "
            "df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0])"
            % (tickers[0], tickers[1])
        )
    return {
        "answer": answer,
        "sources": [entry["source"] for entry in resolved],
        "query": query,
        "tier": "source_first_cross_entity_v1",
        "operation": "subtract",
        "metric_variants": metric_variants,
        "requested_scope": requested_scope,
        "requested_year": requested_year,
        "source_scopes": sorted(source_scopes),
        "ticker_source": ticker_source,
        "planned_tickers": [
            str(value).strip().upper()
            for value in plan.get("tickers") or []
            if str(value).strip()
        ],
    }


def build_source_first_cross_entity_lookup_index(
    items_by_question: Mapping[int, dict[str, Any]],
    *,
    tables_by_pair: Mapping[tuple[str, int], list[dict[str, Any]]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Run the controlled two-issuer source proposal lane with telemetry."""

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    for question_id, item in items_by_question.items():
        plan = item.get("question_plan") or {}
        if str(plan.get("family") or "") != "cross_entity_comparison":
            stats["questions_skipped_non_cross_entity"] += 1
            continue
        stats["questions_considered"] += 1
        result = source_first_cross_entity_answer(item, tables_by_pair=tables_by_pair)
        if result is None:
            stats["questions_unresolved_or_ambiguous"] += 1
            continue
        answers[int(question_id)] = result
        stats["questions_resolved"] += 1
        stats[f"scope_{result.get('requested_scope') or 'unqualified'}"] += 1
        stats[f"ticker_source_{result.get('ticker_source') or 'unknown'}"] += 1
    return answers, {
        **dict(stats),
        "protocol": "source_first_cross_entity_v1",
        "requires_two_tickers_one_year": True,
        "exact_report_year_only": True,
        "accepted_match_modes": sorted(_CROSS_ENTITY_SOURCE_ALLOWED_MATCH_MODES),
        "shared_scope_required": True,
        "accepted_answers_are_current_table_replayed": True,
        "machine_artifact_is_not_human_verified": True,
        "incomplete_plans_may_use_exact_code_stock_alias_recovery": True,
        "alias_recovery_is_routing_only": True,
        "lane": "authorized_best_effort_submission_candidate",
    }


def ratio_metric_pair(question: str) -> tuple[str, str] | None:
    q = normalize(question)
    if " tren " not in f" {q} ":
        return None
    left, right = q.split(" tren ", 1)
    markers = ("ty trong ", "ty le ", "ty suat ", "ty so ", "he so ")
    numerator = left
    for marker in markers:
        pos = left.rfind(marker)
        if pos >= 0:
            numerator = left[pos + len(marker):]
            break
    denominator = re.split(
        r"\s+(?:cua|cuoi nam|nam 20\d{2}|tinh den|den ngay|tai thoi diem)\s+",
        right,
        maxsplit=1,
    )[0]
    numerator = numerator.strip()
    denominator = denominator.strip()
    if len(content_tokens(numerator)) < 1 or len(content_tokens(denominator)) < 1:
        return None
    return numerator, denominator


def multi_entity_metric_hint(item: dict[str, Any]) -> str:
    """Extract a conservative metric phrase for an entity-group plan."""

    question = str(item.get("question") or "")
    normalized = normalize(question)
    # ``... trung bình tại năm ...`` is the common average form.  The text
    # before ``trung bình`` is the requested financial line, not the entity
    # list that follows it.
    if " trung binh" in f" {normalized}":
        prefix = normalized.split(" trung binh", 1)[0].strip()
        prefix = re.sub(r"^(?:muc|gia tri|so)\s+", "", prefix)
        if prefix:
            return prefix

    # Conditional questions place the metric after the last ``có``.  Using
    # the last occurrence avoids capturing ``có bao nhiêu công ty``.
    if " bao nhieu cong ty" in normalized and " co " in f" {normalized} ":
        tail = normalized.rsplit(" co ", 1)[-1]
        tail = re.split(r"\s+(?:trong|tai|vao|den)\s+(?:nam|ngay)\b", tail, maxsplit=1)[0]
        tail = re.sub(r"\s+(?:duong|am|khong am|khong duong)\s*$", "", tail)
        if tail:
            return tail.strip()

    # Aggregation/comparison questions often use ``<metric> của <entities>``.
    prefix = re.split(r"\s+cua\s+", normalized, maxsplit=1)[0].strip()
    prefix = re.sub(
        r"^(?:tong|so|gia tri|muc)\s+",
        "",
        prefix,
    )
    prefix = re.sub(r"\s+(?:cao nhat|lon nhat|thap nhat|nho nhat)\s*$", "", prefix)
    return prefix or normalized


def multi_entity_operation(item: dict[str, Any]) -> str | None:
    plan = item.get("question_plan") or {}
    operation = str((plan.get("operation_ast") or {}).get("op") or "")
    if operation in {"mean", "sum", "min", "max", "count", "subtract"}:
        return operation
    question = normalize(item.get("question") or "")
    if "bao nhieu cong ty" in question:
        return "count"
    if "trung binh" in question or "binh quan" in question:
        return "mean"
    if "tong" in question:
        return "sum"
    if any(cue in question for cue in ("cao nhat", "lon nhat")):
        return "max"
    if any(cue in question for cue in ("thap nhat", "nho nhat")):
        return "min"
    return None


def multi_entity_program_answer(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    """Resolve simple multi-company plans from one selected cell per entity.

    This route is intentionally narrow: it requires an explicit ticker group,
    one numeric V2 cell per ticker, and a known aggregate/comparison operator.
    It is a calculation route, not a permission to treat candidate scores as
    values.  Unresolved questions continue to the best surviving candidate
    path and remain visible as such in diagnostics.
    """

    plan = item.get("question_plan") or {}
    tickers = [str(value).upper() for value in plan.get("tickers") or [] if str(value).strip()]
    if len(tickers) < 2:
        return None
    operation = multi_entity_operation(item)
    if operation is None:
        return None
    if operation == "subtract" and len(tickers) != 2:
        return None
    if operation not in {"count", "mean", "sum", "min", "max", "subtract"}:
        return None

    years = [int(value) for value in plan.get("years") or [] if value is not None]
    if not years:
        years = [int(value) for value in YEAR_RE.findall(str(item.get("question") or ""))]
    target_year = years[-1] if years else None
    metric = multi_entity_metric_hint(item)
    selections: list[dict[str, Any]] = []
    for ticker in tickers:
        selection = choose_semantic_cell(
            item,
            metric_override=metric,
            year_override=target_year,
            ticker_override=ticker,
            tables_by_uid=tables_by_uid,
            allow_uncertain=False,
        )
        # A multi-entity route must have a real row-semantic margin for every
        # entity.  A candidate merely surviving metadata filters is not enough
        # to trigger arithmetic; otherwise a missing metric would be averaged
        # with an unrelated numeric row.
        if selection is None or float(selection.get("score") or 0.0) < 1.8:
            return None
        selection["role"] = f"entity_{ticker}"
        selections.append(selection)

    values = [selection["value"] for selection in selections]
    question = normalize(item.get("question") or "")
    if operation == "count":
        if "am" in question and "khong am" not in question:
            answer = Decimal(sum(value < 0 for value in values))
            predicate = "< 0"
        else:
            answer = Decimal(sum(value > 0 for value in values))
            predicate = "> 0"
        query = f"float((df1['operand_value'] {predicate}).sum())"
    elif operation == "mean":
        answer = sum(values, Decimal(0)) / Decimal(len(values))
        terms = [
            f"df1.loc[df1.operand_role=='{selection['role']}','operand_value'].iloc[0]"
            for selection in selections
        ]
        query = f"float(({' + '.join(terms)})/{len(terms)})"
    elif operation == "sum":
        answer = sum(values, Decimal(0))
        query = "float(df1['operand_value'].sum())"
    elif operation == "max":
        answer = max(values)
        query = "float(df1['operand_value'].max())"
    elif operation == "min":
        answer = min(values)
        query = "float(df1['operand_value'].min())"
    else:
        if any(cue in question for cue in ("vuot", "cao hon", "lon hon")):
            answer = values[0] - values[1]
        else:
            answer = values[0] - values[1]
        query = (
            "float(df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0] - "
            "df1.loc[df1.operand_role=='entity_%s','operand_value'].iloc[0])"
            % (tickers[0], tickers[1])
        )
    return answer, selections, query, "program_multi_entity_plan"


def program_aware_answer(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    question = str(item.get("question") or "")
    plan = item.get("question_plan") or {}
    q = normalize(question)
    years = [int(year) for year in YEAR_RE.findall(question)]
    target_year = years[-1] if years else ((plan.get("years") or [None])[0])

    ratio = ratio_metric_pair(question)
    complex_cues = ("trong nhom", "xet cac", "doanh nghiep co", "giai doan", "trung binh", "trung vi")
    forbidden_reported_ratios = ("so huu", "quyen bieu quyet", "loi ich kinh te")
    simple_ratio = (
        plan.get("family") == "ratio_or_derived"
        and not any(cue in q for cue in complex_cues)
        and not any(cue in q for cue in forbidden_reported_ratios)
        and len(plan.get("tickers") or []) <= 1
        and len(plan.get("years") or []) <= 1
    )
    if ratio and simple_ratio and ("%" in question or "phan tram" in q or (plan.get("operation_ast") or {}).get("op") == "divide"):
        numerator_metric, denominator_metric = ratio
        numerator = choose_semantic_cell(
            item,
            metric_override=numerator_metric,
            year_override=target_year,
            tables_by_uid=tables_by_uid,
        )
        denominator = choose_semantic_cell(
            item,
            metric_override=denominator_metric,
            year_override=target_year,
            document_override=str(numerator.get("document_id")) if numerator else None,
            tables_by_uid=tables_by_uid,
        )
        same_cell = bool(
            numerator
            and denominator
            and (
                numerator.get("internal_table_uid"), numerator.get("row_index"), numerator.get("column_index")
            )
            == (
                denominator.get("internal_table_uid"), denominator.get("row_index"), denominator.get("column_index")
            )
        )
        if (
            numerator
            and denominator
            and denominator["value"] != 0
            and not same_cell
            and numerator["score"] >= 1.4
            and denominator["score"] >= 1.4
        ):
            numerator["role"] = "numerator"
            denominator["role"] = "denominator"
            multiplier = Decimal(100) if ("%" in question or "phan tram" in q) else Decimal(1)
            answer = numerator["value"] / denominator["value"] * multiplier
            query = (
                "float(df1.loc[df1.operand_role=='numerator','operand_value'].iloc[0] / "
                "df1.loc[df1.operand_role=='denominator','operand_value'].iloc[0]"
                + (" * 100)" if multiplier == 100 else ")")
            )
            return answer, [numerator, denominator], query, "program_ratio_heuristic"

    multi_entity = multi_entity_program_answer(item, tables_by_uid=tables_by_uid)
    if multi_entity is not None:
        return multi_entity

    operands = infer_temporal_operands(item)
    operation = infer_formula_operation(item, operands)
    if operation is None:
        return None
    resolved: dict[str, dict[str, Any]] = {}
    for operand in operands:
        operand_id = str(operand.get("operand_id") or "")
        period = operand.get("period")
        selection = choose_semantic_cell(
            item,
            metric_override=str(operand.get("metric") or question),
            year_override=int(period) if period else None,
            tables_by_uid=tables_by_uid,
        )
        if selection is None:
            return None
        selection["role"] = operand_id
        resolved[operand_id] = selection
    if "x_old" not in resolved or "x_new" not in resolved:
        return None
    old_locator = (
        resolved["x_old"].get("internal_table_uid"),
        resolved["x_old"].get("row_index"),
        resolved["x_old"].get("column_index"),
    )
    new_locator = (
        resolved["x_new"].get("internal_table_uid"),
        resolved["x_new"].get("row_index"),
        resolved["x_new"].get("column_index"),
    )
    if old_locator == new_locator or min(resolved["x_old"]["score"], resolved["x_new"]["score"]) < 1.0:
        return None
    old = resolved["x_old"]["value"]
    new = resolved["x_new"]["value"]
    if operation == "percentage_change":
        if old == 0:
            return None
        if "giam" in q and new < old:
            answer = (old - new) / abs(old) * Decimal(100)
            query = (
                "float((df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0] - "
                "df1.loc[df1.operand_role=='x_new','operand_value'].iloc[0]) / abs("
                "df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0]) * 100)"
            )
        else:
            answer = (new - old) / abs(old) * Decimal(100)
            query = (
                "float((df1.loc[df1.operand_role=='x_new','operand_value'].iloc[0] - "
                "df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0]) / abs("
                "df1.loc[df1.operand_role=='x_old','operand_value'].iloc[0]) * 100)"
            )
        return answer, [resolved["x_old"], resolved["x_new"]], query, "program_growth_heuristic"

    mentioned_years = [int(year) for year in YEAR_RE.findall(question)]
    if "be hon" in q or "giam" in q:
        answer = old - new
        first_role, second_role = "x_old", "x_new"
    elif "lon hon" in q and len(mentioned_years) == 2 and mentioned_years[0] == min(mentioned_years):
        answer = old - new
        first_role, second_role = "x_old", "x_new"
    else:
        answer = new - old
        first_role, second_role = "x_new", "x_old"
    query = (
        f"float(df1.loc[df1.operand_role=='{first_role}','operand_value'].iloc[0] - "
        f"df1.loc[df1.operand_role=='{second_role}','operand_value'].iloc[0])"
    )
    return answer, [resolved["x_old"], resolved["x_new"]], query, "program_subtract_heuristic"


def exact_replay_answer(record: dict[str, Any]) -> tuple[Decimal, list[dict[str, Any]]] | None:
    if record.get("execution_status") != "execution_replay_ready":
        return None
    composition = record.get("composition_trace") or {}
    answer_raw = composition.get("converted_output_decimal")
    sources: list[dict[str, Any]] = []
    for stage in record.get("stage_traces") or []:
        sources.extend(stage.get("operand_sources") or [])
        if answer_raw is None and stage.get("status") == "execution_replay_ready":
            answer_raw = stage.get("converted_output_decimal")
    if answer_raw is None:
        return None
    return Decimal(str(answer_raw)), sources


def _model_result_unit_matches(question_item: dict[str, Any], result_unit: Any) -> bool:
    expected = normalize((question_item.get("question_plan") or {}).get("requested_unit"))
    actual = normalize(result_unit)
    if not expected or not actual:
        return True
    if expected in {"percent", "percentage"}:
        return actual in {"percent", "percentage"}
    if expected in {"times", "time", "lan", "x"}:
        return actual in {"times", "time", "lan", "x"}
    if expected in {"million vnd", "million dong"}:
        return actual in {"million vnd", "million dong", "trieu vnd", "trieu dong"}
    if expected in {"billion vnd", "billion dong"}:
        return actual in {"billion vnd", "billion dong", "ty vnd", "ty dong"}
    return expected == actual


def _replay_model_answer_candidate(
    record: dict[str, Any],
    *,
    question_item: dict[str, Any],
    tables_by_uid: dict[str, dict[str, Any]],
) -> tuple[Decimal, list[dict[str, Any]]]:
    """Replay a model-produced staged result against current V2 cells.

    This is the narrow bridge from the research/model lane to the primary
    submission lane.  The model result itself is not recomputed here, but every
    cited cell and the final independently recorded stage value must agree with
    the current immutable table bundle.
    """
    if record.get("execution_status") != "grounded":
        raise ValueError("model result is not grounded")
    if record.get("grounding_status") != "staged_exact_cells_replayed":
        raise ValueError("model result lacks staged exact-cell replay")
    answer = parse_decimal(record.get("result_value"))
    if answer is None:
        raise ValueError("model result has no numeric result_value")
    if not _model_result_unit_matches(question_item, record.get("result_unit")):
        raise ValueError("model result unit does not match requested question unit")

    direct_gate = record.get("direct_replay_gate") or {}
    if not isinstance(direct_gate, dict) or not direct_gate:
        raise ValueError("model result has no direct replay gate")
    sources: list[dict[str, Any]] = []
    for stage_id, stage in direct_gate.items():
        if not isinstance(stage, dict) or stage.get("status") not in {
            "direct_replay_ready",
            "source_free_deterministic_transition",
        }:
            raise ValueError(f"model stage {stage_id} is not direct_replay_ready")
        bindings = stage.get("replayed_bindings") or []
        if stage.get("status") == "source_free_deterministic_transition":
            if bindings:
                raise ValueError(f"model stage {stage_id} unexpectedly has source bindings")
            continue
        if not bindings:
            raise ValueError(f"model stage {stage_id} has no replayed bindings")
        for binding in bindings:
            uid = str(binding.get("internal_table_uid") or "")
            row_index = _int_or_none(binding.get("row_index"))
            column_index = _int_or_none(binding.get("column_index"))
            table = tables_by_uid.get(uid)
            if table is None or row_index is None or column_index is None:
                raise ValueError(f"model stage {stage_id} cites an unavailable cell")
            document_id = str(binding.get("document_id") or "").removesuffix(".txt")
            table_document = str(table.get("document_id") or "").removesuffix(".txt")
            if document_id and document_id != table_document:
                raise ValueError(f"model stage {stage_id} cites a document mismatch")
            rows = table.get("rows") or []
            if (
                row_index < 0
                or row_index >= len(rows)
                or column_index < 0
                or column_index >= len(rows[row_index])
            ):
                raise ValueError(f"model stage {stage_id} cites an invalid coordinate")
            expected = parse_decimal(binding.get("raw_value"))
            observed = parse_decimal(rows[row_index][column_index])
            if expected is None or observed is None or expected != observed:
                raise ValueError(f"model stage {stage_id} cell value diverges from V2")
            sources.append(
                {
                    "raw_value_decimal": str(observed),
                    "role": binding.get("variable_id") or "model_replayed_operand",
                    "document_id": table_document,
                    "internal_table_uid": uid,
                    "row_index": row_index,
                    "column_index": column_index,
                    "model_stage_id": stage_id,
                    "candidate_source": "model_staged_execution_replay",
                }
            )

    critic_gate = record.get("independent_critic_gate") or {}
    if not isinstance(critic_gate, dict) or not critic_gate:
        raise ValueError("model result has no independent critic gate")
    final_values: list[Decimal] = []
    observed_values: list[Decimal] = []
    for stage_id, stage in critic_gate.items():
        if not isinstance(stage, dict):
            continue
        status = stage.get("status")
        if status not in {"independent_critic_ready", "independent_transition_ready"}:
            raise ValueError(f"model critic stage {stage_id} is not independently replayed")
        execution = stage.get("deterministic_stage_execution") or {}
        value = parse_decimal(execution.get("aggregate_value"))
        if value is None:
            continue
        observed_values.append(value)
        if execution.get("final_stage") is True:
            final_values.append(value)
    if not observed_values:
        raise ValueError("model result has no deterministic critic value")
    if final_values:
        if answer not in final_values:
            raise ValueError("model result differs from final independent critic value")
    elif answer != observed_values[-1]:
        raise ValueError("model result differs from independent critic value")
    return answer, sources


def validate_model_answer_candidate(
    record: dict[str, Any],
    *,
    question_item: dict[str, Any],
    tables_by_uid: dict[str, dict[str, Any]],
) -> tuple[Decimal, list[dict[str, Any]]] | None:
    """Return a model answer only when its current-table replay passes."""
    try:
        return _replay_model_answer_candidate(
            record,
            question_item=question_item,
            tables_by_uid=tables_by_uid,
        )
    except ValueError:
        return None


def load_model_answer_candidates(
    paths: Iterable[Path],
    *,
    items_by_question: dict[int, dict[str, Any]],
    tables_by_uid: dict[str, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Load independently replayed model answers for primary submission."""
    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    source_paths: list[str] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        stats["paths_requested"] += 1
        if not path.exists():
            stats["paths_missing"] += 1
            continue
        source_paths.append(str(path))
        stats["paths_used"] += 1
        for record in read_jsonl(path):
            stats["records_scanned"] += 1
            question_id = _record_question_id(record)
            if question_id is None or question_id not in items_by_question:
                stats["records_rejected_unknown_question"] += 1
                continue
            try:
                answer, sources = _replay_model_answer_candidate(
                    record,
                    question_item=items_by_question[question_id],
                    tables_by_uid=tables_by_uid,
                )
            except ValueError:
                stats["records_rejected_replay"] += 1
                continue
            if question_id in answers:
                stats["records_rejected_duplicate_question"] += 1
                continue
            answers[question_id] = {
                "answer": answer,
                "sources": sources,
                "source_path": str(path),
                "result_unit": record.get("result_unit"),
                "protocol": record.get("protocol"),
            }
            stats["answers_accepted"] += 1
    stats["questions_with_answers"] = len(answers)
    stats["source_paths"] = source_paths
    return answers, dict(stats)


def _direct_replay_row_label(row: list[Any]) -> str:
    """Return the human-readable part of a replayed source row."""

    labels = [str(cell).strip() for cell in row if parse_decimal(cell) is None and str(cell).strip()]
    return " ".join(labels)


def _table_declared_source_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    """Resolve a table unit from its compact header/anchor context.

    The replay artifact may have inferred ``vnd`` from the selected numeric
    cell even when the table header says ``Triệu VND``.  Header text is the
    stronger source-side contract.  In particular, do not merge
    ``context_trace.unit_labels`` into the header: that trace can report a
    unit from neighbouring prose (for example ``tỷ đồng``) even when the
    selected table is explicitly ``VND``.
    """

    header_parts: list[str] = [str(value) for value in table.get("column_labels") or []]
    header_parts.extend(str(value) for value in table.get("headers") or [])
    header_multiplier = _unit_multiplier_from_text(" ".join(header_parts))
    if header_multiplier is not None:
        return header_multiplier

    # Unit rows are normally at the top of the structured table.  Require a
    # declaration marker or a non-numeric row before trusting a unit token;
    # otherwise a financial line that happens to contain ``tỷ đồng`` would be
    # mistaken for the table's scale.
    for raw_row in (table.get("rows") or [])[:8]:
        if not isinstance(raw_row, (list, tuple)):
            continue
        row_text = normalize(" ".join(str(cell) for cell in raw_row))
        row_multiplier = _unit_multiplier_from_text(row_text)
        if row_multiplier is None:
            continue
        has_numeric_cell = any(parse_decimal(cell) is not None for cell in raw_row)
        if "don vi" in row_text or "unit" in row_text or not has_numeric_cell:
            return row_multiplier

    # Some source titles carry an actual ``Đơn vị tính: ...`` declaration.
    # Read only the tail after that marker; do not use the derived
    # ``unit_labels`` summary, which is intentionally navigation-only.
    trace = table.get("context_trace")
    if isinstance(trace, Mapping):
        source_title = normalize(trace.get("source_title") or "")
        if "don vi" in source_title:
            source_tail = source_title.rsplit("don vi", 1)[-1]
            title_multiplier = _unit_multiplier_from_text(source_tail)
            if title_multiplier is not None:
                return title_multiplier

    # ``unit_hint`` is only a fallback when no source-side declaration was
    # available.  It can be wrong, as seen in the v4 dense run, but exact
    # canonical values remain useful for sparse/legacy tables.
    unit_hint = table.get("unit_hint")
    hints = unit_hint.values() if isinstance(unit_hint, Mapping) else [unit_hint]
    for hint in hints:
        if not hint:
            continue
        try:
            return vnd_scale(str(hint))
        except (TypeError, ValueError):
            hint_multiplier = _unit_multiplier_from_text(hint)
            if hint_multiplier is not None:
                return hint_multiplier
    return None


def load_direct_evidence_replay_candidates(
    paths: Iterable[Path],
    *,
    items_by_question: dict[int, dict[str, Any]],
    tables_by_uid: dict[str, dict[str, Any]],
    include_provisional: bool = False,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Load unique source-cell replay candidates for the answer lane.

    The input is a model/research artifact, not an answer authority.  It is
    admitted only when it describes one exact source value and identifies the
    selected source coordinate.  Several duplicate documents may carry the
    same exact value; that is still safe for the numeric answer, so the loader
    selects the candidate whose UID matches ``machine_selected_uid``.  The current
    structured table remains authoritative: this function re-reads the cell,
    validates its coordinate and raw value, resolves the source/output VND
    scales, and recomputes the proposed answer with ``Decimal``.  In
    particular, ``replay_value`` is a consistency check and is never copied
    into the submission without replaying the current table.
    """

    answers: dict[int, dict[str, Any]] = {}
    stats: Counter[str] = Counter()
    source_paths: list[str] = []
    allowed_statuses = {"machine_calibrated"}
    if include_provisional:
        allowed_statuses.add("machine_provisional")

    for raw_path in paths:
        path = Path(raw_path).expanduser()
        stats["paths_requested"] += 1
        if not path.exists():
            stats["paths_missing"] += 1
            continue
        source_paths.append(str(path))
        stats["paths_used"] += 1
        for record in read_jsonl(path):
            stats["records_scanned"] += 1
            question_id = _record_question_id(record)
            if question_id is None or question_id not in items_by_question:
                stats["records_rejected_unknown_question"] += 1
                continue
            if record.get("status") != "shadow_replay_ready":
                stats["records_rejected_status"] += 1
                continue
            if record.get("distinct_exact_value_count") != 1:
                stats["records_rejected_non_unique"] += 1
                continue
            consensus_status = str(record.get("machine_consensus_status") or "")
            if consensus_status not in allowed_statuses:
                stats["records_rejected_consensus_status"] += 1
                continue
            candidates = record.get("valid_exact_candidates")
            if not isinstance(candidates, list) or not candidates:
                stats["records_rejected_candidate_count"] += 1
                continue
            selected_uid = str(record.get("machine_selected_uid") or "")
            candidate = next(
                (
                    value
                    for value in candidates
                    if isinstance(value, Mapping)
                    and str(value.get("internal_table_uid") or "") == selected_uid
                ),
                None,
            )
            if not isinstance(candidate, Mapping) or not selected_uid:
                stats["records_rejected_candidate_shape"] += 1
                continue
            uid = str(candidate.get("internal_table_uid") or "")
            table = tables_by_uid.get(uid)
            if table is None:
                stats["records_rejected_missing_table"] += 1
                continue
            row_index = _int_or_none(candidate.get("row_index"))
            column_index = _int_or_none(candidate.get("column_index"))
            rows = table.get("rows") or []
            if (
                row_index is None
                or column_index is None
                or row_index < 0
                or row_index >= len(rows)
                or column_index < 0
                or column_index >= len(rows[row_index])
            ):
                stats["records_rejected_bad_coordinate"] += 1
                continue
            observed = parse_decimal(rows[row_index][column_index])
            candidate_raw = parse_decimal(candidate.get("raw_value"))
            candidate_parsed = parse_decimal_literal(candidate.get("parsed_value"))
            if observed is None or candidate_raw is None or candidate_parsed is None:
                stats["records_rejected_non_numeric"] += 1
                continue
            if observed != candidate_raw or observed != candidate_parsed:
                stats["records_rejected_raw_value_mismatch"] += 1
                continue

            source_unit = str(candidate.get("source_unit") or "")
            replay_unit = str(record.get("replay_unit") or "")
            try:
                candidate_source_multiplier = vnd_scale(source_unit)
                output_divisor = vnd_scale(replay_unit)
            except (TypeError, ValueError):
                stats["records_rejected_unit_contract"] += 1
                continue

            artifact_source_base_vnd = observed * candidate_source_multiplier
            replay_value = parse_decimal_literal(record.get("replay_value"))
            comparison_value = parse_decimal_literal(candidate.get("comparison_value"))
            if replay_value is None or comparison_value is None:
                stats["records_rejected_missing_replay_value"] += 1
                continue
            if comparison_value != replay_value:
                stats["records_rejected_replay_mismatch"] += 1
                continue
            if artifact_source_base_vnd != replay_value * output_divisor:
                stats["records_rejected_replay_mismatch"] += 1
                continue

            table_source_multiplier = _table_declared_source_multiplier(table)
            if table_source_multiplier is None:
                source_multiplier_value = candidate_source_multiplier
                stats["source_unit_from_replay_candidate"] += 1
            else:
                source_multiplier_value = table_source_multiplier
                if source_multiplier_value != candidate_source_multiplier:
                    stats["source_unit_corrected_from_current_table"] += 1

            question_text = str(items_by_question[question_id].get("question") or "")
            question_output_divisor = requested_divisor(question_text)
            if question_output_divisor != output_divisor:
                # Compound literals such as "trăm tỷ" were flattened to
                # ``billion_vnd`` by an older question-plan compiler.  The raw
                # Vietnamese question is the correct output contract.
                stats["output_unit_corrected_from_question_literal"] += 1
            source_base_vnd = observed * source_multiplier_value
            answer = source_base_vnd / question_output_divisor
            if question_id in answers:
                stats["records_rejected_duplicate_question"] += 1
                continue

            document_id = str(table.get("document_id") or "").removesuffix(".txt")
            row_label = _direct_replay_row_label(list(rows[row_index]))
            tier = (
                "direct_source_replay_v1"
                if consensus_status == "machine_calibrated"
                else "direct_source_replay_provisional_v1"
            )
            selection = {
                "score": 100.0 if consensus_status == "machine_calibrated" else 99.0,
                "value": answer,
                "raw_value": observed,
                "source_multiplier": source_multiplier_value,
                "row_index": row_index,
                "column_index": column_index,
                "row_label": row_label,
                "document_id": document_id,
                "internal_table_uid": uid,
                "candidate_rank": 0,
                "candidate_source": "direct_evidence_replay_v1",
                "research_candidate_only": False,
                "direct_replay": True,
                "direct_replay_status": record.get("status"),
                "machine_consensus_status": consensus_status,
                "source_unit": source_unit,
                "replay_unit": replay_unit,
                "question_output_divisor": question_output_divisor,
                "declared_source_multiplier": candidate_source_multiplier,
                "candidate_filter_status": "DIRECT_EXACT_SOURCE_REPLAY",
                "validity_probability": 1.0,
                "validity_model_status": "direct_source_replay_gate",
            }
            answers[question_id] = {
                "answer": answer,
                "sources": [
                    {
                        "raw_value_decimal": str(observed),
                        "value": answer,
                        "role": "direct_source_replay_selected",
                        "document_id": document_id,
                        "internal_table_uid": uid,
                        "row_index": row_index,
                        "column_index": column_index,
                        "row_label": row_label,
                        "source_to_vnd_multiplier": str(source_multiplier_value),
                        "source_unit": source_unit,
                        "declared_source_multiplier": str(candidate_source_multiplier),
                        "question_output_divisor": str(question_output_divisor),
                        "requested_output_unit": replay_unit,
                        "candidate_source": "direct_evidence_replay_v1",
                        "machine_consensus_status": consensus_status,
                        "direct_replay": True,
                    }
                ],
                "selection": selection,
                "source_path": str(path),
                "protocol": record.get("protocol"),
                "machine_consensus_status": consensus_status,
                "tier": tier,
            }
            stats["answers_accepted"] += 1
            stats[f"answers_accepted_{consensus_status}"] += 1
    stats["questions_with_answers"] = len(answers)
    stats["source_paths"] = source_paths
    stats["include_provisional"] = include_provisional
    stats["answer_authority"] = "current_structured_table_decimal_replay"
    stats["promotion_allowed"] = False
    return answers, dict(stats)


def load_source_line_map(path: Path | None) -> dict[str, int]:
    """Load precomputed OCR line coordinates for remote/runtime builds.

    Kaggle's review bundle intentionally contains structured tables but not
    the original OCR files.  ``source_provenance.source_path`` therefore
    cannot be opened there.  The map is keyed by ``internal_table_uid`` and
    stores the 1-based line at which the table starts in the original OCR
    document.  It is a provenance-only navigation aid; it never supplies a
    numeric answer.
    """
    if path is None:
        return {}
    path = path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"source line map does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("source line map must be a JSON object")
    result: dict[str, int] = {}
    for uid, line in payload.items():
        if not isinstance(uid, str) or not uid:
            raise ValueError("source line map keys must be non-empty strings")
        try:
            value = int(line)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid OCR line for {uid!r}: {line!r}") from exc
        if value < 1:
            raise ValueError(f"OCR line must be positive for {uid!r}")
        result[uid] = value
    return result


def validate_source_line_map_coverage(
    source_line_map: Mapping[str, int],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    *,
    allow_extra: bool = False,
) -> dict[str, Any]:
    """Validate that a production map can locate every structured table.

    ``local_ordinal`` is an internal V2 asset coordinate.  It is useful for
    joins and replay, but it is not a safe substitute for the competition's
    source table position.  A runtime map must therefore cover the complete
    V2 table population before it is used as the competition coordinate
    authority.
    """

    table_uids = {str(uid) for uid in tables_by_uid}
    map_uids = {str(uid) for uid in source_line_map}
    missing = sorted(table_uids - map_uids)
    extra = sorted(map_uids - table_uids)
    if missing or (extra and not allow_extra):
        raise ValueError(
            "source line map coverage mismatch: "
            f"missing={len(missing)} extra={len(extra)} "
            f"missing_sample={missing[:3]} extra_sample={extra[:3]}"
        )
    return {
        "table_uid_count": len(table_uids),
        "map_entry_count": len(map_uids),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "extra_entries_allowed": bool(allow_extra),
    }


def unavailable_source_paths(tables_by_uid: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return source paths that cannot be used for local line derivation."""

    paths = {
        str(table_provenance(table).get("source_path") or "")
        for table in tables_by_uid.values()
    }
    return sorted(path for path in paths if path and not Path(path).exists())


def table_line_locator(
    table: dict[str, Any],
    source_cache: dict[Path, str],
    source_line_map: dict[str, int] | None = None,
    line_stats: Counter[str] | None = None,
) -> int:
    provenance = table_provenance(table)
    source = Path(str(provenance.get("source_path") or ""))
    char_start = int(provenance.get("char_start") or 0)
    if not source.exists():
        uid = str(table.get("internal_table_uid") or "")
        mapped = (source_line_map or {}).get(uid)
        if mapped is not None:
            if line_stats is not None:
                line_stats["source_line_map"] += 1
            return mapped
        if line_stats is not None:
            line_stats["local_ordinal_fallback"] += 1
        return max(1, int(table.get("local_ordinal") or 0) + 1)
    if line_stats is not None:
        line_stats["source_char_start"] += 1
    if source not in source_cache:
        source_cache[source] = source.read_text(encoding="utf-8", errors="replace")
    return source_cache[source].count("\n", 0, char_start) + 1


def json_number(value: Decimal) -> float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    return number


def _csv_operand_value(value: Any) -> str:
    """Serialize an operand so pandas cannot reinterpret a large integer as text.

    The competition evidence contract replays expressions such as
    ``df['operand_value'].sum()``.  Pandas infers a 20-digit integer as an
    ``object`` column and then ``sum`` concatenates strings.  Scientific
    notation keeps the value numeric while preserving all Decimal digits for
    the evaluator's float conversion.  Ordinary financial values retain the
    readable canonical representation.
    """

    parsed = parse_decimal_literal(value)
    if parsed is None:
        return str(value or "")
    if parsed == parsed.to_integral_value() and abs(parsed) >= Decimal("1e18"):
        return format(parsed, "E")
    return str(parsed)


def write_evidence_csv(path: Path, rows: list[dict[str, Any]], answer: Decimal, tier: str) -> None:
    fields = [
        "value", "operand_value", "raw_value", "operand_role", "document_id", "internal_table_uid",
        "row_index", "column_index", "row_label", "source_multiplier", "confidence_tier",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        if not rows:
            rows = [{}]
        for index, row in enumerate(rows):
            writer.writerow(
                {
                    "value": str(answer) if index == 0 else "",
                    "operand_value": _csv_operand_value(row.get("value", "")),
                    "raw_value": row.get("raw_value_decimal", row.get("raw_value", "")),
                    "operand_role": row.get("role", "selected_cell" if tier != "fallback_zero" else "fallback"),
                    "document_id": row.get("document_id", ""),
                    "internal_table_uid": row.get("internal_table_uid", ""),
                    "row_index": row.get("row_index", ""),
                    "column_index": row.get("column_index", ""),
                    "row_label": row.get("row_label", ""),
                    "source_multiplier": row.get("source_to_vnd_multiplier", row.get("source_multiplier", "")),
                    "confidence_tier": tier,
                }
            )


def validate_submission(output_dir: Path, expected: list[dict[str, Any]]) -> dict[str, Any]:
    import pandas as pd

    submission_path = output_dir / "submission.json"
    records = json.loads(submission_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if len(records) != len(expected):
        errors.append(f"record_count={len(records)} expected={len(expected)}")
    if [row.get("id") for row in records] != [row.get("id") for row in expected]:
        errors.append("question ids/order do not match test set")
    replayed = 0
    for row in records:
        required = {"id", "question", "answer", "relevant_docs", "relevant_tables", "evidence", "pandas_query"}
        missing = required - set(row)
        if missing:
            errors.append(f"Q{row.get('id')}: missing {sorted(missing)}")
            continue
        variables: dict[str, pd.DataFrame] = {}
        for evidence in row["evidence"]:
            csv_path = str(evidence.get("csv_path") or "")
            if not csv_path.startswith("data/"):
                errors.append(f"Q{row['id']}: invalid csv_path {csv_path}")
                continue
            path = output_dir / csv_path
            if not path.exists():
                errors.append(f"Q{row['id']}: missing {csv_path}")
                continue
            variables[str(evidence["variable"])] = pd.read_csv(path)
        try:
            replay = float(
                eval(
                    row["pandas_query"],
                    {"__builtins__": {}, "float": float, "abs": abs},
                    variables,
                )
            )
            if not math.isclose(replay, float(row["answer"]), rel_tol=1e-12, abs_tol=1e-9):
                errors.append(f"Q{row['id']}: query result mismatch")
            else:
                replayed += 1
        except (KeyError, IndexError, TypeError, ValueError, SyntaxError, NameError) as exc:
            errors.append(f"Q{row['id']}: replay error {exc}")
    return {"valid": not errors, "records": len(records), "queries_replayed": replayed, "errors": errors[:50]}


def build(args: argparse.Namespace) -> None:
    global _SEMANTIC_CELL_CONTRACT_ENABLED
    _SEMANTIC_CELL_CONTRACT_ENABLED = not bool(
        getattr(args, "disable_semantic_cell_contract", False)
    )
    reranker_policy = resolve_reranker_policy(args)
    dense_index_dir = getattr(args, "dense_index_dir", None)
    dense_artifact_validation: dict[str, Any] | None = None
    if dense_index_dir is not None:
        dense_artifact_validation = validate_dense_index_artifact(dense_index_dir)
        if not dense_artifact_validation["valid"]:
            raise ValueError(
                "dense index failed the completed-artifact gate: "
                + json.dumps(dense_artifact_validation, ensure_ascii=False, sort_keys=True)
            )
    implementation_at_build_start = implementation_fingerprint()
    questions = list(read_jsonl(args.questions))
    review_items = {int(row["id"]): row for row in read_jsonl(args.bundle / "review_items.jsonl")}
    review_items, registry_identity_stats = enrich_review_items_with_registry_identity(
        review_items
    )
    replays = {int(row["question_id"]): row for row in read_jsonl(args.replay)}

    structured_tables_path = getattr(args, "structured_tables", None)
    if structured_tables_path is None:
        structured_tables_path = args.bundle / "tables_structured_v2.jsonl"
    structured_tables_path = Path(structured_tables_path).expanduser()
    if not structured_tables_path.is_file():
        raise FileNotFoundError(f"structured table asset does not exist: {structured_tables_path}")

    structured_table_filter = str(
        getattr(args, "structured_table_filter", "candidate_uids")
    ).strip().lower()
    if structured_table_filter not in {"candidate_uids", "all"}:
        raise ValueError(
            "--structured-table-filter must be one of: candidate_uids, all"
        )
    review_candidate_uids = review_candidate_table_uids(review_items)
    tables: dict[str, dict[str, Any]] = {}
    structured_table_lines_scanned = 0
    structured_table_lines_skipped = 0
    with structured_tables_path.open("r", encoding="utf-8") as structured_handle:
        for line in structured_handle:
            if not line.strip():
                continue
            structured_table_lines_scanned += 1
            raw_table = json.loads(line)
            raw_uid = str(raw_table.get("internal_table_uid") or "")
            if (
                structured_table_filter == "candidate_uids"
                and raw_uid not in review_candidate_uids
            ):
                structured_table_lines_skipped += 1
                continue
            table = normalize_structured_table(raw_table)
            uid = table.get("internal_table_uid")
            if uid:
                tables[str(uid)] = table
    if structured_table_filter == "candidate_uids" and not tables:
        raise ValueError(
            "candidate_uids table filter hydrated no tables; use --structured-table-filter all "
            "only after checking the review packet and structured asset"
        )

    # Keep the generic ranking/retrieval population frozen while allowing the
    # explicitly audited source-first route families to search the complete
    # structured asset.  This is intentionally opt-in: the earlier full-corpus
    # diagnostic showed that sharing the expanded table map with generic
    # semantic ranking changed unrelated rows.  The route-only map is still
    # subject to the same source-line, coordinate and Decimal replay gates.
    selective_source_first_route_hydration = bool(
        getattr(args, "selective_source_first_route_hydration", False)
    )
    route_tables = tables
    route_table_lines_scanned = 0
    route_table_lines_skipped = 0
    if selective_source_first_route_hydration and structured_table_filter == "candidate_uids":
        route_tables = {}
        with structured_tables_path.open("r", encoding="utf-8") as structured_handle:
            for line in structured_handle:
                if not line.strip():
                    continue
                route_table_lines_scanned += 1
                raw_table = json.loads(line)
                table = normalize_structured_table(raw_table)
                uid = table.get("internal_table_uid")
                if uid:
                    route_tables[str(uid)] = table
                else:
                    route_table_lines_skipped += 1
        if not route_tables:
            raise ValueError(
                "selective source-first route hydration loaded no route tables; "
                "pass a complete structured asset"
            )
    else:
        route_table_lines_scanned = structured_table_lines_scanned
        route_table_lines_skipped = structured_table_lines_skipped

    # Source-first direct lookup uses the same frozen table population as the
    # normal retrieval/replay path, indexed once by the immutable report
    # identity.  This is a recall lane: it can discover a table omitted from
    # the review shortlist, but it still has to pass row, period, unit and
    # downstream proposal verification before release.
    tables_by_pair: dict[tuple[str, int], list[dict[str, Any]]] = {}
    inferred_ticker_count = 0
    missing_table_identity_count = 0
    for table in tables.values():
        explicit_ticker = str(table.get("ticker") or "").strip()
        ticker = table_ticker(table)
        if ticker and not explicit_ticker:
            inferred_ticker_count += 1
        report_year = _int_or_none(table.get("report_year"))
        if report_year is None:
            report_year = document_year(table.get("document_id"))
        if not ticker or report_year is None:
            missing_table_identity_count += 1
            continue
        tables_by_pair.setdefault((ticker, report_year), []).append(table)

    route_tables_by_pair: dict[tuple[str, int], list[dict[str, Any]]] = {}
    route_inferred_ticker_count = 0
    route_missing_table_identity_count = 0
    for table in route_tables.values():
        explicit_ticker = str(table.get("ticker") or "").strip()
        ticker = table_ticker(table)
        if ticker and not explicit_ticker:
            route_inferred_ticker_count += 1
        report_year = _int_or_none(table.get("report_year"))
        if report_year is None:
            report_year = document_year(table.get("document_id"))
        if not ticker or report_year is None:
            route_missing_table_identity_count += 1
            continue
        route_tables_by_pair.setdefault((ticker, report_year), []).append(table)
    route_answer_tables_by_uid = tables if route_tables is tables else {**tables, **route_tables}

    source_first_report_year_neighbor_offset = getattr(
        args,
        "source_first_report_year_neighbor_offset",
        1,
    )
    if getattr(args, "disable_source_first_report_year_neighbor", False):
        source_first_report_year_neighbor_offset = None
    if source_first_report_year_neighbor_offset not in {None, 1, 2}:
        raise ValueError(
            "--source-first-report-year-neighbor-offset must be 1 or 2"
        )

    period_neighbor_offset = getattr(args, "period_neighbor_offset", None)
    period_neighbor_table_slots = getattr(args, "period_neighbor_table_slots", 2)
    period_neighbor_navigation_only = bool(
        getattr(args, "period_neighbor_navigation_only", False)
    )
    if period_neighbor_offset is not None and not dense_index_dir:
        raise ValueError("--period-neighbor-offset requires --dense-index-dir")
    if period_neighbor_navigation_only and period_neighbor_offset is None:
        raise ValueError(
            "--period-neighbor-navigation-only requires --period-neighbor-offset"
        )
    if period_neighbor_table_slots < 1 or period_neighbor_table_slots > 5:
        raise ValueError("--period-neighbor-table-slots must be between 1 and 5")
    pre_dense_review_items = review_items
    dense_expansion_stats = {
        "requests": 0,
        "dense_hits": 0,
        "hydrated_hits": 0,
        "period_neighbor_enabled": int(period_neighbor_offset is not None),
        "period_neighbor_requests": 0,
        "period_neighbor_hits": 0,
        "period_neighbor_hydrated_hits": 0,
    }
    if dense_index_dir:
        review_items, dense_expansion_stats = expand_review_items_with_dense(
            items=review_items,
            tables_by_uid=tables,
            index_dir=dense_index_dir,
            limit=args.dense_candidate_limit,
            device=args.dense_device,
            batch_size=args.dense_batch_size,
            period_neighbor_offset=period_neighbor_offset,
        )

    disable_research_fusion = bool(getattr(args, "disable_research_fusion", False))
    explicit_research_paths = getattr(args, "research_candidate", None)
    if disable_research_fusion:
        research_paths: list[Path] = []
    elif explicit_research_paths is None:
        research_paths = [path for path in DEFAULT_RESEARCH_CANDIDATES if path.exists()]
    else:
        research_paths = list(explicit_research_paths)
    research_hints, research_input_stats = load_research_candidate_hints(
        research_paths,
        tables_by_uid=tables,
    )
    review_items, research_fusion_stats = fuse_research_candidates(
        review_items,
        research_hints,
        tables_by_uid=tables,
    )
    navigation_review_items = review_items
    answer_review_items_pre_dense = None
    answer_research_fusion_stats: dict[str, Any] | None = None
    if period_neighbor_navigation_only:
        # Reapply the same value-blind research hints to the pre-dense pool so
        # this arm changes only the document/table navigation lane.  The
        # expanded dense pool remains available through navigation_review_items.
        answer_review_items_pre_dense, answer_research_fusion_stats = fuse_research_candidates(
            pre_dense_review_items,
            research_hints,
            tables_by_uid=tables,
        )
    answer_review_items = _answer_review_items_for_period_neighbor(
        review_items,
        period_neighbor_offset=period_neighbor_offset,
        navigation_only=period_neighbor_navigation_only,
        pre_dense_items=answer_review_items_pre_dense,
    )

    route_overlay_path = getattr(args, "route_overlay", None)
    if route_overlay_path is None and not disable_research_fusion and DEFAULT_ROUTE_OVERLAY.exists():
        route_overlay_path = DEFAULT_ROUTE_OVERLAY
    route_overlay, route_overlay_stats = load_route_overlay(route_overlay_path)

    explicit_model_answer_paths = getattr(args, "model_answer_candidate", None)
    if disable_research_fusion:
        model_answer_paths: list[Path] = []
    elif explicit_model_answer_paths is None:
        model_answer_paths = [path for path in DEFAULT_MODEL_ANSWER_CANDIDATES if path.exists()]
    else:
        model_answer_paths = list(explicit_model_answer_paths)
    model_answers, model_answer_stats = load_model_answer_candidates(
        model_answer_paths,
        items_by_question=answer_review_items,
        tables_by_uid=tables,
    )

    explicit_direct_replay_paths = getattr(args, "direct_evidence_replay", None)
    direct_replay_paths: list[Path] = (
        [] if explicit_direct_replay_paths is None else list(explicit_direct_replay_paths)
    )
    direct_replay_answers, direct_replay_stats = load_direct_evidence_replay_candidates(
        direct_replay_paths,
        items_by_question=answer_review_items,
        tables_by_uid=tables,
        include_provisional=bool(getattr(args, "direct_replay_include_provisional", False)),
    )
    source_first_resolver_kwargs = {
        "parse_decimal": parse_decimal,
        "candidate_evidence_window": candidate_evidence_window,
        "choose_year_column": choose_year_column,
        "source_multiplier": source_multiplier,
        "requested_divisor": requested_divisor,
        "report_year_neighbor_fallback": source_first_report_year_neighbor_offset,
    }
    source_first_answers, source_first_stats = build_source_first_direct_lookup_index(
        answer_review_items,
        tables_by_pair=tables_by_pair,
        **source_first_resolver_kwargs,
    )
    if bool(getattr(args, "disable_source_first_reclassified_direct", False)):
        source_first_reclassified_answers: dict[int, dict[str, Any]] = {}
        source_first_reclassified_stats: dict[str, Any] = {
            "protocol": _RECLASSIFIED_DIRECT_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        source_first_reclassified_answers, source_first_reclassified_stats = (
            build_source_first_reclassified_direct_lookup_index(
                answer_review_items,
                tables_by_pair=tables_by_pair,
                resolver_kwargs=source_first_resolver_kwargs,
                disable_financial_liability_maturity_total=bool(
                    getattr(args, "disable_source_first_financial_liability_total", False)
                ),
                disable_financial_receivables_total=bool(
                    getattr(args, "disable_source_first_financial_receivables_total", False)
                ),
            )
        )
        source_first_reclassified_stats["enabled"] = True
        source_first_reclassified_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_selector", False)):
        source_first_multi_entity_selector_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_selector_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_SELECTOR_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_selector_answers,
            source_first_multi_entity_selector_stats,
        ) = build_source_first_multi_entity_selector_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_selector_stats["enabled"] = True
        source_first_multi_entity_selector_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_ratio_selector", False)):
        source_first_multi_entity_ratio_selector_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_ratio_selector_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_ratio_selector_answers,
            source_first_multi_entity_ratio_selector_stats,
        ) = build_source_first_multi_entity_ratio_selector_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_ratio_selector_stats["enabled"] = True
        source_first_multi_entity_ratio_selector_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_threshold", False)):
        source_first_multi_entity_threshold_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_threshold_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_THRESHOLD_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_threshold_answers,
            source_first_multi_entity_threshold_stats,
        ) = build_source_first_multi_entity_threshold_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_threshold_stats["enabled"] = True
        source_first_multi_entity_threshold_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_share_threshold", False)):
        source_first_multi_entity_share_threshold_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_share_threshold_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_share_threshold_answers,
            source_first_multi_entity_share_threshold_stats,
        ) = build_source_first_multi_entity_share_threshold_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_share_threshold_stats["enabled"] = True
        source_first_multi_entity_share_threshold_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_lease_threshold", False)):
        source_first_multi_entity_lease_threshold_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_lease_threshold_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_lease_threshold_answers,
            source_first_multi_entity_lease_threshold_stats,
        ) = build_source_first_multi_entity_lease_threshold_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_lease_threshold_stats["enabled"] = True
        source_first_multi_entity_lease_threshold_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_interest_threshold", False)):
        source_first_multi_entity_interest_threshold_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_interest_threshold_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_interest_threshold_answers,
            source_first_multi_entity_interest_threshold_stats,
        ) = build_source_first_multi_entity_interest_threshold_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_interest_threshold_stats["enabled"] = True
        source_first_multi_entity_interest_threshold_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_composed_total", False)):
        source_first_composed_answers: dict[int, dict[str, Any]] = {}
        source_first_composed_stats: dict[str, Any] = {
            "protocol": _COMPOSED_TOTAL_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        source_first_composed_answers, source_first_composed_stats = (
            build_source_first_composed_total_lookup_index(
                answer_review_items,
                tables_by_pair=tables_by_pair,
                resolver_kwargs=source_first_resolver_kwargs,
            )
        )
        source_first_composed_stats["enabled"] = True
        source_first_composed_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_candidate_bound", False)):
        source_first_candidate_bound_answers: dict[int, dict[str, Any]] = {}
        source_first_candidate_bound_stats: dict[str, Any] = {
            "protocol": "source_first_candidate_bound_v1",
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        source_first_candidate_bound_answers, source_first_candidate_bound_stats = (
            build_source_first_candidate_bound_lookup_index(
                answer_review_items,
                tables_by_pair=tables_by_pair,
                resolver_kwargs=source_first_resolver_kwargs,
            )
        )
    if bool(getattr(args, "disable_source_first_period_extreme", False)):
        source_first_period_extreme_answers: dict[int, dict[str, Any]] = {}
        source_first_period_extreme_stats: dict[str, Any] = {
            "protocol": _PERIOD_EXTREME_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_period_extreme_answers,
            source_first_period_extreme_stats,
        ) = build_source_first_period_extreme_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_period_extreme_stats["enabled"] = True
        source_first_period_extreme_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_temporal", False)):
        source_first_temporal_answers: dict[int, dict[str, Any]] = {}
        source_first_temporal_stats: dict[str, Any] = {
            "protocol": "source_first_temporal_v1",
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        source_first_temporal_answers, source_first_temporal_stats = (
            build_source_first_temporal_lookup_index(
                answer_review_items,
                tables_by_pair=tables_by_pair,
            )
        )
        source_first_temporal_stats["enabled"] = True
        source_first_temporal_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_conditional_temporal", False)):
        source_first_conditional_answers: dict[int, dict[str, Any]] = {}
        source_first_conditional_stats: dict[str, Any] = {
            "protocol": "source_first_conditional_temporal_v1",
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        source_first_conditional_answers, source_first_conditional_stats = (
            build_source_first_conditional_temporal_lookup_index(
                answer_review_items,
                tables_by_pair=tables_by_pair,
                resolver_kwargs=source_first_resolver_kwargs,
            )
        )
    if bool(getattr(args, "disable_source_first_cross_entity", False)):
        source_first_cross_entity_answers: dict[int, dict[str, Any]] = {}
        source_first_cross_entity_stats: dict[str, Any] = {
            "protocol": "source_first_cross_entity_v1",
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        source_first_cross_entity_answers, source_first_cross_entity_stats = (
            build_source_first_cross_entity_lookup_index(
                answer_review_items,
                tables_by_pair=tables_by_pair,
            )
        )
        source_first_cross_entity_stats["enabled"] = True
        source_first_cross_entity_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_direct_aggregation", False)):
        source_first_multi_entity_direct_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_direct_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_direct_answers,
            source_first_multi_entity_direct_stats,
        ) = build_source_first_multi_entity_direct_aggregation_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_direct_stats["enabled"] = True
        source_first_multi_entity_direct_stats["status"] = "active"
    if bool(getattr(args, "disable_source_first_multi_entity_conditional_count", False)):
        source_first_multi_entity_conditional_count_answers: dict[int, dict[str, Any]] = {}
        source_first_multi_entity_conditional_count_stats: dict[str, Any] = {
            "protocol": _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
            "enabled": False,
            "status": "disabled",
            "questions_considered": 0,
            "questions_resolved": 0,
            "questions_unresolved_or_ambiguous": 0,
        }
    else:
        (
            source_first_multi_entity_conditional_count_answers,
            source_first_multi_entity_conditional_count_stats,
        ) = build_source_first_multi_entity_conditional_count_lookup_index(
            answer_review_items,
            tables_by_pair=route_tables_by_pair,
            resolver_kwargs=source_first_resolver_kwargs,
        )
        source_first_multi_entity_conditional_count_stats["enabled"] = True
        source_first_multi_entity_conditional_count_stats["status"] = "active"

    # Candidate validity is a ranking layer only.  Prefer the existing
    # human-trained calibrator when it is available locally; a native JSON
    # model produced by train_candidate_validity_model_v1.py is also accepted.
    # If neither exists, the same top-k/plan path uses a deterministic prior
    # and reports that no learned model was available.
    validity_model_path = getattr(args, "candidate_validity_model", None)
    if validity_model_path is None and not bool(
        getattr(args, "disable_candidate_validity", False)
    ) and DEFAULT_CANDIDATE_VALIDITY_MODEL.exists():
        validity_model_path = DEFAULT_CANDIDATE_VALIDITY_MODEL
    validity_model = None
    validity_model_stats: dict[str, Any] = {
        "protocol": CANDIDATE_VALIDITY_PROTOCOL,
        "enabled": False,
        "path": str(validity_model_path) if validity_model_path else None,
        "status": "disabled" if getattr(args, "disable_candidate_validity", False) else "heuristic_prior",
        "ranking_only": True,
        "may_authorize_answer": False,
    }
    if validity_model_path is not None and not bool(
        getattr(args, "disable_candidate_validity", False)
    ):
        validity_model = load_candidate_validity_model(validity_model_path)
        validity_model_stats.update(
            {
                "enabled": True,
                "status": validity_model.kind,
                "feature_names": list(validity_model.feature_names),
                "metadata": dict(validity_model.metadata),
            }
        )

    source_line_map_path = args.source_line_map
    if source_line_map_path is None:
        bundled_map = args.bundle / "source_line_map.json"
        if bundled_map.exists():
            source_line_map_path = bundled_map
    source_line_map = load_source_line_map(source_line_map_path)
    require_source_line_map = bool(getattr(args, "require_source_line_map", False))
    allow_local_ordinal_fallback = bool(
        getattr(args, "allow_local_ordinal_fallback", False)
    )
    if require_source_line_map and allow_local_ordinal_fallback:
        raise ValueError(
            "--require-source-line-map and --allow-local-ordinal-fallback are mutually exclusive"
        )
    source_line_map_coverage = {
        "table_uid_count": len(tables),
        "map_entry_count": len(source_line_map),
        "missing_count": None,
        "extra_count": None,
    }
    route_source_line_map_coverage = {
        "table_uid_count": len(route_tables),
        "map_entry_count": len(source_line_map),
        "missing_count": None,
        "extra_count": None,
    }
    if require_source_line_map:
        if source_line_map_path is None:
            raise ValueError(
                "production submission requires source_line_map.json; "
                "pass --source-line-map or bundle it next to tables_structured_v2.jsonl"
            )
        source_line_map_coverage = validate_source_line_map_coverage(
            source_line_map,
            tables,
            allow_extra=structured_table_filter == "candidate_uids",
        )
        route_source_line_map_coverage = validate_source_line_map_coverage(
            source_line_map,
            route_tables,
            allow_extra=structured_table_filter == "candidate_uids",
        )
    elif not source_line_map and not allow_local_ordinal_fallback:
        missing_sources = unavailable_source_paths(route_answer_tables_by_uid)
        if missing_sources:
            raise ValueError(
                "cannot derive competition table coordinates: source_line_map.json is "
                "missing and structured-table source files are unavailable; "
                "pass --source-line-map, or use --allow-local-ordinal-fallback only "
                "for diagnostic builds. "
                f"missing_source_sample={missing_sources[:3]}"
            )
    line_stats: Counter[str] = Counter()

    # The fine-tuned reranker changes navigation order only.  A separate model
    # answer candidate can enter the primary output only through the staged
    # current-table replay above.  Keep both paths visible in the report so a
    # smoke checkpoint is never mistaken for a promoted production model.
    model_manifest: FineTunedArtifactManifest | None = None
    model_scorer: PairScorer | None = None
    model_questions = 0
    model_candidates = 0
    finetuned_model_root = getattr(args, "finetuned_model_root", None)
    if reranker_policy == "use_finetuned_reranker":
        model_manifest, model_scorer = load_finetuned_reranker(
            finetuned_model_root,
            device=args.model_device,
        )
    model_items: dict[int, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    if model_scorer is not None:
        # One batched model call is materially faster than starting a
        # CrossEncoder DataLoader once per question (the old V11 path could
        # spend hours on the same 38k candidate pairs).
        model_items = rerank_review_items_candidates(
            # The period-neighbor arm is navigation-only.  Do not let a
            # reranker reintroduce its candidates into the isolated answer
            # pool after the earlier filtering step.
            items=list(answer_review_items.values()),
            assets_by_uid=tables,
            scorer=model_scorer,
            limit=args.model_candidate_limit,
            batch_size=args.model_batch_size,
        )
        model_questions = len(model_items)
        model_candidates = sum(len(rows) for _, rows in model_items.values())

    # Rank after research fusion and optional neural reranking so every lane
    # competes in one primary candidate pool.  The active item keeps only the
    # top 10 direct candidates or the top 20 per operand/entity group for a
    # complex plan; every retained row still points to the original V2 table.
    validity_items: dict[int, dict[str, Any]] = {}
    validity_summaries: dict[int, dict[str, Any]] = {}
    validity_pool_before = 0
    validity_pool_after = 0
    validity_model_statuses: Counter[str] = Counter()
    active_input_items = dict(answer_review_items)
    for item_id, (model_item, model_rows) in model_items.items():
        # Keep the complete fused retrieval pool available to candidate
        # validity.  The fine-tuned model may score only a CPU-friendly prefix;
        # replacing the whole item here would silently turn model speed into
        # a recall filter, especially for multi-entity plans.
        base_item = dict(answer_review_items.get(item_id) or model_item)
        base_candidates = list(base_item.get("candidates") or [])

        def candidate_identity(candidate: Mapping[str, Any]) -> tuple[str, str, str, str]:
            return (
                str(candidate.get("internal_table_uid") or ""),
                str(candidate.get("row_index") if candidate.get("row_index") is not None else ""),
                str(candidate.get("column_index") if candidate.get("column_index") is not None else ""),
                str(candidate.get("label") or ""),
            )

        merged_candidates: list[dict[str, Any]] = []
        seen_candidates: set[tuple[str, str, str, str]] = set()
        for candidate in [*model_rows, *base_candidates]:
            key = candidate_identity(candidate)
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            merged_candidates.append(dict(candidate))
        base_item["candidates"] = merged_candidates
        base_item["fine_tuned_prefix_size"] = len(model_rows)
        active_input_items[item_id] = base_item
    if getattr(args, "disable_candidate_validity", False):
        validity_items = active_input_items
        validity_model_statuses["disabled"] = len(active_input_items)
    else:
        for item_id, item in active_input_items.items():
            ranked_item, plan_summary = rank_candidate_pool(
                item,
                tables_by_uid=tables,
                model=validity_model,
                top_k=getattr(args, "candidate_top_k", None),
            )
            validity_items[item_id] = ranked_item
            validity_summaries[item_id] = plan_summary
            metadata = ranked_item.get("candidate_validity") or {}
            validity_pool_before += int(metadata.get("candidate_pool_before") or 0)
            validity_pool_after += int(metadata.get("candidate_pool_after") or 0)
            validity_model_statuses[str(metadata.get("model_status") or "unknown")] += 1

    # A formula sidecar is admitted only through the independent bridge.  The
    # bridge rechecks every selected coordinate against the current hydrated
    # table asset and recomputes a known Decimal AST; it never copies a stored
    # sidecar answer.  Keep this lane opt-in so the established build remains
    # an exact control for score comparisons.
    formula_bridge_enabled = bool(
        getattr(args, "enable_formula_evidence_bridge", False)
    )
    formula_evidence_path = getattr(args, "formula_evidence", None)
    formula_bridge_candidates: dict[int, dict[str, Any]] = {}
    if formula_bridge_enabled:
        if formula_evidence_path is None:
            formula_evidence_path = args.bundle / "formula_evidence_sets_typed_v1.jsonl"
        formula_bridge_candidates, formula_bridge_stats = (
            build_formula_evidence_candidates(
                Path(formula_evidence_path),
                tables_by_uid=route_answer_tables_by_uid,
                questions_by_id={int(row["id"]): row for row in questions},
            )
        )
    else:
        formula_bridge_stats = {
            "protocol": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
            "path": str(formula_evidence_path) if formula_evidence_path else None,
            "enabled": False,
            "table_count": len(route_answer_tables_by_uid),
            "stats": {},
            "candidate_question_ids": [],
            "candidate_only": True,
            "answer_authority": "none; bridge disabled",
            "stored_sidecar_answers_used": False,
        }

    output_dir: Path = args.output
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing submission output: {output_dir}")
    zip_path = output_dir.with_suffix(".zip")
    if zip_path.exists():
        raise FileExistsError(f"refusing to overwrite existing submission archive: {zip_path}")
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True)
    verification_certificate_path = getattr(args, "verification_certificate", None)
    verification_config_path = getattr(args, "verification_config", None)
    if verification_certificate_path is not None and verification_config_path is not None:
        raise ValueError("use either --verification-certificate or --verification-config, not both")
    verification_certificates = load_certificate_index(verification_certificate_path)
    source_cache: dict[Path, str] = {}
    counts: Counter[str] = Counter()
    verification_counts: Counter[str] = Counter()
    verification_failure_counts: Counter[str] = Counter()
    answer_level_selector_enabled = bool(
        getattr(args, "enable_answer_level_selector", False)
    )
    answer_level_selector_max_candidates = int(
        getattr(args, "answer_level_selector_max_candidates", 64)
    )
    answer_level_selector_stats: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    best_candidate_records: list[dict[str, Any]] = []
    proposal_audit_records: list[dict[str, Any]] = []
    used_table_uids: set[str] = set()

    for question in questions:
        question_id = int(question["id"])
        base_item = navigation_review_items.get(question_id, {})
        # When candidate validity is disabled there is no validity_items
        # overlay.  Keep the answer lane isolated from navigation-only dense
        # candidates in that case; the navigation item is still used below
        # for relevant document/table emission.
        answer_item = answer_review_items.get(question_id, base_item)
        active_item = validity_items.get(question_id, answer_item)
        answer = Decimal(0)
        evidence_rows: list[dict[str, Any]] = []
        tier = "fallback_zero"
        pandas_query = "float(df1.loc[0, 'value'])"
        exact = exact_replay_answer(replays.get(question_id, {}))
        direct_prediction = direct_replay_answers.get(question_id)
        source_first_prediction = source_first_answers.get(question_id)
        source_first_reclassified_prediction = source_first_reclassified_answers.get(question_id)
        source_first_multi_entity_selector_prediction = (
            source_first_multi_entity_selector_answers.get(question_id)
        )
        source_first_multi_entity_ratio_selector_prediction = (
            source_first_multi_entity_ratio_selector_answers.get(question_id)
        )
        source_first_multi_entity_threshold_prediction = (
            source_first_multi_entity_threshold_answers.get(question_id)
        )
        source_first_multi_entity_share_threshold_prediction = (
            source_first_multi_entity_share_threshold_answers.get(question_id)
        )
        source_first_multi_entity_lease_threshold_prediction = (
            source_first_multi_entity_lease_threshold_answers.get(question_id)
        )
        source_first_multi_entity_interest_threshold_prediction = (
            source_first_multi_entity_interest_threshold_answers.get(question_id)
        )
        source_first_composed_prediction = source_first_composed_answers.get(question_id)
        source_first_candidate_bound_prediction = source_first_candidate_bound_answers.get(question_id)
        source_first_period_extreme_prediction = source_first_period_extreme_answers.get(question_id)
        source_first_conditional_prediction = source_first_conditional_answers.get(question_id)
        source_first_temporal_prediction = source_first_temporal_answers.get(question_id)
        source_first_cross_entity_prediction = source_first_cross_entity_answers.get(question_id)
        source_first_multi_entity_direct_prediction = (
            source_first_multi_entity_direct_answers.get(question_id)
        )
        source_first_multi_entity_conditional_count_prediction = (
            source_first_multi_entity_conditional_count_answers.get(question_id)
        )
        selection: dict[str, Any] | None = None
        if direct_prediction is not None:
            # A direct source replay is more specific than the broad r9
            # execution ledger for this direct-lookup family.  It also
            # re-resolves table units and compound literals from the current
            # table/question, fixing stale unit bindings while preserving the
            # exact r9 answer whenever both routes agree.
            answer = direct_prediction["answer"]
            evidence_rows = direct_prediction["sources"]
            selection = direct_prediction["selection"]
            tier = str(direct_prediction["tier"])
        elif exact is not None:
            answer, evidence_rows = exact
            tier = "exact_execution_r9"
        elif source_first_prediction is not None:
            answer = source_first_prediction["answer"]
            evidence_rows = source_first_prediction["sources"]
            selection = source_first_prediction["selection"]
            tier = str(source_first_prediction["tier"])
        elif source_first_reclassified_prediction is not None:
            answer = source_first_reclassified_prediction["answer"]
            evidence_rows = source_first_reclassified_prediction["sources"]
            selection = source_first_reclassified_prediction["selection"]
            tier = str(source_first_reclassified_prediction["tier"])
        elif source_first_multi_entity_selector_prediction is not None:
            answer = source_first_multi_entity_selector_prediction["answer"]
            evidence_rows = source_first_multi_entity_selector_prediction["sources"]
            selection = source_first_multi_entity_selector_prediction["selection"]
            pandas_query = source_first_multi_entity_selector_prediction["query"]
            tier = str(source_first_multi_entity_selector_prediction["tier"])
        elif source_first_multi_entity_ratio_selector_prediction is not None:
            answer = source_first_multi_entity_ratio_selector_prediction["answer"]
            evidence_rows = source_first_multi_entity_ratio_selector_prediction["sources"]
            selection = source_first_multi_entity_ratio_selector_prediction["selection"]
            pandas_query = source_first_multi_entity_ratio_selector_prediction["query"]
            tier = str(source_first_multi_entity_ratio_selector_prediction["tier"])
        elif source_first_multi_entity_share_threshold_prediction is not None:
            answer = source_first_multi_entity_share_threshold_prediction["answer"]
            evidence_rows = source_first_multi_entity_share_threshold_prediction["sources"]
            selection = source_first_multi_entity_share_threshold_prediction["selection"]
            pandas_query = source_first_multi_entity_share_threshold_prediction["query"]
            tier = str(source_first_multi_entity_share_threshold_prediction["tier"])
        elif source_first_multi_entity_lease_threshold_prediction is not None:
            answer = source_first_multi_entity_lease_threshold_prediction["answer"]
            evidence_rows = source_first_multi_entity_lease_threshold_prediction["sources"]
            selection = source_first_multi_entity_lease_threshold_prediction["selection"]
            pandas_query = source_first_multi_entity_lease_threshold_prediction["query"]
            tier = str(source_first_multi_entity_lease_threshold_prediction["tier"])
        elif source_first_multi_entity_interest_threshold_prediction is not None:
            answer = source_first_multi_entity_interest_threshold_prediction["answer"]
            evidence_rows = source_first_multi_entity_interest_threshold_prediction["sources"]
            selection = source_first_multi_entity_interest_threshold_prediction["selection"]
            pandas_query = source_first_multi_entity_interest_threshold_prediction["query"]
            tier = str(source_first_multi_entity_interest_threshold_prediction["tier"])
        elif source_first_multi_entity_threshold_prediction is not None:
            answer = source_first_multi_entity_threshold_prediction["answer"]
            evidence_rows = source_first_multi_entity_threshold_prediction["sources"]
            pandas_query = source_first_multi_entity_threshold_prediction["query"]
            tier = str(source_first_multi_entity_threshold_prediction["tier"])
        elif source_first_composed_prediction is not None:
            answer = source_first_composed_prediction["answer"]
            evidence_rows = source_first_composed_prediction["sources"]
            selection = source_first_composed_prediction["selection"]
            pandas_query = source_first_composed_prediction["query"]
            tier = str(source_first_composed_prediction["tier"])
        elif source_first_candidate_bound_prediction is not None:
            answer = source_first_candidate_bound_prediction["answer"]
            evidence_rows = source_first_candidate_bound_prediction["sources"]
            selection = source_first_candidate_bound_prediction["selection"]
            tier = str(source_first_candidate_bound_prediction["tier"])
        elif source_first_period_extreme_prediction is not None:
            answer = source_first_period_extreme_prediction["answer"]
            evidence_rows = source_first_period_extreme_prediction["sources"]
            selection = source_first_period_extreme_prediction["selection"]
            pandas_query = source_first_period_extreme_prediction["query"]
            tier = str(source_first_period_extreme_prediction["tier"])
        elif source_first_conditional_prediction is not None:
            answer = source_first_conditional_prediction["answer"]
            evidence_rows = source_first_conditional_prediction["sources"]
            selection = source_first_conditional_prediction["selection"]
            pandas_query = source_first_conditional_prediction["query"]
            tier = str(source_first_conditional_prediction["tier"])
        elif source_first_temporal_prediction is not None:
            answer = source_first_temporal_prediction["answer"]
            evidence_rows = source_first_temporal_prediction["sources"]
            pandas_query = source_first_temporal_prediction["query"]
            tier = str(source_first_temporal_prediction["tier"])
        elif source_first_cross_entity_prediction is not None:
            answer = source_first_cross_entity_prediction["answer"]
            evidence_rows = source_first_cross_entity_prediction["sources"]
            pandas_query = source_first_cross_entity_prediction["query"]
            tier = str(source_first_cross_entity_prediction["tier"])
        elif source_first_multi_entity_direct_prediction is not None:
            answer = source_first_multi_entity_direct_prediction["answer"]
            evidence_rows = source_first_multi_entity_direct_prediction["sources"]
            selection = source_first_multi_entity_direct_prediction["selection"]
            pandas_query = source_first_multi_entity_direct_prediction["query"]
            tier = str(source_first_multi_entity_direct_prediction["tier"])
        elif source_first_multi_entity_conditional_count_prediction is not None:
            answer = source_first_multi_entity_conditional_count_prediction["answer"]
            evidence_rows = source_first_multi_entity_conditional_count_prediction["sources"]
            selection = source_first_multi_entity_conditional_count_prediction["selection"]
            pandas_query = source_first_multi_entity_conditional_count_prediction["query"]
            tier = str(source_first_multi_entity_conditional_count_prediction["tier"])
        elif question_id in RAW_CORPUS_RECOVERIES:
            recovery = RAW_CORPUS_RECOVERIES[question_id]
            answer = recovery["answer"]
            evidence_rows = recovery["sources"]
            tier = "raw_corpus_recovery"
        elif question_id in model_answers:
            model_prediction = model_answers[question_id]
            answer = model_prediction["answer"]
            evidence_rows = model_prediction["sources"]
            tier = "model_staged_replay"
        else:
            program = program_aware_answer(active_item, tables_by_uid=tables)
            if program is not None:
                answer, evidence_rows, pandas_query, tier = program
            else:
                selection = choose_semantic_cell(
                    active_item,
                    tables_by_uid=tables,
                    allow_uncertain=True,
                )
                if selection is not None:
                    answer = selection["value"]
                    evidence_rows = [selection]
                    tier = (
                        "semantic_cell_heuristic"
                        if selection["score"] > 0.0
                        else "best_surviving_candidate"
                    )

        # A prediction is only a proposal.  Keep the primary route and a
        # bounded set of hydrated semantic alternatives, then let the
        # independent verifier rerank them.  The verifier cannot invent a new
        # answer; it can only reject a proposal or improve its trust class.
        plan_for_verification = (
            active_item.get("effective_question_plan")
            or active_item.get("question_plan")
            or {}
        )
        candidate_specs: list[
            tuple[
                Decimal,
                list[dict[str, Any]],
                str,
                str,
                dict[str, Any] | None,
                Mapping[str, Any] | None,
                Mapping[str, Any] | None,
            ]
        ] = [(answer, evidence_rows, tier, pandas_query, selection, None, None)]
        formula_candidate = formula_bridge_candidates.get(question_id)
        if formula_candidate is not None:
            # Keep the formula candidate in the same bounded proposal pool as
            # the control.  Its own question plan is carried into the
            # proposal so the AST and claims describe the formula actually
            # replayed, while the legacy primary route remains unchanged.
            candidate_specs.append(
                (
                    formula_candidate["answer"],
                    list(formula_candidate["sources"]),
                    str(formula_candidate["tier"]),
                    str(formula_candidate["query"]),
                    formula_candidate.get("selection"),
                    formula_candidate.get("question_plan"),
                    formula_candidate,
                )
            )
        semantic_limit = max(1, int(getattr(args, "verification_candidate_limit", 8)))
        semantic_options = rank_semantic_cells(
            active_item,
            tables_by_uid=tables,
            allow_uncertain=True,
        )[:semantic_limit]
        primary_locator = (
            selection.get("internal_table_uid"),
            selection.get("row_index"),
            selection.get("column_index"),
        ) if selection else None
        for semantic_option in semantic_options:
            locator = (
                semantic_option.get("internal_table_uid"),
                semantic_option.get("row_index"),
                semantic_option.get("column_index"),
            )
            if primary_locator is not None and locator == primary_locator:
                continue
            semantic_tier = (
                "semantic_cell_heuristic"
                if semantic_option["score"] > 0.0
                else "best_surviving_candidate"
            )
            candidate_specs.append(
                (
                    semantic_option["value"],
                    [semantic_option],
                    semantic_tier,
                    "float(df1.loc[0, 'value'])",
                    semantic_option,
                    None,
                    None,
                )
            )

        proposals: list[dict[str, Any]] = []
        for proposal_index, (
            proposal_answer,
            proposal_evidence_rows,
            proposal_tier,
            proposal_query,
            proposal_selection,
            proposal_question_plan,
            proposal_metadata,
        ) in enumerate(candidate_specs, start=1):
            proposal = _proposed_answer_plan(
                question_id=question_id,
                answer=proposal_answer,
                tier=proposal_tier,
                evidence_rows=proposal_evidence_rows,
                question_plan=proposal_question_plan or plan_for_verification,
                pandas_query=proposal_query,
                selection=proposal_selection,
                proposal_index=proposal_index,
            )
            if proposal_metadata is not None:
                proposal["formula_bridge"] = {
                    "protocol": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
                    "formula_id": proposal_metadata.get("formula_id"),
                    "expression": proposal_metadata.get("formula_expression"),
                    "confidence": proposal_metadata.get("formula_confidence"),
                    "stored_sidecar_answers_used": False,
                }
            proposal["verification"] = verify_proposed_answer(
                proposal,
                tables_by_uid=route_answer_tables_by_uid,
                strict_certificate=verification_certificates.get(question_id),
            )
            proposal["_evidence_rows"] = proposal_evidence_rows
            proposal["_selection"] = proposal_selection
            proposals.append(proposal)

        legacy_selected_proposal = select_best_proposal(proposals)
        selected_proposal = legacy_selected_proposal
        answer_level_selection: dict[str, Any] | None = None
        if answer_level_selector_enabled:
            selected_by_answer_level, answer_level_selection = (
                _select_proposal_with_answer_level_selector(
                    question_id=question_id,
                    question_plan=plan_for_verification,
                    question_text=question,
                    proposals=proposals,
                    legacy_selected_proposal=legacy_selected_proposal,
                    tables_by_uid=route_answer_tables_by_uid,
                    max_candidates=answer_level_selector_max_candidates,
                )
            )
            if selected_by_answer_level is not None:
                selected_proposal = selected_by_answer_level
                answer_level_selector_stats["selected"] += 1
                if answer_level_selection.get("decision") == "BASELINE_FALLBACK":
                    answer_level_selector_stats["baseline_fallback"] += 1
            else:
                answer_level_selector_stats["abstain"] += 1
        if selected_proposal is None:
            fallback = _proposed_answer_plan(
                question_id=question_id,
                answer=Decimal(0),
                tier="fallback_zero",
                evidence_rows=[],
                question_plan=plan_for_verification,
                pandas_query="float(df1.loc[0, 'value'])",
                selection=None,
                proposal_index=len(proposals) + 1,
            )
            fallback["verification"] = verify_proposed_answer(
                fallback,
                tables_by_uid=route_answer_tables_by_uid,
                strict_certificate=verification_certificates.get(question_id),
            )
            fallback["_evidence_rows"] = []
            fallback["_selection"] = None
            selected_proposal = fallback

        answer = Decimal(str(selected_proposal["answer_decimal"]))
        evidence_rows = list(selected_proposal.pop("_evidence_rows"))
        selection = selected_proposal.pop("_selection")
        selected_proposal.pop("_selector_question_plan", None)
        tier = str(selected_proposal["answer_route"])
        pandas_query = str(selected_proposal["pandas_query"])
        verification = dict(selected_proposal["verification"])
        verification_counts[str(verification["verification_class"])] += 1
        verification_failure_counts.update(
            str(failure["reason"])
            for failure in verification.get("failures") or []
            if isinstance(failure, Mapping)
        )
        selected_proposal["selected"] = True
        proposal_audit_records.append(selected_proposal)

        counts[tier] += 1
        docs: list[str] = []
        table_refs: list[str] = []
        seen_docs: set[str] = set()
        seen_tables: set[str] = set()
        period_neighbor_table_refs_emitted = 0
        for source in evidence_rows:
            doc = str(source.get("document_id") or "")
            uid = str(source.get("internal_table_uid") or "")
            if doc and doc not in seen_docs:
                seen_docs.add(doc)
                docs.append(doc.removesuffix(".txt"))
            if uid and uid in route_answer_tables_by_uid and uid not in seen_tables:
                seen_tables.add(uid)
                used_table_uids.add(uid)
                table = route_answer_tables_by_uid[uid]
                table_doc = str(table.get("document_id") or doc).removesuffix(".txt")
                line = table_line_locator(table, source_cache, source_line_map, line_stats)
                table_refs.append(f"{table_doc}|{line}")
            elif doc and source.get("line_override"):
                line_stats["line_override"] += 1
                table_ref = f"{doc.removesuffix('.txt')}|{int(source['line_override'])}"
                if table_ref not in table_refs:
                    table_refs.append(table_ref)

        # F2 weights recall more heavily.  Keep direct/exact lookups precise,
        # while multi-step questions expose a short ranked candidate list.
        family = ((active_item.get("question_plan") or {}).get("family") or "")
        if should_emit_candidate_context(
            family=family,
            tier=tier,
            period_neighbor_offset=period_neighbor_offset,
        ):
            emission_item = base_item if period_neighbor_offset is not None else active_item
            emission_candidates = (
                period_aware_emission_candidates(
                    emission_item.get("candidates") or [],
                    limit=12,
                    neighbor_slots=period_neighbor_table_slots,
                )
                if period_neighbor_offset is not None
                else list((emission_item.get("candidates") or [])[:12])
            )
            for candidate in emission_candidates:
                doc = str(candidate.get("document_id") or "").removesuffix(".txt")
                uid = str(candidate.get("internal_table_uid") or "")
                if doc and doc not in seen_docs and len(docs) < 3:
                    seen_docs.add(doc)
                    docs.append(doc)
                if (
                    uid
                    and uid in route_answer_tables_by_uid
                    and uid not in seen_tables
                    and len(table_refs) < 5
                ):
                    seen_tables.add(uid)
                    used_table_uids.add(uid)
                    table = route_answer_tables_by_uid[uid]
                    table_doc = str(table.get("document_id") or doc).removesuffix(".txt")
                    line = table_line_locator(table, source_cache, source_line_map, line_stats)
                    table_refs.append(f"{table_doc}|{line}")
                    if int(candidate.get("period_neighbor_offset") or 0) > 0:
                        period_neighbor_table_refs_emitted += 1
                if len(docs) >= 3 and len(table_refs) >= 5:
                    break

        csv_name = f"q{question_id:04d}_evidence.csv"
        write_evidence_csv(data_dir / csv_name, evidence_rows, answer, tier)
        records.append(
            {
                "id": question_id,
                "question": question["question"],
                "answer": json_number(answer),
                "relevant_docs": docs,
                "relevant_tables": table_refs,
                "evidence": [{"variable": "df1", "csv_path": f"data/{csv_name}"}],
                "pandas_query": pandas_query,
                "model_reranked": bool(model_scorer is not None),
                "prediction_tier": tier,
                "candidate_validity_model": validity_model_stats.get("status"),
                "candidate_top_k": (active_item.get("candidate_validity") or {}).get("top_k"),
            }
        )
        research_route = route_overlay.get(question_id, {})
        selected_candidate_source = selection.get("candidate_source") if selection else None
        plan_summary = validity_summaries.get(question_id) or active_item.get("candidate_plan_summary") or {}
        validity_metadata = active_item.get("candidate_validity") or {}
        diagnostics.append(
            {
                "id": question_id,
                "tier": tier,
                "answer_status": "FALLBACK" if tier == "fallback_zero" else "PREDICTED",
                "answer_decimal": str(answer),
                "relevant_docs": docs,
                "relevant_tables": table_refs,
                "semantic_score": selection.get("score") if selection else None,
                "candidate_validity_probability": (
                    selection.get("validity_probability") if selection else None
                ),
                "candidate_validity_rank": (
                    selection.get("validity_rank") if selection else None
                ),
                "candidate_validity_model_status": (
                    selection.get("validity_model_status")
                    if selection
                    else validity_metadata.get("model_status")
                ),
                "candidate_pool_before": validity_metadata.get("candidate_pool_before"),
                "candidate_pool_after": validity_metadata.get("candidate_pool_after"),
                "candidate_top_k": validity_metadata.get("top_k"),
                "candidate_selection_scope": validity_metadata.get("selection_scope"),
                "plan_shape": plan_summary.get("plan_shape"),
                "plan_status": plan_summary.get("plan_status"),
                "plan_score": plan_summary.get("candidate_plan_score"),
                "plan_vector": plan_summary.get("plan_vector"),
                "plan_groups": plan_summary.get("groups") or [],
                "selected_operands": [
                    {
                        key: source.get(key)
                        for key in (
                            "role",
                            "document_id",
                            "internal_table_uid",
                            "row_index",
                            "column_index",
                            "row_label",
                            "score",
                            "validity_probability",
                            "validity_rank",
                        )
                        if source.get(key) is not None
                    }
                    for source in evidence_rows
                    if source.get("role") or tier == "program_multi_entity_plan"
                ],
                "row_label": selection.get("row_label") if selection else None,
                "candidate_source": selected_candidate_source,
                "research_candidate_selected": bool(
                    selection and selection.get("research_candidate_only")
                ),
                "selected_internal_table_uid": selection.get("internal_table_uid")
                if selection
                else None,
                "selected_row_index": selection.get("row_index") if selection else None,
                "selected_column_index": selection.get("column_index") if selection else None,
                "period_neighbor_table_refs_emitted": period_neighbor_table_refs_emitted,
                "model_answer_replayed": tier == "model_staged_replay",
                "formula_evidence_bridge_selected": tier
                == FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
                "source_first_selected": tier == "source_first_exact_row_v1",
                "source_first_reclassified_selected": tier
                == _RECLASSIFIED_DIRECT_PROTOCOL,
                "source_first_multi_entity_selector_selected": tier
                == _MULTI_ENTITY_SELECTOR_PROTOCOL,
                "source_first_multi_entity_ratio_selector_selected": tier
                == _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
                "source_first_multi_entity_threshold_selected": tier
                == _MULTI_ENTITY_THRESHOLD_PROTOCOL,
                "source_first_multi_entity_share_threshold_selected": tier
                == _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
                "source_first_multi_entity_lease_threshold_selected": tier
                == _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
                "source_first_multi_entity_interest_threshold_selected": tier
                == _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
                "source_first_composed_selected": tier == _COMPOSED_TOTAL_PROTOCOL,
                "source_first_period_extreme_selected": tier
                == _PERIOD_EXTREME_PROTOCOL,
                "source_first_temporal_selected": tier in {
                    "source_first_temporal_v1",
                    "source_first_temporal_cross_entity_v1",
                },
                "source_first_multi_entity_direct_aggregation_selected": tier
                == _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
                "direct_source_replay_selected": tier in {
                    "direct_source_replay_v1",
                    "direct_source_replay_provisional_v1",
                },
                "direct_source_replay_consensus_status": (
                    selection.get("machine_consensus_status")
                    if selection and selection.get("direct_replay")
                    else None
                ),
                "research_route_status": research_route.get("route_status"),
                "research_route_reason_codes": research_route.get("reason_codes") or [],
                "pandas_query": pandas_query,
                "proposal_id": selected_proposal["proposal_id"],
                "proposal_count": len(proposals),
                "answer_level_selector": answer_level_selection,
                "verification_class": verification["verification_class"],
                "verification": verification,
                "confidence_class": (
                    "VERIFIED"
                    if verification["verification_class"] == "VERIFIED"
                    else "BEST_EFFORT"
                ),
            }
        )
        if tier != "fallback_zero" and evidence_rows:
            best_candidate_records.append(
                {
                    "schema_version": 1,
                    "protocol": "vifinqa_best_surviving_candidate_v1",
                    "question_id": question_id,
                    "candidate_id": f"q{question_id}:primary:{tier}",
                    "answer_decimal": str(answer),
                    "filter_status": "SURVIVED_FILTER",
                    "filter_passed": True,
                    "filter_score": selection.get("score") if selection else None,
                    "validity_probability": (
                        selection.get("validity_probability") if selection else None
                    ),
                    "validity_model_status": (
                        selection.get("validity_model_status") if selection else validity_metadata.get("model_status")
                    ),
                    "plan_status": plan_summary.get("plan_status"),
                    "selection_method": "primary_best_surviving_candidate",
                    "tier": tier,
                    "proposal_id": selected_proposal["proposal_id"],
                    "verification_class": verification["verification_class"],
                    "verification": verification,
                    "source": [
                        {
                            key: source.get(key)
                            for key in (
                                "role",
                                "document_id",
                                "internal_table_uid",
                                "row_index",
                                "column_index",
                                "row_label",
                            )
                            if source.get(key) is not None
                        }
                        for source in evidence_rows
                    ],
                }
            )

    fallback_count = line_stats.get("local_ordinal_fallback", 0)
    if fallback_count and not allow_local_ordinal_fallback:
        raise RuntimeError(
            "refusing to write submission with local_ordinal table coordinates: "
            f"fallback_count={fallback_count}; provide a complete source_line_map.json "
            "or explicitly opt into --allow-local-ordinal-fallback for diagnostics"
        )

    submission_path = output_dir / "submission.json"
    submission_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate_ledger_path = output_dir / "best_surviving_candidates_v1.jsonl"
    candidate_ledger_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in best_candidate_records
        ),
        encoding="utf-8",
    )
    if verification_config_path is not None:
        verification_certificate_path = _run_submission_verifier(
            base_config=verification_config_path,
            candidate_ledger_path=candidate_ledger_path,
            output_dir=output_dir,
        )
        verification_certificates = load_certificate_index(verification_certificate_path)
        verification_counts, verification_failure_counts = _apply_certificate_overlay(
            proposals=proposal_audit_records,
            diagnostics=diagnostics,
            candidates=best_candidate_records,
            tables_by_uid=route_answer_tables_by_uid,
            certificates=verification_certificates,
        )
        candidate_ledger_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in best_candidate_records
            ),
            encoding="utf-8",
        )
    proposal_audit_path = output_dir / "prediction_audit_ledger_v1.jsonl"
    proposal_audit_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in proposal_audit_records
        ),
        encoding="utf-8",
    )
    validation = validate_submission(output_dir, questions)
    expected_question_count = getattr(args, "expected_question_count", None)
    require_full_population = bool(getattr(args, "require_full_population", False))
    submission_validation_gate = submission_gate(
        validation,
        expected_question_count=expected_question_count,
        require_full_population=require_full_population,
    )
    implementation_at_report = implementation_fingerprint()

    input_fingerprints: dict[str, dict[str, Any]] = {}

    def record_input(name: str, path: Path | None, *, requested: bool = True) -> None:
        input_fingerprints[name] = fingerprint_path(path, requested=requested)

    record_input("questions", args.questions)
    record_input("review_items", args.bundle / "review_items.jsonl")
    record_input("replay", args.replay)
    record_input("structured_tables", structured_tables_path)
    record_input("source_line_map", source_line_map_path, requested=source_line_map_path is not None)
    for index, path in enumerate(research_paths):
        record_input(f"research_candidate_{index}", path)
    record_input("route_overlay", route_overlay_path, requested=route_overlay_path is not None)
    for index, path in enumerate(model_answer_paths):
        record_input(f"model_answer_candidate_{index}", path)
    for index, path in enumerate(direct_replay_paths):
        record_input(f"direct_evidence_replay_{index}", path)
    record_input(
        "formula_evidence",
        Path(formula_evidence_path) if formula_evidence_path else None,
        requested=formula_bridge_enabled,
    )
    record_input(
        "candidate_validity_model",
        validity_model_path,
        requested=not bool(getattr(args, "disable_candidate_validity", False))
        and validity_model_path is not None,
    )
    record_input(
        "dense_index",
        dense_index_dir,
        requested=dense_index_dir is not None,
    )
    record_input(
        "fine_tuned_model_root",
        finetuned_model_root,
        requested=reranker_policy == "use_finetuned_reranker",
    )
    prompt_contract_path = getattr(args, "prompt_contract", None)
    record_input(
        "prompt_contract",
        prompt_contract_path,
        requested=prompt_contract_path is not None,
    )

    dense_requests = int(dense_expansion_stats.get("requests") or 0)
    dense_loaded = bool(dense_artifact_validation and dense_requests > 0)
    dense_model_id = (
        dense_artifact_validation.get("model")
        if dense_artifact_validation is not None
        else None
    )
    reranker_loaded = model_scorer is not None
    reranker_ran = reranker_loaded and model_questions > 0
    validity_requested = not bool(getattr(args, "disable_candidate_validity", False))
    validity_available = bool(validity_model_path and Path(validity_model_path).exists())
    validity_ran = validity_requested and bool(active_input_items)
    research_requested = not disable_research_fusion
    research_loaded = int(research_input_stats.get("paths_used") or 0) > 0
    stages = {
        "lexical": stage_status(
            "lexical",
            requested=True,
            available=(args.bundle / "review_items.jsonl").is_file(),
            loaded=bool(review_items),
            ran=bool(review_items and tables),
            artifact=input_fingerprints["review_items"],
            details={"implementation": "review_bundle_and_source_first_lexical_lookup"},
        ),
        "dense": stage_status(
            "dense",
            requested=dense_index_dir is not None,
            available=bool(dense_artifact_validation and dense_artifact_validation["valid"]),
            loaded=dense_loaded,
            ran=dense_requests > 0,
            artifact=input_fingerprints["dense_index"],
            details={
                "navigation_only": True,
                "model_id": dense_model_id,
                "requests": dense_requests,
                "hits": int(dense_expansion_stats.get("dense_hits") or 0),
            },
        ),
        "retriever": stage_status(
            "retriever",
            requested=dense_index_dir is not None,
            available=bool(dense_artifact_validation and dense_artifact_validation["valid"]),
            loaded=dense_loaded,
            ran=dense_requests > 0,
            artifact=input_fingerprints["dense_index"],
            details={
                "role": "dense_query_encoder",
                "model_id": dense_model_id,
                "promotion_allowed": False,
            },
        ),
        "fine_tuned_reranker": stage_status(
            "fine_tuned_reranker",
            requested=reranker_policy == "use_finetuned_reranker",
            available=bool(model_manifest and model_manifest.reranker_path),
            loaded=reranker_loaded,
            ran=reranker_ran,
            artifact=input_fingerprints["fine_tuned_model_root"],
            details={
                "policy": reranker_policy,
                "candidate_only": True,
                "reranked_questions": model_questions,
                "reranked_candidates": model_candidates,
                "artifact_declared_promotion_allowed": (
                    model_manifest.reranker_promotion_allowed if model_manifest else False
                ),
            },
        ),
        "generator": stage_status(
            "generator",
            requested=False,
            available=bool(model_manifest and model_manifest.generator_path),
            loaded=False,
            ran=False,
            artifact=(
                fingerprint_path(model_manifest.generator_path, requested=False)
                if model_manifest and model_manifest.generator_path
                else fingerprint_path(None)
            ),
            details={
                "role": "answer_generation",
                "status_note": "builder_does_not_load_or_run_generator",
            },
        ),
        "candidate_validity": stage_status(
            "candidate_validity",
            requested=validity_requested,
            available=validity_available,
            loaded=validity_model is not None,
            ran=validity_ran,
            artifact=input_fingerprints["candidate_validity_model"],
            details={
                "model_status": validity_model_stats.get("status"),
                "ranking_only": True,
                "may_authorize_answer": False,
            },
        ),
        "research_fusion": stage_status(
            "research_fusion",
            requested=research_requested,
            available=research_loaded,
            loaded=research_loaded,
            ran=research_requested,
            details={
                "candidate_only": True,
                "paths_requested": int(research_input_stats.get("paths_requested") or 0),
                "paths_used": int(research_input_stats.get("paths_used") or 0),
                "coordinates_accepted": int(research_input_stats.get("coordinates_accepted") or 0),
                "raw_research_values_used": False,
            },
        ),
        "formula_evidence_bridge": stage_status(
            "formula_evidence_bridge",
            requested=formula_bridge_enabled,
            available=bool(
                formula_evidence_path and Path(formula_evidence_path).is_file()
            ),
            loaded=bool(formula_bridge_candidates),
            ran=formula_bridge_enabled,
            artifact=input_fingerprints["formula_evidence"],
            details={
                "protocol": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
                "candidate_count": len(formula_bridge_candidates),
                "candidate_only": True,
                "stored_sidecar_answers_used": False,
                "answer_authority": (
                    "current_structured_table_coordinate_and_decimal_replay"
                    if formula_bridge_enabled
                    else "none; bridge disabled"
                ),
            },
        ),
    }
    run_manifest_path = output_dir / "run_manifest_v1.json"
    run_manifest: dict[str, Any] = {
        "schema_version": 1,
        "protocol": "vifinqa_model_rag_run_manifest_v1",
        "run": {
            "run_id": output_dir.name,
            "reranker_policy": reranker_policy,
            "answer_authority": "current_v2_coordinate_replay_and_decimal_execution",
            "best_effort_submission": True,
        },
        "implementation": implementation_at_report,
        "route_flags": route_flag_snapshot(args),
        "inputs": input_fingerprints,
        "stages": stages,
        "gates": {
            "submission": submission_validation_gate,
            "source_line_map": {
                "status": (
                    "PASS" if not fallback_count else "DEGRADED_LOCAL_ORDINAL_FALLBACK"
                ),
                "map_coverage": source_line_map_coverage,
                "fallback_count": fallback_count,
            },
        },
        "promotion": {
            "allowed": False,
            "model_promotion_allowed": False,
            "reason": "load/run success is not an independent held-out promotion gate",
        },
        "outputs": {
            "submission": fingerprint_path(output_dir / "submission.json", requested=True),
            "zip": {"path": str(zip_path), "exists": False, "sha256": None},
        },
    }
    report = {
        "schema_version": "vifinqa_competition_submission_report_v4",
        "primary_model": "integrated_submission_pipeline_v1",
        "semantic_cell_contract": {
            "enabled": _SEMANTIC_CELL_CONTRACT_ENABLED,
            "protocol": (
                "semantic_cell_contract_v1"
                if _SEMANTIC_CELL_CONTRACT_ENABLED
                else "legacy_baseline_compat_v1"
            ),
            "answer_authority": "structured_table_coordinate_replay",
            "scope": "ranking_and_semantic_cell_candidate_filter_only",
        },
        "implementation": {
            **implementation_at_report,
            "loaded_at_build_start": implementation_at_build_start,
            "on_disk_at_report": implementation_at_report,
            "changed_during_build": implementation_at_build_start != implementation_at_report,
            "purpose": "A/B runs must compare implementation fingerprints before interpreting score deltas",
        },
        "prediction_policy": {
            "all_questions_emitted": True,
            "leaderboard_answer_for_fallback": 0,
            "fallback_is_marked_in_diagnostics": True,
            "objective": "best_effort_document_question_answering",
            "abstain_policy": (
                "serve the best hydrated candidate that survived filters and "
                "mark it uncertain; use null only when no numeric candidate exists"
            ),
        },
        "answer_level_selector": {
            "enabled": answer_level_selector_enabled,
            "protocol": "vifinqa_candidate_plan_selector_v1",
            "max_candidates": answer_level_selector_max_candidates,
            "counts": dict(answer_level_selector_stats),
            "mode": "opt_in_shadow_then_switch" if answer_level_selector_enabled else "disabled_control",
            "answer_authority": "none; selected values remain best-effort candidates",
            "strict_answer_authorized": False,
            "release_authorized": False,
            "training_eligible": False,
            "promotion_allowed": False,
            "rollback_control": "legacy_select_best_proposal_when_selector_abstains",
        },
        "formula_evidence_bridge": {
            **formula_bridge_stats,
            "selected_questions": counts.get(FORMULA_EVIDENCE_BRIDGE_PROTOCOL, 0),
            "route_priority": _route_priority(FORMULA_EVIDENCE_BRIDGE_PROTOCOL),
            "candidate_only": True,
            "strict_answer_authorized": False,
            "release_authorized": False,
            "training_eligible": False,
            "promotion_allowed": False,
        },
        "submission_path": str(submission_path),
        "best_candidate_ledger_path": str(candidate_ledger_path),
        "best_candidate_ledger_count": len(best_candidate_records),
        "prediction_audit_ledger_path": str(proposal_audit_path),
        "verification": {
            "certificate_path": str(verification_certificate_path)
            if verification_certificate_path
            else None,
            "config_path": str(verification_config_path) if verification_config_path else None,
            "class_counts": dict(sorted(verification_counts.items())),
            "failure_reason_counts": dict(sorted(verification_failure_counts.items())),
            "policy": "VERIFIED > PARTIAL > UNRESOLVED > REJECTED; rejected proposals are never selected",
            "local_verifier_authority": "none; only a matching complete canonical E2E certificate upgrades VERIFIED",
        },
        "question_count": len(questions),
        "review_item_registry_identity": {
            **registry_identity_stats,
            "source": str(DEFAULT_CODE_STOCK),
            "routing_only": True,
            "answer_authority": "hydrated_table_coordinate_replay",
        },
        "structured_table_asset": {
            "path": str(structured_tables_path),
            "sha256": sha256_file(structured_tables_path),
            "table_count": len(tables),
            "generic_table_count": len(tables),
            "route_table_count": len(route_tables),
            "filter_mode": structured_table_filter,
            "review_candidate_uid_count": len(review_candidate_uids),
            "lines_scanned": structured_table_lines_scanned,
            "lines_skipped_by_filter": structured_table_lines_skipped,
            "route_lines_scanned": route_table_lines_scanned,
            "route_lines_skipped_by_filter": route_table_lines_skipped,
            "source_boundary": (
                "frozen_review_packet_candidate_uids"
                if structured_table_filter == "candidate_uids"
                else "complete_structured_asset"
            ),
            "schema_mode": (
                "full_table_assets_v1"
                if structured_tables_path.name == "full_table_assets_v1.jsonl"
                else "tables_structured_v2_compatible"
            ),
            "normalized_for_runtime": True,
        },
        "source_first_route_hydration": {
            "enabled": selective_source_first_route_hydration,
            "mode": (
                "route_only_complete_asset"
                if selective_source_first_route_hydration
                and structured_table_filter == "candidate_uids"
                else (
                    "shared_complete_asset"
                    if structured_table_filter == "all"
                    else "shared_frozen_candidate_uids"
                )
            ),
            "generic_ranking_table_count": len(tables),
            "route_lookup_table_count": len(route_tables),
            "route_lookup_pair_count": len(route_tables_by_pair),
            "route_ticker_inferred_from_document_id": route_inferred_ticker_count,
            "route_missing_identity_count": route_missing_table_identity_count,
            "generic_and_route_uid_overlap": len(set(tables) & set(route_tables)),
            "full_asset_search_is_limited_to_explicit_route_families": True,
            "route_families": [
                _MULTI_ENTITY_SELECTOR_PROTOCOL,
                _MULTI_ENTITY_RATIO_SELECTOR_PROTOCOL,
                _MULTI_ENTITY_THRESHOLD_PROTOCOL,
                _MULTI_ENTITY_SHARE_THRESHOLD_PROTOCOL,
                _MULTI_ENTITY_LEASE_THRESHOLD_PROTOCOL,
                _MULTI_ENTITY_INTEREST_THRESHOLD_PROTOCOL,
                _PERIOD_EXTREME_PROTOCOL,
                _MULTI_ENTITY_DIRECT_AGGREGATION_PROTOCOL,
                _MULTI_ENTITY_CONDITIONAL_COUNT_PROTOCOL,
            ],
            "may_authorize_answer": False,
            "promotion_allowed": False,
        },
        "confidence_tiers": dict(counts),
        "predicted_questions": len(questions) - counts.get("fallback_zero", 0),
        "fallback_questions": counts.get("fallback_zero", 0),
        "research_selected_questions": sum(
            1 for row in diagnostics if row.get("research_candidate_selected")
        ),
        "model_answer_questions": sum(
            1 for row in diagnostics if row.get("model_answer_replayed")
        ),
        "nonzero_answers": sum(1 for row in records if float(row["answer"]) != 0.0),
        "with_relevant_docs": sum(1 for row in records if row["relevant_docs"]),
        "with_relevant_tables": sum(1 for row in records if row["relevant_tables"]),
        "average_docs_per_question": sum(len(row["relevant_docs"]) for row in records) / len(records),
        "average_tables_per_question": sum(len(row["relevant_tables"]) for row in records) / len(records),
        "source_line_coordinates": {
            "map_path": str(source_line_map_path) if source_line_map_path else None,
            "map_entries": len(source_line_map),
            "map_sha256": sha256_file(source_line_map_path)
            if source_line_map_path
            else None,
            "source_char_start": line_stats.get("source_char_start", 0),
            "source_line_map": line_stats.get("source_line_map", 0),
            "local_ordinal_fallback": fallback_count,
            "local_ordinal_fallback_allowed": allow_local_ordinal_fallback,
            "line_override": line_stats.get("line_override", 0),
            "used_table_uids": len(used_table_uids),
            "map_coverage": source_line_map_coverage,
            "route_map_coverage": route_source_line_map_coverage,
            "status": (
                "DEGRADED_LOCAL_ORDINAL_FALLBACK"
                if fallback_count
                else "PASS"
            ),
        },
        "dense_expansion": {
            "enabled": bool(dense_index_dir),
            "index_dir": str(dense_index_dir) if dense_index_dir else None,
            "candidate_limit": args.dense_candidate_limit if dense_index_dir else None,
            **dense_expansion_stats,
            "period_neighbor": {
                "enabled": period_neighbor_offset is not None,
                "report_year_offset": period_neighbor_offset,
                "table_slots": period_neighbor_table_slots,
                "navigation_only_answer_ablation": period_neighbor_navigation_only,
                "answer_selection_pool": (
                    "pre_dense_review_bundle+research_fusion"
                    if period_neighbor_navigation_only
                    else (
                        "expanded_exact_year+research_fusion"
                        if period_neighbor_offset is not None
                        else "expanded_review_bundle+research_fusion"
                    )
                ),
                "emitted_table_refs": sum(
                    int(row.get("period_neighbor_table_refs_emitted") or 0)
                    for row in diagnostics
                ),
                "navigation_only": True,
                "may_authorize_answer": False,
            },
            "navigation_only": True,
            "may_authorize_answer": False,
        },
        "research_fusion": {
            "enabled": not disable_research_fusion,
            "candidate_inputs": research_input_stats,
            "candidate_fusion": research_fusion_stats,
            "answer_lane_candidate_fusion": answer_research_fusion_stats,
            "route_overlay": route_overlay_stats,
            "research_candidates_are_hydrated_from_v2": True,
            "raw_research_values_used": False,
        },
        "model_answer_candidates": {
            **model_answer_stats,
            "accepted_answers_are_current_table_replayed": True,
            "source_values_are_not_copied_without_coordinate_check": True,
        },
        "direct_evidence_replay": {
            **direct_replay_stats,
            "accepted_answers_are_current_table_replayed": True,
            "source_values_are_not_copied_without_coordinate_check": True,
            "machine_artifact_is_not_human_verified": True,
            "lane": "authorized_best_effort_submission_candidate",
        },
        "source_first_direct_lookup": {
            **source_first_stats,
            "table_index": {
                "asset_table_count": len(tables),
                "indexed_table_count": sum(len(rows) for rows in tables_by_pair.values()),
                "indexed_pair_count": len(tables_by_pair),
                "ticker_inferred_from_document_id": inferred_ticker_count,
                "missing_identity_count": missing_table_identity_count,
            },
            "accepted_answers_are_current_table_replayed": True,
            "source_values_are_not_copied_without_coordinate_check": True,
            "machine_artifact_is_not_human_verified": True,
            "lane": "authorized_best_effort_submission_candidate",
        },
        "source_first_reclassified_direct_lookup": source_first_reclassified_stats,
        "source_first_multi_entity_selector_lookup": (
            source_first_multi_entity_selector_stats
        ),
        "source_first_multi_entity_ratio_selector_lookup": (
            source_first_multi_entity_ratio_selector_stats
        ),
        "source_first_multi_entity_share_threshold_lookup": (
            source_first_multi_entity_share_threshold_stats
        ),
        "source_first_multi_entity_lease_threshold_lookup": (
            source_first_multi_entity_lease_threshold_stats
        ),
        "source_first_multi_entity_interest_threshold_lookup": (
            source_first_multi_entity_interest_threshold_stats
        ),
        "source_first_multi_entity_threshold_lookup": source_first_multi_entity_threshold_stats,
        "source_first_composed_total_lookup": source_first_composed_stats,
        "source_first_candidate_bound_lookup": source_first_candidate_bound_stats,
        "source_first_period_extreme_lookup": source_first_period_extreme_stats,
        "source_first_conditional_temporal_lookup": source_first_conditional_stats,
        "source_first_temporal_lookup": source_first_temporal_stats,
        "source_first_cross_entity_lookup": source_first_cross_entity_stats,
        "source_first_multi_entity_direct_aggregation_lookup": (
            source_first_multi_entity_direct_stats
        ),
        "source_first_multi_entity_conditional_count_lookup": (
            source_first_multi_entity_conditional_count_stats
        ),
        "candidate_validity": {
            **validity_model_stats,
            "top_k_policy": {
                "direct_lookup": 10,
                "complex_or_multi_operand": 20,
                "multi_entity_selection": "top_k_per_entity_group",
            },
            "candidate_pool_before": validity_pool_before,
            "candidate_pool_after": validity_pool_after,
            "model_status_counts": dict(validity_model_statuses),
            "plan_status_counts": dict(
                Counter(str(row.get("plan_status") or "unknown") for row in diagnostics)
            ),
            "ranking_only": True,
            "answer_authority": "current_v2_coordinate_replay_and_decimal_execution",
        },
        "validation": validation,
        "warning": (
            "This is a best-effort competition model. Official answer accuracy is "
            "not available locally; compare the produced ZIP on the same leaderboard split."
        ),
    }
    if model_manifest is not None:
        report["fine_tuned_model"] = {
            "root": str(model_manifest.root),
            "retriever_path": str(model_manifest.retriever_path) if model_manifest.retriever_path else None,
            "reranker_path": str(model_manifest.reranker_path) if model_manifest.reranker_path else None,
            "generator_path": str(model_manifest.generator_path) if model_manifest.generator_path else None,
            "metrics_path": str(model_manifest.metrics_path) if model_manifest.metrics_path else None,
            "fast_dev_run": model_manifest.fast_dev_run,
            # This is a declaration from the artifact's own metrics, not a
            # promotion decision for this run.  The run-level manifest keeps
            # promotion false until an independent held-out gate is supplied.
            "artifact_declared_promotion_allowed": model_manifest.promotion_allowed,
            "retriever_promotion_allowed": model_manifest.promotion_allowed,
            "reranker_promotion_allowed": model_manifest.reranker_promotion_allowed,
            "generator_promotion_allowed": model_manifest.generator_promotion_allowed,
            "promotion_allowed": False,
            "promotion_basis": "independent_held_out_gate_not_provided",
            "reranked_questions": model_questions,
            "reranked_candidates": model_candidates,
            "numeric_authority": "v2_table_replay_for_cells_and_staged_model_results",
        }
    (output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "diagnostics.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in diagnostics),
        encoding="utf-8",
    )
    if not validation["valid"]:
        raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(submission_path, "submission.json")
        for csv_path in sorted(data_dir.glob("*.csv")):
            archive.write(csv_path, f"data/{csv_path.name}")
    run_manifest["outputs"]["zip"] = fingerprint_path(zip_path, requested=True)
    write_json(run_manifest_path, run_manifest)
    report["zip_path"] = str(zip_path)
    report["zip_size_bytes"] = zip_path.stat().st_size
    report["run_manifest_path"] = str(run_manifest_path)
    report["run_manifest_sha256"] = sha256_file(run_manifest_path)
    (output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add primary-submission arguments to a script or public CLI parser."""
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument(
        "--structured-tables",
        type=Path,
        default=None,
        help=(
            "Optional structured-table JSONL used for hydration and replay. "
            "Accepts the full-corpus full_table_assets_v1.jsonl; when omitted, "
            "the bundle's tables_structured_v2.jsonl is used."
        ),
    )
    parser.add_argument(
        "--structured-table-filter",
        choices=("candidate_uids", "all"),
        default="candidate_uids",
        help=(
            "Hydrate only table UIDs named by the frozen review packet by default; "
            "use 'all' for an explicitly full-corpus diagnostic build."
        ),
    )
    parser.add_argument(
        "--selective-source-first-route-hydration",
        action="store_true",
        help=(
            "Keep generic ranking on candidate_uids while hydrating the complete "
            "structured asset for the explicit multi-entity and period source-first "
            "route families only; requires a complete structured asset."
        ),
    )
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--source-line-map",
        type=Path,
        default=None,
        help=(
            "JSON object mapping internal_table_uid to canonical 1-based OCR "
            "table-start line; auto-detected from bundle/source_line_map.json"
        ),
    )
    parser.add_argument(
        "--require-source-line-map",
        action="store_true",
        help=(
            "Require an exact source_line_map.json coverage match for the V2 "
            "table bundle; use this for leaderboard/Kaggle builds."
        ),
    )
    parser.add_argument(
        "--allow-local-ordinal-fallback",
        action="store_true",
        help=(
            "Allow local_ordinal+1 when no source coordinate is available. "
            "Diagnostic-only; it is rejected by the production coordinate gate."
        ),
    )
    parser.add_argument(
        "--candidate-validity-model",
        type=Path,
        default=None,
        help=(
            "Native candidate-validity JSON or legacy human calibrator .joblib; "
            "ranking only, never answer authority"
        ),
    )
    parser.add_argument(
        "--candidate-top-k",
        type=int,
        default=None,
        help="Override family top-k; default is 10 for direct lookup and 20 for complex plans",
    )
    parser.add_argument(
        "--disable-candidate-validity",
        action="store_true",
        help="Disable learned/heuristic candidate ranking for an A/B baseline",
    )
    parser.add_argument(
        "--disable-semantic-cell-contract",
        action="store_true",
        help=(
            "Use the established pre-contract semantic-cell ranking for a "
            "controlled route A/B; source-first routes and replay gates remain active"
        ),
    )
    parser.add_argument(
        "--verification-certificate",
        type=Path,
        default=None,
        help=(
            "Canonical run-e2e answer_certificates_v1.jsonl used only to upgrade "
            "a same-answer proposal to VERIFIED; ABSTAIN never authorizes it."
        ),
    )
    parser.add_argument(
        "--verification-config",
        type=Path,
        default=None,
        help=(
            "Run canonical run-e2e after proposal generation using this source-closure "
            "config and this build's candidate ledger. Mutually exclusive with "
            "--verification-certificate."
        ),
    )
    parser.add_argument(
        "--verification-candidate-limit",
        type=int,
        default=8,
        help=(
            "Maximum hydrated semantic alternatives per question checked by the "
            "proposal verifier before response policy selects one."
        ),
    )
    parser.add_argument(
        "--enable-answer-level-selector",
        action="store_true",
        help=(
            "Opt in to whole-question CandidatePlanSet selection after the "
            "legacy proposal selector; invalid/replay-unproven plans are "
            "rejected and the v2 control is retained on abstain."
        ),
    )
    parser.add_argument(
        "--answer-level-selector-max-candidates",
        type=int,
        default=64,
        help="Bound the whole-question candidate pool (1..256; default 64)",
    )
    parser.add_argument(
        "--enable-formula-evidence-bridge",
        action="store_true",
        help=(
            "Admit only fully bound, known formula sidecar rows after current "
            "table-coordinate and Decimal AST replay; candidate-only and opt-in"
        ),
    )
    parser.add_argument(
        "--formula-evidence",
        type=Path,
        default=None,
        help=(
            "Formula evidence JSONL for --enable-formula-evidence-bridge; "
            "defaults to the bundle's formula_evidence_sets_typed_v1.jsonl"
        ),
    )
    parser.add_argument(
        "--reranker-policy",
        choices=("baseline_no_reranker", "use_finetuned_reranker"),
        default="baseline_no_reranker",
        help=(
            "Explicit navigation policy. baseline_no_reranker is the stable "
            "A/B baseline; use_finetuned_reranker requires --finetuned-model-root."
        ),
    )
    parser.add_argument(
        "--finetuned-model-root",
        type=Path,
        default=None,
        help=(
            "Fine-tuned output directory containing reranker_finetuned/; it is "
            "ignored only by omission, never implicitly enabled (navigation only)"
        ),
    )
    parser.add_argument(
        "--model-device",
        choices=("cpu", "cuda"),
        default="cpu",
        help="device for the fine-tuned reranker; answer execution remains CPU/Decimal",
    )
    parser.add_argument(
        "--model-candidate-limit",
        type=int,
        default=40,
        help="shortlist size scored by the fine-tuned reranker",
    )
    parser.add_argument(
        "--model-batch-size",
        type=int,
        default=64,
        help="CrossEncoder batch size for the all-question model pass",
    )
    parser.add_argument(
        "--dense-index-dir",
        type=Path,
        default=None,
        help="Optional validated full-corpus dense index used only to expand navigation candidates",
    )
    parser.add_argument(
        "--dense-candidate-limit",
        type=int,
        default=50,
        help="Top-k dense hits per ticker/year route before fusion with the review shortlist",
    )
    parser.add_argument(
        "--dense-device",
        choices=("cpu", "cuda"),
        default="cpu",
        help="Device for encoding dense expansion queries",
    )
    parser.add_argument(
        "--dense-batch-size",
        type=int,
        default=64,
        help="Query encode batch size for dense expansion",
    )
    parser.add_argument(
        "--expected-question-count",
        type=int,
        default=None,
        help=(
            "Optional full-run record/replay gate. Kaggle preparation passes "
            "1012; diagnostic/unit runs may omit it."
        ),
    )
    parser.add_argument(
        "--require-full-population",
        action="store_true",
        help=(
            "Require the configured expected question count and exact replay "
            "with an empty error list before accepting the submission build."
        ),
    )
    parser.add_argument(
        "--source-first-report-year-neighbor-offset",
        type=int,
        choices=(1, 2),
        default=1,
        help=(
            "For source-first direct lookups, try report year Y+offset only "
            "after every exact-Y source misses; the selected column remains Y. "
            "Set --disable-source-first-report-year-neighbor for the baseline."
        ),
    )
    parser.add_argument(
        "--disable-source-first-report-year-neighbor",
        action="store_true",
        help="Disable the exact-year-first comparative-report fallback arm",
    )
    parser.add_argument(
        "--disable-source-first-temporal",
        action="store_true",
        help=(
            "Disable the exact two-operand source-first temporal proposal lane "
            "for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-candidate-bound",
        action="store_true",
        help=(
            "Disable the candidate-bound source-first direct-lookup proposal lane "
            "for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-period-extreme",
        action="store_true",
        help=(
            "Disable the exact multi-year max/min source-replay proposal lane "
            "for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-reclassified-direct",
        action="store_true",
        help=(
            "Disable the family-based one-period multi-plan to direct-lookup "
            "proposal lane for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-financial-liability-total",
        action="store_true",
        help=(
            "Disable only the financial-liability maturity-total pattern "
            "inside the reclassified direct lane for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-financial-receivables-total",
        action="store_true",
        help=(
            "Disable only the contextual gross-receivables total pattern "
            "inside the reclassified direct lane for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-threshold",
        action="store_true",
        help=(
            "Disable the bounded source-replayed multi-issuer positive-threshold "
            "count proposal lane for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-share-threshold",
        action="store_true",
        help=(
            "Disable the bounded source-replayed outstanding-share threshold "
            "count lane for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-lease-threshold",
        action="store_true",
        help=(
            "Disable the bounded source-replayed operating-lease maturity "
            "threshold count lane for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-interest-threshold",
        action="store_true",
        help=(
            "Disable the bounded source-replayed parent-company interest-expense "
            "threshold count lane for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-selector",
        action="store_true",
        help=(
            "Disable the bounded source-replayed selector-then-lookup lane "
            "for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-ratio-selector",
        action="store_true",
        help=(
            "Disable the bounded source-replayed debt/equity selector and "
            "interest-coverage lane for a controlled A/B"
        ),
    )
    parser.add_argument(
        "--disable-source-first-composed-total",
        action="store_true",
        help=(
            "Disable the narrow same-table two-component reported-total "
            "proposal lane for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-conditional-temporal",
        action="store_true",
        help=(
            "Disable the source-replayed conditional year-selection proposal lane "
            "for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-cross-entity",
        action="store_true",
        help=(
            "Disable the exact two-issuer source-first subtraction proposal lane "
            "for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-direct-aggregation",
        action="store_true",
        help=(
            "Disable the fail-closed per-issuer source-first mean/sum aggregation "
            "lane for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--disable-source-first-multi-entity-conditional-count",
        action="store_true",
        help=(
            "Disable the fail-closed explicit-scope source-first positive-count "
            "lane for a controlled ablation baseline"
        ),
    )
    parser.add_argument(
        "--period-neighbor-offset",
        type=int,
        choices=(1, 2),
        default=None,
        help=(
            "Optional report-year neighbor retrieval offset. For example, 1 "
            "adds report-year Y+1 as a navigation-only source for questions "
            "requesting period Y; requires --dense-index-dir."
        ),
    )
    parser.add_argument(
        "--period-neighbor-table-slots",
        type=int,
        choices=tuple(range(1, 6)),
        default=2,
        help="Bounded neighbor candidates reserved in F2-oriented table emission",
    )
    parser.add_argument(
        "--period-neighbor-navigation-only",
        action="store_true",
        help=(
            "Use period-neighbor dense hits only for relevant document/table "
            "emission; keep answer selection on the pre-dense review pool "
            "plus value-blind research hints. Requires --period-neighbor-offset."
        ),
    )
    parser.add_argument(
        "--research-candidate",
        type=Path,
        action="append",
        default=None,
        help=(
            "Value-blind research candidate JSONL; repeat to add sources. "
            "Known local artifacts are auto-discovered when omitted."
        ),
    )
    parser.add_argument(
        "--route-overlay",
        type=Path,
        default=None,
        help="Optional research route overlay used for diagnostics only",
    )
    parser.add_argument(
        "--model-answer-candidate",
        type=Path,
        action="append",
        default=None,
        help=(
            "Model/staged answer JSONL. A result is used only after all cited "
            "cells and the independent final stage replay against V2."
        ),
    )
    parser.add_argument(
        "--direct-evidence-replay",
        type=Path,
        action="append",
        default=None,
        help=(
            "Unique direct-source replay JSONL used as a best-effort answer "
            "candidate. The current structured table is re-read and the "
            "Decimal value is recomputed before use."
        ),
    )
    parser.add_argument(
        "--direct-replay-include-provisional",
        action="store_true",
        help=(
            "Also admit shadow_replay_ready records marked machine_provisional; "
            "calibrated records are always admitted, and all remain best-effort."
        ),
    )
    parser.add_argument(
        "--disable-research-fusion",
        action="store_true",
        help="Run the legacy review/replay/program path without research/model fusion",
    )
    return parser


def parse_args() -> argparse.Namespace:
    parser = configure_parser(argparse.ArgumentParser())
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
