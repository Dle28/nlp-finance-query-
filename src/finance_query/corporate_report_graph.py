"""Source-bounded graph of report families and observed corporate relations.

The graph makes cross-report navigation safer: it relates reports from the
same issuer, marks distinct accounting scopes, and retains only relationship
phrases that appear in a source table.  It is deliberately *not* a financial
fact store.  It cannot select a numeric cell, infer a missing scope, compute an
answer, or promote provenance.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .financial_metrics import fold_text
from .report_entities import canonical_entity_name
from .table_structure import normalize_space, sha256_file


CORPORATE_REPORT_GRAPH_VERSION = 1
CORPORATE_REPORT_GRAPH_PROTOCOL = "source_bounded_corporate_report_graph_v1"
GRAPH_SOURCE_CONTRACT = {
    "metadata_only": True,
    "navigation_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "may_select_table_candidate": True,
    "may_select_value_cell": False,
    "may_compute_answer": False,
    "may_infer_scope": False,
    "may_promote_provenance": False,
}

RELATION_LABELS = {
    "cong ty con": "subsidiary",
    "cong ty lien ket": "associate",
    "cong ty lien doanh": "joint_venture",
    "ben lien quan": "related_party",
}
RELATION_PHRASE = r"(?:công\s+ty\s+con|công\s+ty\s+liên\s+kết|công\s+ty\s+liên\s+doanh|bên\s+liên\s+quan)"
VIETNAMESE_ENTITY_RELATION_RE = re.compile(
    rf"(?P<entity>(?:công\s+ty|tổng\s+công\s+ty|tập\s+đoàn|ngân\s+hàng|quỹ)\b[^,;]{{2,180}}?)"
    rf"\s*,\s*(?:một\s+)?(?P<relation>{RELATION_PHRASE})\b",
    re.IGNORECASE,
)
# Keep this case-sensitive.  It deliberately starts a foreign legal name only
# at a run of title-cased words, so Vietnamese prose before a name cannot be
# absorbed into the candidate.
FOREIGN_ENTITY_RELATION_RE = re.compile(
    rf"(?P<entity>(?:[A-Z][A-Za-z0-9&.'’-]*\s+){{1,11}}(?:Limited|Ltd\.?|JSC|LLC))"
    rf"\s*,\s*(?:một\s+)?(?P<relation>{RELATION_PHRASE})\b"
)
VIETNAMESE_ENTITY_LABEL_RE = re.compile(
    r"^(?P<entity>(?:công\s+ty|tổng\s+công\s+ty|tập\s+đoàn|ngân\s+hàng|quỹ)\b[^,;]{2,180})$",
    re.IGNORECASE,
)
FOREIGN_ENTITY_LABEL_RE = re.compile(
    r"^(?P<entity>(?:[A-Z][A-Za-z0-9&.'’-]*\s+){1,11}(?:Limited|Ltd\.?|JSC|LLC))$"
)
INVESTMENT_CATEGORY_FOLDED_RE = re.compile(
    r"^dau tu(?: gop von)?(?: vao)?(?: cac)? (?P<relation>"
    r"cong ty con|cong ty lien ket|cong ty lien doanh|ben lien quan)$"
)
NON_ENTITY_TAIL_PREFIXES = {
    "con",
    "lien",
    "nhan",
    "duoc",
    "da",
    "se",
    "nay",
    "co",
    "trong",
    "theo",
    "tu",
    "voi",
}
LEGAL_NAME_TAIL_PREFIXES = {"co", "trach", "tnhh", "joint", "stock", "limited", "ltd", "jsc", "llc"}
ORGANISATION_PREFIX_RE = re.compile(
    r"^(?:công\s+ty|tổng\s+công\s+ty|tập\s+đoàn|ngân\s+hàng|quỹ)\s+(?P<tail>.+)$",
    re.IGNORECASE,
)


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _relation_type(value: str) -> str | None:
    return RELATION_LABELS.get(fold_text(value))


def _entity_candidates(label: object, *, explicit_only: bool) -> list[str]:
    """Return literal organisation-name candidates without fuzzy matching."""
    text = normalize_space(str(label or "").lstrip("▪•*-– "))
    patterns = (
        (VIETNAMESE_ENTITY_RELATION_RE, FOREIGN_ENTITY_RELATION_RE)
        if explicit_only
        else (VIETNAMESE_ENTITY_LABEL_RE, FOREIGN_ENTITY_LABEL_RE)
    )
    results: list[str] = []
    for pattern in patterns:
        matches = pattern.finditer(text) if explicit_only else [pattern.fullmatch(text)]
        for match in matches:
            if match is None:
                continue
            entity = normalize_space(match.group("entity")).strip(" -–:;,.")
            canonical = canonical_entity_name(entity)
            if (
                entity
                and _plausible_organisation_name(entity)
                and len(canonical.split()) >= 2
                and entity not in results
            ):
                results.append(entity)
    return results


def _plausible_organisation_name(entity: str) -> bool:
    """Reject a narrative phrase that merely begins with an organisation word."""
    match = ORGANISATION_PREFIX_RE.match(normalize_space(entity))
    if match is None:
        return True  # foreign legal-name regex already carries a legal suffix.
    tail = normalize_space(match.group("tail"))
    folded_tail = fold_text(tail)
    first = folded_tail.split(maxsplit=1)[0] if folded_tail else ""
    original_first = tail.split(maxsplit=1)[0] if tail else ""
    # ``Cổ phần`` folds to ``co phan`` while the prose verb ``có`` folds to
    # ``co`` too. Preserve the source capitalisation distinction rather than
    # broadening a normalized match.
    if first in LEGAL_NAME_TAIL_PREFIXES and original_first and original_first[0].isupper():
        return True
    if first in NON_ENTITY_TAIL_PREFIXES:
        return False
    if folded_tail.startswith(("cong ty con", "lien ket", "lien doanh", "ben lien quan")):
        return False
    return bool(original_first and (original_first[0].isupper() or first in LEGAL_NAME_TAIL_PREFIXES))


def _explicit_relations(label: object) -> list[tuple[str, str]]:
    """Extract a source-literal entity/relation pair only from a safe phrase."""
    text = normalize_space(str(label or ""))
    results: list[tuple[str, str]] = []
    for pattern in (VIETNAMESE_ENTITY_RELATION_RE, FOREIGN_ENTITY_RELATION_RE):
        for match in pattern.finditer(text):
            relation = _relation_type(match.group("relation"))
            entity = normalize_space(match.group("entity")).strip(" -–:;,.")
            if (
                relation
                and _plausible_organisation_name(entity)
                and len(canonical_entity_name(entity).split()) >= 2
            ):
                pair = (entity, relation)
                if pair not in results:
                    results.append(pair)
    return results


def _investment_category_relation(label: object) -> str | None:
    """Classify a source table category; no relationship is inferred otherwise."""
    match = INVESTMENT_CATEGORY_FOLDED_RE.fullmatch(fold_text(str(label or "")))
    return _relation_type(match.group("relation")) if match is not None else None


def document_ticker_map(tables: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Bridge document metadata ``company`` to table-level ``ticker`` safely."""
    tickers: dict[str, set[str]] = defaultdict(set)
    for table in tables:
        document_id = str(table.get("document_id") or "")
        ticker = str(table.get("ticker") or "")
        if document_id and ticker:
            tickers[document_id].add(ticker)
    output: dict[str, str] = {}
    for document_id, values in tickers.items():
        if len(values) != 1:
            raise ValueError(f"Document {document_id} has ambiguous table tickers")
        output[document_id] = next(iter(values))
    return output


