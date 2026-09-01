"""Research-only row, period, unit, and scope diagnostics for hybrid routes.

The module deliberately exposes labels, headers, coordinates, and source
hashes but redacts financial amounts.  It is for measuring and reviewing the
path to an exact cell; it cannot select a value or produce an answer.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit
from finance_query.e2e.core.financial_taxonomy import normalize_label
from finance_query.e2e.core.table_retrieval import load_jsonl, sha256_file
from finance_query.research.full_corpus_candidate_retrieval import _tokens


PROTOCOL = "vifinqa_exact_cell_research_v1"
FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "rows",
        "raw_source_row",
        "raw_source_cell",
    }
)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
GROUPED_AMOUNT_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:[.,]\d{3})+|\d{5,})(?!\d)")
NUMERIC_CELL_RE = re.compile(r"\d")


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


def _redact_amounts(value: object) -> str:
    """Keep years/short labels while suppressing grouped monetary amounts."""
    return GROUPED_AMOUNT_RE.sub("[SỐ_ĐÃ_ẨN]", str(value or "")).strip()


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _row_candidates(
    asset: Mapping[str, Any], *, query: str, maximum: int
) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    header_rows = {int(value) for value in asset.get("header_row_indices") or []}
    scored: list[tuple[float, int, str, list[int]]] = []
    for row_index, raw_row in enumerate(asset.get("rows") or []):
        if row_index in header_rows or not raw_row:
            continue
        row = [str(cell) for cell in raw_row]
        numeric_columns = [
            column_index
            for column_index, cell in enumerate(row[1:], start=1)
            if NUMERIC_CELL_RE.search(cell)
        ]
        if not numeric_columns:
            continue
        label = row[0].strip()
        label_tokens = _tokens(label)
        score = len(query_tokens & label_tokens) / max(1, len(query_tokens | label_tokens))
        scored.append((score, row_index, label, numeric_columns))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [
        {
            "row_rank": rank,
            "row_index": row_index,
            "row_label": _redact_amounts(label),
            "row_label_token_jaccard": score,
            "numeric_column_indices": numeric_columns,
            "row_label_sha256": hashlib.sha256(label.encode("utf-8")).hexdigest(),
        }
        for rank, (score, row_index, label, numeric_columns) in enumerate(
            scored[:maximum], start=1
        )
    ]


def _all_numeric_columns(asset: Mapping[str, Any]) -> list[int]:
    header_rows = {int(value) for value in asset.get("header_row_indices") or []}
    columns: set[int] = set()
    for row_index, raw_row in enumerate(asset.get("rows") or []):
        if row_index in header_rows:
            continue
        for column_index, value in enumerate(raw_row):
            if column_index > 0 and NUMERIC_CELL_RE.search(str(value)):
                columns.add(column_index)
    return sorted(columns)


def _column_headers(asset: Mapping[str, Any], column_index: int) -> list[str]:
    values: list[str] = []
    headers = asset.get("headers") or []
    if column_index < len(headers):
        values.append(str(headers[column_index]))
    for header_row_index in asset.get("header_row_indices") or []:
        raw_rows = asset.get("rows") or []
        if not isinstance(header_row_index, int) or not 0 <= header_row_index < len(raw_rows):
            continue
        row = raw_rows[header_row_index]
        if column_index < len(row):
            values.append(str(row[column_index]))
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _period_unit_diagnostic(
    asset: Mapping[str, Any], *, requested_year: int, requested_scope: str | None
) -> dict[str, Any]:
    numeric_columns = _all_numeric_columns(asset)
    column_records: list[dict[str, Any]] = []
    raw_headers_by_column: dict[int, list[str]] = {}
    matching_columns: list[int] = []
    unit_anchors: list[dict[str, Any]] = []
    for column_index in numeric_columns:
        raw_headers = _column_headers(asset, column_index)
        raw_headers_by_column[column_index] = raw_headers
        years = sorted(
            {
                int(match.group(1))
                for header in raw_headers
                for match in YEAR_RE.finditer(header)
            }
        )
        if requested_year in years:
            matching_columns.append(column_index)
        for raw_header in raw_headers:
            unit_anchors.append({"raw_source_cell": raw_header})
        column_records.append(
            {
                "column_index": column_index,
                "header_labels": [_redact_amounts(value) for value in raw_headers],
                "header_years": years,
                "header_sha256": hashlib.sha256(
                    "\n".join(raw_headers).encode("utf-8")
                ).hexdigest(),
            }
        )
    if not numeric_columns:
        period_status = "NO_NUMERIC_COLUMNS"
    elif len(matching_columns) == 1:
        period_status = "UNIQUE_YEAR_HEADER_CANDIDATE"
    elif len(matching_columns) > 1:
        period_status = "AMBIGUOUS_YEAR_HEADER_COLUMNS"
    else:
        period_status = "NO_YEAR_HEADER_CANDIDATE"
    current_header_columns = [
        column_index
        for column_index, headers in raw_headers_by_column.items()
        if any(
            term in normalize_label(header)
            for header in headers
            for term in ("nam nay", "so cuoi nam")
        )
    ]
    prior_header_columns = [
        column_index
        for column_index, headers in raw_headers_by_column.items()
        if any(
            term in normalize_label(header)
            for header in headers
            for term in ("nam truoc", "so dau nam", "ky truoc")
        )
    ]
    if period_status != "NO_YEAR_HEADER_CANDIDATE":
        fallback_period_status = "NOT_NEEDED_EXPLICIT_YEAR_HEADER"
        fallback_column_index = None
    elif requested_year != int(asset.get("report_year") or 0):
        fallback_period_status = "RESEARCH_REPORT_YEAR_MISMATCH"
        fallback_column_index = None
    elif len(current_header_columns) == 1:
        fallback_period_status = "RESEARCH_CURRENT_HEADER_REPORT_YEAR_CANDIDATE"
        fallback_column_index = current_header_columns[0]
    elif len(numeric_columns) == 1 and not prior_header_columns:
        fallback_period_status = "RESEARCH_SINGLE_NUMERIC_COLUMN_REPORT_YEAR_CANDIDATE"
        fallback_column_index = numeric_columns[0]
    else:
        fallback_period_status = "RESEARCH_AMBIGUOUS_OR_UNSUPPORTED"
        fallback_column_index = None
    source_unit, _, multiplier = resolve_source_unit(unit_anchors)
    if source_unit is not None and multiplier is not None:
        unit_status = "UNIQUE_HEADER_UNIT_CANDIDATE"
    elif asset.get("unit_hint"):
        unit_status = "UNVERIFIED_TABLE_UNIT_HINT"
    else:
        unit_status = "UNIT_MISSING_OR_AMBIGUOUS"
    observed_scope = str(asset.get("scope") or "unknown")
    scope_status = (
        "SCOPE_NOT_REQUESTED"
        if requested_scope is None
        else "SCOPE_MATCH"
        if observed_scope == requested_scope
        else "SCOPE_MISMATCH"
    )
    return {
        "period_status": period_status,
        "fallback_period_status": fallback_period_status,
        "fallback_period_column_index": fallback_column_index,
        "requested_year": requested_year,
        "matching_year_column_indices": matching_columns,
        "numeric_column_count": len(numeric_columns),
        "column_headers": column_records,
        "unit_status": unit_status,
        "source_unit_candidate": source_unit,
        "scope_status": scope_status,
        "observed_scope": observed_scope,
    }


def _asset_locator(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_path": str(asset["source_path"]),
        "source_sha256": str(asset["source_sha256"]),
        "table_sha256": str(asset["table_sha256"]),
        "local_ordinal": int(asset["local_ordinal"]),
        "char_start": int(asset["char_start"]),
        "page_no": asset.get("page_no"),
    }


def _review_sample(
    review_queue: list[Mapping[str, Any]], sample_per_bucket: Mapping[str, int]
) -> list[dict[str, Any]]:
    by_bucket: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for packet in review_queue:
        by_bucket[str(packet["priority_bucket"])].append(packet)
    sample: list[dict[str, Any]] = []
    for bucket, expected_count in sorted(sample_per_bucket.items()):
        packets = sorted(
            by_bucket[bucket], key=lambda packet: str(packet["review_packet_id"]) 
        )
        if len(packets) < int(expected_count):
            raise ValueError(f"bucket {bucket} has fewer packets than requested sample")
        for packet in packets[: int(expected_count)]:
            sample.append(
                {
                    "protocol": PROTOCOL,
                    "sample_id": _canonical_sha(
                        {"review_packet_id": packet["review_packet_id"], "sample": "v1"}
                    ),
                    "review_packet_id": packet["review_packet_id"],
                    "route_id": packet["route_id"],
                    "question_id": packet["question_id"],
                    "operand_id": packet["operand_id"],
                    "ticker": packet["ticker"],
                    "report_year": packet["report_year"],
                    "requested_scope": packet["requested_scope"],
                    "metric_core_query": packet["metric_core_query"],
                    "priority_bucket": bucket,
                    "candidate_count": len(packet.get("candidates") or []),
                    "review_state": "UNREVIEWED",
                    "human_verified": False,
                    "navigation_metadata_only": True,
                    "may_authorize_evidence": False,
                    "may_authorize_answer": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                }
            )
    return sorted(sample, key=lambda row: (str(row["priority_bucket"]), str(row["sample_id"])))


def build_exact_cell_research(
    *,
    config_path: Path,
    hybrid_review_queue_path: Path,
    assets_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build exact-row and period/unit diagnostics without numeric disclosure."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("protocol") != PROTOCOL:
        raise ValueError("unexpected exact-cell research protocol")
    review_queue = list(load_jsonl(hybrid_review_queue_path))
    expected_route_count = int(config["expected_route_count"])
    if len(review_queue) != expected_route_count:
        raise ValueError("hybrid review queue does not have the expected route count")
    if len({str(row["route_id"]) for row in review_queue}) != len(review_queue):
        raise ValueError("hybrid review queue route IDs are not unique")

    selected_candidates = [
        candidate
        for packet in review_queue
        for candidate in (packet.get("candidates") or [])[
            : int(config["research"]["max_tables_per_route"])
        ]
    ]
    selected_uids = {str(candidate["internal_table_uid"]) for candidate in selected_candidates}
    assets: dict[str, dict[str, Any]] = {}
    for asset in load_jsonl(assets_path):
        uid = str(asset["internal_table_uid"])
        if uid in selected_uids:
            assets[uid] = asset
    missing_uids = sorted(selected_uids - set(assets))
    if missing_uids:
        raise ValueError(f"selected candidate UID missing from assets: {missing_uids[:3]}")

    table_diagnostics: list[dict[str, Any]] = []
    row_candidates: list[dict[str, Any]] = []
    for packet in review_queue:
        for candidate in (packet.get("candidates") or [])[
            : int(config["research"]["max_tables_per_route"])
        ]:
            uid = str(candidate["internal_table_uid"])
            asset = assets[uid]
            locator = _asset_locator(asset)
            diagnostic_id = _canonical_sha(
                {"route_id": packet["route_id"], "internal_table_uid": uid}
            )
            rows = _row_candidates(
                asset,
                query=str(packet["metric_core_query"]),
                maximum=int(config["research"]["max_rows_per_table"]),
            )
            period_unit = _period_unit_diagnostic(
                asset,
                requested_year=int(packet["report_year"]),
                requested_scope=(
                    str(packet["requested_scope"])
                    if packet.get("requested_scope") is not None
                    else None
                ),
            )
            table_diagnostics.append(
                {
                    "protocol": PROTOCOL,
                    "diagnostic_id": diagnostic_id,
                    "route_id": packet["route_id"],
                    "question_id": packet["question_id"],
                    "operand_id": packet["operand_id"],
                    "ticker": packet["ticker"],
                    "report_year": packet["report_year"],
                    "requested_scope": packet["requested_scope"],
                    "metric_core_query": packet["metric_core_query"],
                    "priority_bucket": packet["priority_bucket"],
                    "review_priority_rank": candidate["review_priority_rank"],
                    "internal_table_uid": uid,
                    "document_id": str(asset["document_id"]),
                    "exact_table_locator": locator,
                    "exact_table_locator_sha256": _canonical_sha(locator),
                    "table_function": asset.get("table_function") or {},
                    "table_section": asset.get("table_section") or {},
                    "row_candidate_status": (
                        "ROW_CANDIDATES_FOUND" if rows else "NO_ROW_CANDIDATE"
                    ),
                    "row_candidate_count": len(rows),
                    **period_unit,
                    "raw_numeric_values_included": False,
                    "human_verified": False,
                    "navigation_metadata_only": True,
                    "may_authorize_evidence": False,
                    "may_authorize_answer": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                }
            )
            row_candidates.extend(
                {
                    "protocol": PROTOCOL,
                    "row_candidate_id": _canonical_sha(
                        {
                            "diagnostic_id": diagnostic_id,
                            "row_index": row["row_index"],
                        }
                    ),
                    "diagnostic_id": diagnostic_id,
                    "route_id": packet["route_id"],
                    "question_id": packet["question_id"],
                    "operand_id": packet["operand_id"],
                    "internal_table_uid": uid,
                    "document_id": str(asset["document_id"]),
                    "exact_table_locator_sha256": _canonical_sha(locator),
                    **row,
                    "raw_numeric_values_included": False,
                    "human_verified": False,
                    "navigation_metadata_only": True,
                    "may_authorize_evidence": False,
                    "may_authorize_answer": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                }
                for row in rows
            )

    review_sample = _review_sample(
        review_queue, config["review_sample"]["per_priority_bucket"]
    )
    diagnostics_by_route: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for diagnostic in table_diagnostics:
        diagnostics_by_route[str(diagnostic["route_id"])].append(diagnostic)
    rows_by_diagnostic: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in row_candidates:
        rows_by_diagnostic[str(row["diagnostic_id"])].append(row)
    for sample in review_sample:
        diagnostic_packets = sorted(
            diagnostics_by_route[str(sample["route_id"])],
            key=lambda row: int(row["review_priority_rank"]),
        )
        sample["review_instruction"] = (
            "Verify table subject, source scope, row label, period header, and unit; "
            "record approve, reject, or uncertain outside this non-approving artifact."
        )
        sample["candidate_packets"] = [
            {
                key: diagnostic[key]
                for key in (
                    "review_priority_rank",
                    "internal_table_uid",
                    "document_id",
                    "exact_table_locator",
                    "exact_table_locator_sha256",
                    "table_function",
                    "table_section",
                    "row_candidate_status",
                    "period_status",
                    "matching_year_column_indices",
                    "numeric_column_count",
                    "column_headers",
                    "fallback_period_status",
                    "fallback_period_column_index",
                    "unit_status",
                    "source_unit_candidate",
                    "scope_status",
                    "observed_scope",
                )
            }
            | {
                "row_candidates": [
                    {
                        key: row[key]
                        for key in (
                            "row_rank",
                            "row_index",
                            "row_label",
                            "row_label_token_jaccard",
                            "numeric_column_indices",
                            "row_label_sha256",
                        )
                    }
                    for row in sorted(
                        rows_by_diagnostic[str(diagnostic["diagnostic_id"])],
                        key=lambda row: int(row["row_rank"]),
                    )
                ]
            }
            for diagnostic in diagnostic_packets
        ]
    period_counts = Counter(row["period_status"] for row in table_diagnostics)
    fallback_period_counts = Counter(
        row["fallback_period_status"] for row in table_diagnostics
    )
    unit_counts = Counter(row["unit_status"] for row in table_diagnostics)
    scope_counts = Counter(row["scope_status"] for row in table_diagnostics)
    row_counts = Counter(row["row_candidate_status"] for row in table_diagnostics)
    sample_counts = Counter(row["priority_bucket"] for row in review_sample)
    structural_by_bucket = Counter()
    structural_candidate_count = 0
    fallback_structural_by_bucket = Counter()
    fallback_structural_candidate_count = 0
    for row in table_diagnostics:
        structurally_candidate_ready = (
            row["row_candidate_status"] == "ROW_CANDIDATES_FOUND"
            and row["period_status"] == "UNIQUE_YEAR_HEADER_CANDIDATE"
            and row["unit_status"] == "UNIQUE_HEADER_UNIT_CANDIDATE"
            and row["scope_status"] in {"SCOPE_MATCH", "SCOPE_NOT_REQUESTED"}
        )
        structural_candidate_count += int(structurally_candidate_ready)
        if structurally_candidate_ready:
            structural_by_bucket[str(row["priority_bucket"])] += 1
        fallback_candidate_ready = (
            row["row_candidate_status"] == "ROW_CANDIDATES_FOUND"
            and row["period_status"] == "NO_YEAR_HEADER_CANDIDATE"
            and str(row["fallback_period_status"]).startswith("RESEARCH_")
            and row["fallback_period_column_index"] is not None
            and row["unit_status"] == "UNIQUE_HEADER_UNIT_CANDIDATE"
            and row["scope_status"] in {"SCOPE_MATCH", "SCOPE_NOT_REQUESTED"}
        )
        fallback_structural_candidate_count += int(fallback_candidate_ready)
        if fallback_candidate_ready:
            fallback_structural_by_bucket[str(row["priority_bucket"])] += 1
    coverage = {
        "protocol": PROTOCOL,
        "route_count": len(review_queue),
        "table_diagnostic_count": len(table_diagnostics),
        "row_candidate_count": len(row_candidates),
        "unique_table_count": len(selected_uids),
        "row_candidate_status_counts": dict(sorted(row_counts.items())),
        "period_status_counts": dict(sorted(period_counts.items())),
        "fallback_period_status_counts": dict(sorted(fallback_period_counts.items())),
        "unit_status_counts": dict(sorted(unit_counts.items())),
        "scope_status_counts": dict(sorted(scope_counts.items())),
        "structural_candidate_only_count": structural_candidate_count,
        "structural_candidate_only_by_priority_bucket": dict(sorted(structural_by_bucket.items())),
        "fallback_structural_candidate_only_count": fallback_structural_candidate_count,
        "fallback_structural_candidate_only_by_priority_bucket": dict(
            sorted(fallback_structural_by_bucket.items())
        ),
        "review_sample_count": len(review_sample),
        "review_sample_bucket_counts": dict(sorted(sample_counts.items())),
        "hypotheses": {
            "H1_row_label_can_create_reviewable_candidates": {
                "status": "OBSERVED_NOT_GOLD_VALIDATED",
                "tables_with_row_candidates": row_counts["ROW_CANDIDATES_FOUND"],
            },
            "H2_requested_year_can_be_read_from_headers": {
                "status": "OBSERVED_NOT_GOLD_VALIDATED",
                "unique_year_header_candidates": period_counts[
                    "UNIQUE_YEAR_HEADER_CANDIDATE"
                ],
            },
            "H3_header_units_are_sufficient_for_execution": {
                "status": "NOT_PROVEN_BY_DIAGNOSTIC",
                "unique_header_unit_candidates": unit_counts[
                    "UNIQUE_HEADER_UNIT_CANDIDATE"
                ],
            },
            "H4_row_period_unit_scope_can_form_exact_cell_candidates": {
                "status": "STRUCTURAL_CANDIDATES_ONLY",
                "candidate_count": structural_candidate_count,
                "warning": "does not confirm semantic row, exact value cell, or answer",
            },
            "H5_current_header_period_fallback_can_expand_research_coverage": {
                "status": "OBSERVED_RESEARCH_ONLY",
                "fallback_candidate_count": fallback_structural_candidate_count,
                "warning": "requires source-title and exact-header validation before any use",
            },
        },
        "raw_numeric_values_included": False,
        "human_verified_count": 0,
        "answer_eligible": False,
        "submission_eligible": False,
    }

    output_dir.mkdir(parents=True)
    diagnostic_path = output_dir / "table_diagnostics_v1.jsonl"
    row_path = output_dir / "row_candidates_v1.jsonl"
    sample_path = output_dir / "stratified_review_sample_v1.jsonl"
    coverage_path = output_dir / "coverage_report_v1.json"
    _write_jsonl(diagnostic_path, table_diagnostics)
    _write_jsonl(row_path, row_candidates)
    _write_jsonl(sample_path, review_sample)
    coverage_path.write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
            "hybrid_review_queue": {
                "path": str(hybrid_review_queue_path),
                "sha256": sha256_file(hybrid_review_queue_path),
            },
            "assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
        },
        "outputs": {
            path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in (diagnostic_path, row_path, sample_path, coverage_path)
        },
        "authorization": config["authorization"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return coverage


def validate_exact_cell_research(
    artifact_dir: Path,
    *,
    expected_route_count: int = 1232,
    expected_review_sample_count: int = 150,
) -> dict[str, Any]:
    """Reject incomplete coverage, numeric leakage, or any authority drift."""
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("unexpected exact-cell research protocol")
    for filename, contract in manifest["outputs"].items():
        if sha256_file(artifact_dir / filename) != contract["sha256"]:
            raise ValueError(f"output hash mismatch: {filename}")
    diagnostics = list(load_jsonl(artifact_dir / "table_diagnostics_v1.jsonl"))
    rows = list(load_jsonl(artifact_dir / "row_candidates_v1.jsonl"))
    sample = list(load_jsonl(artifact_dir / "stratified_review_sample_v1.jsonl"))
    coverage = json.loads((artifact_dir / "coverage_report_v1.json").read_text(encoding="utf-8"))
    if int(coverage["route_count"]) != expected_route_count:
        raise ValueError("exact-cell research route count mismatch")
    if len(sample) != expected_review_sample_count:
        raise ValueError("exact-cell research sample count mismatch")
    diagnostic_ids = [str(row["diagnostic_id"]) for row in diagnostics]
    row_ids = [str(row["row_candidate_id"]) for row in rows]
    sample_ids = [str(row["sample_id"]) for row in sample]
    if len(diagnostic_ids) != len(set(diagnostic_ids)):
        raise ValueError("duplicate table diagnostic ID")
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("duplicate row candidate ID")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate review sample ID")
    diagnostic_id_set = set(diagnostic_ids)
    for row in [*diagnostics, *rows, *sample]:
        if _contains_forbidden_key(row):
            raise ValueError("exact-cell research artifact contains a forbidden value field")
        if row.get("human_verified") is not False:
            raise ValueError("exact-cell research row is incorrectly human verified")
        if row.get("navigation_metadata_only") is not True:
            raise ValueError("exact-cell research row lost navigation-only boundary")
        if row.get("may_authorize_answer") is not False:
            raise ValueError("exact-cell research row incorrectly authorizes an answer")
        if row.get("submission_eligible") is not False:
            raise ValueError("exact-cell research row incorrectly enables submission")
    for diagnostic in diagnostics:
        locator = diagnostic.get("exact_table_locator")
        if not isinstance(locator, Mapping) or diagnostic.get("exact_table_locator_sha256") != _canonical_sha(locator):
            raise ValueError("invalid exact table locator")
    for row in rows:
        if str(row["diagnostic_id"]) not in diagnostic_id_set:
            raise ValueError("row candidate references an unknown table diagnostic")
        if row.get("raw_numeric_values_included") is not False:
            raise ValueError("row candidate exposes a raw numeric value")
    if int(coverage["table_diagnostic_count"]) != len(diagnostics):
        raise ValueError("table diagnostic count mismatch")
    if int(coverage["row_candidate_count"]) != len(rows):
        raise ValueError("row candidate count mismatch")
    if int(coverage["review_sample_count"]) != len(sample):
        raise ValueError("review sample count mismatch")
    return {
        "status": "PASS",
        "route_count": expected_route_count,
        "table_diagnostic_count": len(diagnostics),
        "row_candidate_count": len(rows),
        "review_sample_count": len(sample),
        "human_verified_count": 0,
        "answer_eligible": False,
        "submission_eligible": False,
    }
