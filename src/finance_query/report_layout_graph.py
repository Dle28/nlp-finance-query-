"""Source-bounded layout graph for sections inside and across financial reports.

``report_segments_v1`` already exposes source headings beside individual
tables.  This module preserves their order, groups contiguous tables beneath
the same literal heading, and connects only identically headed sections across
report relationships already observed in the report graph.  The result helps
navigation understand a report's layout; it never represents a value, row,
cell, formula, answer, or inferred scope.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .financial_metrics import fold_text
from .table_structure import normalize_space, sha256_file


REPORT_LAYOUT_GRAPH_VERSION = 1
REPORT_LAYOUT_GRAPH_PROTOCOL = "source_bounded_report_layout_graph_v1"
LAYOUT_GRAPH_SOURCE_CONTRACT = {
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
    "value_substitution_forbidden": True,
}


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _unique_strings(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_space(str(value or ""))
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _source_layout_label(segment: Mapping[str, Any]) -> str:
    """Pick a literal source heading, never a semantic descriptor fallback."""
    return normalize_space(
        str(segment.get("source_parent_heading") or segment.get("source_heading") or "")
    )


def _ordinal_sort_key(segment: Mapping[str, Any]) -> tuple[int, str]:
    ordinal = segment.get("local_ordinal")
    return (ordinal if type(ordinal) is int else 10**12, str(segment.get("internal_table_uid") or ""))


def _section_node(
    document_id: str,
    index: int,
    members: list[Mapping[str, Any]],
    layout_label: str,
) -> dict[str, Any]:
    first = members[0]
    table_uids = [str(member["internal_table_uid"]) for member in members]
    heading_signature = fold_text(layout_label) if layout_label else ""
    identity = {
        "document_id": document_id,
        "section_ordinal": index,
        "internal_table_uids": table_uids,
    }
    return {
        "schema_version": REPORT_LAYOUT_GRAPH_VERSION,
        "layout_section_id": _sha256_json(identity),
        "document_id": document_id,
        "issuer_ticker": first.get("ticker"),
        "report_year": first.get("report_year"),
        "report_scope": first.get("scope"),
        "section_ordinal": index,
        "source_layout_label": layout_label,
        "source_heading_signature": heading_signature,
        "source_heading_status": "source_heading_present" if layout_label else "source_heading_absent",
        "source_heading_kinds": _unique_strings(member.get("source_heading_kind") for member in members),
        "source_headings": _unique_strings(member.get("source_heading") for member in members),
        "internal_table_uids": table_uids,
        "first_local_ordinal": first.get("local_ordinal"),
        "last_local_ordinal": members[-1].get("local_ordinal"),
        "table_function_kinds": _unique_strings(
            (member.get("table_function") or {}).get("kind")
            for member in members
            if isinstance(member.get("table_function"), Mapping)
        ),
        "table_section_kinds": _unique_strings(
            (member.get("table_section") or {}).get("kind")
            for member in members
            if isinstance(member.get("table_section"), Mapping)
        ),
        "source_context_sha256s": [str(member.get("source_context_sha256") or "") for member in members],
        "value_substitution_forbidden": True,
        "source_contract": dict(LAYOUT_GRAPH_SOURCE_CONTRACT),
    }


def build_report_layout_graph(
    segments: Iterable[Mapping[str, Any]], report_edges: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return profiles, sections, within-report sequence and cross-report edges.

    Each source segment must have a unique table UID and a document identity.
    Consecutive runs with the same literal layout label become a section.  A
    heading absence remains an explicit layout gap, rather than borrowing a
    previous heading.
    """
    by_document: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_uids: set[str] = set()
    for raw_segment in segments:
        segment = dict(raw_segment)
        uid, document_id = str(segment.get("internal_table_uid") or ""), str(segment.get("document_id") or "")
        if not uid or not document_id or uid in seen_uids:
            raise ValueError("Report layout segments require unique table UIDs and document IDs")
        if segment.get("evidence_eligible") is not False or segment.get("training_eligible") is not False:
            raise ValueError("Report layout graph requires navigation-only source segments")
        seen_uids.add(uid)
        by_document[document_id].append(segment)

    profiles: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    sequence_edges: list[dict[str, Any]] = []
    sections_by_document: dict[str, list[dict[str, Any]]] = {}
    for document_id, raw_members in sorted(by_document.items()):
        members = sorted(raw_members, key=_ordinal_sort_key)
        groups: list[tuple[str, list[Mapping[str, Any]]]] = []
        for member in members:
            label = _source_layout_label(member)
            if groups and groups[-1][0] == label:
                groups[-1][1].append(member)
            else:
                groups.append((label, [member]))
        document_sections = [
            _section_node(document_id, index, group_members, label)
            for index, (label, group_members) in enumerate(groups, start=1)
        ]
        sections.extend(document_sections)
        sections_by_document[document_id] = document_sections
        for left, right in zip(document_sections, document_sections[1:]):
            identity = {
                "relation_type": "next_source_layout_section",
                "source_section_id": left["layout_section_id"],
                "target_section_id": right["layout_section_id"],
            }
            sequence_edges.append(
                {
                    "schema_version": REPORT_LAYOUT_GRAPH_VERSION,
                    "layout_relation_id": _sha256_json(identity),
                    **identity,
                    "document_id": document_id,
                    "source_document_id": document_id,
                    "target_document_id": document_id,
                    "value_substitution_forbidden": True,
                    "source_contract": dict(LAYOUT_GRAPH_SOURCE_CONTRACT),
                }
            )
        source_present = [section for section in document_sections if section["source_heading_status"] == "source_heading_present"]
        first = members[0]
        profile_identity = {"document_id": document_id, "section_ids": [row["layout_section_id"] for row in document_sections]}
        profiles.append(
            {
                "schema_version": REPORT_LAYOUT_GRAPH_VERSION,
                "document_layout_profile_id": _sha256_json(profile_identity),
                "document_id": document_id,
                "issuer_ticker": first.get("ticker"),
                "report_year": first.get("report_year"),
                "report_scope": first.get("scope"),
                "table_count": len(members),
                "section_count": len(document_sections),
                "source_heading_section_count": len(source_present),
                "source_heading_coverage": len(source_present) / len(document_sections),
                "layout_section_ids": [row["layout_section_id"] for row in document_sections],
                "source_contract": dict(LAYOUT_GRAPH_SOURCE_CONTRACT),
            }
        )

    cross_report_edges: list[dict[str, Any]] = []
    allowed_relations = {
        "parallel_scope_candidate": "parallel_scope_section_candidate",
        "adjacent_reporting_period_candidate": "adjacent_period_section_candidate",
    }
    for report_edge in report_edges:
        source_document_id = str(report_edge.get("source_document_id") or "")
        target_document_id = str(report_edge.get("target_document_id") or "")
        relation_type = allowed_relations.get(str(report_edge.get("relation_type") or ""))
        if not source_document_id or not target_document_id or relation_type is None:
            continue
        source_sections = sections_by_document.get(source_document_id, [])
        target_sections = sections_by_document.get(target_document_id, [])
        target_by_signature: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for section in target_sections:
            signature = str(section.get("source_heading_signature") or "")
            if signature:
                target_by_signature[signature].append(section)
        for source_section in source_sections:
            signature = str(source_section.get("source_heading_signature") or "")
            if not signature:
                continue
            for target_section in target_by_signature.get(signature, []):
                identity = {
                    "relation_type": relation_type,
                    "source_section_id": source_section["layout_section_id"],
                    "target_section_id": target_section["layout_section_id"],
                }
                cross_report_edges.append(
                    {
                        "schema_version": REPORT_LAYOUT_GRAPH_VERSION,
                        "cross_report_section_relation_id": _sha256_json(identity),
                        **identity,
                        "source_document_id": source_document_id,
                        "target_document_id": target_document_id,
                        "issuer_ticker": report_edge.get("issuer_ticker"),
                        "source_report_year": report_edge.get("source_report_year"),
                        "target_report_year": report_edge.get("target_report_year"),
                        "source_scope": report_edge.get("source_scope"),
                        "target_scope": report_edge.get("target_scope"),
                        "source_heading_signature": signature,
                        "source_heading_match_policy": "literal_normalized_source_heading_exact_match_v1",
                        "period_alignment_status": report_edge.get("period_alignment_status"),
                        "value_substitution_forbidden": True,
                        "source_contract": dict(LAYOUT_GRAPH_SOURCE_CONTRACT),
                    }
                )
    return (
        sorted(profiles, key=lambda row: str(row["document_id"])),
        sorted(sections, key=lambda row: (str(row["document_id"]), int(row["section_ordinal"]))),
        sorted(sequence_edges, key=lambda row: str(row["layout_relation_id"])),
        sorted(cross_report_edges, key=lambda row: str(row["cross_report_section_relation_id"])),
    )


