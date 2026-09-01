"""Candidate-only retrieval and value-blind row review for ViFinQA.

This module deliberately stops before exact-cell binding.  Table and row
candidates are navigation aids; they cannot authorize evidence or answers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.table_retrieval import (
    STOPWORDS,
    WORD_RE,
    load_jsonl,
    search_lexical,
    sha256_file,
)


PROTOCOL = "vifinqa_full_corpus_candidate_retrieval_v1"
NUMERIC_CELL_RE = re.compile(r"\d")
TEXT_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
FORBIDDEN_REVIEW_KEYS = frozenset(
    {"answer", "raw_value", "raw_values", "cell_value", "pandas_query"}
)


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in WORD_RE.findall(value.casefold())
        if len(token) >= 2 and token not in STOPWORDS
    }


def _metric_query(plan: Mapping[str, Any], operand: Mapping[str, Any]) -> str:
    """Return one row-label-sized query for a typed operand.

    A generated operand label may lead with ``TICKER —`` and end with a year,
    while its second hint is the canonical report-row wording.  Concatenating
    all hints made the Jaccard denominator grow and could make an exact row
    miss the strict 0.90 gate.  Keep the first non-empty *cleaned* hint rather
    than treating aliases as words that must co-occur in one source row.
    """
    ticker = str(operand.get("ticker") or operand.get("entity") or "").strip()
    for raw_hint in operand.get("metric_hints") or []:
        hint = str(raw_hint).strip()
        if not hint:
            continue
        if ticker:
            hint = re.sub(
                rf"^{re.escape(ticker)}\s*[—–-]\s*",
                "",
                hint,
                flags=re.IGNORECASE,
            ).strip()
        hint = re.sub(r"\s+\b(?:19|20)\d{2}\b\s*$", "", hint).strip()
        if hint:
            return hint
    return str(plan.get("question") or "")


ENTITY_MARKER_RE = re.compile(
    r"\b(?:công\s+ty\s+mẹ|công\s+ty\s+cổ\s+phần|ctcp|"
    r"ngân\s+hàng\s+tmcp|ngân\s+hàng\s+thương\s+mại\s+cổ\s+phần|"
    r"tập\s+đoàn|tổng\s+công\s+ty)\b",
    re.IGNORECASE,
)


def metric_core_query(value: str, *, ticker: str) -> str:
    """Keep the metric prefix and remove a trailing issuer/title phrase."""
    boundaries = [match.start() for match in ENTITY_MARKER_RE.finditer(value)]
    ticker_match = re.search(rf"\b{re.escape(ticker)}\b", value, flags=re.IGNORECASE)
    if ticker_match is not None:
        boundaries.append(ticker_match.start())
    core = value[: min(boundaries)].strip() if boundaries else value.strip()
    core = re.sub(
        r"\b(?:cuối|đầu|trong)\s+năm\b.*$", "", core, flags=re.IGNORECASE
    ).strip()
    return core or value.strip()


def _search_operand_year(
    *,
    index_path: Path,
    precision_query: str,
    recall_query: str,
    ticker: str,
    year: int,
    scope: str | None,
    top_k: int,
    match_mode: str,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Search exact scope first, then a visibly tagged unscoped supplement."""
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append(
        rows: Iterable[Mapping[str, Any]], lane: str, query_lane: str, query: str
    ) -> None:
        for row in rows:
            uid = str(row["internal_table_uid"])
            if uid in seen or len(ranked) >= top_k:
                continue
            observed_scope = str(row.get("scope") or "unknown")
            ranked.append(
                {
                    **dict(row),
                    "candidate_rank": len(ranked) + 1,
                    "retrieval_lane": lane,
                    "query_lane": query_lane,
                    "retrieval_query": query,
                    "requested_scope": scope,
                    "scope_match": None if scope is None else observed_scope == scope,
                }
            )
            seen.add(uid)

    query_lanes = [("metric_core_all", precision_query, "all")]
    if recall_query != precision_query or match_mode != "all":
        query_lanes.append(("operand_any_recall", recall_query, match_mode))
    for query_lane, query, lane_match_mode in query_lanes:
        if scope is not None and len(ranked) < top_k:
            append(
                search_lexical(
                    index_path=index_path,
                    query=query,
                    ticker=ticker,
                    report_year=year,
                    scope=scope,
                    limit=top_k,
                    match_mode=lane_match_mode,
                    connection=connection,
                ),
                "explicit_scope",
                query_lane,
                query,
            )
        if len(ranked) < top_k:
            append(
                search_lexical(
                    index_path=index_path,
                    query=query,
                    ticker=ticker,
                    report_year=year,
                    limit=top_k,
                    match_mode=lane_match_mode,
                    connection=connection,
                ),
                "unscoped" if scope is None else "unscoped_supplement",
                query_lane,
                query,
            )
    return ranked


