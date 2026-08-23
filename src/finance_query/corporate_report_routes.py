"""Fail-closed route hints derived from a corporate/report graph.

Hints make report-family and relationship metadata available to retrieval. They
are not evidence bindings and intentionally stop when the question lacks a
single issuer, year, or scope.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .corporate_report_graph import GRAPH_SOURCE_CONTRACT
from .report_entities import canonical_entity_name
from .table_structure import sha256_file


CORPORATE_REPORT_ROUTE_HINT_VERSION = 1
CORPORATE_REPORT_ROUTE_HINT_PROTOCOL = "corporate_report_route_hints_v1"
ROUTE_HINT_SOURCE_CONTRACT = {
    **GRAPH_SOURCE_CONTRACT,
    "may_select_table_candidate": True,
    "may_select_value_cell": False,
    "may_compute_answer": False,
    "may_infer_scope": False,
}


def _sha256_json(value: Mapping[str, Any]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _single_strings(values: object) -> list[str]:
    return sorted({str(value) for value in values or [] if str(value)})


def _single_years(values: object) -> list[int]:
    return sorted({value for value in values or [] if isinstance(value, int)})


def build_corporate_report_route_hints(
    review_items: Iterable[Mapping[str, Any]],
    report_families: Iterable[Mapping[str, Any]],
    report_edges: Iterable[Mapping[str, Any]],
    corporate_edges: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build one non-evidence routing record for every question.

    An exact document is emitted only if the question itself supplies exactly
    one ticker, year and known scope and the family has exactly one matching
    source-derived member.  Adjacent-period documents stay informational and
    never become primary document candidates.
    """
    by_family = {
        (str(row.get("issuer_ticker") or ""), row.get("report_year"), str(row.get("report_type") or "")): row
        for row in report_families
    }
    adjacent: dict[str, set[str]] = {}
    for edge in report_edges:
        if edge.get("relation_type") != "adjacent_reporting_period_candidate":
            continue
        if edge.get("period_alignment_status") != "matching_source_period_end":
            continue
        source, target = str(edge.get("source_document_id") or ""), str(edge.get("target_document_id") or "")
        if source and target:
            adjacent.setdefault(source, set()).add(target)
            adjacent.setdefault(target, set()).add(source)
    rows: list[dict[str, Any]] = []
    for item in review_items:
        question_id = item.get("id")
        plan = item.get("question_plan") or {}
        tickers, years = _single_strings(plan.get("tickers")), _single_years(plan.get("years"))
        scope = str(plan.get("scope") or "unknown")
        identity = {"question_id": question_id}
        base = {
            "schema_version": CORPORATE_REPORT_ROUTE_HINT_VERSION,
            "route_hint_id": _sha256_json(identity),
            **identity,
            "question_context": {"tickers": tickers, "years": years, "scope": scope},
            "primary_document_ids": [],
            "scope_exclusion_document_ids": [],
            "adjacent_period_document_ids": [],
            "relationship_table_uids": [],
            "source_contract": dict(ROUTE_HINT_SOURCE_CONTRACT),
        }
        if len(tickers) != 1 or len(years) != 1 or scope in {"", "unknown"}:
            base["route_hint_status"] = "question_context_incomplete_or_ambiguous"
            rows.append(base)
            continue
        family = by_family.get((tickers[0], years[0], "financial_statements"))
        if family is None:
            base["route_hint_status"] = "no_matching_source_report_family"
            rows.append(base)
            continue
        if family.get("family_status") != "source_derived_unique_scope_members":
            base["route_hint_status"] = "ambiguous_source_report_family"
            rows.append(base)
            continue
        members = family.get("members") or []
        primary = sorted(
            str(member.get("document_id") or "")
            for member in members
            if str(member.get("report_scope") or "unknown") == scope
        )
        primary = [value for value in primary if value]
        if len(primary) != 1:
            base["route_hint_status"] = "requested_scope_not_uniquely_available"
            base["scope_exclusion_document_ids"] = sorted(
                str(member.get("document_id") or "")
                for member in members
                if str(member.get("document_id") or "") and str(member.get("report_scope") or "unknown") != scope
            )
            rows.append(base)
            continue
        primary_document = primary[0]
        base["primary_document_ids"] = primary
        base["scope_exclusion_document_ids"] = sorted(
            str(member.get("document_id") or "")
            for member in members
            if str(member.get("document_id") or "") and str(member.get("document_id") or "") != primary_document
        )
        base["adjacent_period_document_ids"] = sorted(adjacent.get(primary_document, set()))
        normalized_question = canonical_entity_name(item.get("question") or "")
        relationship_uids = {
            str(edge.get("internal_table_uid") or "")
            for edge in corporate_edges
            if str(edge.get("issuer_ticker") or "") == tickers[0]
            and edge.get("report_year") == years[0]
            and str(edge.get("report_scope") or "unknown") == scope
            and str(edge.get("document_id") or "") == primary_document
            # The graph edge has already passed source-literal legal-name
            # extraction. Two distinctive name tokens (for example
            # ``VietjetAir Cargo``) are sufficient for an exact substring
            # hint; this still never chooses a value cell.
            and len(str(edge.get("target_canonical_entity") or "").split()) >= 2
            and str(edge.get("target_canonical_entity") or "") in normalized_question
        }
        base["relationship_table_uids"] = sorted(uid for uid in relationship_uids if uid)
        base["route_hint_status"] = "exact_scope_source_document_found"
        rows.append(base)
    return rows


def validate_corporate_report_route_hints(output_dir: Path) -> dict[str, Any]:
    """Validate a route-hint artifact without treating it as evidence."""
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "corporate_report_route_hints_v1.manifest.json"
    hints_path = output_dir / "corporate_report_route_hints_v1.jsonl"
    if not manifest_path.is_file() or not hints_path.is_file():
        raise FileNotFoundError("Corporate report route-hint output is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != CORPORATE_REPORT_ROUTE_HINT_VERSION:
        raise ValueError("Unsupported corporate report route-hint schema")
    if manifest.get("protocol") != CORPORATE_REPORT_ROUTE_HINT_PROTOCOL:
        raise ValueError("Unexpected corporate report route-hint protocol")
    if manifest.get("source_contract") != ROUTE_HINT_SOURCE_CONTRACT:
        raise ValueError("Corporate report route hints must remain navigation-only")
    if manifest.get("hints_sha256") != sha256_file(hints_path):
        raise ValueError("Corporate report route-hint checksum mismatch")
    rows = [json.loads(line) for line in hints_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != int(manifest.get("question_count") or -1):
        raise ValueError("Corporate report route-hint count mismatch")
    if len({row.get("question_id") for row in rows}) != len(rows):
        raise ValueError("Corporate report route hints require unique question IDs")
    for row in rows:
        if row.get("source_contract") != ROUTE_HINT_SOURCE_CONTRACT:
            raise ValueError("Corporate report route hint attempted to change its contract")
    return manifest
