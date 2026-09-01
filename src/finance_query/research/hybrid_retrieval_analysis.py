"""Value-blind lexical/dense comparison for grounded ViFinQA routes.

The artifact produced here is research and navigation metadata only.  It
binds candidate UIDs back to immutable table-source hashes, but never exposes
numeric cells and never authorizes evidence, answers, training, or submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any, Mapping

from finance_query.e2e.core.dense_retrieval import search_dense_batch
from finance_query.e2e.core.table_retrieval import load_jsonl, sha256_file
from finance_query.research.full_corpus_candidate_retrieval import (
    _best_row_label_cell,
    _metric_query,
    _tokens,
    metric_core_query,
)


PROTOCOL = "vifinqa_hybrid_retrieval_analysis_v1"
FORBIDDEN_KEYS = frozenset(
    {"answer", "raw_value", "raw_values", "cell_value", "pandas_query", "rows"}
)


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _route_specs(
    plans: list[Mapping[str, Any]], eligible_statuses: set[str]
) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for plan in plans:
        if str(plan.get("decomposition_status") or "") not in eligible_statuses:
            continue
        question_id = int(plan["question_id"])
        for operand in plan.get("operands") or []:
            operand_id = str(operand.get("operand_id") or "")
            ticker = str(operand.get("ticker") or "").strip().upper()
            years = sorted({int(value) for value in operand.get("years") or []})
            if not operand_id or not ticker or not years:
                raise ValueError(f"eligible question {question_id} has an incomplete route")
            query = _metric_query(plan, operand)
            core_query = metric_core_query(query, ticker=ticker)
            scope_value = operand.get("scope")
            scope = str(scope_value) if scope_value is not None else None
            for year in years:
                identity = {
                    "question_id": question_id,
                    "operand_id": operand_id,
                    "report_year": year,
                }
                routes.append(
                    {
                        **identity,
                        "route_id": _canonical_sha(identity),
                        "ticker": ticker,
                        "requested_scope": scope,
                        "metric_query": query,
                        "metric_core_query": core_query,
                    }
                )
    return routes


def _merge_scoped_dense(
    *,
    scoped: list[Mapping[str, Any]],
    unscoped: list[Mapping[str, Any]],
    requested_scope: str | None,
    top_k: int,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append(rows: list[Mapping[str, Any]], lane: str) -> None:
        for row in rows:
            uid = str(row["internal_table_uid"])
            if uid in seen or len(ranked) >= top_k:
                continue
            observed_scope = str(row.get("scope") or "unknown")
            ranked.append(
                {
                    **dict(row),
                    "dense_rank": len(ranked) + 1,
                    "dense_lane": lane,
                    "requested_scope": requested_scope,
                    "scope_match": (
                        None if requested_scope is None else observed_scope == requested_scope
                    ),
                }
            )
            seen.add(uid)

    if requested_scope is not None:
        append(scoped, "explicit_scope")
        append(unscoped, "unscoped_supplement")
    else:
        append(unscoped, "unscoped")
    return ranked


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _max_row_label_jaccard(asset: Mapping[str, Any], query: str) -> float:
    query_tokens = _tokens(query)
    scores: list[float] = []
    for raw_row in asset.get("rows") or []:
        if not raw_row:
            continue
        _label, _column_index, score = _best_row_label_cell(
            [str(cell) for cell in raw_row], query_tokens
        )
        scores.append(score)
    return max(scores, default=0.0)


def _quality_summary(values: list[float]) -> dict[str, Any]:
    return {
        "mean": statistics.mean(values) if values else 0.0,
        "median": statistics.median(values) if values else 0.0,
        "zero_count": sum(value == 0 for value in values),
        "ge_0_5_count": sum(value >= 0.5 for value in values),
    }


def build_hybrid_retrieval_analysis(
    *,
    config_path: Path,
    plans_path: Path,
    lexical_candidates_path: Path,
    dense_index_dir: Path,
    assets_path: Path,
    output_dir: Path,
    requested_device: str = "cpu",
    encoder_factory: Any | None = None,
) -> dict[str, Any]:
    """Compare both retrieval lanes and emit source-bound hybrid candidates."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("protocol") != PROTOCOL:
        raise ValueError("unexpected hybrid retrieval protocol")
    top_k = int(config["retrieval"]["top_k_per_route"])
    rrf_constant = int(config["retrieval"]["rrf_constant"])
    eligible_statuses = set(config["eligible_decomposition_statuses"])

    plans = list(load_jsonl(plans_path))
    if len(plans) != int(config["expected_question_count"]):
        raise ValueError("question population does not match hybrid config")
    routes = _route_specs(plans, eligible_statuses)
    expected_route_count = int(config.get("expected_route_count") or len(routes))
    if len(routes) != expected_route_count:
        raise ValueError(
            f"route count mismatch: expected {expected_route_count}, observed {len(routes)}"
        )

    route_by_key = {
        (route["question_id"], route["operand_id"], route["report_year"]): route
        for route in routes
    }
    if len(route_by_key) != len(routes):
        raise ValueError("duplicate route identity")

    lexical_by_route: defaultdict[tuple[int, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in load_jsonl(lexical_candidates_path):
        key = (int(row["question_id"]), str(row["operand_id"]), int(row["report_year"]))
        if key not in route_by_key:
            raise ValueError(f"lexical candidate references an unknown route: {key}")
        lexical_by_route[key].append(row)
    missing_lexical_routes = sorted(set(route_by_key) - set(lexical_by_route))
    if missing_lexical_routes:
        raise ValueError(f"eligible routes missing lexical candidates: {missing_lexical_routes[:3]}")
    for rows in lexical_by_route.values():
        rows.sort(key=lambda row: int(row["candidate_rank"]))

    dense_requests: list[dict[str, Any]] = []
    request_positions: dict[str, dict[str, int]] = {}
    for route in routes:
        positions: dict[str, int] = {}
        if route["requested_scope"] is not None:
            positions["scoped"] = len(dense_requests)
            dense_requests.append(
                {
                    "query": route["metric_core_query"],
                    "ticker": route["ticker"],
                    "report_year": route["report_year"],
                    "scope": route["requested_scope"],
                }
            )
        positions["unscoped"] = len(dense_requests)
        dense_requests.append(
            {
                "query": route["metric_core_query"],
                "ticker": route["ticker"],
                "report_year": route["report_year"],
            }
        )
        request_positions[route["route_id"]] = positions

    dense_outputs = search_dense_batch(
        index_dir=dense_index_dir,
        requests=dense_requests,
        limit=top_k,
        requested_device=requested_device,
        encode_batch_size=int(config["retrieval"]["encode_batch_size"]),
        encoder_factory=encoder_factory,
    )

    route_reports: list[dict[str, Any]] = []
    provisional_candidates: list[dict[str, Any]] = []
    requested_uids: set[str] = set()
    for route in routes:
        key = (route["question_id"], route["operand_id"], route["report_year"])
        lexical_rows = lexical_by_route[key][:top_k]
        positions = request_positions[route["route_id"]]
        dense_rows = _merge_scoped_dense(
            scoped=(dense_outputs[positions["scoped"]] if "scoped" in positions else []),
            unscoped=dense_outputs[positions["unscoped"]],
            requested_scope=route["requested_scope"],
            top_k=top_k,
        )
        lexical_ranks = {
            str(row["internal_table_uid"]): int(row["candidate_rank"])
            for row in lexical_rows
        }
        dense_ranks = {
            str(row["internal_table_uid"]): int(row["dense_rank"])
            for row in dense_rows
        }
        lexical_lookup = {str(row["internal_table_uid"]): row for row in lexical_rows}
        dense_lookup = {str(row["internal_table_uid"]): row for row in dense_rows}
        lexical_uids = list(lexical_ranks)
        dense_uids = list(dense_ranks)
        overlap = set(lexical_uids) & set(dense_uids)
        lexical_only_recall = all(
            str(row.get("query_lane")) == "operand_any_recall" for row in lexical_rows
        )

        union_rows: list[dict[str, Any]] = []
        for uid in set(lexical_uids) | set(dense_uids):
            lexical_rank = lexical_ranks.get(uid)
            dense_rank = dense_ranks.get(uid)
            lexical_row = lexical_lookup.get(uid)
            dense_row = dense_lookup.get(uid)
            source_row = lexical_row or dense_row
            if source_row is None:
                raise AssertionError("hybrid source row unexpectedly missing")
            rrf_score = (
                (1.0 / (rrf_constant + lexical_rank) if lexical_rank is not None else 0.0)
                + (1.0 / (rrf_constant + dense_rank) if dense_rank is not None else 0.0)
            )
            union_rows.append(
                {
                    "uid": uid,
                    "document_id": str(source_row["document_id"]),
                    "scope": str(source_row.get("scope") or "unknown"),
                    "lexical_rank": lexical_rank,
                    "dense_rank": dense_rank,
                    "lexical_query_lane": (
                        str(lexical_row.get("query_lane")) if lexical_row else None
                    ),
                    "dense_lane": str(dense_row.get("dense_lane")) if dense_row else None,
                    "support": (
                        "lexical_and_dense"
                        if lexical_rank is not None and dense_rank is not None
                        else "lexical_only"
                        if lexical_rank is not None
                        else "dense_only"
                    ),
                    "rrf_score": rrf_score,
                }
            )
        union_rows.sort(
            key=lambda row: (
                -float(row["rrf_score"]),
                min(row["lexical_rank"] or 10**9, row["dense_rank"] or 10**9),
                row["uid"],
            )
        )
        selected = union_rows[:top_k]
        for hybrid_rank, row in enumerate(selected, start=1):
            requested_uids.add(row["uid"])
            provisional_candidates.append(
                {
                    "protocol": PROTOCOL,
                    "route_id": route["route_id"],
                    "question_id": route["question_id"],
                    "operand_id": route["operand_id"],
                    "ticker": route["ticker"],
                    "report_year": route["report_year"],
                    "requested_scope": route["requested_scope"],
                    "metric_core_query": route["metric_core_query"],
                    "hybrid_rank": hybrid_rank,
                    "internal_table_uid": row["uid"],
                    "document_id": row["document_id"],
                    "observed_scope": row["scope"],
                    "scope_match": (
                        None
                        if route["requested_scope"] is None
                        else row["scope"] == route["requested_scope"]
                    ),
                    "lexical_rank": row["lexical_rank"],
                    "dense_rank": row["dense_rank"],
                    "lexical_query_lane": row["lexical_query_lane"],
                    "dense_lane": row["dense_lane"],
                    "retrieval_support": row["support"],
                    "rrf_score": row["rrf_score"],
                }
            )

        route_reports.append(
            {
                "protocol": PROTOCOL,
                **route,
                "lexical_candidate_count": len(lexical_rows),
                "dense_candidate_count": len(dense_rows),
                "lexical_only_recall_lane": lexical_only_recall,
                "top1_agreement": bool(
                    lexical_uids and dense_uids and lexical_uids[0] == dense_uids[0]
                ),
                "top3_overlap_count": len(set(lexical_uids[:3]) & set(dense_uids[:3])),
                "top_k_overlap_count": len(overlap),
                "dense_addition_count": len(set(dense_uids) - set(lexical_uids)),
                "hybrid_candidate_count": len(selected),
                "navigation_metadata_only": True,
                "may_authorize_answer": False,
                "submission_eligible": False,
            }
        )

    assets: dict[str, dict[str, Any]] = {}
    for asset in load_jsonl(assets_path):
        uid = str(asset["internal_table_uid"])
        if uid in requested_uids:
            assets[uid] = asset
    missing_assets = sorted(requested_uids - set(assets))
    if missing_assets:
        raise ValueError(f"hybrid candidate UIDs missing from assets: {missing_assets[:3]}")

    candidates: list[dict[str, Any]] = []
    for candidate in provisional_candidates:
        asset = assets[candidate["internal_table_uid"]]
        locator = {
            "source_path": str(asset["source_path"]),
            "source_sha256": str(asset["source_sha256"]),
            "table_sha256": str(asset["table_sha256"]),
            "local_ordinal": int(asset["local_ordinal"]),
            "char_start": int(asset["char_start"]),
            "page_no": asset.get("page_no"),
        }
        candidates.append(
            {
                **candidate,
                "exact_table_locator": locator,
                "exact_table_locator_sha256": _canonical_sha(locator),
                "max_row_label_token_jaccard": _max_row_label_jaccard(
                    asset, str(candidate["metric_core_query"])
                ),
                "navigation_metadata_only": True,
                "may_authorize_evidence": False,
                "may_authorize_answer": False,
                "training_eligible": False,
                "submission_eligible": False,
            }
        )

    candidates_by_route: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_route[str(candidate["route_id"])].append(candidate)
    quality_values: dict[str, list[float]] = {
        "lexical_top1": [],
        "dense_top1": [],
        "hybrid_top1": [],
        "review_priority_top1": [],
    }
    pairwise_quality = Counter()
    weak_pairwise_quality = Counter()
    route_report_by_id = {str(row["route_id"]): row for row in route_reports}
    review_priority_changed_top1_count = 0
    for route_id, rows in candidates_by_route.items():
        lexical_top1 = next(row for row in rows if row.get("lexical_rank") == 1)
        dense_top1 = next(row for row in rows if row.get("dense_rank") == 1)
        hybrid_top1 = min(rows, key=lambda row: int(row["hybrid_rank"]))
        prioritized = sorted(
            rows,
            key=lambda row: (
                -float(row["rrf_score"]),
                -float(row["max_row_label_token_jaccard"]),
                min(row.get("lexical_rank") or 10**9, row.get("dense_rank") or 10**9),
                str(row["internal_table_uid"]),
            ),
        )
        for review_priority_rank, row in enumerate(prioritized, start=1):
            row["review_priority_rank"] = review_priority_rank
        review_priority_top1 = prioritized[0]
        review_priority_changed_top1_count += int(
            review_priority_top1["internal_table_uid"]
            != hybrid_top1["internal_table_uid"]
        )
        values = {
            "lexical_top1": float(lexical_top1["max_row_label_token_jaccard"]),
            "dense_top1": float(dense_top1["max_row_label_token_jaccard"]),
            "hybrid_top1": float(hybrid_top1["max_row_label_token_jaccard"]),
            "review_priority_top1": float(
                review_priority_top1["max_row_label_token_jaccard"]
            ),
        }
        for key, value in values.items():
            quality_values[key].append(value)
        preference = (
            "dense_better"
            if values["dense_top1"] > values["lexical_top1"]
            else "lexical_better"
            if values["lexical_top1"] > values["dense_top1"]
            else "equal"
        )
        pairwise_quality[preference] += 1
        route_report = route_report_by_id[route_id]
        if route_report["lexical_only_recall_lane"]:
            weak_pairwise_quality[preference] += 1
        route_report.update(
            {
                "lexical_top1_row_label_jaccard": values["lexical_top1"],
                "dense_top1_row_label_jaccard": values["dense_top1"],
                "hybrid_top1_row_label_jaccard": values["hybrid_top1"],
                "review_priority_top1_row_label_jaccard": values[
                    "review_priority_top1"
                ],
                "review_priority_changed_top1": (
                    review_priority_top1["internal_table_uid"]
                    != hybrid_top1["internal_table_uid"]
                ),
                "row_label_proxy_preference": preference,
            }
        )

    intrinsic_quality = {
        "metric": "maximum_value_blind_row_label_token_jaccard",
        "warning": "intrinsic proxy only; not gold answer or table accuracy",
        "summaries": {
            key: _quality_summary(values) for key, values in quality_values.items()
        },
        "dense_vs_lexical": dict(sorted(pairwise_quality.items())),
        "dense_vs_lexical_on_lexical_or_only_routes": dict(
            sorted(weak_pairwise_quality.items())
        ),
        "review_priority_changed_top1_count": review_priority_changed_top1_count,
    }

    review_queue: list[dict[str, Any]] = []
    review_bucket_counts = Counter()
    for route_report in route_reports:
        route_id = str(route_report["route_id"])
        if (
            int(route_report["top_k_overlap_count"]) == 0
            or float(route_report["review_priority_top1_row_label_jaccard"]) == 0.0
        ):
            priority_bucket = "hard_review"
            priority_reason = "zero cross-method overlap or zero row-label proxy"
        elif (
            route_report["top1_agreement"]
            and float(route_report["review_priority_top1_row_label_jaccard"]) >= 0.5
        ):
            priority_bucket = "agreement_high_proxy"
            priority_reason = "lexical and dense top-1 agree with strong row-label proxy"
        else:
            priority_bucket = "standard_review"
            priority_reason = "candidate methods partially agree or require semantic review"
        review_bucket_counts[priority_bucket] += 1
        top_candidates = sorted(
            candidates_by_route[route_id], key=lambda row: int(row["review_priority_rank"])
        )[:3]
        review_queue.append(
            {
                "protocol": PROTOCOL,
                "review_packet_id": _canonical_sha(
                    {"route_id": route_id, "purpose": "hybrid_table_review"}
                ),
                "route_id": route_id,
                "question_id": route_report["question_id"],
                "operand_id": route_report["operand_id"],
                "ticker": route_report["ticker"],
                "report_year": route_report["report_year"],
                "requested_scope": route_report["requested_scope"],
                "metric_core_query": route_report["metric_core_query"],
                "priority_bucket": priority_bucket,
                "priority_reason": priority_reason,
                "top1_agreement": route_report["top1_agreement"],
                "top_k_overlap_count": route_report["top_k_overlap_count"],
                "candidates": [
                    {
                        key: candidate[key]
                        for key in (
                            "review_priority_rank",
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
                    }
                    for candidate in top_candidates
                ],
                "raw_numeric_values_included": False,
                "navigation_metadata_only": True,
                "may_authorize_evidence": False,
                "may_authorize_answer": False,
                "training_eligible": False,
                "submission_eligible": False,
            }
        )

    support_counts = Counter(row["retrieval_support"] for row in candidates)
    recall_only_routes = [row for row in route_reports if row["lexical_only_recall_lane"]]
    coverage = {
        "protocol": PROTOCOL,
        "question_count": len(plans),
        "eligible_question_count": sum(
            str(plan.get("decomposition_status") or "") in eligible_statuses for plan in plans
        ),
        "ineligible_question_count": sum(
            str(plan.get("decomposition_status") or "") not in eligible_statuses for plan in plans
        ),
        "route_count": len(routes),
        "route_with_dense_candidates_count": sum(
            row["dense_candidate_count"] > 0 for row in route_reports
        ),
        "top1_agreement_count": sum(row["top1_agreement"] for row in route_reports),
        "top1_agreement_rate": (
            sum(row["top1_agreement"] for row in route_reports) / len(route_reports)
            if route_reports
            else 0.0
        ),
        "route_with_any_overlap_count": sum(
            row["top_k_overlap_count"] > 0 for row in route_reports
        ),
        "route_with_zero_overlap_count": sum(
            row["top_k_overlap_count"] == 0 for row in route_reports
        ),
        "lexical_only_recall_route_count": len(recall_only_routes),
        "lexical_only_recall_top1_agreement_count": sum(
            row["top1_agreement"] for row in recall_only_routes
        ),
        "lexical_only_recall_with_overlap_count": sum(
            row["top_k_overlap_count"] > 0 for row in recall_only_routes
        ),
        "dense_addition_count": sum(row["dense_addition_count"] for row in route_reports),
        "hybrid_candidate_count": len(candidates),
        "unique_hybrid_table_count": len(requested_uids),
        "hybrid_support_counts": dict(sorted(support_counts.items())),
        "review_priority_bucket_counts": dict(sorted(review_bucket_counts.items())),
        "hypothesis_results": {
            "H1_dense_rescues_empty_lexical_routes": {
                "status": "NOT_TESTABLE_ON_ELIGIBLE_ROUTES",
                "reason": "all eligible routes already have lexical OR-recall candidates",
            },
            "H2_dense_adds_candidates_to_weak_lexical_routes": {
                "status": "OBSERVED_NOT_ACCURACY_PROOF",
                "weak_route_count": len(recall_only_routes),
                "weak_routes_with_overlap": sum(
                    row["top_k_overlap_count"] > 0 for row in recall_only_routes
                ),
            },
            "H3_cross_method_agreement_can_prioritize_review": {
                "status": "OBSERVED_NOT_GOLD_VALIDATED",
                "top1_agreement_count": sum(row["top1_agreement"] for row in route_reports),
            },
            "H4_dense_solves_incomplete_question_plans": {
                "status": "REJECTED_BY_DESIGN",
                "ineligible_question_count": sum(
                    str(plan.get("decomposition_status") or "") not in eligible_statuses
                    for plan in plans
                ),
            },
            "H5_hybrid_improves_value_blind_row_label_alignment": {
                "status": "OBSERVED_PROXY_ONLY",
                "lexical_top1_mean": intrinsic_quality["summaries"]["lexical_top1"]["mean"],
                "dense_top1_mean": intrinsic_quality["summaries"]["dense_top1"]["mean"],
                "hybrid_top1_mean": intrinsic_quality["summaries"]["hybrid_top1"]["mean"],
                "review_priority_top1_mean": intrinsic_quality["summaries"][
                    "review_priority_top1"
                ]["mean"],
                "review_priority_changed_top1_count": review_priority_changed_top1_count,
            },
        },
        "intrinsic_value_blind_quality": intrinsic_quality,
        "raw_numeric_values_included": False,
        "answer_eligible": False,
        "submission_eligible": False,
    }

    output_dir.mkdir(parents=True)
    route_path = output_dir / "route_comparison_v1.jsonl"
    candidate_path = output_dir / "hybrid_table_candidates_v1.jsonl"
    review_queue_path = output_dir / "hybrid_review_queue_v1.jsonl"
    coverage_path = output_dir / "coverage_report_v1.json"
    _write_jsonl(route_path, route_reports)
    _write_jsonl(candidate_path, candidates)
    _write_jsonl(review_queue_path, review_queue)
    coverage_path.write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
            "plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
            "lexical_candidates": {
                "path": str(lexical_candidates_path),
                "sha256": sha256_file(lexical_candidates_path),
            },
            "dense_manifest": {
                "path": str(dense_index_dir / "manifest.json"),
                "sha256": sha256_file(dense_index_dir / "manifest.json"),
            },
            "assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
        },
        "outputs": {
            path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in (route_path, candidate_path, review_queue_path, coverage_path)
        },
        "authorization": config["authorization"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return coverage


def validate_hybrid_retrieval_analysis(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
    expected_route_count: int = 1232,
) -> dict[str, Any]:
    """Fail closed on hash, coverage, source-binding, or authority drift."""
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected hybrid artifact protocol")
    for filename, contract in manifest["outputs"].items():
        if sha256_file(artifact_dir / filename) != contract["sha256"]:
            raise ValueError(f"output hash mismatch: {filename}")
    routes = list(load_jsonl(artifact_dir / "route_comparison_v1.jsonl"))
    candidates = list(load_jsonl(artifact_dir / "hybrid_table_candidates_v1.jsonl"))
    review_queue = list(load_jsonl(artifact_dir / "hybrid_review_queue_v1.jsonl"))
    coverage = json.loads((artifact_dir / "coverage_report_v1.json").read_text(encoding="utf-8"))
    if int(coverage["question_count"]) != expected_question_count:
        raise ValueError("hybrid question count mismatch")
    if len(routes) != expected_route_count or int(coverage["route_count"]) != len(routes):
        raise ValueError("hybrid route count mismatch")
    route_ids = [str(row["route_id"]) for row in routes]
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("duplicate hybrid route ID")
    route_id_set = set(route_ids)
    candidate_keys: set[tuple[str, str]] = set()
    review_ranks: defaultdict[str, list[int]] = defaultdict(list)
    for row in [*routes, *candidates, *review_queue]:
        if row.get("navigation_metadata_only") is not True:
            raise ValueError("hybrid row lost navigation-only boundary")
        if row.get("may_authorize_answer") is not False:
            raise ValueError("hybrid row incorrectly authorizes an answer")
        if row.get("submission_eligible") is not False:
            raise ValueError("hybrid row incorrectly enables submission")
        if _contains_forbidden_key(row):
            raise ValueError("hybrid artifact contains a forbidden value field")
    for row in candidates:
        route_id = str(row["route_id"])
        if route_id not in route_id_set:
            raise ValueError("hybrid candidate references an unknown route")
        key = (route_id, str(row["internal_table_uid"]))
        if key in candidate_keys:
            raise ValueError("duplicate hybrid route/table candidate")
        candidate_keys.add(key)
        review_ranks[route_id].append(int(row["review_priority_rank"]))
        row_label_score = float(row["max_row_label_token_jaccard"])
        if not 0.0 <= row_label_score <= 1.0:
            raise ValueError("hybrid candidate has an invalid row-label proxy")
        locator = row.get("exact_table_locator")
        if not isinstance(locator, Mapping) or row.get("exact_table_locator_sha256") != _canonical_sha(locator):
            raise ValueError("hybrid candidate has an invalid exact table locator")
        if "human_verified" in row or row.get("may_authorize_evidence") is not False:
            raise ValueError("hybrid candidate has invalid review/evidence authority")
    for route_id, ranks in review_ranks.items():
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError(f"invalid review-priority ranks for route {route_id}")
    packet_ids = [str(row["review_packet_id"]) for row in review_queue]
    if len(review_queue) != len(routes) or len(packet_ids) != len(set(packet_ids)):
        raise ValueError("hybrid review queue does not cover every route uniquely")
    allowed_buckets = {"hard_review", "standard_review", "agreement_high_proxy"}
    for row in review_queue:
        if str(row["route_id"]) not in route_id_set:
            raise ValueError("hybrid review packet references an unknown route")
        if row.get("priority_bucket") not in allowed_buckets:
            raise ValueError("hybrid review packet has an invalid priority bucket")
        if not 1 <= len(row.get("candidates") or []) <= 3:
            raise ValueError("hybrid review packet must contain one to three candidates")
        if row.get("raw_numeric_values_included") is not False:
            raise ValueError("hybrid review packet exposes raw numeric values")
    if int(coverage["hybrid_candidate_count"]) != len(candidates):
        raise ValueError("hybrid candidate count mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "route_count": len(routes),
        "hybrid_candidate_count": len(candidates),
        "review_packet_count": len(review_queue),
        "answer_eligible": False,
        "submission_eligible": False,
    }