def _best_row_label_cell(row: list[str], query_tokens: set[str]) -> tuple[str, int, float]:
    """Score every textual cell so code-first statements retain their label.

    Financial statements often store ``Mã số`` in column zero and the metric
    wording in column one.  Treating the first cell as the label makes an
    otherwise exact route score zero.  This remains a row-navigation heuristic:
    it selects a label within the already selected row and never selects a
    numeric cell or authorizes a source binding.
    """
    choices: list[tuple[float, int, int, str]] = []
    for column_index, raw_cell in enumerate(row):
        label = raw_cell.strip()
        # Keep report labels such as ``Lợi nhuận trước thuế (50 = 30 + 40)``.
        # Only a cell with no letters at all is a code/numeric cell here.
        if not label or TEXT_LETTER_RE.search(label) is None:
            continue
        label_tokens = _tokens(label)
        score = len(query_tokens & label_tokens) / max(1, len(query_tokens | label_tokens))
        choices.append((score, len(label_tokens), column_index, label))
    if not choices:
        return (row[0].strip() if row else "", 0, 0.0)
    # Prefer the highest semantic overlap.  A longer text is a deterministic
    # tie-breaker before column order, avoiding a short structural cell such as
    # "Chỉ tiêu" when the actual metric wording is also present in the row.
    score, _token_count, column_index, label = max(
        choices,
        key=lambda value: (value[0], value[1], -value[2]),
    )
    return label, column_index, score


def _row_candidates(
    asset: Mapping[str, Any],
    query: str,
    *,
    maximum: int,
) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    scored: list[tuple[float, int, int, str, list[str], list[int]]] = []
    for row_index, raw_row in enumerate(asset.get("rows") or []):
        row = [str(cell) for cell in raw_row]
        if not row:
            continue
        numeric_indices = [
            index
            for index, cell in enumerate(row[1:], start=1)
            if NUMERIC_CELL_RE.search(cell)
        ]
        if not numeric_indices:
            continue
        label, label_column_index, score = _best_row_label_cell(row, query_tokens)
        scored.append((score, row_index, label_column_index, label, row, numeric_indices))
    scored.sort(key=lambda value: (-value[0], value[1], value[2]))
    selected = [value for value in scored if value[0] > 0][:maximum]
    if not selected:
        selected = scored[:maximum]
    return [
        {
            "row_rank": rank,
            "row_index": row_index,
            "row_label": label,
            "row_label_column_index": label_column_index,
            "row_token_jaccard": score,
            "numeric_cell_indices": numeric_indices,
            "numeric_cell_sha256": [
                hashlib.sha256(row[index].encode("utf-8")).hexdigest()
                for index in numeric_indices
            ],
            "row_sha256": _canonical_sha(row),
        }
        for rank, (score, row_index, label_column_index, label, row, numeric_indices) in enumerate(selected, start=1)
    ]