def validate_report_layout_graph(output_dir: Path) -> dict[str, Any]:
    """Check graph protocol, output hashes and its navigation-only contract."""
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "report_layout_graph_v1.manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Report layout graph manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != REPORT_LAYOUT_GRAPH_VERSION
        or manifest.get("protocol") != REPORT_LAYOUT_GRAPH_PROTOCOL
        or manifest.get("source_contract") != LAYOUT_GRAPH_SOURCE_CONTRACT
    ):
        raise ValueError("Unexpected report layout graph manifest contract")
    outputs = manifest.get("outputs") or {}
    required_outputs = {
        "profiles": "report_layout_profiles_v1.jsonl",
        "sections": "report_layout_sections_v1.jsonl",
        "sequence_edges": "report_layout_sequence_edges_v1.jsonl",
        "cross_report_edges": "report_layout_cross_report_edges_v1.jsonl",
    }
    for key, filename in required_outputs.items():
        output = outputs.get(key) or {}
        if output.get("file") != filename:
            raise ValueError(f"Report layout graph has invalid {key} filename")
        path = output_dir / filename
        if not path.is_file() or output.get("sha256") != sha256_file(path):
            raise ValueError(f"Report layout graph checksum mismatch for {key}")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("source_contract") != LAYOUT_GRAPH_SOURCE_CONTRACT:
                raise ValueError(f"Report layout graph row {key}:{line_number} changed its source contract")
    return manifest