def build_report_families(
    documents: Iterable[Mapping[str, Any]], *, document_tickers: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Group source-derived documents by issuer, report year, and report type."""
    groups: dict[tuple[str, int | None, str], list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        document_id = str(document.get("document_id") or "")
        ticker = str(document_tickers.get(document_id) or "")
        company = str(document.get("company") or "")
        if not document_id or not ticker or company != ticker:
            raise ValueError("Document metadata company/ticker bridge is missing or inconsistent")
        groups[(ticker, document.get("report_year"), str(document.get("report_type") or "unknown"))].append(
            dict(document)
        )
    rows: list[dict[str, Any]] = []
    for (ticker, year, report_type), members in sorted(groups.items()):
        members = sorted(members, key=lambda row: str(row["document_id"]))
        scopes = [str(row.get("report_scope") or "unknown") for row in members]
        scope_counts = {scope: scopes.count(scope) for scope in sorted(set(scopes))}
        family_status = (
            "source_derived_unique_scope_members"
            if all(row.get("metadata_status") == "source_derived" for row in members)
            and all(count == 1 for count in scope_counts.values())
            else "ambiguous_document_metadata_or_scope_members"
        )
        identity = {
            "issuer_ticker": ticker,
            "report_year": year,
            "report_type": report_type,
            "document_ids": [str(row["document_id"]) for row in members],
        }
        rows.append(
            {
                "schema_version": CORPORATE_REPORT_GRAPH_VERSION,
                "report_family_id": _sha256_json(identity),
                **identity,
                "scope_counts": scope_counts,
                "has_parallel_consolidated_and_separate": (
                    scope_counts.get("consolidated") == 1 and scope_counts.get("separate") == 1
                ),
                "family_status": family_status,
                "members": [
                    {
                        "document_id": str(row["document_id"]),
                        "report_scope": str(row.get("report_scope") or "unknown"),
                        "reporting_period_end": row.get("reporting_period_end"),
                        "metadata_status": row.get("metadata_status"),
                    }
                    for row in members
                ],
                "source_contract": dict(GRAPH_SOURCE_CONTRACT),
            }
        )
    return rows


def _report_edge(
    relation_type: str,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    alignment_status: str,
) -> dict[str, Any]:
    identity = {
        "relation_type": relation_type,
        "source_document_id": str(left["document_id"]),
        "target_document_id": str(right["document_id"]),
    }
    return {
        "schema_version": CORPORATE_REPORT_GRAPH_VERSION,
        "report_relation_id": _sha256_json(identity),
        **identity,
        "issuer_ticker": str(left["company"]),
        "source_report_year": left.get("report_year"),
        "target_report_year": right.get("report_year"),
        "source_scope": str(left.get("report_scope") or "unknown"),
        "target_scope": str(right.get("report_scope") or "unknown"),
        "report_type": str(left.get("report_type") or "unknown"),
        "period_alignment_status": alignment_status,
        "value_substitution_forbidden": True,
        "source_contract": dict(GRAPH_SOURCE_CONTRACT),
    }


def build_report_relationship_edges(
    documents: Iterable[Mapping[str, Any]], *, document_tickers: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Create scope and adjacent-period navigation edges between known reports."""
    normalized: list[dict[str, Any]] = []
    for document in documents:
        item = dict(document)
        document_id = str(item.get("document_id") or "")
        ticker = str(document_tickers.get(document_id) or "")
        if not document_id or ticker != str(item.get("company") or ""):
            raise ValueError("Document metadata company/ticker bridge is missing or inconsistent")
        if item.get("metadata_status") != "source_derived":
            continue
        normalized.append(item)
    rows: list[dict[str, Any]] = []
    by_family: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        year = item.get("report_year")
        if isinstance(year, int):
            by_family[(str(item["company"]), year, str(item.get("report_type") or "unknown"))].append(item)
    for members in by_family.values():
        members = sorted(members, key=lambda row: str(row["document_id"]))
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                left_scope = str(left.get("report_scope") or "unknown")
                right_scope = str(right.get("report_scope") or "unknown")
                if left_scope == right_scope or "unknown" in {left_scope, right_scope}:
                    continue
                rows.append(
                    _report_edge(
                        "parallel_scope_candidate",
                        left,
                        right,
                        alignment_status="same_report_year_distinct_scope",
                    )
                )
    by_series: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        year = item.get("report_year")
        scope = str(item.get("report_scope") or "unknown")
        if isinstance(year, int) and scope != "unknown":
            by_series[(str(item["company"]), scope, str(item.get("report_type") or "unknown"))].append(item)
    for members in by_series.values():
        members = sorted(members, key=lambda row: (int(row["report_year"]), str(row["document_id"])))
        for left, right in zip(members, members[1:]):
            if int(right["report_year"]) - int(left["report_year"]) != 1:
                continue
            left_end, right_end = left.get("reporting_period_end"), right.get("reporting_period_end")
            aligned = (
                isinstance(left_end, Mapping)
                and isinstance(right_end, Mapping)
                and left_end.get("day") == right_end.get("day")
                and left_end.get("month") == right_end.get("month")
            )
            rows.append(
                _report_edge(
                    "adjacent_reporting_period_candidate",
                    left,
                    right,
                    alignment_status="matching_source_period_end" if aligned else "year_only_candidate",
                )
            )
    return sorted(rows, key=lambda row: str(row["report_relation_id"]))


def _alias_tickers(aliases: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for alias in aliases:
        canonical = str(alias.get("canonical_entity") or "")
        ticker = str(alias.get("ticker") or "")
        if canonical and ticker:
            output[canonical].add(ticker)
    return output


def _corporate_edge(
    *,
    table: Mapping[str, Any],
    segment: Mapping[str, Any],
    row_index: int,
    source_row_label: str,
    target_entity: str,
    relation_type: str,
    extraction_mode: str,
    alias_tickers: Mapping[str, set[str]],
) -> dict[str, Any]:
    canonical_target = canonical_entity_name(target_entity)
    matched_tickers = sorted(alias_tickers.get(canonical_target, set()))
    if len(matched_tickers) == 1:
        target_ticker, resolution_status = matched_tickers[0], "unique_source_title_alias"
    elif len(matched_tickers) > 1:
        target_ticker, resolution_status = "", "ambiguous_source_title_alias"
    else:
        target_ticker, resolution_status = "", "unresolved_source_title_alias"
    raw_row = (table.get("rows") or [])[row_index]
    identity = {
        "issuer_ticker": str(table["ticker"]),
        "document_id": str(table["document_id"]),
        "internal_table_uid": str(table["internal_table_uid"]),
        "source_row_index": row_index,
        "target_canonical_entity": canonical_target,
        "relation_type": relation_type,
    }
    return {
        "schema_version": CORPORATE_REPORT_GRAPH_VERSION,
        "corporate_relation_id": _sha256_json(identity),
        **identity,
        "report_year": table.get("report_year"),
        "report_scope": table.get("scope"),
        "target_source_entity": target_entity,
        "target_ticker": target_ticker,
        "target_resolution_status": resolution_status,
        "extraction_mode": extraction_mode,
        "source_table_function": (segment.get("table_function") or {}).get("kind"),
        "source_row_label": source_row_label,
        "source_row_sha256": hashlib.sha256(
            json.dumps(raw_row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "source_context_sha256": str(segment.get("source_context_sha256") or ""),
        "source_contract": dict(GRAPH_SOURCE_CONTRACT),
    }


def build_corporate_relationship_edges(
    tables: Iterable[Mapping[str, Any]],
    segments: Iterable[Mapping[str, Any]],
    aliases: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Extract only literal corporate-relation mentions from eligible source rows."""
    by_segment = {str(row.get("internal_table_uid") or ""): row for row in segments}
    alias_tickers = _alias_tickers(aliases)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for table in tables:
        uid = str(table.get("internal_table_uid") or "")
        segment = by_segment.get(uid)
        if segment is None:
            raise ValueError("Every table must have one report-segment record")
        if str(segment.get("document_id") or "") != str(table.get("document_id") or ""):
            raise ValueError("Table/report-segment document identity differs")
        function = str((segment.get("table_function") or {}).get("kind") or "")
        active_investment_relation: str | None = None
        for row_index, row in enumerate(table.get("rows") or []):
            if not isinstance(row, list) or not row:
                continue
            source_row_label = normalize_space(str(row[0] or ""))
            if not source_row_label:
                continue
            explicit = _explicit_relations(source_row_label)
            for target_entity, relation_type in explicit:
                edge = _corporate_edge(
                    table=table,
                    segment=segment,
                    row_index=row_index,
                    source_row_label=source_row_label,
                    target_entity=target_entity,
                    relation_type=relation_type,
                    extraction_mode="explicit_source_relation_phrase_v1",
                    alias_tickers=alias_tickers,
                )
                if edge["corporate_relation_id"] not in seen:
                    seen.add(edge["corporate_relation_id"])
                    rows.append(edge)
            if function != "investment_schedule":
                continue
            category_relation = _investment_category_relation(source_row_label)
            if category_relation is not None:
                active_investment_relation = category_relation
                continue
            if active_investment_relation is None or explicit:
                continue
            for target_entity in _entity_candidates(source_row_label, explicit_only=False):
                edge = _corporate_edge(
                    table=table,
                    segment=segment,
                    row_index=row_index,
                    source_row_label=source_row_label,
                    target_entity=target_entity,
                    relation_type=active_investment_relation,
                    extraction_mode="investment_table_source_category_v1",
                    alias_tickers=alias_tickers,
                )
                if edge["corporate_relation_id"] not in seen:
                    seen.add(edge["corporate_relation_id"])
                    rows.append(edge)
    return sorted(rows, key=lambda row: str(row["corporate_relation_id"]))


def validate_corporate_report_graph(output_dir: Path) -> dict[str, Any]:
    """Validate graph output hashes and its strict navigation-only contract."""
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "corporate_report_graph_v1.manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Corporate-report graph manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != CORPORATE_REPORT_GRAPH_VERSION:
        raise ValueError("Unsupported corporate-report graph schema")
    if manifest.get("protocol") != CORPORATE_REPORT_GRAPH_PROTOCOL:
        raise ValueError("Unexpected corporate-report graph protocol")
    if manifest.get("source_contract") != GRAPH_SOURCE_CONTRACT:
        raise ValueError("Corporate-report graph must remain navigation-only metadata")
    outputs = manifest.get("outputs") or {}
    for key, expected_count_key in (
        ("report_families", "report_family_count"),
        ("report_relationship_edges", "report_relationship_edge_count"),
        ("corporate_relationship_edges", "corporate_relationship_edge_count"),
    ):
        item = outputs.get(key) or {}
        name = str(item.get("file") or "")
        if not name or Path(name).name != name:
            raise ValueError(f"Invalid {key} output filename")
        path = output_dir / name
        if not path.is_file() or item.get("sha256") != sha256_file(path):
            raise ValueError(f"Corporate-report graph hash mismatch for {key}")
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        if count != int(manifest.get(expected_count_key) or -1):
            raise ValueError(f"Corporate-report graph count mismatch for {key}")
    return manifest