def build_candidate_artifact(
    *,
    config_path: Path,
    plans_path: Path,
    lexical_index_path: Path,
    assets_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build all-question routing status plus table and value-blind row candidates."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("protocol") != PROTOCOL:
        raise ValueError("unexpected candidate retrieval protocol")
    retrieval = config["retrieval"]
    top_k = int(retrieval["top_k_tables_per_operand_year"])
    maximum_rows = int(retrieval["max_row_candidates_per_table"])
    match_mode = str(retrieval["match_mode"])
    eligible_statuses = set(config["eligible_decomposition_statuses"])

    plans = list(load_jsonl(plans_path))
    if len(plans) != int(config["expected_question_count"]):
        raise ValueError(
            f"question count mismatch: expected {config['expected_question_count']}, "
            f"observed {len(plans)}"
        )

    statuses: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    requested_uids: set[str] = set()
    status_counts: Counter[str] = Counter()
    question_candidate_counts: defaultdict[int, int] = defaultdict(int)

    connection = sqlite3.connect(lexical_index_path)
    try:
        connection.execute("PRAGMA query_only=ON")
        for plan_number, plan in enumerate(plans, start=1):
            question_id = int(plan["question_id"])
            decomposition_status = str(plan.get("decomposition_status") or "")
            if decomposition_status not in eligible_statuses:
                status = {
                    "question_id": question_id,
                    "decomposition_status": decomposition_status,
                    "routing_status": "NOT_ROUTED",
                    "reason_code": "PLAN_NOT_COMPLETE",
                    "answer_eligible": False,
                    "submission_eligible": False,
                }
                statuses.append(status)
                status_counts[status["routing_status"]] += 1
                continue

            operand_routes = 0
            operand_blockers: list[str] = []
            for operand in plan.get("operands") or []:
                operand_id = str(operand.get("operand_id") or "")
                ticker = str(operand.get("ticker") or "").strip().upper()
                years = sorted({int(value) for value in operand.get("years") or []})
                scope_value = operand.get("scope")
                scope = str(scope_value) if scope_value is not None else None
                if not operand_id or not ticker or not years:
                    operand_blockers.append(f"{operand_id or 'unknown'}:MISSING_TICKER_OR_YEAR")
                    continue
                query = _metric_query(plan, operand)
                precision_query = metric_core_query(query, ticker=ticker)
                try:
                    for year in years:
                        rows = _search_operand_year(
                            index_path=lexical_index_path,
                            precision_query=precision_query,
                            recall_query=query,
                            ticker=ticker,
                            year=year,
                            scope=scope,
                            top_k=top_k,
                            match_mode=match_mode,
                            connection=connection,
                        )
                        if not rows:
                            operand_blockers.append(f"{operand_id}:{year}:NO_TABLE_CANDIDATE")
                        for row in rows:
                            uid = str(row["internal_table_uid"])
                            candidate = {
                                "protocol": PROTOCOL,
                                "question_id": question_id,
                                "operand_id": operand_id,
                                "ticker": ticker,
                                "report_year": year,
                                "metric_query": query,
                                "metric_core_query": precision_query,
                                **row,
                                "navigation_metadata_only": True,
                                "may_authorize_evidence": False,
                                "may_authorize_answer": False,
                                "training_eligible": False,
                                "submission_eligible": False,
                            }
                            candidates.append(candidate)
                            requested_uids.add(uid)
                            operand_routes += 1
                            question_candidate_counts[question_id] += 1
                except ValueError as exc:
                    operand_blockers.append(f"{operand_id}:QUERY_REJECTED:{exc}")

            routing_status = "ROUTED" if operand_routes and not operand_blockers else (
                "PARTIAL" if operand_routes else "NO_CANDIDATE"
            )
            status = {
                "question_id": question_id,
                "decomposition_status": decomposition_status,
                "routing_status": routing_status,
                "table_candidate_count": operand_routes,
                "blockers": operand_blockers,
                "answer_eligible": False,
                "submission_eligible": False,
            }
            statuses.append(status)
            status_counts[routing_status] += 1
            if plan_number % 100 == 0:
                print(
                    json.dumps(
                        {
                            "phase": "lexical_candidate_routing",
                            "questions_completed": plan_number,
                            "questions_total": len(plans),
                            "table_candidates": len(candidates),
                        }
                    ),
                    flush=True,
                )
    finally:
        connection.close()

    assets: dict[str, dict[str, Any]] = {}
    for asset in load_jsonl(assets_path):
        uid = str(asset["internal_table_uid"])
        if uid in requested_uids:
            assets[uid] = asset
    missing_assets = sorted(requested_uids - set(assets))
    if missing_assets:
        raise ValueError(f"candidate UIDs missing from assets: {missing_assets[:3]}")

    review_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        asset = assets[str(candidate["internal_table_uid"])]
        for row in _row_candidates(
            asset, candidate["retrieval_query"], maximum=maximum_rows
        ):
            packet_identity = {
                "question_id": candidate["question_id"],
                "operand_id": candidate["operand_id"],
                "report_year": candidate["report_year"],
                "internal_table_uid": candidate["internal_table_uid"],
                "row_index": row["row_index"],
            }
            review_rows.append(
                {
                    "protocol": PROTOCOL,
                    "review_packet_id": _canonical_sha(packet_identity),
                    **packet_identity,
                    "ticker": candidate["ticker"],
                    "requested_scope": candidate["requested_scope"],
                    "observed_scope": candidate["scope"],
                    "scope_match": candidate["scope_match"],
                    "retrieval_lane": candidate["retrieval_lane"],
                    "query_lane": candidate["query_lane"],
                    "candidate_rank": candidate["candidate_rank"],
                    "document_id": asset["document_id"],
                    "source_path": asset["source_path"],
                    "source_sha256": asset["source_sha256"],
                    "table_sha256": asset["table_sha256"],
                    "local_ordinal": asset["local_ordinal"],
                    "char_start": asset["char_start"],
                    "page_no": asset.get("page_no"),
                    "headers": asset.get("headers") or [],
                    **row,
                    "raw_numeric_values_included": False,
                    "exact_column_resolved": False,
                    "navigation_metadata_only": True,
                    "may_authorize_evidence": False,
                    "may_authorize_answer": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                }
            )

    eligible_questions = sum(
        str(plan.get("decomposition_status") or "") in eligible_statuses for plan in plans
    )
    coverage = {
        "protocol": PROTOCOL,
        "question_count": len(plans),
        "eligible_question_count": eligible_questions,
        "ineligible_question_count": len(plans) - eligible_questions,
        "routing_status_counts": dict(sorted(status_counts.items())),
        "questions_with_table_candidates": len(question_candidate_counts),
        "table_candidate_count": len(candidates),
        "unique_candidate_table_count": len(requested_uids),
        "row_review_packet_count": len(review_rows),
        "explicit_scope_candidate_count": sum(
            row["retrieval_lane"] == "explicit_scope" for row in candidates
        ),
        "unscoped_supplement_count": sum(
            row["retrieval_lane"] == "unscoped_supplement" for row in candidates
        ),
        "raw_numeric_values_included": False,
        "answer_eligible": False,
        "submission_eligible": False,
    }

    output_dir.mkdir(parents=True)
    output_values = {
        "question_routing_status_v1.jsonl": statuses,
        "table_candidates_v1.jsonl": candidates,
        "row_review_queue_v1.jsonl": review_rows,
    }
    for filename, values in output_values.items():
        (output_dir / filename).write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in values
            ),
            encoding="utf-8",
        )
    coverage_path = output_dir / "coverage_report_v1.json"
    coverage_path.write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    inputs = {
        "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
        "plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
        "lexical_index": {
            "path": str(lexical_index_path),
            "sha256": sha256_file(lexical_index_path),
        },
        "assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
    }
    outputs = {
        path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for path in [
            *(output_dir / filename for filename in output_values),
            coverage_path,
        ]
    }
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": inputs,
        "outputs": outputs,
        "authorization": config["authorization"],
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return coverage


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_REVIEW_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def validate_candidate_artifact(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest_path = artifact_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected candidate artifact protocol")
    for filename, contract in manifest["outputs"].items():
        path = artifact_dir / filename
        if sha256_file(path) != contract["sha256"]:
            raise ValueError(f"output hash mismatch: {filename}")

    statuses = list(load_jsonl(artifact_dir / "question_routing_status_v1.jsonl"))
    candidates = list(load_jsonl(artifact_dir / "table_candidates_v1.jsonl"))
    review_rows = list(load_jsonl(artifact_dir / "row_review_queue_v1.jsonl"))
    coverage = json.loads((artifact_dir / "coverage_report_v1.json").read_text(encoding="utf-8"))
    if len(statuses) != expected_question_count:
        raise ValueError("question routing status does not cover the expected population")
    if len({row["question_id"] for row in statuses}) != len(statuses):
        raise ValueError("duplicate question IDs in routing status")
    candidate_uids = {str(row["internal_table_uid"]) for row in candidates}
    packet_ids = [str(row["review_packet_id"]) for row in review_rows]
    if len(packet_ids) != len(set(packet_ids)):
        raise ValueError("duplicate row review packet IDs")
    for row in [*candidates, *review_rows]:
        # New artifacts must omit this retired field.  Earlier immutable
        # candidate-only artifacts recorded it as ``false``; accepting that
        # exact legacy value keeps their provenance replayable without ever
        # treating it as a review decision or emitting it again.
        if "human_verified" in row and row.get("human_verified") is not False:
            raise ValueError("candidate has an invalid legacy human verification field")
        if row.get("may_authorize_answer") is not False:
            raise ValueError("candidate incorrectly authorizes an answer")
        if row.get("submission_eligible") is not False:
            raise ValueError("candidate incorrectly marked submission eligible")
    for row in review_rows:
        if str(row["internal_table_uid"]) not in candidate_uids:
            raise ValueError("row review packet has no table candidate")
        if row.get("raw_numeric_values_included") is not False:
            raise ValueError("row review packet includes raw numeric values")
        if row.get("exact_column_resolved") is not False:
            raise ValueError("row review packet prematurely resolves an exact column")
        if _contains_forbidden_key(row):
            raise ValueError("row review packet contains a forbidden answer/value field")
    expected_counts = {
        "question_count": len(statuses),
        "table_candidate_count": len(candidates),
        "unique_candidate_table_count": len(candidate_uids),
        "row_review_packet_count": len(review_rows),
    }
    for key, value in expected_counts.items():
        if int(coverage[key]) != value:
            raise ValueError(f"coverage count mismatch for {key}")
    return {
        "status": "PASS",
        **expected_counts,
        "answer_eligible": False,
        "submission_eligible": False,
    }
